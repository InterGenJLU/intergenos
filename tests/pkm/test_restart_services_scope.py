#!/usr/bin/env python3
"""`pkm restart-services` is bounded to this boot and never ends the login
session (R001.3 gating row 41).

What went wrong: `--all` walked every installed package and restarted every
active unit any of them owned — on a workstation that was the system bus,
the display manager, logind, sshd, NetworkManager and polkit among 23 units,
and the desktop session ended (2026-09-11). Its REBOOT REQUIRED lines fired
for every reboot-class package on the machine regardless of when it was
installed.

What these tests pin:
  * the session-carrying unit set and its canonical-name matching;
  * the boot time read from /proc/stat's `btime` row (and None when it
    cannot be read — the caller must refuse, never guess);
  * a unit's active-since instant derived from systemd's monotonic stamp;
  * the classification: a package whose only running units carry the
    session is REBOOT-class; one with both kinds restarts only the others;
  * PackageDB.packages_changed_since: successful installs/upgrades at or
    after the stamp, newest stamp per package, failures and removals excluded;
  * `--list` skips packages that predate this boot and counts them;
  * `--all` refuses without a readable boot time; restarts nothing when
    nothing changed since boot; restarts only units whose active-since
    predates the package's upgrade; reports units already restarted after
    the upgrade; never restarts a session-carrying unit and says REBOOT
    REQUIRED for it instead;
  * a session-carrying unit named explicitly is restarted, with a warning.

Systemd is never touched: the fixed systemctl module constant is bound to a
fake executable and the restart runner is observed, not run.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pkm import cli  # noqa: E402
from pkm import services  # noqa: E402
from pkm.database import PackageDB  # noqa: E402
from pkm.services import (  # noqa: E402
    SESSION_CRITICAL_UNITS,
    boot_time_epoch,
    classify_restart_requirement,
    is_session_critical,
    unit_active_since_epoch,
    unit_canonical_name,
)


def _write_fake_systemctl(bindir, active, active_since_usec=None):
    """A `systemctl` that answers `is-active <unit>` (0 for units in `active`,
    3 otherwise) and `show -p ActiveEnterTimestampMonotonic --value <unit>`
    (the microsecond value from `active_since_usec`, or an empty line)."""
    active_since_usec = active_since_usec or {}
    lines = ["#!/bin/bash", 'cmd="$1"; shift', 'unit="${@: -1}"',
             'if [ "$cmd" = is-active ]; then', '  case "$unit" in']
    for unit in active:
        lines.append(f'    {unit}) exit 0;;')
    lines += ['    *) exit 3;;', '  esac', 'fi',
              'if [ "$cmd" = show ]; then', '  case "$unit" in']
    for unit, usec in active_since_usec.items():
        lines.append(f'    {unit}) echo {usec}; exit 0;;')
    lines += ['    *) echo ""; exit 0;;', '  esac', 'fi', 'exit 1']
    path = Path(bindir) / "systemctl"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    services.SYSTEMCTL = str(path)


class SessionCriticalSetTest(unittest.TestCase):
    def test_the_named_units_carry_the_session(self):
        for unit in ("dbus.service", "dbus-broker.service",
                     "systemd-logind.service", "systemd-journald.service",
                     "gdm.service", "display-manager.service"):
            self.assertIn(unit, SESSION_CRITICAL_UNITS)
            self.assertTrue(is_session_critical(unit))

    def test_a_bare_name_is_matched_as_a_service(self):
        self.assertTrue(is_session_critical("gdm"))
        self.assertEqual(unit_canonical_name("gdm"), "gdm.service")
        self.assertEqual(unit_canonical_name("dbus.socket"), "dbus.socket")

    def test_ordinary_units_are_not_session_critical(self):
        for unit in ("sshd.service", "NetworkManager.service", "cups",
                     "polkit.service", "avahi-daemon.service"):
            self.assertFalse(is_session_critical(unit), unit)


class BootTimeTest(unittest.TestCase):
    def test_btime_row_is_read(self):
        with tempfile.NamedTemporaryFile("w", suffix="stat", delete=False) as fh:
            fh.write("cpu  1 2 3 4\nbtime 1757900000\nprocesses 5\n")
            path = fh.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(boot_time_epoch(path), 1757900000)

    def test_missing_or_malformed_stat_is_none(self):
        self.assertIsNone(boot_time_epoch("/nonexistent/proc/stat"))
        with tempfile.NamedTemporaryFile("w", suffix="stat", delete=False) as fh:
            fh.write("cpu  1 2 3 4\nbtime notanumber\n")
            path = fh.name
        self.addCleanup(os.unlink, path)
        self.assertIsNone(boot_time_epoch(path))
        with tempfile.NamedTemporaryFile("w", suffix="stat", delete=False) as fh:
            fh.write("cpu  1 2 3 4\n")
            path2 = fh.name
        self.addCleanup(os.unlink, path2)
        self.assertIsNone(boot_time_epoch(path2))


class ActiveSinceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = self.tmp.name
        self._old_path = os.environ.get("PATH", "")
        self._old_systemctl = services.SYSTEMCTL
        os.environ["PATH"] = self.bin + os.pathsep + self._old_path
        self.addCleanup(os.environ.__setitem__, "PATH", self._old_path)
        self.addCleanup(setattr, services, "SYSTEMCTL", self._old_systemctl)

    def test_monotonic_stamp_is_added_to_boot_time(self):
        _write_fake_systemctl(self.bin, ["sshd.service"],
                              {"sshd.service": 90_000_000})  # 90 s after boot
        self.assertAlmostEqual(
            unit_active_since_epoch("sshd.service", 1_000_000), 1_000_090.0)

    def test_unknown_unit_or_zero_stamp_is_none(self):
        _write_fake_systemctl(self.bin, [], {"never.service": 0})
        self.assertIsNone(unit_active_since_epoch("never.service", 1_000_000))
        self.assertIsNone(unit_active_since_epoch("absent.service", 1_000_000))

    def test_boot_instant_is_precise_not_the_truncated_btime(self):
        # btime says 1_000_000 (truncated); /proc/uptime says the boot was at
        # 1_000_000.73. A unit that entered active 90.5 s after boot is at
        # 1_000_091.23, not 1_000_090.5 — the sub-second part decides whether a
        # unit restarted within the same second as its upgrade reads current.
        _write_fake_systemctl(self.bin, ["sshd.service"],
                              {"sshd.service": 90_500_000})
        with patch.object(services, "precise_boot_epoch",
                          return_value=1_000_000.73):
            self.assertAlmostEqual(
                unit_active_since_epoch("sshd.service", 1_000_000), 1_000_091.23)
        # A precise reading that disagrees with btime by two seconds or more is
        # not the same boot (a stale reading) and is ignored.
        with patch.object(services, "precise_boot_epoch",
                          return_value=1_000_005.0):
            self.assertAlmostEqual(
                unit_active_since_epoch("sshd.service", 1_000_000), 1_000_090.5)

    def test_precise_boot_epoch_reads_proc_uptime(self):
        with tempfile.NamedTemporaryFile("w", suffix="uptime", delete=False) as fh:
            fh.write("123.45 400.00\n")
            path = fh.name
        self.addCleanup(os.unlink, path)
        self.assertAlmostEqual(
            services.precise_boot_epoch(path, now=1_000_123.45), 1_000_000.0)
        self.assertIsNone(services.precise_boot_epoch("/nonexistent/uptime"))

    def test_no_boot_time_is_none(self):
        _write_fake_systemctl(self.bin, ["sshd.service"],
                              {"sshd.service": 90_000_000})
        self.assertIsNone(unit_active_since_epoch("sshd.service", None))


class ClassificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = self.tmp.name
        self._old_path = os.environ.get("PATH", "")
        self._old_systemctl = services.SYSTEMCTL
        os.environ["PATH"] = self.bin + os.pathsep + self._old_path
        self.addCleanup(os.environ.__setitem__, "PATH", self._old_path)
        self.addCleanup(setattr, services, "SYSTEMCTL", self._old_systemctl)

    def test_only_session_units_running_is_reboot_class(self):
        _write_fake_systemctl(self.bin, ["dbus.service"])
        result = classify_restart_requirement(
            "dbus", ["usr/lib/systemd/system/dbus.service", "usr/bin/dbus-daemon"])
        self.assertEqual(result["requirement"], "reboot")
        self.assertEqual(result["services"], [])
        self.assertEqual(result["session_critical"], ["dbus.service"])
        self.assertIn("login session", result["reason"])

    def test_mixed_units_restart_only_the_ordinary_ones(self):
        _write_fake_systemctl(self.bin, ["gdm.service", "gdm-helper.service"])
        result = classify_restart_requirement(
            "gdm", ["usr/lib/systemd/system/gdm.service",
                    "usr/lib/systemd/system/gdm-helper.service"])
        self.assertEqual(result["requirement"], "restart")
        self.assertEqual(result["services"], ["gdm-helper.service"])
        self.assertEqual(result["session_critical"], ["gdm.service"])
        self.assertIn("not restarted live", result["reason"])

    def test_ordinary_running_unit_is_restart_class_unchanged(self):
        _write_fake_systemctl(self.bin, ["sshd.service"])
        result = classify_restart_requirement(
            "openssh", ["usr/lib/systemd/system/sshd.service"])
        self.assertEqual(result["requirement"], "restart")
        self.assertEqual(result["services"], ["sshd.service"])
        self.assertEqual(result["session_critical"], [])


class PackagesChangedSinceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "root"
        root.mkdir()
        self.db = PackageDB(str(Path(self.tmp.name) / "pkm.db"), root=str(root))
        self.addCleanup(self.db.close)

    def _row(self, ts, op, name, success=1):
        self.db.conn.execute(
            "INSERT INTO history (timestamp, operation, package_name, success)"
            " VALUES (?, ?, ?, ?)", (ts, op, name, success))
        self.db.conn.commit()

    def test_rows_at_or_after_the_stamp_newest_per_package(self):
        self._row("2026-09-15T10:00:00+00:00", "install", "old-before-boot")
        self._row("2026-09-15T12:00:00+00:00", "upgrade", "openssh")
        self._row("2026-09-15T13:00:00+00:00", "upgrade", "openssh")
        self._row("2026-09-15T12:30:00+00:00", "install", "wsdd")
        self._row("2026-09-15T12:45:00+00:00", "upgrade", "failed-one", success=0)
        self._row("2026-09-15T12:50:00+00:00", "remove", "gone")
        changed = self.db.packages_changed_since("2026-09-15T11:00:00+00:00")
        self.assertEqual(changed, {"openssh": "2026-09-15T13:00:00+00:00",
                                   "wsdd": "2026-09-15T12:30:00+00:00"})

    def test_the_stamp_itself_counts_and_nothing_before_it(self):
        self._row("2026-09-15T11:00:00+00:00", "install", "exact")
        self._row("2026-09-15T10:59:59+00:00", "install", "just-before")
        changed = self.db.packages_changed_since("2026-09-15T11:00:00+00:00")
        self.assertEqual(set(changed), {"exact"})

    def test_real_log_operation_stamps_are_comparable(self):
        boot = datetime.now(timezone.utc).timestamp() - 60
        since_iso = datetime.fromtimestamp(boot, timezone.utc).isoformat()
        self.db.log_operation("upgrade", "openssh", "10.1p1", "10.2p1")
        self.assertIn("openssh", self.db.packages_changed_since(since_iso))


class RestartServicesCommandTest(unittest.TestCase):
    """cmd_restart_services against a real database and a fake systemd."""

    BOOT = 1_757_900_000  # an arbitrary epoch second; stamps derive from it

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "root"
        root.mkdir()
        self.db = PackageDB(str(Path(self.tmp.name) / "pkm.db"), root=str(root))
        self.addCleanup(self.db.close)
        self.bin = str(Path(self.tmp.name) / "bin")
        os.mkdir(self.bin)
        self._old_path = os.environ.get("PATH", "")
        self._old_systemctl = services.SYSTEMCTL
        os.environ["PATH"] = self.bin + os.pathsep + self._old_path
        self.addCleanup(os.environ.__setitem__, "PATH", self._old_path)
        self.addCleanup(setattr, services, "SYSTEMCTL", self._old_systemctl)
        self.restarted = []

    def _package(self, name, files, changed_at_offset_s=None, reboot=False):
        pid = self.db.add_installed(name, "1.0", release=1, tier="core",
                                    reboot_required=reboot)
        self.db.add_files(pid, files)
        if changed_at_offset_s is not None:
            ts = datetime.fromtimestamp(self.BOOT + changed_at_offset_s,
                                        timezone.utc).isoformat()
            self.db.conn.execute(
                "INSERT INTO history (timestamp, operation, package_name, success)"
                " VALUES (?, 'upgrade', ?, 1)", (ts, name))
            self.db.conn.commit()

    def _run(self, boot=BOOT, **flags):
        fields = {"restart_list": False, "restart_all": False, "services": []}
        fields.update(flags)
        args = SimpleNamespace(**fields)
        out, err = io.StringIO(), io.StringIO()

        def fake_run(units):
            self.restarted.extend(units)
            return [(u, True, "") for u in units]

        with patch.object(services, "boot_time_epoch", return_value=boot), \
                patch.object(services, "run_restart_services", fake_run), \
                patch.object(cli, "_render_restart_results",
                             lambda results: 0, create=True), \
                redirect_stdout(out), redirect_stderr(err):
            rc = cli.cmd_restart_services(self.db, args)
        return rc, out.getvalue() + err.getvalue()

    # --list ---------------------------------------------------------------

    def test_list_skips_packages_that_predate_this_boot(self):
        self._package("openssh", ["usr/lib/systemd/system/sshd.service"],
                      changed_at_offset_s=None)
        self._package("wsdd", ["usr/lib/systemd/system/wsdd.service"],
                      changed_at_offset_s=100)
        _write_fake_systemctl(self.bin, ["sshd.service", "wsdd.service"])
        rc, text = self._run(restart_list=True)
        self.assertEqual(rc, 0)
        self.assertIn("wsdd", text)
        self.assertNotIn("openssh", text)
        self.assertIn("predate this boot", text)

    def test_list_without_boot_time_classifies_everything_with_a_warning(self):
        self._package("openssh", ["usr/lib/systemd/system/sshd.service"])
        _write_fake_systemctl(self.bin, ["sshd.service"])
        rc, text = self._run(boot=None, restart_list=True)
        self.assertEqual(rc, 0)
        self.assertIn("boot time could not be read", text)
        self.assertIn("openssh", text)

    # --all ----------------------------------------------------------------

    def test_all_refuses_without_a_boot_time(self):
        self._package("openssh", ["usr/lib/systemd/system/sshd.service"],
                      changed_at_offset_s=100)
        _write_fake_systemctl(self.bin, ["sshd.service"])
        rc, text = self._run(boot=None, restart_all=True)
        self.assertEqual(rc, 1)
        self.assertIn("cannot read this boot's start time", text)
        self.assertEqual(self.restarted, [])

    def test_all_restarts_nothing_when_nothing_changed_since_boot(self):
        self._package("openssh", ["usr/lib/systemd/system/sshd.service"])
        _write_fake_systemctl(self.bin, ["sshd.service"])
        rc, text = self._run(restart_all=True)
        self.assertEqual(rc, 0)
        self.assertIn("No package was installed or upgraded since this boot", text)
        self.assertEqual(self.restarted, [])

    def test_all_restarts_only_units_still_running_old_code(self):
        # openssh upgraded 100 s after boot; sshd started 50 s after boot ->
        # running old code -> restart. wsdd upgraded 200 s after boot; its
        # unit started 300 s after boot -> already current -> reported, kept.
        self._package("openssh", ["usr/lib/systemd/system/sshd.service"],
                      changed_at_offset_s=100)
        self._package("wsdd", ["usr/lib/systemd/system/wsdd.service"],
                      changed_at_offset_s=200)
        _write_fake_systemctl(self.bin, ["sshd.service", "wsdd.service"],
                              {"sshd.service": 50_000_000,
                               "wsdd.service": 300_000_000})
        rc, text = self._run(restart_all=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.restarted, ["sshd.service"])
        self.assertIn("Already running the upgraded code", text)
        self.assertIn("wsdd.service", text)
        self.assertIn("(wsdd)", text)

    def test_all_restarts_a_unit_whose_state_systemd_cannot_give(self):
        self._package("openssh", ["usr/lib/systemd/system/sshd.service"],
                      changed_at_offset_s=100)
        _write_fake_systemctl(self.bin, ["sshd.service"])  # no show value
        rc, text = self._run(restart_all=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.restarted, ["sshd.service"])
        self.assertIn("could not say when", text)

    def test_all_never_restarts_a_session_carrying_unit(self):
        self._package("dbus", ["usr/lib/systemd/system/dbus.service"],
                      changed_at_offset_s=100)
        self._package("gdm", ["usr/lib/systemd/system/gdm.service",
                              "usr/lib/systemd/system/gdm-helper.service"],
                      changed_at_offset_s=100)
        _write_fake_systemctl(self.bin, ["dbus.service", "gdm.service",
                                         "gdm-helper.service"],
                              {"gdm-helper.service": 50_000_000})
        rc, text = self._run(restart_all=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.restarted, ["gdm-helper.service"])
        self.assertNotIn("dbus.service", self.restarted)
        self.assertNotIn("gdm.service", self.restarted)
        self.assertIn("dbus.service", text)
        self.assertIn("next boot", text)

    def test_all_reboot_required_only_for_packages_changed_since_boot(self):
        self._package("linux-kernel", ["boot/vmlinuz"], changed_at_offset_s=None,
                      reboot=True)
        self._package("nvidia", ["usr/lib/modules/x/nvidia.ko"],
                      changed_at_offset_s=100, reboot=True)
        _write_fake_systemctl(self.bin, [])
        rc, text = self._run(restart_all=True)
        self.assertEqual(rc, 0)
        self.assertIn("nvidia", text)
        self.assertNotIn("linux-kernel", text)
        self.assertEqual(self.restarted, [])

    # explicit units -------------------------------------------------------

    def test_named_session_unit_is_restarted_with_a_warning(self):
        _write_fake_systemctl(self.bin, ["gdm.service"])
        rc, text = self._run(services=["gdm.service"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.restarted, ["gdm.service"])
        self.assertIn("carry the login session", text)


if __name__ == "__main__":
    unittest.main()
