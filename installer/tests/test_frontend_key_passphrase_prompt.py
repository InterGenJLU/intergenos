# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The installer asks the owner to set the signing key's passphrase, in its own step.

Three secrets are set during an install and they are not the same thing:

  - the disk passphrase, typed at every boot, which protects what is on the disk;
  - the firmware enrollment password, typed once at the firmware's own key manager
    to confirm that this machine's key may be trusted;
  - the signing key's passphrase, which protects the key that signs this machine's
    boot images and driver modules for the life of the machine.

The third is new. It gets its own step and its own words, because a person who is
told "another password" three times learns nothing about what any of them does.

The one reuse that is refused is the disk passphrase: the two protect different
things and are typed in different places, and someone who watches the disk
passphrase typed at boot must not thereby be able to sign a boot image this
machine will trust. Reusing the enrollment password is not refused — that rule was
not asked for and inventing it would be a rule nobody decided.
"""

import unittest

from installer.frontend import tui


class TestThePromptSaysWhatItGuards(unittest.TestCase):

    def setUp(self):
        self.text = tui._key_passphrase_prompt_text()

    def test_it_says_what_the_key_signs(self):
        lowered = self.text.lower()
        self.assertTrue("boot" in lowered and "sign" in lowered, self.text)

    def test_it_says_when_it_will_be_asked_for(self):
        lowered = self.text.lower()
        self.assertTrue(
            "kernel" in lowered or "update" in lowered,
            f"the prompt does not tell the person they will be asked again "
            f"later, which is the part that decides whether they write it "
            f"down: {self.text}")

    def test_it_does_not_call_itself_the_disk_passphrase(self):
        self.assertNotIn("disk-encryption passphrase", self.text)


class TestCollectingIt(unittest.TestCase):
    """The collection loop, with the dialog replaced by a list of answers."""

    def collect(self, answers, disk_passphrase=None):
        self.said = []
        pending = list(answers)

        def ask(_title, _prompt):
            if not pending:
                return 1, ""       # the person cancelled
            return 0, pending.pop(0)

        def notify(message):
            self.said.append(message)

        return tui._collect_key_passphrase(
            ask, notify, disk_passphrase=disk_passphrase)

    def test_a_matching_pair_is_accepted(self):
        self.assertEqual(self.collect(["signing-pass-1", "signing-pass-1"]),
                         "signing-pass-1")

    def test_a_mismatch_is_said_and_asked_again(self):
        got = self.collect(["signing-pass-1", "typed-wrong",
                            "signing-pass-1", "signing-pass-1"])
        self.assertEqual(got, "signing-pass-1")
        self.assertTrue(any("not match" in m for m in self.said), self.said)

    def test_too_short_is_refused_with_the_reason(self):
        got = self.collect(["short", "short", "long-enough-1", "long-enough-1"])
        self.assertEqual(got, "long-enough-1")
        self.assertTrue(any("8" in m for m in self.said), self.said)

    def test_the_disk_passphrase_is_refused(self):
        got = self.collect(["same-as-disk-1", "same-as-disk-1",
                            "a-different-one-1", "a-different-one-1"],
                           disk_passphrase="same-as-disk-1")
        self.assertEqual(got, "a-different-one-1")
        self.assertTrue(any("disk" in m.lower() for m in self.said), self.said)

    def test_cancelling_returns_none(self):
        self.assertIsNone(self.collect([]))

    def test_an_empty_entry_is_not_accepted_as_a_passphrase(self):
        """Empty means "skip" for the enrollment password. Not for this one."""
        got = self.collect(["", "", "proper-pass-1", "proper-pass-1"])
        self.assertEqual(got, "proper-pass-1")


class TestItReachesTheBackend(unittest.TestCase):

    def test_the_install_dict_carries_it_under_the_name_the_backend_reads(self):
        from installer.backend import install as backend_install
        import inspect
        source = inspect.getsource(backend_install)
        self.assertIn("mok_key_passphrase", source,
                      "the backend does not read the key the frontend writes")


if __name__ == "__main__":
    unittest.main()
