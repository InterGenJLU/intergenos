#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Read authenticated apt metadata and order complete Debian versions.

The caller verifies the signature and Packages digest before invoking this
reader. Version ordering follows Debian Policy 5.6.12, including epoch and
revision; pkm's separate distribution release is not part of this version.
"""

import json
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote


def version_parts(value):
    if not isinstance(value, str) or not value or not value.isascii():
        raise ValueError("missing or invalid Debian version")
    epoch, rest = "0", value
    if ":" in rest:
        epoch, rest = rest.split(":", 1)
        if not re.fullmatch(r"[0-9]+", epoch):
            raise ValueError("invalid Debian version epoch")
    upstream, separator, revision = rest.rpartition("-")
    if not separator:
        upstream, revision = rest, "0"
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+~\-]*", upstream):
        raise ValueError("invalid Debian upstream version")
    if not re.fullmatch(r"[A-Za-z0-9+.~]+", revision):
        raise ValueError("invalid Debian revision")
    return int(epoch), upstream, revision


def _order(char):
    if char == "~":
        return -1
    if not char or char.isdigit():
        return 0
    return ord(char) if char.isalpha() else ord(char) + 256


def _compare_part(left, right):
    a = b = 0
    while a < len(left) or b < len(right):
        while ((a < len(left) and not left[a].isdigit())
               or (b < len(right) and not right[b].isdigit())):
            x = _order(left[a] if a < len(left) else "")
            y = _order(right[b] if b < len(right) else "")
            if x != y:
                return -1 if x < y else 1
            a += 1
            b += 1
        while a < len(left) and left[a] == "0":
            a += 1
        while b < len(right) and right[b] == "0":
            b += 1
        first_difference = 0
        while a < len(left) and b < len(right) and left[a].isdigit() and right[b].isdigit():
            if not first_difference:
                first_difference = ord(left[a]) - ord(right[b])
            a += 1
            b += 1
        if a < len(left) and left[a].isdigit():
            return 1
        if b < len(right) and right[b].isdigit():
            return -1
        if first_difference:
            return -1 if first_difference < 0 else 1
    return 0


def compare_versions(left, right):
    le, lu, lr = version_parts(left)
    re_, ru, rr = version_parts(right)
    if le != re_:
        return -1 if le < re_ else 1
    return _compare_part(lu, ru) or _compare_part(lr, rr)


def stanzas(text):
    fields = {}
    previous = None
    for line in text.splitlines() + [""]:
        if not line.strip():
            if fields:
                yield fields
            fields, previous = {}, None
        elif line[0].isspace():
            if previous is None:
                raise ValueError("metadata continuation has no field")
            fields[previous] += "\n" + line.strip()
        else:
            key, separator, value = line.partition(":")
            key = key.lower()
            if not separator or key in fields or not re.fullmatch(r"[a-z0-9-]+", key):
                raise ValueError("malformed or repeated metadata field")
            fields[key], previous = value.strip(), key


def safe_pool_path(value):
    decoded = unquote(value)
    if (not value or not re.fullmatch(r"[A-Za-z0-9_./+~:%-]+", value)
            or decoded.startswith("/") or ":" in decoded.split("/", 1)[0]
            or any(part in ("", ".", "..") for part in decoded.split("/"))
            or not decoded.endswith(".deb")):
        raise ValueError("invalid package Filename path")
    return value


def select_package(path, package, requested=""):
    if requested:
        version_parts(requested)
    selected = None
    versions = {}
    for fields in stanzas(Path(path).read_text(encoding="utf-8")):
        if fields.get("package") != package or fields.get("architecture") not in ("amd64", "all"):
            continue
        version = fields.get("version", "")
        version_parts(version)
        filename = safe_pool_path(fields.get("filename", ""))
        digest = fields.get("sha256", "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("package has no valid SHA256 digest")
        record = (filename, version, digest.lower())
        if version in versions and versions[version] != record:
            raise ValueError("conflicting package records for the same version")
        versions[version] = record
        if requested and version != requested:
            continue
        if selected is None or compare_versions(version, selected[1]) > 0:
            selected = record
    if selected is None:
        raise ValueError("requested package version is absent from verified metadata")
    filename, version, digest = selected
    return "|".join((PurePosixPath(filename).name, version, filename, digest))


def main(args):
    try:
        if len(args) == 4 and args[0] == "select":
            print(select_package(*args[1:]))
        elif len(args) == 3 and args[0] == "query":
            latest, installed = args[1:]
            print(json.dumps({"version": latest, "comparison": compare_versions(latest, installed)}))
        else:
            raise ValueError("invalid metadata-reader arguments")
    except (OSError, UnicodeError, ValueError) as error:
        print(f"ERROR: could not read verified vendor metadata: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
