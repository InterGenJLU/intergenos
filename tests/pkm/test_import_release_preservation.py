# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm import` must CARRY a release, never invent one.

The defect this pins, measured 2026-07-29: a single package rebuild in the
build chroot reset the recorded release of 215 packages to 1, producing a
database that described a different build of ~24% of the corpus than the
archives shipping beside it (the split the squashfs Step 2.7 metadata/payload
sync gate reports).

The chain, all of it structural rather than incidental:

  * `pkg_install` (scripts/pkg-functions.sh) runs `pkm import` after EVERY
    package build, and `import_manifests` walks the WHOLE manifest directory —
    so any one build re-examines every package on the system.
  * every writer that records a TRUTHFUL release — an archive install from
    .PKGINFO pkgrel, a helper install, an igos-build source build from
    pkg.release — registered its row with `manifest_sha256` unset.
  * a NULL stored hash means "provenance unproven", which correctly forces a
    re-register...
  * ...and the re-register read the release from a text manifest that carried
    no PACKAGE RELEASE header, so it fell to the schema default of 1.

Each link is individually reasonable; composed, a default silently overwrote a
known truth on every package at once. The fix is a precedence rule with no
silent default: manifest header > the release already on the row (same version
only) > the schema default, which now applies only to a package this database
has never seen.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class ImportReleasePreservationTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.mdir = self.tmp / "manifests"
        self.mdir.mkdir()
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _write_manifest(self, name, version, content="payload", release=None):
        """Write one package's files + text manifest.

        release=None reproduces the header-less manifest every writer emitted
        before this fix — the shape still sitting on already-built media, which
        is why the reader-side rule has to hold on its own.
        """
        rel = f"usr/bin/{name}"
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        lines = [f"PACKAGE NAME: {name}-{version}",
                 f"PACKAGE VERSION: {version}"]
        if release is not None:
            lines.append(f"PACKAGE RELEASE: {release}")
        lines += ["BUILD DATE: 2026-01-01T00:00:00Z",
                  "FILE LIST:",
                  f"{rel} sha256:{_sha(content)}"]
        (self.mdir / f"{name}-{version}").write_text("\n".join(lines) + "\n")

    def _register_healed(self, name, version, release):
        """Register a row exactly as an archive install does.

        pkm/installer.py takes the release from .PKGINFO pkgrel and does not
        pass manifest_sha256 — so the row is truthful about the release and
        NULL about its manifest provenance. That combination is what the import
        used to clobber.
        """
        self.db.add_installed(name=name, version=version, release=release,
                              install_method="archive")
        self.assertIsNone(self.db.get_installed(name)["manifest_sha256"],
                          "precondition: an archive install leaves the manifest "
                          "hash NULL — if that changes, this test's premise "
                          "must be re-derived, not adjusted")

    # ---- the measured defect ---------------------------------------------

    def test_headerless_manifest_does_not_reset_a_healed_release(self):
        """The 215-row reset, reduced to one package."""
        self._write_manifest("linux-kernel", "6.18.10")   # no PACKAGE RELEASE
        self._register_healed("linux-kernel", "6.18.10", release=8)

        self.db.import_manifests(self.mdir)

        self.assertEqual(self.db.get_installed("linux-kernel")["release"], 8,
                         "a header-less manifest states no release; it must not "
                         "be read as a claim that the release is 1")

    def test_reset_does_not_spread_across_the_corpus(self):
        """One rebuild re-examines every manifest — none may lose its release."""
        expected = {"alpha": 3, "bravo": 7, "charlie": 2, "delta": 11}
        for name, release in expected.items():
            self._write_manifest(name, "1.0")
            self._register_healed(name, "1.0", release)

        self.db.import_manifests(self.mdir)

        observed = {n: self.db.get_installed(n)["release"] for n in expected}
        self.assertEqual(observed, expected,
                         "a corpus-wide import must be release-preserving for "
                         "every row it touches, not just the package that built")

    def test_repeated_imports_stay_stable(self):
        """Idempotence: the release must not drift over successive builds."""
        self._write_manifest("mpdecimal", "4.0.1")
        self._register_healed("mpdecimal", "4.0.1", release=4)
        for _ in range(3):
            self.db.import_manifests(self.mdir)
        self.assertEqual(self.db.get_installed("mpdecimal")["release"], 4)

    # ---- precedence -------------------------------------------------------

    def test_manifest_header_is_authoritative_over_the_row(self):
        """A stated release wins — that is the whole point of stating it."""
        self._write_manifest("pyyaml", "6.0.3", release=2)
        self._register_healed("pyyaml", "6.0.3", release=1)

        self.db.import_manifests(self.mdir)

        self.assertEqual(self.db.get_installed("pyyaml")["release"], 2,
                         "the manifest's own header outranks the stored row")

    def test_release_bump_in_the_manifest_is_applied(self):
        """A real r1 -> r2 rebuild must move the row forward, not be pinned."""
        self._write_manifest("bash", "5.3", content="r1", release=1)
        self.db.import_manifests(self.mdir)
        self.assertEqual(self.db.get_installed("bash")["release"], 1)

        self._write_manifest("bash", "5.3", content="r2-rebuilt", release=2)
        self.db.import_manifests(self.mdir)
        self.assertEqual(self.db.get_installed("bash")["release"], 2,
                         "preservation must not become a floor that blocks a "
                         "genuine release bump")

    def test_version_bump_without_a_header_falls_to_the_default(self):
        """A release counts rebuilds of ONE version and restarts on a bump.

        Carrying r7 across 6.18.10 -> 6.19.0 would trade one falsehood for
        another, so inheritance is scoped to a same-version re-register.
        """
        self._write_manifest("linux-kernel", "6.18.10")
        self._register_healed("linux-kernel", "6.18.10", release=7)

        for stale in self.mdir.glob("linux-kernel-*"):
            stale.unlink()
        self._write_manifest("linux-kernel", "6.19.0")
        self.db.import_manifests(self.mdir)

        row = self.db.get_installed("linux-kernel")
        self.assertEqual(row["version"], "6.19.0")
        self.assertEqual(row["release"], 1,
                         "a new version with no stated release starts at 1")

    def test_unseen_package_uses_the_schema_default(self):
        """With no row and no header there is nothing to carry — 1 is honest."""
        self._write_manifest("brand-new", "0.1")
        self.db.import_manifests(self.mdir)
        self.assertEqual(self.db.get_installed("brand-new")["release"], 1)


class InstallerManifestReleaseTest(unittest.TestCase):
    """The install-time writer states the release, closing the loop.

    An installed system's manifests are written by pkm itself, not by a
    builder, so `pkm import` on a live machine reads THESE bytes. If they omit
    the release, the target inherits the same silent-reset the build chroot
    had — the defect would simply move downstream. This walks the real
    round trip: installer writes -> parser reads -> import registers.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        (self.root / "usr" / "bin").mkdir(parents=True)
        (self.root / "usr" / "bin" / "demo").write_text("payload")
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_written_release_survives_a_reimport(self):
        from pkm.database import _parse_manifest
        from pkm.installer import PackageInstaller

        inst = PackageInstaller(self.db, root=str(self.root))
        inst._write_manifest("demo", "1.0", ["usr/bin/demo"], release=5)

        mdir = self.root / "var" / "lib" / "igos" / "packages"
        meta = _parse_manifest((mdir / "demo-1.0").read_text())
        self.assertEqual(meta.get("release"), 5,
                         "the install-time manifest must state the release")

        self.db.import_manifests(mdir)
        self.assertEqual(self.db.get_installed("demo")["release"], 5)

    def test_unknown_release_omits_the_header(self):
        """No claim beats a wrong claim — and the parser stays legacy-tolerant."""
        from pkm.database import _parse_manifest
        from pkm.installer import PackageInstaller

        inst = PackageInstaller(self.db, root=str(self.root))
        inst._write_manifest("demo", "1.0", ["usr/bin/demo"])

        mdir = self.root / "var" / "lib" / "igos" / "packages"
        text = (mdir / "demo-1.0").read_text()
        self.assertNotIn("PACKAGE RELEASE:", text)
        self.assertIsNotNone(_parse_manifest(text),
                             "a header-less manifest must still parse")


if __name__ == "__main__":
    unittest.main()
