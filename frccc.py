#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_NETWORK = "forgejo-net"
DEFAULT_RUNNER_NAME = "forgejo-runner"
DEFAULT_DIND_NAME = "docker-dind"
DEFAULT_DIND_VOLUME = "forgejo-dind-data"
DEFAULT_DIND_PORT = 2375
DEFAULT_BASE_IMAGE = "data.forgejo.org/forgejo/runner:12"
DEFAULT_DOCKER_RUNNER_IMAGE = "local/forgejo-runner-docker:12"


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


def ensure_container_system_started() -> None:
    run(["container", "system", "start"])


def ensure_network_exists(network_name: str) -> None:
    proc = run(
        ["container", "network", "list", "--format", "json"], capture_output=True
    )
    raw = proc.stdout

    exists = f'"{network_name}"' in raw
    if not exists:
        run(["container", "network", "create", network_name])


def ensure_volume_exists(volume_name: str) -> None:
    proc = run(["container", "volume", "list", "--format", "json"], capture_output=True)
    raw = proc.stdout

    exists = f'"{volume_name}"' in raw
    if not exists:
        run(["container", "volume", "create", volume_name])


def ensure_runner_config(base_image: str, runner_config: Path) -> None:
    runner_config.parent.mkdir(parents=True, exist_ok=True)
    if runner_config.exists():
        print(f"Runner config exists: {runner_config}")
        return

    proc = run(
        ["container", "run", "--rm", base_image, "forgejo-runner", "generate-config"],
        capture_output=True,
    )
    runner_config.write_text(proc.stdout)
    print(f"Generated config: {runner_config}")


def runner_registered_marker(runner_config: Path) -> Path:
    return runner_config.parent / ".runner"


def register_runner_non_interactive(
    *,
    base_image: str,
    runner_config: Path,
    runner_name: str,
    runner_labels: str,
    forgejo_url: str,
    runner_token: str,
) -> None:
    marker = runner_registered_marker(runner_config)
    if marker.exists():
        print(f"Runner registration exists: {marker}")
        return

    config_dir = runner_config.parent.resolve()
    config_name = runner_config.name

    run(
        [
            "container",
            "run",
            "--rm",
            "-i",
            "-v",
            f"{config_dir}:/data",
            base_image,
            "forgejo-runner",
            "register",
            "--no-interactive",
            "--instance",
            forgejo_url,
            "--token",
            runner_token,
            "--name",
            runner_name,
            "--labels",
            runner_labels,
            "--config",
            f"/data/{config_name}",
        ]
    )


def wait_for_dind(
    *,
    dind_name: str,
    dind_port: int,
    timeout_seconds: int,
) -> None:
    print(
        f"Waiting for Docker daemon in {dind_name} on 127.0.0.1:{dind_port} "
        f"(timeout: {timeout_seconds}s)..."
    )
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", dind_port), timeout=2):
                print("Docker daemon TCP port is reachable.")
                return
        except OSError:
            time.sleep(1)

    raise CliError(
        "Timed out waiting for docker:dind TCP port to become ready. "
        "Check logs with: container logs -n 200 docker-dind"
    )


def start_dind_container(
    *,
    dind_name: str,
    network_name: str,
    dind_volume: str,
    dind_port: int,
) -> None:
    run(["container", "delete", "-f", dind_name], check=False)

    run(
        [
            "container",
            "run",
            "-d",
            "--name",
            dind_name,
            "--network",
            network_name,
            "--cap-add",
            "ALL",
            "-v",
            f"{dind_volume}:/var/lib/docker",
            "-p",
            f"127.0.0.1:{dind_port}:2375",
            "docker:dind",
            "dockerd",
            "-H",
            "tcp://0.0.0.0:2375",
            "--tls=false",
        ]
    )


def start_runner_container(
    *,
    runner_image: str,
    network_name: str,
    runner_name: str,
    runner_config: Path,
    docker_host: str | None,
) -> None:
    run(["container", "delete", "-f", runner_name], check=False)

    config_dir = runner_config.parent.resolve()
    config_name = runner_config.name

    cmd = [
        "container",
        "run",
        "-d",
        "--name",
        runner_name,
        "--network",
        network_name,
        "-v",
        f"{config_dir}:/data",
    ]

    if docker_host:
        cmd.extend(["-e", f"DOCKER_HOST=tcp://{docker_host}:2375"])

    cmd.extend(
        [
            runner_image,
            "forgejo-runner",
            "daemon",
            "--config",
            f"/data/{config_name}",
        ]
    )

    run(cmd)


def resolve_runner_image(args: argparse.Namespace) -> str:
    if args.runner_image:
        return args.runner_image
    if args.with_docker:
        return DEFAULT_DOCKER_RUNNER_IMAGE
    return args.base_image


def cmd_start(args: argparse.Namespace) -> int:
    ensure_container_binary()
    ensure_container_system_started()

    runner_data_dir = Path(args.runner_data_dir)
    runner_data_dir.mkdir(parents=True, exist_ok=True)

    runner_config = (
        Path(args.runner_config)
        if args.runner_config
        else runner_data_dir / "runner-config.yml"
    )

    ensure_network_exists(args.network_name)
    ensure_runner_config(args.base_image, runner_config)

    if args.forgejo_url and args.runner_token:
        register_runner_non_interactive(
            base_image=args.base_image,
            runner_config=runner_config,
            runner_name=args.runner_name,
            runner_labels=args.runner_labels,
            forgejo_url=args.forgejo_url,
            runner_token=args.runner_token,
        )
    else:
        print(
            "Skipping registration (missing --forgejo-url or --runner-token). "
            "You can still start the container, but jobs will not be picked up until registered."
        )

    runner_image = resolve_runner_image(args)

    if args.with_docker:
        ensure_volume_exists(args.dind_volume)
        start_dind_container(
            dind_name=args.dind_name,
            network_name=args.network_name,
            dind_volume=args.dind_volume,
            dind_port=args.dind_port,
        )
        wait_for_dind(
            dind_name=args.dind_name,
            dind_port=args.dind_port,
            timeout_seconds=args.dind_wait_timeout,
        )
        docker_host = args.dind_host or args.dind_name
    else:
        docker_host = None

    start_runner_container(
        runner_image=runner_image,
        network_name=args.network_name,
        runner_name=args.runner_name,
        runner_config=runner_config,
        docker_host=docker_host,
    )

    print("Runner started.")
    if args.with_docker:
        print(
            f"Docker sidecar started: {args.dind_name} on tcp://127.0.0.1:{args.dind_port}"
        )
    print("Use `./frccc.py logs -f` to follow runner logs.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    ensure_container_binary()
    ensure_container_system_started()
    run(["container", "list", "--all"])
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    ensure_container_binary()
    ensure_container_system_started()

    targets: list[str]
    if args.target == "both":
        targets = [args.runner_name, args.dind_name]
    elif args.target == "dind":
        targets = [args.dind_name]
    else:
        targets = [args.runner_name]

    for idx, target in enumerate(targets):
        if len(targets) > 1:
            print(f"\n===== logs: {target} =====")
        cmd = ["container", "logs"]
        if args.follow:
            cmd.append("-f")
        if args.lines is not None:
            cmd.extend(["-n", str(args.lines)])
        cmd.append(target)
        run(cmd)
        if args.follow and idx < len(targets) - 1:
            break

    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    ensure_container_binary()
    ensure_container_system_started()

    run(["container", "stop", args.runner_name], check=False)
    run(["container", "stop", args.dind_name], check=False)
    print("Stopped runner and docker sidecar (if running).")
    return 0


def cmd_build_runner_image(args: argparse.Namespace) -> int:
    ensure_container_binary()
    ensure_container_system_started()
    run(["container", "build", "-t", args.tag, "."])
    print(f"Built image: {args.tag}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="frccc",
        description="Forgejo Runner Container Command Center",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start Forgejo runner in container")
    start.add_argument("--network-name", default=DEFAULT_NETWORK)
    start.add_argument("--runner-name", default=DEFAULT_RUNNER_NAME)
    start.add_argument("--runner-data-dir", default="./runner-data")
    start.add_argument(
        "-c",
        "--runner-config",
        default=None,
        help="Path to runner config file (default: <runner-data-dir>/runner-config.yml)",
    )
    start.add_argument("--base-image", default=DEFAULT_BASE_IMAGE)
    start.add_argument(
        "--runner-image",
        default=None,
        help=(
            "Runner image used for daemon. "
            "Default is base image without --with-docker, "
            "or local/forgejo-runner-docker:12 with --with-docker."
        ),
    )
    start.add_argument("--runner-labels", default="container,macos")
    start.add_argument("--forgejo-url", default=None)
    start.add_argument("--runner-token", default=None)
    start.add_argument(
        "--with-docker",
        action="store_true",
        help="Start docker:dind sidecar and connect runner to it via DOCKER_HOST",
    )
    start.add_argument("--dind-name", default=DEFAULT_DIND_NAME)
    start.add_argument(
        "--dind-host",
        default=None,
        help="Host/IP for runner to reach Docker daemon (default: --dind-name)",
    )
    start.add_argument("--dind-volume", default=DEFAULT_DIND_VOLUME)
    start.add_argument("--dind-port", type=int, default=DEFAULT_DIND_PORT)
    start.add_argument("--dind-wait-timeout", type=int, default=60)
    start.set_defaults(func=cmd_start)

    status = sub.add_parser("status", help="Show container status")
    status.set_defaults(func=cmd_status)

    logs = sub.add_parser("logs", help="Show logs")
    logs.add_argument("--runner-name", default=DEFAULT_RUNNER_NAME)
    logs.add_argument("--dind-name", default=DEFAULT_DIND_NAME)
    logs.add_argument(
        "--target",
        choices=["runner", "dind", "both"],
        default="runner",
        help="Which logs to show",
    )
    logs.add_argument("-n", "--lines", type=int, default=100)
    logs.add_argument("-f", "--follow", action="store_true")
    logs.set_defaults(func=cmd_logs)

    stop = sub.add_parser("stop", help="Stop runner and docker sidecar")
    stop.add_argument("--runner-name", default=DEFAULT_RUNNER_NAME)
    stop.add_argument("--dind-name", default=DEFAULT_DIND_NAME)
    stop.set_defaults(func=cmd_stop)

    build = sub.add_parser(
        "build-runner-image", help="Build docker-enabled runner image"
    )
    build.add_argument("--tag", default=DEFAULT_DOCKER_RUNNER_IMAGE)
    build.set_defaults(func=cmd_build_runner_image)

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
