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

Since 2026-09-30 the gate also FAILS CLOSED: it permits only under a mode this
build recognises, and refuses a mode it cannot interpret — None, an empty string,
a misspelling, a fourth mode added to the command line and not to the rule —
whatever the index says of the file. It permitted all of those before, match or no
match, and because the two paths defaulted a MISSING mode differently (one took
`getattr(args, "archive_trust", "strict")`, which is None when the attribute
carries nothing; the other coerced the same expression with `or "strict"`) one
command line could mean two things: an archive the index contradicts was installed
by one path and refused by the other. Both halves are held here — the gate's
answer for every mode, and that both paths now take a missing mode from the one
place that defaults it, `_archive_trust_mode`.

Nothing here reaches the machine running the suite: the pre-transaction restore
point and the update-advisory refresh are stubbed at the mechanism, every archive
is built in a temporary directory, and no install is performed.
"""

import argparse
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


# "the args object carries no archive_trust attribute at all", as distinct from
# "it carries None". Both are a mode nobody named; they arrive by different
# accidents, and a default that covered one and not the other would still split.
MISSING = object()


def _with_trust_mode(args, mode):
    """`args` with the trust mode a command line could not have supplied.

    argparse's own `choices` refuses an unrecognised mode at the command line —
    that is the outer guard, and these cases exercise the inner one, which is
    what a caller that builds its args itself, or a later subcommand that adds the
    flag without its default, actually meets.
    """
    if mode is MISSING:
        if hasattr(args, "archive_trust"):
            delattr(args, "archive_trust")
    else:
        setattr(args, "archive_trust", mode)
    return args


def _archive_trust_choices():
    """What the command line itself admits for --archive-trust, per subcommand.

    Read back off the built parser, because the point of the case below is that
    nobody has to trust a tuple in the source to match the flag a person can
    actually type. argparse offers no public way to do it.
    """
    found = {}
    for action in cli.build_parser()._actions:
        subcommands = getattr(action, "choices", None)
        if not isinstance(subcommands, dict):
            continue
        for name, sub in subcommands.items():
            for sub_action in getattr(sub, "_actions", []):
                if "--archive-trust" in (sub_action.option_strings or []):
                    found[name] = (tuple(sub_action.choices), sub_action.default)
    return found


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

    def test_an_unrecognised_mode_is_refused_whatever_the_index_says(self):
        """FAIL CLOSED (2026-09-30), where it permitted before.

        A mode this build cannot interpret is not a decision the gate can honour,
        and on a gate whose subject is what may be installed the answer to a mode
        it does not know has to be "no" — including for an archive the index
        vouches for, where permitting would read the person's unknown intent as
        consent. The standing still travels, so the caller can report what the
        index thought of the file as well as the refusal.
        """
        for mode in (None, "", "banana", "STRICT", "repo_only", "Loose"):
            for entry, standing in (
                    (_entry(self.SHA), cli.ARCHIVE_TRUST_MATCHES),
                    (_entry(OTHER_SHA), cli.ARCHIVE_TRUST_CONTRADICTED),
                    (None, cli.ARCHIVE_TRUST_UNVOUCHED)):
                with self.subTest(mode=mode, standing=standing):
                    self.assertEqual(
                        cli._explicit_archive_trust(entry, self.SHA, mode),
                        (standing, False),
                        f"the gate permitted under mode {mode!r}")

    def test_the_modes_the_gate_recognises_are_the_modes_the_command_line_admits(self):
        """The one assumption fail-closed rests on, measured against the parser.

        The gate now refuses what it does not recognise, so a fourth mode added to
        `--archive-trust` and not to ARCHIVE_TRUST_MODES would be refused on every
        archive on both paths. That is the safe direction of the two, and it is
        still a break; this case is what says so out loud, before a person meets
        it. The command line's own default is held to the same one place.
        """
        offered = _archive_trust_choices()
        self.assertEqual(sorted(offered), ["install", "upgrade"],
                         f"--archive-trust is offered by {sorted(offered)}, and "
                         f"this case knows about install and upgrade")
        for subcommand, (choices, default) in sorted(offered.items()):
            with self.subTest(subcommand=subcommand):
                self.assertEqual(
                    sorted(choices), sorted(cli.ARCHIVE_TRUST_MODES),
                    f"`pkm {subcommand} --archive-trust` admits {sorted(choices)}, "
                    f"the gate recognises {sorted(cli.ARCHIVE_TRUST_MODES)}; a mode "
                    f"in the first and not the second is refused on every archive")
                self.assertEqual(default, cli.ARCHIVE_TRUST_DEFAULT_MODE)

    def test_every_mode_that_requires_a_match_is_one_the_gate_recognises(self):
        for mode in cli.ARCHIVE_TRUST_MODES_REQUIRING_A_MATCH:
            with self.subTest(mode=mode):
                self.assertIn(mode, cli.ARCHIVE_TRUST_MODES)


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

    def _args_for(self, argv, mode):
        """Args for one site, carrying a mode a command line may not be able to.

        Whether it can is asked of the PARSER rather than of a constant in the
        product: these helpers are the harness, and a harness that imported the
        rule it exercises could not run at this lane's parent at all — the
        no-regression legs below have to run there, or they measure nothing.
        """
        admitted = {name
                    for choices, _default in _archive_trust_choices().values()
                    for name in choices}
        if isinstance(mode, str) and mode in admitted:
            return cli.build_parser().parse_args(
                argv + ["--archive-trust", mode])
        return _with_trust_mode(
            cli.build_parser().parse_args(argv + ["--archive-trust", "strict"]),
            mode)

    def _install_args(self, mode):
        return self._args_for(
            ["install", PKG, "--archive", str(self.archive), "--yes"], mode)

    def _upgrade_args(self, mode):
        return self._args_for(
            ["upgrade", PKG, "--archive", str(self.archive)], mode)

    def _recorded(self, site, index_entry, answer, mode="strict"):
        """Drive one site with the gate replaced by a recorder."""
        calls = []

        def recorder(entry, sha, trust_mode):
            calls.append((entry, sha, trust_mode))
            return answer

        with patch("pkm.cli._explicit_archive_trust", side_effect=recorder):
            site(index_entry, mode)
        return calls

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
        """Follow literal sha256 subscripts/get calls with any default, names
        bound from expressions containing them, ==, !=, in/not in, and digest
        comparisons. This is a conservative syntax inventory, not execution
        or general data-flow analysis; other dynamic forms are not certified.
        """
        source = Path(cli.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        def scope_nodes(function):
            pending = list(function.body)
            while pending:
                node = pending.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                    continue
                yield node
                pending.extend(ast.iter_child_nodes(node))

        def entry_sha_lookup(node):
            key = None
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and node.args):
                key = node.args[0]
            elif isinstance(node, ast.Subscript):
                key = node.slice
            return isinstance(key, ast.Constant) and key.value == "sha256"

        found = []
        for function in [n for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            nodes = list(scope_nodes(function))
            holds_entry_sha = set()

            def has_entry_sha(expression):
                return any(entry_sha_lookup(n) or
                           (isinstance(n, ast.Name) and n.id in holds_entry_sha)
                           for n in ast.walk(expression))

            # A finite fixed point follows assignments through any containing
            # expression and through further names, including cycles.
            changed = True
            while changed:
                previous = set(holds_entry_sha)
                for node in nodes:
                    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                        if node.value is None or not has_entry_sha(node.value):
                            continue
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        for target in targets:
                            if isinstance(target, ast.Name):
                                holds_entry_sha.add(target.id)
                changed = previous != holds_entry_sha
            for node in nodes:
                operands = []
                if isinstance(node, ast.Compare) and any(
                        isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn))
                        for op in node.ops):
                    operands = [node.left, *node.comparators]
                elif isinstance(node, ast.Call) and (
                        isinstance(node.func, ast.Attribute) and node.func.attr == "compare_digest"
                        or isinstance(node.func, ast.Name) and node.func.id == "compare_digest"):
                    operands = node.args
                if any(has_entry_sha(operand) for operand in operands):
                    found.append((function.name, node.lineno,
                                  " ".join(ast.get_source_segment(source, node).split())))
        deciders = sorted({name for name, _line, _src in found})
        self.assertEqual(
            deciders, ["_explicit_archive_trust", "_trusted_cached_archive"],
            f"an additional function compares an entry's sha256: {found!r}")

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


class OneDefaultForAMissingMode(unittest.TestCase):
    """A mode nobody named means one thing, and one place says what."""

    def test_the_default_is_one_named_constant_the_gate_recognises(self):
        self.assertEqual(cli.ARCHIVE_TRUST_DEFAULT_MODE, "strict")
        self.assertIn(cli.ARCHIVE_TRUST_DEFAULT_MODE, cli.ARCHIVE_TRUST_MODES)

    def test_a_mode_that_is_absent_or_unset_is_the_default(self):
        for mode in (MISSING, None):
            with self.subTest(mode=mode):
                args = _with_trust_mode(argparse.Namespace(), mode)
                self.assertEqual(cli._archive_trust_mode(args),
                                 cli.ARCHIVE_TRUST_DEFAULT_MODE)

    def test_a_mode_that_was_named_is_returned_as_it_came(self):
        """An empty string included: it is unrecognised, not missing, and the
        gate refuses it. Reading it as the default would turn an unusable value
        into a permission silently."""
        for mode in ("strict", "repo-only", "loose", "banana", ""):
            with self.subTest(mode=mode):
                self.assertEqual(
                    cli._archive_trust_mode(
                        argparse.Namespace(archive_trust=mode)),
                    mode)

    def test_nothing_else_in_the_module_reads_the_mode_for_itself(self):
        """Inventory every pkm module for trust-mode attributes, getattr and
        builtins.getattr, dictionary get/subscript reads (including vars and
        __dict__), and invoked attrgetter factories. Follow literal, added and
        locally bound string names. Other dynamic forms remain unproven.
        """
        package_root = Path(cli.__file__).parent
        readers, askers = [], []

        def scope_nodes(owner):
            pending = list(owner.body)
            while pending:
                node = pending.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                yield node
                pending.extend(ast.iter_child_nodes(node))

        def string_values(node, bindings, seen=frozenset()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return {node.value}
            if isinstance(node, ast.Name) and node.id not in seen:
                return {value for expression in bindings.get(node.id, [])
                        for value in string_values(expression, bindings, seen | {node.id})}
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return {left + right for left in string_values(node.left, bindings, seen)
                        for right in string_values(node.right, bindings, seen)}
            return set()

        for path in sorted(package_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            owners = [tree] + [n for n in ast.walk(tree) if isinstance(
                n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            for owner in owners:
                nodes = list(scope_nodes(owner))
                bindings = {}
                for node in nodes:
                    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        for target in targets:
                            if isinstance(target, ast.Name) and node.value is not None:
                                bindings.setdefault(target.id, []).append(node.value)
                location = (str(path.relative_to(package_root)),
                            getattr(owner, "name", "<module>"))
                for node in nodes:
                    key = None
                    reads_it = (isinstance(node, ast.Attribute)
                                and isinstance(node.ctx, ast.Load)
                                and node.attr == "archive_trust")
                    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                        key = node.slice
                    elif isinstance(node, ast.Call):
                        function = node.func
                        name = (function.id if isinstance(function, ast.Name) else
                                function.attr if isinstance(function, ast.Attribute) else None)
                        if name == "getattr" and len(node.args) >= 2:
                            key = node.args[1]
                        elif name == "get" and node.args:
                            key = node.args[0]
                        elif (isinstance(function, ast.Call)
                              and isinstance(function.func, ast.Attribute)
                              and function.func.attr == "attrgetter"):
                            reads_it = any("archive_trust" in string_values(arg, bindings)
                                           for arg in function.args)
                        if name == "_archive_trust_mode":
                            askers.append(location)
                    if reads_it or (key is not None and
                                    "archive_trust" in string_values(key, bindings)):
                        readers.append((*location, node.lineno))
        self.assertEqual(
            sorted({(module, name) for module, name, _line in readers}),
            [("cli.py", "_archive_trust_mode")],
            f"the trust mode is read outside its one defaulting function: {readers!r}")
        self.assertEqual(
            sorted(set(askers)),
            [("cli.py", "_local_archive_upgrade_candidates"),
             ("cli.py", "_name_header_mode"), ("cli.py", "cmd_install")],
            f"the one place is asked by {askers!r}")


class BothSitesUnderEveryMode(_BothPaths, unittest.TestCase):
    """Every mode a caller can arrive with, at both sites, real gate in place."""

    def test_each_recognised_mode_decides_as_it_always_has(self):
        """The no-regression leg: green at this lane's parent too, by design.

        What strict, repo-only and loose decide is excluded from this change, so
        these nine sub-cases are here to prove it — a mode this build recognises
        reaches exactly the answer it reached before.
        """
        for mode, admits_a_contradiction in (("strict", False),
                                             ("repo-only", False),
                                             ("loose", True)):
            with self.subTest(mode=mode, standing="matches", site="install"):
                self.assertIsNotNone(
                    self._drive_install(_entry(self.archive_sha), mode=mode),
                    f"install refused a matching archive under {mode}: {self.said}")
            with self.subTest(mode=mode, standing="matches", site="upgrade"):
                self.assertTrue(
                    self._drive_upgrade(_entry(self.archive_sha), mode=mode),
                    f"upgrade refused a matching archive under {mode}: {self.said}")
            with self.subTest(mode=mode, standing="contradicted", site="install"):
                call = self._drive_install(_entry(OTHER_SHA), mode=mode)
                self.assertEqual(call is not None, admits_a_contradiction,
                                 f"under {mode}: {self.said}")
            with self.subTest(mode=mode, standing="contradicted", site="upgrade"):
                queue = self._drive_upgrade(_entry(OTHER_SHA), mode=mode)
                self.assertEqual(bool(queue), admits_a_contradiction,
                                 f"under {mode}: {self.said}")

    def test_an_unrecognised_mode_is_refused_on_both_sites_in_each_ones_words(self):
        """And it is NOT refused in strict's words: the mode is the reason."""
        for mode in ("banana", ""):
            with self.subTest(mode=mode, site="install"):
                call = self._drive_install(_entry(self.archive_sha), mode=mode)
                self.assertIsNone(
                    call,
                    f"install proceeded under an unrecognised trust mode "
                    f"{mode!r}: {self.said}")
                self.assertIn("is not a trust mode", self.said)
                self.assertIn("strict, repo-only, loose", self.said)
                self.assertNotIn("--archive-trust=strict requires", self.said)
                # This path refuses the package and carries the transaction on.
                self.assertNotIn("Nothing was changed", self.said)
            with self.subTest(mode=mode, site="upgrade"):
                queue = self._drive_upgrade(_entry(self.archive_sha), mode=mode)
                self.assertIsNone(
                    queue,
                    f"upgrade proceeded under an unrecognised trust mode "
                    f"{mode!r}: {self.said}")
                self.assertIn("is not a trust mode", self.said)
                self.assertIn("strict, repo-only, loose", self.said)
                self.assertNotIn("--archive-trust=strict requires", self.said)
                # And this one stops the command and says so.
                self.assertIn("Nothing was changed", self.said)

    def test_a_missing_mode_means_strict_on_both_sites(self):
        """The split this closes: at this lane's parent a contradicted archive
        under a mode of None was INSTALLED by `pkm install` and refused by the
        upgrade path, off one command line."""
        for mode in (MISSING, None):
            with self.subTest(mode=mode, standing="matches"):
                self.assertIsNotNone(
                    self._drive_install(_entry(self.archive_sha), mode=mode),
                    f"install refused a matching archive: {self.said}")
                self.assertTrue(
                    self._drive_upgrade(_entry(self.archive_sha), mode=mode),
                    f"upgrade refused a matching archive: {self.said}")
            with self.subTest(mode=mode, standing="contradicted", site="install"):
                self.assertIsNone(
                    self._drive_install(_entry(OTHER_SHA), mode=mode),
                    f"install proceeded on a contradicted archive with no mode "
                    f"named: {self.said}")
                self.assertIn("--archive-trust=strict requires", self.said)
            with self.subTest(mode=mode, standing="contradicted", site="upgrade"):
                self.assertIsNone(
                    self._drive_upgrade(_entry(OTHER_SHA), mode=mode),
                    f"upgrade proceeded on a contradicted archive with no mode "
                    f"named: {self.said}")
                self.assertIn("--archive-trust=strict requires", self.said)
                self.assertIn("Nothing was changed", self.said)

    def test_a_missing_mode_reaches_the_gate_as_the_default_on_both_sites(self):
        """Not only the same outcome: the same value, asked of the gate once."""
        for site, name in ((self._drive_install, "install"),
                           (self._drive_upgrade, "upgrade")):
            for mode in (MISSING, None):
                with self.subTest(site=name, mode=mode):
                    calls = self._recorded(
                        site, _entry(self.archive_sha),
                        (cli.ARCHIVE_TRUST_MATCHES, True), mode=mode)
                    self.assertEqual([call[2] for call in calls],
                                     [cli.ARCHIVE_TRUST_DEFAULT_MODE])

    def test_the_name_header_rule_reads_the_same_one_place(self):
        """The third reader of the same value, answer for answer.

        `_name_header_mode` had its own inline default for the same attribute, so
        it is now a caller of the one place. Its answers do not change — a
        missing mode was never "loose" and still is not — and that is what this
        holds: another no-regression leg, green at this lane's parent as well.
        """
        for mode in ("loose", "strict", "repo-only", "banana", "", None, MISSING):
            expected = "report" if mode == "loose" else "refuse"
            with self.subTest(mode=mode, archive="named"):
                args = _with_trust_mode(
                    argparse.Namespace(archive=str(self.archive)), mode)
                self.assertEqual(cli._name_header_mode(args), expected)
            with self.subTest(mode=mode, archive="none"):
                args = _with_trust_mode(argparse.Namespace(archive=None), mode)
                self.assertEqual(cli._name_header_mode(args), "refuse")


if __name__ == "__main__":
    unittest.main()
