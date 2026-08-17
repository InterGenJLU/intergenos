# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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


class TestGenerateLocaleInjectionDefense(unittest.TestCase):
    """S4 (security-review 2026-07-01): the user-selected locale is interpolated
    into a root `bash -c` inside the target chroot. A name carrying shell
    metacharacters must be REFUSED before it reaches the shell — fail-closed, and
    never persist a poisoned locale.conf. (Pre-fix these drove localedef via
    run_chroot with the injected string.)"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        (Path(self.tmp) / "etc").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _locale_conf(self):
        return Path(self.tmp) / "etc" / "locale.conf"

    # Every entry is a locale name laced with a shell breakout. The command
    # substitution / separator / redirect classes are all covered.
    _INJECTIONS = [
        "en_US.UTF-8; touch /pwned",
        "en_US.UTF-8 && rm -rf /",
        "en_US.UTF-8 | tee /pwned",
        "en_US.UTF-8`touch /pwned`",
        "en_US.UTF-8$(touch /pwned)",
        "en_US.UTF-8 > /etc/shadow",
        "en_US.UTF-8\nrm -rf /",
        "$(reboot)",
        "en_US.UTF-8 ${IFS}rm",
        'en_US.UTF-8"; rm -rf /;"',
    ]

    def test_shell_injection_locales_are_refused(self):
        for bad in self._INJECTIONS:
            with self.subTest(locale=bad):
                with patch("installer.backend.hooks.run_chroot") as mock_chroot:
                    with self.assertRaises(ValueError):
                        generate_locale(self.tmp, bad)
                    # Never reached the root shell...
                    mock_chroot.assert_not_called()
                    # ...and never persisted the poisoned value.
                    self.assertFalse(self._locale_conf().exists(),
                                     f"locale.conf must NOT be written for {bad!r}")

    def test_whitespace_and_control_names_are_refused(self):
        for bad in ("en US.UTF-8", "en_US.UTF 8", "\ten_US.UTF-8", "en_US.UTF-8 "):
            with self.subTest(locale=bad):
                with patch("installer.backend.hooks.run_chroot") as mock_chroot:
                    with self.assertRaises(ValueError):
                        generate_locale(self.tmp, bad)
                    mock_chroot.assert_not_called()

    def test_empty_locale_is_refused(self):
        with patch("installer.backend.hooks.run_chroot") as mock_chroot:
            with self.assertRaises(ValueError):
                generate_locale(self.tmp, "")
            mock_chroot.assert_not_called()

    @patch("installer.backend.hooks.run_chroot")
    def test_valid_locales_still_pass_the_gate(self, mock_chroot):
        # The allowlist must not block any real glibc locale shape, incl. an
        # @modifier locale — a regression here would break legitimate installs.
        mock_chroot.return_value = (0, "", "")
        for good in ("en_US.UTF-8", "fr_FR.UTF-8", "de_DE.ISO-8859-1",
                     "ca_ES@valencia", "sr_RS.UTF-8@latin", "C", "POSIX"):
            with self.subTest(locale=good):
                generate_locale(self.tmp, good)  # must not raise

    @patch("installer.backend.hooks.run_chroot")
    def test_benign_malformed_names_still_pass_the_gate(self, mock_chroot):
        # ".UTF-8" / "fr_FR." carry no shell metacharacters, so they clear the
        # security gate and keep their prior benign behavior (conf written,
        # localedef skipped on the empty base/encoding) — the fix targets
        # injection, not these.
        for benign in (".UTF-8", "fr_FR."):
            with self.subTest(locale=benign):
                generate_locale(self.tmp, benign)  # must not raise
                self.assertEqual(self._locale_conf().read_text(),
                                 f"LANG={benign}\n")
        mock_chroot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
