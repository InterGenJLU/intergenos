"""Tests for installer/backend/config.py:generate_locale — C-010 + J-026 fix.

Covers: locale.conf write, localedef chroot invocation with correctly-parsed
base + encoding, skip behavior for C / POSIX / malformed locales, and
RuntimeError on localedef failure.

run_chroot is mocked at the module import path (config.py imports inside
the function, so we patch installer.backend.hooks.run_chroot).
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.backend.config import generate_locale


class TestGenerateLocale(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        (Path(self.tmp) / "etc").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _read_locale_conf(self):
        return (Path(self.tmp) / "etc" / "locale.conf").read_text()

    @patch("installer.backend.hooks.run_chroot")
    def test_en_us_utf8_writes_conf_and_invokes_localedef(self, mock_chroot):
        mock_chroot.return_value = (0, "", "")
        generate_locale(self.tmp, "en_US.UTF-8")
        self.assertEqual(self._read_locale_conf(), "LANG=en_US.UTF-8\n")
        mock_chroot.assert_called_once_with(
            self.tmp, "localedef -i en_US -f UTF-8 en_US.UTF-8"
        )

    @patch("installer.backend.hooks.run_chroot")
    def test_fr_fr_utf8_parses_base_and_encoding(self, mock_chroot):
        mock_chroot.return_value = (0, "", "")
        generate_locale(self.tmp, "fr_FR.UTF-8")
        self.assertEqual(self._read_locale_conf(), "LANG=fr_FR.UTF-8\n")
        mock_chroot.assert_called_once_with(
            self.tmp, "localedef -i fr_FR -f UTF-8 fr_FR.UTF-8"
        )

    @patch("installer.backend.hooks.run_chroot")
    def test_de_de_iso88591_parses_non_utf8_encoding(self, mock_chroot):
        # Non-UTF-8 encoding still resolves the right -f arg per partition.
        mock_chroot.return_value = (0, "", "")
        generate_locale(self.tmp, "de_DE.ISO-8859-1")
        mock_chroot.assert_called_once_with(
            self.tmp, "localedef -i de_DE -f ISO-8859-1 de_DE.ISO-8859-1"
        )

    @patch("installer.backend.hooks.run_chroot")
    def test_C_locale_skips_localedef(self, mock_chroot):
        # C / POSIX / single-name locales are always-present in glibc-core
        # baked set; no localedef invocation.
        generate_locale(self.tmp, "C")
        self.assertEqual(self._read_locale_conf(), "LANG=C\n")
        mock_chroot.assert_not_called()

    @patch("installer.backend.hooks.run_chroot")
    def test_POSIX_locale_skips_localedef(self, mock_chroot):
        generate_locale(self.tmp, "POSIX")
        self.assertEqual(self._read_locale_conf(), "LANG=POSIX\n")
        mock_chroot.assert_not_called()

    @patch("installer.backend.hooks.run_chroot")
    def test_malformed_empty_base_skips_localedef(self, mock_chroot):
        # ".UTF-8" — empty base. locale.conf still written so user can
        # hand-fix; don't run localedef on garbage.
        generate_locale(self.tmp, ".UTF-8")
        self.assertEqual(self._read_locale_conf(), "LANG=.UTF-8\n")
        mock_chroot.assert_not_called()

    @patch("installer.backend.hooks.run_chroot")
    def test_malformed_empty_encoding_skips_localedef(self, mock_chroot):
        # "fr_FR." — empty encoding after the dot.
        generate_locale(self.tmp, "fr_FR.")
        self.assertEqual(self._read_locale_conf(), "LANG=fr_FR.\n")
        mock_chroot.assert_not_called()

    @patch("installer.backend.hooks.run_chroot")
    def test_localedef_failure_raises_runtimeerror(self, mock_chroot):
        mock_chroot.return_value = (
            4, "", "cannot read character map directory `/usr/share/i18n/charmaps'\n"
        )
        with self.assertRaises(RuntimeError) as ctx:
            generate_locale(self.tmp, "ja_JP.UTF-8")
        # Error message includes locale name + parsed base/encoding +
        # captured stderr so operator can diagnose without re-running.
        msg = str(ctx.exception)
        self.assertIn("ja_JP.UTF-8", msg)
        self.assertIn("ja_JP", msg)
        self.assertIn("UTF-8", msg)
        self.assertIn("cannot read character map", msg)
        # locale.conf was still written before the failure (consistent
        # with the partial-write tolerance of the rest of generate_all).
        self.assertEqual(self._read_locale_conf(), "LANG=ja_JP.UTF-8\n")


if __name__ == "__main__":
    unittest.main()
