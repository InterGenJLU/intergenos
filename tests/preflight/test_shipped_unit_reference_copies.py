# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The in-tree unit copies must document what the recipe actually installs.

`intergen/data/intergen.service` and `intergen/data/com.intergenos.InterGen.service`
are not packaged. The recipe writes both units to their real locations from
heredocs and, in the same place, excludes the tree copies from the package with
the stated reason that they "document shipped content".

A copy that documents shipped content and does not match it is worse than no copy
at all: the next person to work on the privileged boundary reads the tree, sees a
unit with no hardening in it, and reasons about a system that does not exist.

WHERE THIS FILE LIVES, AND WHY IT MOVED (2026-08-24). It used to sit in
`intergen/tests/`, which the recipe copies wholesale into the installed package.
On a user's machine there is no repository to read, so it failed three times on
every installed system — a red suite for a reason that told the user nothing.
This is a check ON the packaging tree, so it belongs with the other preflight
gates, which are not shipped. Nothing here is weakened by the move; the checks
below are strictly stronger than the ones that moved.

WHAT AN INDEPENDENT REVIEW FOUND WRONG WITH THE OLD VERSION, all three fixed here:

 1. It used `pattern.search()` and compared against the FIRST heredoc it found,
    asserting nothing about whether a later line also wrote that destination. A
    second writer could decide the packaged file while the first heredoc and the
    tree copy stayed equal, and every assertion would still be green.
    -> `test_exactly_one_command_writes_each_destination` now requires a unique
       producer, and looks for overlay writes by any of the usual means.

 2. It compared decoded source text to decoded source text and never looked at
    an artifact. A source-to-source comparison cannot establish what a consumer
    receives.
    -> `test_the_staged_artifact_matches_the_documented_copy` executes the
       recipe's OWN install command into a temporary DESTDIR and compares the
       staged bytes. That is the nearest consumer boundary reachable without a
       full build, and the residue beyond it is named in the class docstring.

 3. Its hardening floor used unanchored substring membership, so
    `CapabilityBoundingSet=` matched the COMMENT on line 51 as readily as the
    real directive on line 97 — deleting the directive would have left the test
    green.
    -> `test_the_documented_user_unit_carries_its_hardening` parses effective
       directives and ignores comments, and a control below proves the old
       shape passed where the new one fails.

The controls are not decoration. Each one mutates a fixture into the exact shape
being guarded against and requires this file's own logic to reject it, because a
gate that has never been shown to fail is not evidence that anything passed.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RECIPE = _REPO_ROOT / "packages" / "ai" / "intergen" / "build.sh"

# destination written by the recipe -> the tree copy that documents it
_PAIRS = {
    "/usr/lib/systemd/user/intergen.service":
        _REPO_ROOT / "intergen" / "data" / "intergen.service",
    "/usr/share/dbus-1/services/com.intergenos.InterGen.service":
        _REPO_ROOT / "intergen" / "data" / "com.intergenos.InterGen.service",
}

#: The one command shape this gate knows how to execute safely. The extracted
#: snippet is matched against this in full before it is run: the heredoc tag is
#: single-quoted (so the body is taken literally, with no expansion and no
#: command substitution), the only variable is DESTDIR, and the command is
#: `install`. Anything else is refused rather than executed — this file runs a
#: fragment of a build recipe, and the safety of doing that rests entirely on
#: having read what the fragment is first.
_INSTALL_BLOCK = re.compile(
    r"\Ainstall\s+-Dm(?P<mode>\d+)\s+/dev/stdin\s+\"\$\{DESTDIR\}"
    r"(?P<dest>/[A-Za-z0-9._/+-]+)\"\s*<<\s*'(?P<tag>[A-Z0-9_]+)'\n"
    r"(?P<body>.*?)\n(?P=tag)\n\Z",
    re.DOTALL,
)


def _install_blocks(recipe_text: str, destination: str) -> list[str]:
    """Every `install ... /dev/stdin <destination> << 'TAG' ... TAG` block.

    Returns the WHOLE matched text of each, so a caller can both count the
    producers and re-execute one. A list, deliberately: the count is the
    finding, and a function that returned the first match would be reproducing
    the defect this gate exists to close.
    """
    pattern = re.compile(
        r"install\s+-Dm\d+\s+/dev/stdin\s+\"\$\{DESTDIR\}"
        + re.escape(destination)
        + r"\"\s*<<\s*'(?P<tag>[A-Z0-9_]+)'\n(?:.*?)\n(?P=tag)\n",
        re.DOTALL,
    )
    return [m.group(0) for m in pattern.finditer(recipe_text)]


def _other_writers(recipe_text: str, destination: str) -> list[str]:
    """Lines that write `destination` by some means other than the heredoc.

    A later `cp`, `sed -i`, `tee`, `ln -sf` or shell redirection aimed at the
    same staged path would decide the packaged bytes while leaving the heredoc
    and the tree copy in perfect agreement.
    """
    hits = []
    for line in recipe_text.splitlines():
        if destination not in line:
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r"install\s+-Dm\d+\s+/dev/stdin", stripped):
            continue          # the heredoc producer itself, counted elsewhere
        if re.search(r"\b(cp|mv|sed|tee|ln|cat|printf|echo)\b|>>?\s*\"?\$\{DESTDIR\}",
                     stripped):
            hits.append(stripped)
    return hits


def _stage_with_the_recipes_own_command(block: str, destination: str) -> bytes:
    """Run the recipe's install command into a temporary DESTDIR; return bytes.

    This executes a fragment of a build recipe, so the fragment is validated
    against `_INSTALL_BLOCK` first and refused if it is anything other than the
    single expected `install` with a quoted heredoc. `install` writes one file
    to a path underneath the DESTDIR handed to it, and the heredoc body cannot
    expand or substitute; nothing else in the recipe is sourced.
    """
    match = _INSTALL_BLOCK.match(block)
    if match is None:
        raise AssertionError(
            "the install block for this destination is no longer the shape "
            "this gate validated before executing it; re-derive the gate "
            "rather than relaxing the pattern:\n" + block
        )
    with tempfile.TemporaryDirectory(prefix="unit-staging-") as destdir:
        completed = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", block],
            cwd=destdir, env={"PATH": "/usr/bin:/bin", "DESTDIR": destdir},
            capture_output=True, text=True, timeout=60,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"the recipe's own install command failed in a temporary "
                f"DESTDIR (rc={completed.returncode}): {completed.stderr}"
            )
        staged = Path(destdir) / destination.lstrip("/")
        if not staged.is_file():
            raise AssertionError(
                f"the recipe's install command reported success but staged no "
                f"file at {staged}"
            )
        return staged.read_bytes()


def _effective_directives(unit_text: str) -> list[str]:
    """`Key=Value` lines that systemd would actually read.

    Comments and blank lines are dropped, and a continued line is joined, so a
    directive named only inside a comment does not count as present. The old
    version of this gate used substring membership and would have accepted the
    parenthetical "(CapabilityBoundingSet= empty)" in this unit's own comments
    as proof that the directive was set.
    """
    directives, pending = [], ""
    for raw in unit_text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        line = pending + line
        if line.endswith("\\"):
            pending = line[:-1]
            continue
        pending = ""
        if "=" in line and not line.startswith("["):
            directives.append(line)
    return directives


def _has_directive(unit_text: str, key: str) -> bool:
    return any(d.split("=", 1)[0].strip() == key
               for d in _effective_directives(unit_text))


@unittest.skipUnless(_RECIPE.is_file(), "packaging tree not present")
class ShippedUnitReferenceCopyTests(unittest.TestCase):
    """Proven here: a unique heredoc producer per destination; the staged bytes
    that producer emits; equality with the documented tree copy; the hardening
    floor as effective directives.

    NOT proven here, and named rather than implied: the bytes inside a built
    package archive or a booted image. Reaching those needs a real build, which
    this gate deliberately does not perform. What it establishes is that the
    single command which produces them, run as the recipe writes it, produces
    the documented content.
    """

    def setUp(self):
        self.recipe_text = _RECIPE.read_text(encoding="utf-8")

    def test_exactly_one_command_writes_each_destination(self):
        for destination in _PAIRS:
            with self.subTest(destination=destination):
                blocks = _install_blocks(self.recipe_text, destination)
                self.assertEqual(
                    len(blocks), 1,
                    f"{len(blocks)} heredoc install commands write "
                    f"{destination}; with more than one, comparing the first "
                    f"proves nothing about what the package finally contains",
                )
                overlays = _other_writers(self.recipe_text, destination)
                self.assertEqual(
                    overlays, [],
                    f"{destination} is also written by other means, so the "
                    f"heredoc is not the last word on its content: {overlays}",
                )

    def test_the_staged_artifact_matches_the_documented_copy(self):
        for destination, tree_copy in _PAIRS.items():
            with self.subTest(destination=destination):
                self.assertTrue(
                    tree_copy.is_file(),
                    f"{tree_copy} is missing; the recipe excludes it from the "
                    f"package on the stated grounds that it documents "
                    f"{destination}",
                )
                (block,) = _install_blocks(self.recipe_text, destination)
                staged = _stage_with_the_recipes_own_command(block, destination)
                self.assertEqual(
                    tree_copy.read_bytes(), staged,
                    f"{tree_copy.relative_to(_REPO_ROOT)} does not match the "
                    f"bytes the recipe's own command stages at {destination}",
                )

    def test_the_documented_user_unit_carries_its_hardening(self):
        """A directly stated floor, read as effective directives.

        A future edit cannot quietly produce a copy that parses but describes
        an unhardened service, and — unlike the version this replaces — cannot
        satisfy the floor with a mention in a comment.
        """
        documented = _PAIRS["/usr/lib/systemd/user/intergen.service"].read_text(
            encoding="utf-8")
        for directive in (
            "NoNewPrivileges",
            "ProtectSystem",
            "CapabilityBoundingSet",
            "SystemCallFilter",
            "RestrictAddressFamilies",
        ):
            with self.subTest(directive=directive):
                self.assertTrue(
                    _has_directive(documented, directive),
                    f"{directive}= is not an effective directive in the "
                    f"documented unit (a mention in a comment does not count)",
                )

    def test_the_recipe_still_excludes_both_copies_from_the_package(self):
        """The comparison above is only meaningful while the copies are
        reference material rather than packaged files."""
        self.assertRegex(
            self.recipe_text,
            r"_excl_data=\"[^\"]*intergen\.service[^\"]*\"",
        )
        self.assertRegex(
            self.recipe_text,
            r"_excl_data=\"[^\"]*com\.intergenos\.InterGen\.service[^\"]*\"",
        )


class TheGateIsStrongerThanTheOneItReplaces(unittest.TestCase):
    """Controls. Each mutates a fixture into the shape the review named and
    requires this file's logic to reject it — and, where it matters, shows the
    OLD shape accepting the same input."""

    _UNIT = (
        "[Unit]\n"
        "Description=probe\n"
        "\n"
        "[Service]\n"
        "# hardening note: the bounding set is emptied "
        "(CapabilityBoundingSet= empty) below\n"
        "NoNewPrivileges=yes\n"
    )

    def test_a_directive_named_only_in_a_comment_is_not_present(self):
        self.assertIn(
            "CapabilityBoundingSet=", self._UNIT,
            "fixture precondition: the old substring test's input is present",
        )
        self.assertFalse(
            _has_directive(self._UNIT, "CapabilityBoundingSet"),
            "a directive mentioned only inside a comment was counted as set — "
            "this gate is no stronger than the substring test it replaces",
        )

    def test_the_effective_parser_still_finds_a_real_directive(self):
        """Negative control: the parser must not simply reject everything."""
        self.assertTrue(_has_directive(self._UNIT, "NoNewPrivileges"))

    def test_a_second_producer_is_detected(self):
        destination = "/usr/lib/systemd/user/probe.service"
        one = (
            'install -Dm644 /dev/stdin "${DESTDIR}' + destination + '" << \'SVC\'\n'
            "[Service]\nExecStart=/bin/true\nSVC\n"
        )
        self.assertEqual(len(_install_blocks(one, destination)), 1)
        self.assertEqual(len(_install_blocks(one + one, destination)), 2,
                         "a second writer of the same destination went unseen")

    def test_an_overlay_write_is_detected(self):
        destination = "/usr/lib/systemd/user/probe.service"
        recipe = (
            'install -Dm644 /dev/stdin "${DESTDIR}' + destination + '" << \'SVC\'\n'
            "[Service]\nExecStart=/bin/true\nSVC\n"
            'sed -i "s/true/false/" "${DESTDIR}' + destination + '"\n'
        )
        self.assertTrue(
            _other_writers(recipe, destination),
            "a later sed aimed at the staged file was not detected",
        )

    def test_a_comment_mentioning_the_path_is_not_an_overlay(self):
        """Negative control for the overlay scan."""
        destination = "/usr/lib/systemd/user/probe.service"
        recipe = "# we deliberately do not cp " + destination + " anywhere\n"
        self.assertEqual(_other_writers(recipe, destination), [])

    def test_an_unexpected_command_shape_is_refused_not_executed(self):
        """The executor must refuse a block it did not validate."""
        with self.assertRaises(AssertionError):
            _stage_with_the_recipes_own_command(
                'cp /etc/passwd "${DESTDIR}/x"\n', "/x")


if __name__ == "__main__":
    unittest.main()
