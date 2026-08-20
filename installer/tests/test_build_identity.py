# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the installed system's release identity (N-6 + the single-source rule).

Two things are proven here.

BUILD IDENTITY (N-6). An ISO previously identified itself nowhere a user could
see. The medium's os-release carries BUILD_ID (stamped at squashfs assembly),
and Forge records that value onto the installed target as IMAGE_VERSION — so an
installed system can say which medium installed it.

SINGLE SOURCE. The release identity strings (NAME, VERSION, VERSION_ID,
PRETTY_NAME, the codename, the LOGO key) are authored in exactly one place:
the files packages/core/intergenos-base-files ships, which the packages phase
has already installed on the target by the time the config phase runs. Forge
does not restate them. Before this rule, Forge wrote its own hand-maintained
copy of os-release, and that copy had already drifted from the shipped file:
every installed system lost the shipped LOGO= line, silently.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

from installer.backend.config import (
    generate_branding,
    generate_os_release,
    _live_build_id,
)

# What intergenos-base-files ships, in the shape the packages phase leaves on
# the target. Field values are deliberately NOT the current release strings —
# the point is that whatever the package ships is what the installed system
# gets, so the test must fail if Forge ever re-imposes strings of its own.
SHIPPED_OS_RELEASE = (
    'NAME="InterGenOS"\n'
    'VERSION="R000.0 (Testcase)"\n'
    'ID=intergenos\n'
    'ID_LIKE=lfs\n'
    'VERSION_ID=r000.0\n'
    'VERSION_CODENAME=testcase\n'
    'PRETTY_NAME="InterGenOS R000.0 (Testcase)"\n'
    'HOME_URL="https://github.com/InterGenJLU/intergenos"\n'
    'BUG_REPORT_URL="https://github.com/InterGenJLU/intergenos/issues"\n'
    'LOGO=intergenos\n'
)
SHIPPED_IGOS_RELEASE = "R000.0\n"
SHIPPED_ISSUE = "\n  InterGenOS R000.0 (Testcase)\n  Kernel \\r on \\m (\\l)\n\n"
SHIPPED_MOTD = "\n  Welcome to InterGenOS\n\n"


class _TargetWithBaseFiles(unittest.TestCase):
    """A target root in the state PHASE_PACKAGES leaves it in."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.etc = Path(self.tmp) / "etc"
        self.etc.mkdir(parents=True)
        (self.etc / "os-release").write_text(SHIPPED_OS_RELEASE)
        (self.etc / "igos-release").write_text(SHIPPED_IGOS_RELEASE)
        (self.etc / "issue").write_text(SHIPPED_ISSUE)
        (self.etc / "motd").write_text(SHIPPED_MOTD)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _os_release(self):
        return (self.etc / "os-release").read_text()


class TestInstalledIdentityComesFromThePackage(_TargetWithBaseFiles):
    @patch("installer.backend.config._live_build_id", return_value=None)
    def test_unstamped_medium_leaves_the_shipped_file_byte_identical(self, _):
        generate_os_release(self.tmp)
        self.assertEqual(self._os_release(), SHIPPED_OS_RELEASE)

    @patch("installer.backend.config._live_build_id", return_value="ge9b-10-dev")
    def test_every_shipped_field_survives(self, _):
        generate_os_release(self.tmp)
        content = self._os_release()
        for line in SHIPPED_OS_RELEASE.splitlines():
            self.assertIn(line + "\n", content)

    @patch("installer.backend.config._live_build_id", return_value="ge9b-10-dev")
    def test_shipped_logo_key_reaches_the_installed_system(self, _):
        # The regression this rule exists for: Forge's own copy of os-release
        # never carried LOGO=, so every installed system lost it.
        generate_os_release(self.tmp)
        self.assertIn("LOGO=intergenos\n", self._os_release())

    @patch("installer.backend.config._live_build_id", return_value="ge9b-10-dev")
    def test_medium_build_id_recorded_as_image_version(self, _):
        generate_os_release(self.tmp)
        self.assertIn('IMAGE_VERSION="ge9b-10-dev"\n', self._os_release())

    @patch("installer.backend.config._live_build_id", return_value=None)
    def test_unstamped_medium_writes_no_empty_field(self, _):
        generate_os_release(self.tmp)
        content = self._os_release()
        self.assertNotIn("IMAGE_VERSION", content)
        self.assertNotIn("BUILD_ID", content)

    @patch("installer.backend.config._live_build_id", return_value="ge9b-10-dev")
    def test_medium_stamp_fields_are_not_carried_through_verbatim(self, _):
        # A target file that already carried a stamp (a re-run, or a shipped
        # file stamped by the image build) must not end with two of them.
        (self.etc / "os-release").write_text(
            SHIPPED_OS_RELEASE + 'BUILD_ID="stale"\nIMAGE_VERSION="stale"\n'
        )
        generate_os_release(self.tmp)
        content = self._os_release()
        self.assertNotIn("stale", content)
        self.assertNotIn("BUILD_ID=", content)
        self.assertEqual(content.count("IMAGE_VERSION="), 1)

    @patch("installer.backend.config._live_build_id", return_value=None)
    def test_igos_release_is_left_as_the_package_shipped_it(self, _):
        generate_os_release(self.tmp)
        self.assertEqual((self.etc / "igos-release").read_text(),
                         SHIPPED_IGOS_RELEASE)

    @patch("installer.backend.config._live_build_id", return_value=None)
    def test_branding_files_are_left_as_the_package_shipped_them(self, _):
        generate_branding(self.tmp)
        self.assertEqual((self.etc / "issue").read_text(), SHIPPED_ISSUE)
        self.assertEqual((self.etc / "motd").read_text(), SHIPPED_MOTD)


class TestMissingPackageFilesFailLoudly(_TargetWithBaseFiles):
    """A target without base-files is a broken install, not a default."""

    @patch("installer.backend.config._live_build_id", return_value=None)
    def test_absent_os_release_raises(self, _):
        (self.etc / "os-release").unlink()
        with self.assertRaises(FileNotFoundError) as ctx:
            generate_os_release(self.tmp)
        self.assertIn("intergenos-base-files", str(ctx.exception))

    @patch("installer.backend.config._live_build_id", return_value=None)
    def test_absent_igos_release_raises(self, _):
        (self.etc / "igos-release").unlink()
        with self.assertRaises(FileNotFoundError):
            generate_os_release(self.tmp)

    @patch("installer.backend.config._live_build_id", return_value=None)
    def test_os_release_without_identity_fields_raises(self, _):
        (self.etc / "os-release").write_text("ID=intergenos\n")
        with self.assertRaises(ValueError) as ctx:
            generate_os_release(self.tmp)
        self.assertIn("PRETTY_NAME", str(ctx.exception))

    def test_absent_issue_raises(self):
        (self.etc / "issue").unlink()
        with self.assertRaises(FileNotFoundError) as ctx:
            generate_branding(self.tmp)
        self.assertIn("intergenos-base-files", str(ctx.exception))

    def test_absent_motd_raises(self):
        (self.etc / "motd").unlink()
        with self.assertRaises(FileNotFoundError):
            generate_branding(self.tmp)


class TestLiveBuildIdParse(unittest.TestCase):
    def test_parses_quoted_value(self):
        data = 'NAME="InterGenOS"\nBUILD_ID="ge9b-10-dev"\n'
        with patch("builtins.open", mock_open(read_data=data)):
            self.assertEqual(_live_build_id(), "ge9b-10-dev")

    def test_absent_field_returns_none(self):
        data = 'NAME="InterGenOS"\nVERSION_ID=r001.1\n'
        with patch("builtins.open", mock_open(read_data=data)):
            self.assertIsNone(_live_build_id())

    def test_unreadable_file_returns_none(self):
        with patch("builtins.open", side_effect=OSError):
            self.assertIsNone(_live_build_id())


if __name__ == "__main__":
    unittest.main()
