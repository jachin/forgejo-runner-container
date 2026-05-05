#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Protocol, cast

DEFAULT_IMAGE_TAG = "local/forgejo-runner-docker:12"
DEFAULT_TEST_TIMEOUT_SECONDS = 60
DEFAULT_NETWORK_NAME = "forgejo-net"
DEFAULT_RUNNER_NAME = "forgejo-runner"
DEFAULT_DIND_NAME = "docker-dind"
DEFAULT_DIND_VOLUME = "forgejo-dind-data"
DEFAULT_DIND_PORT = 2375
DEFAULT_RUNNER_DATA_DIR = "./runner-data"
DEFAULT_RUNNER_CONFIG = "runner-config.yml"


class CliError(RuntimeError):
    pass


JsonObject = dict[str, object]


class StatusArgs(Protocol):
    runner_data_dir: str
    runner_name: str
    dind_name: str


class BuildArgs(Protocol):
    tag: str


class TestArgs(Protocol):
    tag: str
    timeout: int


class StartArgs(Protocol):
    tag: str
    network_name: str
    runner_name: str
    dind_name: str
    dind_volume: str
    dind_port: int
    runner_data_dir: str
    timeout: int


class StopArgs(Protocol):
    runner_name: str
    dind_name: str


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
            "Container service does not appear to be running. Start it first with `brew services start container`."
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
    _ = run(["container", "builder", "stop"], check=False)
    _ = run(["container", "builder", "delete"], check=False)

    start_proc = run(
        ["container", "builder", "start"], check=False, capture_output=True
    )
    if start_proc.returncode != 0:
        raise CliError(
            f"Failed to start builder during preflight. stdout: {start_proc.stdout.strip()} stderr: {start_proc.stderr.strip()}"
        )

    verify_proc = run(
        ["container", "builder", "status"], check=False, capture_output=True
    )
    verify_text = f"{verify_proc.stdout}\n{verify_proc.stderr}"
    if not builder_status_running(verify_text):
        raise CliError(
            f"Builder preflight did not reach a running state. status output: {verify_proc.stdout.strip()} {verify_proc.stderr.strip()}"
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
    _ = run(cmd)


def write_test_container_context(target_dir: Path) -> None:
    dockerfile = target_dir / "Dockerfile"
    marker = target_dir / "hello.txt"

    _ = dockerfile.write_text(
        "\n".join(
            [
                "FROM alpine:3.20",
                "COPY hello.txt /hello.txt",
                "RUN test -f /hello.txt",
                'CMD ["cat", "/hello.txt"]',
            ]
        )
        + "\n"
    )
    _ = marker.write_text("hello from frccc test\n")


def start_dind_container(
    dind_name: str,
    network_name: str,
    dind_volume: str,
    dind_port: int,
) -> None:
    common = [
        "container",
        "run",
        "-d",
        "--name",
        dind_name,
        "--network",
        network_name,
        "-v",
        f"{dind_volume}:/var/lib/docker",
    ]

    if dind_port > 0:
        common.extend(["-p", f"127.0.0.1:{dind_port}:2375"])

    tail = [
        "docker:dind",
        "dockerd",
        "-H",
        "tcp://0.0.0.0:2375",
        "--tls=false",
    ]

    base_cmd = [*common, *tail]
    cmd_with_caps = [*common, "--cap-add", "ALL", *tail]

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
            f"Failed to start docker:dind without --cap-add. stdout: {proc2.stdout.strip()} stderr: {proc2.stderr.strip()}"
        )

    raise CliError(
        f"Failed to start docker:dind. stdout: {proc.stdout.strip()} stderr: {proc.stderr.strip()}"
    )


def create_network_if_missing(network_name: str) -> None:
    proc = run(
        ["container", "network", "create", network_name],
        check=False,
        capture_output=True,
    )
    if proc.returncode == 0:
        return

    text = f"{proc.stdout}\n{proc.stderr}".lower()
    if "already exists" in text:
        return

    raise CliError(
        f"Failed to create network {network_name}. stdout: {proc.stdout.strip()} stderr: {proc.stderr.strip()}"
    )


def create_volume_if_missing(volume_name: str) -> None:
    proc = run(
        ["container", "volume", "create", volume_name], check=False, capture_output=True
    )
    if proc.returncode == 0:
        return

    text = f"{proc.stdout}\n{proc.stderr}".lower()
    if "already exists" in text:
        return

    raise CliError(
        f"Failed to create volume {volume_name}. stdout: {proc.stdout.strip()} stderr: {proc.stderr.strip()}"
    )


def as_json_object(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None

    obj: JsonObject = {}
    source = cast(dict[object, object], value)
    for key, val in source.items():
        if isinstance(key, str):
            obj[key] = val
    return obj


def as_json_object_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []

    out: list[JsonObject] = []
    source = cast(list[object], value)
    for item in source:
        obj = as_json_object(item)
        if obj is not None:
            out.append(obj)
    return out


def get_obj_str(obj: JsonObject, key: str) -> str | None:
    value = obj.get(key)
    if isinstance(value, str):
        return value
    return None


def parse_container_inspect(stdout: str, container_name: str) -> JsonObject:
    raw = cast(object, json.loads(stdout))
    payload = as_json_object_list(raw)
    if not payload:
        raise CliError(f"Unexpected inspect output for container {container_name}")
    return payload[0]


def get_container_ipv4(container_name: str, network_name: str) -> str:
    proc = run(["container", "inspect", container_name], capture_output=True)

    details = parse_container_inspect(proc.stdout, container_name)
    networks = as_json_object_list(details.get("networks"))

    for net in networks:
        net_id = get_obj_str(net, "network") or get_obj_str(net, "id")
        address = (
            get_obj_str(net, "address")
            or get_obj_str(net, "addr")
            or get_obj_str(net, "ipv4Address")
        )
        if net_id == network_name and address:
            return address.split("/")[0]

    for net in networks:
        address = (
            get_obj_str(net, "address")
            or get_obj_str(net, "addr")
            or get_obj_str(net, "ipv4Address")
        )
        if address:
            return address.split("/")[0]

    raise CliError(
        f"Could not determine IP address for container {container_name} on network {network_name}"
    )


def wait_for_dind(
    dind_host: str,
    dind_name: str,
    network_name: str,
    timeout_seconds: int,
) -> None:
    print(
        f"Waiting for docker:dind to become ready at {dind_host}:2375 (timeout: {timeout_seconds}s)..."
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
            print("docker:dind is ready.")
            return

        time.sleep(1)

    raise CliError(
        f"Timed out waiting for docker:dind to become ready. Check logs with: container logs -n 200 {dind_name}"
    )


def run_temp_runner_docker_build(
    *,
    runner_image: str,
    network_name: str,
    dind_host: str,
    context_dir: Path,
    test_image_tag: str,
) -> None:
    _ = run(
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


def parse_simple_yaml(
    config_path: Path,
) -> tuple[dict[tuple[str, ...], str], list[str]]:
    values: dict[tuple[str, ...], str] = {}
    errors: list[str] = []
    stack: list[tuple[int, str]] = []

    key_pattern = re.compile(r"^(\s*)([^:#\n][^:\n]*?):(?:\s*(.*))?$")

    for line_no, raw_line in enumerate(config_path.read_text().splitlines(), start=1):
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-"):
            continue

        match = key_pattern.match(line)
        if not match:
            continue

        indent = len(match.group(1))
        key = match.group(2).strip().strip('"').strip("'")
        value = match.group(3)

        while stack and indent <= stack[-1][0]:
            _ = stack.pop()

        path = tuple([part for _, part in stack] + [key])

        if value is None or value == "":
            stack.append((indent, key))
            continue

        v = value.strip()
        if v.startswith("#"):
            v = ""

        if v in ("|", ">"):
            errors.append(
                f"line {line_no}: block scalar is not supported by this validator"
            )
            continue

        values[path] = v.strip('"').strip("'")

    return values, errors


def validate_runner_data_paths(
    runner_data_dir: Path,
) -> tuple[bool, bool, Path]:
    config_path = runner_data_dir / DEFAULT_RUNNER_CONFIG
    return runner_data_dir.is_dir(), config_path.is_file(), config_path


def validate_runner_config(
    config_path: Path,
) -> tuple[bool, str, dict[tuple[str, ...], str]]:
    if not config_path.is_file():
        return False, "file not found", {}

    try:
        values, errors = parse_simple_yaml(config_path)
    except Exception as exc:
        return False, f"unreadable: {exc}", {}

    if errors:
        return False, "; ".join(errors), values

    if not values:
        return False, "no key/value entries found", values

    return True, "basic YAML structure parsed", values


def container_running_state(container_name: str) -> tuple[bool, str]:
    proc = run(
        ["container", "inspect", container_name], check=False, capture_output=True
    )
    if proc.returncode != 0:
        text = f"{proc.stdout}\n{proc.stderr}".strip().lower()
        if "not found" in text:
            return False, "not found"
        return False, "unavailable"

    try:
        details = parse_container_inspect(proc.stdout, container_name)
    except (json.JSONDecodeError, CliError):
        return False, "inspect output invalid"

    status = (get_obj_str(details, "status") or "").strip().lower()
    if status == "running":
        return True, "running"
    if status:
        return False, status
    return False, "unknown"


def resolve_runner_env_file_path(
    runner_data_dir: Path,
    parsed_values: dict[tuple[str, ...], str],
) -> Path | None:
    env_file = parsed_values.get(("runner", "env_file"), "").strip()
    if not env_file:
        return None

    env_path = Path(env_file)
    if env_path.is_absolute():
        return env_path

    return runner_data_dir / env_path


def write_runner_env_file(
    runner_data_dir: Path, config_path: Path, docker_host: str
) -> None:
    valid, detail, parsed_values = validate_runner_config(config_path)
    if not valid:
        raise CliError(f"runner-config.yml became invalid: {detail}")

    env_file_path = resolve_runner_env_file_path(runner_data_dir, parsed_values)
    if env_file_path is None:
        return

    env_file_path.parent.mkdir(parents=True, exist_ok=True)
    _ = env_file_path.write_text(
        f"DOCKER_HOST={docker_host}\nCONTAINER_DOCKER_HOST={docker_host}\n"
    )


def ensure_runner_config_ready(runner_data_dir: Path) -> Path:
    dir_exists, file_exists, config_path = validate_runner_data_paths(runner_data_dir)

    if not dir_exists:
        raise CliError(
            f"Missing runner data directory: {runner_data_dir}. Create it and place runner-config.yml there."
        )

    if not file_exists:
        raise CliError(
            f"Missing runner config file: {config_path}. Create runner-data/runner-config.yml first."
        )

    valid, detail, _parsed = validate_runner_config(config_path)
    if not valid:
        raise CliError(f"runner-config.yml is not valid enough to use: {detail}")

    return config_path


def cmd_status(args: StatusArgs) -> int:
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

    runner_data_dir = Path(args.runner_data_dir)
    data_dir_exists, config_exists, config_path = validate_runner_data_paths(
        runner_data_dir
    )

    config_valid = False
    config_valid_detail = "skipped: config file not found"

    if config_exists:
        config_valid, config_valid_detail, _parsed = validate_runner_config(config_path)

    dind_running = False
    dind_detail = "skipped: container CLI not responsive"
    runner_running = False
    runner_detail = "skipped: container CLI not responsive"

    if cli_responsive:
        dind_running, dind_detail = container_running_state(args.dind_name)
        runner_running, runner_detail = container_running_state(args.runner_name)

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
        (
            "runner-data directory",
            "OK" if data_dir_exists else "FAIL",
            str(runner_data_dir.resolve()) if data_dir_exists else "missing",
        ),
        (
            "runner-config.yml exists",
            "OK" if config_exists else "FAIL",
            str(config_path.resolve()) if config_exists else "missing",
        ),
        (
            "runner-config.yml valid",
            "OK" if config_valid else "FAIL",
            config_valid_detail,
        ),
        (
            "docker runtime wiring",
            "OK",
            "frccc sets DOCKER_HOST + CONTAINER_DOCKER_HOST at container start",
        ),
        (
            "docker sidecar running",
            "OK" if dind_running else "FAIL",
            f"{args.dind_name}: {dind_detail}",
        ),
        (
            "runner container running",
            "OK" if runner_running else "FAIL",
            f"{args.runner_name}: {runner_detail}",
        ),
    ]

    print_status_table(rows)

    if not cli_responsive:
        print("\nTo start the container service:")
        print("  brew services start container")

    all_required_ok = (
        container_cli_installed
        and cli_responsive
        and data_dir_exists
        and config_exists
        and config_valid
        and dind_running
        and runner_running
    )
    return 0 if all_required_ok else 1


def cmd_build(args: BuildArgs) -> int:
    ensure_container_binary()
    require_container_service_running()
    ensure_builder_ready()

    build_runner_image(args.tag)
    print(f"Built image: {args.tag}")
    return 0


def cmd_test(args: TestArgs) -> int:
    ensure_container_binary()
    require_container_service_running()
    ensure_builder_ready()

    print("Running fresh build for test...")
    build_runner_image(args.tag, no_cache=True)

    suffix = uuid.uuid4().hex[:8]
    network_name = f"frccc-test-net-{suffix}"
    dind_name = f"frccc-test-dind-{suffix}"
    dind_volume = f"frccc-test-vol-{suffix}"
    test_image_tag = f"frccc-test-image:{suffix}"

    print("Preparing temporary test environment...")
    _ = run(["container", "network", "create", network_name])

    try:
        start_dind_container(dind_name, network_name, dind_volume, 0)
        dind_host = get_container_ipv4(dind_name, network_name)
        wait_for_dind(dind_host, dind_name, network_name, args.timeout)

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
        _ = run(["container", "delete", "-f", dind_name], check=False)
        _ = run(["container", "network", "delete", network_name], check=False)
        _ = run(["container", "volume", "delete", dind_volume], check=False)


def cmd_start(args: StartArgs) -> int:
    ensure_container_binary()
    require_container_service_running()

    runner_data_dir = Path(args.runner_data_dir)
    config_path = ensure_runner_config_ready(runner_data_dir)

    create_network_if_missing(args.network_name)
    create_volume_if_missing(args.dind_volume)

    _ = run(["container", "delete", "-f", args.runner_name], check=False)
    _ = run(["container", "delete", "-f", args.dind_name], check=False)

    start_dind_container(
        dind_name=args.dind_name,
        network_name=args.network_name,
        dind_volume=args.dind_volume,
        dind_port=args.dind_port,
    )

    dind_host = get_container_ipv4(args.dind_name, args.network_name)
    wait_for_dind(dind_host, args.dind_name, args.network_name, args.timeout)

    docker_host = f"tcp://{dind_host}:2375"
    write_runner_env_file(runner_data_dir, config_path, docker_host)

    _ = run(
        [
            "container",
            "run",
            "-d",
            "--name",
            args.runner_name,
            "--network",
            args.network_name,
            "-e",
            f"DOCKER_HOST={docker_host}",
            "-e",
            f"CONTAINER_DOCKER_HOST={docker_host}",
            "-v",
            f"{runner_data_dir.resolve()}:/data",
            args.tag,
            "forgejo-runner",
            "daemon",
            "--config",
            f"/data/{config_path.name}",
        ]
    )

    print("Runner stack started.")
    print(f"- docker sidecar: {args.dind_name} ({docker_host})")
    print(f"- runner: {args.runner_name}")
    return 0


def cmd_stop(args: StopArgs) -> int:
    ensure_container_binary()
    require_container_service_running()

    _ = run(["container", "delete", "-f", args.runner_name], check=False)
    _ = run(["container", "delete", "-f", args.dind_name], check=False)

    print("Runner stack stopped.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="frccc",
        description="Forgejo Runner Container Command Center",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser(
        "status",
        help="Show environment and runner-config status checks",
    )
    _ = status.add_argument("--runner-data-dir", default=DEFAULT_RUNNER_DATA_DIR)
    _ = status.add_argument("--runner-name", default=DEFAULT_RUNNER_NAME)
    _ = status.add_argument("--dind-name", default=DEFAULT_DIND_NAME)
    status.set_defaults(func=cmd_status)

    build = sub.add_parser(
        "build",
        help="Build the image from Containerfile",
    )
    _ = build.add_argument("--tag", default=DEFAULT_IMAGE_TAG)
    build.set_defaults(func=cmd_build)

    test = sub.add_parser(
        "test",
        help="Fresh-build runner image, launch temporary test stack, and verify Docker build",
    )
    _ = test.add_argument("--tag", default=DEFAULT_IMAGE_TAG)
    _ = test.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TEST_TIMEOUT_SECONDS,
        help="Seconds to wait for temporary docker:dind to become ready",
    )
    test.set_defaults(func=cmd_test)

    start = sub.add_parser(
        "start",
        help="Start docker:dind and forgejo-runner containers",
    )
    _ = start.add_argument("--tag", default=DEFAULT_IMAGE_TAG)
    _ = start.add_argument("--network-name", default=DEFAULT_NETWORK_NAME)
    _ = start.add_argument("--runner-name", default=DEFAULT_RUNNER_NAME)
    _ = start.add_argument("--dind-name", default=DEFAULT_DIND_NAME)
    _ = start.add_argument("--dind-volume", default=DEFAULT_DIND_VOLUME)
    _ = start.add_argument("--dind-port", type=int, default=DEFAULT_DIND_PORT)
    _ = start.add_argument("--runner-data-dir", default=DEFAULT_RUNNER_DATA_DIR)
    _ = start.add_argument("--timeout", type=int, default=DEFAULT_TEST_TIMEOUT_SECONDS)
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser(
        "stop",
        help="Stop and remove docker:dind and forgejo-runner containers",
    )
    _ = stop.add_argument("--runner-name", default=DEFAULT_RUNNER_NAME)
    _ = stop.add_argument("--dind-name", default=DEFAULT_DIND_NAME)
    stop.set_defaults(func=cmd_stop)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = cast(str, getattr(args, "command", ""))

    try:
        if command == "status":
            return cmd_status(cast(StatusArgs, cast(object, args)))
        if command == "build":
            return cmd_build(cast(BuildArgs, cast(object, args)))
        if command == "test":
            return cmd_test(cast(TestArgs, cast(object, args)))
        if command == "start":
            return cmd_start(cast(StartArgs, cast(object, args)))
        if command == "stop":
            return cmd_stop(cast(StopArgs, cast(object, args)))

        raise CliError(f"Unknown command: {command}")
    except CliError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}", file=sys.stderr)
        stdout_raw = cast(object, exc.stdout)
        stderr_raw = cast(object, exc.stderr)
        stdout_text = stdout_raw if isinstance(stdout_raw, str) else ""
        stderr_text = stderr_raw if isinstance(stderr_raw, str) else ""
        if stdout_text:
            print(stdout_text, file=sys.stderr)
        if stderr_text:
            print(stderr_text, file=sys.stderr)
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
