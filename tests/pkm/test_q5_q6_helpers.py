#!/usr/bin/env python3
"""Integration-style tests for pkm.services (Q5) + pkm.preflight (Q6).

Q5 — Service-restart manifest scan:
  - scan_manifest_for_services extracts unit names from path patterns
  - query_active_services calls systemctl is-active with a fake bin
  - classify_restart_requirement returns reboot for kernel/glibc/systemd-class
    packages, restart for live-service packages, none when nothing active
  - format_service_summary renders the multi-line user-facing output
  - run_restart_services calls systemctl restart with success/failure tracking

Q6 — Free-disk preflight:
  - estimate_required_space applies the extraction multiplier + floor
  - check_free_space gates against the safety margin
  - format_preflight_failure renders a user-facing error

Tests use fake-bin PATH override for systemctl interactions (GREEN + RED
paths exercised against the actual subprocess.run call surface) and
tmpfs-style real filesystem queries for disk-space checks (cannot easily
fake shutil.disk_usage). Per D-009 item 8 + arc lesson: integration-style
sanity check standard for any cross-module data-contract surface.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# systemctl-driven tests in this module use fake-bin PATH overrides to
# intercept the systemctl subprocess. The fake-bin pattern needs POSIX
# shell semantics (sh script with shebang + chmod +x) which doesn't
# translate cleanly to Windows where executables resolve via PATHEXT.
# Decorate class-level so the pure-Python helper tests still run.
_LINUX_ONLY = unittest.skipUnless(
    sys.platform.startswith("linux"),
    "Linux-only: systemctl fake-bin pattern needs POSIX shell + chmod +x",
)

from pkm.services import (
    REBOOT_TRIGGER_PACKAGES,
    scan_manifest_for_services,
    query_active_services,
    classify_restart_requirement,
    format_service_summary,
    run_restart_services,
)
from pkm.preflight import (
    EXTRACTION_MULTIPLIER,
    MIN_HEADROOM_BYTES,
    SAFETY_MARGIN_MULTIPLIER,
    estimate_required_space,
    check_free_space,
    format_preflight_failure,
)


def _make_fake_systemctl(bindir, behavior):
    """Create a fake systemctl stub that responds based on the behavior dict.

    behavior: dict {unit_name: exit_code} for is-active calls.
              "default" key is the fallback for unknown units.
              Also supports "restart_exit_code" for restart-call exit code.
    """
    path = Path(bindir) / "systemctl"
    log = Path(bindir) / "systemctl.log"
    # Build a bash script that dispatches based on first non-flag arg.
    behavior_lines = []
    for unit, exit_code in behavior.items():
        if unit not in ("default", "restart_exit_code"):
            behavior_lines.append(f'    "{unit}") exit {exit_code} ;;')
    default_code = behavior.get("default", 3)  # systemd's "inactive" exit
    restart_code = behavior.get("restart_exit_code", 0)
    script = f"""#!/bin/bash
echo "$*" >> "{log}"
ACTION="$1"
shift
# Strip --quiet flag if present
if [ "$1" = "--quiet" ]; then shift; fi
UNIT="$1"
case "$ACTION" in
    is-active)
        case "$UNIT" in
{chr(10).join(behavior_lines)}
            *) exit {default_code} ;;
        esac
        ;;
    restart)
        exit {restart_code}
        ;;
    *)
        exit 0 ;;
esac
"""
    path.write_text(script)
    path.chmod(0o755)
    return path, log


class TestScanManifestForServices(unittest.TestCase):

    def test_systemd_service_paths_extracted(self):
        units = scan_manifest_for_services([
            "usr/lib/systemd/system/postgresql.service",
            "usr/lib/systemd/system/nginx.service",
            "usr/bin/postgres",
        ])
        self.assertEqual(units, ["postgresql.service", "nginx.service"])

    def test_etc_systemd_paths_extracted(self):
        units = scan_manifest_for_services([
            "etc/systemd/system/custom.service",
        ])
        self.assertEqual(units, ["custom.service"])

    def test_sysv_init_scripts_extracted(self):
        units = scan_manifest_for_services([
            "etc/init.d/nginx",
            "etc/init.d/postgresql",
        ])
        self.assertEqual(units, ["nginx", "postgresql"])

    def test_no_service_paths_returns_empty(self):
        units = scan_manifest_for_services([
            "usr/bin/foo", "etc/foo.conf", "usr/share/foo/data",
        ])
        self.assertEqual(units, [])

    def test_directories_skipped(self):
        units = scan_manifest_for_services([
            "usr/lib/systemd/system/",
            "usr/lib/systemd/system/postgresql.service",
        ])
        self.assertEqual(units, ["postgresql.service"])

    def test_nested_service_paths_not_matched(self):
        # Files inside a sub-dir of systemd/system/ should not be unit-classified.
        units = scan_manifest_for_services([
            "usr/lib/systemd/system/foo.service.d/override.conf",
        ])
        self.assertEqual(units, [])


@_LINUX_ONLY
class TestQueryActiveServices(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-q5-")
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._orig_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}:{self._orig_path}"

    def tearDown(self):
        os.environ["PATH"] = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_input_returns_empty(self):
        self.assertEqual(query_active_services([]), [])

    def test_active_units_returned(self):
        _make_fake_systemctl(self.bin, {
            "postgresql.service": 0,  # active
            "nginx.service": 3,        # inactive
            "default": 3,
        })
        active = query_active_services(["postgresql.service", "nginx.service"])
        self.assertEqual(active, ["postgresql.service"])

    def test_no_systemd_returns_empty(self):
        # Empty PATH = no systemctl available; should return empty silently.
        os.environ["PATH"] = "/nonexistent-bin-only"
        try:
            self.assertEqual(query_active_services(["foo.service"]), [])
        finally:
            os.environ["PATH"] = f"{self.bin}:{self._orig_path}"


@_LINUX_ONLY
class TestClassifyRestartRequirement(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-q5-classify-")
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._orig_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}:{self._orig_path}"

    def tearDown(self):
        os.environ["PATH"] = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_kernel_returns_reboot(self):
        result = classify_restart_requirement("linux-kernel", ["usr/lib/modules/6.6/foo.ko"])
        self.assertEqual(result["requirement"], "reboot")

    def test_glibc_returns_reboot(self):
        result = classify_restart_requirement("glibc", ["lib/libc.so.6"])
        self.assertEqual(result["requirement"], "reboot")

    def test_systemd_returns_reboot(self):
        result = classify_restart_requirement("systemd", ["usr/bin/systemd"])
        self.assertEqual(result["requirement"], "reboot")

    def test_reboot_trigger_set_membership(self):
        # Every entry in REBOOT_TRIGGER_PACKAGES must classify as reboot.
        for pkg in REBOOT_TRIGGER_PACKAGES:
            result = classify_restart_requirement(pkg, [])
            self.assertEqual(
                result["requirement"], "reboot",
                f"{pkg} in REBOOT_TRIGGER_PACKAGES but did not classify as reboot",
            )

    def test_no_units_returns_none(self):
        result = classify_restart_requirement("vim", ["usr/bin/vim", "usr/share/vim/help.txt"])
        self.assertEqual(result["requirement"], "none")
        self.assertEqual(result["services"], [])

    def test_units_present_but_inactive_returns_none(self):
        _make_fake_systemctl(self.bin, {"default": 3})  # all inactive
        result = classify_restart_requirement(
            "postgresql", ["usr/lib/systemd/system/postgresql.service"],
        )
        self.assertEqual(result["requirement"], "none")
        self.assertEqual(result["services"], [])

    def test_active_units_return_restart(self):
        _make_fake_systemctl(self.bin, {
            "postgresql.service": 0,
            "default": 3,
        })
        result = classify_restart_requirement(
            "postgresql", ["usr/lib/systemd/system/postgresql.service"],
        )
        self.assertEqual(result["requirement"], "restart")
        self.assertEqual(result["services"], ["postgresql.service"])


class TestFormatServiceSummary(unittest.TestCase):

    def test_none_returns_empty_string(self):
        self.assertEqual(
            format_service_summary({"requirement": "none", "services": [], "reason": "x"}),
            "",
        )

    def test_reboot_message(self):
        s = format_service_summary({
            "requirement": "reboot",
            "services": [],
            "reason": "kernel change",
        })
        self.assertIn("REBOOT REQUIRED", s)
        self.assertIn("sudo reboot", s)

    def test_restart_message_lists_services(self):
        s = format_service_summary({
            "requirement": "restart",
            "services": ["nginx.service", "postgresql.service"],
            "reason": "2 services",
        })
        self.assertIn("nginx.service", s)
        self.assertIn("postgresql.service", s)
        self.assertIn("pkm restart-services", s)


@_LINUX_ONLY
class TestRunRestartServices(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-q5-restart-")
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._orig_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}:{self._orig_path}"

    def tearDown(self):
        os.environ["PATH"] = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_succeed_returns_all_true(self):
        _make_fake_systemctl(self.bin, {"restart_exit_code": 0})
        result = run_restart_services(["foo.service", "bar.service"])
        self.assertEqual(result, {"foo.service": True, "bar.service": True})

    def test_failure_per_unit_tracked(self):
        _make_fake_systemctl(self.bin, {"restart_exit_code": 1})
        result = run_restart_services(["foo.service"])
        self.assertEqual(result, {"foo.service": False})

    def test_no_systemctl_returns_all_false(self):
        os.environ["PATH"] = "/nonexistent-bin"
        try:
            result = run_restart_services(["foo.service"])
            self.assertEqual(result, {"foo.service": False})
        finally:
            os.environ["PATH"] = f"{self.bin}:{self._orig_path}"


# ---------------------------------------------------------------------------
# Q6 — free-disk preflight
# ---------------------------------------------------------------------------


class TestEstimateRequiredSpace(unittest.TestCase):

    def test_typical_archives_get_1_5x_multiplier(self):
        # 200 MiB total compressed → 300 MiB estimated extracted.
        sizes = [100 * 1024 * 1024, 100 * 1024 * 1024]
        est = estimate_required_space(sizes)
        self.assertEqual(est, int(200 * 1024 * 1024 * EXTRACTION_MULTIPLIER))

    def test_below_floor_clamps_to_min_headroom(self):
        # 1 MiB archive total → 1.5 MiB estimate, but floor is 100 MiB.
        est = estimate_required_space([1 * 1024 * 1024])
        self.assertEqual(est, MIN_HEADROOM_BYTES)

    def test_empty_input_returns_floor(self):
        self.assertEqual(estimate_required_space([]), MIN_HEADROOM_BYTES)

    def test_huge_archive_no_overflow(self):
        # 10 GiB compressed * 1.5 = 15 GiB — fits in int64.
        sizes = [10 * 1024 * 1024 * 1024]
        est = estimate_required_space(sizes)
        self.assertEqual(est, int(10 * 1024 * 1024 * 1024 * EXTRACTION_MULTIPLIER))


class TestCheckFreeSpace(unittest.TestCase):

    def test_sufficient_space_returns_ok(self):
        # Required = 1 MiB; available filesystem (/tmp on the host) is
        # certainly much more than that.
        result = check_free_space(1024 * 1024, "/tmp")
        self.assertTrue(result["ok"])
        self.assertEqual(result["required_bytes"], 1024 * 1024)
        self.assertEqual(
            result["required_with_margin"],
            int(1024 * 1024 * SAFETY_MARGIN_MULTIPLIER),
        )

    def test_insufficient_space_returns_not_ok(self):
        # Required = 10 EiB (way more than any real filesystem) → not ok.
        result = check_free_space(10 * 1024 ** 6, "/tmp")
        self.assertFalse(result["ok"])

    def test_non_existent_path_walks_up_to_parent(self):
        # /tmp/<random>/sub/dir/that/never/existed — should walk up to /tmp.
        nonexistent = "/tmp/pkm-test-never-exists-xxx/sub/dir"
        result = check_free_space(1024, nonexistent)
        # Just need it to not crash; the disk_usage should report something.
        self.assertIn("available_bytes", result)


class TestFormatPreflightFailure(unittest.TestCase):

    def test_message_includes_avail_required_target(self):
        check = {
            "ok": False,
            "available_bytes": 50 * 1024 * 1024,
            "required_bytes": 100 * 1024 * 1024,
            "required_with_margin": 110 * 1024 * 1024,
        }
        msg = format_preflight_failure(check, "/var/cache/pkm")
        self.assertIn("/var/cache/pkm", msg)
        self.assertIn("50 MiB", msg)
        self.assertIn("110 MiB", msg)
        self.assertIn("Refusing", msg)


if __name__ == "__main__":
    unittest.main()
