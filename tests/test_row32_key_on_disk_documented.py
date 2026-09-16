# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""R001.3 gating row 32 — the signing key's location is a stated trade.

The key that signs this machine's kernels lives on the machine, without a
passphrase, because every kernel and driver update has to sign something
with it and no person is present when that happens. The row's instruction
is to KEEP that arrangement and to STATE the trade in the documentation
rather than leave a reader to discover it.

A documentation claim that nothing checks goes stale silently, so these
tests hold the documents against the code they describe: the path, the
mode, the absence of a passphrase and the reason all come from
installer/backend/mok.py, and a change there that the documents do not
follow fails here.
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

    def test_the_private_key_is_generated_without_a_passphrase(self):
        self.assertIn('"-nodes"', self.src)

    def test_the_private_key_is_readable_only_by_the_administrator(self):
        self.assertIn("os.chmod(key_fspath, 0o600)", self.src)


class TestTheSecureBootGuideStatesTheTrade(unittest.TestCase):

    def setUp(self):
        self.doc = SECURE_BOOT_DOC.read_text()

    def test_it_has_a_section_about_where_the_key_lives(self):
        self.assertRegex(self.doc, r"(?m)^## .*key lives on the disk")

    def test_it_names_the_file_and_that_it_has_no_passphrase(self):
        self.assertIn("/var/lib/intergen/mok/mok.key", self.doc)
        self.assertRegex(self.doc, r"no passphrase|without a passphrase")

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

    def _section(self):
        match = re.search(r"(?ms)^## .*key lives on the disk.*?(?=^## |\Z)", self.doc)
        self.assertIsNotNone(match, "the section is missing")
        body = match.group(0)
        self.assertGreater(len(body.splitlines()), 8,
                           "a heading with no substance under it is not a statement")
        return body


class TestTheDefaultsGuideCarriesTheSameFact(unittest.TestCase):

    def setUp(self):
        self.doc = DEFAULTS_DOC.read_text()

    def test_it_names_the_key_on_disk_and_points_at_the_longer_account(self):
        self.assertIn("/var/lib/intergen/mok/mok.key", self.doc)
        self.assertIn("secure-boot-and-mok.md", self.doc)

    def test_it_does_not_claim_the_key_is_protected_by_a_passphrase(self):
        self.assertNotRegex(
            self.doc, r"(?i)signing key is (?:password|passphrase)-protected")


if __name__ == "__main__":
    unittest.main()
