# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""fcrontab works for a user on the installed system, or it ships broken.

WHY THIS EXISTS. On the shipped R001.1 system, `fcrontab -l` run by the
installed user fails. Measured 2026-08-24 on this image
(the run is captured whole in this change set's evidence bundle).
Two independent defects, either of which alone is fatal to the command:

  1. AUTHORIZATION FILES UNREADABLE.  /etc/fcron.allow and /etc/fcron.deny
     ship root:root 0640. fcrontab is setuid root AND setgid fcron, and it
     drops the root it was given before reading them, keeping the fcron group
     the setgid bit grants. With group root, nothing it retains can open them:

         ERROR could not open /etc/fcron.allow: Permission denied

     The `fcron` group exists at gid 22 with no members, which is exactly the
     design - membership is not how access is granted here, the setgid bit is -
     so the files have to be group-owned by fcron for that grant to reach them.
     Fixed by staging and restoring them root:fcron 0640, the same ownership
     repair this recipe already performs for /etc/fcron.conf and the spool.

  2. NO PAM SERVICE FILE, SO PAM DENIES BY DEFAULT.  fcrontab calls PAM with
     the service name "fcrontab" and the daemon with "fcron". Neither
     /etc/pam.d/fcrontab nor /etc/pam.d/fcron exists on an installed system, so
     Linux-PAM falls through to /etc/pam.d/other, which is pam_warn plus
     pam_deny, and refuses every caller:

         pam_warn(fcrontab:auth): ... user=[<the invoking user>]
         ERROR Could not authenticate user using PAM (7): Authentication failure

     The package DOES carry upstream's PAM configuration - as /etc/pam.conf,
     a monolithic file Linux-PAM reads only when /etc/pam.d does not exist.
     This system has /etc/pam.d, so those bytes have never had any effect, and
     upstream's own install document warns about exactly this outcome. A
     package also has no business owning a system-wide /etc/pam.conf.

     Upstream's default in that unused file is `auth required pam_permit.so`
     for both services, with the comment that fcron has no way to prompt a
     user - and that is the right model: WHO MAY use fcron is decided by
     fcron.allow and fcron.deny, not by a password challenge. The replacement
     keeps it, so nothing here can ever block waiting for a person, and puts
     the account stack on pam_unix so a locked or expired account is still
     refused.

WHAT THIS MEASURES. The recipe's real text for what it stages, and the recipe's
real post_install - extracted by the real sealer (igos-build/hookseal.py),
exactly as the build seals it into the archive - fired as a script with a
recording stand-in for every command that would otherwise reach this machine.
Nothing below reimplements a hook.

NO TEST HERE MAY AUTHENTICATE, PROMPT, OR REACH THE HOST. The hook chowns and
chmods absolute paths under /usr and /etc and runs systemd-sysusers; all of
those are stand-ins here that record their arguments and refuse to act. The
PAM files are checked by reading them - a test that actually exercised a PAM
stack could reach a password prompt, and a test that can prompt is a defect.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FCRON_DIR = REPO_ROOT / "packages" / "base" / "fcron"
BUILD_SH = FCRON_DIR / "build.sh"

ALLOW = "/etc/fcron.allow"
DENY = "/etc/fcron.deny"
PAM_SERVICES = ("fcron", "fcrontab")

# PAM modules that can put a question in front of a person. None of them may
# appear in an auth stack this package ships.
PROMPTING_MODULES = ("pam_unix.so", "pam_sss.so", "pam_ldap.so",
                     "pam_krb5.so", "pam_fprintd.so", "pam_systemd_home.so")

# Commands the sealed hook calls that must never reach the machine.
INTERCEPTED = ("chown", "chmod", "systemd-sysusers", "install", "systemctl")


def _hookseal():
    path = REPO_ROOT / "igos-build" / "hookseal.py"
    spec = importlib.util.spec_from_file_location("_hookseal_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _do_install_body(text):
    """The do_install function body, by brace depth."""
    start = text.index("\ndo_install() {")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError("do_install() has no closing brace")


class FcronUsable(unittest.TestCase):

    def setUp(self):
        self.text = BUILD_SH.read_text()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    # ---------- defect 1: the authorization files ----------

    def _post_install_calls(self):
        """Fire the REAL sealed post_install; return the recorded argv lines."""
        hookseal = _hookseal()
        body = hookseal.extract_function(self.text, "post_install")
        self.assertIsNotNone(body, "the fcron recipe declares no post_install")
        script = self.tmp / "post_install.sh"
        script.write_text(hookseal.render_script("post_install", body,
                                                 "fcron", "3.4.0"))
        script.chmod(0o755)

        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        log = self.tmp / "calls.log"
        for name in INTERCEPTED:
            shim = bindir / name
            shim.write_text(
                "#!/bin/sh\n"
                f'printf "%s %s\\n" "{name}" "$*" >> "{log}"\n'
                "exit 0\n")
            shim.chmod(0o755)
        # Anything else the hook needs comes from the real PATH; only the
        # commands that would change this machine are replaced.
        env = dict(os.environ)
        env["PATH"] = f"{bindir}:{env['PATH']}"
        proc = subprocess.run(["/bin/bash", str(script)], capture_output=True,
                              text=True, env=env, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0,
                         f"the sealed post_install failed: {proc.stdout}{proc.stderr}")
        return log.read_text().splitlines() if log.exists() else []

    def test_post_install_gives_the_authorization_files_to_the_fcron_group(self):
        calls = self._post_install_calls()
        chowns = [c for c in calls if c.startswith("chown ")]
        for path in (ALLOW, DENY):
            with self.subTest(path=path):
                self.assertTrue(
                    any(path in c and "root:fcron" in c for c in chowns),
                    f"post_install never chowns {path} to root:fcron, so on an "
                    "installed system fcrontab cannot read it and refuses every "
                    f"user. Recorded chowns: {chowns}")

    def test_post_install_leaves_the_authorization_files_group_readable(self):
        calls = self._post_install_calls()
        chmods = [c for c in calls if c.startswith("chmod ")]
        for path in (ALLOW, DENY):
            with self.subTest(path=path):
                relevant = [c for c in chmods if path in c]
                for c in relevant:
                    mode = c.split()[1]
                    self.assertRegex(
                        mode, r"^0?6[4-7]0$",
                        f"{path} would be left mode {mode}; it must stay "
                        "group-readable and never world-readable")

    def test_do_install_asserts_the_authorization_files_staged(self):
        """Upstream's install target puts them there; a silent loss is worse."""
        body = _do_install_body(self.text)
        for path in ("fcron.allow", "fcron.deny"):
            with self.subTest(path=path):
                self.assertIn(path, body,
                              f"do_install never mentions {path}, so upstream "
                              "dropping it from `make install` would ship a "
                              "package that silently denies every user")

    def test_fcrontab_is_owned_by_fcron_not_root(self):
        """The saved uid it can return to is decided by who owns the file."""
        calls = self._post_install_calls()
        chowns = [c for c in calls if c.startswith("chown ")]
        owning = [c for c in chowns if "/usr/bin/fcrontab" in c]
        self.assertTrue(owning, f"post_install never chowns fcrontab: {chowns}")
        for c in owning:
            with self.subTest(call=c):
                self.assertIn(
                    "fcron:fcron", c,
                    "fcrontab is chowned to something other than fcron:fcron. "
                    "It runs setuid to its OWNER, drops to the invoking user, "
                    "and then asks to become uid 22 - which it can only do if "
                    "22 is the uid it started as. Owned by root it fails with "
                    "'could not change euid to 22: Operation not permitted' and "
                    "refuses every caller.")

    def test_fcrontab_keeps_its_setuid_and_setgid_bits(self):
        """chown clears them, even for root - so the chmod has to come after."""
        calls = self._post_install_calls()
        order = [c for c in calls
                 if ("/usr/bin/fcrontab" in c
                     and (c.startswith("chown ") or c.startswith("chmod ")))]
        self.assertTrue(order, "post_install neither chowns nor chmods fcrontab")
        self.assertTrue(order[0].startswith("chown "),
                        f"fcrontab is chmodded before it is chowned: {order}")
        self.assertTrue(
            any(c.startswith("chmod ") and "6755" in c for c in order),
            f"the setuid+setgid mode is not restored after the chown: {order}")

    # ---------- defect 2: the PAM configuration ----------

    def _staged_pam_stacks(self):
        """The PAM stacks do_install stages, read out of the recipe.

        The bytes are written from a heredoc, the same way the `at` recipe
        stages /etc/pam.d/atd, so this reads the heredoc body rather than a
        file on disk - that body IS what ships. Parsed by locating the redirect
        into ${DESTDIR}/etc/pam.d/ and taking the block up to its terminator,
        so a stack the recipe stops staging disappears from this mapping
        instead of quietly keeping an old answer.
        """
        body = _do_install_body(self.text)
        stacks = {}
        pattern = re.compile(
            r'cat\s*>\s*"\$\{DESTDIR\}/etc/pam\.d/(?P<name>[^"]+)"\s*<<\s*"?(?P<eof>\w+)"?\n'
            r'(?P<content>.*?)\n(?P=eof)\n', re.DOTALL)
        for m in pattern.finditer(body):
            name = m.group("name")
            content = m.group("content")
            # A loop variable in the destination stages one file per service.
            if "${_svc}" in name:
                loop = re.search(r'for\s+_svc\s+in\s+([^\n;]+)', body)
                self.assertIsNotNone(
                    loop, "the pam.d install loops but names no services")
                for svc in loop.group(1).split():
                    stacks[svc] = content.replace("${_svc}", svc)
            else:
                stacks[name] = content
        return stacks

    def test_a_pam_service_file_ships_for_each_service_name(self):
        stacks = self._staged_pam_stacks()
        for service in PAM_SERVICES:
            with self.subTest(service=service):
                self.assertIn(
                    service, stacks,
                    f"do_install stages no /etc/pam.d/{service}. Linux-PAM then "
                    "falls through to /etc/pam.d/other, which is pam_warn plus "
                    f"pam_deny, and every caller is refused. Staged: {sorted(stacks)}")

    def test_no_pam_stack_this_package_ships_can_prompt(self):
        stacks = self._staged_pam_stacks()
        for service, content in stacks.items():
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.split()[0] != "auth":
                    continue
                with self.subTest(service=service, line=stripped):
                    for module in PROMPTING_MODULES:
                        self.assertNotIn(
                            module, stripped,
                            f"{module} in the auth stack can ask for a password. "
                            "fcron has no way to answer one, and fcrontab is "
                            "also run from scripts; authorization here is "
                            "fcron.allow and fcron.deny")

    def test_the_account_stack_still_refuses_a_locked_account(self):
        for service, content in self._staged_pam_stacks().items():
            account = [l.strip() for l in content.splitlines()
                       if l.strip().startswith("account")]
            with self.subTest(service=service):
                self.assertTrue(
                    any("pam_unix.so" in l for l in account),
                    "nothing in the account stack consults the system account "
                    f"database, so a locked or expired account keeps its fcron "
                    f"access. Account stack: {account}")

    def test_the_package_does_not_own_a_system_wide_pam_conf(self):
        body = _do_install_body(self.text)
        self.assertRegex(
            body, r'rm\s+-f[^\n]*\$\{?DESTDIR\}?/etc/pam\.conf',
            "do_install does not remove ${DESTDIR}/etc/pam.conf. Upstream's "
            "`make install` writes it there; Linux-PAM ignores it whenever "
            "/etc/pam.d exists, which is always on this system, so those bytes "
            "have never had an effect - and a package must not claim a "
            "system-wide PAM configuration file either way")

    def test_the_shipped_pam_files_are_registered_for_verification(self):
        yml = (FCRON_DIR / "package.yml").read_text()
        for service in PAM_SERVICES:
            with self.subTest(service=service):
                self.assertIn(f"/etc/pam.d/{service}", yml,
                              "the file is not in verify_paths, so it can go "
                              "missing from an install without anything saying so")


if __name__ == "__main__":
    unittest.main()
