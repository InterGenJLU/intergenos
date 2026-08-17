# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""F-038 netlink-redirect gate: iproute2 tools are unavailable in the daemon.

The hardened unit denies AF_NETLINK (RestrictAddressFamilies, F-038), so any
iproute2-netlink tool (ip/ss/bridge/tc/...) SIGSYS-coredumps the run_command
child the instant it opens its netlink socket. classify_command routes the
whole family to BLOCKED and get_blocked_response redirects to a netlink-free
source (/sys/class/net) or the user's own terminal — a clear refusal instead of
a silent crash, mirroring the L2 wrong-package-manager redirect.

net-tools `ifconfig`/`netstat` are NOT netlink (they use /proc + AF_INET ioctl),
so they must stay runnable (AUTO) — these tests pin that boundary too.
"""

from __future__ import annotations

import unittest

from intergen.safety import classify_command, get_blocked_response
from intergen.interfaces.types import SafetyTier


class TestNetlinkFamilyBlocked(unittest.TestCase):
    def test_ip_subcommands_blocked(self):
        # base `ip` catches every subcommand form — none should auto-run.
        for c in ("ip addr", "ip route", "ip link", "ip -brief addr show",
                  "ip route show", "ip neigh"):
            self.assertEqual(classify_command(c), SafetyTier.BLOCKED, c)

    def test_ss_blocked_even_readonly(self):
        # `ss` looks read-only but opens NETLINK_SOCK_DIAG → SIGSYS in the daemon.
        self.assertEqual(classify_command("ss"), SafetyTier.BLOCKED)
        self.assertEqual(classify_command("ss -tlnp"), SafetyTier.BLOCKED)

    def test_other_iproute2_tools_blocked(self):
        for c in ("bridge link", "tc qdisc show", "nstat", "genl ctrl list",
                  "devlink dev show", "rtmon"):
            self.assertEqual(classify_command(c), SafetyTier.BLOCKED, c)

    def test_compound_with_netlink_segment_blocked(self):
        # A netlink segment cannot be laundered by a benign pipe partner.
        self.assertEqual(classify_command("ip addr | grep eth0"),
                         SafetyTier.BLOCKED)
        self.assertEqual(classify_command("ss -tlnp | grep :22"),
                         SafetyTier.BLOCKED)


class TestNonNetlinkNetToolsStillRun(unittest.TestCase):
    """net-tools query /proc + AF_INET ioctl (no netlink) — must NOT be blocked."""

    def test_ifconfig_and_netstat_stay_auto(self):
        self.assertEqual(classify_command("ifconfig"), SafetyTier.AUTO)
        self.assertEqual(classify_command("netstat -tlnp"), SafetyTier.AUTO)

    def test_other_network_info_unaffected(self):
        for c in ("ping -c1 8.8.8.8", "dig example.com", "host example.com",
                  "traceroute example.com"):
            self.assertEqual(classify_command(c), SafetyTier.AUTO, c)

    def test_benign_unaffected(self):
        self.assertEqual(classify_command("ls -la"), SafetyTier.AUTO)


class TestNetlinkRedirectMessage(unittest.TestCase):
    def test_ip_redirect_points_to_sysfs(self):
        msg = get_blocked_response("ip addr")
        self.assertIn("/sys/class/net", msg)
        # Names the tool and signals it's a sandbox-availability issue, not danger.
        self.assertIn("ip", msg)
        self.assertNotIn("dangerous", msg.lower())

    def test_ss_redirect(self):
        msg = get_blocked_response("ss -tlnp")
        self.assertIn("/sys/class/net", msg)
        self.assertIn("ss", msg)

    def test_bridge_redirect(self):
        self.assertIn("/sys/class/net", get_blocked_response("bridge link"))


if __name__ == "__main__":
    unittest.main()
