#!/usr/bin/env python3
"""Configuration fingerprints cover every watched entry deterministically."""

import os
import tempfile
import unittest
from pathlib import Path

from chronicle import sentinel as _sentinel


class SentinelFingerprintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chronicle-fingerprint-")
        self.root = Path(self.tmp.name) / "watched"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_future_dated_entry_cannot_mask_another_file_edit(self):
        future = self.root / "future"
        changed = self.root / "changed"
        future.write_text("future\n")
        changed.write_text("before\n")
        future_ns = 1_900_000_000_000_000_000
        os.utime(future, ns=(future_ns, future_ns))
        before = _sentinel.config_set_fingerprint([str(self.root)])

        changed.write_text("after\n")
        after = _sentinel.config_set_fingerprint([str(self.root)])

        self.assertNotEqual(before, after)

    def test_same_second_nanosecond_edit_changes_fingerprint(self):
        document = self.root / "document"
        second = 1_800_000_000_000_000_000
        document.write_bytes(b"AAAA")
        os.utime(document, ns=(second + 100, second + 100))
        before = _sentinel.config_set_fingerprint([str(self.root)])

        document.write_bytes(b"BBBB")
        os.utime(document, ns=(second + 900, second + 900))
        after = _sentinel.config_set_fingerprint([str(self.root)])

        self.assertNotEqual(before, after)

    def test_unchanged_tree_has_a_stable_fingerprint(self):
        (self.root / "one").write_text("one\n")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "two").write_text("two\n")
        self.assertEqual(
            _sentinel.config_set_fingerprint([str(self.root)]),
            _sentinel.config_set_fingerprint([str(self.root)]),
        )

    def test_delete_and_rename_change_fingerprint(self):
        document = self.root / "before"
        document.write_text("contents\n")
        original = _sentinel.config_set_fingerprint([str(self.root)])
        document.rename(self.root / "after")
        renamed = _sentinel.config_set_fingerprint([str(self.root)])
        (self.root / "after").unlink()
        deleted = _sentinel.config_set_fingerprint([str(self.root)])
        self.assertNotEqual(original, renamed)
        self.assertNotEqual(renamed, deleted)

    def test_directory_link_target_changes_fingerprint(self):
        (self.root / "first").mkdir()
        (self.root / "second").mkdir()
        link = self.root / "active"
        link.symlink_to("first")
        before = _sentinel.config_set_fingerprint([str(self.root)])
        link.unlink()
        link.symlink_to("second")
        after = _sentinel.config_set_fingerprint([str(self.root)])
        self.assertNotEqual(before, after)

    def test_excluded_subtree_does_not_affect_fingerprint(self):
        kept = self.root / "kept"
        kept.write_text("kept\n")
        excluded = self.root / "excluded"
        excluded.mkdir()
        ignored = excluded / "ignored"
        ignored.write_text("before\n")
        excludes = [str(excluded)]
        original = _sentinel.config_set_fingerprint(
            [str(self.root)], excludes=excludes
        )

        ignored.write_text("after\n")
        ignored_change = _sentinel.config_set_fingerprint(
            [str(self.root)], excludes=excludes
        )
        kept.write_text("changed\n")
        kept_change = _sentinel.config_set_fingerprint(
            [str(self.root)], excludes=excludes
        )

        self.assertEqual(original, ignored_change)
        self.assertNotEqual(ignored_change, kept_change)

    def test_non_utf8_filename_is_stable_and_observable(self):
        raw_path = os.path.join(
            os.fsencode(self.root), b"configuration-\xff"
        )
        with open(raw_path, "wb") as stream:
            stream.write(b"before")

        original = _sentinel.config_set_fingerprint([str(self.root)])
        stable = _sentinel.config_set_fingerprint([str(self.root)])
        with open(raw_path, "wb") as stream:
            stream.write(b"after!")
        changed = _sentinel.config_set_fingerprint([str(self.root)])

        self.assertEqual(original, stable)
        self.assertNotEqual(stable, changed)


if __name__ == "__main__":
    unittest.main()
