# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The kernel recipe names /boot/System.map-* and /boot/config-* by release.

WHY THIS EXISTS. packages/core/linux-kernel/build.sh installs three boot
artifacts. The kernel image is named from the recipe's computed release,
${KVER} = <version>-igos-<release>; System.map and .config were named from a
literal "6.18.10" typed into the same two lines. So an installed system has

    /boot/vmlinuz-6.18.10-igos-17     <- release-stamped, correct
    /boot/System.map-6.18.10          <- version only
    /boot/config-6.18.10              <- version only

(measured on the R001.1 install, 2026-08-22). Every tool that looks for the
running kernel's configuration looks for /boot/config-$(uname -r) - that is
the name the kernel's own Kconfig documentation, and every module builder and
`make oldconfig` workflow, uses. On this system that path does not exist, so
those tools report the kernel as having no config rather than reading the one
that is sitting right there under a different name. The two files are also
NOT release-stamped, which means two kernel releases of the same version
overwrite each other's map and config while their images coexist: the retained
previous kernel silently acquires the new kernel's symbol table.

WHAT THIS MEASURES. The real recipe text, parsed rather than pattern-matched
for a substring: every `cp`/`install` line under do_install that writes into
${DESTDIR}/boot is extracted, and its destination basename must carry the
recipe's release expression. A literal version anywhere in those destinations
fails. Text alone is what a recipe IS - there is no build to run here without
a full kernel compile - so the check is made specific by locating the install
lines first and asserting about their destinations, not by grepping the file
for a version string that also appears in comments and documentation paths.

The recipe's own release expression is read from the recipe, so bumping the
kernel version cannot make this test pass vacuously.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# BOTH kernel recipes, because both stage into ${DESTDIR}/boot and both had
# the literal version. The pass-2 recipe states the contract in its own
# comment - "both passes stage the identical /boot/vmlinuz-<KVER>" - and these
# were the two lines that were not identical.
RECIPES = (
    REPO_ROOT / "packages" / "core" / "linux-kernel" / "build.sh",
    REPO_ROOT / "packages" / "core" / "linux-kernel-pass2" / "build.sh",
)


def _do_install_body(text):
    """The do_install function body, by brace depth - not by a line window."""
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


# A copy/install command whose destination is under ${DESTDIR}/boot.
BOOT_DEST_RE = re.compile(
    r'^\s*(?:cp|install)\b[^\n]*?"\$\{DESTDIR\}/boot/(?P<dest>[^"]+)"',
    re.MULTILINE,
)


class KernelBootArtifactNames(unittest.TestCase):

    def setUp(self):
        self.bodies = {}
        for recipe in RECIPES:
            text = recipe.read_text()
            self.bodies[recipe] = (text, _do_install_body(text))

    def test_each_recipe_still_computes_a_release_stamped_kver(self):
        """Pin the expression the destinations below have to use."""
        for recipe, (text, _) in self.bodies.items():
            with self.subTest(recipe=recipe.parent.name):
                self.assertRegex(
                    text,
                    r'(?m)^KVER="\$\{PKG_VERSION\}-igos-\$\{_KREL\}"$',
                    "this recipe no longer computes KVER as "
                    "<version>-igos-<release>; its premise and the boot "
                    "artifact naming both move with it",
                )

    def test_boot_destinations_are_found_at_all(self):
        """A parser that matches nothing would make every check below vacuous."""
        for recipe, (_, body) in self.bodies.items():
            with self.subTest(recipe=recipe.parent.name):
                dests = [m.group("dest") for m in BOOT_DEST_RE.finditer(body)]
                self.assertGreaterEqual(
                    len(dests), 3,
                    f"expected the image, System.map and config installs; "
                    f"found {dests}")
                self.assertTrue(any(d.startswith("vmlinuz-") for d in dests), dests)
                self.assertTrue(any(d.startswith("System.map-") for d in dests), dests)
                self.assertTrue(any(d.startswith("config-") for d in dests), dests)

    def test_every_boot_artifact_is_named_from_the_release(self):
        for recipe, (_, body) in self.bodies.items():
            for m in BOOT_DEST_RE.finditer(body):
                dest = m.group("dest")
                with self.subTest(recipe=recipe.parent.name, dest=dest):
                    self.assertIn(
                        "${KVER}", dest,
                        f"${{DESTDIR}}/boot/{dest} is not named from the "
                        "recipe's release, so it neither matches $(uname -r) on "
                        "the installed system nor stays distinct across two "
                        "releases of one version")

    def test_no_boot_artifact_carries_a_literal_version(self):
        literal = re.compile(r"\d+\.\d+\.\d+")
        for recipe, (_, body) in self.bodies.items():
            for m in BOOT_DEST_RE.finditer(body):
                dest = m.group("dest")
                with self.subTest(recipe=recipe.parent.name, dest=dest):
                    self.assertIsNone(
                        literal.search(dest),
                        f"${{DESTDIR}}/boot/{dest} has a version typed into it; "
                        "it will keep the old number the next time the kernel "
                        "is bumped")

    def test_both_recipes_stage_the_same_boot_artifact_names(self):
        """The pass-2 recipe states this contract in its own comment."""
        sets = []
        for recipe, (_, body) in self.bodies.items():
            sets.append((recipe.parent.name,
                         {m.group("dest") for m in BOOT_DEST_RE.finditer(body)}))
        (name_a, a), (name_b, b) = sets
        self.assertEqual(a, b,
                         f"{name_a} and {name_b} stage different /boot names; "
                         "the installer enforces that one package owns each")

    def test_boot_installs_never_prompt(self):
        """`cp -i` asks a question, and answers "no" when nobody is there.

        Build code must not be able to block on a person, and the failure mode
        here is the silent one: with no terminal, `cp -i` declines the copy and
        still exits 0, so a rebuild into a populated staging root would ship
        the PREVIOUS artifact while the build reported success.
        """
        for recipe, (_, body) in self.bodies.items():
            for line in body.splitlines():
                if not BOOT_DEST_RE.match(line):
                    continue
                flags = re.match(r'\s*(?:cp|install)\s+(-\S+)', line)
                with self.subTest(recipe=recipe.parent.name, line=line.strip()):
                    self.assertFalse(
                        flags and "i" in flags.group(1).lstrip("-"),
                        "this boot-artifact install runs with -i (interactive)")


if __name__ == "__main__":
    unittest.main()
