#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

DEFAULT_IMAGE_TAG = "local/forgejo-runner-docker:12"
DEFAULT_TEST_TIMEOUT_SECONDS = 60


class CliError(RuntimeError):
    pass


def run(
    cmd: list[str], *, check: bool = True, capture_output: bool = False
) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def ensure_container_binary() -> None:
    if shutil.which("container") is None:
        raise CliError(
            "`container` command not found. Install Apple's container runtime first."
        )


def container_cli_responsive() -> bool:
    proc = run(["container", "list", "--all"], check=False, capture_output=True)
    return proc.returncode == 0


def brew_service_status(service_name: str) -> str | None:
    if shutil.which("brew") is None:
        return None

    proc = run(["brew", "services", "list"], check=False, capture_output=True)
    if proc.returncode != 0:
        return "unknown"

    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("name"):
            continue

        parts = stripped.split()
        if parts and parts[0] == service_name:
            if len(parts) >= 2:
                return parts[1]
            return "unknown"

    return "not-listed"


def require_container_service_running() -> None:
    if not container_cli_responsive():
        raise CliError(
            "Container service does not appear to be running. "
            "Start it first with `brew services start container`."
        )


def builder_status_running(status_text: str) -> bool:
    text = status_text.strip().lower()
    if not text:
        return False

    if "not running" in text:
        return False

    if "is running" in text:
        return True

    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "buildkit" and "running" in parts:
            return True

    return False


def ensure_builder_ready() -> None:
    print("Running build preflight: checking builder...")
    status_proc = run(
        ["container", "builder", "status"], check=False, capture_output=True
    )
    status_text = f"{status_proc.stdout}\n{status_proc.stderr}"

    if builder_status_running(status_text):
        print("Builder is already running.")
        return

    print("Builder is not ready. Attempting recovery (stop/delete/start).")
    run(["container", "builder", "stop"], check=False)
    run(["container", "builder", "delete"], check=False)

    start_proc = run(
        ["container", "builder", "start"], check=False, capture_output=True
    )
    if start_proc.returncode != 0:
        raise CliError(
            "Failed to start builder during preflight. "
            f"stdout: {start_proc.stdout.strip()} stderr: {start_proc.stderr.strip()}"
        )

    verify_proc = run(
        ["container", "builder", "status"], check=False, capture_output=True
    )
    verify_text = f"{verify_proc.stdout}\n{verify_proc.stderr}"
    if not builder_status_running(verify_text):
        raise CliError(
            "Builder preflight did not reach a running state. "
            f"status output: {verify_proc.stdout.strip()} {verify_proc.stderr.strip()}"
        )

    print("Builder preflight completed.")


def print_status_table(rows: list[tuple[str, str, str]]) -> None:
    headers = ("Check", "Status", "Details")
    widths = [len(headers[0]), len(headers[1]), len(headers[2])]

    for check, status, details in rows:
        widths[0] = max(widths[0], len(check))
        widths[1] = max(widths[1], len(status))
        widths[2] = max(widths[2], len(details))

    def fmt_row(c1: str, c2: str, c3: str) -> str:
        return f"{c1:<{widths[0]}} | {c2:<{widths[1]}} | {c3:<{widths[2]}}"

    separator = f"{'-' * widths[0]}-+-{'-' * widths[1]}-+-{'-' * widths[2]}"

    print(fmt_row(*headers))
    print(separator)
    for row in rows:
        print(fmt_row(*row))


def build_runner_image(tag: str, *, no_cache: bool = False) -> None:
    cmd = ["container", "build"]
    if no_cache:
        cmd.append("--no-cache")
    cmd.extend(["-t", tag, "."])
    run(cmd)


def write_test_container_context(target_dir: Path) -> None:
    dockerfile = target_dir / "Dockerfile"
    marker = target_dir / "hello.txt"

    dockerfile.write_text(
        "FROM alpine:3.20\n"
        "COPY hello.txt /hello.txt\n"
        "RUN test -f /hello.txt\n"
        'CMD ["cat", "/hello.txt"]\n'
    )
    marker.write_text("hello from frccc test\n")


def start_temp_dind(dind_name: str, network_name: str) -> None:
    base_cmd = [
        "container",
        "run",
        "-d",
        "--name",
        dind_name,
        "--network",
        network_name,
        "docker:dind",
        "dockerd",
        "-H",
        "tcp://0.0.0.0:2375",
        "--tls=false",
    ]

    cmd_with_caps = [
        "container",
        "run",
        "-d",
        "--name",
        dind_name,
        "--network",
        network_name,
        "--cap-add",
        "ALL",
        "docker:dind",
        "dockerd",
        "-H",
        "tcp://0.0.0.0:2375",
        "--tls=false",
    ]

    proc = run(cmd_with_caps, check=False, capture_output=True)
    if proc.returncode == 0:
        return

    combined = f"{proc.stdout}\n{proc.stderr}".lower()
    if (
        "unknown option '--cap-add'" in combined
        or 'unknown option "--cap-add"' in combined
    ):
        print("`container run` does not support --cap-add; retrying without it.")
        proc2 = run(base_cmd, check=False, capture_output=True)
        if proc2.returncode == 0:
            return
        raise CliError(
            "Failed to start docker:dind without --cap-add. "
            f"stdout: {proc2.stdout.strip()} stderr: {proc2.stderr.strip()}"
        )

    raise CliError(
        "Failed to start docker:dind. "
        f"stdout: {proc.stdout.strip()} stderr: {proc.stderr.strip()}"
    )


def get_container_ipv4(container_name: str, network_name: str) -> str:
    proc = run(["container", "inspect", container_name], capture_output=True)

    # Keep parsing defensive because `container inspect` schema may evolve.
    import json

    payload = json.loads(proc.stdout)
    if not isinstance(payload, list) or not payload:
        raise CliError(f"Unexpected inspect output for container {container_name}")

    details = payload[0]
    networks = details.get("networks") or []

    # Prefer exact network match first.
    for net in networks:
        net_id = net.get("network") or net.get("id")
        address = net.get("address") or net.get("addr") or net.get("ipv4Address")
        if net_id == network_name and isinstance(address, str) and address:
            return address.split("/")[0]

    # Fallback: first available IPv4-ish address.
    for net in networks:
        address = net.get("address") or net.get("addr") or net.get("ipv4Address")
        if isinstance(address, str) and address:
            return address.split("/")[0]

    raise CliError(
        f"Could not determine IP address for container {container_name} on network {network_name}"
    )


def wait_for_temp_dind(
    dind_host: str,
    dind_name: str,
    network_name: str,
    timeout_seconds: int,
) -> None:
    print(
        f"Waiting for temp docker:dind to become ready at {dind_host}:2375 (timeout: {timeout_seconds}s)..."
    )
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        probe = run(
            [
                "container",
                "run",
                "--rm",
                "--network",
                network_name,
                "docker:cli",
                "-H",
                f"tcp://{dind_host}:2375",
                "info",
            ],
            check=False,
            capture_output=True,
        )
        if probe.returncode == 0:
            print("Temp docker:dind is ready.")
            return

        time.sleep(1)

    raise CliError(
        "Timed out waiting for temporary docker:dind to become ready. "
        f"Check logs with: container logs -n 200 {dind_name}"
    )


def run_temp_runner_docker_build(
    *,
    runner_image: str,
    network_name: str,
    dind_host: str,
    context_dir: Path,
    test_image_tag: str,
) -> None:
    run(
        [
            "container",
            "run",
            "--rm",
            "--network",
            network_name,
            "-e",
            f"DOCKER_HOST=tcp://{dind_host}:2375",
            "-v",
            f"{context_dir.resolve()}:/workspace",
            runner_image,
            "sh",
            "-lc",
            (
                "docker --version && "
                f"docker build -t {test_image_tag} /workspace && "
                f"docker image inspect {test_image_tag} >/dev/null"
            ),
        ]
    )


def cmd_status(_: argparse.Namespace) -> int:
    container_cli_installed = shutil.which("container") is not None

    if container_cli_installed:
        cli_responsive = container_cli_responsive()
        cli_detail = "responsive" if cli_responsive else "not responsive"
    else:
        cli_responsive = False
        cli_detail = "skipped: `container` command not found"

    brew_status = brew_service_status("container")
    if brew_status is None:
        brew_row_status = "WARN"
        brew_detail = "`brew` command not found"
    elif brew_status == "started":
        brew_row_status = "OK"
        brew_detail = "started"
    elif brew_status == "none":
        brew_row_status = "WARN"
        brew_detail = "not started"
    elif brew_status == "not-listed":
        brew_row_status = "WARN"
        brew_detail = "service not listed"
    elif brew_status == "unknown":
        brew_row_status = "WARN"
        brew_detail = "unable to determine status"
    else:
        brew_row_status = "WARN"
        brew_detail = brew_status

    rows = [
        (
            "container CLI installed",
            "OK" if container_cli_installed else "FAIL",
            "found on PATH" if container_cli_installed else "not found",
        ),
        (
            "container CLI responsive",
            "OK" if cli_responsive else "FAIL",
            cli_detail,
        ),
        (
            "brew service (container)",
            brew_row_status,
            brew_detail,
        ),
    ]

    print_status_table(rows)

    if not cli_responsive:
        print("\nTo start the container service:")
        print("  brew services start container")

    return 0 if container_cli_installed and cli_responsive else 1


def cmd_build(args: argparse.Namespace) -> int:
    ensure_container_binary()
    require_container_service_running()
    ensure_builder_ready()

    build_runner_image(args.tag)
    print(f"Built image: {args.tag}")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    ensure_container_binary()
    require_container_service_running()
    ensure_builder_ready()

    print("Running fresh build for test...")
    build_runner_image(args.tag, no_cache=True)

    suffix = uuid.uuid4().hex[:8]
    network_name = f"frccc-test-net-{suffix}"
    dind_name = f"frccc-test-dind-{suffix}"
    test_image_tag = f"frccc-test-image:{suffix}"

    print("Preparing temporary test environment...")
    run(["container", "network", "create", network_name])

    try:
        start_temp_dind(dind_name, network_name)
        dind_host = get_container_ipv4(dind_name, network_name)
        wait_for_temp_dind(dind_host, dind_name, network_name, args.timeout)

        with tempfile.TemporaryDirectory(prefix="frccc-test-") as tmpdir:
            context_dir = Path(tmpdir)
            write_test_container_context(context_dir)
            run_temp_runner_docker_build(
                runner_image=args.tag,
                network_name=network_name,
                dind_host=dind_host,
                context_dir=context_dir,
                test_image_tag=test_image_tag,
            )

        print("Test passed: temporary runner successfully built a Docker image.")
        return 0
    finally:
        run(["container", "delete", "-f", dind_name], check=False)
        run(["container", "network", "delete", network_name], check=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="frccc",
        description="Forgejo Runner Container Command Center",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser(
        "status",
        help="Check whether Apple's container service is running",
    )
    status.set_defaults(func=cmd_status)

    build = sub.add_parser(
        "build",
        help="Build the image from Containerfile",
    )
    build.add_argument("--tag", default=DEFAULT_IMAGE_TAG)
    build.set_defaults(func=cmd_build)

    test = sub.add_parser(
        "test",
        help="Fresh-build runner image, launch temporary test stack, and verify Docker build",
    )
    test.add_argument("--tag", default=DEFAULT_IMAGE_TAG)
    test.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TEST_TIMEOUT_SECONDS,
        help="Seconds to wait for temporary docker:dind to become ready",
    )
    test.set_defaults(func=cmd_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except CliError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}", file=sys.stderr)
        if exc.stdout:
            print(exc.stdout, file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
