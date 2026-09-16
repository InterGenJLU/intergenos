"""R001.3 gating row 10 — a machine's trusted key set can shrink, deliberately.

Every install enrols a fresh machine owner key and nothing ever retired the old
ones, so a reinstalled machine accumulates a trusted key per install; one fleet
machine was measured carrying seven of this project's keys, six of them for
disks that had since been installed over. Nothing in the boot path checks the
dates in those certificates, so the only thing that ever removes one is a
person choosing to.

What these tests hold:

(a) the key this install just generated is never offered for removal, and the
    exclusion is by the same fingerprint the firmware itself prints;
(b) certificates that are not this project's — a vendor authority — are left
    alone;
(c) a store that cannot be read is never reported as a store with nothing in
    it;
(d) nothing is ever removed without an explicit choice, and the removal goes
    through the firmware's own manager;
(e) the answer is recorded either way, including when the person keeps
    everything.
"""

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.backend import mok  # noqa: E402


def _self_signed_der(common_name, days="3650"):
    """A throwaway self-signed certificate with the given CN, DER bytes."""
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(
        ["openssl", "req", "-new", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(tmp / "k.pem"), "-out", str(tmp / "c.der"),
         "-outform", "DER", "-days", days, "-subj", f"/CN={common_name}/"],
        check=True, capture_output=True)
    return (tmp / "c.der").read_bytes()


class PriorKeySelection(unittest.TestCase):
    """Which enrolled certificates the offer may name, and which it may not."""

    @classmethod
    def setUpClass(cls):
        cls.current = _self_signed_der(mok.OWNER_KEY_COMMON_NAME)
        cls.older_one = _self_signed_der(mok.OWNER_KEY_COMMON_NAME)
        cls.older_two = _self_signed_der(mok.OWNER_KEY_COMMON_NAME)
        cls.vendor = _self_signed_der("Some Vendor Secure Boot CA")

    def test_the_fingerprint_is_the_one_the_firmware_prints(self):
        """SHA-1 over the certificate bytes — the value in the firmware's own
        listing. A differently computed fingerprint never matches it, which is
        how a live key gets mistaken for a prior one."""
        self.assertEqual(mok.sha1_fingerprint(self.current),
                         hashlib.sha1(self.current).hexdigest())

    def test_the_key_this_install_generated_is_never_offered(self):
        prior = mok.prior_owner_keys(
            [self.current, self.older_one, self.older_two], self.current)
        offered = {p["sha1"] for p in prior}
        self.assertNotIn(mok.sha1_fingerprint(self.current), offered)
        self.assertEqual(offered, {mok.sha1_fingerprint(self.older_one),
                                   mok.sha1_fingerprint(self.older_two)})

    def test_a_certificate_that_is_not_ours_is_left_alone(self):
        prior = mok.prior_owner_keys([self.vendor, self.older_one], self.current)
        self.assertEqual([p["sha1"] for p in prior],
                         [mok.sha1_fingerprint(self.older_one)])

    def test_the_offer_is_empty_when_only_the_current_key_is_enrolled(self):
        self.assertEqual(mok.prior_owner_keys([self.current], self.current), [])

    def test_identity_carries_what_a_person_needs_to_recognise_a_key(self):
        identity = mok.certificate_identity(self.older_one)
        self.assertEqual(identity["common_name"], mok.OWNER_KEY_COMMON_NAME)
        self.assertEqual(identity["sha1"], mok.sha1_fingerprint(self.older_one))
        self.assertTrue(identity["created"], "a creation date must be shown")
        self.assertTrue(identity["expires"], "the validity end must be shown")

    def test_bytes_that_are_not_a_certificate_are_skipped_not_guessed_at(self):
        self.assertIsNone(mok.certificate_identity(b"not a certificate"))
        prior = mok.prior_owner_keys([b"rubbish", self.older_one], self.current)
        self.assertEqual(len(prior), 1)


class ReadingTheStore(unittest.TestCase):
    """An unreadable store and an empty one are different answers."""

    def setUp(self):
        self.target = Path(tempfile.mkdtemp())

    def test_a_failed_export_reports_unreadable_rather_than_empty(self):
        with patch.object(mok, "mount_efivars"), \
             patch.object(mok, "unmount_efivars"):
            result = mok.export_enrolled_certificates(
                self.target, runner=lambda *a, **k: (1, "", "mokutil: failed"))
        self.assertIsNone(result, "a failure must not read as 'no prior keys'")

    def test_a_successful_export_returns_what_the_firmware_wrote(self):
        der_one = _self_signed_der(mok.OWNER_KEY_COMMON_NAME)
        der_two = _self_signed_der("Some Vendor Secure Boot CA")
        export_dir = self.target / mok._EXPORT_DIR.lstrip("/")

        def runner(target, command):
            self.assertIn("mokutil --export", command)
            export_dir.mkdir(parents=True, exist_ok=True)
            (export_dir / "MOK-0001.der").write_bytes(der_two)
            (export_dir / "MOK-0002.der").write_bytes(der_one)
            return 0, "", ""

        with patch.object(mok, "mount_efivars"), \
             patch.object(mok, "unmount_efivars"):
            result = mok.export_enrolled_certificates(self.target, runner=runner)
        self.assertEqual(result, [der_two, der_one])

    def test_the_firmware_variables_are_mounted_for_the_read_and_released(self):
        with patch.object(mok, "mount_efivars") as mount, \
             patch.object(mok, "unmount_efivars") as unmount:
            mok.export_enrolled_certificates(
                self.target, runner=lambda *a, **k: (1, "", ""))
        mount.assert_called_once()
        unmount.assert_called_once()


class QueueingARemoval(unittest.TestCase):
    """Nothing is removed without a choice, and the firmware confirms it."""

    def setUp(self):
        self.target = Path(tempfile.mkdtemp())
        self.identity = mok.certificate_identity(
            _self_signed_der(mok.OWNER_KEY_COMMON_NAME))

    def test_keeping_everything_never_reaches_the_removal_path(self):
        with self.assertRaises(ValueError):
            mok.queue_owner_key_removal(self.target, [], "a-password-1234")

    def test_a_removal_without_the_password_is_refused(self):
        with self.assertRaises(ValueError):
            mok.queue_owner_key_removal(self.target, [self.identity], "")

    def test_the_chosen_keys_are_named_to_the_firmware_manager(self):
        seen = {}

        def runner(target, command, stdin_data):
            seen["command"] = command
            seen["stdin"] = stdin_data
            return 0, "", ""

        with patch.object(mok, "mount_efivars"), \
             patch.object(mok, "unmount_efivars"), \
             patch.object(mok.trace, "trace_event") as traced:
            mok.queue_owner_key_removal(
                self.target, [self.identity], "a-password-1234", runner=runner)

        self.assertIn("mokutil --delete", seen["command"])
        self.assertIn(self.identity["sha1"], seen["command"])
        self.assertEqual(seen["stdin"], "a-password-1234\na-password-1234\n")
        written = (self.target / mok._EXPORT_DIR.lstrip("/")
                   / f"retire-{self.identity['sha1']}.der").read_bytes()
        self.assertEqual(written, self.identity["der"],
                         "the file named to the firmware must be the key shown")
        traced.assert_called_once()
        self.assertEqual(traced.call_args.kwargs["count"], 1)

    def test_a_failed_request_is_raised_not_swallowed(self):
        with patch.object(mok, "mount_efivars"), \
             patch.object(mok, "unmount_efivars"), \
             patch.object(mok.trace, "trace_event"):
            with self.assertRaises(RuntimeError):
                mok.queue_owner_key_removal(
                    self.target, [self.identity], "a-password-1234",
                    runner=lambda *a, **k: (1, "", "mokutil: refused"))


class RecordingTheAnswer(unittest.TestCase):
    """Whichever way the person answers, the install says so."""

    def test_a_decline_is_recorded_as_deliberately_as_a_removal(self):
        with patch.object(mok.trace, "trace_event") as traced:
            mok.record_owner_key_decision(kept=[1, 2, 3], removed=[])
        fields = traced.call_args.kwargs
        self.assertEqual(fields["offered"], 3)
        self.assertEqual(fields["kept"], 3)
        self.assertEqual(fields["removed"], 0)
        self.assertTrue(fields["declined"])

    def test_a_removal_is_recorded_with_its_counts(self):
        with patch.object(mok.trace, "trace_event") as traced:
            mok.record_owner_key_decision(kept=[1], removed=[2, 3])
        fields = traced.call_args.kwargs
        self.assertEqual((fields["offered"], fields["kept"], fields["removed"]),
                         (3, 1, 2))
        self.assertFalse(fields["declined"])


if __name__ == "__main__":
    unittest.main()
