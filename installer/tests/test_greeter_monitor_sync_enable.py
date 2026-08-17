# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""RED/GREEN tests for users.enable_greeter_monitor_sync (decided 2026-07-21).

The function enables the gdm-shipped templated path unit
igos-greeter-monitors-sync@<username>.path on the install target via
host-side `systemctl --root` (no daemon in the chroot), and FAILS LOUD on a
non-zero rc — on InterGenOS media the unit is always shipped by the gdm
package, so a failed enable means a corrupted or mismatched payload and the
install must not proceed silently degraded.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from installer.backend import users


def _result(rc, stdout="", stderr=""):
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


class TestEnableGreeterMonitorSync(unittest.TestCase):
    def test_enables_templated_instance_for_username(self):
        with patch.object(users.trace, "traced_run",
                          return_value=_result(0)) as run:
            users.enable_greeter_monitor_sync("/mnt/target", "christopher")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:3], ["systemctl", "--root", "/mnt/target"])
        self.assertIn("enable", cmd)
        self.assertIn("igos-greeter-monitors-sync@christopher.path", cmd)

    def test_nonzero_rc_fails_loud(self):
        with patch.object(users.trace, "traced_run",
                          return_value=_result(1, stderr="No such file")):
            with self.assertRaises(Exception) as ctx:
                users.enable_greeter_monitor_sync("/mnt/target", "user")
        self.assertIn("igos-greeter-monitors-sync@user.path", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
