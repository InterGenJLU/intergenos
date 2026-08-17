# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""RT-3 post-configure feature-matrix assertion (GE arc).

Meson auto-features SILENTLY DISABLE when a dependency or predicate is
missing at configure time (mesa's own meson.build:
`get_option('gbm').disable_auto_if(not with_dri)` and the `.require()`
chains) — a missing lib32 dep can kill the x11 WSI while every other gate
stays green and vulkaninfo still enumerates devices (the RT-3 disease).

This checker runs BETWEEN `meson setup` and `ninja` and REFUSES the build
on ANY deviation between the recipe's declared feature matrix (a sidecar
JSON beside the recipe) and the RESOLVED option values meson recorded in
`<builddir>/meson-info/intro-buildoptions.json` (the stable introspection
API — structured and authoritative; the human summary block is not parsed).

Fail-closed like the sibling gates (elfaudit/time64audit): a matrix key
absent from the introspection data (option renamed/removed on an upstream
bump), a missing/unparseable intro file, or an unreadable matrix each
REFUSE — never skip.

Comparison rules:
  * array options: SET equality (order-independent) — both a MISSING pinned
    entry and an UNEXPECTED extra (lavapipe creeping into vulkan-drivers)
    are violations;
  * everything else (feature/combo/boolean/string): exact string compare
    (booleans normalized to true/false).

Usage (from a recipe's configure(), after meson setup):
    python3 /mnt/intergenos/igos-build/mesa_feature_matrix.py \
        --build build --matrix "$PKG_TEMPLATE_DIR/feature-matrix.json" \
        --label lib32-mesa
Exit 0 == every pinned option matches; nonzero == REFUSE with every
deviation printed.
"""

import argparse
import json
import sys
from pathlib import Path


def _normalize(value):
    """Normalize a resolved meson option value for comparison."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return sorted(str(v) for v in value)
    return str(value)


def _normalize_declared(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return sorted(str(v) for v in value)
    return str(value)


def load_resolved_options(build_dir: Path) -> dict:
    """Load {option_name: resolved_value} from meson introspection.

    Raises ValueError (never returns partial data) on any structural
    problem — the caller converts that to a REFUSE."""
    intro = build_dir / "meson-info" / "intro-buildoptions.json"
    if not intro.is_file():
        raise ValueError(
            f"{intro} missing — did meson setup run? The matrix cannot be "
            f"verified; refusing (fail-closed)")
    try:
        data = json.loads(intro.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"{intro} unreadable/unparseable: {e}")
    if not isinstance(data, list):
        raise ValueError(f"{intro}: expected a list of option records")
    out = {}
    for rec in data:
        if isinstance(rec, dict) and "name" in rec and "value" in rec:
            out[rec["name"]] = rec["value"]
    if not out:
        raise ValueError(f"{intro}: no option records found")
    return out


def check_matrix(resolved: dict, matrix: dict, label: str) -> list:
    """Return a list of human-readable violations (empty == clean)."""
    violations = []
    for opt, declared in matrix.items():
        if opt.startswith("_"):
            continue  # sidecar metadata keys (_comment etc.)
        if opt not in resolved:
            violations.append(
                f"{label}: pinned option '{opt}' is ABSENT from the resolved "
                f"build options — renamed/removed upstream? Re-ground the "
                f"matrix against the pinned tarball (fail-closed)")
            continue
        got = _normalize(resolved[opt])
        want = _normalize_declared(declared)
        if isinstance(want, list) or isinstance(got, list):
            got_set = set(got if isinstance(got, list) else [got])
            want_set = set(want if isinstance(want, list) else [want])
            missing = want_set - got_set
            extra = got_set - want_set
            if missing or extra:
                parts = []
                if missing:
                    parts.append(f"missing {sorted(missing)}")
                if extra:
                    parts.append(f"unexpected {sorted(extra)}")
                violations.append(
                    f"{label}: '{opt}' resolved {sorted(got_set)} vs pinned "
                    f"{sorted(want_set)} — {'; '.join(parts)}")
        elif got != want:
            violations.append(
                f"{label}: '{opt}' resolved '{got}' but the matrix pins "
                f"'{want}' — an auto-feature silently resolved away from "
                f"the declared surface (the RT-3 class)")
    return violations


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", required=True, help="meson build directory")
    ap.add_argument("--matrix", required=True,
                    help="path to the recipe's feature-matrix.json sidecar")
    ap.add_argument("--label", default="mesa", help="package label for output")
    args = ap.parse_args(argv)

    matrix_path = Path(args.matrix)
    try:
        matrix = json.loads(matrix_path.read_text())
        if not isinstance(matrix, dict) or not matrix:
            raise ValueError("matrix must be a non-empty JSON object")
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"RT-3 REFUSE: matrix sidecar {matrix_path}: {e}", file=sys.stderr)
        return 2

    try:
        resolved = load_resolved_options(Path(args.build))
    except ValueError as e:
        print(f"RT-3 REFUSE: {e}", file=sys.stderr)
        return 2

    violations = check_matrix(resolved, matrix, args.label)
    if violations:
        print(f"RT-3 REFUSE: {len(violations)} feature-matrix deviation(s) "
              f"for {args.label}:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    pinned = sum(1 for k in matrix if not k.startswith("_"))
    print(f"RT-3 OK: {args.label} — {pinned} pinned options match the "
          f"resolved configure state")
    return 0


if __name__ == "__main__":
    sys.exit(main())
