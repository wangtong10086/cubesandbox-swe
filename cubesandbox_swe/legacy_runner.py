"""Dispatch helpers for the legacy runner modules."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence


def run_legacy(module_name: str, argv: Sequence[str]) -> int:
    module = importlib.import_module(f"cubesandbox_swe.legacy.{module_name}")
    old_argv = sys.argv[:]
    sys.argv = [module_name, *argv]
    try:
        result = module.main()
    finally:
        sys.argv = old_argv
    return int(result or 0)
