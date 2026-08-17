#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""check-trace-schema.py — validate JSONL trace files against the canonical schema.

Pre-push gate (#12 per dossier 50-implementation-priority.md section 6).

Scans every *.jsonl file under tests/golden/trace/ (and optionally a path
passed as argv[1]) and validates every event line against
tests/schema/trace-event.schema.json. Fails on:

  - Missing required keys (type, ts).
  - Wrong type for known keys.
  - Malformed timestamp (must match ISO-8601 millisecond UTC pattern).
  - Invalid UTF-8 in any line.

Pure-stdlib implementation — no jsonschema dependency. The schema's
required-field + simple-type validation surface is small enough that
inline validation is right-sized. (Adding jsonschema would create a
build-host Python-deps gate where one didn't exist.)

Usage:
    python3 scripts/check-trace-schema.py
        — validates every tests/golden/trace/*.jsonl
    python3 scripts/check-trace-schema.py /path/to/dir
        — validates every *.jsonl under /path/to/dir
    python3 scripts/check-trace-schema.py /path/to/file.jsonl
        — validates a single file

Exit codes:
    0 — every event passed
    1 — at least one event failed validation
    2 — schema file missing or unreadable
"""

import json
import re
import sys
from pathlib import Path


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "tests" / "schema" / "trace-event.schema.json"
DEFAULT_SCAN_ROOT = Path(__file__).resolve().parent.parent / "tests" / "golden" / "trace"

# ISO-8601 millisecond UTC pattern matches both Python (igos_trace._iso_ts)
# and bash (trace.sh:_trace_iso_ts) emit format byte-for-byte.
_TS_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$")

# Runid pattern: 1-16 lowercase hex chars (16 is the canonical length;
# 1-15 allowed as a defense against truncated/test fixtures).
_RUNID_RE = re.compile(r"^[0-9a-f]{1,16}$")


def _is_type(value, allowed):
    """JSON Schema `type` check — supports list-of-types + nullable."""
    if not isinstance(allowed, list):
        allowed = [allowed]
    for kind in allowed:
        if kind == "string" and isinstance(value, str):
            return True
        if kind == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if kind == "array" and isinstance(value, list):
            return True
        if kind == "null" and value is None:
            return True
        if kind == "object" and isinstance(value, dict):
            return True
        if kind == "boolean" and isinstance(value, bool):
            return True
    return False


def validate_event(event, schema, line_no, path):
    """Validate one parsed event dict. Returns a list of error strings."""
    errors = []
    required = schema.get("required", [])
    props = schema.get("properties", {})

    if not isinstance(event, dict):
        errors.append(f"{path}:{line_no}: not a JSON object (got {type(event).__name__})")
        return errors

    for key in required:
        if key not in event:
            errors.append(f"{path}:{line_no}: missing required field '{key}'")

    for key, value in event.items():
        prop = props.get(key)
        if prop is None:
            # additionalProperties: true — allow unknown keys.
            continue
        prop_type = prop.get("type")
        if prop_type and not _is_type(value, prop_type):
            errors.append(
                f"{path}:{line_no}: field '{key}' has type {type(value).__name__}, "
                f"expected {prop_type}"
            )
        pattern = prop.get("pattern")
        if pattern and isinstance(value, str):
            if not re.match(pattern, value):
                errors.append(
                    f"{path}:{line_no}: field '{key}' value '{value[:40]}...' "
                    f"does not match pattern {pattern}"
                )
        minimum = prop.get("minimum")
        if minimum is not None and isinstance(value, int) and value < minimum:
            errors.append(
                f"{path}:{line_no}: field '{key}' value {value} below minimum {minimum}"
            )

    return errors


def validate_file(path, schema):
    """Validate every JSONL line in path. Returns (error_count, line_count)."""
    errors = 0
    lines = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                raw = raw.rstrip("\n")
                if not raw.strip():
                    continue
                lines += 1
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError as e:
                    print(f"{path}:{line_no}: JSON parse error: {e}", file=sys.stderr)
                    errors += 1
                    continue
                for err in validate_event(event, schema, line_no, path):
                    print(err, file=sys.stderr)
                    errors += 1
    except OSError as e:
        print(f"{path}: cannot read: {e}", file=sys.stderr)
        return (1, 0)
    return (errors, lines)


def main(argv):
    if not SCHEMA_PATH.exists():
        print(f"check-trace-schema: schema file missing at {SCHEMA_PATH}", file=sys.stderr)
        return 2

    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"check-trace-schema: cannot load schema: {e}", file=sys.stderr)
        return 2

    targets = []
    if len(argv) > 1:
        scan = Path(argv[1])
        if scan.is_file():
            targets = [scan]
        elif scan.is_dir():
            targets = sorted(scan.glob("**/*.jsonl"))
        else:
            print(f"check-trace-schema: argv[1] is neither file nor dir: {scan}", file=sys.stderr)
            return 2
    else:
        if DEFAULT_SCAN_ROOT.exists():
            targets = sorted(DEFAULT_SCAN_ROOT.glob("**/*.jsonl"))

    if not targets:
        print("check-trace-schema: no JSONL files to validate (pass on first run)")
        return 0

    total_errors = 0
    total_lines = 0
    for target in targets:
        errs, lines = validate_file(target, schema)
        total_errors += errs
        total_lines += lines

    if total_errors:
        print(
            f"check-trace-schema: {total_errors} error(s) across "
            f"{len(targets)} file(s) ({total_lines} event(s) checked)",
            file=sys.stderr,
        )
        return 1

    print(
        f"check-trace-schema: OK — {len(targets)} file(s), "
        f"{total_lines} event(s) validated"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
