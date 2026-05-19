#!/usr/bin/env python3
"""Integration-style tests for pkm.hooks (Q2 hook framework).

Covers both layered mechanisms:
  - Content-triggered canonical hooks (depmod / ldconfig / glib /
    apparmor / ca-trust / icon-cache / font-cache / desktop-db / mime-db)
    invoked via file_list pattern matching.
  - Archive .scripts/<event>.sh lifecycle hooks invoked from a staging dir.

Tests use a temporary fake-bin directory prepended to PATH so the hook
framework calls our stub binaries instead of the real depmod / ldconfig /
etc. Stub binaries log their argv + return a configurable exit code,
exercising both GREEN (exit 0) and RED (non-zero exit) paths.

Critical vs cosmetic semantics are exercised end-to-end:
  - Critical hook failure → HookResult.critical_failures populated.
  - Cosmetic hook failure → HookResult.cosmetic_failures populated; the
    overall install can proceed (caller's call).
  - Archive lifecycle hook exit 2 → reclassified as cosmetic.
  - Archive lifecycle hook other non-zero exit → critical.

Env hygiene tests verify HOOK_ENV_ALLOWLIST does its job — LD_PRELOAD
and PYTHONPATH from the parent env never reach hook execution. PKM_*
vars are present + carry the expected per-hook values.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from pkm.hooks import (
    HOOK_ENV_ALLOWLIST,
    LIFECYCLE_EVENTS,
    CANONICAL_HOOKS,
    run_canonical_hooks,
    run_archive_lifecycle_hook,
    format_hook_summary,
)


def _make_fake_bin(bindir, name, exit_code=0):
    """Create an executable shell stub at bindir/name.

    The stub logs its argv to bindir/<name>.log (one line per invocation,
    space-separated quoted args) and the inherited env to bindir/<name>.env
    (as KEY=VALUE lines, sorted). Exits with the configured exit_code so
    tests can exercise both GREEN and RED paths.
    """
    path = Path(bindir) / name
    log = Path(bindir) / f"{name}.log"
    env_log = Path(bindir) / f"{name}.env"
    path.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'env | sort > "{env_log}"\n'
        f"exit {exit_code}\n"
    )
    path.chmod(0o755)
    return path, log, env_log


class TestRunCanonicalHooks(unittest.TestCase):
    """Direct unit tests for run_canonical_hooks with fake bin dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-hooktest-")
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self.root = Path(self.tmp) / "root"
        self.root.mkdir()
        # Prepend fake bin to PATH; record original for tearDown restore.
        self._orig_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin}:{self._orig_path}"

    def tearDown(self):
        os.environ["PATH"] = self._orig_path
        # Best-effort cleanup; tmp is OS-managed if rmtree fails.
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_depmod_fires_with_kver_extracted(self):
        _, log, _ = _make_fake_bin(self.bin, "depmod", exit_code=0)
        file_list = [
            "usr/lib/modules/6.6.50-igos/kernel/drivers/foo.ko",
            "usr/lib/modules/6.6.50-igos/modules.dep",
        ]
        result = run_canonical_hooks(self.root, file_list, "linux-kernel", "6.6.50", "install")
        self.assertEqual(result.critical_failures, [])
        self.assertEqual(result.cosmetic_failures, [])
        self.assertTrue(log.exists(), "depmod stub should have been invoked")
        invocation = log.read_text().strip()
        self.assertIn("6.6.50-igos", invocation,
                      "depmod should be called with the kver extracted from path")
        self.assertIn("-b", invocation,
                      "depmod should target the install root via -b when root != /")

    def test_ldconfig_fires_for_so_files(self):
        _, log, _ = _make_fake_bin(self.bin, "ldconfig", exit_code=0)
        file_list = ["usr/lib/libfoo.so.1.2.3", "usr/lib/libfoo.so.1", "usr/include/foo.h"]
        result = run_canonical_hooks(self.root, file_list, "libfoo", "1.2.3", "install")
        self.assertEqual(result.critical_failures, [])
        self.assertTrue(log.exists(), "ldconfig stub should have been invoked")

    def test_canonical_hook_silent_when_no_match(self):
        # File list with no canonical-trigger paths — nothing should fire.
        _make_fake_bin(self.bin, "depmod")
        _make_fake_bin(self.bin, "ldconfig")
        file_list = ["usr/bin/foo", "etc/foo.conf"]
        result = run_canonical_hooks(self.root, file_list, "foo", "1.0", "install")
        self.assertEqual(result.critical_failures, [])
        self.assertEqual(result.cosmetic_failures, [])
        self.assertEqual(result.messages, [])

    def test_critical_hook_failure_flags_critical(self):
        # depmod exits non-zero → critical_failures should include "depmod".
        _make_fake_bin(self.bin, "depmod", exit_code=1)
        file_list = ["usr/lib/modules/6.6.50-igos/kernel/drivers/foo.ko"]
        result = run_canonical_hooks(self.root, file_list, "linux-kernel", "6.6.50", "install")
        self.assertIn("depmod", result.critical_failures)
        self.assertEqual(result.cosmetic_failures, [])

    def test_cosmetic_hook_failure_flags_cosmetic_not_critical(self):
        # gtk-update-icon-cache (cosmetic) exits non-zero → cosmetic_failures.
        _make_fake_bin(self.bin, "gtk-update-icon-cache", exit_code=1)
        file_list = ["usr/share/icons/InterGenOS/16x16/foo.png"]
        result = run_canonical_hooks(self.root, file_list, "intergenos-icons", "1.0", "install")
        self.assertEqual(result.critical_failures, [])
        self.assertIn("icon-cache", result.cosmetic_failures)

    def test_missing_canonical_command_flags_failure(self):
        # No depmod stub anywhere in PATH → subprocess.run raises FileNotFoundError.
        # Critical hook's exec-failure path should flag critical_failures.
        os.environ["PATH"] = "/nonexistent-dir-deliberately-empty"
        try:
            file_list = ["usr/lib/modules/6.6.50-igos/kernel/drivers/foo.ko"]
            result = run_canonical_hooks(self.root, file_list, "linux-kernel", "6.6.50", "install")
            self.assertIn("depmod", result.critical_failures)
        finally:
            os.environ["PATH"] = f"{self.bin}:{self._orig_path}"

    def test_apparmor_reload_passes_profile_paths(self):
        _, log, _ = _make_fake_bin(self.bin, "apparmor_parser", exit_code=0)
        file_list = ["etc/apparmor.d/usr.bin.foo", "etc/apparmor.d/usr.bin.bar"]
        result = run_canonical_hooks(self.root, file_list, "foo-apparmor", "1.0", "install")
        self.assertEqual(result.critical_failures, [])
        self.assertTrue(log.exists())
        invocation = log.read_text().strip()
        self.assertIn("-r", invocation, "apparmor_parser called with -r")
        self.assertIn("usr.bin.foo", invocation)
        self.assertIn("usr.bin.bar", invocation)


class TestArchiveLifecycleHook(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-archhook-")
        self.staging = Path(self.tmp) / "staging"
        self.staging.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_script(self, event, body, exit_code=0, executable=True):
        scripts = self.staging / ".scripts"
        scripts.mkdir(exist_ok=True)
        script = scripts / f"{event}.sh"
        script.write_text(f"#!/bin/bash\n{body}\nexit {exit_code}\n")
        if executable:
            script.chmod(0o755)
        else:
            script.chmod(0o644)
        return script

    def test_absent_script_silent_skip(self):
        # No .scripts/ at all — silent skip, all-zero result.
        result = run_archive_lifecycle_hook(self.staging, "post_install", "foo", "1.0", "/")
        self.assertEqual(result.critical_failures, [])
        self.assertEqual(result.cosmetic_failures, [])
        self.assertEqual(result.messages, [])

    def test_present_script_executes(self):
        marker = Path(self.tmp) / "ran.marker"
        self._make_script("post_install", f'touch "{marker}"', exit_code=0)
        result = run_archive_lifecycle_hook(self.staging, "post_install", "foo", "1.0", "/")
        self.assertEqual(result.critical_failures, [])
        self.assertEqual(result.cosmetic_failures, [])
        self.assertTrue(marker.exists(), "lifecycle hook should have executed")

    def test_exit_0_clean_pass(self):
        self._make_script("post_install", "echo ok", exit_code=0)
        result = run_archive_lifecycle_hook(self.staging, "post_install", "foo", "1.0", "/")
        self.assertEqual(result.critical_failures, [])
        self.assertEqual(result.cosmetic_failures, [])

    def test_exit_2_reclassified_as_cosmetic(self):
        self._make_script("post_install", "echo cosmetic-fail", exit_code=2)
        result = run_archive_lifecycle_hook(self.staging, "post_install", "foo", "1.0", "/")
        self.assertEqual(result.critical_failures, [])
        self.assertEqual(result.cosmetic_failures, ["post_install"])

    def test_other_non_zero_exit_critical(self):
        self._make_script("post_install", "echo bad", exit_code=1)
        result = run_archive_lifecycle_hook(self.staging, "post_install", "foo", "1.0", "/")
        self.assertEqual(result.critical_failures, ["post_install"])
        self.assertEqual(result.cosmetic_failures, [])

    def test_non_executable_script_chmod_recovers(self):
        # tar extraction can drop x bit; the hook framework chmods on absence.
        self._make_script("post_install", "echo ok", exit_code=0, executable=False)
        result = run_archive_lifecycle_hook(self.staging, "post_install", "foo", "1.0", "/")
        self.assertEqual(result.critical_failures, [])

    def test_unknown_event_rejected(self):
        with self.assertRaises(ValueError):
            run_archive_lifecycle_hook(self.staging, "post_oops", "foo", "1.0", "/")


class TestHookEnvHygiene(unittest.TestCase):
    """Verify HOOK_ENV_ALLOWLIST does its job — LD_PRELOAD / PYTHONPATH never
    reach hook execution; PKM_* vars are present with expected values."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-envhook-")
        self.staging = Path(self.tmp) / "staging"
        self.staging.mkdir()
        # Pollute parent env with values the allowlist must drop.
        self._injected = {
            "LD_PRELOAD": "/tmp/evil.so",
            "PYTHONPATH": "/tmp/evil-py",
            "HTTP_PROXY": "http://evil-proxy",
            "HTTPS_PROXY": "http://evil-proxy",
            "LD_LIBRARY_PATH": "/tmp/evil-lib",
        }
        for k, v in self._injected.items():
            os.environ[k] = v

    def tearDown(self):
        for k in self._injected:
            os.environ.pop(k, None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_archive_hook_env_excludes_attack_vars(self):
        env_dump = Path(self.tmp) / "env.dump"
        scripts = self.staging / ".scripts"
        scripts.mkdir()
        script = scripts / "post_install.sh"
        script.write_text(f"#!/bin/bash\nenv | sort > '{env_dump}'\nexit 0\n")
        script.chmod(0o755)
        result = run_archive_lifecycle_hook(self.staging, "post_install", "foo", "1.0", "/tmp/root")
        self.assertEqual(result.critical_failures, [])
        env_text = env_dump.read_text()
        # None of the injected attack vars should appear in the hook env.
        for k in self._injected:
            self.assertNotRegex(
                env_text, rf"^{k}=", f"{k} leaked into hook env via parent inheritance"
            )
        # PKM_PACKAGE_* vars must be present with expected values.
        self.assertIn("PKM_PACKAGE_NAME=foo\n", env_text)
        self.assertIn("PKM_PACKAGE_VERSION=1.0\n", env_text)
        self.assertIn("PKM_PACKAGE_ROOT=/tmp/root\n", env_text)
        self.assertIn("PKM_PACKAGE_OPERATION=post_install\n", env_text)


class TestFormatHookSummary(unittest.TestCase):
    """format_hook_summary aggregates messages + appends failure summaries."""

    def test_empty_result_empty_summary(self):
        from pkm.hooks import HookResult
        r = HookResult([], [], [])
        self.assertEqual(format_hook_summary(r), "")

    def test_critical_failure_in_summary(self):
        from pkm.hooks import HookResult
        r = HookResult(["depmod"], [], ["  hook[depmod] CRITICAL: exit 1; ..."])
        summary = format_hook_summary(r)
        self.assertIn("depmod", summary)
        self.assertIn("Rollback recommended", summary)

    def test_cosmetic_failure_in_summary(self):
        from pkm.hooks import HookResult
        r = HookResult([], ["icon-cache"], ["  hook[icon-cache] WARN: ..."])
        summary = format_hook_summary(r)
        self.assertIn("icon-cache", summary)
        self.assertIn("non-blocking", summary)

    def test_multiple_results_aggregated(self):
        from pkm.hooks import HookResult
        a = HookResult(["depmod"], [], ["  hook[depmod] CRITICAL"])
        b = HookResult([], ["icon-cache"], ["  hook[icon-cache] WARN"])
        summary = format_hook_summary(a, b)
        self.assertIn("depmod", summary)
        self.assertIn("icon-cache", summary)


class TestLifecycleEventsCoverage(unittest.TestCase):
    """Sanity: every lifecycle event is recognized; misspellings rejected."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-lifecycle-")
        self.staging = Path(self.tmp) / "staging"
        self.staging.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_lifecycle_events_accepted(self):
        for event in LIFECYCLE_EVENTS:
            # No script present, so silent-skip; the event itself must be
            # accepted by run_archive_lifecycle_hook without ValueError.
            result = run_archive_lifecycle_hook(self.staging, event, "foo", "1.0", "/")
            self.assertEqual(result.critical_failures, [])

    def test_all_canonical_hooks_have_unique_ids(self):
        ids = [h.id for h in CANONICAL_HOOKS]
        self.assertEqual(len(ids), len(set(ids)), "canonical hook IDs must be unique")


if __name__ == "__main__":
    unittest.main()
