# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Python tiers emit the SAME progress line the bash tiers do.

The bash tiers (ch8, core-extra, base, and the unified tier driver) emit
per-package counters through scripts/lib/logging.sh, and
tests/build-logging/test_progress_and_stream.sh is the spec for that shape.
The Python tiers — desktop, ai, extra, compute — ran their builds through
igos-build and said nothing in that shape, so anything following a build by
the counter went blind the moment the Python tiers took over.

These assert the emission against the SAME documented consumer regex the
shell suite uses, written here independently rather than imported, so a
drift in either half shows up as a disagreement between the two suites
rather than as both moving together.

build_package is replaced per test: what is under test is the accounting in
build_all, not the building. Nothing here compiles anything or touches a
real chroot.
"""

import importlib
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tests"))

# The package directory has a hyphen, so it cannot be imported by name.
_builder = importlib.import_module("igos-build.builder")
BuildExecutor = _builder.BuildExecutor

# The shared factory builds REAL parser dataclasses, so a new required field
# breaks in one place instead of scattering attribute errors across the suite.
# Decided 2026-07-25; reused here rather than hand-rolling a stand-in.
from igos_build.factories import make_package  # noqa: E402

# The documented consumer regex, transcribed from the comment in
# scripts/lib/logging.sh. If the Python half and the documented shape drift
# apart, this is what catches it.
PROGRESS_RE = re.compile(
    r"^\[[^]]*\] progress: package (\d+) of (\d+) — (\S+) \(([^)]+)\) — (.*)$"
)


def _pkgs(*specs):
    return [make_package(name=n, version=v, tier=t) for n, v, t in specs]


class ProgressHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.log_dir = root / "logs"
        self.executor = BuildExecutor(
            work_dir=root / "work",
            log_dir=self.log_dir,
            sources_dir=root / "sources",
            patches_dir=root / "patches",
            system_root=root / "system",
        )
        self.addCleanup(self._tmp.cleanup)

    def run_plan(self, packages, outcomes=None):
        """Run build_all with build_package stubbed; return progress lines."""
        outcomes = outcomes or {}
        self.executor.build_package = lambda pkg: outcomes.get(pkg.name, True)
        buf = []
        real_write = sys.stdout.write

        def capture(text):
            buf.append(text)
            return real_write("")

        sys.stdout.write = capture
        try:
            self.executor.build_all(packages, halt_on_failure=False)
        finally:
            sys.stdout.write = real_write
        return [ln for ln in "".join(buf).splitlines() if "progress: package" in ln]


class TestLineShape(ProgressHarness):
    def test_every_line_matches_the_documented_regex(self):
        lines = self.run_plan(_pkgs(("gtk4", "4.20.3", "desktop")))
        self.assertTrue(lines, "no progress lines were emitted at all")
        for ln in lines:
            self.assertRegex(ln, PROGRESS_RE, f"line outside documented shape: {ln}")

    def test_fields_carry_name_tier_and_total(self):
        lines = self.run_plan(_pkgs(("gtk4", "4.20.3", "desktop"),
                                    ("mesa", "25.0", "desktop")))
        m = PROGRESS_RE.match(lines[0])
        self.assertEqual(m.group(1), "1")          # index
        self.assertEqual(m.group(2), "2")          # total, derived from the plan
        self.assertEqual(m.group(3), "gtk4")       # name
        self.assertEqual(m.group(4), "desktop")    # tier
        self.assertEqual(m.group(5), "start")      # state

    def test_the_tier_is_the_packages_own_tier(self):
        # A mixed plan (an --only run pulling a base prereq) must report each
        # package's real tier, not one label for the whole run.
        lines = self.run_plan(_pkgs(("cpio", "2.15", "base"),
                                    ("gtk4", "4.20.3", "desktop")))
        tiers = [PROGRESS_RE.match(ln).group(4) for ln in lines]
        self.assertIn("base", tiers)
        self.assertIn("desktop", tiers)


class TestStates(ProgressHarness):
    def test_success_closes_with_done(self):
        lines = self.run_plan(_pkgs(("gtk4", "4.20.3", "desktop")))
        self.assertTrue(lines[-1].endswith("— done"), lines)

    def test_failure_closes_with_failed_and_a_code(self):
        lines = self.run_plan(_pkgs(("gtk4", "4.20.3", "desktop")),
                              outcomes={"gtk4": False})
        self.assertRegex(lines[-1], r"— failed rc=\d+$")

    def test_a_failed_package_still_closes_its_pair(self):
        # The fail-closed property: a failure must not look like a hang.
        lines = self.run_plan(_pkgs(("gtk4", "4.20.3", "desktop")),
                              outcomes={"gtk4": False})
        self.assertEqual(len([ln for ln in lines if "package 1 of 1" in ln]), 2)


class TestPairing(ProgressHarness):
    @staticmethod
    def unpaired(lines):
        """Indices that opened with `start` and never closed."""
        started, ended = set(), set()
        for ln in lines:
            m = PROGRESS_RE.match(ln)
            if not m:
                continue
            idx, state = m.group(1), m.group(5)
            if state == "start":
                started.add(idx)
            elif state == "done" or state.startswith("failed"):
                ended.add(idx)
        return started - ended

    def test_a_clean_run_leaves_nothing_unpaired(self):
        lines = self.run_plan(_pkgs(("a", "1", "desktop"), ("b", "1", "desktop")))
        self.assertEqual(self.unpaired(lines), set())

    def test_a_hang_would_be_visible_as_an_unmatched_start(self):
        # Proves the detector detects: an unclosed start IS reported. Without
        # this, "nothing unpaired" above could be vacuously true.
        forged = [
            "[t] progress: package 1 of 2 — a (desktop) — start",
            "[t] progress: package 1 of 2 — a (desktop) — done",
            "[t] progress: package 2 of 2 — b (desktop) — start",
        ]
        self.assertEqual(self.unpaired(forged), {"2"})


class TestSkips(ProgressHarness):
    def _skipped_plan(self):
        """A plan where the first package is already tracked."""
        pkgs = _pkgs(("a", "1", "desktop"), ("b", "1", "desktop"))
        self.executor.skip_built = True
        db = Path(self._tmp.name) / "pkgdb"
        db.mkdir(parents=True, exist_ok=True)
        self.executor.pkg_db = db
        manifest = db / "a-1"
        # _compute_template_hash returns None for a package with no recipe on
        # disk, which is the "no hash to compare" path — the manifest alone
        # then means already-built, which is the skip under test.
        manifest.write_text("dummy manifest\n")
        return pkgs

    def test_a_skip_consumes_an_index_so_n_stays_position_in_plan(self):
        lines = self.run_plan(self._skipped_plan())
        self.assertIn("package 1 of 2 — a (desktop) — skipped (already tracked)",
                      "\n".join(lines))
        # b is the SECOND thing the plan reached, even though a never built.
        self.assertRegex("\n".join(lines), r"package 2 of 2 — b \(desktop\) — start")

    def test_a_skip_is_terminal_and_not_mistaken_for_a_hang(self):
        lines = self.run_plan(self._skipped_plan())
        self.assertNotIn("1", TestPairing.unpaired(lines))


class TestAggregatedStream(ProgressHarness):
    def test_lines_also_land_in_the_shared_build_stream(self):
        # The whole point of the shape is that ONE tail follows a whole build,
        # so the Python half must append to the same file the shell library
        # writes: <log dir>/build-current.log.
        self.run_plan(_pkgs(("gtk4", "4.20.3", "desktop")))
        stream = self.log_dir / "build-current.log"
        self.assertTrue(stream.is_file(), "no aggregated stream file was written")
        body = stream.read_text()
        self.assertIn("progress: package 1 of 1 — gtk4 (desktop) — start", body)
        self.assertIn("— done", body)

    def test_the_stream_path_matches_the_shell_librarys_derivation(self):
        # scripts/lib/logging.sh: <IGOS_LOGS>/build-current.log. The tier
        # scripts point IGOS_LOGS at the same build/logs dir the builder uses,
        # so the two halves must agree on the filename.
        self.assertEqual(self.executor._progress_stream_path().name,
                         "build-current.log")
        self.assertEqual(self.executor._progress_stream_path().parent,
                         self.log_dir)

    def test_an_unwritable_stream_does_not_fail_the_build(self):
        # The stream is a convenience view; the per-tier logs are the record.
        # A broken log dir must lose the stream line, never the package.
        self.executor._progress_stream_path = lambda: Path("/proc/nonexistent/x.log")
        lines = self.run_plan(_pkgs(("gtk4", "4.20.3", "desktop")))
        self.assertTrue(any("— done" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main()
