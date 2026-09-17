# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The signing key's arrangement is a stated trade — and the trade changed.

R001.3 gating row 32 asked for the arrangement of the day to be KEPT and STATED
rather than left for a reader to discover: the key that signs this machine's
kernels lives on the machine, without a passphrase, because every kernel and
driver update has to sign something with it and no person is present when that
happens.

Decided 2026-09-17, that reasoning was reversed. Unattended signing meant any
program running as root could sign a boot image the firmware trusts, so the boot
chain resisted an attacker without root and no one with it. The key is now
encrypted under a passphrase the machine owner sets, and every signing step asks
for it. The key still lives on the machine — that half of row 32 stands — and
the trade is still stated rather than implicit, which is what these tests keep.

This file was rewritten rather than deleted, and the assertions were INVERTED
rather than relaxed. A test that merely stopped checking would have let the
documents drift back to describing an unprotected key, which is precisely the
stale claim row 32 existed to prevent.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURE_BOOT_DOC = REPO_ROOT / "docs" / "users" / "secure-boot-and-mok.md"
DEFAULTS_DOC = REPO_ROOT / "docs" / "users" / "security-defaults.md"
MOK_SOURCE = REPO_ROOT / "installer" / "backend" / "mok.py"


class TestTheCodeStillMatchesWhatTheDocumentsSay(unittest.TestCase):
    """The documents state these as facts; if they stop being facts, fail."""

    def setUp(self):
        self.src = MOK_SOURCE.read_text()

    def test_the_key_directory_is_the_one_the_documents_name(self):
        self.assertIn('MOK_DIR = "/var/lib/intergen/mok"', self.src)

    def test_the_private_key_is_generated_with_a_passphrase(self):
        """The inverted assertion: -nodes is what made the key plain."""
        self.assertNotIn('"-nodes"', self.src,
                         "openssl -nodes writes an unencrypted private key")
        self.assertIn('"-passout"', self.src)

    def test_the_passphrase_is_required_rather_than_optional(self):
        self.assertIn("validate_mok_key_passphrase", self.src)

    def test_the_generated_key_is_read_back_before_the_install_goes_on(self):
        self.assertIn("verify_key_is_encrypted", self.src)

    def test_the_read_back_uses_an_empty_passphrase_as_its_control(self):
        """A check that only proves the right passphrase works passes on a
        plain key too, because a plain key opens under any passphrase."""
        self.assertIn('"-passin", "pass:"', self.src)

    def test_the_private_key_is_readable_only_by_the_administrator(self):
        self.assertIn("os.chmod(key_fspath, 0o600)", self.src)

    def test_the_protection_state_is_recorded_where_a_person_can_read_it(self):
        self.assertIn("mok-key-protection", self.src)


class TestTheSecureBootGuideStatesTheTrade(unittest.TestCase):

    def setUp(self):
        self.doc = SECURE_BOOT_DOC.read_text()

    def test_it_has_a_section_about_where_the_key_lives(self):
        self.assertRegex(self.doc, r"(?m)^## .*key lives on the disk")

    def test_it_names_the_file_and_that_it_has_a_passphrase(self):
        self.assertIn("/var/lib/intergen/mok/mok.key", self.doc)
        section = self._section()
        self.assertRegex(section, r"(?i)encrypted with a passphrase")

    def test_it_says_when_the_person_will_be_asked_for_it(self):
        section = self._section()
        self.assertRegex(section, r"(?i)kernel update")

    def test_it_says_what_happens_when_the_passphrase_is_not_given(self):
        section = self._section()
        self.assertIn("NOT BOOTABLE UNTIL SIGNED", section)
        self.assertRegex(section, r"(?i)previous release's signed boot image")

    def test_it_tells_a_reader_with_an_older_machine_what_happens_to_them(self):
        section = self._section()
        self.assertRegex(section, r"(?i)installed before")

    def test_it_says_what_someone_who_takes_the_disk_gains(self):
        section = self._section()
        self.assertRegex(section, r"(?i)takes the disk|removes the disk|has the disk")
        self.assertRegex(section, r"(?i)full disk encryption|encrypted|LUKS")

    def test_it_names_the_alternative_and_what_it_would_cost(self):
        section = self._section()
        self.assertRegex(section, r"(?i)hardware|smart card|TPM|external")
        self.assertRegex(section, r"(?i)every kernel|each kernel|kernel update")

    def test_it_says_what_would_change_the_trade(self):
        section = self._section()
        self.assertRegex(section, r"(?i)would change|changes this|reconsider")

    def test_it_states_the_cost_rather_than_hiding_it(self):
        section = self._section()
        self.assertRegex(section, r"(?i)one passphrase prompt")

    def _section(self):
        """The section about the key, from its heading to the next one."""
        match = re.search(
            r"(?ms)^## .*key lives on the disk.*?(?=^## )", self.doc)
        self.assertIsNotNone(match, "the section is not there to read")
        return match.group(0)


class TestTheDefaultsListStatesIt(unittest.TestCase):

    def setUp(self):
        self.doc = DEFAULTS_DOC.read_text()

    def test_the_what_we_dont_do_list_names_the_signing_key(self):
        self.assertRegex(self.doc, r"(?i)signing key")

    def test_it_says_nothing_signs_without_asking(self):
        self.assertRegex(self.doc, r"(?i)no unattended signing|without asking")

    def test_it_points_at_the_longer_explanation(self):
        self.assertIn("secure-boot-and-mok.md", self.doc)


if __name__ == "__main__":
    unittest.main()
