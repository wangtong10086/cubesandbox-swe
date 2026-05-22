"""Command-line interface for CubeSandbox SWE workflows."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from .artifacts import write_manifest
from .doctor import main as doctor_main
from .legacy_runner import run_legacy


HELP = """cubesandbox-swe

Usage:
  cubesandbox-swe solve [legacy solve options] [--dry-run]
  cubesandbox-swe verify [legacy verifier options] [--dry-run]
  cubesandbox-swe collect swe50 [legacy collector options]
  cubesandbox-swe templates prepare [--dry-run]
  cubesandbox-swe templates smoke [--dry-run] [--limit N] [--template-id ID]
  cubesandbox-swe images test [--dry-run]
  cubesandbox-swe artifacts summarize [--output PATH]
  cubesandbox-swe doctor [--runtime] [--runtime-smoke] [--codex-runtime-smoke] [--model MODEL]
  cubesandbox-swe legacy gpt55-e2e [legacy options]
"""


def _print_help() -> int:
    print(HELP.rstrip())
    return 0


def _dry_run(command: str, module_name: str) -> int:
    print(f"dry-run: would execute {command} via cubesandbox_swe.legacy.{module_name}")
    return 0


def _run_simple_legacy(command: str, module_name: str, argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"cubesandbox-swe {command}")
    parser.add_argument("--dry-run", action="store_true", help="show the action without executing it")
    args = parser.parse_args(list(argv))
    if args.dry_run:
        return _dry_run(command, module_name)
    return run_legacy(module_name, [])


def _run_legacy_with_cli_dry_run(command: str, module_name: str, argv: Sequence[str]) -> int:
    if "--dry-run" in argv:
        return _dry_run(command, module_name)
    return run_legacy(module_name, argv)


def _run_passthrough(command: str, module_name: str, argv: Sequence[str]) -> int:
    if "--dry-run" in argv:
        return _dry_run(command, module_name)
    return run_legacy(module_name, argv)


def _artifacts(argv: Sequence[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print("Usage: cubesandbox-swe artifacts summarize [--output PATH]")
        return 0
    if argv[0] != "summarize":
        raise SystemExit(f"unknown artifacts command: {argv[0]}")
    parser = argparse.ArgumentParser(prog="cubesandbox-swe artifacts summarize")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(list(argv[1:]))
    out_path = write_manifest(args.output)
    print(out_path)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        return _print_help()

    command = argv[0]
    rest = argv[1:]
    if command == "solve":
        return _run_passthrough("solve", "run_cubesandbox_codex_swe_e2e", rest)
    if command == "verify":
        return _run_passthrough("verify", "run_affinetes_cubesandbox_swe_e2e", rest)
    if command == "collect":
        if not rest or rest[0] in {"-h", "--help"}:
            print("Usage: cubesandbox-swe collect swe50 [legacy collector options]")
            return 0
        if rest[0] != "swe50":
            raise SystemExit(f"unknown collect command: {rest[0]}")
        if "--dry-run" in rest[1:]:
            return _dry_run("collect swe50", "collect_cubesandbox_codex_swe50")
        return run_legacy("collect_cubesandbox_codex_swe50", rest[1:])
    if command == "templates":
        if not rest or rest[0] in {"-h", "--help"}:
            print("Usage: cubesandbox-swe templates prepare|smoke [--dry-run] [smoke options]")
            return 0
        if rest[0] == "prepare":
            return _run_simple_legacy("templates prepare", "prepare_cubesandbox_swe_templates", rest[1:])
        if rest[0] == "smoke":
            return _run_legacy_with_cli_dry_run("templates smoke", "smoke_cubesandbox_swe_templates", rest[1:])
        raise SystemExit(f"unknown templates command: {rest[0]}")
    if command == "images":
        if not rest or rest[0] in {"-h", "--help"}:
            print("Usage: cubesandbox-swe images test [--dry-run]")
            return 0
        if rest[0] != "test":
            raise SystemExit(f"unknown images command: {rest[0]}")
        return _run_simple_legacy("images test", "test_swe_infinite_images_50", rest[1:])
    if command == "artifacts":
        return _artifacts(rest)
    if command == "doctor":
        return doctor_main(rest)
    if command == "legacy":
        if not rest or rest[0] in {"-h", "--help"}:
            print("Usage: cubesandbox-swe legacy gpt55-e2e [legacy options]")
            return 0
        if rest[0] == "gpt55-e2e":
            return _run_passthrough("legacy gpt55-e2e", "run_gpt55_cubesandbox_swe_e2e", rest[1:])
        raise SystemExit(f"unknown legacy command: {rest[0]}")

    raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    raise SystemExit(main())
