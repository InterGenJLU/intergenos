#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm upgrade` tells the truth about what it did: exit code, advisory, sidecars.

Three defects, all three measured on real machines during the 2026-08-06 multi-package
upgrade, and all three sharing one shape — the upgrade path KNEW something and
did not pass it on.

  EXIT-CODE TRUTH. A package whose files deployed and whose critical
  post-install hook then failed is reported to the person reading the terminal
  and NOT to the exit code: the loop printed the error and carried on, and the
  command returned normally. Measured here: `pkm upgrade linux-kernel
  --allow-kernel-replace` printed "critical post-install hook(s) FAILED …
  Package marked DEGRADED" while its systemd unit recorded ExecMainStatus=0.
  Any automation gating on the exit code was told the upgrade succeeded. This
  is exactly the silent-failure class: a failure that is invisible to the
  thing that checks for failures.

  ADVISORY TRUTH. The update-advisory JSON that drives the desktop indicator is
  rewritten at the end of a transaction — but the rewrite was keyed on the
  SUCCESS list. A package that deployed and then failed its hook is a changed
  system that produced no rewrite, so the indicator stayed lit against a count
  taken before the change. That is what happened on every upgraded system: the JSON was
  written at 11:25:12 and the kernel it was describing was replaced at 11:29:44.

  SIDECAR TRUTH. When an upgrade cannot take a user's edited config file, it
  writes the new default beside it as a `.pkmnew` sidecar. The install path
  builds a block naming them — and the upgrade loop discards the message that
  carries it on success. Corroborated on four separate machines: sidecars were
  written and no upgrade output ever said so. The same block was ALSO skipped
  entirely by the critical-hook early return, so the one case where a user most
  needs to know what is pending was the case that said least.

These cases drive the real handler with the repository and installer stubbed,
because what is under test is the DECISION the upgrade path makes with the
results it is handed — not the download or the extraction, which have their own
coverage.
"""

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.cli as cli
from pkm import output
from pkm.database import PackageDB


def _flat(text):
    """Collapse wrapped prose to one line so an assertion tests the message
    rather than the terminal width it happened to be wrapped to."""
    return " ".join(text.split())


class FakeRepo:
    """Repository stub: one upgradable package, already 'downloaded'."""

    def __init__(self, remote):
        self.remote = remote

    def get_package(self, name):
        return self.remote.get(name)

    def download_package(self, name, reporter=None):
        return True, f"/nonexistent/{name}.igos.tar.gz"

    def resolve_dependencies(self, name, db):
        return True, [name]

    def has_synced_index(self):
        return True


class FakeInstaller:
    """Installer stub whose result and sidecar list each case sets."""

    def __init__(self, result=(True, "Installed"), sidecars=()):
        self.result = result
        self.sidecars = list(sidecars)
        self.calls = []

    def install(self, name, archive_path=None, expected_sha256=None,
                install_reason="manual", reporter=None, sidecars_out=None,
                queue=None):
        self.calls.append(name)
        if sidecars_out is not None:
            sidecars_out.extend(self.sidecars)
        return self.result


class FakeRemover:
    def __init__(self, db, root=None):
        self.db = db

    def remove(self, name, force=False, reporter=None, on_file=None):
        return True, f"Removed {name}"


class UpgradeTruthTestBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))
        self.db.add_installed("kern", "1.0", tier="core")
        self.remote = {
            "kern": {"name": "kern", "version": "2.0", "release": 1,
                     "sha256": "0" * 64, "size": 10, "depends": []},
        }
        self.refreshed = []

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def run_upgrade(self, installer, level=output.NORMAL,
                    remover_cls=None):
        args = argparse.Namespace(
            packages=["kern"], upgrade_all=False, allow_downgrade=False,
            ignore_holds=False, upgrade_security_only=False,
            upgrade_allow_kernel_replace=True, assume_yes=True,
            upgrade_dry_run=False, quiet=False, verbose=False,
        )
        buf = io.StringIO()
        prior = output.process_level()
        output.set_process_level(level)
        output._process_reporter.stream = buf
        output._process_reporter.err_stream = buf
        try:
            with redirect_stdout(buf), \
                 patch.object(cli, "RepoManager",
                              lambda *a, **k: FakeRepo(self.remote)), \
                 patch.object(cli, "PackageInstaller",
                              lambda *a, **k: installer), \
                 patch("pkm.remover.PackageRemover",
                       remover_cls or FakeRemover), \
                 patch.object(cli, "_confirm_upgrade", lambda _a: True), \
                 patch.object(cli, "_save_rollback_archive",
                              lambda *a, **k: None), \
                 patch.object(cli, "refresh_available_updates_after_transaction",
                              lambda db, **k: self.refreshed.append(True)), \
                 patch("pkm.pretxn.run_pre_transaction_hook",
                       lambda *a, **k: None):
                rc = cli.cmd_upgrade(self.db, args)
        finally:
            output.set_process_level(prior)
            output._process_reporter.stream = None
            output._process_reporter.err_stream = None
        return rc, buf.getvalue()


class ExitCodeTruthTest(UpgradeTruthTestBase):
    def test_a_successful_upgrade_returns_zero(self):
        rc, _ = self.run_upgrade(FakeInstaller(result=(True, "Installed kern")))
        self.assertIn(rc, (0, None))

    def test_a_critical_hook_failure_makes_the_command_exit_non_zero(self):
        """The measured defect. The message says DEGRADED; the exit code must
        say so too, or automation cannot see it."""
        msg = ("Installed kern 2.0 (5 files), but critical post-install "
               "hook(s) FAILED: post_install. Package marked DEGRADED")
        rc, text = self.run_upgrade(FakeInstaller(result=(False, msg)))
        self.assertEqual(rc, 1)
        self.assertIn("DEGRADED", text)

    def test_the_failure_is_named_in_the_closing_line(self):
        """A non-zero code with no statement of what failed sends the reader
        back through the scroll-back to find out."""
        rc, text = self.run_upgrade(FakeInstaller(result=(False, "nope")))
        self.assertEqual(rc, 1)
        flat = _flat(text)
        self.assertIn("did not upgrade: kern", flat)
        self.assertIn("Exiting non-zero", flat)

    def test_a_refused_removal_also_fails_the_transaction(self):
        """The remove step's return value was discarded outright, so a refused
        removal left the old package in place and the install landed on top of
        a package that was supposed to be gone."""
        class RefusingRemover(FakeRemover):
            def remove(self, name, force=False, reporter=None, on_file=None):
                return False, "another package depends on it"

        installer = FakeInstaller(result=(True, "Installed kern"))
        rc, text = self.run_upgrade(installer, remover_cls=RefusingRemover)
        self.assertEqual(rc, 1)
        self.assertIn("did not succeed", _flat(text))
        # And it did not go on to install over the package it failed to remove.
        self.assertEqual(installer.calls, [])


class AdvisoryTruthTest(UpgradeTruthTestBase):
    def test_a_successful_upgrade_refreshes_the_advisory(self):
        self.run_upgrade(FakeInstaller(result=(True, "Installed kern")))
        self.assertEqual(len(self.refreshed), 1)

    def test_a_degraded_upgrade_also_refreshes_the_advisory(self):
        """The measured defect. The package deployed and was registered;
        what is available to upgrade HAS moved. Keying the refresh on the
        success list left the indicator describing the system as it was
        before."""
        msg = ("Installed kern 2.0 (5 files), but critical post-install "
               "hook(s) FAILED: post_install. Package marked DEGRADED")
        self.run_upgrade(FakeInstaller(result=(False, msg)))
        self.assertEqual(
            len(self.refreshed), 1,
            "a package that deployed and then failed its hook changed the "
            "system; the update advisory must be recomputed or the desktop "
            "indicator stays lit against a stale count")


class SidecarTruthTest(UpgradeTruthTestBase):
    def test_sidecars_written_during_an_upgrade_are_named(self):
        installer = FakeInstaller(result=(True, "Installed kern"),
                                  sidecars=["/etc/sudoers.pkmnew"])
        _rc, text = self.run_upgrade(installer)
        self.assertIn("/etc/sudoers.pkmnew", text)
        self.assertIn("pending review", _flat(text))

    def test_the_review_path_is_offered_rather_than_a_blind_move(self):
        installer = FakeInstaller(result=(True, "Installed kern"),
                                  sidecars=["/etc/sudoers.pkmnew"])
        _rc, text = self.run_upgrade(installer)
        flat = _flat(text)
        self.assertIn("diff <path> <path>.pkmnew", flat)
        self.assertIn("pkm refresh-baseline", flat)

    def test_an_account_database_sidecar_carries_its_refusal(self):
        """Moving the pristine skeleton over a live account database erases
        every account. The upgrade path must carry that warning, not just the
        install path."""
        installer = FakeInstaller(result=(True, "Installed kern"),
                                  sidecars=["/etc/shadow.pkmnew"])
        _rc, text = self.run_upgrade(installer)
        flat = _flat(text)
        self.assertIn("/etc/shadow.pkmnew", flat)
        self.assertIn("do NOT `mv` these over the live files", flat)

    def test_nothing_is_printed_when_no_sidecars_were_written(self):
        """The common case. An upgrade that touched no protected config must
        not print an empty configuration block.

        Asserted against the block's own heading rather than the word
        `pkmnew`, because the pre-upgrade plan summary already promises that
        sidecars will be reported at the end — a promise that, until this
        change, nothing kept."""
        _rc, text = self.run_upgrade(FakeInstaller(result=(True, "ok")))
        self.assertNotIn("pending review", text)
        self.assertNotIn("Account databases", text)

    def test_the_installer_hands_back_sidecar_paths_as_data(self):
        """The mechanism that makes the above possible: the paths are returned
        to the caller as a list rather than buried in a message the caller
        discards. A substring of prose can be dropped by accident; a list the
        caller owns cannot."""
        import inspect
        from pkm.installer import PackageInstaller
        params = inspect.signature(PackageInstaller.install).parameters
        self.assertIn("sidecars_out", params)


class DegradedInstallStillNamesItsSidecarsTest(unittest.TestCase):
    """The install path's own half: the critical-hook early return used to
    skip the sidecar block entirely, so the run that most needed to say what
    was pending said the least."""

    def test_the_critical_hook_return_includes_the_sidecar_block(self):
        from pkm import installer as installer_mod
        src = Path(installer_mod.__file__).read_text(encoding="utf-8")
        # The early return for critical hook failures must build and carry the
        # summary. Located by the marker the code itself uses.
        self.assertIn("degraded_pkmnew = summary_lines(pkmnew_written)", src)
        head, tail = src.split("degraded_pkmnew = summary_lines", 1)
        # …and it must be threaded into the returned message, inside that
        # same early-return block rather than only in the success path below.
        self.assertIn("degraded_pkmnew", tail.split("return False,", 1)[1]
                      .split("def ", 1)[0])


if __name__ == "__main__":
    unittest.main()
