# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The ISO archive manifest is the shipped subset, and the staging gate
checks both directions.

The R001.2 install aborted at the installer's integrity check: the signed
manifest on the media listed every archive the build chroot holds (the
mirror's census) while the media, by design, carries only the archives the
ISO ships — 284 entries promised nothing on the stick. The build-time gate
could not catch it because it asked only "is every staged archive in the
manifest?", never "is every manifest entry staged?".

Two pieces close the class, both exercised here as real programs on real
files in a temporary directory:

  scripts/lib/manifest_coverage.py           (the shared logic)
  scripts/derive-iso-archive-manifest.py     (full manifest − excludes → ISO manifest)

The staging gate itself needs a signature that chains to the pinned release
key, which no test key can provide; its reverse check imports the same
module tested here, and the gate is fired against the real build tree (real
manifest, real archives, real exclusion list) as the proof that rides with
the change.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
DERIVE = SCRIPTS / "derive-iso-archive-manifest.py"

sys.path.insert(0, str(SCRIPTS / "lib"))
import manifest_coverage as mc  # noqa: E402


HEADER = (
    "# InterGenOS archive integrity manifest\n"
    "# Build: test-build\n"
    "# Built: 2026-09-02T00:00:00Z\n"
    "# Built-on: test-host\n"
    "# Manifest-version: 1\n"
)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
ENTRIES = (
    f"SHA256 (alpha-1.0.igos.tar.gz) = {SHA_A}\n"
    f"SHA256 (mirror-only-2.0.igos.tar.gz) = {SHA_B}\n"
    f"SHA256 (zeta-3.0.igos.tar.gz) = {SHA_C}\n"
)
TRAILER = "# Trace-runid: abc\n# End of manifest.\n"
FULL = HEADER + ENTRIES + TRAILER


class ReadExcludes(unittest.TestCase):
    def test_prefix_comments_and_blanks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "excludes.txt"
            p.write_text(
                "# a comment\n"
                "\n"
                "var/lib/igos/archives/mirror-only-2.0.igos.tar.gz\n"
                "bare-name-4.0.igos.tar.gz\n"
            )
            self.assertEqual(
                mc.read_excludes(p),
                {"mirror-only-2.0.igos.tar.gz", "bare-name-4.0.igos.tar.gz"},
            )


class DeriveIsoManifest(unittest.TestCase):
    def test_drops_excluded_keeps_the_rest_in_order(self):
        r = mc.derive_iso_manifest(FULL, {"mirror-only-2.0.igos.tar.gz"})
        self.assertEqual(r.kept, 2)
        self.assertEqual(r.dropped, ["mirror-only-2.0.igos.tar.gz"])
        self.assertEqual(r.excludes_absent, [])
        lines = r.text.splitlines()
        sha_lines = [l for l in lines if l.startswith("SHA256 ")]
        self.assertEqual(sha_lines, [
            f"SHA256 (alpha-1.0.igos.tar.gz) = {SHA_A}",
            f"SHA256 (zeta-3.0.igos.tar.gz) = {SHA_C}",
        ])
        self.assertNotIn("mirror-only", r.text)

    def test_header_scope_and_terminator(self):
        r = mc.derive_iso_manifest(FULL, {"mirror-only-2.0.igos.tar.gz"})
        lines = r.text.splitlines()
        self.assertEqual(lines[0], "# InterGenOS archive integrity manifest")
        self.assertIn("# Manifest-version: 1", lines)
        v = lines.index("# Manifest-version: 1")
        self.assertEqual(lines[v + 1], "# Manifest-scope: iso")
        self.assertEqual(lines[v + 2], "# Archives-excluded: 1")
        self.assertEqual(lines[-1], "# End of manifest.")
        self.assertIn("# Trace-runid: abc", lines)
        self.assertTrue(r.text.endswith("\n"))

    def test_existing_scope_line_is_replaced_not_duplicated(self):
        full = FULL.replace("# Manifest-version: 1\n",
                            "# Manifest-version: 1\n# Manifest-scope: mirror\n")
        r = mc.derive_iso_manifest(full, set())
        self.assertEqual(r.text.count("# Manifest-scope:"), 1)
        self.assertIn("# Manifest-scope: iso", r.text)

    def test_declared_but_absent_exclusions_are_reported_not_fatal(self):
        r = mc.derive_iso_manifest(FULL, {"never-built-9.9.igos.tar.gz"})
        self.assertEqual(r.kept, 3)
        self.assertEqual(r.excludes_absent, ["never-built-9.9.igos.tar.gz"])

    def test_refuses_missing_header(self):
        with self.assertRaises(ValueError):
            mc.derive_iso_manifest(ENTRIES + TRAILER, set())

    def test_refuses_missing_terminator(self):
        with self.assertRaises(ValueError):
            mc.derive_iso_manifest(HEADER + ENTRIES, set())

    def test_refuses_malformed_line(self):
        with self.assertRaises(ValueError):
            mc.derive_iso_manifest(HEADER + "garbage\n" + TRAILER, set())

    def test_refuses_empty_result(self):
        with self.assertRaises(ValueError):
            mc.derive_iso_manifest(FULL, {
                "alpha-1.0.igos.tar.gz",
                "mirror-only-2.0.igos.tar.gz",
                "zeta-3.0.igos.tar.gz",
            })


class ShippedSetAndCoverage(unittest.TestCase):
    def _tree(self, tmp):
        base = Path(tmp) / "archives"
        (base / "sub").mkdir(parents=True)
        (base / "alpha-1.0.igos.tar.gz").write_bytes(b"a")
        (base / "mirror-only-2.0.igos.tar.gz").write_bytes(b"b")
        (base / "sub" / "nested-5.0.igos.tar.gz").write_bytes(b"n")
        (base / "not-an-archive.txt").write_bytes(b"x")
        return base

    def test_shipped_set_subtracts_excludes_and_uses_posix_relpaths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._tree(tmp)
            got = mc.shipped_set(base, {"mirror-only-2.0.igos.tar.gz"})
            self.assertEqual(got, {"alpha-1.0.igos.tar.gz",
                                   "sub/nested-5.0.igos.tar.gz"})

    def test_reverse_check_names_promised_but_absent_entries(self):
        entries = {"alpha-1.0.igos.tar.gz": SHA_A,
                   "ghost-7.0.igos.tar.gz": SHA_B}
        unmanifested, missing = mc.coverage(entries, {"alpha-1.0.igos.tar.gz"})
        self.assertEqual(unmanifested, [])
        self.assertEqual(missing, ["ghost-7.0.igos.tar.gz"])

    def test_forward_check_names_staged_but_unmanifested(self):
        entries = {"alpha-1.0.igos.tar.gz": SHA_A}
        unmanifested, missing = mc.coverage(
            entries, {"alpha-1.0.igos.tar.gz", "extra-8.0.igos.tar.gz"})
        self.assertEqual(unmanifested, ["extra-8.0.igos.tar.gz"])
        self.assertEqual(missing, [])

    def test_the_r0012_shape_full_manifest_on_a_subset_media_is_caught(self):
        # The full manifest lists the mirror-only archive; the media (staged
        # archives minus the exclusion list) does not carry it.
        with tempfile.TemporaryDirectory() as tmp:
            base = self._tree(tmp)
            entries = {"alpha-1.0.igos.tar.gz": SHA_A,
                       "mirror-only-2.0.igos.tar.gz": SHA_B,
                       "sub/nested-5.0.igos.tar.gz": SHA_C}
            shipped = mc.shipped_set(base, {"mirror-only-2.0.igos.tar.gz"})
            unmanifested, missing = mc.coverage(entries, shipped)
            self.assertEqual(missing, ["mirror-only-2.0.igos.tar.gz"])
            self.assertEqual(unmanifested, [])


class DeriveProgram(unittest.TestCase):
    def test_end_to_end_on_real_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = Path(tmp) / "intergenos-archive-manifest.txt"
            full.write_text(FULL)
            ex = Path(tmp) / "excludes.txt"
            ex.write_text("var/lib/igos/archives/mirror-only-2.0.igos.tar.gz\n"
                          "var/lib/igos/archives/never-built-9.9.igos.tar.gz\n")
            out = Path(tmp) / "intergenos-archive-manifest-iso.txt"
            p = subprocess.run(
                [sys.executable, str(DERIVE),
                 "--full-manifest", str(full),
                 "--archive-excludes", str(ex),
                 "--output", str(out)],
                capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            text = out.read_text()
            self.assertIn("# Manifest-scope: iso", text)
            self.assertIn("# Archives-excluded: 1", text)
            self.assertNotIn("mirror-only", text)
            self.assertEqual(text.count("SHA256 ("), 2)
            self.assertIn("kept 2", p.stderr)
            self.assertIn("excluded 1", p.stderr)
            self.assertIn("never-built-9.9", p.stderr)

    def test_program_refuses_an_empty_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = Path(tmp) / "full.txt"
            full.write_text(FULL)
            ex = Path(tmp) / "excludes.txt"
            ex.write_text("alpha-1.0.igos.tar.gz\nmirror-only-2.0.igos.tar.gz\n"
                          "zeta-3.0.igos.tar.gz\n")
            out = Path(tmp) / "iso.txt"
            p = subprocess.run(
                [sys.executable, str(DERIVE), "--full-manifest", str(full),
                 "--archive-excludes", str(ex), "--output", str(out)],
                capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
