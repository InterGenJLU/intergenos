#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""trace-emit.py — jq-free JSON-line emitter for the bash forensic-trace companion.

`scripts/lib/trace.sh` originally built every event with `jq`. jq is present on
the Ubuntu build host but NOT inside the InterGenOS chroot (it is not an LFS/BLFS
package), so every in-chroot bash trace emit failed and produced 0-byte sink
files. `python3` IS available both on the host and inside the chroot (built as a
bootstrap tool), and the shared igos_trace.py already proves python is the right
JSON engine. This helper lets trace.sh emit via python instead of jq.

Large fields (multi-MB compiler logs) are passed as FILE PATHS (--rawfile), read
here directly — never as argv — so there is no ARG_MAX limit and bytes are
preserved verbatim. Non-UTF-8 bytes are decoded with surrogateescape and
re-encoded so no content is lost or dropped.

Usage (all flags repeatable):
  trace-emit.py --sink PATH
      [--str  KEY VALUE]      # string field
      [--json KEY JSONLIT]    # raw JSON literal (numbers/bools/arrays)
      [--rawfile KEY FILE]    # field value = verbatim file contents (UTF-8,
                              #   surrogateescape); empty string if unreadable

Appends exactly one JSON object (one line) to PATH under an flock. Exit 0 on
success; on any failure prints a diagnostic to stderr and exits 1 (the caller
treats that as "never silently dropped").
"""
from __future__ import annotations

import sys
import os
import json
import base64
import fcntl


def _set_rawfile(event: dict, key: str, path: str) -> None:
    """Read a file verbatim into the event under `key`.

    Build logs are normally UTF-8 text → stored readable under `key`. If the
    bytes are NOT valid UTF-8 (rare binary output), store base64 under
    `key + "_b64"` plus `key + "_encoding" = "base64"` so the emitted JSONL
    stays valid UTF-8 / parseable while remaining byte-for-byte lossless.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        event[key] = ""
        return
    try:
        event[key] = data.decode("utf-8")  # strict — keeps JSONL valid UTF-8
    except UnicodeDecodeError:
        event[key + "_b64"] = base64.b64encode(data).decode("ascii")
        event[key + "_encoding"] = "base64"


def main(argv: list[str]) -> int:
    sink = None
    event: dict = {}
    i = 0
    n = len(argv)
    while i < n:
        flag = argv[i]
        if flag == "--sink":
            sink = argv[i + 1]; i += 2
        elif flag == "--str":
            event[argv[i + 1]] = argv[i + 2]; i += 3
        elif flag == "--json":
            try:
                event[argv[i + 1]] = json.loads(argv[i + 2])
            except (ValueError, IndexError):
                # Fall back to the raw string rather than dropping the field.
                event[argv[i + 1]] = argv[i + 2]
            i += 3
        elif flag == "--rawfile":
            _set_rawfile(event, argv[i + 1], argv[i + 2]); i += 3
        else:
            i += 1  # ignore unknown tokens defensively
    if not sink:
        sys.stderr.write("trace-emit.py: no --sink given\n")
        return 1
    # ensure_ascii=False keeps the JSONL human-readable (UTF-8) like the jq path.
    # All field values are now guaranteed valid UTF-8 (text decoded strict, or
    # base64-ASCII), so the line is valid UTF-8 and standard JSON parsers read it.
    line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
    try:
        fd = os.open(sink, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line.encode("utf-8"))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except OSError as exc:
        sys.stderr.write(f"trace-emit.py: sink write failed ({sink}): {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
