# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""GATE — on the INSTALLED system the kernel hardening floor is what the kernel says it is.

WHAT THIS CATCHES THAT THE SOURCE-TREE TESTS CANNOT. tests/preflight proves the
shipped drop-in PARSES to the designed values; tests/igos_build proves the kernel
gate refuses a config with COMPAT_BRK on. Neither can prove what a real install
runs with: that systemd-sysctl applied the drop-in AFTER its own 50-default.conf
(which sets the reverse path filter loose on every interface through a glob), that
no later file in /etc/sysctl.d undid a value, and that the kernel actually booted
was built with the heap-randomisation guard off. Measured on every R001.2-03
machine before this change: kptr_restrict 0, randomize_va_space 1, rp_filter 2
everywhere, redirects accepted and sent, martians unlogged, CONFIG_COMPAT_BRK=y.

Every value is read from /proc/sys — the kernel's own answer — never from the
drop-in. The drop-in's presence is asserted separately, so "value right for the
wrong reason" and "file shipped but never applied" are both distinguishable.

THE KERNEL ARM IS RED ON EVERY R001.2-03 MACHINE UNTIL THE R001.3 KERNEL. That is
this gate working: the running kernel's own /boot/config-$(uname -r) (or
/proc/config.gz) must state `# CONFIG_COMPAT_BRK is not set`, and the shipped
6.18.10 kernels state `CONFIG_COMPAT_BRK=y`. The sysctl arm goes green as soon as
base-files r22 is installed; the kernel arm goes green only with a rebuilt kernel.
The two are separate tests so the record says which half a machine has.

CONTROLS. The value reader is fed injected wrong values and must fail; a reader
that cannot fail is not a reader.
"""

import gzip
import os
import re
from pathlib import Path

import pytest

DROP_IN = Path("/usr/lib/sysctl.d/60-intergenos-hardening.conf")
PROC_SYS = Path("/proc/sys")

# key -> required value, read back from /proc/sys. The per-interface keys are
# checked on `all`, `default` AND every interface present, because the kernel
# applies the stronger of `all` and the interface's own value for rp_filter and
# the drop-in states all three (a glob for the interfaces) for that reason.
REQUIRED_GLOBAL = {
    "kernel/kptr_restrict": "1",
    "kernel/randomize_va_space": "2",
}
REQUIRED_PER_IPV4_INTERFACE = {
    "rp_filter": "1",
    "accept_redirects": "0",
    "secure_redirects": "0",
    "send_redirects": "0",
    "log_martians": "1",
}
REQUIRED_PER_IPV6_INTERFACE = {
    "accept_redirects": "0",
}
COMPAT_BRK_OFF = "# CONFIG_COMPAT_BRK is not set"


def _read(key: str) -> str:
    return (PROC_SYS / key).read_text().strip()


def _ipv4_interfaces():
    return sorted(p.name for p in (PROC_SYS / "net/ipv4/conf").iterdir())


def _ipv6_interfaces():
    base = PROC_SYS / "net/ipv6/conf"
    return sorted(p.name for p in base.iterdir()) if base.is_dir() else []


def _required_for(key: str, required: dict) -> str:
    """A global requirement is keyed by its full /proc/sys path; a per-interface
    requirement by the key's last segment (rp_filter, accept_redirects, ...)."""
    if key in required:
        return required[key]
    return required[key.rsplit("/", 1)[-1]]


def _mismatches(readings: dict, required: dict) -> list:
    """readings: key -> value as read. Returns the keys whose reading differs from
    the requirement. Pure, so the controls can feed it wrong values."""
    return sorted(k for k, v in readings.items() if v != _required_for(k, required))


def _running_kernel_config_text() -> str:
    release = Path("/proc/sys/kernel/osrelease").read_text().strip()
    boot = Path("/boot") / f"config-{release}"
    if boot.is_file():
        return boot.read_text(errors="replace")
    gz = Path("/proc/config.gz")
    if gz.is_file():
        return gzip.open(gz, "rt", errors="replace").read()
    pytest.fail(
        f"neither {boot} nor /proc/config.gz is readable: the running kernel's "
        "configuration cannot be read, so the kernel arm of this gate cannot be "
        "verified. That is a failure, not a pass."
    )


@pytest.mark.usefixtures("require_installed_intergenos")
class TestKernelHardeningFloor:
    # ── controls: the readers must be able to fail ──────────────────────────
    def test_control_global_reader_fails_on_injected_values(self):
        injected = {"kernel/kptr_restrict": "0", "kernel/randomize_va_space": "1"}
        assert _mismatches(injected, REQUIRED_GLOBAL) == sorted(injected), "the control must fail"

    def test_control_interface_reader_fails_on_the_loose_value(self):
        injected = {"net/ipv4/conf/all/rp_filter": "2", "net/ipv4/conf/all/send_redirects": "1"}
        assert _mismatches(injected, REQUIRED_PER_IPV4_INTERFACE) == sorted(injected), "the control must fail"

    def test_control_kernel_config_reader_fails_on_compat_brk_on(self):
        assert COMPAT_BRK_OFF not in "CONFIG_COMPAT_BRK=y\nCONFIG_RANDOMIZE_BASE=y\n"

    # ── the sysctl arm: green once base-files r22 is installed ───────────────
    def test_the_drop_in_is_installed(self):
        assert DROP_IN.is_file(), (
            f"{DROP_IN} is absent: base-files does not ship the hardening floor on this "
            "machine (R001.3 row 23); the values below may be right by accident or wrong"
        )

    def test_global_values_are_at_the_floor(self):
        readings = {k: _read(k) for k in REQUIRED_GLOBAL}
        bad = _mismatches(readings, REQUIRED_GLOBAL)
        assert not bad, "\n".join(
            f"  /proc/sys/{k} = {readings[k]} (required {REQUIRED_GLOBAL[k]})" for k in bad
        ) + (
            "\nkernel.randomize_va_space is clamped to 1 by a kernel built with "
            "CONFIG_COMPAT_BRK=y whatever the sysctl says — see the kernel arm below."
            if "kernel/randomize_va_space" in bad else ""
        )

    def test_every_ipv4_interface_is_at_the_floor(self):
        readings = {}
        for iface in _ipv4_interfaces():
            for suffix in REQUIRED_PER_IPV4_INTERFACE:
                key = f"net/ipv4/conf/{iface}/{suffix}"
                readings[key] = _read(key)
        bad = _mismatches(readings, REQUIRED_PER_IPV4_INTERFACE)
        detail = "\n".join(
            f"  /proc/sys/{k} = {readings[k]} (required {REQUIRED_PER_IPV4_INTERFACE[k.rsplit('/', 1)[-1]]})"
            for k in bad
        )
        assert not bad, (
            "the reverse-path filter / redirect / martian floor is not applied on:\n" + detail +
            "\n(systemd's 50-default.conf sets rp_filter loose through a glob; the shipped "
            "60-intergenos-hardening.conf must sort after it and state the same glob strict)"
        )

    def test_every_ipv6_interface_refuses_redirects(self):
        ifaces = _ipv6_interfaces()
        if not ifaces:
            pytest.fail("no IPv6 interface configuration under /proc/sys — the IPv6 arm cannot be read")
        readings = {f"net/ipv6/conf/{i}/accept_redirects": _read(f"net/ipv6/conf/{i}/accept_redirects")
                    for i in ifaces}
        bad = _mismatches(readings, REQUIRED_PER_IPV6_INTERFACE)
        assert not bad, "\n".join(f"  /proc/sys/{k} = {readings[k]} (required 0)" for k in bad)

    # ── the kernel arm: RED on every R001.2-03 kernel until the R001.3 kernel ──
    def test_the_running_kernel_was_built_with_compat_brk_off(self):
        text = _running_kernel_config_text()
        stated = re.search(r"^(CONFIG_COMPAT_BRK=.*|# CONFIG_COMPAT_BRK is not set)$", text, re.M)
        assert stated, "the running kernel's config does not mention CONFIG_COMPAT_BRK at all"
        assert stated.group(0) == COMPAT_BRK_OFF, (
            f"the running kernel {Path('/proc/sys/kernel/osrelease').read_text().strip()} was built "
            f"with {stated.group(0)}: the heap start of every process is at a predictable "
            "offset and kernel.randomize_va_space is clamped to 1. This is the R001.2 kernel; "
            "the arm goes green with the first kernel built from the row-23 fragment (R001.3)."
        )
