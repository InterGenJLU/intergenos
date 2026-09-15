#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail closed when package-manager execution surfaces drift from review."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MODULE_PATH = SCRIPT_DIR / "lib/pkm_execution_inventory.py"


def _load_inventory_module():
    if not MODULE_PATH.is_file() or MODULE_PATH.is_symlink():
        raise RuntimeError(f"required scanner module is not a regular file: {MODULE_PATH}")
    spec = importlib.util.spec_from_file_location("pkm_execution_inventory", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load scanner module: {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument(
        "--inventory",
        default=str(REPO_ROOT / "config/pkm-execution-inventory.tsv"),
    )
    parser.add_argument(
        "--process-calls",
        default=str(REPO_ROOT / "config/pkm-process-calls.tsv"),
    )
    parser.add_argument(
        "--shapes",
        default=str(REPO_ROOT / "config/pkm-execution-shapes.tsv"),
    )
    parser.add_argument(
        "--safe-launchers",
        default=str(REPO_ROOT / "config/pkm-safe-python-launchers.tsv"),
    )
    args = parser.parse_args(argv)

    try:
        inventory = _load_inventory_module()
        issues, surface_count, call_count, shape_count, safe_count = inventory.check_tree(
            Path(args.root),
            Path(args.inventory),
            Path(args.process_calls),
            Path(args.shapes),
            Path(args.safe_launchers),
        )
    except Exception as error:
        print(f"pkm execution inventory: ERROR: {error}", file=sys.stderr)
        return 2

    print(
        "pkm execution inventory: "
        f"{surface_count} semantic surfaces, {call_count} process calls, "
        f"{shape_count} archive shapes, {safe_count} safe launchers"
    )
    if issues:
        print("pkm execution inventory: FAIL")
        for issue in issues:
            print(f"  {issue}")
        return 1
    print("pkm execution inventory: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
