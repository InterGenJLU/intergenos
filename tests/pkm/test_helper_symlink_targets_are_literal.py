#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A helper records the link a machine actually holds, not a resolved path.

Measured on an installed system, 2026-09-16. A download helper deposits a
node module and links its entry point onto the path; npm leaves a RELATIVE
link, and the machine held a link of the shape

    /usr/bin/<tool> -> ../lib/node_modules/<scope>/<pkg>/bin/<entry>

while the helper's manifest recorded, for the same entry, the RESOLVED
absolute path under /usr/lib/node_modules/.

The link on disk is relative; the record says absolute. Two helpers derived the
recorded target with `readlink -f`, which resolves the link instead of reading
it, so the record described a path the filesystem does not contain at that
entry. Nothing reads the field back, so nothing caught it: the manifest is the
machine's own account of what a helper deposited, and it was not true.

The recording library now reads the link itself, so a caller cannot supply a
resolved path by accident. The end-to-end leg below drives the real library in
a temporary root and asserts the recorded target is the literal one; the source
leg asserts both helpers use it.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HELPER_LIB = REPO / "packages" / "core" / "intergenos-helper-lib" / "helper-lib.sh"
# The recipes this change covers, found by READING THE TREE rather than by
# naming them, so a helper of the same shape added later is covered the day it
# lands.
#
# THE CLASS IS SCOPED, on purpose. It is the helpers that install a NODE MODULE
# and link its entry point onto the path: npm leaves a RELATIVE link, and those
# recipes derived the recorded target from it. A recipe that records a symlink
# it created itself, with a target it already knows, is not this class — it is
# not deriving anything — so widening the scan to every symlink-recording
# recipe would assert something this change never established. (Measured while
# writing this: an unscoped scan also matched a recipe that passes a target it
# supplies directly, which is a separate question and is reported as its own
# finding rather than silently folded in here.)
def _node_module_helper_recipes() -> dict:
    found = {}
    for build in sorted((REPO / "packages").glob("*/*/build.sh")):
        try:
            text = build.read_text()
        except OSError:
            continue
        if "igos_helper_record_symlink" in text and "node_modules" in text:
            found[build.parent.name] = build
    return found


RECIPES = _node_module_helper_recipes()

# The fixture below is SYNTHETIC — a temporary root built by this test. The
# names are deliberately generic: what is under test is the recording library's
# treatment of a relative link, which is the same for any helper.
LINK_NAME = "tool"
SCOPE_DIR = "@vendor"
PACKAGE_DIR = "tool-cli"
ENTRY_NAME = "tool-entry"
RELATIVE_TARGET = f"../lib/node_modules/{SCOPE_DIR}/{PACKAGE_DIR}/bin/{ENTRY_NAME}"


class TheRecordedTargetIsTheLinkOnDisk(unittest.TestCase):
    """Drives the real helper-lib.sh, not a re-implementation of it."""

    def _record_through_the_library(self, root: Path) -> dict:
        usr_bin = root / "usr" / "bin"
        module_bin = (root / "usr" / "lib" / "node_modules" / SCOPE_DIR
                      / PACKAGE_DIR / "bin")
        usr_bin.mkdir(parents=True)
        module_bin.mkdir(parents=True)
        (module_bin / ENTRY_NAME).write_bytes(b"#!/bin/sh\nexit 0\n")
        # The shape the npm install leaves behind: a RELATIVE link.
        (usr_bin / LINK_NAME).symlink_to(RELATIVE_TARGET)

        manifest_dir = root / "var" / "lib" / "igos" / "helpers"
        manifest_dir.mkdir(parents=True)

        script = f"""
set -eu
export IGOS_HELPER_MANIFEST_DIR={manifest_dir!s}
. {HELPER_LIB!s}
igos_helper_init probe-helper
igos_helper_set_version 1.0
igos_helper_record_symlink_literal {usr_bin / LINK_NAME!s}
igos_helper_commit
"""
        run = subprocess.run(["/bin/bash", "-c", script],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0,
                         f"the library refused the recording:\n"
                         f"stdout={run.stdout}\nstderr={run.stderr}")
        return json.loads((manifest_dir / "probe-helper.manifest").read_text())

    def test_a_relative_link_is_recorded_as_the_relative_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self._record_through_the_library(root)
            symlinks = manifest.get("symlinks", [])
            self.assertEqual(len(symlinks), 1, f"expected one entry: {symlinks}")
            entry = symlinks[0]
            self.assertEqual(entry["path"],
                             str(root / "usr" / "bin" / LINK_NAME))
            self.assertEqual(
                entry["target"], RELATIVE_TARGET,
                "the recorded target is not the link the filesystem holds; a "
                "resolved path here makes the manifest describe a machine "
                "state that does not exist")

    def test_the_recorded_target_round_trips_to_the_same_file(self):
        """The literal target still names the deposited file, read from the
        link's own directory — the property a resolved path was bought with."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self._record_through_the_library(root)
            entry = manifest["symlinks"][0]
            link = Path(entry["path"])
            resolved = (link.parent / entry["target"]).resolve()
            self.assertTrue(resolved.is_file(),
                            f"{entry['target']} does not resolve to a file "
                            f"from {link.parent}")


class BothHelpersUseIt(unittest.TestCase):

    def test_the_tree_scan_found_the_recipes_it_is_meant_to_check(self):
        """A scan that matched nothing would pass every assertion below
        without checking anything."""
        self.assertGreaterEqual(
            len(RECIPES), 2,
            "no node-module download-helper recording a symlink was found; "
            "the scan below would then assert nothing")

    def test_neither_helper_resolves_a_link_before_recording_it(self):
        for name, recipe in RECIPES.items():
            with self.subTest(helper=name):
                offenders = [
                    f"{recipe.name}:{n}: {line.strip()}"
                    for n, line in enumerate(
                        recipe.read_text().splitlines(), start=1)
                    if "readlink -f" in line
                ]
                self.assertEqual(
                    offenders, [],
                    f"{name} resolves a link with readlink -f; the recording "
                    f"library reads the link itself")

    def test_each_helper_records_its_binary_link_literally(self):
        for name, recipe in RECIPES.items():
            with self.subTest(helper=name):
                calls = [
                    line.strip()
                    for line in recipe.read_text().splitlines()
                    if "igos_helper_record_symlink" in line
                ]
                self.assertTrue(calls, f"{name} records no symlink at all")
                for call in calls:
                    self.assertIn(
                        "igos_helper_record_symlink_literal", call,
                        f"{name} still passes a target it derived itself: "
                        f"{call}")


if __name__ == "__main__":
    unittest.main()
