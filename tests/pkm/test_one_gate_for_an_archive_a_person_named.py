#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""ONE GATE HOLDS AN ARCHIVE A PERSON NAMED, ON BOTH PATHS THAT TAKE ONE.

Two commands let someone install a file they chose themselves: `pkm install
<name> --archive <file>` and `pkm upgrade <name> --archive <file>`. Both hold it
to the same gate — compare its sha256 with the signed index's entry, then apply
the trust mode, where strict and repo-only require a match and loose proceeds
with a warning — and each carried its own copy of that. The second one's own
docstring said as much in words: "the trust gate `pkm install --archive`
applies, same modes, same words". Two implementations of one trust gate drift
apart silently, and a mode added to or tightened in one leaves the other weaker
with nothing saying so.

The gate is now `_explicit_archive_trust`, and these cases are what keeps it one
gate. They ask it directly for every standing and every mode; they assert that
BOTH paths ask it, with the gate replaced by a recorder so a path that decided
for itself would be caught; they inventory the source with its own parser rather
than by counting a string, so a comparison written the other way round cannot
slip past; and they hold each path to its own words, which are deliberately not
the same — only the upgrade path says "Nothing was changed", and only
`pkm install` prints both hashes when they disagree.

Nothing here reaches the machine running the suite: the pre-transaction restore
point and the update-advisory refresh are stubbed at the mechanism, every archive
is built in a temporary directory, and no install is performed.
"""

import ast
import io
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkm import cli

PKG = "demo"
INSTALLED_VERSION = "1.0"
ARCHIVE_VERSION = "2.0"
OTHER_SHA = "b" * 64


def _archive(directory: Path, version: str) -> Path:
    """A real .igos.tar.gz of PKG that a reader of .PKGINFO will accept."""
    lines = [f"pkgname={PKG}", f"pkgver={version}", "pkgrel=1",
             "pkgdesc=a package for the explicit-archive trust-gate cases",
             "license=GPL", "tier=extra", "builddate=2026-09-30T00:00:00Z",
             "size=64", "filecount=1"]
    out = directory / f"{PKG}-{version}.igos.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        data = ("\n".join(lines) + "\n").encode()
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        payload = b"x\n"
        info = tarfile.TarInfo(f"./usr/share/{PKG}/marker")
        info.size = len(payload)
        info.mode = 0o644
        tf.addfile(info, io.BytesIO(payload))
    return out


def _entry(sha256):
    """The signed index's entry for PKG, or None when it lists nothing."""
    if sha256 is None:
        return None
    return {"name": PKG, "version": ARCHIVE_VERSION, "release": 1, "sha256": sha256}


class TheGateItself(unittest.TestCase):
    """What `_explicit_archive_trust` answers, asked directly."""

    SHA = "a" * 64

    def test_the_gate_exists_as_one_named_function(self):
        self.assertTrue(callable(getattr(cli, "_explicit_archive_trust", None)),
                        "pkm.cli._explicit_archive_trust is the single copy of the "
                        "explicit-archive trust gate; it is not there")

    def test_an_archive_the_index_vouches_for_stands_matched_and_passes_every_mode(self):
        for mode in ("strict", "repo-only", "loose"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    cli._explicit_archive_trust(_entry(self.SHA), self.SHA, mode),
                    (cli.ARCHIVE_TRUST_MATCHES, True))

    def test_an_archive_the_index_contradicts_is_refused_unless_the_mode_is_loose(self):
        for mode, permitted in (("strict", False), ("repo-only", False), ("loose", True)):
            with self.subTest(mode=mode):
                self.assertEqual(
                    cli._explicit_archive_trust(_entry(OTHER_SHA), self.SHA, mode),
                    (cli.ARCHIVE_TRUST_CONTRADICTED, permitted))

    def test_an_archive_the_index_does_not_vouch_for_is_refused_unless_loose(self):
        # Two ways the index vouches for nothing: no entry at all, and an entry
        # with no hash in it. Both are the same fact to this gate.
        for entry in (None, {"name": PKG, "version": ARCHIVE_VERSION}):
            for mode, permitted in (("strict", False), ("repo-only", False),
                                    ("loose", True)):
                with self.subTest(entry=entry, mode=mode):
                    self.assertEqual(
                        cli._explicit_archive_trust(entry, self.SHA, mode),
                        (cli.ARCHIVE_TRUST_UNVOUCHED, permitted))

    def test_the_two_modes_that_require_a_match_are_named_not_inferred(self):
        self.assertEqual(tuple(cli.ARCHIVE_TRUST_MODES_REQUIRING_A_MATCH),
                         ("strict", "repo-only"))

    def test_an_unrecognised_mode_permits_which_is_what_both_copies_did(self):
        """PRESERVED, not endorsed.

        Neither copy refused under a mode that was not strict or repo-only, and
        this extraction changes no behaviour, so neither does the gate. The
        parser admits only the three modes, so no caller reaches it; the case is
        here so that the day someone closes it, the change is deliberate and
        this line is what they edit.
        """
        for mode in (None, "", "banana"):
            with self.subTest(mode=mode):
                standing, permitted = cli._explicit_archive_trust(
                    _entry(OTHER_SHA), self.SHA, mode)
                self.assertEqual(standing, cli.ARCHIVE_TRUST_CONTRADICTED)
                self.assertTrue(permitted)


class _BothPaths:
    """The two paths that take an archive a person named. A mixin, not a suite."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pkm-trust-gate-")
        self.dir = Path(self._tmp.name)
        self.archive = _archive(self.dir, ARCHIVE_VERSION)
        self.archive_sha = cli._sha256(str(self.archive))
        self.said = ""

    def tearDown(self):
        self._tmp.cleanup()

    def _install_args(self, mode):
        return cli.build_parser().parse_args(
            ["install", PKG, "--archive", str(self.archive),
             "--archive-trust", mode, "--yes"])

    def _upgrade_args(self, mode):
        return cli.build_parser().parse_args(
            ["upgrade", PKG, "--archive", str(self.archive),
             "--archive-trust", mode])

    def _drive_install(self, index_entry, mode="strict"):
        """`pkm install <name> --archive <file>`."""
        db = MagicMock()
        db.get_installed.return_value = None
        db.list_held.return_value = []
        out, err = io.StringIO(), io.StringIO()
        # Nothing of the machine running the suite: no restore point is asked of
        # its Chronicle engine and its update advisory is not refreshed.
        with patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli.refresh_available_updates_after_transaction"), \
             patch("pkm.cli.is_download_helper", return_value=False), \
             patch("pkm.cli.helper_is_present", return_value=False), \
             patch("pkm.cli.refuse_unrunnable_package_hook", return_value=None), \
             patch("pkm.cli._proprietary_install"), \
             patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.RepoManager") as Repo:
            Installer.return_value.install.return_value = (True, "installed")
            Installer.return_value._find_archive.return_value = None
            Repo.return_value.get_package.return_value = index_entry
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.cmd_install(db, self._install_args(mode))
                except SystemExit:
                    pass
            self.said = " ".join((out.getvalue() + " " + err.getvalue()).split())
            return Installer.return_value.install.call_args

    def _drive_upgrade(self, index_entry, mode="strict"):
        """`pkm upgrade <name> --archive <file>`, at its candidate step."""
        db = MagicMock()
        db.get_installed.return_value = {
            "name": PKG, "version": INSTALLED_VERSION, "release": 1}
        db.list_held.return_value = []
        db.get_installed_files.return_value = []
        repo = MagicMock()
        repo.get_package.return_value = index_entry
        out, err = io.StringIO(), io.StringIO()
        result = None
        with redirect_stdout(out), redirect_stderr(err):
            try:
                result = cli._local_archive_upgrade_candidates(
                    db, repo, self._upgrade_args(mode), str(self.archive))
            except SystemExit:
                result = None
        self.said = " ".join((out.getvalue() + " " + err.getvalue()).split())
        return result


class BothPathsAskTheOneGate(_BothPaths, unittest.TestCase):
    """The routing claim: neither path decides for itself any more."""

    def _recorded(self, site, index_entry, answer, mode="strict"):
        calls = []

        def recorder(entry, sha, trust_mode):
            calls.append((entry, sha, trust_mode))
            return answer

        with patch("pkm.cli._explicit_archive_trust", side_effect=recorder):
            site(index_entry, mode)
        return calls

    def test_pkm_install_with_an_archive_asks_the_gate(self):
        calls = self._recorded(self._drive_install, _entry(self.archive_sha),
                               (cli.ARCHIVE_TRUST_MATCHES, True))
        self.assertEqual(len(calls), 1, f"pkm install asked the gate {len(calls)} times")
        self.assertEqual(calls[0][0], _entry(self.archive_sha))
        self.assertEqual(calls[0][1], self.archive_sha)
        self.assertEqual(calls[0][2], "strict")

    def test_pkm_upgrade_with_an_archive_asks_the_gate(self):
        calls = self._recorded(self._drive_upgrade, _entry(self.archive_sha),
                               (cli.ARCHIVE_TRUST_MATCHES, True))
        self.assertEqual(len(calls), 1, f"pkm upgrade asked the gate {len(calls)} times")
        self.assertEqual(calls[0][0], _entry(self.archive_sha))
        self.assertEqual(calls[0][1], self.archive_sha)
        self.assertEqual(calls[0][2], "strict")

    def test_neither_path_decides_for_itself(self):
        """The source read by a parser, not by counting a string.

        A comparison written the other way round, or with the operands renamed,
        would pass a textual count; this walks the module's own syntax tree for
        every comparison against an index entry's sha256 and asserts the only one
        that decides trust for a named archive lives in the gate.
        """
        source = Path(cli.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        def entry_sha_operand(node):
            """True for `<something>.get("sha256")`."""
            return (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "sha256")

        found = []
        for function in [n for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            # A comparison need not carry the lookup in its own operands: the
            # entry's hash is usually bound to a name a line earlier. Follow
            # those names, or a copy written that way would not be seen — the
            # very blind spot a textual count has.
            holds_entry_sha = set()
            for node in ast.walk(function):
                if isinstance(node, ast.Assign) and entry_sha_operand(node.value):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            holds_entry_sha.add(target.id)
                if isinstance(node, ast.NamedExpr) and entry_sha_operand(node.value):
                    holds_entry_sha.add(node.target.id)
            for node in ast.walk(function):
                if not isinstance(node, ast.Compare):
                    continue
                operands = [node.left] + list(node.comparators)
                looks_at_the_entrys_hash = any(
                    entry_sha_operand(o)
                    or (isinstance(o, ast.Name) and o.id in holds_entry_sha)
                    for o in operands)
                if looks_at_the_entrys_hash and any(
                        isinstance(op, ast.Eq) for op in node.ops):
                    found.append((function.name, node.lineno,
                                  " ".join(ast.get_source_segment(source, node).split())))

        deciders = sorted({name for name, _line, _src in found})
        self.assertEqual(
            deciders, ["_explicit_archive_trust", "_trusted_cached_archive"],
            "a function other than the two rules compares an index entry's "
            f"sha256 for equality: {found!r}")

        callers = sorted({function.name
                          for function in ast.walk(tree)
                          if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
                          for node in ast.walk(function)
                          if isinstance(node, ast.Call)
                          and isinstance(node.func, ast.Name)
                          and node.func.id == "_explicit_archive_trust"})
        self.assertEqual(callers, ["_local_archive_upgrade_candidates", "cmd_install"],
                         f"the gate is called from {callers!r}, not from exactly the "
                         f"two paths that take an archive a person named")


class EachPathKeepsItsOwnWords(_BothPaths, unittest.TestCase):
    """A control per path and per mode, with the real gate in place."""

    def test_a_matching_archive_is_accepted_on_both_paths(self):
        call = self._drive_install(_entry(self.archive_sha))
        self.assertIsNotNone(call, f"pkm install refused a matching archive: {self.said}")
        self.assertIn("sha256 matches repository index", self.said)

        queue = self._drive_upgrade(_entry(self.archive_sha))
        self.assertTrue(queue, f"pkm upgrade refused a matching archive: {self.said}")
        self.assertIn("sha256 matches the repository index", self.said)

    def test_a_contradicted_archive_is_refused_under_strict_on_both_paths(self):
        self._drive_install(_entry(OTHER_SHA))
        self.assertIn("--archive-trust=strict requires", self.said)
        # Only this path prints both hashes when they disagree.
        self.assertIn("archive SHA256 does not match repository index!", self.said)
        self.assertIn(self.archive_sha, self.said)
        self.assertIn(OTHER_SHA, self.said)
        self.assertNotIn("Nothing was changed", self.said)

        queue = self._drive_upgrade(_entry(OTHER_SHA))
        self.assertIsNone(queue)
        self.assertIn("--archive-trust=strict requires", self.said)
        # Only this path stops the command and says so.
        self.assertIn("Nothing was changed", self.said)

    def test_an_unvouched_archive_is_refused_under_repo_only_on_both_paths(self):
        self._drive_install(None, mode="repo-only")
        self.assertIn("--archive-trust=repo-only requires", self.said)

        queue = self._drive_upgrade(None, mode="repo-only")
        self.assertIsNone(queue)
        self.assertIn("--archive-trust=repo-only requires", self.said)
        self.assertIn("Nothing was changed", self.said)

    def test_loose_proceeds_with_its_warning_on_both_paths(self):
        call = self._drive_install(_entry(OTHER_SHA), mode="loose")
        self.assertIsNotNone(call, f"loose refused an archive: {self.said}")
        self.assertIn("--archive-trust=loose", self.said)
        self.assertIn("skipping repo verification", self.said)

        queue = self._drive_upgrade(_entry(OTHER_SHA), mode="loose")
        self.assertTrue(queue, f"loose refused an archive: {self.said}")
        self.assertIn("skipping repo verification", self.said)


if __name__ == "__main__":
    unittest.main()
