# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""R001.3 gating row 32 — a person never ends an install trusting a key
they were not shown.

The defect this file pins, measured 2026-09-16 on the released image: the
installer's graphical key box validated `text.strip().split("\\n", 1)[0]`
— the first line only — and then stored and wrote the entire contents of
the box. Pasting an existing `authorized_keys` file had line 1 checked
and every line installed. The validator and the writer disagreed about
what one unit of work was, and nothing showed the person what had been
accepted.

What is required, and what each class below proves:

  * every line is validated, not the first (`TestEveryLineIsValidated`);
  * a line that fails is REJECTED with a reason the person reads, never
    dropped (`TestRejectedLinesAreNamed`);
  * each key is shown with its type, comment and fingerprint, and is
    installed only if it was accepted (`TestOnlyAcceptedKeysAreStored`,
    `TestTuiAcceptsOneKeyExplicitly`);
  * the file on the target holds exactly the accepted keys
    (`TestBackendWritesOnlyValidatedKeys`);
  * a key set that ends up empty does not ship the keys-only server
    configuration, which would leave nobody able to log in
    (`TestNoUsableKeyMeansNoLockout`).

The fingerprint instrument is proved against the real `ssh-keygen`
(`TestFingerprintMatchesSshKeygen`) rather than against its own
arithmetic — an instrument never shown to agree with the tool it claims
to reproduce cannot certify anything.
"""

import base64
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from installer.backend import sshkeys, users  # noqa: E402
from installer.frontend import tui  # noqa: E402
from installer.frontend.gui.screens import packages as packages_screen  # noqa: E402


def _keypair(key_type="ed25519", comment="someone@somewhere"):
    """A real key made by the real tool. Returns (public line, fingerprint)."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "id"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", key_type, "-N", "", "-C", comment,
         "-f", str(path)],
        check=True, capture_output=True)
    pub = (tmp / "id.pub").read_text().strip()
    fp = subprocess.run(
        ["ssh-keygen", "-lf", str(tmp / "id.pub")],
        check=True, capture_output=True, text=True).stdout.split()[1]
    return pub, fp


class TestFingerprintMatchesSshKeygen(unittest.TestCase):
    """The instrument agrees with the tool it reproduces."""

    def test_every_supported_key_type_matches(self):
        for key_type in ("ed25519", "rsa", "ecdsa"):
            with self.subTest(key_type=key_type):
                pub, expected = _keypair(key_type)
                result = sshkeys.parse(pub)
                self.assertEqual(len(result.accepted), 1, result.rejected)
                self.assertEqual(result.accepted[0].fingerprint, expected)

    def test_bit_size_matches_ssh_keygen(self):
        tmp = Path(tempfile.mkdtemp())
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "rsa", "-b", "3072", "-N", "",
             "-C", "sized", "-f", str(tmp / "id")],
            check=True, capture_output=True)
        listing = subprocess.run(
            ["ssh-keygen", "-lf", str(tmp / "id.pub")],
            check=True, capture_output=True, text=True).stdout.split()
        parsed = sshkeys.parse((tmp / "id.pub").read_text()).accepted[0]
        self.assertEqual(str(parsed.bits), listing[0])


class TestEveryLineIsValidated(unittest.TestCase):
    """The first line is not the unit of work; every line is."""

    def test_a_pasted_authorized_keys_file_yields_one_entry_per_key(self):
        first, first_fp = _keypair(comment="laptop")
        second, second_fp = _keypair("rsa", comment="desktop")
        third, third_fp = _keypair("ecdsa", comment="phone")
        block = f"# my keys\n{first}\n\n{second}\n{third}\n"

        result = sshkeys.parse(block)

        self.assertEqual([k.fingerprint for k in result.accepted],
                         [first_fp, second_fp, third_fp])
        self.assertEqual([k.comment for k in result.accepted],
                         ["laptop", "desktop", "phone"])
        self.assertEqual(result.rejected, ())

    def test_a_bad_line_after_a_good_one_is_caught(self):
        good, _ = _keypair()
        result = sshkeys.parse(f"{good}\nnot-a-key at all\n")

        self.assertEqual(len(result.accepted), 1)
        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(result.rejected[0].lineno, 2)

    def test_line_numbers_point_at_the_line_the_person_sees(self):
        good, _ = _keypair()
        result = sshkeys.parse(f"# header\n\n{good}\ngarbage here\n")
        self.assertEqual(result.accepted[0].lineno, 3)
        self.assertEqual(result.rejected[0].lineno, 4)

    def test_material_that_does_not_match_its_declared_type_is_refused(self):
        ed, _ = _keypair()
        material = ed.split()[1]
        result = sshkeys.parse(f"ssh-rsa {material} mismatched")
        self.assertEqual(result.accepted, ())
        self.assertIn("ssh-ed25519", result.rejected[0].reason)

    def test_a_truncated_paste_is_refused(self):
        ed, _ = _keypair()
        key_type, material, comment = ed.split()
        result = sshkeys.parse(f"{key_type} {material[:20]} {comment}")
        self.assertEqual(result.accepted, ())
        self.assertEqual(len(result.rejected), 1)

    def test_material_cut_off_after_the_algorithm_name_is_refused(self):
        """The class the name check alone let through.

        A blob truncated immediately after its algorithm name still
        decodes as base64 and still names its own algorithm, so a check
        that stopped at the name accepted it. Caught here before the
        parser shipped; the field-count check is what refuses it.
        """
        name_only = base64.b64encode(
            struct.pack(">I", 11) + b"ssh-ed25519").decode()
        result = sshkeys.parse(f"ssh-ed25519 {name_only} cut-short")
        self.assertEqual(result.accepted, ())
        self.assertIn("2 parts", result.rejected[0].reason)

    def test_extra_material_on_the_end_is_refused(self):
        ed, _ = _keypair()
        blob = base64.b64decode(ed.split()[1])
        padded = base64.b64encode(
            blob + struct.pack(">I", 4) + b"junk").decode()
        result = sshkeys.parse(f"ssh-ed25519 {padded} tampered")
        self.assertEqual(result.accepted, ())
        self.assertIn("extra text", result.rejected[0].reason)

    def test_the_same_key_twice_installs_once_and_says_so(self):
        key, _ = _keypair()
        result = sshkeys.parse(f"{key}\n{key}\n")
        self.assertEqual(len(result.accepted), 1)
        self.assertIn("line 1", result.rejected[0].reason)


class TestRejectedLinesAreNamed(unittest.TestCase):
    """A line we will not install is named, with a reason to act on."""

    def test_a_private_key_paste_says_so_in_those_words(self):
        result = sshkeys.parse(
            "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaA==\n")
        self.assertEqual(result.accepted, ())
        self.assertIn("PRIVATE key", result.rejected[0].reason)

    def test_an_options_prefix_is_refused_and_named(self):
        key, _ = _keypair()
        result = sshkeys.parse(f'command="/bin/false" {key}')
        self.assertEqual(result.accepted, ())
        self.assertIn("option", result.rejected[0].reason)

    def test_a_certificate_is_refused_and_named(self):
        result = sshkeys.parse(
            "ssh-ed25519-cert-v01@openssh.com AAAAB3 signed@somewhere")
        self.assertEqual(result.accepted, ())
        self.assertIn("certificate", result.rejected[0].reason)

    def test_a_type_with_no_material_is_refused(self):
        result = sshkeys.parse("ssh-ed25519")
        self.assertEqual(result.accepted, ())
        self.assertIn("no key material", result.rejected[0].reason)

    def test_nothing_the_person_pasted_vanishes(self):
        good, _ = _keypair()
        block = f"{good}\nrubbish\nssh-ed25519 !!!!\n"
        result = sshkeys.parse(block)
        self.assertEqual(
            len(result.accepted) + len(result.rejected), 3)


class TestOnlyAcceptedKeysAreStored(unittest.TestCase):
    """The graphical screen stores what the person accepted, and nothing else."""

    def test_the_first_line_only_validator_is_gone(self):
        self.assertFalse(
            hasattr(packages_screen.PackagesPage, "_looks_like_ssh_pubkey"),
            "the first-line-only validator is the defect this row closes; "
            "it must not survive alongside the parser")

    def test_a_block_with_a_bad_line_is_refused_whole(self):
        good, _ = _keypair()
        text, error = packages_screen.resolve_ssh_keys(
            f"{good}\nnot-a-key at all\n", accepted_fingerprints=None)
        self.assertIsNone(text)
        self.assertIn("line 2", error)

    def test_only_the_fingerprints_the_person_ticked_are_written(self):
        first, first_fp = _keypair(comment="laptop")
        second, second_fp = _keypair(comment="desktop")
        text, error = packages_screen.resolve_ssh_keys(
            f"{first}\n{second}\n", accepted_fingerprints={first_fp})
        self.assertIsNone(error)
        self.assertEqual(text, first + "\n")
        self.assertNotIn(second_fp.split(":")[1][:12], text)

    def test_accepting_none_of_them_is_refused_rather_than_silently_empty(self):
        key, _ = _keypair()
        text, error = packages_screen.resolve_ssh_keys(
            key, accepted_fingerprints=set())
        self.assertIsNone(text)
        self.assertIn("no key", error.lower())

    def test_an_empty_box_is_not_an_error(self):
        text, error = packages_screen.resolve_ssh_keys(
            "   \n", accepted_fingerprints=None)
        self.assertEqual(text, "")
        self.assertIsNone(error)


class TestTheReviewDialogIsReal(unittest.TestCase):
    """The dialog is built by the real toolkit and drives the real state.

    The pure decision is proved above; this fires the widget code itself
    against GTK, because a review surface that never builds is a review
    surface nobody sees. Skipped where no display is available (the
    pattern tests/welcome uses); the delivery records whether it ran.
    """

    @classmethod
    def setUpClass(cls):
        # The guard asks for a DISPLAY, not for init_check(): measured on
        # this machine 2026-09-16, `Gtk.init_check()` returns True with no
        # display at all, and the first widget construction then raises
        # "Gtk couldn't be initialized". A guard that cannot detect the
        # condition it guards against turns a skip into a failure on any
        # headless machine.
        from gi.repository import Gdk, Gtk
        Gtk.init_check()
        if Gdk.Display.get_default() is None:
            raise unittest.SkipTest("no display for GTK widget construction")

    def test_each_key_gets_a_row_naming_its_fingerprint_and_only_ticked_keys_are_kept(self):
        from gi.repository import Adw, Gtk

        first, first_fp = _keypair(comment="laptop")
        second, second_fp = _keypair(comment="desktop")

        window = Adw.ApplicationWindow()
        page = packages_screen.PackagesPage(window)
        page._ssh_key_view.get_buffer().set_text(f"{first}\n{second}\n")

        captured = []
        with patch.object(Adw.AlertDialog, "present",
                          new=lambda self, parent: captured.append(self)):
            page._on_review_ssh_keys(None)

        self.assertEqual(len(captured), 1, "no dialog was built")
        dialog = captured[0]

        rows, checks, child = [], [], dialog.get_extra_child().get_first_child()
        while child is not None:
            for widget in _walk(child):
                if isinstance(widget, Adw.ActionRow):
                    rows.append(widget)
                elif isinstance(widget, Gtk.CheckButton):
                    checks.append(widget)
            child = child.get_next_sibling()

        subtitles = [r.get_subtitle() for r in rows]
        self.assertEqual(len(rows), 2, subtitles)
        self.assertTrue(any(first_fp in s for s in subtitles), subtitles)
        self.assertTrue(any(second_fp in s for s in subtitles), subtitles)
        self.assertTrue(any("laptop" in (r.get_title() or "") for r in rows))
        self.assertFalse(any(c.get_active() for c in checks),
                         "a key was pre-ticked; keeping one is a decision")

        checks[0].set_active(True)
        dialog.emit("response", "keep")
        self.assertEqual(page._ssh_key_accepted, {first_fp})

        text, error = packages_screen.resolve_ssh_keys(
            f"{first}\n{second}\n", page._ssh_key_accepted)
        self.assertIsNone(error)
        self.assertEqual(text, first + "\n")


def _walk(widget):
    """Every widget in this subtree, the widget itself included."""
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


class TestTuiAcceptsOneKeyExplicitly(unittest.TestCase):
    """The text installer shows the key and takes an explicit yes."""

    def test_a_rejected_key_re_prompts_instead_of_substituting_passwords(self):
        good, good_fp = _keypair()
        asked = []

        def fake_input(title, prompt, default=""):
            asked.append(prompt)
            return (0, "not-a-key at all") if len(asked) == 1 else (0, good)

        with patch.object(tui, "_ask_input", side_effect=fake_input), \
                patch.object(tui, "_dialog", return_value=(0, "")) as dialog, \
                patch.object(tui, "_ask_yesno", return_value=True):
            key = tui.prompt_ssh_public_key()

        self.assertEqual(key, good)
        self.assertEqual(len(asked), 2, "the person was not asked again")
        shown = " ".join(str(a) for call in dialog.call_args_list
                         for a in call.args)
        self.assertIn("not-a-key", shown,
                      "the rejected line itself was never shown")

    def test_the_key_is_shown_with_its_fingerprint_before_it_is_accepted(self):
        good, good_fp = _keypair(comment="laptop")
        with patch.object(tui, "_ask_input", return_value=(0, good)), \
                patch.object(tui, "_ask_yesno", return_value=True) as yesno:
            key = tui.prompt_ssh_public_key()

        self.assertEqual(key, good)
        shown = " ".join(str(a) for call in yesno.call_args_list
                         for a in call.args)
        self.assertIn(good_fp, shown)
        self.assertIn("laptop", shown)

    def test_declining_the_key_installs_nothing(self):
        good, _ = _keypair()
        with patch.object(tui, "_ask_input", return_value=(0, good)), \
                patch.object(tui, "_ask_yesno", return_value=False):
            self.assertEqual(tui.prompt_ssh_public_key(), "")


class TestBackendWritesOnlyValidatedKeys(unittest.TestCase):
    """The file on the target holds exactly the keys that were accepted."""

    def _target_with_user(self):
        target = Path(tempfile.mkdtemp())
        (target / "etc").mkdir(parents=True)
        (target / "etc/passwd").write_text(
            f"someone:x:{os.getuid()}:{os.getgid()}::/home/someone:/bin/bash\n")
        (target / "home/someone").mkdir(parents=True)
        return target

    def test_each_accepted_key_lands_on_its_own_line(self):
        target = self._target_with_user()
        first, _ = _keypair(comment="laptop")
        second, _ = _keypair(comment="desktop")

        users._install_ssh_authorized_key(
            target, "someone", f"{first}\n{second}\n")

        written = (target / "home/someone/.ssh/authorized_keys").read_text()
        self.assertEqual(written, f"{first}\n{second}\n")

    def test_an_unvalidated_line_never_reaches_the_file(self):
        target = self._target_with_user()
        good, _ = _keypair()

        users._install_ssh_authorized_key(
            target, "someone", f"{good}\ncommand=\"/bin/false\" {good}\n")

        written = (target / "home/someone/.ssh/authorized_keys").read_text()
        self.assertEqual(written, f"{good}\n")
        self.assertNotIn("command=", written)

    def test_a_key_whose_ownership_cannot_be_set_counts_as_not_installed(self):
        """sshd ignores an authorized_keys it does not trust — so do we.

        When the target's /etc/passwd has no such user the ownership
        cannot be set and sshd will refuse the file. Reporting that as an
        installed key would let the caller disable password login with no
        working way back in.
        """
        target = Path(tempfile.mkdtemp())
        (target / "etc").mkdir(parents=True)
        (target / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
        good, _ = _keypair()

        installed = users._install_ssh_authorized_key(target, "someone", good)

        self.assertEqual(installed, [])

    def test_the_file_keeps_the_permissions_sshd_requires(self):
        target = self._target_with_user()
        good, _ = _keypair()
        users._install_ssh_authorized_key(target, "someone", good)
        auth = target / "home/someone/.ssh/authorized_keys"
        self.assertEqual(auth.stat().st_mode & 0o777, 0o600)
        self.assertEqual(auth.parent.stat().st_mode & 0o777, 0o700)


class TestNoUsableKeyMeansNoLockout(unittest.TestCase):
    """An empty key set must not ship a server nobody can log in to."""

    def test_keys_only_configuration_is_withheld_when_no_key_validates(self):
        target = Path(tempfile.mkdtemp())
        (target / "etc").mkdir(parents=True)
        (target / "etc/passwd").write_text(
            f"someone:x:{os.getuid()}:{os.getgid()}::/home/someone:/bin/bash\n")
        (target / "home/someone").mkdir(parents=True)

        with patch.object(users.trace, "traced_run") as run, \
                patch.object(users, "write_ssh_firewall_fragment"):
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            summary = users.enable_ssh_server(
                target, username="someone",
                public_key='command="/bin/false" ssh-ed25519 AAAA bad')

        self.assertEqual(summary.installed, 0)
        self.assertEqual(len(summary.rejected), 1)
        self.assertFalse(
            (target / "etc/ssh/sshd_config.d"
             / "02-intergenos-keys-only.conf").exists(),
            "password login was disabled with no key able to replace it")
        self.assertFalse(
            (target / "home/someone/.ssh/authorized_keys").exists())

    def test_a_valid_key_does_ship_the_keys_only_configuration(self):
        target = Path(tempfile.mkdtemp())
        (target / "etc").mkdir(parents=True)
        (target / "etc/passwd").write_text(
            f"someone:x:{os.getuid()}:{os.getgid()}::/home/someone:/bin/bash\n")
        (target / "home/someone").mkdir(parents=True)
        good, _ = _keypair()

        with patch.object(users.trace, "traced_run") as run, \
                patch.object(users, "write_ssh_firewall_fragment"):
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            summary = users.enable_ssh_server(
                target, username="someone", public_key=good)

        self.assertEqual(summary.installed, 1)
        self.assertTrue(
            (target / "etc/ssh/sshd_config.d"
             / "02-intergenos-keys-only.conf").exists())


if __name__ == "__main__":
    unittest.main()
