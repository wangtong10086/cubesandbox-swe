#!/usr/bin/env python3
"""Deprecated legacy smoke entrypoint."""

from __future__ import annotations


def main() -> int:
    print(
        "legacy gpt55-e2e is deprecated; use "
        "`cubesandbox-swe solve --codex-location sandbox` instead."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
