# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The kernel post-install hook's degrade messages tell the truth about WHY.

WHY THIS EXISTS. Two conditions the linux-kernel post-install hook meets on
EVERY install are expected sequencing there and a real failure on a running
machine, and the hook printed one fixed message for each without saying which
situation it was in. Both messages were measured on the R001.1 install trace
of 2026-08-22 (/var/log/intergen-kernel-postinstall.log on the installed
system) and both misled:

  1. "ukify not on PATH - install ships systemd without UKI builder;
      grub-loads-vmlinuz path remains canonical for this host."
     Both halves are false. ukify is built and shipped - by the desktop-tier
     systemd-pass2 recipe (-D ukify=enabled), not by the core-tier systemd
     recipe (-D ukify=disabled) - and the signed UKI is this system's
     canonical boot path, with the grub-loads-vmlinuz entry as the recovery
     fallback. What actually happened is package ordering: the core-tier
     linux-kernel package extracts before the desktop-tier systemd-pass2, so
     the first fire of this hook in an install has no UKI builder yet. The
     same trace shows the second fire, at 02:28:09, finding ukify and building
     and signing the UKI - so nothing was wrong and the message said the
     system's boot design had changed.

  2. "WARNING: boot-menu update FAILED - ... the next boot can drop to
      emergency mode. Manual fix: point every kernel-release token in
      /boot/efi/EFI/intergenos/grub.cfg at <kver>"
     Printed during the initial install, where the installer composes the menu
     AFTER the package hooks run and there is by definition nothing to
     repoint. The updater it had just called said so in its own message on the
     line above. And the path it offered for the manual fix is lowercase
     `intergenos`, which exists on no installed system: the installer composes
     the menu at /boot/efi/EFI/InterGenOS/grub.cfg. scripts/update-boot-menu.sh
     resolves that correctly already; the hook spelled a second copy that had
     drifted from it.

WHAT THIS MEASURES. The real hook, fired as a script exactly as pkm fires it,
against real filesystem fixtures, with the REAL scripts/update-boot-menu.sh
staged where the linux-kernel package stages it (/usr/lib/intergen/) so the
menu-path resolution under test is the shipped one and not a stand-in.

NO TEST HERE MAY REACH THE MACHINE RUNNING IT, AND THAT IS ENFORCED RATHER
THAN INTENDED. The hook writes to absolute /boot, /boot/efi and /var/log; it
has no root-override knob and must not grow one just to be testable. So every
firing happens inside an unprivileged user+mount namespace (unshare
--map-root-user --mount) with fixture directories bind-mounted over those
three paths. The namespace is not decoration: test_host_is_isolated below
fires the hook with a fixture that makes it write, and then asserts the bytes
landed in the fixture and that the host's own files did not move.

PATH is replaced outright, not prepended to, so "ukify is absent" is true by
construction rather than by hoping the host has none.
"""

import hashlib
import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "packages" / "core" / "linux-kernel" / "hooks" / "post-install.sh"
UPDATER = REPO_ROOT / "scripts" / "update-boot-menu.sh"

KVER = "6.18.10-igos-17"
VALID_MACHINE_ID = "0123456789abcdef0123456789abcdef"

# The commands the hook reaches for through PATH. Anything not listed is
# absent inside the fixture, which is how "ukify not on PATH" is made true.
NEEDED = (
    "bash", "cat", "stat", "date", "mkdir", "grep", "tr", "sed", "basename",
    "tee", "cp", "ls", "rm", "find", "readlink", "dirname", "cut", "sort",
    "head", "awk", "chmod", "mv", "sh",
)


def _which(name):
    return shutil.which(name)


class Fixture:
    """A throwaway target tree the hook can be fired against."""

    def __init__(self, tmp, *, machine_id=None, with_ukify=False,
                 with_updater=False, esp_menu_dir=None, menu_repointable=True):
        self.root = Path(tmp)
        for rel in ("boot/efi/EFI/Linux", "varlog", "libintergen", "bin",
                    "pkgroot/etc"):
            (self.root / rel).mkdir(parents=True, exist_ok=True)

        # A vmlinuz has to exist or the hook exits before anything under test.
        (self.root / "boot" / f"vmlinuz-{KVER}").write_text("not a real kernel\n")

        for name in NEEDED:
            src = _which(name)
            if src:
                (self.root / "bin" / name).symlink_to(src)

        if with_ukify:
            # Stands in for the real builder only so the hook can reach the
            # boot-menu step below. It produces a file and exits 0; nothing
            # here asserts anything about UKI contents.
            ukify = self.root / "bin" / "ukify"
            ukify.write_text(
                "#!/bin/sh\n"
                "for a in \"$@\"; do\n"
                "  case \"$a\" in --output=*) out=${a#--output=} ;; esac\n"
                "done\n"
                "[ -n \"${out:-}\" ] && : > \"$out\"\n"
                "exit 0\n"
            )
            ukify.chmod(0o755)

        if with_updater:
            # The REAL shipped updater, at the path the linux-kernel recipe
            # installs it to (packages/core/linux-kernel/build.sh).
            dst = self.root / "libintergen" / "update-boot-menu.sh"
            shutil.copy2(UPDATER, dst)
            dst.chmod(0o755)

        if esp_menu_dir:
            d = self.root / "boot" / "efi" / "EFI" / esp_menu_dir
            d.mkdir(parents=True, exist_ok=True)
            if menu_repointable:
                body = ('menuentry "InterGenOS 6.18.10-igos-1 (UKI)" {\n'
                        "    chainloader /EFI/Linux/intergenos-6.18.10-igos-1.efi\n"
                        "}\n")
            else:
                # Carries kernel-release tokens, so the updater recognizes the
                # menu and edits it, but no UKI chainloader line, so its own
                # post-edit verification refuses the result and restores the
                # backup. That is a REAL update failure at a REAL resolved
                # path - the case whose warning has to name that path.
                body = ('menuentry "InterGenOS 6.18.10-igos-1" {\n'
                        "    linux /boot/vmlinuz-6.18.10-igos-1 ro\n"
                        "}\n")
            (d / "grub.cfg").write_text(body)

        if machine_id is not None:
            (self.root / "pkgroot" / "etc" / "machine-id").write_text(machine_id)

    @property
    def log(self):
        return self.root / "varlog" / "intergen-kernel-postinstall.log"


def hook_lines(out):
    """Only the hook's OWN messages.

    Everything the hook shells out to also writes here - the updater's
    diagnostics among them - and an assertion against the whole blob would
    pass on the strength of a message some other program printed. That is how
    a check goes green on the very defect it exists to catch, so the stream is
    split by its author before anything is asserted about it.
    """
    return "\n".join(l for l in out.splitlines()
                      if "[linux-kernel:post-install]" in l)


def _run(fixture):
    """Run the real hook inside a user+mount namespace over the fixture.

    The namespace is what keeps this off the machine running the suite; see
    the module docstring.
    """
    inner = (
        'mount --bind "$IGOSC_FIX/boot" /boot && '
        'mount --bind "$IGOSC_FIX/varlog" /var/log && '
        'mount --bind "$IGOSC_FIX/libintergen" /usr/lib/intergen && '
        'PATH="$IGOSC_FIX/bin" exec "$IGOSC_FIX/bin/bash" "$IGOSC_HOOK"'
    )
    env = dict(os.environ)
    env.update({
        "IGOSC_FIX": str(fixture.root),
        "IGOSC_HOOK": str(HOOK),
        "PKM_PACKAGE_NAME": "linux-kernel",
        "PKM_PACKAGE_VERSION": "6.18.10",
        "PKM_PACKAGE_ROOT": str(fixture.root / "pkgroot"),
    })
    proc = subprocess.run(
        ["unshare", "--map-root-user", "--mount", "/bin/bash", "-c", inner],
        capture_output=True, text=True, env=env, timeout=180,
    )
    return proc


def fire(fixture):
    """The hook's combined output."""
    proc = _run(fixture)
    return proc.stdout + proc.stderr


def fire_rc(fixture):
    """Same firing, returning the hook's exit status."""
    return _run(fixture).returncode


class KernelHookMessages(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    # ---------- the harness itself ----------

    def test_unshare_is_available(self):
        """A harness that cannot build its namespace must fail, not skip."""
        self.assertIsNotNone(_which("unshare"),
                             "unshare(1) absent: this suite cannot isolate the host")
        probe = subprocess.run(
            ["unshare", "--map-root-user", "--mount", "/bin/true"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(probe.returncode, 0,
                         f"unprivileged mount namespaces unavailable: {probe.stderr}")

    def test_host_is_isolated(self):
        """The hook writes; prove the writes land in the fixture, not the host."""
        host_log = Path("/var/log/intergen-kernel-postinstall.log")
        before = (host_log.stat().st_mtime, host_log.stat().st_size) if host_log.exists() else None
        host_boot_before = hashlib.sha256(
            "\n".join(sorted(os.listdir("/boot"))).encode()).hexdigest()

        f = Fixture(self.tmp)
        out = fire(f)

        self.assertTrue(f.log.exists(),
                        f"the hook wrote no log inside the fixture; it may not have run: {out}")
        self.assertIn("linux-kernel:post-install", f.log.read_text())

        after = (host_log.stat().st_mtime, host_log.stat().st_size) if host_log.exists() else None
        self.assertEqual(before, after,
                         "the hook reached the HOST's kernel post-install log")
        self.assertEqual(
            host_boot_before,
            hashlib.sha256("\n".join(sorted(os.listdir("/boot"))).encode()).hexdigest(),
            "the hook changed the HOST's /boot listing")

    # ---------- I-03: the ukify-absent message ----------

    def test_ukify_absent_on_first_install_names_the_ordering(self):
        f = Fixture(self.tmp)          # no machine-id -> not yet booted
        msgs = hook_lines(fire(f))
        self.assertIn("ukify", msgs)
        self.assertIn("first-install ordering", msgs,
                      "the message does not say the ordering is why ukify is absent")
        self.assertIn("systemd-pass2", msgs,
                      "the message does not name the package that ships ukify")

    def test_ukify_absent_message_makes_no_false_claim(self):
        msgs = hook_lines(fire(Fixture(self.tmp)))
        self.assertNotIn("install ships systemd without UKI builder", msgs,
                         "still claims the install ships no UKI builder; "
                         "systemd-pass2 builds ukify with -D ukify=enabled")
        self.assertNotIn("grub-loads-vmlinuz path remains canonical", msgs,
                         "still calls grub-loads-vmlinuz canonical; the signed "
                         "UKI is the canonical path and that entry is the fallback")

    def test_ukify_absent_on_a_running_system_is_a_warning(self):
        f = Fixture(self.tmp, machine_id=VALID_MACHINE_ID)
        msgs = hook_lines(fire(f))
        self.assertIn("WARNING", msgs,
                      "a missing UKI builder on a system that has already booted "
                      "is a real failure and must be reported as one")
        self.assertIn(KVER, msgs)
        self.assertNotIn("first-install ordering", msgs,
                         "a booted system is not a first install and must not be "
                         "told its ordering explains anything")

    def test_ukify_absent_still_exits_zero(self):
        """Degrading must never fail the kernel install, on either path."""
        for machine_id in (None, VALID_MACHINE_ID):
            with self.subTest(machine_id=machine_id):
                f = Fixture(os.path.join(self.tmp, f"mid-{machine_id}"),
                            machine_id=machine_id)
                self.assertEqual(fire_rc(f), 0)

    # ---------- I-04: the boot-menu message ----------

    def test_menu_message_never_prints_the_lowercase_path(self):
        for machine_id in (None, VALID_MACHINE_ID):
            with self.subTest(machine_id=machine_id):
                f = Fixture(os.path.join(self.tmp, f"lc-{machine_id}"),
                            machine_id=machine_id, with_ukify=True,
                            with_updater=True)
                self.assertNotIn("/EFI/intergenos/grub.cfg", hook_lines(fire(f)),
                                 "still offers /boot/efi/EFI/intergenos/grub.cfg, "
                                 "which exists on no installed system")

    def test_menu_message_names_the_path_the_updater_resolved(self):
        """Resolution comes from the updater, so a discovered menu is named."""
        f = Fixture(self.tmp, machine_id=VALID_MACHINE_ID, with_ukify=True,
                    with_updater=True, esp_menu_dir="IGOSMENU",
                    menu_repointable=False)
        msgs = hook_lines(fire(f))
        self.assertIn("/boot/efi/EFI/IGOSMENU/grub.cfg", msgs,
                      "the hook's own message does not name the menu the shipped "
                      "updater resolved; the path is still spelled in the hook")

    def test_menu_absent_on_first_install_is_not_an_emergency_warning(self):
        msgs = hook_lines(fire(Fixture(self.tmp, with_ukify=True, with_updater=True)))
        self.assertNotIn("can drop to emergency mode", msgs,
                         "the initial install's expected ordering is still "
                         "reported as an impending emergency-mode boot")
        self.assertIn("has not booted", msgs,
                      "the message does not say why this is the expected order")

    def test_menu_failure_on_a_running_system_stays_loud(self):
        f = Fixture(self.tmp, machine_id=VALID_MACHINE_ID, with_ukify=True,
                    with_updater=True)
        msgs = hook_lines(fire(f))
        self.assertIn("WARNING", msgs)
        self.assertIn("emergency mode", msgs,
                      "on a system that has booted, a failed menu update is "
                      "still the one degrade that breaks the NEXT boot")


if __name__ == "__main__":
    unittest.main()
