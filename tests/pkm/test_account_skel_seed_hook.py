#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The account-database skeleton is seeded BEFORE the first systemd-sysusers run.

intergenos-base-files ships passwd/group/shadow/gshadow as reference data under
/usr/share and the only path from there to /etc is a create-only helper (decided
2026-07-24). Create-only makes ordering the whole property: systemd-sysusers
creates the databases itself when they are absent, populated with its declared
entries and nothing else, so a seed that arrives after that run can only report
what it found. The skeleton's baseline accounts — bin, sys, daemon — are
declared by no sysusers.d fragment, and their absence was measured on a fresh
install as openssh's post_install refusing with "invalid group 'sys'", the
man-db tmpfiles entry exiting 65 every boot, and `pkm verify man-db` DEGRADED.

The seed therefore runs from pkm's canonical pre-lifecycle hook list, ahead of
the sysusers entry. These tests pin that ordering two ways: structurally, as the
position of the two hooks in CANONICAL_HOOKS_PRE, and behaviourally, by having
the systemd-sysusers stub record whether the databases already existed at the
moment it was invoked. They exercise the REAL shipped helper script, copied out
of the recipe tree, so a change to its create-only contract fails here.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from pkm.hooks import (
    ACCOUNT_SEED_SCRIPT_REL,
    ACCOUNT_SKEL_REL,
    CANONICAL_HOOKS_PRE,
    _account_skel_seed_cmd,
    run_canonical_hooks,
)

REPO_ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
        cwd=str(Path(__file__).resolve().parent),
    ).stdout.strip()
)
SHIPPED_SEED_SCRIPT = (
    REPO_ROOT / "packages/core/intergenos-base-files/files"
    / ACCOUNT_SEED_SCRIPT_REL
)

# Baseline rows the skeleton carries and sysusers.d fragments do not declare.
SKELETON = {
    "passwd": (
        "root:x:0:0:root:/root:/bin/bash\n"
        "bin:x:1:1:bin:/dev/null:/usr/bin/false\n"
        "daemon:x:6:6:Daemon User:/dev/null:/usr/bin/false\n"
    ),
    "group": "root:x:0:\nbin:x:1:daemon\nsys:x:2:\ndaemon:x:6:\n",
    "shadow": "root:*:19000::::::\nbin:*:19000::::::\n",
    "gshadow": "root:*::\nbin:*::\n",
}

SYSUSERS_CONF_REL = "usr/lib/sysusers.d/testpkg.conf"


class AccountSkelSeedHookTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-seedhook-")
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self.root = Path(self.tmp) / "root"
        (self.root / "usr/lib/sysusers.d").mkdir(parents=True)
        (self.root / SYSUSERS_CONF_REL).write_text("u testsvc 990 - -\n")
        self._orig_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin}:{self._orig_path}"

    def tearDown(self):
        os.environ["PATH"] = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ---------------------------------------------------------

    def _install_skeleton(self):
        skel = self.root / ACCOUNT_SKEL_REL
        skel.mkdir(parents=True)
        for name, content in SKELETON.items():
            (skel / name).write_text(content)

    def _install_seed_script(self):
        dest = self.root / ACCOUNT_SEED_SCRIPT_REL
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SHIPPED_SEED_SCRIPT, dest)
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR)

    def _stub_sysusers(self):
        """systemd-sysusers stub that records the state it was handed.

        It writes one line per invocation naming whether <root>/etc/passwd
        already existed — the behavioural half of the ordering assertion —
        and then creates the databases the way the real tool would, so a
        seed that ran afterwards would be provably too late to matter.
        """
        log = self.bin / "sysusers-observations.log"
        path = self.bin / "systemd-sysusers"
        path.write_text(
            "#!/bin/bash\n"
            "root=/\n"
            'while [ $# -gt 0 ]; do\n'
            '    if [ "$1" = "--root" ]; then root="$2"; shift 2; else shift; fi\n'
            "done\n"
            'if [ -f "${root%/}/etc/passwd" ]; then\n'
            f'    echo "passwd_present_at_sysusers=yes" >> "{log}"\n'
            "else\n"
            f'    echo "passwd_present_at_sysusers=no" >> "{log}"\n'
            "fi\n"
            'mkdir -p "${root%/}/etc"\n'
            'for db in passwd group shadow gshadow; do\n'
            '    printf "testsvc:x:990:990::/:/usr/bin/false\\n" '
            '>> "${root%/}/etc/${db}"\n'
            "done\n"
            "exit 0\n"
        )
        path.chmod(0o755)
        return log

    def _fire_pre_hooks(self):
        return run_canonical_hooks(
            self.root, [SYSUSERS_CONF_REL], "testpkg", "1.0", "install",
            hooks=CANONICAL_HOOKS_PRE,
        )

    # -- structural ordering ----------------------------------------------

    def test_seed_hook_precedes_sysusers_in_the_pre_hook_list(self):
        ids = [h.id for h in CANONICAL_HOOKS_PRE]
        self.assertIn("account-skel-seed", ids)
        self.assertIn("sysusers", ids)
        self.assertLess(
            ids.index("account-skel-seed"), ids.index("sysusers"),
            "the create-only seed has no effect once sysusers has written the "
            "databases; it must be iterated first",
        )

    def test_seed_hook_is_critical(self):
        hook = next(h for h in CANONICAL_HOOKS_PRE if h.id == "account-skel-seed")
        self.assertTrue(
            hook.critical,
            "a target left without baseline accounts is not a cosmetic outcome",
        )

    def test_seed_and_sysusers_share_a_trigger_pattern(self):
        seed, sysusers = (
            next(h for h in CANONICAL_HOOKS_PRE if h.id == wanted)
            for wanted in ("account-skel-seed", "sysusers")
        )
        self.assertEqual(
            seed.pattern.pattern, sysusers.pattern.pattern,
            "the pair must fire together on the same install, or the seed "
            "can be skipped on the very install that creates the databases",
        )

    # -- behavioural ordering ---------------------------------------------

    def test_databases_are_seeded_before_sysusers_runs(self):
        self._install_skeleton()
        self._install_seed_script()
        log = self._stub_sysusers()

        result = self._fire_pre_hooks()

        self.assertEqual(result.critical_failures, [], result.messages)
        self.assertTrue(log.exists(), "the sysusers stub should have been invoked")
        self.assertEqual(
            log.read_text().strip(), "passwd_present_at_sysusers=yes",
            "sysusers must find the seeded databases already in place",
        )
        passwd = (self.root / "etc/passwd").read_text()
        self.assertIn("bin:x:1:1:", passwd, "skeleton baseline rows must be present")
        self.assertIn("daemon:x:6:6:", passwd)
        self.assertIn(
            "sys:x:2:", (self.root / "etc/group").read_text(),
            "the group 'sys' is the one openssh's post_install resolves",
        )
        for db in ("passwd", "group", "shadow", "gshadow"):
            self.assertTrue((self.root / "etc" / db).is_file(), db)

    def test_existing_databases_are_never_rewritten(self):
        self._install_skeleton()
        self._install_seed_script()
        self._stub_sysusers()
        etc = self.root / "etc"
        etc.mkdir()
        live = "root:x:0:0::/root:/bin/bash\nreal-user:x:1000:1000::/home/u:/bin/bash\n"
        (etc / "passwd").write_text(live)

        self.assertIsNone(
            _account_skel_seed_cmd(self.root, [SYSUSERS_CONF_REL]),
            "an existing /etc/passwd is the system's own account state",
        )
        result = self._fire_pre_hooks()
        self.assertEqual(result.critical_failures, [], result.messages)
        self.assertIn(
            "real-user", (etc / "passwd").read_text(),
            "the live account rows must survive the hook untouched",
        )

    def test_no_command_without_the_skeleton_or_the_helper(self):
        self.assertIsNone(
            _account_skel_seed_cmd(self.root, [SYSUSERS_CONF_REL]),
            "no skeleton and no helper on the root means nothing to seed from",
        )
        self._install_skeleton()
        self.assertIsNone(
            _account_skel_seed_cmd(self.root, [SYSUSERS_CONF_REL]),
            "the skeleton alone is not a seed path — the helper owns the copy",
        )
        self._install_seed_script()
        cmd = _account_skel_seed_cmd(self.root, [SYSUSERS_CONF_REL])
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[-2:], ["--root", str(self.root)])
        self.assertTrue(
            cmd[1].startswith(str(self.root)),
            "the helper must be the one shipped on the target root",
        )

    def test_sysusers_still_runs_on_a_root_with_no_skeleton(self):
        log = self._stub_sysusers()
        result = self._fire_pre_hooks()
        self.assertEqual(result.critical_failures, [], result.messages)
        self.assertEqual(
            log.read_text().strip(), "passwd_present_at_sysusers=no",
            "absent seed inputs must not suppress the sysusers hook",
        )


if __name__ == "__main__":
    unittest.main()
