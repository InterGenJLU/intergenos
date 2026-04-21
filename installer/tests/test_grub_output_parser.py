"""Unit tests for grub_output_parser — fixture-driven.

All tests use inline fixture strings approximating real grub+kernel
boot output across known outcomes. Runs anywhere.

When Phase A-2 empirical runs land, any captured output that the
parser classifies as `unknown` should be added here as a new fixture
under the right outcome — the parser patterns are intentionally loose
now and can be tightened against real captures.
"""

from __future__ import annotations

import textwrap
import unittest

from installer.tests import grub_output_parser as gop


# --- Fixtures (representative shapes) -------------------------------------


BOOT_SUCCESS = textwrap.dedent("""\
    Booting from Hard Disk...

    Welcome to GRUB!

    Loading Linux 6.18.10-intergenos ...
    Loading initial ramdisk ...
    [    0.000000] Linux version 6.18.10-intergenos (root@build-vm) (gcc 14.2.0) #1 SMP PREEMPT_DYNAMIC Mon Apr 21 00:00:00 UTC 2026
    [    0.000000] Command line: BOOT_IMAGE=/boot/vmlinuz-6.18.10-intergenos root=UUID=abc-def ro quiet
    [    0.000000] KERNEL supported cpus:
""")

BOOT_SIG_MISSING_CANONICAL = textwrap.dedent("""\
    Booting from Hard Disk...

    Welcome to GRUB!

    Loading Linux 6.18.10-intergenos ...
    error: /boot/vmlinuz-6.18.10-intergenos has no signature.
    Press any key to continue...
""")

BOOT_SIG_MISSING_BACKTICK = textwrap.dedent("""\
    Welcome to GRUB!

    Loading Linux 6.18.10-intergenos ...
    error: file `/boot/vmlinuz-6.18.10-intergenos' has no signature.
    Press any key to continue...
""")

BOOT_SIG_MISSING_VERIFICATION_REQUESTED = textwrap.dedent("""\
    Welcome to GRUB!

    Loading Linux 6.18.10-intergenos ...
    error: verification requested but file /boot/vmlinuz-6.18.10-intergenos is not signed.
    Press any key to continue...
""")

BOOT_SIG_VERIFY_FAIL_BAD_SIG = textwrap.dedent("""\
    Welcome to GRUB!

    Loading Linux 6.18.10-intergenos ...
    error: bad signature.
    Press any key to continue...
""")

BOOT_SIG_VERIFY_FAIL_INVALID = textwrap.dedent("""\
    Welcome to GRUB!

    Loading Linux 6.18.10-intergenos ...
    error: signature is invalid.
    Press any key to continue...
""")

BOOT_SIG_VERIFY_FAIL_PUBKEY_NOT_FOUND = textwrap.dedent("""\
    Welcome to GRUB!

    Loading Linux 6.18.10-intergenos ...
    error: public key 0x1234abcd not found.
    Press any key to continue...
""")

BOOT_FILE_NOT_FOUND = textwrap.dedent("""\
    Welcome to GRUB!

    Loading Linux 6.18.10-intergenos ...
    error: file `/boot/vmlinuz-missing' not found.
    Press any key to continue...
""")

BOOT_FILE_NOT_FOUND_NO_SUCH = textwrap.dedent("""\
    Welcome to GRUB!

    error: no such file.
    Press any key to continue...
""")

BOOT_UNKNOWN_GARBAGE = """\
This is some random output that doesn't match any grub pattern.
Nothing about signatures, files, or Linux.
"""

BOOT_EMPTY = ""


# --- Kernel-loaded cases --------------------------------------------------


class TestKernelLoaded(unittest.TestCase):
    def test_clean_boot(self):
        r = gop.parse_grub_boot_output(BOOT_SUCCESS)
        self.assertEqual(r.boot_outcome, "kernel_loaded")
        self.assertIsNone(r.captured_error_line)
        self.assertIsNotNone(r.detected_kernel_path)

    def test_kernel_path_extracted_from_success(self):
        """On successful boot the kernel command-line exposes the full
        path (via BOOT_IMAGE=...) — that's more specific than the
        `Loading Linux <version>` line's version-only, so the path
        lookup prefers it."""
        r = gop.parse_grub_boot_output(BOOT_SUCCESS)
        self.assertEqual(
            r.detected_kernel_path,
            "/boot/vmlinuz-6.18.10-intergenos",
        )

    def test_kernel_banner_wins_over_earlier_warning(self):
        """If kernel banner is present, handoff happened — no earlier error
        line should classify this as a failure."""
        text = (
            "warning: some irrelevant message\n"
            "[    0.000000] Linux version 6.18.10 (...) #1\n"
        )
        r = gop.parse_grub_boot_output(text)
        self.assertEqual(r.boot_outcome, "kernel_loaded")


# --- Signature-missing cases ----------------------------------------------


class TestSignatureMissing(unittest.TestCase):
    def test_canonical_no_signature_message(self):
        r = gop.parse_grub_boot_output(BOOT_SIG_MISSING_CANONICAL)
        self.assertEqual(r.boot_outcome, "signature_missing")
        self.assertIn("has no signature", r.captured_error_line)
        self.assertEqual(
            r.detected_kernel_path,
            "/boot/vmlinuz-6.18.10-intergenos",
        )

    def test_backtick_quoted_path_variant(self):
        r = gop.parse_grub_boot_output(BOOT_SIG_MISSING_BACKTICK)
        self.assertEqual(r.boot_outcome, "signature_missing")
        self.assertIn("has no signature", r.captured_error_line)

    def test_verification_requested_not_signed_variant(self):
        r = gop.parse_grub_boot_output(BOOT_SIG_MISSING_VERIFICATION_REQUESTED)
        self.assertEqual(r.boot_outcome, "signature_missing")
        self.assertIn("not signed", r.captured_error_line)


# --- Signature-verify-failed cases ----------------------------------------


class TestSignatureVerifyFailed(unittest.TestCase):
    def test_bad_signature_message(self):
        r = gop.parse_grub_boot_output(BOOT_SIG_VERIFY_FAIL_BAD_SIG)
        self.assertEqual(r.boot_outcome, "signature_verify_failed")
        self.assertIn("bad signature", r.captured_error_line)

    def test_signature_is_invalid(self):
        r = gop.parse_grub_boot_output(BOOT_SIG_VERIFY_FAIL_INVALID)
        self.assertEqual(r.boot_outcome, "signature_verify_failed")

    def test_public_key_not_found(self):
        """enforce-rejects-because-key-not-loaded is a verify-fail semantically.

        Even though the signature block is technically readable, if the
        key to verify it isn't in any loaded keyring, the verification
        step fails. Category: verify_fail.
        """
        r = gop.parse_grub_boot_output(BOOT_SIG_VERIFY_FAIL_PUBKEY_NOT_FOUND)
        self.assertEqual(r.boot_outcome, "signature_verify_failed")
        self.assertIn("public key", r.captured_error_line)


# --- File-not-found cases -------------------------------------------------


class TestFileNotFound(unittest.TestCase):
    def test_canonical_file_not_found(self):
        r = gop.parse_grub_boot_output(BOOT_FILE_NOT_FOUND)
        self.assertEqual(r.boot_outcome, "file_not_found")
        self.assertIn("not found", r.captured_error_line)

    def test_no_such_file_variant(self):
        r = gop.parse_grub_boot_output(BOOT_FILE_NOT_FOUND_NO_SUCH)
        self.assertEqual(r.boot_outcome, "file_not_found")


# --- Unknown / edge cases -------------------------------------------------


class TestUnknownAndEdgeCases(unittest.TestCase):
    def test_garbage_input(self):
        r = gop.parse_grub_boot_output(BOOT_UNKNOWN_GARBAGE)
        self.assertEqual(r.boot_outcome, "unknown")
        self.assertIsNone(r.captured_error_line)

    def test_empty_input(self):
        r = gop.parse_grub_boot_output(BOOT_EMPTY)
        self.assertEqual(r.boot_outcome, "unknown")
        self.assertIsNone(r.detected_kernel_path)
        self.assertEqual(r.raw_tail, "")

    def test_raw_tail_captured(self):
        r = gop.parse_grub_boot_output(BOOT_SIG_MISSING_CANONICAL)
        # The tail should include the last few lines including "Press any key"
        self.assertIn("Press any key", r.raw_tail)

    def test_to_dict_round_trip(self):
        import json
        r = gop.parse_grub_boot_output(BOOT_SIG_MISSING_CANONICAL)
        reloaded = json.loads(json.dumps(r.to_dict()))
        self.assertEqual(reloaded["boot_outcome"], "signature_missing")
        self.assertIn("raw_tail", reloaded)


# --- Priority ordering ----------------------------------------------------


class TestPriorityOrdering(unittest.TestCase):
    """Tests that the parser picks the correct category when multiple
    patterns could match or when two error lines appear."""

    def test_kernel_banner_beats_earlier_error(self):
        """If a recovery flow re-tried the boot and succeeded, the
        kernel banner at the bottom should win over an earlier error."""
        text = (
            BOOT_SIG_MISSING_CANONICAL
            + "(user pressed key, retried with check_signatures=no)\n"
            "Loading Linux 6.18.10-intergenos ...\n"
            "[    0.000000] Linux version 6.18.10-intergenos...\n"
        )
        r = gop.parse_grub_boot_output(text)
        self.assertEqual(r.boot_outcome, "kernel_loaded")

    def test_sig_missing_beats_generic_verify_fail(self):
        """When both patterns match literally, sig_missing wins because
        it's the more specific (and load-bearing) category."""
        text = (
            "error: /boot/vmlinuz-foo has no signature.\n"
            "error: bad signature.\n"
        )
        r = gop.parse_grub_boot_output(text)
        self.assertEqual(r.boot_outcome, "signature_missing")


if __name__ == "__main__":
    unittest.main()
