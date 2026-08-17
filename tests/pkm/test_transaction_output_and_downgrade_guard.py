#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Release honesty, the downgrade guard, and the pre-download acceptance gate.

Every test here is written against a MEASURED failure, and every one of them
FAILS on the tree immediately before this change — that is the property that
makes them worth having. The three origins:

  1. `pkm install forge` then `pkm reinstall forge` replaced release 133
     (locally built, ahead of publication) with the mirror's release 110. pkm
     held both numbers and compared nothing, and no transaction line printed a
     release, so it was invisible until a later `pkm info`.
  2. The same removal printed roughly 700 co-owner package names for 18
     retained directories, twice.
  3. `sudo pkm install steam` resolved a 40-package lib32 closure, installed
     every one of it with no confirmation of any kind and a per-package file
     listing throughout, then stopped after deploying the HELPER — reporting
     success with the application absent, while the launcher's own error
     message advised running the command that had just claimed success.

The parts are numbered as they were specified, and each class says which part
it covers and what the base behaviour was.
"""

import argparse
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pkm.cli as cli
from pkm import output, txn


# --------------------------------------------------------------------------
# Fixtures shaped like the real transactions that failed.
# --------------------------------------------------------------------------

FORGE_INSTALLED = {"name": "forge", "version": "1.0.0", "release": 133}
FORGE_MIRROR = {"name": "forge", "version": "1.0.0", "release": 110,
                "sha256": "f" * 64, "size": 1024, "installed_size": 4096}

# The steam closure, in the shape resolve_dependencies returns: 40 names, the
# requested one last, which is what put the helper deploy at the very end.
STEAM_CLOSURE = [f"lib32-dep{i:02d}" for i in range(39)] + ["steam"]


def _index_entry(name, version="1.0", release=2, size=1000, installed_size=4000):
    return {"name": name, "version": version, "release": release,
            "sha256": "a" * 64, "size": size, "installed_size": installed_size}


def _install_args(*packages, **kw):
    ns = argparse.Namespace(
        packages=list(packages), archive=None, archive_trust="strict",
        quiet=False, verbose=False, allow_downgrade=False, assume_yes=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _reinstall_args(*packages, **kw):
    ns = argparse.Namespace(
        packages=list(packages), quiet=False, verbose=False,
        allow_downgrade=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class _FakeStdin:
    """A stdin whose tty-ness and answer are both explicit."""

    def __init__(self, tty=True, answer="\n"):
        self._tty = tty
        self._answer = answer

    def isatty(self):
        return self._tty


# ==========================================================================
# PART 1 — the downgrade guard, fail-closed.
# ==========================================================================

class Part1DowngradeGuard(unittest.TestCase):
    """BASE BEHAVIOUR: nothing compared the two releases at all, so the
    r133 -> r110 replacement went through silently."""

    def test_the_r133_to_r110_replay_refuses(self):
        d = txn.downgrade_decision("forge", FORGE_INSTALLED, FORGE_MIRROR)
        self.assertEqual(d.kind, "refuse")
        self.assertFalse(d.ok)

    def test_the_refusal_names_both_numbers(self):
        d = txn.downgrade_decision("forge", FORGE_INSTALLED, FORGE_MIRROR)
        self.assertIn("1.0.0-133", d.message)
        self.assertIn("1.0.0-110", d.message)

    def test_the_refusal_names_the_override(self):
        d = txn.downgrade_decision("forge", FORGE_INSTALLED, FORGE_MIRROR)
        self.assertIn("--allow-downgrade", d.message)

    def test_the_override_permits_it_and_says_so(self):
        d = txn.downgrade_decision(
            "forge", FORGE_INSTALLED, FORGE_MIRROR, allow_downgrade=True)
        self.assertEqual(d.kind, "downgrade")
        self.assertTrue(d.ok)
        self.assertIn("DOWNGRADING", d.message)

    def test_an_upgrade_is_not_obstructed(self):
        newer = dict(FORGE_MIRROR, release=140)
        self.assertEqual(
            txn.downgrade_decision("forge", FORGE_INSTALLED, newer).kind,
            "proceed")

    def test_the_same_build_is_a_legitimate_reinstall(self):
        same = dict(FORGE_MIRROR, release=133)
        self.assertEqual(
            txn.downgrade_decision("forge", FORGE_INSTALLED, same).kind, "same")

    def test_a_version_downgrade_refuses_not_only_a_release_downgrade(self):
        older = {"version": "0.9.9", "release": 999}
        self.assertEqual(
            txn.downgrade_decision("forge", FORGE_INSTALLED, older).kind,
            "refuse")

    def test_an_unorderable_pair_refuses_rather_than_guessing(self):
        # A malformed release cannot be ordered. Treating "cannot compare" as
        # "safe to replace" would reintroduce the silent replacement through a
        # different door, so it refuses.
        bad = {"version": "1.0.0", "release": "not-an-int"}
        d = txn.downgrade_decision("forge", FORGE_INSTALLED, bad)
        self.assertEqual(d.kind, "unknown")
        self.assertFalse(d.ok)

    def test_a_source_stating_no_version_is_not_treated_as_a_downgrade(self):
        # Narrower than the case above on purpose: no comparison was possible,
        # which is not evidence of direction. Blocking here would break
        # reinstalls for a reason unrelated to the defect.
        self.assertTrue(
            txn.downgrade_decision("forge", FORGE_INSTALLED, {"name": "forge"}).ok)

    def test_a_first_install_has_no_direction_to_compare(self):
        self.assertTrue(txn.downgrade_decision("forge", None, FORGE_MIRROR).ok)


class Part1ReinstallRefusesBeforeTouchingAnything(unittest.TestCase):
    """The guard has to fire BEFORE the acquire, so a refusal costs no
    download and removes nothing. BASE BEHAVIOUR: cmd_reinstall went straight
    to acquire-then-remove-then-install with no comparison anywhere."""

    def _run(self, allow_downgrade=False):
        db = MagicMock()
        db.get_installed.return_value = FORGE_INSTALLED
        with patch("pkm.cli.is_download_helper", return_value=False), \
             patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.PackageRemover") as Remover, \
             patch("pkm.cli.RepoManager") as Repo:
            Repo.return_value.get_package.return_value = FORGE_MIRROR
            Repo.return_value.download_package.return_value = (True, "/tmp/x")
            Installer.return_value._find_archive.return_value = None
            Remover.return_value.remove.return_value = (True, "removed")
            Installer.return_value.install.return_value = (True, "installed")
            buf = io.StringIO()
            raised = None
            try:
                with redirect_stdout(buf):
                    cli.cmd_reinstall(
                        db, _reinstall_args("forge",
                                            allow_downgrade=allow_downgrade))
            except SystemExit as exc:
                raised = exc
            return raised, Remover, Repo, Installer

    def test_reinstall_refuses_and_exits_nonzero(self):
        raised, _, _, _ = self._run()
        self.assertIsNotNone(raised, "a downgrading reinstall must not proceed")
        self.assertNotEqual(raised.code, 0)

    def test_nothing_was_removed(self):
        _, Remover, _, _ = self._run()
        Remover.return_value.remove.assert_not_called()

    def test_nothing_was_downloaded(self):
        _, _, Repo, _ = self._run()
        Repo.return_value.download_package.assert_not_called()

    def test_the_override_lets_it_through(self):
        raised, Remover, _, _ = self._run(allow_downgrade=True)
        self.assertIsNone(raised)
        Remover.return_value.remove.assert_called_once()


class Part1UpgradeSaysWhyItSkipped(unittest.TestCase):
    """A NAMED package the repository would move backwards was correctly not
    upgraded — and was also silently dropped, so `pkm upgrade forge` answered
    "nothing to upgrade" with no reason given."""

    def _run(self, allow_downgrade=False):
        db = MagicMock()
        db.list_installed.return_value = [FORGE_INSTALLED]
        db.list_held.return_value = []
        args = argparse.Namespace(
            packages=["forge"], upgrade_all=False, upgrade_yes=True,
            upgrade_dry_run=True, allow_downgrade=allow_downgrade,
            ignore_holds=False, upgrade_security_only=False,
            quiet=False, verbose=False,
        )
        with patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.cli.PackageInstaller"):
            Repo.return_value.get_package.return_value = FORGE_MIRROR
            buf, err = io.StringIO(), io.StringIO()
            r = output.Reporter(stream=buf, err_stream=err)
            with patch("pkm.output._process_reporter", r):
                try:
                    with redirect_stdout(buf):
                        cli.cmd_upgrade(db, args)
                except SystemExit:
                    pass
            return buf.getvalue() + err.getvalue()

    def test_the_skip_is_explained_with_both_numbers(self):
        out = self._run()
        self.assertIn("1.0.0-133", out)
        self.assertIn("1.0.0-110", out)

    def test_the_explanation_names_the_override(self):
        self.assertIn("--allow-downgrade", self._run())


# ==========================================================================
# PART 2 — releases in every transaction line.
# ==========================================================================

class Part2ReleasesInTransactionLines(unittest.TestCase):
    """BASE BEHAVIOUR: transaction lines printed the version alone, so
    `forge 1.0.0` read identically whether it had just moved 133 -> 110 or
    110 -> 133."""

    def test_a_version_release_carries_both_numbers(self):
        self.assertEqual(txn.format_vr(FORGE_INSTALLED), "1.0.0-133")

    def test_a_missing_release_renders_the_schema_default(self):
        self.assertEqual(txn.format_vr({"version": "2.1"}), "2.1-1")

    def test_a_missing_version_is_visible_rather_than_omitted(self):
        self.assertEqual(txn.format_vr({}), "?")

    def test_a_change_names_both_sides_with_releases(self):
        self.assertEqual(
            txn.describe_change("forge", FORGE_INSTALLED, FORGE_MIRROR),
            "forge 1.0.0-133 -> 1.0.0-110")

    def test_a_helper_subject_names_the_payload_build_too(self):
        # The helper package's own version and the vendor build it fetched are
        # different things; printing only one is how they came to drift with
        # nothing on screen saying so.
        row = {"version": "1.132.0-1785860022", "release": 1,
               "payload_version": "1.132.0"}
        self.assertIn("(payload 1.132.0)", txn.describe_subject("vscode", row))

    def test_a_normal_package_gets_no_payload_clause(self):
        self.assertNotIn("payload", txn.describe_subject("forge", FORGE_INSTALLED))

    def test_the_reinstall_line_prints_both_releases(self):
        db = MagicMock()
        db.get_installed.return_value = FORGE_INSTALLED
        newer = dict(FORGE_MIRROR, release=140)
        with patch("pkm.cli.is_download_helper", return_value=False), \
             patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.PackageRemover") as Remover, \
             patch("pkm.cli.RepoManager") as Repo:
            Repo.return_value.get_package.return_value = newer
            Repo.return_value.download_package.return_value = (True, "/tmp/x")
            Installer.return_value._find_archive.return_value = None
            Remover.return_value.remove.return_value = (True, "removed")
            Installer.return_value.install.return_value = (True, "installed")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_reinstall(db, _reinstall_args("forge"))
        out = buf.getvalue()
        self.assertIn("1.0.0-133", out)
        self.assertIn("1.0.0-140", out)


# ==========================================================================
# PART 3 — already-installed routing BY RELEASE.
# ==========================================================================

class Part3AlreadyInstalledRouting(unittest.TestCase):
    """BASE BEHAVIOUR: a bare "already installed. Use 'pkm reinstall' to
    replace." — true, and the line that invited the downgrade, because it
    never said which side was newer."""

    def test_installed_newer_states_it_and_stops(self):
        state, msg = txn.installed_side("forge", FORGE_INSTALLED, FORGE_MIRROR)
        self.assertEqual(state, "installed-newer")
        self.assertIn("NEWER", msg)
        self.assertIn("1.0.0-133", msg)
        self.assertIn("1.0.0-110", msg)

    def test_installed_newer_warns_that_reinstall_would_go_backwards(self):
        _, msg = txn.installed_side("forge", FORGE_INSTALLED, FORGE_MIRROR)
        self.assertIn("--allow-downgrade", msg)

    def test_index_newer_routes_to_the_upgrade_path(self):
        newer = dict(FORGE_MIRROR, release=140)
        state, msg = txn.installed_side("forge", FORGE_INSTALLED, newer)
        self.assertEqual(state, "index-newer")
        self.assertIn("1.0.0-140", msg)

    def test_identical_says_so(self):
        same = dict(FORGE_MIRROR, release=133)
        state, _ = txn.installed_side("forge", FORGE_INSTALLED, same)
        self.assertEqual(state, "same")

    def test_no_bare_use_reinstall_advice_without_a_direction(self):
        # The exact shape of the line that caused this: advice to reinstall
        # with no statement of which side is newer.
        for candidate in (FORGE_MIRROR, dict(FORGE_MIRROR, release=140)):
            _, msg = txn.installed_side("forge", FORGE_INSTALLED, candidate)
            if "reinstall" in msg:
                self.assertTrue(
                    "NEWER" in msg or "matches" in msg,
                    f"advice to reinstall must state the direction: {msg}")

    def test_cmd_install_of_an_installed_package_states_the_direction(self):
        db = MagicMock()
        db.get_installed.return_value = FORGE_INSTALLED
        with patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli._print_transaction_next_steps"), \
             patch("pkm.cli.refresh_available_updates_after_transaction"):
            Repo.return_value.get_package.return_value = FORGE_MIRROR
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_install(db, _install_args("forge"))
        out = buf.getvalue()
        self.assertIn("1.0.0-110", out)
        self.assertIn("NEWER", out)
        # and it never reached the installer
        Installer.return_value.install.assert_not_called()


# ==========================================================================
# PART 4 — the retained-directories report, capped.
# ==========================================================================

class Part4RetainedReportCapped(unittest.TestCase):
    """BASE BEHAVIOUR: every owner of every retained entry was flattened into
    one comma-joined list — about 700 names for 18 directories, printed
    twice."""

    def _many(self, n_paths=18, n_owners=700):
        owners = [f"pkg{i:03d}" for i in range(n_owners)]
        return [(f"etc/shared{i}", owners) for i in range(n_paths)]

    def test_a_large_owner_set_is_not_enumerated(self):
        lines = txn.retained_report(self._many(), "directory", "directories")
        joined = "\n".join(lines)
        self.assertNotIn("pkg000", joined)
        self.assertNotIn("pkg699", joined)

    def test_the_counts_survive_the_cap(self):
        lines = txn.retained_report(self._many(), "directory", "directories")
        joined = "\n".join(lines)
        self.assertIn("18", joined)
        self.assertIn("700", joined)

    def test_the_report_is_one_line(self):
        self.assertEqual(len(txn.retained_report(self._many())), 1)

    def test_it_names_the_per_path_query(self):
        joined = "\n".join(txn.retained_report(self._many()))
        self.assertIn("pkm provides", joined)

    def test_a_small_owner_set_is_still_named(self):
        # The wall was never that owners were named — it was that all of them
        # always were. A handful is the useful answer and costs one line.
        lines = txn.retained_report([("etc/x", ["keeper"])])
        self.assertIn("keeper", "\n".join(lines))

    def test_verbose_lists_every_path_with_its_owners(self):
        lines = txn.retained_report(self._many(n_paths=3, n_owners=2),
                                    verbose=True)
        joined = "\n".join(lines)
        self.assertIn("/etc/shared0", joined)
        self.assertIn("/etc/shared2", joined)
        self.assertIn("pkg000", joined)

    def test_nothing_retained_prints_nothing(self):
        self.assertEqual(txn.retained_report([]), [])


# ==========================================================================
# PART 5 / PART 7 — deploy and remove listings, quiet by default.
# ==========================================================================

def _render(level, n_files, action="Deploy"):
    buf = io.StringIO()
    r = output.Reporter(level=level, stream=buf)
    r.file_list([f"usr/share/thing/f{i:04d}" for i in range(n_files)],
                action=action, pkg="thing")
    return buf.getvalue()


class Part5DeployAndRemoveQuietByDefault(unittest.TestCase):
    """BASE BEHAVIOUR: every path was listed inline for sets up to 50, which is
    how an ordinary multi-package transaction became a wall of paths that
    buried the lines a user actually reads."""

    def test_a_small_set_prints_one_line_and_no_paths(self):
        out = _render(output.NORMAL, 6)
        self.assertIn("Deploy:", out)
        self.assertNotIn("/usr/share/thing/f0000", out)
        self.assertEqual(len([l for l in out.splitlines() if l.strip()]), 1)

    def test_a_set_under_the_old_fifty_cap_prints_no_paths_either(self):
        # 30 is under the old inline cap of 50 and over the new threshold of
        # 25: base listed all thirty paths, and this is the case that proves
        # the rule changed rather than the number.
        out = _render(output.NORMAL, 30)
        self.assertNotIn("/usr/share/thing/f0000", out)

    def test_above_the_threshold_the_count_carries_the_directory_span(self):
        out = _render(output.NORMAL, 30)
        self.assertIn("30 files (1 directory)", out)

    def test_the_directory_rollup_is_kept_above_the_threshold(self):
        out = _render(output.NORMAL, 30)
        self.assertIn("/usr/share/thing/", out)

    def test_verbose_still_lists_every_path(self):
        out = _render(output.VERBOSE, 30)
        self.assertIn("/usr/share/thing/f0000", out)
        self.assertIn("/usr/share/thing/f0029", out)

    def test_no_per_package_hint_is_printed_under_a_block(self):
        # Hints belong once, in the transaction footer. A "re-run with -v"
        # under every package block is itself a wall.
        out = _render(output.NORMAL, 30)
        self.assertNotIn("re-run with -v", out)

    def test_remove_uses_the_same_shape(self):
        out = _render(output.NORMAL, 6, action="Remove")
        self.assertIn("Remove:", out)
        self.assertNotIn("/usr/share/thing/f0000", out)

    def test_the_threshold_is_one_number_shared_with_the_output_layer(self):
        self.assertEqual(output.FILE_LIST_INLINE_CAP, txn.DEPLOY_PATH_THRESHOLD)
        self.assertEqual(txn.DEPLOY_PATH_THRESHOLD, 25)


class Part7TransactionShape(unittest.TestCase):
    """The completion line, the counter, and the single footer."""

    def test_the_installed_line_carries_the_full_version_release(self):
        buf = io.StringIO()
        r = output.Reporter(stream=buf)
        r.installed("lib32-libXau", "1.0.12-2")
        self.assertIn("Installed", buf.getvalue())
        self.assertIn("lib32-libXau 1.0.12-2", buf.getvalue())

    def test_the_counter_appears_inside_a_transaction(self):
        buf = io.StringIO()
        r = output.Reporter(stream=buf)
        r.begin_transaction(40)
        r.installed("lib32-libXau", "1.0.12-2")
        r.installed("lib32-libxcb", "1.17.0-2")
        out = buf.getvalue()
        self.assertIn("[1/40]", out)
        self.assertIn("[2/40]", out)

    def test_no_counter_outside_a_transaction(self):
        buf = io.StringIO()
        r = output.Reporter(stream=buf)
        r.installed("bash", "5.2-1")
        self.assertNotIn("[", buf.getvalue())

    def test_the_completion_signal_is_not_duplicated_by_a_deploy_complete(self):
        buf = io.StringIO()
        r = output.Reporter(stream=buf)
        r.file_list(["usr/bin/x"], action="Deploy")
        r.installed("x", "1-1")
        self.assertNotIn("COMPLETE", buf.getvalue())

    def test_the_footer_prints_the_hint_exactly_once(self):
        buf = io.StringIO()
        r = output.Reporter(stream=buf)
        r.transaction_footer(count=40, installed_bytes=164358963)
        out = buf.getvalue()
        self.assertEqual(out.count("re-run with -v"), 1)
        self.assertIn("Installed 40 packages", out)

    def test_the_footer_hint_is_suppressed_at_verbose(self):
        buf = io.StringIO()
        r = output.Reporter(level=output.VERBOSE, stream=buf)
        r.transaction_footer(count=2)
        self.assertNotIn("re-run with -v", buf.getvalue())


# ==========================================================================
# PART 6 — the transaction confirmation gate, before any download.
# ==========================================================================

class Part6ConfirmationGate(unittest.TestCase):
    """BASE BEHAVIOUR: `sudo pkm install steam` resolved 40 packages and
    installed all of them with no confirmation of any kind."""

    def _plan(self, names=None, requested="steam"):
        names = names or STEAM_CLOSURE
        return txn.TransactionPlan(
            requested,
            [(n, _index_entry(n, size=1_100_000, installed_size=4_000_000))
             for n in names],
            action="Install",
        )

    def test_the_plan_counts_the_whole_closure(self):
        self.assertEqual(self._plan().count, 40)

    def test_the_summary_names_count_and_both_sizes(self):
        line = self._plan().summary_line()
        self.assertIn("40 packages", line)
        self.assertIn("download", line)
        self.assertIn("installed", line)

    def test_the_gate_fires_when_the_resolution_goes_beyond_the_named_package(self):
        self.assertTrue(self._plan().beyond_requested)

    def test_the_gate_does_not_fire_for_exactly_what_was_asked_for(self):
        self.assertFalse(self._plan(["steam"]).beyond_requested)

    def test_the_full_name_list_is_shown_not_a_sample(self):
        joined = " ".join(self._plan().name_list())
        self.assertIn("lib32-dep00", joined)
        self.assertIn("lib32-dep38", joined)
        self.assertIn("steam", joined)

    def test_the_name_list_carries_names_only(self):
        joined = " ".join(self._plan().name_list())
        self.assertNotIn("1.0-2", joined)
        self.assertNotIn("MiB", joined)

    def test_transaction_sizes_carry_a_tenth(self):
        # The ratified rendering shows 43.2 MiB / 156.7 MiB. The output
        # layer's default drops the decimal at or above 10, which turned
        # 156.7 into "157" — at transaction scale the tenth is what makes the
        # figure checkable against free space.
        self.assertEqual(output.human_size(164_358_963, precision=1),
                         "156.7 MiB")
        line = txn.TransactionPlan(
            "x", [("x", _index_entry("x", size=45_297_664,
                                     installed_size=164_358_963))]).summary_line()
        self.assertIn("43.2 MiB", line)
        self.assertIn("156.7 MiB", line)

    def test_other_callers_keep_the_existing_size_rendering(self):
        # The precision argument is opt-in; unrelated output is untouched.
        self.assertEqual(output.human_size(164_358_963), "157 MiB")

    def test_declining_returns_false(self):
        buf = io.StringIO()
        r = output.Reporter(stream=buf)
        with patch("builtins.input", return_value="n"):
            ok = txn.confirm(self._plan(), r, stdin=_FakeStdin(tty=True))
        self.assertFalse(ok)
        self.assertIn("cancelled", buf.getvalue().lower())

    def test_bare_return_accepts_because_the_default_is_yes(self):
        r = output.Reporter(stream=io.StringIO())
        with patch("builtins.input", return_value=""):
            self.assertTrue(txn.confirm(self._plan(), r,
                                        stdin=_FakeStdin(tty=True)))

    def test_an_interrupted_prompt_declines(self):
        r = output.Reporter(stream=io.StringIO())
        with patch("builtins.input", side_effect=EOFError):
            self.assertFalse(txn.confirm(self._plan(), r,
                                         stdin=_FakeStdin(tty=True)))

    def test_headless_states_the_acceptance_rather_than_asking(self):
        buf = io.StringIO()
        r = output.Reporter(stream=buf)
        with patch("builtins.input", side_effect=AssertionError(
                "headless must never call input()")):
            ok = txn.confirm(self._plan(), r, stdin=_FakeStdin(tty=False))
        self.assertTrue(ok)
        self.assertIn("no terminal attached", buf.getvalue())

    def test_yes_states_the_acceptance_rather_than_asking(self):
        r = output.Reporter(stream=io.StringIO())
        with patch("builtins.input", side_effect=AssertionError(
                "--yes must never call input()")):
            self.assertTrue(
                txn.confirm(self._plan(), r, assume_yes=True,
                            stdin=_FakeStdin(tty=True)))

    def test_the_forty_package_replay_prompts_before_any_download(self):
        """The base-failing case, end to end: the steam closure through
        cmd_install must reach the gate BEFORE download_package is called."""
        db = MagicMock()
        db.get_installed.return_value = None
        order = []

        def _download(name, reporter=None):
            order.append(("download", name))
            return (True, f"/tmp/{name}.tar.gz")

        def _input(prompt):
            order.append(("prompt", prompt))
            return "n"

        with patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli.is_download_helper", return_value=False), \
             patch("pkm.cli._print_transaction_next_steps"), \
             patch("pkm.cli.refresh_available_updates_after_transaction"), \
             patch("builtins.input", _input), \
             patch("sys.stdin", _FakeStdin(tty=True)):
            Installer.return_value._find_archive.return_value = None
            Installer.return_value.install.return_value = (
                False, "No archive found for steam")
            Repo.return_value.get_package.side_effect = \
                lambda n: _index_entry(n)
            Repo.return_value.resolve_dependencies.return_value = (
                True, list(STEAM_CLOSURE))
            Repo.return_value.download_package.side_effect = _download
            buf = io.StringIO()
            with self.assertRaises(SystemExit):
                with redirect_stdout(buf):
                    cli.cmd_install(db, _install_args("steam"))

        self.assertTrue(order, "the transaction produced no prompt and no download")
        self.assertEqual(
            order[0][0], "prompt",
            f"the gate must come before any download; got {order[:3]}")
        self.assertNotIn("download", [k for k, _ in order])


# ==========================================================================
# PART 8 — helper-package first-install routing.
# ==========================================================================

class Part8HelperFirstInstallContinues(unittest.TestCase):
    """BASE BEHAVIOUR: installing a helper package FROM THE REPO deployed the
    helper archive and stopped, reporting success with the payload absent.
    The routing check asks whether /usr/bin/igos-install-<name> is on disk,
    and on a first install it is not — it arrives inside the archive the
    transaction is about to deploy."""

    def _run(self, helper_after_install, payload_present):
        db = MagicMock()
        db.get_installed.return_value = None
        # is_download_helper answers False at the routing check (the helper
        # binary is not on disk yet) and `helper_after_install` afterwards.
        answers = iter([False, helper_after_install, helper_after_install])

        with patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli.is_download_helper",
                   side_effect=lambda n: next(answers, helper_after_install)), \
             patch("pkm.cli.payload_installed", return_value=payload_present), \
             patch("pkm.cli._proprietary_install") as prop, \
             patch("pkm.cli._print_transaction_next_steps"), \
             patch("pkm.cli.refresh_available_updates_after_transaction"), \
             patch("sys.stdin", _FakeStdin(tty=False)):
            Installer.return_value._find_archive.return_value = None
            Installer.return_value.install.side_effect = [
                (False, "No archive found for steam"), (True, "installed")]
            Repo.return_value.get_package.side_effect = \
                lambda n: _index_entry(n)
            Repo.return_value.resolve_dependencies.return_value = (
                True, ["steam"])
            Repo.return_value.download_package.return_value = (
                True, "/tmp/steam.tar.gz")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_install(db, _install_args("steam"))
            return prop, buf.getvalue()

    def test_the_transaction_continues_into_the_payload_flow(self):
        prop, _ = self._run(helper_after_install=True, payload_present=False)
        prop.assert_called_once()

    def test_it_does_not_stop_at_a_success_with_the_payload_absent(self):
        _, out = self._run(helper_after_install=True, payload_present=False)
        self.assertIn("not the application", out)

    def test_a_package_that_is_not_a_helper_is_unaffected(self):
        prop, _ = self._run(helper_after_install=False, payload_present=False)
        prop.assert_not_called()

    def test_an_already_present_payload_is_not_re_fetched(self):
        prop, _ = self._run(helper_after_install=True, payload_present=True)
        prop.assert_not_called()

    def test_the_cached_archive_path_continues_too(self):
        """THE SECOND DOOR. Installing from a verified cached archive reaches a
        completed install WITHOUT passing through the resolution block, so a
        helper installed from the cache would deploy and stop with the payload
        absent for exactly the same reason. Fixed at the mechanism, and this is
        the test that says so."""
        db = MagicMock()
        db.get_installed.return_value = None
        answers = iter([False, True, True])
        with patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli._sha256", return_value="a" * 64), \
             patch("pkm.cli.is_download_helper",
                   side_effect=lambda n: next(answers, True)), \
             patch("pkm.cli.payload_installed", return_value=False), \
             patch("pkm.cli._proprietary_install") as prop, \
             patch("pkm.cli._print_transaction_next_steps"), \
             patch("pkm.cli.refresh_available_updates_after_transaction"), \
             patch("sys.stdin", _FakeStdin(tty=False)):
            # A cached archive whose sha matches the signed index: the
            # top-of-loop path installs it and never reaches the resolution
            # block below.
            Installer.return_value._find_archive.return_value = Path(
                "/var/lib/igos/archives/steam-1.0.igos.tar.gz")
            Installer.return_value.install.return_value = (True, "installed")
            Repo.return_value.get_package.return_value = _index_entry("steam")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_install(db, _install_args("steam"))
        # the install did happen through the cached-archive path...
        Installer.return_value.install.assert_called_once()
        # ...and it did NOT stop with the payload absent.
        prop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
