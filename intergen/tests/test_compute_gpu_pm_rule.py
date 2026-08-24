"""The secondary-AMD-GPU runtime-PM udev rule ships, and ships correctly.

A compute-serving discrete GPU's runtime suspend/resume cycling collapses the
desktop's monitor layout (background drops, windows pulled to the primary
monitor). The mechanism was measured twice (2026-08-03, 2026-08-07): the
kernel's "SMU is resumed successfully" coincides with every background-loss
moment. The one-line live mitigation is reboot-volatile; the durable fix is
this packaged udev rule. These cases pin the whole shipping chain: the rule
file exists with the exact narrow match it was decided with, the recipe
routes it to the udev rules directory, and the package declares the shipped
path load-bearing.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parents[2]
RULE = TREE / "intergen" / "data" / "70-intergen-compute-gpu-pm.rules"
BUILD = TREE / "packages" / "ai" / "intergen" / "build.sh"
RECIPE = TREE / "packages" / "ai" / "intergen" / "package.yml"
SHIPPED = "/usr/lib/udev/rules.d/70-intergen-compute-gpu-pm.rules"


class ComputeGpuPmRuleShips(unittest.TestCase):
    def test_the_rule_file_exists_with_the_decided_narrow_match(self):
        self.assertTrue(RULE.is_file(), f"missing {RULE}")
        body = RULE.read_text()
        lines = [ln for ln in body.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        self.assertEqual(len(lines), 1, "exactly one match rule, all else comment")
        rule = lines[0]
        for token in ('SUBSYSTEM=="pci"', 'DRIVER=="amdgpu"',
                      'ATTR{boot_vga}=="0"', 'ATTR{power/control}="on"'):
            self.assertIn(token, rule)
        self.assertIn('ATTR{boot_vga}=="0"', rule,
                      "present-and-zero — never a bare boot_vga!=1, which would "
                      "also match devices lacking the attribute")

    def _require_packaging_tree(self, path):
        """Skip when the packaging tree is not beside us.

        This file is SHIPPED: the recipe copies every top-level
        intergen/tests/*.py into the installed package, where there is no
        repository to read. Treating that absence as a failure makes a normal
        installed system report a red suite for a reason that tells the user
        nothing. The established answer in this tree is to skip — the same
        thing test_intergen_unit_scoping.py and test_destructive_policy.py do.
        In a checkout the file is present and the assertion below still runs,
        so nothing about the check is weakened where it can be performed.
        """
        if not path.is_file():
            self.skipTest(f"packaging tree not present ({path})")

    def test_the_recipe_routes_the_rule_to_the_udev_rules_dir(self):
        self._require_packaging_tree(BUILD)
        body = BUILD.read_text()
        self.assertIn("70-intergen-compute-gpu-pm.rules", body)
        self.assertIn(SHIPPED, body, "install line targets the shipped path")
        sys_data = re.search(r'_sys_data="[^"]+"', body, re.S)
        self.assertIsNotNone(sys_data)
        self.assertIn("70-intergen-compute-gpu-pm.rules", sys_data.group(0),
                      "classified in the data-completeness inventory — an "
                      "unrouted data file is a FATAL at do_install")

    def test_the_package_declares_the_shipped_path_load_bearing(self):
        self._require_packaging_tree(RECIPE)
        self.assertIn(SHIPPED, RECIPE.read_text(),
                      "verify_paths must carry the rule so the pre-squashfs "
                      "audit halts a build that failed to ship it")


if __name__ == "__main__":
    unittest.main()
