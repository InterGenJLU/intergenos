# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A file whose name contains a backslash must verify.

WHAT THIS PINS. systemd ships three slice units whose file names contain
backslashes (system-systemd\\x2dcryptsetup.slice and two siblings). GNU
sha256sum, given such a name on its command line, writes its output line in
escaped form: a leading backslash before the digest and doubled backslashes
in the name. The bash lane's manifest writer (scripts/pkg-functions.sh
pkg_manifest) cut the first field of that line and wrote
"<path> sha256:\\<digest>" — a row no consumer can read as a hashed file.
pkm import then registered the WHOLE row text as a path with no hash, and
`pkm verify systemd` reported the three existing files missing on every
R001.2 system (pre-install evaluations of 2026-08-27 and 2026-09-02; the
chroot's own systemd-259.1 manifest, rows 505-507).

Two halves, both pinned here:
  - the writer emits a clean digest for a backslash-named file;
  - the reader tolerates the row shape already in the field (a stray
    backslash before the digest), so the installs that carry it verify.
"""

import hashlib
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB, _parse_manifest, _parse_manifest_line, _sha256

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_FUNCTIONS = REPO_ROOT / "scripts" / "pkg-functions.sh"

SLICE = "usr/lib/systemd/system/system-systemd\\x2dcryptsetup.slice"
# A digest of the recorded shape (64 hex), computed rather than quoted so the
# public-content gate does not read it as a credential.
DIGEST = hashlib.sha256(b"[Unit]\nDescription=slice\n").hexdigest()


class ReaderToleratesTheFieldShape(unittest.TestCase):
    def test_a_stray_backslash_before_the_digest_still_parses(self):
        path, h = _parse_manifest_line(f"{SLICE} sha256:\\{DIGEST}")
        self.assertEqual(path, SLICE)
        self.assertEqual(h, DIGEST)

    def test_the_clean_row_parses_as_before(self):
        path, h = _parse_manifest_line(f"{SLICE} sha256:{DIGEST}")
        self.assertEqual((path, h), (SLICE, DIGEST))

    def test_a_shipped_manifest_row_verifies_against_the_real_file(self):
        """The consumer's ground truth: import the field-shaped manifest,
        put the file on disk with the recorded content, verify → nothing
        missing, nothing modified."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            live = base / "live"
            target = live / SLICE
            target.parent.mkdir(parents=True)
            target.write_bytes(b"[Unit]\nDescription=slice\n")
            digest = _sha256(str(target))
            mdir = base / "manifests"
            mdir.mkdir()
            (mdir / "demo-1.0").write_text(
                "PACKAGE NAME: demo-1.0\nPACKAGE VERSION: 1.0\n"
                "PACKAGE RELEASE: 1\nBUILD DATE: 2026-09-02T00:00:00Z\n"
                "DESCRIPTION:\ndemo: a demo\n\nFILE LIST:\n"
                "usr/\nusr/lib/\nusr/lib/systemd/\nusr/lib/systemd/system/\n"
                f"{SLICE} sha256:\\{digest}\n")
            db = PackageDB(str(base / "pkm.db"), root=str(live))
            self.assertEqual(db.import_manifests(mdir), 1)
            con = sqlite3.connect(str(db.db_path))
            paths = [r[0] for r in con.execute("SELECT path FROM files")]
            con.close()
            self.assertIn(SLICE, paths)
            self.assertFalse([p for p in paths if "sha256:" in p], paths)
            result = db.verify_package("demo", strict=True)
            self.assertEqual(result["missing"], [])
            self.assertEqual(result["modified"], [])


class WriterEmitsACleanDigest(unittest.TestCase):
    def test_backslash_named_file_gets_its_digest_without_escaping(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            staging, pkgdb, logs = base / "staging", base / "pkgdb", base / "logs"
            for d in (staging, pkgdb, logs):
                d.mkdir()
            p = staging / "demo-1.0" / SLICE
            p.parent.mkdir(parents=True)
            p.write_bytes(b"[Unit]\nDescription=slice\n")
            script = (
                f'set -e; source "{PKG_FUNCTIONS}" >/dev/null 2>&1; '
                f'IGOS_PKG_STAGING="{staging}"; IGOS_PKG_DB="{pkgdb}"; '
                f'IGOS_LOGS="{logs}"; pkg_manifest demo 1.0 "a demo" 1')
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = (pkgdb / "demo-1.0").read_text()
            row = [l for l in text.splitlines() if l.startswith(SLICE)]
            self.assertEqual(row, [f"{SLICE} sha256:{_sha256(str(p))}"], text)
            parsed = _parse_manifest(text)
            self.assertEqual(parsed["file_hashes"].get(SLICE), _sha256(str(p)))


if __name__ == "__main__":
    unittest.main()
