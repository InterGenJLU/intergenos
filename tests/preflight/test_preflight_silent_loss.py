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
from contextlib import redirect_stdout, redirect_stderr
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


class TestAcceptedLoss(unittest.TestCase):
    """Rule F accept matching (incl. the raw version-suffixed-token fix,
    silent-loss audit 2026-06-25)."""

    def _yml(self, body: str) -> Path:
        td = tempfile.mkdtemp()
        f = Path(td) / "package.yml"
        f.write_text(textwrap.dedent(body))
        return f

    def test_versioned_accept_token_matches_raw(self):
        # name_variants() strips a trailing version, so a versioned accept token
        # must still match via the raw-name check (Edit 1).
        y = self._yml("""\
            name: x
            silent_loss_accepted:
              - vorbisidec
              - libsystemd-login
            """)
        self.assertTrue(preflight.check_accepted_loss(y, "vorbisidec"))
        self.assertTrue(preflight.check_accepted_loss(y, "libsystemd-login"))

    def test_accept_is_precise_not_broad(self):
        # Accepting `libsystemd-login` must NOT suppress a real `libsystemd`
        # loss — the precision the per-consumer accept exists to preserve.
        y = self._yml("""\
            name: x
            silent_loss_accepted:
              - libsystemd-login
            """)
        self.assertFalse(preflight.check_accepted_loss(y, "libsystemd"))
        self.assertFalse(preflight.check_accepted_loss(y, "systemd"))

    def test_no_accept_field_returns_none(self):
        y = self._yml("name: x\n")
        self.assertIsNone(preflight.check_accepted_loss(y, "vorbisidec"))

    def test_summary_feature_names_match(self):
        # The summary-disabled accept hook matches a feature NAME against
        # silent_loss_accepted (man, gtk2, X11-xcb, bluez5, plugins_python).
        y = self._yml("""\
            name: x
            silent_loss_accepted:
              - man
              - gtk2
              - X11-xcb
              - bluez5
              - plugins_python
              - gtk-doc
            """)
        for feat in ("man", "gtk2", "X11-xcb", "bluez5", "plugins_python", "gtk-doc"):
            self.assertTrue(preflight.check_accepted_loss(y, feat),
                            f"summary feature {feat} should be accepted")
        # a NON-listed feature is not accepted
        self.assertFalse(preflight.check_accepted_loss(y, "vulkan"))


class TestRuleDProgramKind(unittest.TestCase):
    """A meson `Program X found: NO` is classified meson-program — the kind
    the declared-path Rule D rescue keys on (build-host tool, not a link dep)."""

    def test_program_line_classified_meson_program(self):
        for probe in ("dbus-broker-launch", "systemd-multi-seat-x", "rpm",
                      "gtk4-update-icon-cache"):
            log = f"Program {probe} found: NO\n"
            hits = preflight.scan_log_for_dep(log, probe)
            self.assertTrue(
                any(h[1] == "meson-program" for h in hits),
                f"{probe} Program miss must classify as meson-program")

    def test_dependency_line_is_not_program_kind(self):
        # A real link-dep miss must NOT be mistaken for a Program (Rule D would
        # wrongly rescue it). cdparanoia is a genuine loss, kind meson-*-dep.
        log = "Run-time dependency cdparanoia-3 found: NO\n"
        hits = preflight.scan_log_for_dep(log, "cdparanoia")
        self.assertTrue(hits)
        self.assertFalse(any(h[1] == "meson-program" for h in hits))


class TestPlatformAndVersionRescues(unittest.TestCase):
    """Regression coverage for the already-committed Rule H (platform backend)
    and Rule I (version-range probe) against the real GB002 audit lines —
    documents which findings they cover so the per-consumer accept set stays
    minimal (suil/gst-good are Rule-I-covered, NOT accept-listed)."""

    def test_rule_h_platform_backend(self):
        for line, tok in (
            ("Run-time dependency cairo-quartz found: NO", "quartz"),
            ("Run-time dependency cairo-win32 found: NO", "win32"),
            ("Run-time dependency gtk4-quartz found: NO", "quartz"),
        ):
            self.assertEqual(preflight.platform_backend_token(line), tok)

    def test_rule_i_covers_versioned_supersede(self):
        # suil gtk+-2.0 (log has gtk+-3.0 YES) and gst-good libsoup-2.4
        # (libsoup-3.0 YES) are rescued by Rule I — no explicit accept needed.
        self.assertTrue(preflight.version_probe_rescue(
            "Run-time dependency gtk+-2.0 found: NO",
            "Run-time dependency gtk+-3.0 found: YES 3.24.51\n"))
        self.assertTrue(preflight.version_probe_rescue(
            "Run-time dependency libsoup-2.4 found: NO",
            "Run-time dependency libsoup-3.0 found: YES 3.6.6\n"))

    def test_rule_i_separatorless_lua54_rescued(self):
        # wireplumber probes lua54/lua53 (separator-less) but finds bare `lua`
        # 5.4.8 and builds its lua-scripting module — must be rescued.
        log = ("Run-time dependency lua54 found: NO\n"
               "Run-time dependency lua53 found: NO\n"
               "Run-time dependency lua found: YES 5.4.8\n")
        self.assertTrue(preflight.version_probe_rescue(
            "Run-time dependency lua54 found: NO", log))
        self.assertTrue(preflight.version_probe_rescue(
            "Run-time dependency lua53 found: NO", log))

    def test_rule_i_separatorless_does_not_over_rescue_gtk4(self):
        # gtk4 is a DIFFERENT major from gtk3 — a real loss if missing. Bare
        # `gtk` is never found YES (only gtk+-3.0 / gtk4), so gtk4 must NOT be
        # rescued by the separator-less branch.
        log = ("Run-time dependency gtk4 found: NO\n"
               "Run-time dependency gtk+-3.0 found: YES 3.24.51\n")
        self.assertIsNone(preflight.version_probe_rescue(
            "Run-time dependency gtk4 found: NO", log))

    def test_rule_i_separatorless_requires_exact_bare_name(self):
        # `foo2 found: NO` with only a versioned `foo-1.0 found: YES` (no exact
        # bare `foo found: YES`) must NOT be rescued.
        log = ("Run-time dependency foo2 found: NO\n"
               "Run-time dependency foo-1.0 found: YES\n")
        self.assertIsNone(preflight.version_probe_rescue(
            "Run-time dependency foo2 found: NO", log))

    def test_rule_i_does_not_cover_non_versioned(self):
        # vorbisidec/libsystemd-login have no strippable version suffix → Rule I
        # cannot rescue them → they correctly require an explicit accept.
        self.assertIsNone(preflight.version_probe_rescue(
            "Run-time dependency vorbisidec found: NO",
            "Run-time dependency vorbis found: YES 1.3.7\n"))
        self.assertIsNone(preflight.version_probe_rescue(
            "Run-time dependency libsystemd-login found: NO",
            "Run-time dependency libsystemd found: YES 259\n"))


class TestRuleJAutotoolsVariant(unittest.TestCase):
    """Rule J — autotools `checking for X... no` rescued when the same base is
    found YES under a variant name (libcanberra GTK->GTK3, silent-loss 2026-06-25)."""

    def test_gtk2_no_rescued_by_gtk3_yes(self):
        log = ("checking for GTK... no\n"
               "checking for GTK3... yes\n")
        self.assertTrue(preflight.autotools_variant_rescue(
            "checking for GTK... no", log))

    def test_unrelated_yes_does_not_rescue(self):
        # A real loss: `checking for FOO... no` with only an UNRELATED yes must
        # NOT be rescued (prefix-match guard).
        log = ("checking for FOO... no\n"
               "checking for BARBAZ... yes\n")
        self.assertIsNone(preflight.autotools_variant_rescue(
            "checking for FOO... no", log))

    def test_no_yes_anywhere_is_real_loss(self):
        log = "checking for GTK... no\n"
        self.assertIsNone(preflight.autotools_variant_rescue(
            "checking for GTK... no", log))


class TestRuleKMesonSubproject(unittest.TestCase):
    """Rule K — a meson `dependency X found: NO` rescued when X is built from a
    bundled subproject (gnome-connections gtk-frdp, silent-loss 2026-06-25)."""

    def test_subproject_executing_rescues(self):
        log = ("Run-time dependency gtk-frdp-0.2 found: NO (tried pkgconfig and cmake)\n"
               "Executing subproject gtk-frdp \n"
               "gtk-frdp| Run-time dependency freerdp3 found: YES 3.22.0\n")
        self.assertTrue(preflight.meson_subproject_rescue(
            "Run-time dependency gtk-frdp-0.2 found: NO", log))

    def test_subproject_finished_rescues(self):
        log = ("Dependency foo-1.0 found: NO\n"
               "Subproject foo finished\n")
        self.assertTrue(preflight.meson_subproject_rescue(
            "Dependency foo-1.0 found: NO", log))

    def test_no_subproject_is_real_loss(self):
        # A genuine miss with NO matching subproject must NOT be rescued.
        log = "Run-time dependency gexiv2-0.16 found: NO\n"
        self.assertIsNone(preflight.meson_subproject_rescue(
            "Run-time dependency gexiv2-0.16 found: NO", log))

    def test_unrelated_subproject_does_not_rescue(self):
        log = ("Run-time dependency gtk-frdp-0.2 found: NO\n"
               "Executing subproject something-else\n")
        self.assertIsNone(preflight.meson_subproject_rescue(
            "Run-time dependency gtk-frdp-0.2 found: NO", log))


class TestSkipBehavior(unittest.TestCase):
    """Gate skips cleanly when chroot data absent."""

    def _run_main(self, repo: Path, chroot: Path, extra=None) -> int:
        argv_orig = sys.argv
        sys.argv = ["preflight-silent-loss.py",
                    "--root", str(repo),
                    "--chroot", str(chroot)] + (extra or [])
        try:
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
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

    def test_skip_with_require_audit_returns_three(self):
        # Same absent-data scenario, but --require-audit turns the SKIP into a
        # fail-closed halt (exit 3): at the build call sites the chroot MUST be
        # populated, so a SKIP means the audit could not run — not a pass.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "packages").mkdir()
            (tmp / "build").mkdir()
            chroot = tmp / "no-such-chroot"
            rc = self._run_main(tmp, chroot, extra=["--require-audit"])
        self.assertEqual(rc, 3, "skip under --require-audit must fail closed (3)")

    def test_repo_missing_packages_returns_two(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # No packages/ directory under repo — should be env error
            (tmp / "scripts").mkdir()
            chroot = tmp / "no-chroot"
            rc = self._run_main(tmp, chroot)
        self.assertEqual(rc, 2, "missing packages/ must return env-error 2")


class TestCmakeTableFeatures(unittest.TestCase):
    """CMake tabular 'Name : - disabled' detection — the exact shape that let
    libheif ship AV1/JPEG/JPEG2000 silently disabled (post-burn sweep). Before
    this scanner these lines matched NO pattern: _SUMMARY_LINE requires leading
    whitespace and a bare value token, but the CMake plugin summary is col-0,
    multi-word, and prefixes the value with '- '."""

    LIBHEIF_SUMMARY = (
        "--- Configuration summary ---\n"
        "Dav1d AV1 decoder                    : - disabled\n"
        "SVT AV1 encoder                      : - disabled\n"
        "JPEG decoder                         : - disabled\n"
        "JPEG encoder                         : - disabled\n"
        "OpenJPEG J2K decoder                 : - disabled\n"
        "OpenJPEG J2K encoder                 : - disabled\n"
        "HEIF decoder (libde265)              : + built-in\n"
        "x265 HEVC encoder                    : + built-in\n"
    )

    def _packages_dir(self, tmp: Path) -> Path:
        (tmp / "packages" / "core").mkdir(parents=True)
        return tmp / "packages"

    def test_cmake_table_disabled_captured(self):
        with tempfile.TemporaryDirectory() as td:
            packages_dir = self._packages_dir(Path(td))
            summary, _ = preflight.scan_summary_block(self.LIBHEIF_SUMMARY, packages_dir)
        cmake_feats = {f["feature"] for f in summary if f.get("source") == "cmake-table"}
        self.assertEqual(cmake_feats, {
            "Dav1d AV1 decoder", "SVT AV1 encoder", "JPEG decoder",
            "JPEG encoder", "OpenJPEG J2K decoder", "OpenJPEG J2K encoder",
        })

    def test_builtin_lines_not_captured(self):
        # The positive "+ built-in" state must NOT be flagged.
        with tempfile.TemporaryDirectory() as td:
            packages_dir = self._packages_dir(Path(td))
            summary, _ = preflight.scan_summary_block(self.LIBHEIF_SUMMARY, packages_dir)
        feats = {f["feature"] for f in summary}
        self.assertNotIn("HEIF decoder (libde265)", feats)
        self.assertNotIn("x265 HEVC encoder", feats)

    def test_cmake_feature_matches_declared_codec_deps(self):
        deps = {"dav1d", "svt-av1", "libjpeg-turbo", "openjpeg2", "libaom", "x265"}
        self.assertTrue(preflight.cmake_feature_dep_match("Dav1d AV1 decoder", deps))
        self.assertTrue(preflight.cmake_feature_dep_match("SVT AV1 encoder", deps))
        self.assertTrue(preflight.cmake_feature_dep_match("JPEG decoder", deps))
        self.assertTrue(preflight.cmake_feature_dep_match("OpenJPEG J2K decoder", deps))

    def test_cmake_feature_no_match_when_dep_absent(self):
        # A codec whose library is NOT a declared dep must not match — the token
        # overlap requires the actual codec/library name to be present.
        deps = {"libpng", "libtiff", "brotli"}
        self.assertFalse(preflight.cmake_feature_dep_match("Dav1d AV1 decoder", deps))
        self.assertFalse(preflight.cmake_feature_dep_match("OpenJPEG J2K decoder", deps))

    def test_generic_role_tokens_do_not_cross_match(self):
        # 'av1'/'decoder'/'encoder' are role tokens (stop-list), so a display
        # name must match on the SUBSTANTIVE codec token, never the generic one:
        # "Dav1d AV1 decoder" matches dav1d but NOT svt-av1, and vice-versa.
        self.assertTrue(preflight.cmake_feature_dep_match("Dav1d AV1 decoder", {"dav1d"}))
        self.assertFalse(preflight.cmake_feature_dep_match("Dav1d AV1 decoder", {"svt-av1"}))
        self.assertTrue(preflight.cmake_feature_dep_match("SVT AV1 encoder", {"svt-av1"}))
        self.assertFalse(preflight.cmake_feature_dep_match("SVT AV1 encoder", {"dav1d"}))

    def test_libheif_shape_would_be_flagged(self):
        # End-to-end at the detection level: the libheif summary + its declared
        # codec deps yields cmake-table features that pass the relevance match,
        # so ALL SIX disabled codecs are flagged (summary_disabled → gate exit 1).
        deps = {"dav1d", "svt-av1", "libjpeg-turbo", "openjpeg2",
                "libaom", "x265", "libwebp", "x264"}
        with tempfile.TemporaryDirectory() as td:
            packages_dir = self._packages_dir(Path(td))
            summary, _ = preflight.scan_summary_block(self.LIBHEIF_SUMMARY, packages_dir)
        flagged = {
            f["feature"] for f in summary
            if f.get("source") == "cmake-table"
            and preflight.cmake_feature_dep_match(f["feature"], deps)
        }
        self.assertEqual(flagged, {
            "Dav1d AV1 decoder", "SVT AV1 encoder", "JPEG decoder",
            "JPEG encoder", "OpenJPEG J2K decoder", "OpenJPEG J2K encoder",
        })


class TestManifestNameDerivation(unittest.TestCase):
    """derive_manifest_pkg_name — the authoritative-name derivation that
    replaced the naive name-(digit-led-version) regex. Both real coverage
    losses it fixes are pinned here: a non-digit version made the whole
    manifest name the package (llama-cpp-b8796 → unaudited), and a
    digit-led name segment truncated early (ntfs-3g-2026.2.25 → 'ntfs')."""

    KNOWN = {"llama-cpp", "ntfs-3g", "perl", "gtk4"}

    def test_non_digit_version_resolves_to_known_name(self):
        self.assertEqual(
            preflight.derive_manifest_pkg_name("llama-cpp-b8796", self.KNOWN),
            "llama-cpp")

    def test_digit_led_name_segment_takes_longest_known_prefix(self):
        self.assertEqual(
            preflight.derive_manifest_pkg_name("ntfs-3g-2026.2.25", self.KNOWN),
            "ntfs-3g")

    def test_plain_versioned_manifest(self):
        self.assertEqual(
            preflight.derive_manifest_pkg_name("perl-5.42.0", self.KNOWN),
            "perl")

    def test_unknown_package_falls_back_to_regex(self):
        # A package removed from the tree still derives via the old regex.
        self.assertEqual(
            preflight.derive_manifest_pkg_name("retired-pkg-1.2.3", self.KNOWN),
            "retired-pkg")

    def test_no_known_names_uses_regex(self):
        self.assertEqual(
            preflight.derive_manifest_pkg_name("libfoo-1.0", None), "libfoo")


class _MainExitHarness:
    """Shared helpers: craft a scan()-shaped result and run main() with
    scan patched to return it."""

    def _result(self, **overrides) -> dict:
        base = {
            "repo": "/r", "chroot": "/c",
            "blfs_db_present": True,
            "chroot_installed_present": True,
            "chroot_logs_present": True,
            "skipped": False, "skip_reason": None,
            "installed_count": 3,
            "findings": [], "rescued": [],
            "log_missing": [], "log_unreadable": [], "yml_missing": [],
            "blfs_no_truth": [],
            "summary_disabled": {}, "meson_not_found_intree": {},
            "cmake_not_found_intree": {},
        }
        base.update(overrides)
        return base

    def _run_main(self, result: dict, extra=None) -> int:
        from unittest.mock import patch
        argv_orig = sys.argv
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "packages").mkdir()
            sys.argv = ["preflight-silent-loss.py",
                        "--root", str(tmp), "--chroot", str(tmp)] + (extra or [])
            try:
                buf = io.StringIO()
                with patch.object(preflight, "scan",
                                  lambda repo, chroot: result), \
                     redirect_stdout(buf), redirect_stderr(buf):
                    return preflight.main()
            finally:
                sys.argv = argv_orig


class TestRequireAuditCoverage(_MainExitHarness, unittest.TestCase):
    """--require-audit must demand POSITIVE coverage: a non-skipped scan
    that examined zero packages, or silently dropped packages it could not
    pair with a log / package.yml, is exit 3 — never a pass."""

    def test_zero_inventory_fails_closed(self):
        rc = self._run_main(self._result(installed_count=0),
                            extra=["--require-audit"])
        self.assertEqual(rc, 3, "empty inventory under --require-audit must be 3")

    def test_missing_log_fails_closed(self):
        rc = self._run_main(self._result(log_missing=["llama-cpp"]),
                            extra=["--require-audit"])
        self.assertEqual(rc, 3, "a missing log under --require-audit must be 3")

    def test_unreadable_log_fails_closed(self):
        rc = self._run_main(self._result(log_unreadable=["gtk4"]),
                            extra=["--require-audit"])
        self.assertEqual(rc, 3, "an unreadable log under --require-audit must be 3")

    def test_missing_yml_fails_closed(self):
        rc = self._run_main(self._result(yml_missing=["ntfs-3g"]),
                            extra=["--require-audit"])
        self.assertEqual(rc, 3, "a missing package.yml under --require-audit must be 3")

    def test_blfs_no_truth_is_permitted_by_policy(self):
        rc = self._run_main(self._result(blfs_no_truth=["intergen", "pkm"]),
                            extra=["--require-audit"])
        self.assertEqual(rc, 0, "outside-BLFS-scope packages are policy-permitted")

    def test_full_coverage_clean_passes(self):
        rc = self._run_main(self._result(), extra=["--require-audit"])
        self.assertEqual(rc, 0)

    def test_coverage_gaps_without_flag_keep_advisory_exit(self):
        # Ad-hoc runs (no --require-audit) keep the historical behaviour.
        rc = self._run_main(self._result(log_missing=["llama-cpp"]))
        self.assertEqual(rc, 0)


class TestExitPredicateBuckets(_MainExitHarness, unittest.TestCase):
    """Every independently-failing result bucket must produce exit 1 —
    cmake_not_found_intree was printed as a FAIL class but omitted from
    the exit predicate, so a CMake-only silent loss escaped with exit 0."""

    _FINDING = {"type": "silent-loss", "pkg": "p", "dep": "d",
                "log": "l", "line_no": 1, "kind": "k", "match": "m"}

    def test_findings_bucket_exits_one(self):
        rc = self._run_main(self._result(findings=[self._FINDING]))
        self.assertEqual(rc, 1)

    def test_summary_disabled_bucket_exits_one(self):
        rc = self._run_main(self._result(
            summary_disabled={"pkg": [{"feature": "f", "value": "no"}]}))
        self.assertEqual(rc, 1)

    def test_meson_bucket_exits_one(self):
        rc = self._run_main(self._result(
            meson_not_found_intree={"pkg": [{"target": "t"}]}))
        self.assertEqual(rc, 1)

    def test_cmake_bucket_exits_one(self):
        # The escaped bucket: CMake-only silent loss must exit 1.
        rc = self._run_main(self._result(
            cmake_not_found_intree={"pkg": [{"target": "t"}]}))
        self.assertEqual(rc, 1)

    def test_all_buckets_empty_exits_zero(self):
        rc = self._run_main(self._result())
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()


class TestRuleMLaterProbeSuccess(unittest.TestCase):
    """Rule M — an exact-same-name `found: YES` AFTER the last `found: NO`
    is meson probe-retry resolution, not a loss. Pinned on the live ge9b-11
    catch (2026-07-29): spice probes liblz4 '>= 129' (legacy lz4 release
    numbering) which fails against 1.10.0, then the unversioned probe
    succeeds and liblz4.so.1 lands in libspice-server's DT_NEEDED."""

    SPICE_LOG = (
        "Dependency liblz4 found: NO. Found 1.10.0 but need: '>= 129'\n"
        "Run-time dependency liblz4 found: NO (tried pkgconfig and cmake)\n"
        "Run-time dependency liblz4 found: YES 1.10.0\n"
        "Checking for function \"LZ4_compress_fast_continue\" "
        "with dependency liblz4: YES\n"
    )

    def test_spice_liblz4_retry_rescued(self):
        self.assertTrue(preflight.later_probe_success_rescue(
            "Run-time dependency liblz4 found: NO (tried pkgconfig and cmake)",
            self.SPICE_LOG))
        self.assertTrue(preflight.later_probe_success_rescue(
            "Dependency liblz4 found: NO. Found 1.10.0 but need: '>= 129'",
            self.SPICE_LOG))

    def test_genuine_loss_not_rescued(self):
        # No later YES for the same name → a real loss stays flagged.
        log = "Run-time dependency liblz4 found: NO (tried pkgconfig)\n"
        self.assertIsNone(preflight.later_probe_success_rescue(
            "Run-time dependency liblz4 found: NO (tried pkgconfig)", log))

    def test_yes_before_no_not_rescued(self):
        # A YES that PRECEDES the last NO is not a retry-resolution — the
        # final probe state is NO and must stay flagged.
        log = ("Run-time dependency liblz4 found: YES 1.10.0\n"
               "Run-time dependency liblz4 found: NO (tried pkgconfig)\n")
        self.assertIsNone(preflight.later_probe_success_rescue(
            "Run-time dependency liblz4 found: NO (tried pkgconfig)", log))

    def test_different_name_yes_not_rescued(self):
        # A YES for a DIFFERENT dep never rescues this one.
        log = ("Run-time dependency liblz4 found: NO (tried pkgconfig)\n"
               "Run-time dependency zlib found: YES 1.3.2\n")
        self.assertIsNone(preflight.later_probe_success_rescue(
            "Run-time dependency liblz4 found: NO (tried pkgconfig)", log))


class TestLogPickerPrefixCollision(unittest.TestCase):
    """find_log_for_pkg must not attribute a LONGER known package's log to a
    prefix package. Pinned on the live ge9b-11 catch (2026-07-29): the
    glib2-bootstrap pass's designed introspection=disabled read as a glib2
    silent loss because `glib2-*.log` globs both."""

    def _mklogs(self, tmp, names):
        import os, time
        for i, n in enumerate(names):
            p = Path(tmp) / n
            p.write_text("x")
            t = time.time() + i
            os.utime(p, (t, t))

    def test_bootstrap_log_excluded_for_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            # bootstrap log is NEWER (mtime) — without the exclusion it wins.
            self._mklogs(tmp, ["glib2-core-extra-20260719-232429.log",
                               "glib2-bootstrap-core-extra-20260719-232405.log"])
            picked = preflight.find_log_for_pkg(
                Path(tmp), "glib2", {"glib2", "glib2-bootstrap"})
            self.assertEqual(picked.name, "glib2-core-extra-20260719-232429.log")

    def test_bootstrap_pkg_still_finds_own_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mklogs(tmp, ["glib2-core-extra-20260719-232429.log",
                               "glib2-bootstrap-core-extra-20260719-232405.log"])
            picked = preflight.find_log_for_pkg(
                Path(tmp), "glib2-bootstrap", {"glib2", "glib2-bootstrap"})
            self.assertEqual(
                picked.name, "glib2-bootstrap-core-extra-20260719-232405.log")

    def test_no_known_names_keeps_old_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mklogs(tmp, ["foo-20260101-000000.log"])
            picked = preflight.find_log_for_pkg(Path(tmp), "foo", None)
            self.assertEqual(picked.name, "foo-20260101-000000.log")
