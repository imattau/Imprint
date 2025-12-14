"""Simple cross-platform task runner for development workflows."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent


def run_command(command: list[str], *, env: dict[str, str] | None = None) -> None:
    """Execute a shell command, raising on failure."""

    print("$", " ".join(command))
    try:
        result = subprocess.run(command, check=False, env=env)
    except FileNotFoundError as exc:  # pragma: no cover - depends on local env
        missing = command[0]
        message = f"Required command '{missing}' was not found. Is it installed and on your PATH?"
        raise SystemExit(message) from exc

    if result.returncode != 0:
        raise SystemExit(result.returncode)


def cmd_install(_args: argparse.Namespace) -> None:
    run_command(["poetry", "install"])


def cmd_run(args: argparse.Namespace) -> None:
    env = os.environ.copy()
    if args.host:
        env["APP_HOST"] = args.host
    if args.port:
        env["APP_PORT"] = str(args.port)
    run_command(["poetry", "run", "imprint"], env=env)


def cmd_test(_args: argparse.Namespace) -> None:
    run_command(["poetry", "run", "pytest"])


def cmd_format(_args: argparse.Namespace) -> None:
    run_command(["poetry", "run", "ruff", "format"])


def cmd_lint(_args: argparse.Namespace) -> None:
    run_command(["poetry", "run", "ruff", "check"])
    run_command(["poetry", "run", "mypy", "app", "tests"])


async def _init_db() -> None:
    from app.main import init_models

    await init_models()


def cmd_db(_args: argparse.Namespace) -> None:
    asyncio.run(_init_db())
    print("Database schema is initialized.")


def cmd_clean(_args: argparse.Namespace) -> None:
    patterns = ["**/__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"]
    removed = 0
    for pattern in patterns:
        for path in ROOT.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    print(f"Removed {removed} cache directories.")


COMMANDS = {
    "install": cmd_install,
    "run": cmd_run,
    "test": cmd_test,
    "format": cmd_format,
    "lint": cmd_lint,
    "db": cmd_db,
    "clean": cmd_clean,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("install", help="Install all dependencies via Poetry.")

    run_parser = subparsers.add_parser("run", help="Start the development server.")
    run_parser.add_argument("--host", help="Host to bind the development server.")
    run_parser.add_argument("--port", type=int, help="Port to bind the development server.")

    subparsers.add_parser("test", help="Run the test suite.")
    subparsers.add_parser("format", help="Format code with Ruff.")
    subparsers.add_parser("lint", help="Run static analysis (Ruff + mypy).")
    subparsers.add_parser("db", help="Initialize the database schema without starting the server.")
    subparsers.add_parser("clean", help="Remove Python and tooling caches.")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = COMMANDS.get(args.command)
    if handler is None:
        parser.error(f"Unknown command: {args.command}")
    handler(args)


if __name__ == "__main__":
    main()
