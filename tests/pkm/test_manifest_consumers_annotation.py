#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every text-manifest consumer reads the canonical FILE LIST shape.

A manifest entry is one of three things:

    "<path>/"                   a directory
    "<path> sha256:<64 hex>"    a regular file
    "<path>"                    anything else, or a pre-annotation manifest

The annotation is anchored at END OF LINE, never delimited, because manifest
paths may contain spaces — linux-firmware ships several, e.g.
"brcmfmac43455-sdio.Raspberry Pi Foundation-Raspberry Pi 4 Model B.txt.xz". A
consumer that takes the first whitespace-delimited token truncates that to
"brcmfmac43455-sdio.Raspberry", which is not a path that exists, and then acts
on the truncation without complaint.

Making the bash builder emit the full shape means consumers that previously saw
only bare paths from those manifests now see annotated ones. That is why this
file exists: the format change and the parser sweep are one change, and a
consumer left behind does not fail loudly, it quietly acts on a wrong path.
Two of the three swept here sit inside fail-closed gates.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

SPACED = ("usr/lib/firmware/brcmfmac43455-sdio.Raspberry Pi Foundation-"
          "Raspberry Pi 4 Model B.txt.xz")
HASH = "0" * 64


def _manifest_text(entries, name="demo", version="1.0"):
    head = [f"PACKAGE NAME: {name}-{version}",
            f"PACKAGE VERSION: {version}",
            "UNCOMPRESSED SIZE: 1K (1024 bytes)",
            "BUILD DATE: 2026-07-30T00:00:00Z",
            "BUILD SYSTEM: InterGenOS LFS 13.0",
            "DESCRIPTION:",
            f"{name}: a demo package",
            "",
            "FILE LIST:"]
    return "\n".join(head + entries) + "\n"


class DeriveIsoExclusionsTest(unittest.TestCase):
    """scripts/derive-iso-exclusions.py — feeds mksquashfs -ef."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "derive_iso_exclusions",
            REPO_ROOT / "scripts" / "derive-iso-exclusions.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _extract(self, entries):
        p = self.tmp / "demo-1.0"
        p.write_text(_manifest_text(entries))
        return self.mod.extract_file_list(p)

    def test_the_hash_annotation_is_stripped(self):
        self.assertEqual(self._extract([f"usr/bin/demo sha256:{HASH}"]),
                         ["usr/bin/demo"])

    def test_a_path_with_spaces_survives_the_annotation(self):
        self.assertEqual(self._extract([f"{SPACED} sha256:{HASH}"]), [SPACED])

    def test_a_path_with_spaces_survives_without_one(self):
        self.assertEqual(self._extract([SPACED]), [SPACED])

    def test_directory_markers_are_skipped(self):
        self.assertEqual(self._extract(["usr/bin/", f"usr/bin/x sha256:{HASH}"]),
                         ["usr/bin/x"])

    def test_a_bare_legacy_entry_still_parses(self):
        self.assertEqual(self._extract(["usr/bin/demo"]), ["usr/bin/demo"])


class EmitPackageArchivesTest(unittest.TestCase):
    """scripts/emit-package-archives.py — builds archives from the file list."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "emit_package_archives",
            REPO_ROOT / "scripts" / "emit-package-archives.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _read(self, entries):
        p = self.tmp / "demo-1.0"
        p.write_text(_manifest_text(entries))
        return self.mod._read_manifest(p)

    def test_annotated_entries_yield_usable_paths(self):
        meta = self._read([f"usr/bin/demo sha256:{HASH}"])
        self.assertEqual(meta["files"], ["usr/bin/demo"])

    def test_a_path_with_spaces_is_not_truncated(self):
        meta = self._read([f"{SPACED} sha256:{HASH}"])
        self.assertEqual(meta["files"], [SPACED])

    def test_directory_markers_do_not_become_archive_members(self):
        meta = self._read(["usr/", "usr/bin/", f"usr/bin/x sha256:{HASH}"])
        self.assertEqual(meta["files"], ["usr/bin/x"])

    def test_a_bare_legacy_entry_still_parses(self):
        self.assertEqual(self._read(["usr/bin/demo"])["files"],
                         ["usr/bin/demo"])


class SquashfsPrunePathCaptureTest(unittest.TestCase):
    """scripts/build-squashfs.sh Step 2.5 — the co-ownership heal's two awks.

    The capture and the claim scan are compared against each other, so they
    must normalize identically. Two packages co-owning a path record their own
    content hashes for it, so comparing annotated lines would never match and
    the heal — a fail-closed gate whose whole job is to catch prune casualties
    — would silently stop finding any.
    """

    CAPTURE = r"""awk 'f{sub(/ sha256:[0-9a-f]+$/, ""); print} /^FILE LIST:$/{f=1}'"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _capture(self, entries):
        p = self.tmp / "pruned-1.0"
        p.write_text(_manifest_text(entries))
        r = subprocess.run(["bash", "-c", f'{self.CAPTURE} "{p}"'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_the_capture_strips_the_annotation(self):
        self.assertEqual(self._capture([f"usr/bin/demo sha256:{HASH}"]),
                         ["usr/bin/demo"])

    def test_the_capture_keeps_paths_with_spaces_whole(self):
        self.assertEqual(self._capture([f"{SPACED} sha256:{HASH}"]), [SPACED])

    def test_two_packages_hashing_a_shared_path_differently_still_match(self):
        """The property the heal depends on."""
        a = self._capture([f"usr/lib/libshared.so sha256:{'a' * 64}"])
        b = self._capture([f"usr/lib/libshared.so sha256:{'b' * 64}"])
        self.assertEqual(a, b)
        self.assertEqual(a, ["usr/lib/libshared.so"])

    def test_the_shipped_script_uses_the_stripping_form_in_both_awks(self):
        text = (REPO_ROOT / "scripts" / "build-squashfs.sh").read_text()
        start = text.index("PRUNED_PATHS=")
        window = text[start:start + 3000]
        self.assertEqual(
            window.count('sub(/ sha256:[0-9a-f]+$/, ""'), 2,
            "both the pruned-path capture and the claim scan must strip the "
            "annotation, or they cannot be compared to each other")


if __name__ == "__main__":
    unittest.main()
