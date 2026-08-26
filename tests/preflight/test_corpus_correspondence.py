# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The staged-corpus <-> built-corpus byte gate, driven as a real program.

The first full-rebuild release exposed the overlay publish model: staging
kept pre-rebuild bytes for every package whose (version, release) did not
move, and the mirror served them — 796 of 842 published components. The
gate decided 2026-08-21 proves byte identity in BOTH directions before an
index is generated. Each case here runs scripts/check-corpus-correspondence.py
as a subprocess against real files in a temporary directory — no mocked
hashing, no stubbed filesystem.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check-corpus-correspondence.py"


class CorpusCorrespondenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.staging = root / "staging"
        self.manifest = root / "chroot-archives.sha256"
        self.staging.mkdir()
        self.built = {}

    def tearDown(self):
        self._tmp.cleanup()

    def _build_archive(self, name, payload, stage=True):
        """Record <name> in the built manifest; optionally stage identical bytes."""
        data = payload.encode()
        self.built[name] = sha256(data).hexdigest()
        if stage:
            (self.staging / name).write_bytes(data)

    def _write_manifest(self):
        self.manifest.write_text(
            "".join(f"{d}  {n}\n" for n, d in sorted(self.built.items())))

    def _run(self):
        self._write_manifest()
        return subprocess.run(
            [sys.executable, str(GATE),
             "--staging", str(self.staging),
             "--chroot-manifest", str(self.manifest)],
            capture_output=True, text=True)

    def test_full_correspondence_passes(self):
        self._build_archive("zlib-1.3.1.igos.tar.gz", "zlib bytes")
        self._build_archive("bash-5.3.igos.tar.gz", "bash bytes")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)

    def test_byte_divergence_fails(self):
        """The Wednesday class: same name staged, different bytes served."""
        self._build_archive("zlib-1.3.1.igos.tar.gz", "rebuilt bytes", stage=False)
        (self.staging / "zlib-1.3.1.igos.tar.gz").write_bytes(b"pre-rebuild bytes")
        r = self._run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BYTES DIFFER", r.stdout)

    def test_missing_from_staging_fails(self):
        self._build_archive("zlib-1.3.1.igos.tar.gz", "zlib bytes", stage=False)
        r = self._run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("MISSING from staging", r.stdout)

    def test_orphan_in_staging_fails(self):
        """The stale-baseline class: staging carries what the build never made."""
        self._build_archive("zlib-1.3.1.igos.tar.gz", "zlib bytes")
        (self.staging / "retired-2.0.igos.tar.gz").write_bytes(b"old baseline")
        r = self._run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("ORPHAN in staging", r.stdout)

    def test_intermediates_excluded_by_name(self):
        """-passN/-tmp/-bootstrap archives are never-publish: absent from
        staging is correct, and every exclusion is printed."""
        self._build_archive("zlib-1.3.1.igos.tar.gz", "zlib bytes")
        for interm in ("gcc-pass1-14.2.igos.tar.gz",
                       "glibc-tmp-2.40.igos.tar.gz",
                       "glib2-bootstrap-2.88.1.igos.tar.gz"):
            self._build_archive(interm, f"intermediate {interm}", stage=False)
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("excluded (never-publish intermediate)"), 3)

    def test_toolchain_twin_plain_archive_stays_publishable(self):
        """The Chapter-8 recipe-less class (first real firing, 2026-08-21):
        glibc/m4/ncurses carry toolchain twin recipes — named glibc-tmp,
        m4-tmp and ncurses-tmp since 2026-08-25 — but their plain archives
        PUBLISH, so a plain versioned name must demand correspondence."""
        self._build_archive("glibc-2.43.igos.tar.gz", "published glibc bytes")
        self._build_archive("m4-1.4.21.igos.tar.gz", "published m4 bytes")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 publishable", r.stdout)

    def test_staged_intermediate_fails(self):
        """An intermediate must not ride INTO staging either."""
        self._build_archive("zlib-1.3.1.igos.tar.gz", "zlib bytes")
        self._build_archive("gcc-pass1-14.2.igos.tar.gz", "intermediate", stage=True)
        r = self._run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("STAGED INTERMEDIATE", r.stdout)

    def test_versioned_name_parsing_keeps_dashed_packages_publishable(self):
        """A dashed package name whose suffix is not an intermediate marker
        (e.g. util-linux-core-2.40) must NOT be swept by the exclusion."""
        self._build_archive("util-linux-core-2.40.igos.tar.gz", "ulc bytes")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 publishable", r.stdout)

    def test_unparseable_manifest_refuses(self):
        self._build_archive("zlib-1.3.1.igos.tar.gz", "zlib bytes")
        self._write_manifest()
        self.manifest.write_text(self.manifest.read_text() + "not a manifest line\n")
        r = subprocess.run(
            [sys.executable, str(GATE),
             "--staging", str(self.staging),
             "--chroot-manifest", str(self.manifest)],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
