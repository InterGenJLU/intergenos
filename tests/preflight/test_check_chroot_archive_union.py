# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for scripts/check-chroot-archive-union.py (build pre-flight gate).

The gate makes archive-carriage of the built chroot enforceable: a chroot
file that no sealed archive carries fails the pre-flight, grouped as the
stub class when the pkm DB claims it installed (no install ever receives
it) or as plain unowned otherwise. Allowlisted generated-state classes and
archive-carried files must pass; an empty archives corpus is a LOUD skip
(exit 0), never a silent one; an unreadable archive is a violation.
"""

import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "chroot_archive_union_test",
        REPO_ROOT / "scripts" / "check-chroot-archive-union.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_archive(archives_dir, name, version, files):
    """Build <name>-<version>.igos.tar.gz with ./.PKGINFO + the given
    files ({path: bytes})."""
    pkginfo = f"pkgname={name}\npkgver={version}\npkgrel=1\n".encode()
    archive = archives_dir / f"{name}-{version}.igos.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(pkginfo)
        tf.addfile(info, io.BytesIO(pkginfo))
        for path, content in files.items():
            finfo = tarfile.TarInfo(f"./{path}")
            finfo.size = len(content)
            tf.addfile(finfo, io.BytesIO(content))
    return archive


class ChrootArchiveUnionGateTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chroot = Path(self.tmp.name) / "chroot"
        self.archives = self.chroot / "var/lib/igos/archives"
        self.archives.mkdir(parents=True)
        self.allowlist = Path(self.tmp.name) / "allowlist.txt"
        self.allowlist.write_text(
            "var/lib/igos/**\tpackage-system state\n"
            "etc/ld.so.cache\tldconfig-generated\n")
        db = self.chroot / "var/lib/igos/pkm.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE installed (name TEXT, version TEXT)")
        conn.execute("CREATE TABLE files (path TEXT)")
        conn.commit()
        conn.close()
        self.db = db
        self.memo_dir = Path(self.tmp.name) / "memo"
        patcher = mock.patch.object(self.mod, "MEMO_DIR", self.memo_dir, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, rel, content=b"x"):
        p = self.chroot / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def _db_own(self, rel):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO files VALUES (?)", (rel,))
        conn.commit()
        conn.close()

    def _run(self, extra_args=()):
        argv = ["check-chroot-archive-union",
                "--chroot", str(self.chroot),
                "--allowlist", str(self.allowlist),
                "--jobs", "1", *extra_args]
        out = io.StringIO()
        old = sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(out):
                rc = self.mod.main()
        finally:
            sys.argv = old
        return rc, out.getvalue()

    def test_pass_archive_carried_and_allowlisted(self):
        _make_archive(self.archives, "coreutils", "9.9",
                      {"usr/bin/ls": b"elf"})
        self._write("usr/bin/ls", b"elf")
        self._write("etc/ld.so.cache")          # allowlisted generated state
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS", out)

    def test_stub_class_db_owned_but_archive_absent(self):
        _make_archive(self.archives, "openssh", "10.0",
                      {"usr/bin/ssh": b"elf"})
        self._write("usr/bin/ssh", b"elf")
        self._write("etc/ssh/sshd_config_stub")
        self._db_own("etc/ssh/sshd_config_stub")
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn("stub class", out)
        self.assertIn("/etc/ssh/sshd_config_stub", out)

    def test_unowned_file_neither_archive_nor_db(self):
        _make_archive(self.archives, "coreutils", "9.9",
                      {"usr/bin/ls": b"elf"})
        self._write("usr/lib/stray.so")
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn("unowned file", out)
        self.assertIn("/usr/lib/stray.so", out)

    def test_empty_corpus_is_loud_skip(self):
        for a in self.archives.glob("*"):
            a.unlink()
        self._write("usr/bin/anything")
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIP", out)

    def test_unreadable_archive_is_violation(self):
        (self.archives / "broken-1.0.igos.tar.gz").write_bytes(b"not a tar")
        _make_archive(self.archives, "coreutils", "9.9",
                      {"usr/bin/ls": b"elf"})
        self._write("usr/bin/ls", b"elf")
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn("unreadable sealed archive", out)

    def test_skip_trees_out_of_scope(self):
        _make_archive(self.archives, "coreutils", "9.9",
                      {"usr/bin/ls": b"elf"})
        self._write("usr/bin/ls", b"elf")
        self._write("sources/tarball.tar.xz")        # SKIP_TOP
        self._write("mnt/intergenos/build/log.txt")  # SKIP_DIRS
        self._write("var/cache/junk")                # SKIP_PREFIX
        rc, out = self._run()
        self.assertEqual(rc, 0, out)

    def test_allowlist_without_reason_refuses(self):
        self.allowlist.write_text("usr/lib/whatever\n")
        _make_archive(self.archives, "coreutils", "9.9",
                      {"usr/bin/ls": b"elf"})
        with self.assertRaises(SystemExit) as ctx:
            self._run()
        self.assertEqual(ctx.exception.code, 2)


    def memo_archive(self):
        self._write("usr/bin/ls", b"elf")
        return _make_archive(self.archives, "coreutils", "9.9", {"usr/bin/ls": b"elf"})

    def memo_path(self):
        return self.memo_dir / "chroot-archive-union.json"

    def test_memo_unchanged_preserves_pass_and_counts(self):
        self.memo_archive()
        rc, first = self._run()
        self.assertEqual(rc, 0, first)
        self.assertIn("union: 1 archives listed, 0 memoized (unchanged)", first)
        with mock.patch.object(self.mod, "_list_members", side_effect=AssertionError("unexpected listing")):
            rc, second = self._run()
        self.assertEqual(rc, 0, second)
        self.assertIn("union: 0 archives listed, 1 memoized (unchanged)", second)
        self.assertEqual([x for x in first.splitlines() if "union:" not in x],
                         [x for x in second.splitlines() if "union:" not in x])

    def test_memo_chroot_scan_still_refuses_new_stray(self):
        self.memo_archive()
        self._run()
        self._write("usr/lib/stray.so")
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn("/usr/lib/stray.so", out)
        self.assertIn("0 archives listed, 1 memoized", out)

    def test_memo_database_classification_still_changes(self):
        self.memo_archive()
        self._write("usr/lib/stray.so")
        self._run()
        self._db_own("usr/lib/stray.so")
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn("stub class", out)
        self.assertIn("0 archives listed, 1 memoized", out)

    def test_memo_rewritten_archive_same_path(self):
        self.memo_archive()
        self._run()
        _make_archive(self.archives, "coreutils", "9.9", {"usr/bin/different": b"new"})
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn("/usr/bin/ls", out)
        self.assertIn("1 archives listed, 0 memoized", out)

    def test_memo_nanosecond_mtime_change(self):
        archive = self.memo_archive()
        self._run()
        st = archive.stat()
        os.utime(archive, ns=(st.st_atime_ns, st.st_mtime_ns + 1))
        _, out = self._run()
        self.assertIn("1 archives listed, 0 memoized", out)

    def test_memo_same_size_rewrite_with_restored_mtime(self):
        archive = self.memo_archive()
        self._run()
        st = archive.stat()
        archive.write_bytes(b"!" + archive.read_bytes()[1:])
        os.utime(archive, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(archive.stat().st_size, st.st_size)
        self.assertEqual(archive.stat().st_mtime_ns, st.st_mtime_ns)
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn("unreadable sealed archive", out)
        self.assertIn("1 archives listed, 0 memoized", out)

    def test_memo_changed_during_listing_not_saved(self):
        self.memo_archive()
        read = self.mod._list_members
        def changing(archive):
            result = read(archive)
            path = Path(archive)
            st = path.stat()
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1))
            return result
        with mock.patch.object(self.mod, "_list_members", changing):
            rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("archive changed during read", out)
        self.assertEqual(json.loads(self.memo_path().read_text())["entries"], {})

    def test_memo_new_and_removed_archive(self):
        original = self.memo_archive()
        self._run()
        extra = _make_archive(self.archives, "extra", "1", {"usr/bin/extra": b"x"})
        _, out = self._run()
        self.assertIn("1 archives listed, 1 memoized", out)
        extra.unlink()
        self._write("usr/bin/extra")
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn("/usr/bin/extra", out)
        self.assertIn("0 archives listed, 1 memoized", out)
        memo = json.loads(self.memo_path().read_text())
        self.assertEqual(set(memo["entries"]), {str(original.resolve())})

    def test_memo_reader_hash_change(self):
        self.memo_archive()
        self._run()
        with mock.patch.object(self.mod._state, "reader_hash", return_value="e" * 64):
            _, out = self._run()
        self.assertIn("reader hash changed", out)
        self.assertIn("1 archives listed, 0 memoized", out)

    def test_memo_no_memo_rewrites(self):
        self.memo_archive()
        self._run()
        rc, out = self._run(["--no-memo"])
        self.assertEqual(rc, 0, out)
        self.assertIn("1 archives listed, 0 memoized", out)
        _, out = self._run()
        self.assertIn("0 archives listed, 1 memoized", out)

    def test_memo_invalid_json_rebuilds(self):
        self.memo_archive()
        self._run()
        self.memo_path().write_text('{"entries":')
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("memo ignored", out)
        self.assertIn("1 archives listed, 0 memoized", out)
        json.loads(self.memo_path().read_text())

    def test_memo_invalid_payload_rebuilds(self):
        self.memo_archive()
        self._run()
        memo = json.loads(self.memo_path().read_text())
        next(iter(memo["entries"].values()))["value"] = {"not": "members"}
        self.memo_path().write_text(json.dumps(memo))
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("memo ignored", out)
        self.assertIn("1 archives listed, 0 memoized", out)

    def test_memo_unreadable_file_rebuilds(self):
        self.memo_archive()
        self._run()
        read_text = Path.read_text
        def read(path, *args, **kwargs):
            if path == self.memo_path():
                raise PermissionError("fixture memo unreadable")
            return read_text(path, *args, **kwargs)
        with mock.patch.object(Path, "read_text", read):
            rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("memo ignored", out)
        self.assertIn("PermissionError", out)
        self.assertIn("1 archives listed, 0 memoized", out)

    def test_memo_write_failure_keeps_verdict(self):
        self.memo_archive()
        self.memo_dir.write_text("not a directory")
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("memo not written", out)
        self.assertIn("1 archives listed, 0 memoized", out)

    def test_memo_corrupt_archive_never_hides_violation(self):
        archive = self.memo_archive()
        self._run()
        archive.write_bytes(b"not a tar archive")
        for _ in range(2):
            rc, out = self._run()
            self.assertEqual(rc, 1, out)
            self.assertIn("unreadable sealed archive", out)
            self.assertIn("1 archives listed, 0 memoized", out)


if __name__ == "__main__":
    unittest.main()
