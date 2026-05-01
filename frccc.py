#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_NETWORK = "forgejo-net"
DEFAULT_RUNNER_NAME = "forgejo-runner"
DEFAULT_BASE_IMAGE = "data.forgejo.org/forgejo/runner:12"


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


def start_runner_container(
    *,
    base_image: str,
    network_name: str,
    runner_name: str,
    runner_config: Path,
) -> None:
    run(["container", "delete", "-f", runner_name], check=False)

    config_dir = runner_config.parent.resolve()
    config_name = runner_config.name

    run(
        [
            "container",
            "run",
            "-d",
            "--name",
            runner_name,
            "--network",
            network_name,
            "-v",
            f"{config_dir}:/data",
            base_image,
            "forgejo-runner",
            "daemon",
            "--config",
            f"/data/{config_name}",
        ]
    )


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

    start_runner_container(
        base_image=args.base_image,
        network_name=args.network_name,
        runner_name=args.runner_name,
        runner_config=runner_config,
    )

    print("Runner started. Use `./frccc.py logs` to follow output.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    ensure_container_binary()
    ensure_container_system_started()
    run(["container", "list", "--all"])
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    ensure_container_binary()
    ensure_container_system_started()

    cmd = ["container", "logs"]
    if args.follow:
        cmd.append("-f")
    cmd.append(args.runner_name)
    run(cmd)
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    ensure_container_binary()
    ensure_container_system_started()

    run(["container", "stop", args.runner_name], check=False)
    print("Runner stopped.")
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
    start.add_argument("--runner-labels", default="container,macos")
    start.add_argument("--forgejo-url", default=None)
    start.add_argument("--runner-token", default=None)
    start.set_defaults(func=cmd_start)

    status = sub.add_parser("status", help="Show container status")
    status.set_defaults(func=cmd_status)

    logs = sub.add_parser("logs", help="Show runner logs")
    logs.add_argument("--runner-name", default=DEFAULT_RUNNER_NAME)
    logs.add_argument("-f", "--follow", action="store_true")
    logs.set_defaults(func=cmd_logs)

    stop = sub.add_parser("stop", help="Stop runner container")
    stop.add_argument("--runner-name", default=DEFAULT_RUNNER_NAME)
    stop.set_defaults(func=cmd_stop)

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
