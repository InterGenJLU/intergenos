#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An install's repository-index lookup does not turn an error into "absent".

Measured 2026-09-19 while changing the lookup's call signature: a TypeError
raised inside `repo.get_package(...)` was caught by a bare `except Exception`
in the install path, which set the looked-up entry to None. The archive was
then treated as a package the index has never heard of, so the line that tells
the person WHY the install is being refused —

    ✗ error: archive SHA256 does not match repository index!

— disappeared, along with the two sha256 values under it. The strict refusal
still fired, so the install was still refused and the exit was still non-zero;
what vanished was the reason, and nothing anywhere reported an error. A
failure that reports itself as an ordinary absence is the failure this
project's gates exist to prevent.

The rule pinned here: only the errors that genuinely MEAN "there is no entry
to compare against" are absorbed — an unreadable index cache, which the trust
gate then refuses on — and every other error travels with its own message
instead of being renamed.

Since 2026-09-30 this module holds the PAIR. `pkm upgrade <name> --archive
<file>` reaches the same lookup through `_local_archive_upgrade_candidates`, and
there the bare `except Exception` had never been narrowed: every error was renamed
as an absent entry, so an index that could not be read was announced to the person
as a package the index does not list, and a programming error inside the lookup
vanished entirely. The install cases below stand unchanged and are now also the
pair's control — green before and after — while the upgrade cases hold that path
to the same rule in its OWN words, which are deliberately not the install path's.

Executed against the shipped command code with its collaborators stood in; the
archive is a real file on disk, because the code hashes it before it decides. The
upgrade cases carry a real .igos.tar.gz, because that path reads the archive's
own .PKGINFO before it reaches the lookup at all.
"""

import argparse
import ast
import hashlib
import io
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

import pkm.cli as cli

NAME = "demo-pkg"
INDEX_SHA = "b" * 64


def _index_entry(name):
    return {"name": name, "version": "1.0", "release": 1,
            "sha256": INDEX_SHA, "size": 1000, "installed_size": 4000}


def _real_archive(directory, version):
    """A real .igos.tar.gz of NAME that a reader of .PKGINFO will accept."""
    lines = [f"pkgname={NAME}", f"pkgver={version}", "pkgrel=1",
             "pkgdesc=a package for the index-lookup cases", "license=GPL",
             "tier=extra", "builddate=2026-09-30T00:00:00Z", "size=2",
             "filecount=1"]
    out = directory / f"{NAME}-{version}.igos.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        data = ("\n".join(lines) + "\n").encode()
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        payload = b"x\n"
        info = tarfile.TarInfo(f"./usr/share/{NAME}/marker")
        info.size = len(payload)
        info.mode = 0o644
        tf.addfile(info, io.BytesIO(payload))
    return out


def _install_args(archive, trust):
    return argparse.Namespace(
        packages=[NAME], archive=str(archive), archive_trust=trust,
        quiet=False, verbose=False, allow_downgrade=False, assume_yes=True,
    )


class TheIndexLookupDoesNotRenameItsErrors(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.archive = Path(self._tmp.name) / f"{NAME}-1.0-1.igos.tar.gz"
        self.archive.write_bytes(b"not the archive the index describes")
        self.assertNotEqual(
            hashlib.sha256(self.archive.read_bytes()).hexdigest(), INDEX_SHA,
            "the fixture archive must not match the index, or it proves "
            "nothing")
        self.addCleanup(self._tmp.cleanup)

    def _run(self, trust, lookup):
        """Run the install command with a stood-in index lookup.

        Returns (raised, stdout, stderr); `raised` is whatever left the
        command — a SystemExit for a refusal, or the error itself."""
        db = MagicMock()
        db.get_installed.return_value = None
        raised = None
        out, err = io.StringIO(), io.StringIO()
        with patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli.refresh_available_updates_after_transaction"), \
             patch("pkm.cli._print_transaction_next_steps"):
            Repo.return_value.get_package.side_effect = lookup
            installer = Installer.return_value
            installer.install.side_effect = AssertionError(
                "the refused archive was handed to install()")
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.cmd_install(db, _install_args(self.archive, trust))
                except BaseException as exc:      # noqa: BLE001 — recorded
                    raised = exc
        return raised, out.getvalue(), err.getvalue()

    def test_an_unexpected_error_is_not_read_as_a_missing_entry(self):
        """THE DEFECT. A TypeError from the lookup used to become None."""
        def _boom(_name):
            raise TypeError("get_package() got an unexpected keyword argument")
        raised, out, err = self._run("strict", _boom)
        self.assertIsInstance(raised, TypeError, (out, err))

    def test_the_mismatch_line_is_there_when_the_lookup_works(self):
        """The control the defect was found by: with a working lookup the
        person is told why, not merely that."""
        _raised, out, err = self._run("strict", _index_entry)
        self.assertIn("does not match repository index", err)
        # The two sha256 values are printed under the error line, on stdout.
        self.assertIn(INDEX_SHA, out + err)

    def test_an_unreadable_index_cache_is_an_absent_entry_and_is_said_so(self):
        """The one error that DOES mean "no entry to compare against". The
        install is still refused under strict trust, and the reason names the
        unreadable index rather than implying the package is unknown."""
        def _unreadable(_name):
            raise OSError(13, "Permission denied")
        raised, out, err = self._run("strict", _unreadable)
        self.assertIsInstance(raised, SystemExit, (out, err))
        self.assertNotEqual(raised.code, 0, err)
        self.assertIn("repository index could not be read", err)
        self.assertIn("Permission denied", err)

    def test_a_missing_entry_is_still_an_ordinary_absence(self):
        """A package the index has never heard of is not an error: no entry,
        no mismatch line, and the trust gate refuses on its own terms."""
        _raised, _out, err = self._run("strict", lambda _name: None)
        self.assertNotIn("does not match repository index", err)
        self.assertNotIn("could not be read", err)
        self.assertIn("--archive-trust=strict requires SHA256 match", err)

    def test_the_refused_package_summary_names_the_lookup_cause(self):
        def unreadable(_name):
            raise OSError(13, "Permission denied")

        def without_hash(name):
            entry = _index_entry(name)
            del entry["sha256"]
            return entry

        cases = (
            ("unlisted", lambda _name: None, "the repository index does not list this package"),
            ("unreadable", unreadable, "the repository index could not be read"),
            ("contradicted", _index_entry, "the archive's SHA256 does not match the repository index"),
            ("without-hash", without_hash, "the repository index provides no SHA256 for this package"),
        )
        for mode in ("strict", "repo-only"):
            for cause, lookup, reason in cases:
                with self.subTest(mode=mode, cause=cause):
                    raised, out, err = self._run(mode, lookup)
                    self.assertIsInstance(raised, SystemExit, (out, err))
                    self.assertEqual(raised.code, 1)
                    self.assertEqual(out.count(f"{NAME}:"), 1)
                    summary = " ".join(out.split(f"{NAME}:", 1)[1].split())
                    self.assertIn(f"{reason} (--archive-trust={mode})", summary)


class TheUpgradePathDoesNotRenameItsErrorsEither(unittest.TestCase):
    """The other half of the pair: `pkm upgrade <name> --archive <file>`.

    Same lookup, same rule, this path's own words. Everything here was RED at
    this lane's parent except the two controls that say so.
    """

    INSTALLED_VERSION = "0.9"
    ARCHIVE_VERSION = "1.0"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.archive = _real_archive(self.dir, self.ARCHIVE_VERSION)
        self.archive_sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.assertNotEqual(
            self.archive_sha, INDEX_SHA,
            "the fixture archive must not match the index, or it proves nothing")
        self.addCleanup(self._tmp.cleanup)

    def _run(self, trust, lookup):
        """Run the upgrade candidate step with a stood-in index lookup.

        Returns (raised, queue, stdout, stderr). The lookup is stood in because
        this is the one place the error class can be presented at the call site:
        the real mechanism reports an unreadable cached index in `pkm`'s own
        words and skips the repo, so it reaches this call as a true absence —
        see the seal's real-mechanism capture, which says so plainly.
        """
        db = MagicMock()
        db.get_installed.return_value = {
            "name": NAME, "version": self.INSTALLED_VERSION, "release": 1}
        db.list_held.return_value = []
        db.get_installed_files.return_value = []
        repo = MagicMock()
        repo.get_package.side_effect = lookup
        args = cli.build_parser().parse_args(
            ["upgrade", NAME, "--archive", str(self.archive),
             "--archive-trust", trust])
        out, err = io.StringIO(), io.StringIO()
        raised, queue = None, None
        with redirect_stdout(out), redirect_stderr(err):
            try:
                queue = cli._local_archive_upgrade_candidates(
                    db, repo, args, str(self.archive))
            except BaseException as exc:      # noqa: BLE001 — recorded
                raised = exc
        return raised, queue, out.getvalue(), err.getvalue()

    def test_an_unexpected_error_is_not_read_as_a_missing_entry(self):
        """THE DEFECT, this side of the pair. A TypeError from the lookup became
        None here long after the install path stopped doing it."""
        def _boom(_name):
            raise TypeError("get_package() got an unexpected keyword argument")
        raised, queue, out, err = self._run("strict", _boom)
        self.assertIsInstance(raised, TypeError, (queue, out, err))

    def test_an_unreadable_index_is_reported_as_unreadable(self):
        """The one error that does mean "no entry to compare against": said, in
        this path's words, and the refusal that follows is the gate's."""
        def _unreadable(_name):
            raise OSError(13, "Permission denied")
        raised, queue, out, err = self._run("strict", _unreadable)
        self.assertIn("the repository index could not be read", err)
        self.assertIn("Permission denied", err)
        self.assertIsNone(queue)
        self.assertIsInstance(raised, SystemExit, (out, err))
        self.assertNotEqual(raised.code, 0)
        # The gate's refusal, not this report's: the two are separate sentences.
        self.assertIn("--archive-trust=strict requires", err)
        self.assertIn("Nothing was changed", err)

    def test_the_report_does_not_decide_the_command(self):
        """It reports and carries on to the gate: under loose the upgrade still
        proceeds, with the index fault named and the gate's own warning."""
        def _unreadable(_name):
            raise OSError(13, "Permission denied")
        raised, queue, out, err = self._run("loose", _unreadable)
        self.assertIsNone(raised, (out, err))
        self.assertTrue(queue, f"loose refused the archive: {err}")
        self.assertIn("the repository index could not be read", err)
        self.assertIn("skipping repo verification", err)

    def test_a_missing_entry_is_still_an_ordinary_absence(self):
        """CONTROL, green at both ends: a package the index has never heard of
        is not an error, and nothing claims the index could not be read."""
        _raised, queue, _out, err = self._run("strict", lambda _name: None)
        self.assertIsNone(queue)
        self.assertNotIn("could not be read", err)
        self.assertIn("--archive-trust=strict requires", err)

    def test_a_working_lookup_is_untouched(self):
        """CONTROL, green at both ends: an index that answers is read, and the
        contradiction is named in this path's own words."""
        _raised, queue, out, err = self._run("strict", _index_entry)
        self.assertIsNone(queue)
        self.assertIn("does not match the repository index entry", out + err)
        self.assertIn(INDEX_SHA, out + err)
        self.assertNotIn("could not be read", err)


class TheTwoPathsSayItInTheirOwnWords(unittest.TestCase):
    """The pair, held apart on purpose and together in substance."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_both_paths_name_an_unreadable_index_and_neither_borrows_the_other(self):
        def _unreadable(_name):
            raise OSError(13, "Permission denied")

        install_case = TheIndexLookupDoesNotRenameItsErrors(
            "test_an_unreadable_index_cache_is_an_absent_entry_and_is_said_so")
        install_case.setUp()
        self.addCleanup(install_case.doCleanups)
        _raised, install_out, install_err = install_case._run("strict", _unreadable)

        upgrade_case = TheUpgradePathDoesNotRenameItsErrorsEither(
            "test_an_unreadable_index_is_reported_as_unreadable")
        upgrade_case.setUp()
        self.addCleanup(upgrade_case.doCleanups)
        _r, _queue, upgrade_out, upgrade_err = upgrade_case._run("strict", _unreadable)

        for where, said in (("install", install_out + install_err),
                            ("upgrade", upgrade_out + upgrade_err)):
            with self.subTest(path=where):
                self.assertIn("the repository index could not be read", said)
                self.assertIn("Permission denied", said)

        def _the_sentence(said):
            """The report, whitespace collapsed — both reporters WRAP it, and
            the first physical line of the two is identical up to the wrap."""
            collapsed = " ".join(said.split())
            start = collapsed.find("the repository index could not be read")
            if start < 0:
                return ""
            rest = collapsed[start:]
            gate = rest.find("--archive-trust")
            return (rest[:gate] if gate > 0 else rest).strip()

        install_line = _the_sentence(install_err)
        upgrade_line = _the_sentence(upgrade_err)
        self.assertTrue(install_line and upgrade_line)
        self.assertNotEqual(
            install_line, upgrade_line,
            "the two paths print the SAME sentence; each keeps its own words, "
            "and a reporter inside the lookup would have forced one on both")
        self.assertIn("cannot be checked against it", install_line)
        self.assertIn("this archive cannot be checked against the index entry",
                      upgrade_line)

    def test_every_guarded_lookup_in_the_module_is_inventoried(self):
        """Walk every pkm module for lookups guarded by try or suppress.

        Follow direct get_package calls, method aliases bound before or inside
        the guard, literal getattr references, and calls through lexical helper
        functions. Pin each guard's exception types. This is a conservative
        syntax/call inventory; arbitrary dynamic dispatch is not certified.
        """
        package_root = Path(cli.__file__).parent
        found = []

        def scope_nodes(statements):
            pending = list(statements)
            while pending:
                node = pending.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                yield node
                pending.extend(ast.iter_child_nodes(node))

        def defined_functions(statements):
            result = {}
            pending = list(statements)
            while pending:
                node = pending.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result[node.name] = node
                elif not isinstance(node, ast.ClassDef):
                    pending.extend(ast.iter_child_nodes(node))
            return result

        def bindings_for(function):
            bound = {}
            for node in scope_nodes(function.body):
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and node.value is not None:
                            bound.setdefault(target.id, []).append(node.value)
            return bound

        def lookup_callable(node, bindings, seen=frozenset()):
            if isinstance(node, ast.Attribute):
                return node.attr == "get_package"
            if isinstance(node, ast.Name) and node.id not in seen:
                return any(lookup_callable(value, bindings, seen | {node.id})
                           for value in bindings.get(node.id, []))
            return (isinstance(node, ast.Call)
                    and (isinstance(node.func, ast.Name) and node.func.id == "getattr"
                         or isinstance(node.func, ast.Attribute) and node.func.attr == "getattr")
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == "get_package")

        def contains_lookup(statements, bindings, helpers, seen=frozenset()):
            for node in scope_nodes(statements):
                if not isinstance(node, ast.Call):
                    continue
                if lookup_callable(node.func, bindings):
                    return True
                if isinstance(node.func, ast.Name) and node.func.id in helpers:
                    helper = helpers[node.func.id]
                    if id(helper) in seen:
                        continue
                    if contains_lookup(helper.body, bindings_for(helper),
                                       helpers | defined_functions(helper.body),
                                       seen | {id(helper)}):
                        return True
            return False

        def caught_by(handler):
            if handler.type is None:
                return ["<bare except>"]
            kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            return [ast.unparse(kind) for kind in kinds]

        for path in sorted(package_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            globals_ = defined_functions(tree.body)
            owners = [tree] + [n for n in ast.walk(tree) if isinstance(
                n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            for function in owners:
                bindings = bindings_for(function)
                helpers = globals_ | defined_functions(function.body)
                for node in scope_nodes(function.body):
                    kind, types = None, ()
                    if isinstance(node, (ast.Try, ast.TryStar)):
                        kind = "try"
                        types = tuple(name for handler in node.handlers for name in caught_by(handler))
                    elif isinstance(node, (ast.With, ast.AsyncWith)):
                        for item in node.items:
                            expression = item.context_expr
                            if isinstance(expression, ast.Call) and (
                                    isinstance(expression.func, ast.Name) and expression.func.id == "suppress"
                                    or isinstance(expression.func, ast.Attribute) and expression.func.attr == "suppress"):
                                kind = "suppress"
                                types += tuple(ast.unparse(arg) for arg in expression.args)
                    if kind and contains_lookup(node.body, bindings, helpers):
                        found.append((str(path.relative_to(package_root)),
                                      getattr(function, "name", "<module>"), kind, types))
        self.assertEqual(
            sorted(found),
            [("cli.py", "_local_archive_upgrade_candidates", "try", ("OSError",)),
             ("cli.py", "cmd_check_updates", "try", ("OSError",)),
             ("cli.py", "cmd_info", "try", ("OSError", "Exception")),
             ("cli.py", "cmd_install", "try", ("OSError",)),
             ("cli.py", "cmd_list", "try", ("OSError",)),
             ("cli.py", "refresh_available_updates_after_transaction", "try",
              ("OSError", "Exception"))],
            f"the guarded index lookups and their exception types changed: {sorted(found)!r}")

    def test_every_read_only_command_guards_the_index_it_consults(self):
        """The read commands, walked from the module's OWN command set.

        An index call reached through `getattr(repo, "has_synced_index", None)`
        and then invoked by that name passes an attribute search; the scheduled
        check holds exactly that shape, and it is what this walk resolves —
        together with following each command into the module-level helpers it
        calls, because a call site one function away is still that command's
        answer. Every site found must sit inside a `try` whose handlers include
        OSError, with ONE documented exception, named below.

        Measured 2026-10-01: before this walk existed, five of these seven sites
        were unguarded, and the two commands that did not end in a traceback said
        "No repository index yet", "No packages available", "Everything is up to
        date" or "is not installed" for an index that existed and could not be
        read.
        """
        source = Path(cli.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {n.name: n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        index_entry_points = {"get_package", "search", "has_synced_index",
                              "list_available"}

        def getattr_bindings(fn):
            """{local name: index call} for `x = getattr(repo, "call", ...)`."""
            bound = {}
            for node in ast.walk(fn):
                if (isinstance(node, ast.Assign)
                        and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id == "getattr"
                        and len(node.value.args) >= 2
                        and isinstance(node.value.args[1], ast.Constant)
                        and node.value.args[1].value in index_entry_points):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bound[target.id] = node.value.args[1].value
            return bound

        def index_calls(fn):
            bound = getattr_bindings(fn)
            found = []
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (isinstance(func, ast.Attribute)
                        and func.attr in index_entry_points):
                    receiver = ast.unparse(func.value)
                    # `db.search(...)` is the local database, not the index.
                    if receiver == "repo_manager()" or receiver.endswith("repo"):
                        found.append((node, func.attr))
                elif isinstance(func, ast.Name) and func.id in bound:
                    found.append((node, bound[func.id]))
            return found

        def guarded_with_oserror(fn, call):
            for node in ast.walk(fn):
                if not isinstance(node, ast.Try):
                    continue
                if not any(inner is call for stmt in node.body
                           for inner in ast.walk(stmt)):
                    continue
                for handler in node.handlers:
                    if handler.type is None:
                        continue
                    kinds = (handler.type.elts
                             if isinstance(handler.type, ast.Tuple)
                             else [handler.type])
                    if any(ast.unparse(kind) == "OSError" for kind in kinds):
                        return True
            return False

        def module_level_callees(fn):
            return {n.func.id for n in ast.walk(fn)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id in functions}

        walked, unguarded = set(), set()
        for command in sorted(cli.PKM_READONLY_COMMANDS):
            entry = "cmd_" + command.replace("-", "_")
            self.assertIn(
                entry, functions,
                f"the read-only command {command!r} has no {entry} in "
                f"pkm/cli.py, so this walk is reading the wrong names")
            seen, stack = set(), [entry]
            while stack:
                name = stack.pop()
                if name in seen:
                    continue
                seen.add(name)
                fn = functions[name]
                for call, which in index_calls(fn):
                    walked.add((command, name, which))
                    if not guarded_with_oserror(fn, call):
                        unguarded.add((command, name, which))
                stack.extend(module_level_callees(fn) - seen)

        self.assertEqual(
            sorted(walked),
            [("check-updates", "_compute_available_updates", "get_package"),
             ("check-updates", "cmd_check_updates", "has_synced_index"),
             ("info", "cmd_info", "get_package"),
             ("list", "cmd_list", "get_package"),
             ("list", "cmd_list", "list_available"),
             ("search", "cmd_search", "has_synced_index"),
             ("search", "cmd_search", "search")],
            f"the index a read-only command consults is reached at "
            f"{sorted(walked)!r}; a site this list does not carry has not been "
            f"given that command's words")

        # The ONE documented exception: the shared computation that the
        # scheduled check and the end-of-transaction refresh both call. Each of
        # its callers guards its own call in its own words — the refresh keeps
        # the words it already had — so narrowing inside the helper would put one
        # account on both. The day that changes, this line is what is edited.
        self.assertEqual(
            sorted(unguarded),
            [("check-updates", "_compute_available_updates", "get_package")],
            f"these index calls in read-only commands catch no OSError: "
            f"{sorted(unguarded)!r} — each one answers a person with an empty "
            f"index or a traceback for a file that could not be read")


if __name__ == "__main__":
    unittest.main()
