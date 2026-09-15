"""R001.3 gating row 37 — no one loses a Machine Owner Key enrolment the way
the hub did on 2026-09-05 (an unseen 10-second MokManager window dropped the
queued request; recovery needed a second machine because nothing stageable
was on the boot partition).

(a) the installer stages the certificate on the EFI system partition (where
    "Enroll key from disk" can read it) and at a public root path (where the
    first-login check can read it);
(c) every pre-install and post-install surface names the window and the
    from-disk path with one shared wording;
companion: the firmware's signature database is read and stated in plain
    language (which Microsoft signing authorities the machine trusts).
"""

import struct
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from installer.backend import bootloader, mok, mok_guidance, secureboot  # noqa: E402
from installer.frontend import tui  # noqa: E402
from installer.frontend.gui.screens import done as done_screen  # noqa: E402


def _self_signed_der(common_name):
    """A throwaway self-signed certificate with the given CN, DER bytes."""
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(
        ["openssl", "req", "-new", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(tmp / "k.pem"), "-out", str(tmp / "c.der"),
         "-outform", "DER", "-days", "1", "-subj", f"/CN={common_name}/"],
        check=True, capture_output=True)
    return (tmp / "c.der").read_bytes()


def _signature_list(ders, sig_type=secureboot._EFI_CERT_X509_GUID):
    """One EFI_SIGNATURE_LIST holding the given DER certificates (same size)."""
    if not ders:
        return b""
    size = max(len(d) for d in ders)
    entries = b"".join(uuid.uuid4().bytes_le + d.ljust(size, b"\0") for d in ders)
    sig_size = 16 + size
    header = uuid.UUID(sig_type).bytes_le + struct.pack(
        "<III", 28 + len(entries), 0, sig_size)
    return header + entries


class TestFirmwareDatabaseReader(unittest.TestCase):
    """The companion check: which Microsoft authorities the firmware trusts."""

    @classmethod
    def setUpClass(cls):
        cls.ca2011 = _self_signed_der(secureboot.MICROSOFT_UEFI_CA_2011)
        cls.ca2023 = _self_signed_der(secureboot.MICROSOFT_UEFI_CA_2023)
        cls.other = _self_signed_der("Some Vendor Root")

    def _efivars(self, payload):
        d = Path(tempfile.mkdtemp())
        (d / f"db-{secureboot._IMAGE_SECURITY_DB_GUID}").write_bytes(
            struct.pack("<I", 0x27) + payload)
        return d

    def test_both_authorities_present(self):
        # Two lists (different certificate sizes are common in real firmware)
        payload = _signature_list([self.ca2011]) + _signature_list(
            [self.ca2023, self.other])
        state = secureboot.microsoft_uefi_ca_state(self._efivars(payload))
        self.assertEqual((state["2011"], state["2023"]), (True, True))
        self.assertIn("Some Vendor Root", state["common_names"])
        self.assertIn("both Microsoft signing authorities",
                      secureboot.microsoft_ca_advisory(state))

    def test_only_the_2011_authority(self):
        state = secureboot.microsoft_uefi_ca_state(
            self._efivars(_signature_list([self.ca2011, self.other])))
        self.assertEqual((state["2011"], state["2023"]), (True, False))
        self.assertIn("not the 2023 authority", secureboot.microsoft_ca_advisory(state))

    def test_neither_authority(self):
        state = secureboot.microsoft_uefi_ca_state(
            self._efivars(_signature_list([self.other])))
        self.assertEqual((state["2011"], state["2023"]), (False, False))
        self.assertIn("neither", secureboot.microsoft_ca_advisory(state))

    def test_hash_lists_are_skipped_not_misread(self):
        sha_list_guid = "c1c41626-504c-4092-aca9-41f936934328"   # EFI_CERT_SHA256_GUID
        hashes = _signature_list([b"\x11" * 32, b"\x22" * 32], sig_type=sha_list_guid)
        payload = hashes + _signature_list([self.ca2023])
        state = secureboot.microsoft_uefi_ca_state(self._efivars(payload))
        self.assertEqual((state["2011"], state["2023"]), (False, True))
        self.assertEqual(len(state["common_names"]), 1)

    def test_unreadable_database_is_unknown_not_a_verdict(self):
        self.assertIsNone(secureboot.microsoft_uefi_ca_state(Path(tempfile.mkdtemp())))
        self.assertEqual(secureboot.microsoft_ca_advisory(None), "")

    def test_a_truncated_list_ends_the_walk_cleanly(self):
        payload = _signature_list([self.ca2011])[:-40]
        self.assertEqual(secureboot.iter_signature_list_certificates(payload), [])


class TestCertificateStaging(unittest.TestCase):
    """(a) the certificate lands on the ESP and at the public root path."""

    def setUp(self):
        self.target = Path(tempfile.mkdtemp())
        self.der = b"\x30\x82\x01\x00" + b"cert-bytes" * 20
        src = self.target / mok.MOK_DIR.lstrip("/") / "mok.der"
        src.parent.mkdir(parents=True)
        src.write_bytes(self.der)

    def test_both_copies_match_the_source(self):
        with patch.object(mok.trace, "trace_event") as ev:
            staged = mok.stage_mok_certificate(self.target, f"{mok.MOK_DIR}/mok.der")
        self.assertEqual(staged, [mok.ESP_MOK_CERT, mok.PUBLIC_MOK_CERT])
        for rel in staged:
            p = self.target / rel.lstrip("/")
            self.assertEqual(p.read_bytes(), self.der)
            self.assertEqual(p.stat().st_mode & 0o777, 0o644)
        ev.assert_called_once()
        self.assertEqual(ev.call_args.kwargs["staged"], staged)

    def test_esp_path_is_the_directory_shim_lives_in(self):
        # "Enroll key from disk" is walked from the shim's directory; the two
        # modules must agree on it.
        self.assertEqual(mok.ESP_MOK_CERT_DIR, bootloader.ESP_BOOT_DIR)
        self.assertTrue(mok.ESP_MOK_CERT.endswith("/EFI/InterGenOS/mok.der"))

    def test_a_missing_source_raises(self):
        with self.assertRaises(OSError):
            mok.stage_mok_certificate(self.target, f"{mok.MOK_DIR}/absent.der")

    def test_the_signed_chain_install_stages_it(self):
        src = Path(bootloader.__file__).read_text()
        chain = src[src.index("def _install_signed_efi_chain("):src.index("def _build_grub_font_memdisk(")]
        self.assertIn("stage_mok_certificate(target, mok_keypair[\"der_path\"])", chain)
        # after shim + MokManager are on the ESP, before the fallback mirror
        self.assertLess(chain.index("shim staging to ESP failed"),
                        chain.index("stage_mok_certificate("))
        self.assertLess(chain.index("stage_mok_certificate("),
                        chain.index("EFI/BOOT fallback staging failed"))


class TestOneWordingOnEverySurface(unittest.TestCase):
    """(c) the window and the from-disk path, stated the same way everywhere."""

    REQUIRED = ("10 seconds", "Enroll key from disk", "EFI > InterGenOS > mok.der")

    def test_the_shared_wording_carries_the_three_facts(self):
        for needle in self.REQUIRED:
            self.assertIn(needle, mok_guidance.MOK_WINDOW_ADVISORY)

    def test_tui_prompt_in_every_secure_boot_state(self):
        with patch.object(tui, "firmware_ca_line", return_value=""):
            for sb in (True, False, None):
                text = tui._mok_prompt_text(sb)
                for needle in self.REQUIRED:
                    self.assertIn(needle, text, (sb, needle))

    def test_tui_prompt_carries_the_firmware_line_when_readable(self):
        with patch.object(tui, "firmware_ca_line",
                          return_value="This machine's firmware trusts both."):
            self.assertIn("firmware trusts both", tui._mok_prompt_text(False))
        with patch.object(tui, "firmware_ca_line", return_value=""):
            self.assertNotIn("firmware trusts", tui._mok_prompt_text(False))

    def test_gui_done_reminder(self):
        text = done_screen._success_description(mok_reminder=True, media_kind="usb")
        for needle in self.REQUIRED:
            self.assertIn(needle, text)

    def test_gui_user_page_description(self):
        from installer.frontend.gui.screens import user as user_screen
        src = Path(user_screen.__file__).read_text()
        self.assertIn("MOK_WINDOW_ADVISORY", src)
        self.assertIn("_ca_line()", src)

    def test_firmware_line_is_empty_when_unreadable(self):
        with patch.object(secureboot, "microsoft_uefi_ca_state", return_value=None):
            self.assertEqual(mok_guidance.firmware_ca_line(), "")


if __name__ == "__main__":
    unittest.main()
