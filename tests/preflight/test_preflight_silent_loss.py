"""Unit tests for scripts/preflight-silent-loss.py.

Focused on the pure functions (name_variants, scan_log_for_dep,
scan_summary_block, is_noise) which are testable without chroot data.
End-to-end chroot-dependent paths are out of scope here — they're covered
by the smoke-test against the live build VM separately.
"""

import importlib.util
import io
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "preflight-silent-loss.py"

_spec = importlib.util.spec_from_file_location(
    "preflight_silent_loss", SCRIPT_PATH
)
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)


class TestNameVariants(unittest.TestCase):
    def test_strip_version_suffix(self):
        variants = preflight.name_variants("libxslt-1.1.45")
        self.assertIn("libxslt", variants)
        self.assertIn("xslt", variants)

    def test_pass_suffix_stripped(self):
        variants = preflight.name_variants("openldap-pass1")
        self.assertIn("openldap", variants)

    def test_lib_prefix_added_and_stripped(self):
        variants = preflight.name_variants("xml2")
        self.assertIn("libxml2", variants)
        variants = preflight.name_variants("libxml2")
        self.assertIn("xml2", variants)

    def test_short_variants_filtered(self):
        variants = preflight.name_variants("a")
        # 'a' itself is below the min-length threshold (3); should not appear
        self.assertNotIn("a", variants)

    def test_digit_cluster_stripped(self):
        variants = preflight.name_variants("openssl3")
        self.assertIn("openssl", variants)


class TestScanLogForDep(unittest.TestCase):
    def test_autotools_checking_for_no(self):
        log = "checking for libxml2... no\n"
        hits = preflight.scan_log_for_dep(log, "libxml2")
        self.assertTrue(any(h[1] == "autotools-checking-for" for h in hits))

    def test_autotools_checking_header_no(self):
        log = "checking for libxml2.h... no\n"
        hits = preflight.scan_log_for_dep(log, "libxml2")
        self.assertTrue(any(h[1] == "autotools-checking-header" for h in hits))

    def test_meson_runtime_dependency_no(self):
        log = "Run-time dependency libgcrypt found: NO\n"
        hits = preflight.scan_log_for_dep(log, "libgcrypt")
        self.assertTrue(any(h[1] == "meson-runtime-dep" for h in hits))

    def test_cmake_could_not_find(self):
        log = "Could NOT find OpenSSL\n"
        hits = preflight.scan_log_for_dep(log, "openssl")
        self.assertTrue(any(h[1] == "cmake-could-not-find" for h in hits))

    def test_pkgconfig_not_found(self):
        log = "Package libapparmor was not found in the pkg-config search path\n"
        hits = preflight.scan_log_for_dep(log, "libapparmor")
        self.assertTrue(any(h[1] == "pkgconfig-not-found" for h in hits))

    def test_autotools_summary_disabled(self):
        log = "    libfido2: disabled\n"
        hits = preflight.scan_log_for_dep(log, "libfido2")
        self.assertTrue(
            any(h[1] == "autotools-summary-feature-disabled" for h in hits)
        )

    def test_no_match_on_present_dep(self):
        log = "checking for libxml2... yes\n"
        hits = preflight.scan_log_for_dep(log, "libxml2")
        # "yes" log line shouldn't trigger any of the failure patterns
        self.assertEqual(hits, [])


class TestScanSummaryBlock(unittest.TestCase):
    def _packages_dir(self, tmp: Path, names: list[str]) -> Path:
        (tmp / "packages" / "core").mkdir(parents=True)
        for n in names:
            d = tmp / "packages" / "core" / n
            d.mkdir()
            (d / "package.yml").write_text(f"name: {n}\n")
        return tmp / "packages"

    def test_summary_disabled_lines_captured(self):
        with tempfile.TemporaryDirectory() as td:
            packages_dir = self._packages_dir(Path(td), [])
            log = textwrap.dedent("""\
                === build summary ===
                    libfido2: disabled
                    homed: no
                    ukify: None
                """)
            summary, _ = preflight.scan_summary_block(log, packages_dir)
        feats = {(f["feature"], f["value"]) for f in summary}
        self.assertIn(("libfido2", "disabled"), feats)
        self.assertIn(("homed", "no"), feats)
        self.assertIn(("ukify", "None"), feats)

    def test_meson_found_no_marks_in_tree_correctly(self):
        with tempfile.TemporaryDirectory() as td:
            packages_dir = self._packages_dir(Path(td), ["libgcrypt"])
            log = textwrap.dedent("""\
                Run-time dependency libgcrypt found: NO
                Run-time dependency some-unknown-thing found: NO
                """)
            _, meson_no = preflight.scan_summary_block(log, packages_dir)
        by_target = {m["target"]: m for m in meson_no}
        self.assertIn("libgcrypt", by_target)
        self.assertTrue(by_target["libgcrypt"]["in_tree"])
        self.assertIn("some-unknown-thing", by_target)
        self.assertFalse(by_target["some-unknown-thing"]["in_tree"])

    def test_noise_filtered(self):
        with tempfile.TemporaryDirectory() as td:
            packages_dir = self._packages_dir(Path(td), [])
            # Literal log — each line indented with 4 spaces to satisfy
            # _SUMMARY_LINE's `^[ \t]+` anchor. textwrap.dedent would
            # strip the common leading whitespace; we want it preserved.
            log = (
                "    windows: no\n"
                "    win32: disabled\n"
                "    debug: no\n"
                "    ipv6: no\n"
                "    libfido2: disabled\n"
            )
            summary, _ = preflight.scan_summary_block(log, packages_dir)
        feats = [f["feature"].lower() for f in summary]
        # noise must NOT appear
        self.assertNotIn("windows", feats)
        self.assertNotIn("win32", feats)
        self.assertNotIn("debug", feats)
        self.assertNotIn("ipv6", feats)
        # but real signal should
        self.assertIn("libfido2", feats)


class TestIsNoise(unittest.TestCase):
    def test_known_noise_terms(self):
        for term in ("windows", "windows.h", "_FILE_OFFSET_BITS",
                     "valgrind", "ipv6", "debug"):
            self.assertTrue(preflight.is_noise(term), f"{term} should be noise")

    def test_real_signal_passes(self):
        for term in ("libgcrypt", "libapparmor", "libfido2", "homed", "ukify"):
            self.assertFalse(preflight.is_noise(term),
                             f"{term} should NOT be noise")

    def test_short_strings_treated_as_noise(self):
        for term in ("a", "x", "no", "xy"):
            self.assertTrue(preflight.is_noise(term),
                            f"{term} (short) should be noise")


class TestSkipBehavior(unittest.TestCase):
    """Gate skips cleanly when chroot data absent."""

    def _run_main(self, repo: Path, chroot: Path) -> int:
        argv_orig = sys.argv
        sys.argv = ["preflight-silent-loss.py",
                    "--root", str(repo),
                    "--chroot", str(chroot)]
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                return preflight.main()
        finally:
            sys.argv = argv_orig

    def test_skip_when_chroot_absent_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "packages").mkdir()
            (tmp / "build").mkdir()
            # No BLFS db, no chroot — should skip cleanly
            chroot = tmp / "no-such-chroot"
            rc = self._run_main(tmp, chroot)
        self.assertEqual(rc, 0, "skip-when-chroot-absent must return 0")

    def test_repo_missing_packages_returns_two(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # No packages/ directory under repo — should be env error
            (tmp / "scripts").mkdir()
            chroot = tmp / "no-chroot"
            rc = self._run_main(tmp, chroot)
        self.assertEqual(rc, 2, "missing packages/ must return env-error 2")


if __name__ == "__main__":
    unittest.main()
