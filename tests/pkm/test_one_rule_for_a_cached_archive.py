#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""ONE RULE DECIDES WHETHER A CACHED ARCHIVE MAY BE USED, ON EVERY PATH.

Three commands can reach an archive that is already sitting in the install
root's archive directory: the lay-down step that installs a download helper's
own package, a bare `pkm install`, and `pkm reinstall`. Each one had its own copy
of the rule that says when such a file may be trusted — read the signed index,
compare the sha256, hand that sha256 to `install()` so the install-time re-hash
guards it, otherwise fetch the verified package. Three copies of one security
rule drift apart silently, and a tightening applied to two of them leaves the
third weaker with nothing saying so.

The rule is now `_trusted_cached_archive`, and these cases are what keeps it one
rule. They assert it exists, that it decides correctly on its own, that EVERY one
of the three paths asks it rather than deciding for itself, and that each path
still says what it said before — because those sentences are deliberately not
identical: only `pkm install`, where a person can have named an archive
themselves, names the deliberate-local-install command, and that advice must not
spread to a download helper's path where there is nothing for it to mean.

Nothing here touches a network, a real store or the machine's own root: the
index entry, the cached path and the file's hash are the three answers the rule
asks for, and each case supplies them.
"""

import argparse
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkm import cli

PKG = "demo"
CACHED = Path("/var/lib/igos/archives/demo-1.0.igos.tar.gz")
DOWNLOADED = "/var/cache/pkm/pkgs/demo-1.0.igos.tar.gz"
LOCAL_SHA = "a" * 64
INDEX_SHA_OTHER = "b" * 64


def _entry(sha256, payload_license="LicenseRef-Demo"):
    """The signed index's entry for PKG, or None when it lists nothing.

    `payload_license` is what routes a package down the download-helper path, so
    the two command drivers below ask for an entry without one: they are the
    paths a package with no vendor payload takes.
    """
    if sha256 is None:
        return None
    entry = {"name": PKG, "version": "1.0", "release": 1, "sha256": sha256}
    if payload_license is not None:
        entry["payload_license"] = payload_license
    return entry


def _recording_reporter():
    """A Reporter writing into strings, so a case can read what a path said."""
    import io as _io
    out, err = _io.StringIO(), _io.StringIO()
    reporter = cli.Reporter(stream=out, err_stream=err)
    reporter._said = (out, err)
    return reporter


def _said(reporter):
    out, err = reporter._said
    return " ".join((out.getvalue() + " " + err.getvalue()).split())


class TheRuleItself(unittest.TestCase):
    """What `_trusted_cached_archive` answers, asked directly."""

    def test_the_rule_exists_as_one_named_function(self):
        # The case that keeps the three copies from coming back: if this name
        # disappears, the rule has been inlined somewhere again.
        self.assertTrue(callable(getattr(cli, "_trusted_cached_archive", None)),
                        "pkm.cli._trusted_cached_archive is the single copy of "
                        "the cached-archive rule; it is not there")

    def test_an_archive_the_index_vouches_for_is_trusted_with_the_index_hash(self):
        with patch("pkm.cli._sha256", return_value=LOCAL_SHA):
            self.assertEqual(
                cli._trusted_cached_archive(CACHED, _entry(LOCAL_SHA)),
                (CACHED, LOCAL_SHA))

    def test_an_archive_the_index_contradicts_is_not_trusted(self):
        with patch("pkm.cli._sha256", return_value=LOCAL_SHA):
            self.assertIsNone(
                cli._trusted_cached_archive(CACHED, _entry(INDEX_SHA_OTHER)))

    def test_an_archive_the_index_does_not_list_is_not_trusted(self):
        with patch("pkm.cli._sha256", return_value=LOCAL_SHA):
            self.assertIsNone(cli._trusted_cached_archive(CACHED, None))

    def test_an_index_entry_with_no_hash_is_not_a_reason_to_trust_anything(self):
        with patch("pkm.cli._sha256", return_value=LOCAL_SHA):
            self.assertIsNone(
                cli._trusted_cached_archive(CACHED, {"name": PKG, "version": "1.0"}))

    def test_no_archive_is_no_decision(self):
        with patch("pkm.cli._sha256", return_value=LOCAL_SHA) as sha:
            self.assertIsNone(cli._trusted_cached_archive(None, _entry(LOCAL_SHA)))
        self.assertEqual(sha.call_count, 0)

    def test_an_unlisted_archive_is_not_even_read(self):
        """CONTROL on the order of the two tests: the file is hashed only once
        the index has offered a hash to compare it against."""
        with patch("pkm.cli._sha256", return_value=LOCAL_SHA) as sha:
            cli._trusted_cached_archive(CACHED, None)
        self.assertEqual(sha.call_count, 0)


def _install_args(*packages):
    """The real parser's own namespace for `pkm install`, not a hand-made one."""
    return cli.build_parser().parse_args(["install", *packages, "--yes"])


def _reinstall_args(*packages):
    # `reinstall` takes no --yes: it is the parser's own namespace either way.
    return cli.build_parser().parse_args(["reinstall", *packages])


class _ThreePaths:
    """The three cached-archive paths, each driven far enough to read its
    decision. A mixin and not a TestCase, so the two suites below each carry
    only their own cases.
    """

    def _drive(self, site, index_entry, trusted, archive=CACHED):
        """Run `site` with the rule replaced by a recorder; return its calls."""
        calls = []

        def recorder(arch, entry):
            calls.append((arch, entry))
            return trusted

        with patch("pkm.cli._trusted_cached_archive", side_effect=recorder):
            site(index_entry, archive)
        return calls

    # -- the three drivers ------------------------------------------------

    def _drive_helper_lay_down(self, index_entry, archive):
        """The lay-down step of a download helper's own package."""
        db = MagicMock()
        db.get_installed.return_value = None
        installer = MagicMock()
        installer._find_archive.return_value = archive
        installer.install.return_value = (True, "installed")
        installer._find_helper.return_value = Path(f"/usr/bin/igos-install-{PKG}")
        installer._run_helper.return_value = (
            True, f"{PKG}: the application is installed", False)
        repo = MagicMock()
        repo.get_package.return_value = index_entry
        repo.download_package.return_value = (True, DOWNLOADED)
        reporter = _recording_reporter()
        with patch("pkm.cli.helper_payload_present", return_value=True), \
             patch("pkm.cli.acceptance_record_exists", return_value=True), \
             patch("pkm.cli._sha256", return_value=LOCAL_SHA), \
             patch("pkm.cli.PackageRemover"):
            try:
                cli._proprietary_install(db, installer, repo, reporter, PKG,
                                         "LicenseRef-Demo")
            except SystemExit:
                pass
        self._said = _said(reporter)
        return installer.install.call_args

    def _drive_install(self, index_entry, archive):
        db = MagicMock()
        db.get_installed.return_value = None
        # Nothing of the machine running the suite: no restore point is asked
        # of its Chronicle engine, and its update advisory is not refreshed.
        with patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli.refresh_available_updates_after_transaction"), \
             patch("pkm.cli.is_download_helper", return_value=False), \
             patch("pkm.cli.helper_is_present", return_value=False), \
             patch("pkm.cli.refuse_unrunnable_package_hook", return_value=None), \
             patch("pkm.cli._proprietary_install"), \
             patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.cli._sha256", return_value=LOCAL_SHA):
            Installer.return_value._find_archive.return_value = archive
            Installer.return_value.install.return_value = (True, "installed")
            Repo.return_value.get_package.return_value = index_entry
            Repo.return_value.download_package.return_value = (True, DOWNLOADED)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.cmd_install(db, _install_args(PKG))
                except SystemExit:
                    pass
            self._said = " ".join((out.getvalue() + " " + err.getvalue()).split())
            return Installer.return_value.install.call_args

    def _drive_reinstall(self, index_entry, archive):
        db = MagicMock()
        db.get_installed.return_value = {"name": PKG, "version": "1.0"}
        # Nothing of the machine running the suite: no restore point is asked
        # of its Chronicle engine, and its update advisory is not refreshed.
        with patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli.refresh_available_updates_after_transaction"), \
             patch("pkm.cli.is_download_helper", return_value=False), \
             patch("pkm.cli.helper_is_present", return_value=False), \
             patch("pkm.cli._proprietary_install"), \
             patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.PackageRemover") as Remover, \
             patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.cli._sha256", return_value=LOCAL_SHA):
            Installer.return_value._find_archive.return_value = archive
            Installer.return_value.install.return_value = (True, "installed")
            Remover.return_value.remove.return_value = (True, "removed")
            Repo.return_value.get_package.return_value = index_entry
            Repo.return_value.download_package.return_value = (True, DOWNLOADED)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.cmd_reinstall(db, _reinstall_args(PKG))
                except SystemExit:
                    pass
            self._said = " ".join((out.getvalue() + " " + err.getvalue()).split())
            return Installer.return_value.install.call_args

    # -- the routing cases ------------------------------------------------


class EveryPathAsksTheOneRule(_ThreePaths, unittest.TestCase):
    """The routing claim: no path decides for itself any more."""

    def test_pkm_install_asks_the_rule(self):
        calls = self._drive(self._drive_install, _entry(LOCAL_SHA, None), None)
        self.assertEqual(len(calls), 1, f"pkm install asked the rule {len(calls)} times")
        self.assertEqual(str(calls[0][0]), str(CACHED))
        self.assertEqual(calls[0][1], _entry(LOCAL_SHA, None))

    def test_pkm_reinstall_asks_the_rule(self):
        calls = self._drive(self._drive_reinstall, _entry(LOCAL_SHA, None), None)
        self.assertEqual(len(calls), 1, f"pkm reinstall asked the rule {len(calls)} times")
        self.assertEqual(str(calls[0][0]), str(CACHED))
        self.assertEqual(calls[0][1], _entry(LOCAL_SHA, None))


    def test_the_helper_lay_down_step_asks_the_rule(self):
        calls = self._drive(self._drive_helper_lay_down, _entry(LOCAL_SHA), None)
        self.assertEqual(len(calls), 1,
                         f"the lay-down step asked the rule {len(calls)} times")
        self.assertEqual(str(calls[0][0]), str(CACHED))
        self.assertEqual(calls[0][1], _entry(LOCAL_SHA))

    def test_no_path_decides_for_itself(self):
        """The three above are every cached-archive path there is: the decisive
        comparison appears once in the module and only inside the rule."""
        source = Path(cli.__file__).read_text(encoding="utf-8")
        self.assertEqual(
            source.count('== _sha256('), 1,
            "the cached-archive comparison appears more than once in pkm/cli.py, "
            "so a path is deciding for itself again")
        self.assertIn("def _trusted_cached_archive(", source)
        self.assertEqual(source.count("_trusted_cached_archive(") - 1, 3,
                         "the rule is not called by exactly the three paths")

class EachPathKeepsItsOwnOutcomeAndItsOwnSentence(_ThreePaths, unittest.TestCase):
    """A control per path, with the real rule in place.

    The three outcomes are the whole of the rule: an archive the index vouches
    for is used with the index's hash, one the index contradicts is not, and one
    the index does not list is not. The sentences are what a person reads, and
    they are deliberately not identical — which is why each is asserted at its
    own path rather than once for all three.
    """

    SITES = ("helper lay-down", "pkm install", "pkm reinstall")

    def _site(self, name):
        return {"helper lay-down": self._drive_helper_lay_down,
                "pkm install": self._drive_install,
                "pkm reinstall": self._drive_reinstall}[name]

    def _run(self, name, index_entry):
        """Drive one path with the REAL rule; return (install kwargs, what it said)."""
        self._said = ""
        call = self._site(name)(index_entry, CACHED)
        kwargs = dict(call.kwargs) if call else {}
        if call and call.args:
            kwargs["_positional"] = call.args
        return kwargs, self._said

    def test_an_archive_the_index_vouches_for_is_used_with_the_index_hash(self):
        for name in self.SITES:
            with self.subTest(site=name):
                entry = _entry(LOCAL_SHA, None if name != "helper lay-down"
                               else "LicenseRef-Demo")
                kwargs, said = self._run(name, entry)
                self.assertEqual(str(kwargs.get("archive_path")), str(CACHED),
                                 f"{name} did not install the cached archive: {kwargs!r}")
                self.assertEqual(kwargs.get("expected_sha256"), LOCAL_SHA,
                                 f"{name} installed it without the index's hash, so "
                                 f"the install-time re-hash could not guard it")
                self.assertIn("matches the signed index", said)

    def test_an_archive_the_index_contradicts_is_not_used(self):
        for name in self.SITES:
            with self.subTest(site=name):
                entry = _entry(INDEX_SHA_OTHER, None if name != "helper lay-down"
                               else "LicenseRef-Demo")
                kwargs, said = self._run(name, entry)
                self.assertNotEqual(str(kwargs.get("archive_path")), str(CACHED),
                                    f"{name} installed an archive the index "
                                    f"contradicts: {kwargs!r}")
                self.assertIn("does not match the signed index", said)

    def test_an_archive_the_index_does_not_list_is_not_used(self):
        for name in self.SITES:
            with self.subTest(site=name):
                entry = {"name": PKG, "version": "1.0"}
                if name == "helper lay-down":
                    entry["payload_license"] = "LicenseRef-Demo"
                kwargs, said = self._run(name, entry)
                self.assertNotEqual(str(kwargs.get("archive_path")), str(CACHED),
                                    f"{name} installed an unlisted archive: {kwargs!r}")
                self.assertIn("is not in the signed index", said)

    def test_only_the_path_where_a_person_can_name_an_archive_offers_the_bypass(self):
        """The wording divergence, pinned.

        `pkm install` is the one command where a person can have named an archive
        themselves, so its unlisted-archive warning tells them how to install a
        local file deliberately. On a download helper's own package there is
        nothing for that advice to mean, and advertising a way around the signed
        index on a path that never needed one would be a quiet weakening. The
        divergence is deliberate and this is what keeps it from being tidied away
        in either direction.
        """
        entries = {"helper lay-down": {"name": PKG, "version": "1.0",
                                       "payload_license": "LicenseRef-Demo"},
                   "pkm install": {"name": PKG, "version": "1.0"},
                   "pkm reinstall": {"name": PKG, "version": "1.0"}}
        said = {name: self._run(name, entries[name])[1] for name in self.SITES}
        self.assertIn("--archive-trust", said["pkm install"])
        for name in ("helper lay-down", "pkm reinstall"):
            with self.subTest(site=name):
                self.assertNotIn("--archive-trust", said[name],
                                 f"{name} now advertises a way around the signed "
                                 f"index; it never needed one")


if __name__ == "__main__":
    unittest.main()
