"""Wedge tests for the archive-time time64 build-log assertion (RT-8, GE
gate-tooling).

Covers:
  * time64audit.py itself — the shared predicate both builder chains run:
    the forbidden define forms hit; autoconf probe CHATTER does not
    false-positive; fail-closed on zero/unreadable logs; 64/mixed semantics;
  * the CLI exactly as the bash pkg_archive chokepoint invokes it;
  * the Python builder chokepoint (Builder.pkg_time64_audit) driven with a
    real BuildLogger log file — red on a planted define, green clean,
    fail-closed with no log, waived-loud on mixed.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

import time64audit  # noqa: E402
from parser import parse_template  # noqa: E402

T64_CLI = REPO_ROOT / "igos-build" / "time64audit.py"

CLEAN_LOG = """\
checking for gcc... gcc
checking whether _TIME_BITS is needed... no
gcc -m32 -O2 -c alsa.c -o alsa.o
configure: support for year-2038 was considered
"""

DIRTY_CMDLINE = """\
checking for gcc... gcc
gcc -m32 -O2 -D_TIME_BITS=64 -D_FILE_OFFSET_BITS=64 -c pcm.c -o pcm.o
"""

DIRTY_SPACED = "cc -D _TIME_BITS=64 -c x.c\n"
DIRTY_CONFIG_H = "  #define _TIME_BITS 64\n"


class TestPredicate(unittest.TestCase):
    def _log(self, tmp, text, name="pkg-20260702.log"):
        p = Path(tmp) / name
        p.write_text(text)
        return p

    def test_cmdline_define_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp, DIRTY_CMDLINE)
            v = time64audit.audit_package_logs([p], "32", "lib32-alsa-lib")
            self.assertEqual(len(v), 1)
            self.assertIn("-D_TIME_BITS=64", v[0])
            self.assertIn(":2:", v[0])  # names the line

    def test_spaced_define_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp, DIRTY_SPACED)
            self.assertTrue(time64audit.audit_package_logs([p], "32"))

    def test_config_h_define_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp, DIRTY_CONFIG_H)
            self.assertTrue(time64audit.audit_package_logs([p], "32"))

    def test_autoconf_probe_chatter_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp, CLEAN_LOG)
            self.assertEqual(
                time64audit.audit_package_logs([p], "32", "lib32-x"), [])

    def test_64bit_package_noop_even_with_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp, DIRTY_CMDLINE)
            self.assertEqual(time64audit.audit_package_logs([p], "64"), [])

    def test_mixed_noop_at_predicate_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp, DIRTY_CMDLINE)
            self.assertEqual(time64audit.audit_package_logs([p], "mixed"), [])

    def test_fail_closed_zero_logs(self):
        v = time64audit.audit_package_logs([], "32", "lib32-x")
        self.assertEqual(len(v), 1)
        self.assertIn("cannot see", v[0])

    def test_fail_closed_unreadable_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "gone.log"  # never created
            v = time64audit.audit_package_logs([p], "32", "lib32-x")
            self.assertTrue(any("unreadable at audit time" in x or
                                "no build log was readable" in x for x in v))

    def test_invalid_expected_is_violation(self):
        v = time64audit.audit_package_logs([], "48", "x")
        self.assertTrue(any("invalid expected" in x for x in v))

    # ---- WC re-cert F2-a/F2-b closes (2026-07-02) ----

    def test_f2a_silent_rules_blind_log_refused(self):
        # A silent-rules log carries no full compile line — the define is
        # INVISIBLE, so a "clean" scan is blind, not clean. Refuse.
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp, "  CC       pcm.lo\n  CCLD     libasound.la\n"
                               "make[2]: Entering directory '/build'\n")
            v = time64audit.audit_package_logs([p], "32", "lib32-alsa-lib")
            self.assertEqual(len(v), 1)
            self.assertIn("silent-rules", v[0])
            self.assertIn("ninja -v", v[0])

    def test_f2a_verbose_log_with_evidence_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp, CLEAN_LOG)  # carries a full gcc -c line
            self.assertEqual(
                time64audit.audit_package_logs([p], "32", "lib32-x"), [])

    def test_f2a_evidence_across_multiple_logs(self):
        # Evidence in ANY provided log satisfies visibility (configure log
        # silent + build log verbose is a normal split).
        with tempfile.TemporaryDirectory() as tmp:
            a = self._log(tmp, "  CC pcm.lo\n", name="pkg-configure.log")
            b = self._log(tmp, CLEAN_LOG, name="pkg-build.log")
            self.assertEqual(
                time64audit.audit_package_logs([a, b], "32", "lib32-x"), [])

    def test_f2b_double_space_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp, "gcc -m32 -D  _TIME_BITS=64 -c x.c -o x.o\n")
            self.assertTrue(
                any("_TIME_BITS" in x for x in
                    time64audit.audit_package_logs([p], "32")))

    def test_f2b_quoted_symbol_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp, 'gcc -m32 -D"_TIME_BITS"=64 -c x.c -o x.o\n')
            self.assertTrue(
                any("_TIME_BITS" in x for x in
                    time64audit.audit_package_logs([p], "32")))

    def test_f2b_quoted_value_caught(self):
        # Re-cert residual 2: the quoted-VALUE form evaded the first widening.
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp, 'gcc -m32 -D_TIME_BITS="64" -c x.c -o x.o\n')
            self.assertTrue(
                any("_TIME_BITS" in x for x in
                    time64audit.audit_package_logs([p], "32")))

    def test_f2b_parenthesized_define_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._log(tmp,
                          "gcc -O2 -c conf.c -o conf.o\n"
                          "#define _TIME_BITS (64)\n")
            self.assertTrue(
                any("_TIME_BITS" in x for x in
                    time64audit.audit_package_logs([p], "32")))


class TestCLI(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, str(T64_CLI), *args],
                              capture_output=True, text=True)

    def test_red_dirty_log_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "lib32-alsa-lib-20260702.log"
            p.write_text(DIRTY_CMDLINE)
            r = self._run("--name", "lib32-alsa-lib", "--expected", "32",
                          "--log", str(p))
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("REFUSED", r.stderr)
            self.assertIn("-D_TIME_BITS=64", r.stderr)

    def test_green_clean_log_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "lib32-alsa-lib-20260702.log"
            p.write_text(CLEAN_LOG)
            r = self._run("--name", "lib32-alsa-lib", "--expected", "32",
                          "--log", str(p))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("clean", r.stderr)

    def test_red_no_logs_fail_closed(self):
        r = self._run("--name", "lib32-x", "--expected", "32")
        self.assertEqual(r.returncode, 1)

    def test_mixed_waives_loudly(self):
        r = self._run("--name", "grub", "--expected", "mixed")
        self.assertEqual(r.returncode, 0)
        self.assertIn("waived", r.stderr)

    def test_64_default_silent_pass(self):
        r = self._run("--name", "zlib", "--expected", "64")
        self.assertEqual(r.returncode, 0)

    def test_prebuilt_vendor_32_waives_loudly(self):
        # lib32-nvidia repackages prebuilt vendor blobs: no compiler runs,
        # so its logs can never carry compile evidence — the governed
        # PREBUILT_VENDOR_32 waiver must pass it LOUDLY (origin: the
        # 2026-07-10 9B-burn halt).
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "lib32-nvidia-20260710.log"
            p.write_text("extracting vendor payload\ninstall done\n")
            r = self._run("--name", "lib32-nvidia", "--expected", "32",
                          "--log", str(p))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("WAIVED", r.stderr)
            self.assertIn("PREBUILT_VENDOR_32", r.stderr)

    def test_prebuilt_vendor_waiver_is_name_governed(self):
        # The RED control: an UNLISTED 32-bit package with the same
        # no-compile-evidence log shape must still be refused — the waiver
        # is a governed set, not a log-shape heuristic.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "lib32-fake-20260710.log"
            p.write_text("extracting vendor payload\ninstall done\n")
            r = self._run("--name", "lib32-fake", "--expected", "32",
                          "--log", str(p))
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("no full compiler-invocation line", r.stderr)

    def test_waivers_resolve_at_the_shared_predicate(self):
        # The 2026-07-10 recurrence: the Python tier calls
        # audit_package_logs directly, so membership must waive THERE, not
        # only in main() — for BOTH governed sets.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "blind.log"
            p.write_text("extracting vendor payload\n")
            for name in ("lib32-nvidia", "lib32-glibc"):
                self.assertEqual(
                    time64audit.audit_package_logs([p], "32", name), [],
                    f"{name} must be waived at the predicate level")
            # and the unlisted control still refuses at the predicate
            self.assertTrue(
                time64audit.audit_package_logs([p], "32", "lib32-fake"))


class TestBuilderChokepoint(unittest.TestCase):
    """Drive the REAL Builder.pkg_time64_audit with a real BuildLogger log."""

    def _builder(self, tmp):
        # igos-build has a dash in its dir name, so register it as a package
        # under an importable alias; builder.py's relative imports
        # (.parser/.log/...) then resolve inside that package.
        import importlib.util
        if "igosbuild" not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                "igosbuild", REPO_ROOT / "igos-build" / "__init__.py",
                submodule_search_locations=[str(REPO_ROOT / "igos-build")])
            pkg = importlib.util.module_from_spec(spec)
            sys.modules["igosbuild"] = pkg
            spec.loader.exec_module(pkg)
        from igosbuild.builder import BuildExecutor as Builder  # noqa: E402
        t = Path(tmp)
        return Builder(work_dir=t / "w", log_dir=t / "l",
                       sources_dir=t / "s", patches_dir=t / "p",
                       system_root=t / "r")

    def _pkg(self, elf_class):
        class P:  # minimal Package stand-in: only .name/.elf_class are read
            name = "lib32-alsa-lib"
        P.elf_class = elf_class
        return P()

    def test_red_planted_define_refuses_then_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._builder(tmp)
            b.logger.start_package("lib32-alsa-lib", "1.2.14", "custom")
            b.logger.output("gcc -m32 -D_TIME_BITS=64 -c pcm.c")
            self.assertFalse(b.pkg_time64_audit(self._pkg("32")))
            b.logger.end_package(True)
        with tempfile.TemporaryDirectory() as tmp:
            b = self._builder(tmp)
            b.logger.start_package("lib32-alsa-lib", "1.2.14", "custom")
            b.logger.output("gcc -m32 -O2 -c pcm.c")
            self.assertTrue(b.pkg_time64_audit(self._pkg("32")))
            b.logger.end_package(True)

    def test_fail_closed_no_log_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._builder(tmp)
            # No start_package -> no live log; a 32-bit package must refuse.
            self.assertFalse(b.pkg_time64_audit(self._pkg("32")))

    def test_64_and_mixed_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._builder(tmp)
            b.logger.start_package("grub", "2.14", "custom")
            b.logger.output("gcc -D_TIME_BITS=64 -c x.c")
            self.assertTrue(b.pkg_time64_audit(self._pkg("64")))
            self.assertTrue(b.pkg_time64_audit(self._pkg("mixed")))
            b.logger.end_package(True)


if __name__ == "__main__":
    unittest.main()
