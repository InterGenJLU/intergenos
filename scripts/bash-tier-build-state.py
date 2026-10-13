#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Read bash-tier recipe and archive currency without consulting the database.

Drivers read one archive snapshot per invocation, then check the installed
manifest at each package. Parsed archive headers are memoized under
build/gate-memo by archive identity and reader bytes; recipe and installed
state are always read anew. The parser and version comparator are the same
ones used by the currency preflight.
"""

import argparse
import importlib.util
import hashlib
import json
import os
import tempfile
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location(
    "redeploy_banked_archives", Path(__file__).with_name("redeploy-banked-archives.py"))
_reader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_reader)
read_pkginfo = _reader.read_pkginfo
vcompare = _reader.vcompare
load_recipes = _reader.load_recipes


MEMO_DIR = Path(__file__).resolve().parent.parent / "build/gate-memo"


def reader_hash(*paths):
    """Bind persisted data to the bytes of every parser it depends on."""
    digest = hashlib.sha256()
    for path in paths:
        data = Path(path).read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


class ArchiveMemo:
    """Optional JSON data for unchanged archives, never a cached verdict.

    Nanosecond timestamps retain changes within a second. The change time
    and inode also invalidate rewrites that restore the original mtime.
    Only successful reads whose identity stays stable are saved. Atomic
    replacement lets concurrent invocations safely miss each other's work.
    """

    def __init__(self, path, digest, valid_value, *, no_memo=False, report=print):
        self.path = Path(path)
        self.digest = digest
        self.report = report
        self.previous = {}
        self.entries = {}
        if no_memo:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("format") != 1:
                raise ValueError("invalid memo format")
            if data.get("reader_sha256") != digest:
                raise ValueError("reader hash changed")
            entries = data.get("entries")
            if not isinstance(entries, dict):
                raise ValueError("invalid memo entries")
            for path, entry in entries.items():
                if not isinstance(entry, dict) or not valid_value(entry.get("value")):
                    raise ValueError("invalid memo value")
                identity = entry.get("identity")
                if (not isinstance(identity, dict) or identity.get("path") != path
                        or not Path(path).is_absolute()
                        or any(type(identity.get(k)) is not int for k in
                               ("size", "mtime_ns", "ctime_ns", "device", "inode"))):
                    raise ValueError("invalid archive identity")
            self.previous = entries
        except FileNotFoundError:
            pass  # First use has no memo yet.
        except (OSError, ValueError) as error:
            report(f"memo ignored: {self.path}: {type(error).__name__}: {error}")

    @staticmethod
    def identity(archive):
        path = Path(archive).resolve()
        stat = path.stat()
        return dict(path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                    ctime_ns=stat.st_ctime_ns, device=stat.st_dev, inode=stat.st_ino)

    def lookup(self, archive):
        try:
            identity = self.identity(archive)
        except OSError as error:
            self.report(f"memo miss: {archive}: {type(error).__name__}: {error}")
            return None, None
        entry = self.previous.get(identity["path"])
        if entry is not None and entry["identity"] == identity:
            self.entries[identity["path"]] = entry
            return identity, entry["value"]
        return identity, None

    def remember(self, archive, identity, value):
        if identity is None:
            return
        try:
            if self.identity(archive) != identity:
                self.report(f"memo entry not saved: {archive}: archive changed during read")
                return
        except OSError as error:
            self.report(f"memo entry not saved: {archive}: {type(error).__name__}: {error}")
            return
        self.entries[identity["path"]] = dict(identity=identity, value=value)

    def save(self):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=self.path.parent,
                                             prefix=self.path.name + ".", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(dict(format=1, reader_sha256=self.digest, entries=self.entries),
                          stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, self.path)
            temporary = None
        except OSError as error:
            self.report(f"memo not written: {self.path}: {type(error).__name__}: {error}")
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as error:
                    self.report(f"memo temporary not removed: {temporary}: {error}")


def newest_archives(directory, *, strict=False, no_memo=False, memo_dir=None, report=None):
    """Ship name -> (version, numeric release, path), ordered by package key.

Preflight retains its existing treatment of unreadable archives. A driver
requires every archive to be readable before it can certify the newest one;
otherwise an unreadable newer sibling could hide behind a current older one.
"""
    if report is None:
        report = lambda message: print(message, file=sys.stderr)
    memo = ArchiveMemo((memo_dir if memo_dir is not None else MEMO_DIR) / "bash-tier-headers.json",
                       reader_hash(__file__, _reader.__file__),
                       lambda value: isinstance(value, dict) and all(
                           isinstance(k, str) and isinstance(v, str) for k, v in value.items()),
                       no_memo=no_memo, report=report)
    newest = {}
    read = hits = 0
    for archive in sorted(directory.glob("*.igos.tar.gz")):
        identity, info = memo.lookup(archive)
        if info is None:
            read += 1
            info = read_pkginfo(archive)
            if info is not None:
                memo.remember(archive, identity, info)
        else:
            hits += 1
        if not info or "pkgname" not in info or (strict and (
                not info.get("pkgname") or not info.get("pkgver"))):
            if strict:
                raise ValueError(f"cannot read package identity from {archive}")
            continue
        version = info.get("pkgver", "0")
        release = int(info.get("pkgrel", "1") or 1)
        name = info["pkgname"]
        current = newest.get(name)
        if current is None or vcompare(current[:2], (version, release)) < 0:
            newest[name] = (version, release, archive)
    memo.save()
    report(f"currency: {read} archives read, {hits} memoized (unchanged)")
    return newest


def build_states(packages_dir, archives_dir, *, no_memo=False, memo_dir=None):
    """Yield tab-separated records for the driver's tier, using recipe keys."""
    if not packages_dir.is_dir():
        raise ValueError(f"recipe directory absent: {packages_dir}")
    newest = newest_archives(archives_dir, strict=True, no_memo=no_memo, memo_dir=memo_dir)
    recipes = load_recipes(packages_dir.parent)
    for name, recipe in sorted(recipes.items()):
        directory = Path(recipe["path"]).parent
        if directory.parent != packages_dir:
            continue
        version, release = recipe["version"], recipe["release"]
        banked = newest.get(name)
        if banked is None:
            state = "never built"
        elif banked[:2] == (version, release):
            state = f"skipped (current {version}-{release}, archive {banked[2].name})"
        else:
            relation = "behind" if vcompare(banked[:2], (version, release)) < 0 else "differs from"
            state = f"stale: archive {banked[0]}-{banked[1]} {relation} tree {version}-{release}"
        fields = (directory.name, name, version, state)
        if any("\t" in field or "\n" in field or "\r" in field for field in fields):
            raise ValueError(f"invalid currency record for {directory}")
        yield "\t".join(fields)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--packages-dir", type=Path, required=True)
    parser.add_argument("--archives-dir", type=Path, default=Path("/var/lib/igos/archives"))
    parser.add_argument("--no-memo", action="store_true", help="read every archive and rewrite the memo")
    parser.add_argument("--memo-dir", type=Path, default=MEMO_DIR)
    args = parser.parse_args()
    try:
        # Finish validation before publishing any records to the driver.
        records = list(build_states(args.packages_dir, args.archives_dir,
                                    no_memo=args.no_memo, memo_dir=args.memo_dir))
    except (OSError, ValueError) as error:
        print(f"bash-tier-build-state: cannot verify currency: {error}", file=sys.stderr)
        return 2
    print("\n".join(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
