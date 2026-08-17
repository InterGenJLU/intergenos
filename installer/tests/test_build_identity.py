# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the build-identity plumbing (N-6).

An ISO previously identified itself nowhere a user could see. The medium's
os-release now carries BUILD_ID (stamped at squashfs assembly), and Forge
records the live medium's BUILD_ID onto the installed target's os-release
as IMAGE_VERSION — so an installed system can say which medium installed
it. When the medium carries no stamp, the installed os-release is exactly
the historical nine-line file, no empty field.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

from installer.backend.config import generate_os_release, _live_build_id


class TestInstalledImageVersion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        (Path(self.tmp) / "etc").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _os_release(self):
        return (Path(self.tmp) / "etc" / "os-release").read_text()

    @patch("installer.backend.config._live_build_id", return_value="ge9b-10-dev")
    def test_medium_build_id_recorded_as_image_version(self, _):
        generate_os_release(self.tmp)
        content = self._os_release()
        self.assertIn('IMAGE_VERSION="ge9b-10-dev"\n', content)
        self.assertIn('NAME="InterGenOS"\n', content)

    @patch("installer.backend.config._live_build_id", return_value=None)
    def test_unstamped_medium_writes_no_empty_field(self, _):
        generate_os_release(self.tmp)
        content = self._os_release()
        self.assertNotIn("IMAGE_VERSION", content)
        self.assertNotIn("BUILD_ID", content)
        self.assertEqual(len(content.splitlines()), 9)


class TestLiveBuildIdParse(unittest.TestCase):
    def test_parses_quoted_value(self):
        data = 'NAME="InterGenOS"\nBUILD_ID="ge9b-10-dev"\n'
        with patch("builtins.open", mock_open(read_data=data)):
            self.assertEqual(_live_build_id(), "ge9b-10-dev")

    def test_absent_field_returns_none(self):
        data = 'NAME="InterGenOS"\nVERSION_ID=1.0\n'
        with patch("builtins.open", mock_open(read_data=data)):
            self.assertIsNone(_live_build_id())

    def test_unreadable_file_returns_none(self):
        with patch("builtins.open", side_effect=OSError):
            self.assertIsNone(_live_build_id())


if __name__ == "__main__":
    unittest.main()
