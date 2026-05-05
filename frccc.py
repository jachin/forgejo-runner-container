#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

DEFAULT_IMAGE_TAG = "local/forgejo-runner-docker:12"


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


def ensure_builder_ready() -> None:
    print("Running build preflight: checking builder...")
    status_proc = run(
        ["container", "builder", "status"], check=False, capture_output=True
    )
    status_text = f"{status_proc.stdout}\n{status_proc.stderr}".strip().lower()

    if "is running" in status_text:
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
    verify_text = f"{verify_proc.stdout}\n{verify_proc.stderr}".strip().lower()
    if "is running" not in verify_text:
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

    run(["container", "build", "-t", args.tag, "."])
    print(f"Built image: {args.tag}")
    return 0


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
