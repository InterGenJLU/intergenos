# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The per-part progress standard: every long pkm operation reports its parts.

WHY THIS FILE EXISTS. A first boot ran the graphical package offer, which shelled
out to an install, and the download leg showed a bare cursor for the length of a
large archive. Decided 2026-08-06: long silent pauses are not acceptable, because they
make a user think the system is broken — and the fix
asked for was a STANDARD, not a list of the places it had been noticed. This file
is what makes it a standard: it pins the shape once, and it fails when a new
corpus-scale loop appears in a user-visible command without adopting it.

THE SHAPE, from pkm.progress:

    announce   what is about to happen, BEFORE it starts   — every level, -q too
    step       which part is running now                   — suppressed at -q
    heartbeat  that a running part is still working        — suppressed at -q
    finish     what happened, when it is over              — every level, -q too

The announce/finish pair survives -q deliberately. A request for quiet is a
request for less chatter, not for a silent multi-minute pause: the two lines
that bracket a wait are exactly what tells a reader the pause is work.

TWO KINDS OF TEST HERE, and both are needed:

  * BEHAVIOURAL — drive the reporter and read what it emitted. These prove the
    shape does what it claims, including the parts that only matter when
    something goes wrong (an exception still closes the report; a console that
    raises cannot break the work).
  * STRUCTURAL — read pkm's own source with `ast` and find corpus-scale loops
    inside user-visible command handlers. This is the half that makes it a
    standard rather than a set of fixes: a NEW silent loop written next year
    fails this test on the day it is written, instead of waiting for someone to
    watch it hang.

The structural test carries an EXEMPTION table rather than a blanket skip.
Every entry names the function and states why its loop is not a user-visible
long operation. An exemption is a decision on the record; a silent pass is not.
"""
import ast
import io
import time
import unittest
import unittest.mock
from pathlib import Path

from pkm import output

# pkm.progress is imported INSIDE the cases that need it, not here. A
# module-level import of a module this change introduces would make the
# whole file fail to COLLECT on a tree that predates it — which proves
# only that the code is new, not that the defect it fixes was real. With
# the import kept local, the structural and end-to-end cases below still
# collect against the old tree and fail on the DEFECT: no progress seams,
# and a download body copied by one blocking call.
PKM_SRC = Path(output.__file__).parent


class ProgressShapeTest(unittest.TestCase):
    """The announce/step/heartbeat/finish contract."""

    def setUp(self):
        from pkm import progress
        self.progress = progress
        self._prior = output.process_level()
        self.buf = io.StringIO()
        output._process_reporter.stream = self.buf

    def tearDown(self):
        output.set_process_level(self._prior)
        output._process_reporter.stream = None

    def emitted(self):
        return self.buf.getvalue()

    def test_announce_and_finish_print_at_normal(self):
        output.set_process_level(output.NORMAL)
        op = self.progress.LongOperation("Doing a long thing", detail="1,006 items")
        op.announce()
        op.finish("all done")
        text = self.emitted()
        self.assertIn("Doing a long thing…", text)
        self.assertIn("1,006 items", text)
        self.assertIn("all done", text)

    def test_announce_and_finish_survive_quiet(self):
        """The whole point of the standard. -q must not be able to produce a
        silent pause: the opening and closing lines still print."""
        output.set_process_level(output.QUIET)
        op = self.progress.LongOperation("Doing a long thing", detail="1,006 items")
        op.announce()
        op.step("part one")
        op.finish("all done")
        text = self.emitted()
        self.assertIn("Doing a long thing…", text)
        self.assertIn("all done", text)
        # The detail and the step line ARE suppressed at -q — quiet still
        # means quiet for everything between the brackets.
        self.assertNotIn("1,006 items", text)
        self.assertNotIn("part one", text)

    def test_step_prints_at_normal_and_not_at_quiet(self):
        output.set_process_level(output.NORMAL)
        self.progress.LongOperation("T").announce().step("reading records")
        self.assertIn("reading records", self.emitted())

        self.buf.truncate(0)
        self.buf.seek(0)
        output.set_process_level(output.QUIET)
        self.progress.LongOperation("T").announce().step("reading records")
        self.assertNotIn("reading records", self.emitted())

    def test_heartbeat_fires_only_after_the_grace_period(self):
        """A part that finishes quickly must NOT print a heartbeat — otherwise
        the standard turns every ordinary command into noise. Proven by
        driving the clock rather than by sleeping."""
        output.set_process_level(output.NORMAL)
        op = self.progress.LongOperation("T")
        op.announce()
        op.step("part")
        # Immediately after the step begins, a beat is too early.
        op.beat()
        self.assertNotIn("still working", self.emitted())
        # Move the part's start time back past the grace period.
        op._step_started = time.monotonic() - (self.progress.HEARTBEAT_AFTER + 1)
        op._last_beat = op._step_started
        op.beat()
        self.assertIn("still working", self.emitted())

    def test_heartbeat_names_where_the_work_is(self):
        output.set_process_level(output.NORMAL)
        op = self.progress.LongOperation("T")
        op.announce()
        op.step("part")
        op._step_started = time.monotonic() - (self.progress.HEARTBEAT_AFTER + 1)
        op._last_beat = op._step_started
        op.beat(note="412 of 1,006 — coreutils")
        self.assertIn("412 of 1,006 — coreutils", self.emitted())

    def test_tick_consults_the_clock_only_periodically(self):
        """tick() is called once per item in loops that run to hundreds of
        thousands of items. It must not pay for a clock read every time."""
        op = self.progress.LongOperation("T")
        op.announce()
        op.step("part")
        calls = []
        op.beat = lambda note=None: calls.append(note)
        for _ in range(self.progress.TICK_CLOCK_EVERY * 3):
            op.tick()
        self.assertEqual(len(calls), 3)

    def test_context_manager_closes_on_success(self):
        output.set_process_level(output.NORMAL)
        with self.progress.LongOperation("Scoped thing"):
            pass
        self.assertIn("Scoped thing: done", self.emitted())

    def test_context_manager_reports_a_failure_and_reraises(self):
        """An operation that dies mid-way must not leave the user looking at a
        step line that never ends, and must not swallow the exception."""
        output.set_process_level(output.NORMAL)
        with self.assertRaises(ValueError):
            with self.progress.LongOperation("Scoped thing") as op:
                op.step("part")
                raise ValueError("boom")
        text = self.emitted()
        self.assertIn("did not complete", text)
        self.assertIn("boom", text)

    def test_failed_line_survives_quiet(self):
        output.set_process_level(output.QUIET)
        op = self.progress.LongOperation("T")
        op.announce()
        op.failed("disk full")
        self.assertIn("did not complete", self.emitted())

    def test_a_console_that_raises_cannot_break_the_operation(self):
        """The guard that matters most. Reporting exists to make work visible;
        it must never be able to STOP that work. A stream that raises on every
        write must not propagate out of any reporter method."""
        class Hostile:
            def write(self, *_a, **_k):
                raise RuntimeError("console is gone")

            def flush(self, *_a, **_k):
                raise RuntimeError("console is gone")

        output._process_reporter.stream = Hostile()
        op = self.progress.LongOperation("T", detail="d")
        op.announce()
        op.step("part")
        op._step_started = time.monotonic() - (self.progress.HEARTBEAT_AFTER + 1)
        op._last_beat = op._step_started
        op.beat()
        op.tick()
        op.finish("done")
        op.failed("x")
        # Reaching here at all is the assertion; make it explicit.
        self.assertTrue(True)

    def test_part_labels_are_the_single_vocabulary(self):
        """Each named part constant is a member of the published tuple, so a
        new part cannot be introduced in one command's output alone."""
        for name in dir(self.progress):
            if name.startswith("PART_") and name != "PART_LABELS":
                self.assertIn(getattr(self.progress, name),
                              self.progress.PART_LABELS)


class ByteProgressTest(unittest.TestCase):
    """The download part: bytes, percent and rate."""

    def setUp(self):
        from pkm import progress
        self.progress = progress

    def test_reports_bytes_percent_and_rate_when_the_total_is_known(self):
        buf = io.StringIO()          # not a TTY -> plain lines
        bp = self.progress.ByteProgress("Get: pkg", total_bytes=1000,
                                   stream=buf, level=output.NORMAL)
        bp.LOG_INTERVAL = 0          # emit on every advance for the test
        bp.advance(500)
        bp.close("✓")
        text = buf.getvalue()
        self.assertIn("50.0%", text)
        self.assertIn("/s", text)
        self.assertIn("Get: pkg", text)

    def test_omits_the_percentage_when_the_total_is_unknown(self):
        """A server that declares no length leaves the total unknown. The
        report says so by omission and never invents a percentage."""
        buf = io.StringIO()
        bp = self.progress.ByteProgress("Get: pkg", total_bytes=None,
                                   stream=buf, level=output.NORMAL)
        bp.LOG_INTERVAL = 0
        bp.advance(500)
        bp.close()
        text = buf.getvalue()
        self.assertNotIn("%", text)
        self.assertIn("/s", text)

    def test_quiet_prints_nothing(self):
        buf = io.StringIO()
        bp = self.progress.ByteProgress("Get: pkg", total_bytes=1000,
                                   stream=buf, level=output.QUIET)
        bp.LOG_INTERVAL = 0
        bp.advance(500)
        bp.close("✓")
        self.assertEqual(buf.getvalue(), "")

    def test_a_non_terminal_stream_gets_no_carriage_returns(self):
        """Carriage returns make a captured log unreadable — one enormous
        line. The distinction is made from the stream, never assumed."""
        buf = io.StringIO()
        bp = self.progress.ByteProgress("Get: pkg", total_bytes=1000,
                                   stream=buf, level=output.NORMAL)
        bp.LOG_INTERVAL = 0
        bp.advance(400)
        bp.advance(600)
        bp.close("✓")
        self.assertNotIn("\r", buf.getvalue())

    def test_a_terminal_stream_rewrites_one_line(self):
        class FakeTty(io.StringIO):
            def isatty(self):
                return True

        buf = FakeTty()
        bp = self.progress.ByteProgress("Get: pkg", total_bytes=1000,
                                   stream=buf, level=output.NORMAL)
        bp.TTY_INTERVAL = 0
        bp.advance(400)
        bp.advance(600)
        bp.close("✓")
        self.assertIn("\r", buf.getvalue())

    def test_a_broken_stream_cannot_break_the_transfer(self):
        class Hostile(io.StringIO):
            def write(self, *_a, **_k):
                raise RuntimeError("gone")

        bp = self.progress.ByteProgress("Get: pkg", total_bytes=1000,
                                   stream=Hostile(), level=output.NORMAL)
        bp.LOG_INTERVAL = 0
        bp.advance(500)
        bp.close("✓")
        self.assertTrue(True)


# ----------------------------------------------------------------------
# The structural half — a new silent loop must FAIL, not wait for a report.
# ----------------------------------------------------------------------

# Calls whose result is the installed corpus or a directory of manifests.
# A `for` loop over one of these is, by construction, a loop whose length
# grows with the size of the user's system — which is what makes silence in
# it a defect rather than a style question.
CORPUS_SCALE_CALLS = frozenset({
    "list_installed",
    "verify_all",
    "import_manifests",
    "iterdir",
    "get_files",
})

# Functions whose corpus-scale loop is NOT a user-visible long operation, each
# with the reason it is exempt. Stated here so the decision is on the record
# and reviewable; a new name silently added to this table is a visible diff,
# whereas a new silent loop with no table entry is a failing test.
EXEMPT = {
    "cmd_list": "renders one line per package as it goes — the output IS the "
                "progress, and it is bounded by what the user asked to see.",
    "cmd_search": "same shape as list: every iteration prints its own result.",
    "cmd_check_updates": "runs unattended from a timer with no console to "
                         "report to; its product is a JSON file.",
    "refresh_available_updates_after_transaction":
        "runs at the end of another operation that has already reported; it "
        "is a cache-only recount with no network and no file writes beyond "
        "one small JSON.",
    "_compute_available_updates": "the computation behind the recount above.",
    "cmd_autoremove": "prints its candidate list before acting and asks for "
                      "confirmation, so the wait is bracketed by output "
                      "already.",
    "cmd_iso_prep": "a build-pipeline command that prints per-package lines "
                    "as it prunes; it has no interactive user.",
    "_print_upgrade_plan_summary": "builds the plan text that is itself the "
                                   "output; it does no I/O per package.",
    "_print_transaction_next_steps": "classifies the packages just installed "
                                     "from rows already in memory.",
    "cmd_files": "prints one line per file, so the output IS the progress "
                 "and a second report would only duplicate it.",
    "cmd_provides": "prints each match as it is found; the scan is a single "
                    "indexed query, not a per-package walk.",
    "cmd_depends": "bounded by one package's own dependency list, which is "
                   "tens of entries rather than a corpus.",
    "cmd_history": "prints one line per recorded operation as it reads them.",
    "cmd_info": "reads and prints exactly one package's record.",
    "cmd_hold": "sets a flag on one named package.",
    "cmd_unhold": "clears a flag on one named package.",
    "cmd_mark": "changes the install reason on one named package.",
    "cmd_update": "the network part (the index sync) reports per repository "
                  "through the reporter already — Hit, Index, Signature and "
                  "the generated timestamp. The corpus loop that follows only "
                  "compares installed versions against the index already in "
                  "memory: measured at 0.159s for the WHOLE command over a "
                  "1,925-line installed listing on the development machine "
                  "(2026-08-06), which is not a pause anyone can perceive.",
    "cmd_install_helper": "its only directory walk lists the handful of "
                          "helper scripts under the EULA helper directory, "
                          "and only to name the available ones when a "
                          "requested helper was not found.",
}

# The library entry points the standard covers in addition to the command
# handlers: each takes a progress callback so its CLI caller can report.
SEAM_FUNCTIONS = {
    ("verifier.py", "verify_all"): "on_package",
    ("database.py", "import_manifests"): "on_manifest",
    ("remover.py", "remove"): "on_file",
}


def _module_functions(path):
    """Yield (name, ast.FunctionDef) for every function in a pkm module,
    including methods, so a seam on a class is found as readily as a
    module-level handler."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node


def _loops_over_corpus(fn):
    """True when `fn` contains a `for` whose iterable calls a corpus-scale
    source. Unwraps enumerate()/sorted()/list() so a loop does not escape the
    check by being wrapped."""
    def _unwrap(node):
        while (isinstance(node, ast.Call)
               and isinstance(node.func, ast.Name)
               and node.func.id in ("enumerate", "sorted", "list", "reversed")
               and node.args):
            node = node.args[0]
        return node

    for node in ast.walk(fn):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        it = _unwrap(node.iter)
        if isinstance(it, ast.Call):
            f = it.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else None)
            if name in CORPUS_SCALE_CALLS:
                return True
    return False


def _mentions_progress(fn):
    """True when `fn` opens a LongOperation or drives one through a callback
    it was handed. Either is adoption of the standard: the handler that owns
    the console opens the operation, and the library seam it calls reports
    through the callback the handler passed in."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr == "LongOperation":
            return True
        if isinstance(node, ast.Name) and node.id == "LongOperation":
            return True
        if isinstance(node, ast.arg) and node.arg in (
                "on_package", "on_manifest", "on_file", "on_bytes"):
            return True
    # A seam function declares its callback as a parameter.
    for a in list(fn.args.args) + list(fn.args.kwonlyargs):
        if a.arg in ("on_package", "on_manifest", "on_file", "on_bytes"):
            return True
    return False


class EveryNamedPartReportsTest(unittest.TestCase):
    """The standard's actual claim, exercised rather than asserted about.

    The decision named the parts a user-visible operation is made of — sync,
    download, signature verification, extract/deploy, hooks — and required
    that each one announces while it runs. These cases drive the real code
    for each part and read what came out.
    """

    def test_the_download_part_reports_bytes_and_percent_through_the_real_read(self):
        """Exercises pkm.repo._download itself: the chunked body read, the
        declared-length handling, and the per-chunk callback. A stub response
        stands in for the network, but the loop under test is the real one."""
        import tempfile
        from pkm.repo import RepoManager

        body = b"x" * (700 * 1024)

        class Resp:
            status = 200
            headers = {"Content-Length": str(len(body))}

            def __init__(self):
                self._buf = io.BytesIO(body)

            def read(self, n=-1):
                return self._buf.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        seen = []
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "pkg.igos.tar.gz"
            partial = Path(td) / "pkg.part"
            mgr = RepoManager.__new__(RepoManager)
            with unittest.mock.patch("urllib.request.urlopen",
                                     lambda *a, **k: Resp()):
                mgr._download(
                    "https://example/pkg", dest, partial_path=partial,
                    on_bytes=lambda n, total, resumed: seen.append(
                        (n, total, resumed)),
                )
            self.assertEqual(dest.stat().st_size, len(body))

        # More than one callback means the body was read in bounded chunks —
        # which is the mechanism that makes any progress reporting possible.
        self.assertGreater(len(seen), 1)
        self.assertEqual(sum(n for n, _, _ in seen), len(body))
        # The declared length reaches the reporter, so a percentage is real
        # rather than estimated.
        self.assertEqual({t for _, t, _ in seen}, {len(body)})

    def test_a_local_archive_install_reports_verify_deploy_and_completion(self):
        """The extract/deploy and completion parts, through a REAL archive
        going through the real installer onto a real (temporary) root."""
        import tarfile
        import tempfile
        from pkm.database import PackageDB
        from pkm.installer import PackageInstaller
        from pkm.output import Reporter

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            staging = tmp / "staging"
            (staging / "usr" / "bin").mkdir(parents=True)
            (staging / "usr" / "bin" / "demo").write_text("#!/bin/sh\n")
            (staging / ".PKGINFO").write_text(
                "pkgname=demo\npkgver=1.0\npkgrel=1\npkgdesc=d\n"
                "license=GPL\ntier=core\nbuilddate=2026-01-01T00:00:00Z\n"
                "size=8\nfilecount=1\n")
            archive = tmp / "demo-1.0.igos.tar.gz"

            def _as_root(ti):
                # Build the archive with root-owned members, the way a real
                # package archive is built. Without this the members carry
                # the building user, whom the temporary root does not define,
                # and the install prints a correct-but-irrelevant ownership
                # warning that has nothing to do with what is under test.
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = "root"
                return ti

            with tarfile.open(archive, "w:gz") as tf:
                tf.add(staging / ".PKGINFO", arcname=".PKGINFO",
                       filter=_as_root)
                tf.add(staging / "usr" / "bin" / "demo",
                       arcname="usr/bin/demo", filter=_as_root)

            root = tmp / "root"
            root.mkdir()
            db = PackageDB(tmp / "pkm.db", root=str(root))
            buf = io.StringIO()
            reporter = Reporter(level=output.NORMAL, stream=buf)
            installer = PackageInstaller(db, root=str(root))
            ok, msg = installer.install(
                "demo", archive_path=str(archive), reporter=reporter)
            db.close()

            # Asserted INSIDE the temporary directory's lifetime: the bytes
            # on disk are half the claim, and they are gone once it closes.
            self.assertTrue(ok, msg)
            text = buf.getvalue()
            self.assertIn("Deploy:", text)     # the extract/deploy part
            self.assertIn("Installed", text)   # the completion signal
            self.assertTrue((root / "usr" / "bin" / "demo").is_file())


class StandardIsEnforcedTest(unittest.TestCase):
    """A corpus-scale loop in a user-visible command adopts the standard or
    is exempt with a stated reason. There is no third option, and that is
    what makes this a standard rather than a list of past fixes."""

    def test_every_corpus_scale_command_reports_or_is_exempt(self):
        cli = PKM_SRC / "cli.py"
        offenders = []
        for name, fn in _module_functions(cli):
            if not name.startswith("cmd_") and not name.startswith("_"):
                continue
            if not _loops_over_corpus(fn):
                continue
            if name in EXEMPT:
                continue
            if _mentions_progress(fn):
                continue
            offenders.append(name)
        self.assertEqual(
            offenders, [],
            "these pkm command handlers loop over the installed corpus "
            "without reporting progress and without an exemption reason. "
            "Adopt pkm.progress.LongOperation, or add the function to EXEMPT "
            "in this file with the reason it is not a user-visible long "
            "operation:\n  " + "\n  ".join(offenders),
        )

    def test_every_exemption_states_a_reason(self):
        for name, reason in EXEMPT.items():
            self.assertTrue(
                reason and len(reason) > 20,
                f"exemption for {name} does not state a reason",
            )

    def test_the_library_seams_take_a_progress_callback(self):
        """The three silent loops named in the decision each expose a seam,
        so the console stays in the CLI and the library stays reusable."""
        for (module, func), param in SEAM_FUNCTIONS.items():
            path = PKM_SRC / module
            found = None
            for name, fn in _module_functions(path):
                if name == func:
                    found = fn
                    break
            self.assertIsNotNone(found, f"{module}:{func} not found")
            names = [a.arg for a in
                     list(found.args.args) + list(found.args.kwonlyargs)]
            self.assertIn(
                param, names,
                f"{module}:{func} lost its {param} progress seam",
            )

    def test_the_download_body_is_read_in_bounded_chunks(self):
        """The download leg cannot report anything if the body is copied by
        one blocking call. This asserts the mechanism that makes byte
        reporting possible at all — copyfileobj on the response is what the
        bare-cursor report was."""
        src = (PKM_SRC / "repo.py").read_text(encoding="utf-8")
        self.assertIn("_DOWNLOAD_CHUNK_BYTES", src)
        self.assertNotIn("shutil.copyfileobj(resp", src)


if __name__ == "__main__":
    unittest.main()
