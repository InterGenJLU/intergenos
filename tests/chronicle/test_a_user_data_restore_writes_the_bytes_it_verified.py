# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A user-data restore writes the bytes it verified.

_restore_one hashed a stored user-data file by its path and then read the path
again to write the restored copy, so a stored file replaced between the two
was written without ever being checked, and a link at the stored name was
followed to whatever it named.

These cases capture a real user-data version into a directory-class target and
restore it in-process, as the user, beside the original. The swap is made at
the one moment that matters: right after the stored bytes are hashed, through
whichever hash entry point the engine uses for them.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chronicle import cas as _cas
from chronicle import engine as _engine
from chronicle import escalate as _escalate
from chronicle import paths as _paths
from chronicle import userdata as _userdata

ORIGINAL = b"the version that was captured\n" * 8
OTHER = b"bytes nobody verified\n" * 8


class AUserDataRestoreWritesTheBytesItVerified(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-test-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.now = [1_000_000.0]
        self.eng = _engine.Engine(local_root=os.path.join(self.tmp, "local"),
                                  now_fn=lambda: self.now[0])
        orig_cap = _escalate.has_cap_chown
        self.addCleanup(setattr, _escalate, "has_cap_chown", orig_cap)
        _escalate.has_cap_chown = lambda *a, **k: True
        home = Path(self.tmp) / "home"
        home.mkdir()
        self.path = str(home / "a.txt")
        Path(self.path).write_bytes(ORIGINAL)
        self.eng.config.user_data_paths = [str(home)]
        target = Path(self.tmp) / "target"
        target.mkdir()
        self.eng.target_adopt(str(target), target_class="directory")
        self.vid = self.eng.capture(_paths.LAYER_USER_DATA, reason="t")["version_id"]
        manifest = self.eng.get_manifest(_paths.LAYER_USER_DATA, self.vid)
        entry = next(e for e in manifest["entries"] if e["path"] == self.path)
        self.stored = Path(_userdata.read_file(self.eng.target_root(), self.vid, entry))
        self.assertEqual(self.stored.read_bytes(), ORIGINAL)

    def restore(self):
        out = self.eng.restore_apply(_paths.LAYER_USER_DATA, self.vid, [self.path],
                                     mode="beside")
        (result,) = out["results"]
        return result

    def replace_stored_with(self, data: bytes) -> None:
        tmp = self.stored.with_name(".swap")
        tmp.write_bytes(data)
        os.replace(tmp, self.stored)

    def test_control_an_unchanged_version_restores(self):
        result = self.restore()
        self.assertTrue(result["ok"], result)
        self.assertEqual(Path(result["written_to"]).read_bytes(), ORIGINAL)

    def test_a_stored_file_replaced_after_its_hash_is_not_what_is_written(self):
        swapped = []
        real_file, real_bytes = _cas.sha256_file, _cas.sha256_bytes

        def file_then_swap(path):
            digest = real_file(path)
            if not swapped and Path(path) == self.stored:
                self.replace_stored_with(OTHER)
                swapped.append(path)
            return digest

        def bytes_then_swap(data):
            digest = real_bytes(data)
            if not swapped and data == ORIGINAL:
                self.replace_stored_with(OTHER)
                swapped.append("bytes")
            return digest

        with mock.patch.object(_cas, "sha256_file", side_effect=file_then_swap), \
                mock.patch.object(_cas, "sha256_bytes", side_effect=bytes_then_swap):
            result = self.restore()
        self.assertTrue(swapped, "the stored bytes were never hashed")
        self.assertEqual(self.stored.read_bytes(), OTHER)
        self.assertTrue(result["ok"], result)
        self.assertEqual(Path(result["written_to"]).read_bytes(), ORIGINAL,
                         "bytes that were never verified were restored")

    def test_a_link_at_the_stored_name_is_refused(self):
        elsewhere = Path(self.tmp) / "elsewhere"
        elsewhere.write_bytes(ORIGINAL)
        self.stored.unlink()
        self.stored.symlink_to(elsewhere)
        result = self.restore()
        self.assertFalse(result["ok"], result)
        self.assertIn("symbolic link", result["reason"])
        self.assertFalse(Path(self.path + f".chronicle-restored-{self.vid}").exists())

    def test_a_stored_file_another_account_owns_is_refused(self):
        uid = os.geteuid()
        # The store itself stays the engine's (its directories are uid's), so
        # the case reaches the stored file's own owner check.
        with mock.patch.object(_paths, "store_owners", return_value={0, uid}), \
                mock.patch.object(os, "geteuid", return_value=uid + 1):
            result = self.restore()
        self.assertFalse(result["ok"], result)
        self.assertIn(f"owned by uid {uid}", result["reason"])
        self.assertFalse(Path(self.path + f".chronicle-restored-{self.vid}").exists())


if __name__ == "__main__":
    unittest.main()
