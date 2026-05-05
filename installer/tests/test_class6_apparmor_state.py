"""Unit tests for class6_apparmor_state — all mocked.

Runs anywhere. No real /sys access, no systemctl, no root.

A future TestClass6PostInstall sibling will exercise the probes against
a real booted InterGenOS target with AppArmor active (same pattern as
Class 1 / 2 / 2b / 5 post-install splits in test_post_install_integration).
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from installer.tests import class6_apparmor_state as c6


# Representative /sys/kernel/security/apparmor/profiles content with a
# realistic mix of loaded profiles in different modes.
PROFILES_FILE_REALISTIC = textwrap.dedent("""\
    /usr/bin/firefox (enforce)
    /usr/bin/man (enforce)
    /usr/bin/cups (complain)
    snap.lxd.activate (complain)
    /usr/lib/cups/backend/cups-pdf (enforce)
    """)

# All profiles in complain mode — v1.0 default per design decision 2026-04-29.
PROFILES_FILE_ALL_COMPLAIN = textwrap.dedent("""\
    /usr/bin/firefox (complain)
    /usr/bin/man (complain)
    /usr/bin/cups (complain)
    """)

# A profile that's loaded-but-unconfined — posture defect: shows up in
# the kernel's view but isn't actually enforcing anything.
PROFILES_FILE_WITH_UNCONFINED = textwrap.dedent("""\
    /usr/bin/firefox (enforce)
    /usr/bin/sshd (unconfined)
    """)

PROFILES_FILE_EMPTY = ""

# Mismatched format — anything that doesn't end in (...) is skipped.
PROFILES_FILE_GARBAGE = textwrap.dedent("""\
    not-a-real-profile-line
    /usr/bin/firefox (enforce)
    junk without parens
    """)


def _write_enabled(td: Path, value: str) -> Path:
    path = td / "enabled"
    path.write_text(value)
    return path


def _write_profiles(td: Path, content: str) -> Path:
    path = td / "profiles"
    path.write_text(content)
    return path


def _mock_systemctl(state: str, returncode: int = 0):
    fake = mock.MagicMock()
    fake.return_value.stdout = state
    fake.return_value.stderr = ""
    fake.return_value.returncode = returncode
    return fake


# --- Module-enabled probe -------------------------------------------------


class TestProbeApparmorModuleEnabled(unittest.TestCase):
    def test_file_missing(self):
        with tempfile.TemporaryDirectory() as td:
            r = c6.probe_apparmor_module_enabled(Path(td) / "missing")
        self.assertFalse(r.passed)
        self.assertIn("not present", r.detail)
        self.assertTrue(r.required)

    def test_enabled_y(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_enabled(Path(td), "Y")
            r = c6.probe_apparmor_module_enabled(path)
        self.assertTrue(r.passed)
        self.assertEqual(r.observed, "Y")

    def test_enabled_n(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_enabled(Path(td), "N")
            r = c6.probe_apparmor_module_enabled(path)
        self.assertFalse(r.passed)
        self.assertEqual(r.observed, "N")
        self.assertIn("disabled at runtime", r.detail)

    def test_enabled_garbage(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_enabled(Path(td), "maybe\n")
            r = c6.probe_apparmor_module_enabled(path)
        self.assertFalse(r.passed)
        self.assertIn("expected Y or N", r.detail)


# --- Service-active probe -------------------------------------------------


class TestProbeApparmorServiceActive(unittest.TestCase):
    def test_systemctl_absent_fails(self):
        with mock.patch(
            "installer.tests.class6_apparmor_state.shutil.which",
            return_value=None,
        ):
            r = c6.probe_apparmor_service_active()
        self.assertFalse(r.passed)
        self.assertTrue(r.required)
        self.assertIn("not in PATH", r.detail)

    def test_service_active(self):
        with mock.patch(
            "installer.tests.class6_apparmor_state.shutil.which",
            return_value="/usr/bin/systemctl",
        ), mock.patch(
            "installer.tests.class6_apparmor_state.subprocess.run",
            _mock_systemctl("active\n"),
        ):
            r = c6.probe_apparmor_service_active()
        self.assertTrue(r.passed)
        self.assertEqual(r.observed, "active")

    def test_service_inactive(self):
        with mock.patch(
            "installer.tests.class6_apparmor_state.shutil.which",
            return_value="/usr/bin/systemctl",
        ), mock.patch(
            "installer.tests.class6_apparmor_state.subprocess.run",
            _mock_systemctl("inactive\n", returncode=3),
        ):
            r = c6.probe_apparmor_service_active()
        self.assertFalse(r.passed)
        self.assertEqual(r.observed, "inactive")
        self.assertIn("expected 'active'", r.detail)

    def test_service_failed(self):
        with mock.patch(
            "installer.tests.class6_apparmor_state.shutil.which",
            return_value="/usr/bin/systemctl",
        ), mock.patch(
            "installer.tests.class6_apparmor_state.subprocess.run",
            _mock_systemctl("failed\n", returncode=3),
        ):
            r = c6.probe_apparmor_service_active()
        self.assertFalse(r.passed)
        self.assertEqual(r.observed, "failed")


# --- Profiles-file parser -------------------------------------------------


class TestParseProfilesFile(unittest.TestCase):
    def test_realistic_parse(self):
        out = c6._parse_profiles_file(PROFILES_FILE_REALISTIC)
        self.assertEqual(len(out), 5)
        names = [n for n, _ in out]
        self.assertIn("/usr/bin/firefox", names)
        self.assertIn("snap.lxd.activate", names)

    def test_modes_extracted(self):
        out = dict(c6._parse_profiles_file(PROFILES_FILE_REALISTIC))
        self.assertEqual(out["/usr/bin/firefox"], "enforce")
        self.assertEqual(out["/usr/bin/cups"], "complain")

    def test_garbage_skipped(self):
        out = c6._parse_profiles_file(PROFILES_FILE_GARBAGE)
        # Only the well-formed firefox line should parse out.
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "/usr/bin/firefox")

    def test_empty_returns_empty(self):
        self.assertEqual(c6._parse_profiles_file(""), [])

    def test_profile_name_with_spaces(self):
        # AppArmor profile names can include spaces in some cases (rare;
        # mostly snap-pkg patterns). Verify rfind('(') handles it.
        text = "snap.firefox.firefox (enforce)\n"
        out = c6._parse_profiles_file(text)
        self.assertEqual(out[0][0], "snap.firefox.firefox")
        self.assertEqual(out[0][1], "enforce")


# --- Profiles-loaded probe ------------------------------------------------


class TestProbeProfilesLoaded(unittest.TestCase):
    def test_realistic_passes(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_profiles(Path(td), PROFILES_FILE_REALISTIC)
            r = c6.probe_profiles_loaded(path)
        self.assertTrue(r.passed)
        self.assertEqual(r.observed, "5")

    def test_empty_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_profiles(Path(td), PROFILES_FILE_EMPTY)
            r = c6.probe_profiles_loaded(path)
        self.assertFalse(r.passed)
        self.assertEqual(r.observed, "0")
        self.assertIn("only 0", r.detail)

    def test_minimum_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_profiles(Path(td), PROFILES_FILE_ALL_COMPLAIN)
            r = c6.probe_profiles_loaded(path, minimum=10)
        self.assertFalse(r.passed)
        self.assertIn("expected ≥ 10", r.detail)

    def test_file_missing(self):
        with tempfile.TemporaryDirectory() as td:
            r = c6.probe_profiles_loaded(Path(td) / "missing")
        self.assertFalse(r.passed)
        self.assertIn("not present", r.detail)


class TestPickFirstLoadedProfile(unittest.TestCase):
    def test_picks_first(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_profiles(Path(td), PROFILES_FILE_REALISTIC)
            name = c6.pick_first_loaded_profile(path)
        self.assertEqual(name, "/usr/bin/firefox")

    def test_empty_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_profiles(Path(td), PROFILES_FILE_EMPTY)
            self.assertIsNone(c6.pick_first_loaded_profile(path))

    def test_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(
                c6.pick_first_loaded_profile(Path(td) / "missing")
            )


# --- Sampled-profile-mode probe -------------------------------------------


class TestProbeSampledProfileMode(unittest.TestCase):
    def test_no_profile_passes_not_required(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_profiles(Path(td), PROFILES_FILE_EMPTY)
            r = c6.probe_sampled_profile_mode(None, path)
        self.assertTrue(r.passed)
        self.assertFalse(r.required)
        self.assertIn("no profiles loaded", r.detail)

    def test_enforce_mode_passes(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_profiles(Path(td), PROFILES_FILE_REALISTIC)
            r = c6.probe_sampled_profile_mode("/usr/bin/firefox", path)
        self.assertTrue(r.passed)
        self.assertIn("(enforce)", r.observed)

    def test_complain_mode_passes(self):
        # v1.0 default per design decision — complain is acceptable.
        with tempfile.TemporaryDirectory() as td:
            path = _write_profiles(Path(td), PROFILES_FILE_ALL_COMPLAIN)
            r = c6.probe_sampled_profile_mode("/usr/bin/firefox", path)
        self.assertTrue(r.passed)
        self.assertIn("(complain)", r.observed)

    def test_unconfined_mode_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_profiles(Path(td), PROFILES_FILE_WITH_UNCONFINED)
            r = c6.probe_sampled_profile_mode("/usr/bin/sshd", path)
        self.assertFalse(r.passed)
        self.assertIn("unconfined", r.detail)
        self.assertIn("loaded but not enforcing", r.detail)

    def test_profile_not_in_loaded_set_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_profiles(Path(td), PROFILES_FILE_REALISTIC)
            r = c6.probe_sampled_profile_mode("/usr/bin/no-such", path)
        self.assertFalse(r.passed)
        self.assertIn("not in loaded set", r.detail)

    def test_profiles_file_unreadable_fails(self):
        with tempfile.TemporaryDirectory() as td:
            r = c6.probe_sampled_profile_mode(
                "/usr/bin/firefox", Path(td) / "missing"
            )
        self.assertFalse(r.passed)
        self.assertIn("could not read", r.detail)


# --- End-to-end run tests -------------------------------------------------


class TestRun(unittest.TestCase):
    def test_all_required_pass(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            enabled = _write_enabled(tdp, "Y")
            profiles = _write_profiles(tdp, PROFILES_FILE_REALISTIC)
            with mock.patch(
                "installer.tests.class6_apparmor_state.shutil.which",
                return_value="/usr/bin/systemctl",
            ), mock.patch(
                "installer.tests.class6_apparmor_state.subprocess.run",
                _mock_systemctl("active"),
            ):
                report = c6.run(enabled, profiles)
        self.assertTrue(report.all_required_pass())
        self.assertEqual(report.sampled_profile, "/usr/bin/firefox")
        self.assertEqual(len(report.results), 4)

    def test_all_complain_passes(self):
        """v1.0 default rollout — all-complain is reviewer-defensible."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            enabled = _write_enabled(tdp, "Y")
            profiles = _write_profiles(tdp, PROFILES_FILE_ALL_COMPLAIN)
            with mock.patch(
                "installer.tests.class6_apparmor_state.shutil.which",
                return_value="/usr/bin/systemctl",
            ), mock.patch(
                "installer.tests.class6_apparmor_state.subprocess.run",
                _mock_systemctl("active"),
            ):
                report = c6.run(enabled, profiles)
        self.assertTrue(report.all_required_pass())

    def test_module_disabled_fails_overall(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            enabled = _write_enabled(tdp, "N")
            profiles = _write_profiles(tdp, PROFILES_FILE_REALISTIC)
            with mock.patch(
                "installer.tests.class6_apparmor_state.shutil.which",
                return_value="/usr/bin/systemctl",
            ), mock.patch(
                "installer.tests.class6_apparmor_state.subprocess.run",
                _mock_systemctl("active"),
            ):
                report = c6.run(enabled, profiles)
        self.assertFalse(report.all_required_pass())

    def test_service_inactive_fails_overall(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            enabled = _write_enabled(tdp, "Y")
            profiles = _write_profiles(tdp, PROFILES_FILE_REALISTIC)
            with mock.patch(
                "installer.tests.class6_apparmor_state.shutil.which",
                return_value="/usr/bin/systemctl",
            ), mock.patch(
                "installer.tests.class6_apparmor_state.subprocess.run",
                _mock_systemctl("inactive", returncode=3),
            ):
                report = c6.run(enabled, profiles)
        self.assertFalse(report.all_required_pass())

    def test_zero_profiles_fails_overall(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            enabled = _write_enabled(tdp, "Y")
            profiles = _write_profiles(tdp, PROFILES_FILE_EMPTY)
            with mock.patch(
                "installer.tests.class6_apparmor_state.shutil.which",
                return_value="/usr/bin/systemctl",
            ), mock.patch(
                "installer.tests.class6_apparmor_state.subprocess.run",
                _mock_systemctl("active"),
            ):
                report = c6.run(enabled, profiles)
        self.assertFalse(report.all_required_pass())

    def test_sample_profile_override(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            enabled = _write_enabled(tdp, "Y")
            profiles = _write_profiles(tdp, PROFILES_FILE_REALISTIC)
            with mock.patch(
                "installer.tests.class6_apparmor_state.shutil.which",
                return_value="/usr/bin/systemctl",
            ), mock.patch(
                "installer.tests.class6_apparmor_state.subprocess.run",
                _mock_systemctl("active"),
            ):
                report = c6.run(enabled, profiles,
                                sample_profile="/usr/bin/cups")
        self.assertEqual(report.sampled_profile, "/usr/bin/cups")
        self.assertTrue(report.all_required_pass())

    def test_unconfined_sampled_fails_overall(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            enabled = _write_enabled(tdp, "Y")
            profiles = _write_profiles(tdp, PROFILES_FILE_WITH_UNCONFINED)
            with mock.patch(
                "installer.tests.class6_apparmor_state.shutil.which",
                return_value="/usr/bin/systemctl",
            ), mock.patch(
                "installer.tests.class6_apparmor_state.subprocess.run",
                _mock_systemctl("active"),
            ):
                report = c6.run(enabled, profiles,
                                sample_profile="/usr/bin/sshd")
        self.assertFalse(report.all_required_pass())

    def test_json_report_shape(self):
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            enabled = _write_enabled(tdp, "Y")
            profiles = _write_profiles(tdp, PROFILES_FILE_REALISTIC)
            with mock.patch(
                "installer.tests.class6_apparmor_state.shutil.which",
                return_value="/usr/bin/systemctl",
            ), mock.patch(
                "installer.tests.class6_apparmor_state.subprocess.run",
                _mock_systemctl("active"),
            ):
                report = c6.run(enabled, profiles)
                d = report.to_dict()
        reloaded = _json.loads(_json.dumps(d))
        self.assertTrue(reloaded["all_required_pass"])
        self.assertEqual(reloaded["sampled_profile"], "/usr/bin/firefox")
        self.assertEqual(len(reloaded["results"]), 4)


# --- CLI smoke ------------------------------------------------------------


class TestCLI(unittest.TestCase):
    """stdout redirected so CLI print() doesn't pollute test runner."""

    def test_cli_good_path_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _write_enabled(tdp, "Y")
            _write_profiles(tdp, PROFILES_FILE_REALISTIC)
            with mock.patch(
                "installer.tests.class6_apparmor_state.shutil.which",
                return_value="/usr/bin/systemctl",
            ), mock.patch(
                "installer.tests.class6_apparmor_state.subprocess.run",
                _mock_systemctl("active"),
            ), contextlib.redirect_stdout(io.StringIO()):
                rc = c6.main([
                    "--enabled-path", str(tdp / "enabled"),
                    "--profiles-path", str(tdp / "profiles"),
                    "--json",
                ])
        self.assertEqual(rc, 0)

    def test_cli_module_disabled_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _write_enabled(tdp, "N")
            _write_profiles(tdp, PROFILES_FILE_REALISTIC)
            with mock.patch(
                "installer.tests.class6_apparmor_state.shutil.which",
                return_value="/usr/bin/systemctl",
            ), mock.patch(
                "installer.tests.class6_apparmor_state.subprocess.run",
                _mock_systemctl("active"),
            ), contextlib.redirect_stdout(io.StringIO()):
                rc = c6.main([
                    "--enabled-path", str(tdp / "enabled"),
                    "--profiles-path", str(tdp / "profiles"),
                    "--json",
                ])
        self.assertEqual(rc, 1)

    def test_cli_report_only_returns_zero_on_fail(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = c6.main([
                    "--enabled-path", str(tdp / "missing1"),
                    "--profiles-path", str(tdp / "missing2"),
                    "--json", "--report-only",
                ])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
