# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""GATE — on the INSTALLED system the SSH posture is what the machine says it is.

WHAT THIS CATCHES THAT THE SOURCE-TREE TESTS CANNOT. tests/installer proves the
installer WRITES the fragment and leaves the shipped ruleset alone; tests/welcome
proves the helper's text. Neither can prove what a real install left on disk:
that the shipped /etc/nftables.conf carries no tcp/22 rule (the R001.2 installer
put one there), that the fragment exists exactly when sshd is enabled, and that
a serial login prompt is not enabled on a machine that never asked for one.

The three facts are read from the installed files only, unprivileged. The live
ruleset (`nft list ruleset`) needs root and is NOT read here; that arm is the
root tier's, and this gate says so rather than pretending.

CONTROLS. The base-file check is run against a copy with the legacy rule
injected and must fail; a reader that cannot fail is not a reader.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

BASE = Path("/etc/nftables.conf")
FRAGMENT = Path("/etc/nftables.d/40-intergen-ssh.conf")
SERIAL_LINK = Path("/etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service")
LEGACY_BLOCK = ("        # SSH server (opt-in via Forge install per D-019)\n"
                "        tcp dport 22 accept\n\n")


def _base_file_carries_no_ssh_rule(text: str) -> bool:
    return not re.search(r"^\s*tcp\s+dport\s+22\s+accept", text, re.M)


def _unit_enabled(unit: str) -> bool:
    r = subprocess.run(["systemctl", "is-enabled", unit], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() in ("enabled", "enabled-runtime")


@pytest.mark.usefixtures("require_installed_intergenos")
class TestSshPostureTruth:
    def test_control_reader_fails_on_the_legacy_rule(self):
        injected = BASE.read_text().replace(
            "        # Everything else inbound DROPS", LEGACY_BLOCK + "        # Everything else inbound DROPS", 1)
        assert not _base_file_carries_no_ssh_rule(injected), "the control must fail"

    def test_shipped_ruleset_carries_no_ssh_rule(self):
        text = BASE.read_text()
        assert _base_file_carries_no_ssh_rule(text), (
            "/etc/nftables.conf carries a tcp/22 accept rule: the port is opened in the "
            "shipped file, where the Welcomer's SSH toggle cannot close it (R001.3 row 31)")

    def test_fragment_present_exactly_when_sshd_enabled(self):
        enabled = _unit_enabled("sshd.service")
        assert FRAGMENT.exists() == enabled, (
            f"sshd enabled={enabled} but the firewall fragment {FRAGMENT} "
            f"{'exists' if FRAGMENT.exists() else 'is absent'}: the service and the port disagree")

    def test_fragment_text_is_the_one_shape(self):
        if not FRAGMENT.exists():
            pytest.skip("no SSH fragment on this machine; nothing to read")
        text = FRAGMENT.read_text()
        assert "tcp dport 22 accept" in text and text.startswith("#!/usr/sbin/nft -f\n")

    def test_serial_login_only_when_this_boot_is_serial(self):
        if not SERIAL_LINK.exists():
            return
        cmdline = Path("/proc/cmdline").read_text()
        assert re.search(r"(^|\s)console=ttyS\d", cmdline), (
            "a serial login prompt is enabled but this machine does not boot with a "
            "serial console: nobody asked for it (R001.3 row 13)")
