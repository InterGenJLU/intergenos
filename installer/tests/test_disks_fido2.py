"""Tests for installer/backend/disks.py:_fido2_assert_hmac — D-001 FIDO2 fix.

Covers the libfido2-canonical output parsing fix per windows-docs-
coordinator 2026-05-19T01:35:56Z FDE self-audit + verbatim libfido2
fido2-assert(1) man-page re-fetch.

Output format (per libfido2 man page): 7 lines, NO prefixes, all base64
except line 2 (RP id UTF-8). Line 6 (0-indexed 5) = hmac secret (base64).

Earlier defective parsing looked for `hmac-secret:` PREFIX line + decoded
as hex; both wrong. Tests below assert the libfido2 shape + the failure
paths (short output / empty line 6 / invalid base64 / wrong-length HMAC).
"""

import base64
import unittest
from unittest.mock import patch, MagicMock

from installer.backend.disks import _fido2_assert_hmac


class TestFido2AssertHmacOutputParsing(unittest.TestCase):
    def _make_completed_process(self, stdout, returncode=0):
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout
        proc.stderr = ""
        return proc

    def _build_libfido2_output(self, hmac_bytes):
        """Construct a synthetic 7-line fido2-assert output matching the
        libfido2 man-page format. Lines 1/3/4/5/7 use placeholder base64
        (1-byte filler 'A'); line 2 is the RP id (UTF-8); line 6 is the
        hmac secret base64-encoded."""
        return "\n".join([
            "AA==",                              # 1: cdh (base64 placeholder)
            "intergenos",                        # 2: RP id (UTF-8)
            "AA==",                              # 3: authdata (base64 placeholder)
            "AA==",                              # 4: assertion sig (base64 placeholder)
            "AA==",                              # 5: user id (base64 placeholder)
            base64.b64encode(hmac_bytes).decode("ascii"),  # 6: HMAC SECRET
            "AA==",                              # 7: largeBlobKey (base64 placeholder)
        ])

    @patch("installer.backend.disks.subprocess.run")
    def test_canonical_libfido2_output_returns_hmac_bytes(self, mock_run):
        # Synthetic 32-byte HMAC-SHA256 output.
        expected_hmac = b"\xab" * 32
        stdout = self._build_libfido2_output(expected_hmac)
        mock_run.return_value = self._make_completed_process(stdout)
        result = _fido2_assert_hmac(
            "/dev/hidraw0", b"fake-cred-id-bytes", b"fake-nonce-bytes-32-chars-padding"
        )
        self.assertEqual(result, expected_hmac)
        self.assertEqual(len(result), 32)

    @patch("installer.backend.disks.subprocess.run")
    def test_subprocess_nonzero_raises(self, mock_run):
        mock_run.return_value = self._make_completed_process(
            "", returncode=1
        )
        mock_run.return_value.stderr = "fido2-assert: token error"
        with self.assertRaises(RuntimeError) as ctx:
            _fido2_assert_hmac("/dev/hidraw0", b"cred", b"nonce")
        self.assertIn("fido2-assert -G failed", str(ctx.exception))
        self.assertIn("token error", str(ctx.exception))

    @patch("installer.backend.disks.subprocess.run")
    def test_short_output_under_6_lines_raises(self, mock_run):
        # Only 5 lines — --hmac-secret extension didn't fire.
        stdout = "AA==\nintergenos\nAA==\nAA==\nAA=="
        mock_run.return_value = self._make_completed_process(stdout)
        with self.assertRaises(RuntimeError) as ctx:
            _fido2_assert_hmac("/dev/hidraw0", b"cred", b"nonce")
        msg = str(ctx.exception)
        self.assertIn("5 lines", msg)
        self.assertIn("expected >=6", msg)
        self.assertIn("--hmac-secret extension may not have fired", msg)

    @patch("installer.backend.disks.subprocess.run")
    def test_empty_hmac_secret_line_raises(self, mock_run):
        # Line 6 (idx 5) is empty/whitespace.
        stdout = "AA==\nintergenos\nAA==\nAA==\nAA==\n   \nAA=="
        mock_run.return_value = self._make_completed_process(stdout)
        with self.assertRaises(RuntimeError) as ctx:
            _fido2_assert_hmac("/dev/hidraw0", b"cred", b"nonce")
        self.assertIn("hmac-secret line (idx 5) is empty", str(ctx.exception))

    @patch("installer.backend.disks.subprocess.run")
    def test_invalid_base64_hmac_raises(self, mock_run):
        # Line 6 contains characters that cannot decode as base64 even with
        # validate=False. Python's base64.b64decode is permissive — it
        # ignores non-base64 characters silently when validate=False (the
        # default). So we need to trigger an explicit failure: pass a
        # string that has incorrect padding/length to cause binascii.Error.
        stdout = "AA==\nintergenos\nAA==\nAA==\nAA==\n!@#$%^&*\nAA=="
        mock_run.return_value = self._make_completed_process(stdout)
        # base64.b64decode("!@#$%^&*") raises binascii.Error due to bad
        # input length (Python 3.14 strict mode varies; this test asserts
        # the failure path even though the specific exception type may
        # vary across Python releases — the RuntimeError wraps either way).
        try:
            _fido2_assert_hmac("/dev/hidraw0", b"cred", b"nonce")
            # If b64decode was permissive, the result will be wrong-length;
            # the wrong-length check downstream should still raise.
            self.fail("expected RuntimeError")
        except RuntimeError as e:
            # Either "not valid base64" or "unexpected length" is acceptable
            # — both are correct failure surfaces for malformed input.
            msg = str(e)
            self.assertTrue(
                "not valid base64" in msg or "unexpected length" in msg,
                f"unexpected error message: {msg!r}",
            )

    @patch("installer.backend.disks.subprocess.run")
    def test_wrong_length_hmac_raises(self, mock_run):
        # Line 6 decodes to 16 bytes (HMAC-SHA128 width — wrong).
        short_hmac = b"\xcd" * 16
        stdout = self._build_libfido2_output(short_hmac)
        # Override line 6 with the 16-byte encoding to actually test the
        # length-check branch (the 32-byte default test was for the happy
        # path; this one stays in the wrong-length branch).
        mock_run.return_value = self._make_completed_process(stdout)
        with self.assertRaises(RuntimeError) as ctx:
            _fido2_assert_hmac("/dev/hidraw0", b"cred", b"nonce")
        msg = str(ctx.exception)
        self.assertIn("unexpected length 16", msg)
        self.assertIn("expected 32 bytes", msg)

    @patch("installer.backend.disks.subprocess.run")
    def test_stdin_format_4_lines_with_correct_values(self, mock_run):
        # Verify the stdin format matches libfido2 man-page: 4 lines
        # cdh + RP_id + cred_id_b64 + nonce_b64.
        mock_run.return_value = self._make_completed_process(
            self._build_libfido2_output(b"\x00" * 32)
        )
        _fido2_assert_hmac("/dev/hidraw0", b"raw-cred-id", b"raw-nonce-bytes")
        # subprocess.run kwargs: input=stdin_text
        kwargs = mock_run.call_args.kwargs
        stdin_text = kwargs.get("input", "")
        lines = stdin_text.split("\n")
        # 4 content lines + trailing empty (from final \n).
        self.assertEqual(len(lines), 5)
        # Line 2 = RP id literal "intergenos" (matches FIDO2_RP_ID).
        self.assertEqual(lines[1], "intergenos")
        # Line 3 = base64-encoded cred_id; reverse-check.
        self.assertEqual(base64.b64decode(lines[2]), b"raw-cred-id")
        # Line 4 = base64-encoded nonce.
        self.assertEqual(base64.b64decode(lines[3]), b"raw-nonce-bytes")
        # Line 1 = base64-encoded 32-byte client-data-hash (length check
        # only — value is random per-call).
        cdh_decoded = base64.b64decode(lines[0])
        self.assertEqual(len(cdh_decoded), 32)


if __name__ == "__main__":
    unittest.main()
