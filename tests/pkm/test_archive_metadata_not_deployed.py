#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Sealed lifecycle hooks are archive metadata, never payload.

Recipe lifecycle functions now travel inside the signed archive as
`.scripts/<event>.sh`, and pkm fires them out of the EXTRACTED STAGING dir. They
must therefore never reach the target: a deployed `/.scripts/post_install.sh`
would be an unowned script sitting at the filesystem root of every install —
this seam manufacturing the very unowned-file class it was built to close.

`.PKGINFO` had exactly this leak once already, and the fix was an exact-name
exclusion. An exact-name test cannot see `.scripts/post_install.sh`, so the
predicate had to grow a directory arm. These tests pin both the predicate and
the real tar path, because the earlier leak was in the tar path and not in the
idea.
"""
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from pkm.installer import (
    _ARCHIVE_METADATA_DIRS,
    _ARCHIVE_METADATA_FILES,
    _is_archive_metadata,
    _safe_extract_tar,
)

# What the deploy extract passes. The STAGING extract deliberately passes less —
# staging is where .PKGINFO is read and where pkm fires .scripts/<event>.sh from,
# so excluding metadata there would stop every sealed hook from ever running.
DEPLOY_EXCLUDES = _ARCHIVE_METADATA_FILES | _ARCHIVE_METADATA_DIRS


class MetadataPredicateTest(unittest.TestCase):
    def test_metadata_files(self):
        for name in _ARCHIVE_METADATA_FILES:
            self.assertTrue(_is_archive_metadata(name), name)

    def test_metadata_dir_and_everything_under_it(self):
        self.assertTrue(_is_archive_metadata(".scripts"))
        self.assertTrue(_is_archive_metadata(".scripts/"))
        self.assertTrue(_is_archive_metadata(".scripts/post_install.sh"))
        self.assertTrue(_is_archive_metadata(".scripts/pre_remove.sh"))

    def test_payload_is_not_metadata(self):
        for p in ("usr/bin/x", "etc/foo.conf", "usr/lib/",
                  "usr/share/.scripts/x"):
            self.assertFalse(_is_archive_metadata(p), p)

    def test_the_dir_set_is_not_empty(self):
        """A predicate that excludes nothing would pass every test above."""
        self.assertIn(".scripts", _ARCHIVE_METADATA_DIRS)


class DeployExtractTest(unittest.TestCase):
    """The real extract path, on a real archive carrying a sealed hook."""

    def _archive(self, path, members):
        with tarfile.open(path, "w:gz") as tf:
            for name, body in members.items():
                data = body.encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))

    def test_scripts_dir_never_lands_on_the_root(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            archive = tmp / "demo-1.0.igos.tar.gz"
            root = tmp / "root"
            root.mkdir()
            self._archive(archive, {
                "./.PKGINFO": "pkgname=demo\n",
                "./.scripts/post_install.sh": "#!/bin/bash\nldconfig\n",
                "./usr/bin/demo": "payload\n",
            })

            ok, err = _safe_extract_tar(
                archive, root, exclude_paths=DEPLOY_EXCLUDES)

            self.assertTrue(ok, err)
            self.assertTrue((root / "usr" / "bin" / "demo").is_file(),
                            "payload must still deploy")
            self.assertFalse((root / ".scripts").exists(),
                             "the sealed hook must not reach the target root")
            self.assertFalse((root / ".PKGINFO").exists())

    def test_bare_named_members_are_excluded_too(self):
        """Archives without the './' prefix must behave identically."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            archive = tmp / "demo-1.0.igos.tar.gz"
            root = tmp / "root"
            root.mkdir()
            self._archive(archive, {
                ".scripts/post_install.sh": "#!/bin/bash\n:\n",
                "usr/bin/demo": "payload\n",
            })

            ok, err = _safe_extract_tar(
                archive, root, exclude_paths=DEPLOY_EXCLUDES)

            self.assertTrue(ok, err)
            self.assertTrue((root / "usr" / "bin" / "demo").is_file())
            self.assertFalse((root / ".scripts").exists())

    def test_staging_extract_KEEPS_the_sealed_hook(self):
        """The regression this pins cost a caught test: an unconditional
        metadata filter stripped .scripts from STAGING too, so pkm had nothing
        to fire and every sealed hook silently did nothing. Exclusion is the
        caller's decision, and only the deploy caller makes it."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            archive = tmp / "demo-1.0.igos.tar.gz"
            staging = tmp / "staging"
            staging.mkdir()
            self._archive(archive, {
                "./.PKGINFO": "pkgname=demo\n",
                "./.scripts/post_install.sh": "#!/bin/bash\n:\n",
                "./usr/bin/demo": "payload\n",
            })

            ok, err = _safe_extract_tar(archive, staging, exclude_paths=())

            self.assertTrue(ok, err)
            self.assertTrue((staging / ".scripts" / "post_install.sh").is_file(),
                            "pkm fires the hook out of staging — it must be there")
            self.assertTrue((staging / ".PKGINFO").is_file())


if __name__ == "__main__":
    unittest.main()
