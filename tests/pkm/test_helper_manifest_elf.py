"""Wedge tests for the wire-up-time ELF word-size re-audit in
_read_helper_manifest (RT-1 deposit gate, second look).

The record-then-swap window: the helper library audits a file's bytes as it
is RECORDED, but the bytes pkm wires up are whatever is on disk AFTER the
helper exited. These tests prove the manifest reader re-reads every tracked
path and refuses a wire-up whose on-disk width violates the helper's
declared contract — including the swap case and the dangling-link-target-
appeared case the record-time check structurally cannot see.
"""

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pkm.installer  # noqa: E402
from pkm.installer import _read_helper_manifest  # noqa: E402


def _elf(ei_class):
    head = bytearray(24)
    head[0:4] = b"\x7fELF"
    head[4] = ei_class
    head[5] = 1
    struct.pack_into("<H", head, 18, 62 if ei_class == 2 else 3)
    return bytes(head)


ELF64 = _elf(2)
ELF32 = _elf(1)


class TestManifestElfReaudit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.manifest_dir = self.tmp / "helpers"
        self.manifest_dir.mkdir()
        self.deposit_dir = self.tmp / "opt" / "app"
        self.deposit_dir.mkdir(parents=True)
        self._patcher = patch.object(
            pkm.installer, "HELPER_MANIFEST_DIR", self.manifest_dir)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def _write_manifest(self, name, files, elf_class=None, symlinks=()):
        manifest = {
            "version": 1,
            "name": name,
            "version_installed": "1.0",
            "files": files,
            "symlinks": list(symlinks),
            "depends": [],
            "post_install_actions_log": [],
        }
        if elf_class is not None:
            manifest["elf_class"] = elf_class
        (self.manifest_dir / f"{name}.manifest").write_text(
            json.dumps(manifest))

    def _deposit(self, fname, data):
        # deposits live under /opt (allowlisted prefix) via the tmp root —
        # the reader opens the path directly, so use a real absolute path
        # under an allowlisted-looking prefix inside tmp.
        p = self.deposit_dir / fname
        p.write_bytes(data)
        return str(p)

    def test_green_matching_width(self):
        path = self._deposit("good64.bin", ELF64)
        # allowlist requires /usr|/opt|/etc|/var/lib prefixes; the tmp path
        # fails that, so test the re-audit through an allowlisted literal:
        # patch the allowlist to include the tmp root for this test.
        with patch.object(pkm.installer, "HELPER_PATH_ALLOWLIST_PREFIXES",
                          (str(self.tmp),)):
            manifest, err = self._read("h1", [path], "64")
        self.assertIsNone(err)
        self.assertEqual(manifest["elf_class"], "64")

    def _read(self, name, files, elf_class=None, symlinks=()):
        self._write_manifest(name, files, elf_class, symlinks)
        return _read_helper_manifest(name)

    def test_red_swap_after_record(self):
        # the record-time audit saw a good 64; the file on disk at wire-up
        # is a 32 — the swap must refuse the wire-up.
        path = self._deposit("swapped.bin", ELF32)
        with patch.object(pkm.installer, "HELPER_PATH_ALLOWLIST_PREFIXES",
                          (str(self.tmp),)):
            manifest, err = self._read("h2", [path], "64")
        self.assertIsNone(manifest)
        self.assertIn("word-size mismatch", err)
        self.assertIn("32-bit", err)

    def test_red_dangling_link_target_appeared(self):
        # a dangling symlink recorded at deposit time whose wrong-width
        # target appeared later: realpath resolution sees the target now.
        target = self._deposit("late32.bin", ELF32)
        link = self.deposit_dir / "app-link"
        link.symlink_to(target)
        with patch.object(pkm.installer, "HELPER_PATH_ALLOWLIST_PREFIXES",
                          (str(self.tmp),)):
            manifest, err = self._read("h3", [str(link)], "64")
        self.assertIsNone(manifest)
        self.assertIn("word-size mismatch", err)

    def test_green_mixed_waives(self):
        path = self._deposit("any32.bin", ELF32)
        with patch.object(pkm.installer, "HELPER_PATH_ALLOWLIST_PREFIXES",
                          (str(self.tmp),)):
            manifest, err = self._read("h4", [path], "mixed")
        self.assertIsNone(err)

    def test_green_absent_field_defaults_64(self):
        # a pre-field manifest (no elf_class key): the tree-wide 64 default
        # applies — a 64-bit deposit passes, and the reader does not error
        # on the missing key.
        path = self._deposit("old64.bin", ELF64)
        with patch.object(pkm.installer, "HELPER_PATH_ALLOWLIST_PREFIXES",
                          (str(self.tmp),)):
            manifest, err = self._read("h5", [path], elf_class=None)
        self.assertIsNone(err)

    def test_red_absent_field_still_audits(self):
        path = self._deposit("old32.bin", ELF32)
        with patch.object(pkm.installer, "HELPER_PATH_ALLOWLIST_PREFIXES",
                          (str(self.tmp),)):
            manifest, err = self._read("h6", [path], elf_class=None)
        self.assertIsNone(manifest)
        self.assertIn("word-size mismatch", err)

    def test_red_invalid_declaration(self):
        with patch.object(pkm.installer, "HELPER_PATH_ALLOWLIST_PREFIXES",
                          (str(self.tmp),)):
            manifest, err = self._read("h7", [], "both")
        self.assertIsNone(manifest)
        self.assertIn("invalid elf_class", err)

    def test_green_absent_file_tolerated(self):
        # a tracked path missing at wire-up is another mechanism's problem,
        # never a width violation.
        with patch.object(pkm.installer, "HELPER_PATH_ALLOWLIST_PREFIXES",
                          (str(self.tmp),)):
            manifest, err = self._read(
                "h8", [str(self.deposit_dir / "vanished.bin")], "64")
        self.assertIsNone(err)

    def test_green_non_elf_ignored(self):
        path = self._deposit("notes.txt", b"plain text config, not a binary")
        with patch.object(pkm.installer, "HELPER_PATH_ALLOWLIST_PREFIXES",
                          (str(self.tmp),)):
            manifest, err = self._read("h9", [path], "64")
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
