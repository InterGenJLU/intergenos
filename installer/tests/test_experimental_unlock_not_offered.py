# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The EXPERIMENTAL unlock methods are not offered in this release.

Decided 2026-08-11: an external functionality review (2026-08-08) found both
EXPERIMENTAL LUKS unlock methods not functional as shipped — FIDO2 enrollment
aborts an install that opts in, and the TPM2 sealed key's parent context does
not survive a reboot. Until the corrected methods return with dedicated
hardware verification, the installer must not offer them anywhere:

* the single offer switch (disks.EXPERIMENTAL_UNLOCK_OFFERED) is False;
* the backend REFUSES install_io that requests them anyway — loudly, in the
  aggregated validation error, never by silently dropping the request;
* passphrase LUKS is untouched by the withholding.

The GUI switch visibility and the TUI questions gate on the same constant, so
these tests plus the constant are the whole disable; the frontends carry no
second copy of the decision.
"""
import unittest
from unittest import mock

from installer.backend import disks
from installer.backend.install import validate_install_inputs

MINIMAL_CFG = {
    "hostname": "testhost",
    "timezone": "UTC",
    "locale": "en_US.UTF-8",
    "keymap": "us",
    "package_groups": ["core"],
}


def _io(**overrides):
    io = {"target_disk": "/dev/vda", "username": "user"}
    io.update(overrides)
    return io


def _validate(io):
    try:
        validate_install_inputs(dict(MINIMAL_CFG), io)
        return []
    except ValueError as e:
        return str(e).splitlines()


class OfferSwitchTests(unittest.TestCase):
    def test_the_methods_are_not_offered_this_release(self):
        self.assertFalse(
            disks.EXPERIMENTAL_UNLOCK_OFFERED,
            "EXPERIMENTAL_UNLOCK_OFFERED flipped to True — that re-offers the "
            "TPM2/FIDO2 unlock methods everywhere and is only correct after "
            "the 2026-08-08 review findings are fixed AND verified on real "
            "hardware across a reboot.",
        )


class BackendRefusalTests(unittest.TestCase):
    def test_tpm2_request_is_refused_loudly(self):
        lines = _validate(_io(luks_enabled=True, luks_passphrase="pw",
                              tpm2_enabled=True))
        self.assertTrue(any("not offered in this release" in ln for ln in lines),
                        f"expected a loud refusal, got: {lines}")

    def test_fido2_request_is_refused_loudly(self):
        lines = _validate(_io(luks_enabled=True, luks_passphrase="pw",
                              fido2_enabled=True))
        self.assertTrue(any("not offered in this release" in ln for ln in lines),
                        f"expected a loud refusal, got: {lines}")

    def test_passphrase_luks_is_unaffected(self):
        lines = _validate(_io(luks_enabled=True, luks_passphrase="pw"))
        self.assertFalse(any("not offered" in ln for ln in lines),
                         f"passphrase-only LUKS must not trip the refusal: {lines}")

    def test_refusal_lifts_when_offered_again(self):
        """Mutation control: the refusal is the constant's doing, nothing else —
        with the switch True the same request validates past this check."""
        with mock.patch.object(disks, "EXPERIMENTAL_UNLOCK_OFFERED", True):
            lines = _validate(_io(luks_enabled=True, luks_passphrase="pw",
                                  tpm2_enabled=True))
            self.assertFalse(any("not offered" in ln for ln in lines),
                             f"refusal must gate on the offer switch: {lines}")


class FrontendGatingTests(unittest.TestCase):
    def test_tui_gates_both_questions_on_the_offer_switch(self):
        """The TUI asks the two questions only behind the constant. Read the
        real source: both _ask_yesno call sites must sit under the gate."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[2]
               / "installer/frontend/tui.py").read_text()
        self.assertIn(
            "if _disks.EXPERIMENTAL_UNLOCK_OFFERED and _disks.tpm2_present()",
            src)
        self.assertIn(
            "if _disks.EXPERIMENTAL_UNLOCK_OFFERED and _disks.fido2_tools_available()",
            src)

    def test_gui_gates_switch_visibility_on_the_offer_switch(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[2]
               / "installer/frontend/gui/screens/disk.py").read_text()
        self.assertEqual(
            src.count("_offered = _disks.EXPERIMENTAL_UNLOCK_OFFERED"), 2,
            "both GUI visibility sites must derive from the single offer switch")
        self.assertIn("self._tpm2_switch.set_visible(active and _offered)", src)
        self.assertIn("self._fido2_switch.set_visible(active and _offered)", src)
        self.assertIn(
            "self._tpm2_switch.set_visible(state.luks_enabled and _offered)", src)
        self.assertIn(
            "self._fido2_switch.set_visible(state.luks_enabled and _offered)", src)


if __name__ == "__main__":
    unittest.main()
