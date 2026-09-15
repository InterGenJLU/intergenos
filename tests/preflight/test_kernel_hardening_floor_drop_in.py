# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The shipped sysctl hardening floor is the designed one, and the tree agrees with itself.

WHY THIS EXISTS. R001.3 row 23: every installed R001.2 machine ran with
kernel.kptr_restrict 0, kernel.randomize_va_space 1 (heap unrandomised), the
reverse path filter loose on every interface, ICMP redirects accepted and sent,
and martian packets unlogged, because nothing in the tree stated otherwise. The
fix is a drop-in in base-files (/usr/lib/sysctl.d/60-intergenos-hardening.conf)
plus the kernel half (CONFIG_COMPAT_BRK off, asserted by the required-symbol gate).

WHAT THIS MEASURES, at authoring time, from the tree:
  * the drop-in parses as systemd-sysctl reads it — `key = value` lines, comments,
    nothing else — so a typo cannot ship as a silently-ignored line;
  * every key is one of a FIXED set, at the designed value, and every designed
    key is present: the file is a decision record, not a place a value drifts;
  * the file sorts after systemd's 50-default.conf (which sets rp_filter loose
    through a glob) — the ordering is what makes the strict value win;
  * the reverse-path filter is stated on all three of `all`, `default` and the
    interface glob, because the kernel applies the stronger of `all` and the
    interface's own value and 50-default.conf sets the interfaces through a glob;
  * base-files declares the file in verify_paths, so squashfs Step 4.5 refuses an
    image that lost it;
  * the userspace half and the kernel half agree: randomize_va_space = 2 here,
    `# CONFIG_COMPAT_BRK is not set` in the overrides fragment, CONFIG_COMPAT_BRK
    in the gate's disabled list.

Nothing here reads the host's /proc/sys; the installed-system gate does that.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_FILES = REPO_ROOT / "packages/core/intergenos-base-files"
DROP_IN = BASE_FILES / "files/usr/lib/sysctl.d/60-intergenos-hardening.conf"
RECIPE = BASE_FILES / "package.yml"
FRAGMENT = REPO_ROOT / "config/kernel/fragments/99-intergenos-overrides.config"
DISABLED_LIST = REPO_ROOT / "config/kernel/required-disabled-symbols.txt"

# The designed floor. A change here is a decision, recorded in the drop-in's
# header and the release note, never a drift.
DESIGNED = {
    "kernel.kptr_restrict": "1",
    "kernel.randomize_va_space": "2",
    "net.ipv4.conf.all.rp_filter": "1",
    "net.ipv4.conf.default.rp_filter": "1",
    "net.ipv4.conf.*.rp_filter": "1",
    "net.ipv4.conf.all.accept_redirects": "0",
    "net.ipv4.conf.default.accept_redirects": "0",
    "net.ipv4.conf.*.accept_redirects": "0",
    "net.ipv4.conf.all.secure_redirects": "0",
    "net.ipv4.conf.default.secure_redirects": "0",
    "net.ipv4.conf.*.secure_redirects": "0",
    "net.ipv4.conf.all.send_redirects": "0",
    "net.ipv4.conf.default.send_redirects": "0",
    "net.ipv4.conf.*.send_redirects": "0",
    "net.ipv6.conf.all.accept_redirects": "0",
    "net.ipv6.conf.default.accept_redirects": "0",
    "net.ipv6.conf.*.accept_redirects": "0",
    "net.ipv4.conf.all.log_martians": "1",
    "net.ipv4.conf.default.log_martians": "1",
    "net.ipv4.conf.*.log_martians": "1",
}

# systemd.sysctl(5): `key = value`; comments start with # or ;. Keys may carry a
# glob. A leading "-" (do-not-set / ignore-failure) is deliberately NOT accepted
# here: this file states values, it excludes nothing.
RE_ASSIGNMENT = re.compile(r"^([A-Za-z0-9_.*\-]+)\s*=\s*(\S+)$")


def parse_drop_in(text: str) -> dict:
    """Every non-comment, non-blank line must be an assignment; anything else is
    a defect (systemd-sysctl would log it and move on, which is a silent loss)."""
    out = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        m = RE_ASSIGNMENT.match(line)
        if not m:
            raise ValueError(f"line {lineno} is not a `key = value` assignment: {raw!r}")
        key, value = m.groups()
        if key in out:
            raise ValueError(f"line {lineno} sets {key} a second time")
        out[key] = value
    return out


class TestHardeningDropIn(unittest.TestCase):
    def test_control_parser_refuses_a_non_assignment_line(self):
        with self.assertRaises(ValueError):
            parse_drop_in("kernel.kptr_restrict = 1\nkernel.randomize_va_space\n")

    def test_control_parser_refuses_a_duplicate_key(self):
        with self.assertRaises(ValueError):
            parse_drop_in("kernel.kptr_restrict = 1\nkernel.kptr_restrict = 2\n")

    def test_drop_in_is_present_and_parses(self):
        self.assertTrue(DROP_IN.is_file(), f"{DROP_IN} is missing")
        parse_drop_in(DROP_IN.read_text())

    def test_drop_in_states_exactly_the_designed_floor(self):
        got = parse_drop_in(DROP_IN.read_text())
        self.assertEqual(got, DESIGNED, (
            "the shipped drop-in and the designed floor differ:\n"
            f"  missing from the file : {sorted(set(DESIGNED) - set(got))}\n"
            f"  not in the design     : {sorted(set(got) - set(DESIGNED))}\n"
            f"  wrong value           : {sorted(k for k in set(got) & set(DESIGNED) if got[k] != DESIGNED[k])}"
        ))

    def test_drop_in_sorts_after_systemd_default(self):
        """50-default.conf sets rp_filter loose through a glob; only a later file wins."""
        self.assertGreater(DROP_IN.name, "50-default.conf")
        self.assertTrue(DROP_IN.name.startswith("60-"), DROP_IN.name)

    def test_rp_filter_is_stated_on_all_default_and_the_interface_glob(self):
        got = parse_drop_in(DROP_IN.read_text())
        for key in ("net.ipv4.conf.all.rp_filter", "net.ipv4.conf.default.rp_filter",
                    "net.ipv4.conf.*.rp_filter"):
            self.assertEqual(got.get(key), "1", key)

    def test_base_files_declares_the_drop_in_in_verify_paths(self):
        text = RECIPE.read_text()
        block = re.search(r"^verify_paths:\n((?:[ \t]+[-#].*\n)+)", text, re.M)
        self.assertIsNotNone(block, "base-files/package.yml has no verify_paths block")
        entries = {l.strip()[1:].strip() for l in block.group(1).splitlines() if l.strip().startswith("-")}
        self.assertIn("/usr/lib/sysctl.d/60-intergenos-hardening.conf", entries)

    def test_userspace_and_kernel_halves_agree(self):
        """randomize_va_space = 2 is only honoured by a kernel built with
        COMPAT_BRK off; the fragment must ask for that and the gate must refuse
        a config that turned it back on."""
        got = parse_drop_in(DROP_IN.read_text())
        self.assertEqual(got["kernel.randomize_va_space"], "2")
        self.assertIn("# CONFIG_COMPAT_BRK is not set", FRAGMENT.read_text())
        disabled = [l.strip() for l in DISABLED_LIST.read_text().splitlines()
                    if l.strip() and not l.strip().startswith("#")]
        self.assertIn("CONFIG_COMPAT_BRK", disabled)

    def test_kexec_load_disabled_is_deliberately_absent(self):
        """Decided 2026-09-14: lockdown integrity + signed-kexec enforcement already
        refuse an unsigned image; the one-way switch would remove the signed path
        too. The header records the decision; this pins it."""
        got = parse_drop_in(DROP_IN.read_text())
        self.assertNotIn("kernel.kexec_load_disabled", got)
        self.assertIn("kexec_load_disabled", DROP_IN.read_text(), "the decision must be stated in the file")


if __name__ == "__main__":
    unittest.main()
