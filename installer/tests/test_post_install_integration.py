"""Post-install integration tests for Classes 2 / 2b / 5.

Complement to the unit-mocked test modules: these classes exercise the
probe modules (`class2_runtime_sb_state`, `class2b_boot_order`,
`class5_module_sigs`) against REAL firmware / efibootmgr / `/proc`
state, rather than mocks.

Runtime-state probes can only answer their question when executed on
the live target system. A mounted-but-not-booted disk image has no
`/sys/firmware/efi/efivars`, no running kernel's `/proc`, no UEFI boot
state — so unlike Class 1 post-install (which can walk a mounted tree
via CLASS1_POST_INSTALL_TARGET), these three classes gate strictly on
`POST_INSTALL_TARGET == /` plus the MOK cert existing at its
conventional path (the authoritative "this was Forge-installed"
signal).

Additional per-class gates:
  - TestClass2PostReboot:   `/sys/firmware/efi/efivars` mounted
  - TestClass2bPostReboot:  `efibootmgr` in PATH
  - TestClass5PostInstall:  `/proc/sys/kernel/module_sig_enforce` present

On any dev / build host, all three skip cleanly with a human-readable
reason. On a booted Forge-installed InterGenOS target with the right
tools, they activate and assert `all_required_pass()` on their report.

Point at an alternate target with:
    POST_INSTALL_TARGET=/  python3 -m unittest \\
        installer.tests.test_post_install_integration -v

(Target other than `/` is intentionally rejected — see docstrings.)
"""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path

from installer.tests import class2_runtime_sb_state as c2
from installer.tests import class2b_boot_order as c2b
from installer.tests import class5_module_sigs as c5


# Shared config. POST_INSTALL_TARGET is also read by test_class1_integration
# for its own post-install class; we reuse the name so the operator sets
# one env var for all integration tiers.
POST_INSTALL_TARGET = Path(os.environ.get(
    "POST_INSTALL_TARGET",
    os.environ.get("CLASS1_POST_INSTALL_TARGET", "/"),
))
MOK_CERT_RELPATH = "var/lib/intergen/mok/mok.crt"
MOK_CERT = POST_INSTALL_TARGET / MOK_CERT_RELPATH


# --- Shared skip-gate plumbing ---------------------------------------------


def _common_prereqs() -> tuple[bool, str]:
    """Gate: are we running ON a booted Forge-installed InterGenOS?

    Runtime-state probes are only meaningful against a live target —
    POST_INSTALL_TARGET != '/' can't answer "is this system in secure
    posture" because the system isn't running. The MOK cert check is
    the authoritative "Forge-installed" signal (cert written to
    /var/lib/intergen/mok/mok.crt by bootloader.py at install time).
    """
    if POST_INSTALL_TARGET != Path("/"):
        return False, (
            f"POST_INSTALL_TARGET={POST_INSTALL_TARGET} but runtime "
            "probes require target=/"
        )
    if not MOK_CERT.exists():
        return False, (
            f"MOK cert {MOK_CERT} not found (not a Forge-installed target)"
        )
    return True, ""


def _class2_prereqs() -> tuple[bool, str]:
    ok, reason = _common_prereqs()
    if not ok:
        return False, reason
    if not Path("/sys/firmware/efi/efivars").exists():
        return False, "/sys/firmware/efi/efivars not mounted (non-UEFI host?)"
    return True, ""


def _class2b_prereqs() -> tuple[bool, str]:
    ok, reason = _common_prereqs()
    if not ok:
        return False, reason
    if not shutil.which("efibootmgr"):
        return False, "efibootmgr not in PATH"
    return True, ""


def _class5_prereqs() -> tuple[bool, str]:
    ok, reason = _common_prereqs()
    if not ok:
        return False, reason
    if not Path("/proc/sys/kernel/module_sig_enforce").exists():
        return False, (
            "/proc/sys/kernel/module_sig_enforce not present "
            "(CONFIG_MODULE_SIG_FORCE=n)"
        )
    return True, ""


_c2_ok, _c2_reason = _class2_prereqs()
_c2b_ok, _c2b_reason = _class2b_prereqs()
_c5_ok, _c5_reason = _class5_prereqs()


# --- Class 2 post-reboot ---------------------------------------------------


@unittest.skipUnless(_c2_ok, f"Class 2 post-reboot: {_c2_reason}")
class TestClass2PostReboot(unittest.TestCase):
    """Exercises `class2_runtime_sb_state.run()` against live efivars.

    Failures here mean the installed target is NOT in the SB posture we
    claimed. Possible causes: SB was disabled in firmware between
    install and test, PK was cleared (SetupMode=1), or the installer
    somehow landed on a non-UEFI host and nobody noticed.
    """

    def test_runtime_sb_state_all_required_pass(self):
        report = c2.run()
        self.assertTrue(
            report.all_required_pass(),
            f"runtime SB state failed: {report.to_dict()}",
        )
        # Sanity: three probes expected (secureboot, setupmode, mokutil).
        probes = {r.probe for r in report.results}
        self.assertEqual(probes, {"secureboot", "setupmode", "mokutil"})

    def test_setupmode_is_user_mode(self):
        """Specific assertion on the load-bearing probe.

        SetupMode=0 means firmware is locked down. Worth a dedicated
        assertion because SetupMode=1 can silently allow tampering even
        when SecureBoot=1 — the composite all_required_pass() check
        catches it, but a named assertion makes the failure diagnosis
        one line shorter.
        """
        report = c2.run()
        sm = next(r for r in report.results if r.probe == "setupmode")
        self.assertTrue(
            sm.passed,
            f"SetupMode probe failed: observed={sm.observed} detail={sm.detail}",
        )


# --- Class 2b post-reboot --------------------------------------------------


@unittest.skipUnless(_c2b_ok, f"Class 2b post-reboot: {_c2b_reason}")
class TestClass2bPostReboot(unittest.TestCase):
    """Exercises `class2b_boot_order.run()` against live efibootmgr.

    Failure modes caught here: installer's efibootmgr --create soft-
    failed (entry missing entirely), entry exists but firmware isn't
    iterating it (not in BootOrder), or an OEM bootloader is rewriting
    BootOrder at POST to push itself ahead (InterGenOS gets buried).
    """

    def test_boot_order_all_required_pass(self):
        report = c2b.run(label="InterGenOS")
        self.assertTrue(
            report.all_required_pass(),
            f"boot-order verification failed: {report.to_dict()}",
        )

    def test_intergenos_entry_is_in_boot_order(self):
        """Name the probe that catches 'entry exists but firmware skips it'."""
        report = c2b.run(label="InterGenOS")
        in_order = next(
            r for r in report.results
            if r.probe == "entry-in-boot-order"
        )
        self.assertTrue(
            in_order.passed,
            f"InterGenOS entry is missing from BootOrder: "
            f"detail={in_order.detail}",
        )


# --- Class 5 post-install --------------------------------------------------


@unittest.skipUnless(_c5_ok, f"Class 5 post-install: {_c5_reason}")
class TestClass5PostInstall(unittest.TestCase):
    """Exercises `class5_module_sigs.run()` against live /proc.

    Tighter assertion than the unit tier because we're now checking
    that the running kernel actually enforces — if this test fails on
    a Forge-installed target, unsigned modules could be loaded and the
    security-only-alignment is a lie.
    """

    def test_module_sigs_all_required_pass(self):
        report = c5.run()
        self.assertTrue(
            report.all_required_pass(),
            f"module signature verification failed: {report.to_dict()}",
        )

    def test_module_sig_enforce_is_on(self):
        """Name the probe that catches kernel built without SIG_FORCE."""
        report = c5.run()
        enforce = next(
            r for r in report.results if r.probe == "module_sig_enforce"
        )
        self.assertTrue(
            enforce.passed,
            f"module_sig_enforce probe failed: observed={enforce.observed} "
            f"detail={enforce.detail}",
        )


if __name__ == "__main__":
    unittest.main()
