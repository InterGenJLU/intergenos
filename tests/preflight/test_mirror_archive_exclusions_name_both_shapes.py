# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The mirror-only exclusion list names every archive a mirror package may be.

scripts/derive-iso-exclusions.py --mode=archive-excludes tells the squashfs
build which archives stay off the ISO. It composed each name by hand as
<name>-<version>.igos.tar.gz. Since 2026-09-22 the producers write
<name>-<version>-<release>.igos.tar.gz for every recipe that states a release
(all of them), so the list named no file any producer writes: measured on the
real tree, 321 of 321 lines named an archive nothing builds, and every
mirror-only archive would have shipped on the ISO.

A lineage build substrate also still holds archives built before that date,
under the release-less name, until each package is rebuilt. The list must
therefore carry BOTH names of each package, composed by pkm/archive_names.py
rather than by another f-string, and group them so a reader that reports per
package (derive-iso-archive-manifest.py) does not report a built package as
unbuilt because its other name is absent.

Every case runs the real scripts as programs against real files in a
temporary directory, except the real-tree case, which reads the repository's
own packages/ tree because the claim under test is about the real recipes.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DERIVE_EXCLUDES = REPO_ROOT / "scripts" / "derive-iso-exclusions.py"
DERIVE_ISO_MANIFEST = REPO_ROOT / "scripts" / "derive-iso-archive-manifest.py"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
from pkm.archive_names import archive_filename  # noqa: E402
import manifest_coverage as mc  # noqa: E402

RECIPE = """\
name: {name}
version: "{version}"
release: {release}
description: test fixture package
license: MIT
source:
- url: https://example.org/{name}-{version}.tar.gz
  sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
dependencies:
  build: []
  host: []
  runtime: []
tier: {tier}
build_style: custom
verify_paths:
  - /usr/bin/{name}
{extra}"""

ARC = "var/lib/igos/archives/"


def write_recipe(packages: Path, tier: str, name: str, version: str,
                 release: int, extra: str = "", directory: str = "") -> None:
    d = packages / tier / (directory or name)
    d.mkdir(parents=True)
    (d / "package.yml").write_text(RECIPE.format(
        name=name, version=version, release=release, tier=tier, extra=extra))


def derive_excludes(packages: Path, output: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(DERIVE_EXCLUDES), "--mode=archive-excludes",
         "--packages", str(packages), "--output", str(output)],
        capture_output=True, text=True)


def names_in(output: Path) -> list[str]:
    return [line for line in output.read_text().splitlines()
            if line and not line.startswith("#")]


class ExclusionListNamesBothShapes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.packages = self.root / "packages"
        self.output = self.root / "excludes.txt"

    def test_a_mirror_package_is_listed_under_the_name_the_producers_write(self):
        write_recipe(self.packages, "extra", "mirrored", "1.0", 3)
        write_recipe(self.packages, "core", "ships", "2.0", 1)
        r = derive_excludes(self.packages, self.output)
        self.assertEqual(r.returncode, 0, r.stderr)
        names = names_in(self.output)
        self.assertIn(ARC + archive_filename("mirrored", "1.0", 3), names)
        self.assertIn(ARC + "mirrored-1.0-3.igos.tar.gz", names)

    def test_and_under_the_release_less_name_a_lineage_substrate_still_holds(self):
        write_recipe(self.packages, "extra", "mirrored", "1.0", 3)
        r = derive_excludes(self.packages, self.output)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            names_in(self.output),
            [ARC + "mirrored-1.0-3.igos.tar.gz", ARC + "mirrored-1.0.igos.tar.gz"],
            "the release-carrying name first, then the release-less one, "
            "and nothing else")

    def test_a_package_that_ships_on_the_iso_is_never_listed(self):
        write_recipe(self.packages, "extra", "mirrored", "1.0", 3)
        write_recipe(self.packages, "core", "ships", "2.0", 1)
        derive_excludes(self.packages, self.output)
        self.assertFalse([n for n in names_in(self.output) if "/ships-" in n])

    def test_the_names_of_one_package_follow_one_package_line(self):
        write_recipe(self.packages, "extra", "alpha", "1.0", 2)
        write_recipe(self.packages, "compute", "beta", "3.1", 7)
        derive_excludes(self.packages, self.output)
        blocks = mc.read_exclude_blocks(self.output)
        self.assertEqual(blocks, [
            ("alpha 1.0 2", ["alpha-1.0-2.igos.tar.gz", "alpha-1.0.igos.tar.gz"]),
            ("beta 3.1 7", ["beta-3.1-7.igos.tar.gz", "beta-3.1.igos.tar.gz"]),
        ])
        # The flat reading every other consumer uses sees the same names.
        self.assertEqual(
            mc.read_excludes(self.output),
            {"alpha-1.0-2.igos.tar.gz", "alpha-1.0.igos.tar.gz",
             "beta-3.1-7.igos.tar.gz", "beta-3.1.igos.tar.gz"})

    def test_a_ships_as_package_is_named_for_its_ship_name_and_its_release(self):
        write_recipe(self.packages, "extra", "tool-core", "4.2", 5,
                     extra="ships_as: tool\n")
        derive_excludes(self.packages, self.output)
        self.assertEqual(
            names_in(self.output),
            [ARC + "tool-4.2-5.igos.tar.gz", ARC + "tool-4.2.igos.tar.gz"])

    def test_the_summary_counts_packages_and_names_separately(self):
        write_recipe(self.packages, "extra", "alpha", "1.0", 2)
        write_recipe(self.packages, "extra", "beta", "3.1", 7)
        r = derive_excludes(self.packages, self.output)
        self.assertIn("MIRROR packages:  2", r.stderr)
        self.assertIn("Archive names:    4", r.stderr)


class TheRealTree(unittest.TestCase):
    """The measurement that found the defect, kept as a test."""

    def test_every_mirror_package_is_listed_under_the_name_its_producer_writes(self):
        parser = importlib.import_module("igos-build.parser")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "excludes.txt"
            r = derive_excludes(REPO_ROOT / "packages", output)
            self.assertEqual(r.returncode, 0, r.stderr)
            listed = set(names_in(output))
        templates = [parser.parse_template(y)
                     for y in sorted((REPO_ROOT / "packages").glob("*/*/package.yml"))]
        declarers = {t.ships_as: t for t in templates if t.ships_as}
        expected, missing = 0, []
        for t in templates:
            if t.iso_include:
                continue
            twin_of = declarers.get(t.name)
            if twin_of is not None and twin_of is not t:
                continue
            ship = t.ships_as or t.name
            expected += 1
            produced = ARC + archive_filename(ship, t.version, t.release)
            if produced not in listed:
                missing.append(produced)
        self.assertGreater(expected, 0, "the real tree has mirror-only packages")
        self.assertEqual(missing, [], f"{len(missing)} of {expected} mirror-only "
                         f"packages are listed under a name no producer writes")


class TheIsoManifestReportsAPackageOnce(unittest.TestCase):
    """derive-iso-archive-manifest.py names a mirror package with no built
    archive; with two names per package it must judge the package, not each
    name, or every built package is reported as unbuilt too."""

    HEADER = ("# InterGenOS archive integrity manifest\n"
              "# Manifest-version: 1\n")

    def run_derive(self, entries: list[str], excludes_text: str):
        with tempfile.TemporaryDirectory() as tmp:
            full = Path(tmp) / "full.txt"
            full.write_text(self.HEADER + "".join(
                f"SHA256 ({e}) = {'a' * 64}\n" for e in entries)
                + "# End of manifest.\n")
            ex = Path(tmp) / "excludes.txt"
            ex.write_text(excludes_text)
            out = Path(tmp) / "iso.txt"
            p = subprocess.run(
                [sys.executable, str(DERIVE_ISO_MANIFEST),
                 "--full-manifest", str(full), "--archive-excludes", str(ex),
                 "--output", str(out)],
                capture_output=True, text=True)
            return p, (out.read_text() if out.exists() else "")

    def excludes_for(self, packages: Path) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "excludes.txt"
            r = derive_excludes(packages, output)
            self.assertEqual(r.returncode, 0, r.stderr)
            return output.read_text()

    def test_built_under_either_name_is_excluded_and_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = Path(tmp) / "packages"
            write_recipe(packages, "extra", "newname", "1.0", 3)
            write_recipe(packages, "extra", "oldname", "2.0", 4)
            text = self.excludes_for(packages)
        p, iso = self.run_derive(
            ["keep-5.0-1.igos.tar.gz",
             "newname-1.0-3.igos.tar.gz",      # rebuilt since the change
             "oldname-2.0.igos.tar.gz"],       # banked before it
            text)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("keep-5.0-1.igos.tar.gz", iso)
        self.assertNotIn("newname-1.0-3", iso)
        self.assertNotIn("oldname-2.0", iso)
        self.assertIn("excluded 2", p.stderr)
        self.assertNotIn("note:", p.stderr)

    def test_a_package_built_under_neither_name_is_reported_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = Path(tmp) / "packages"
            write_recipe(packages, "extra", "neverbuilt", "9.9", 2)
            write_recipe(packages, "extra", "built", "1.0", 1)
            text = self.excludes_for(packages)
        p, _ = self.run_derive(
            ["keep-5.0-1.igos.tar.gz", "built-1.0-1.igos.tar.gz"], text)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("note: 1 declared mirror package(s)", p.stderr)
        listed = [line.strip() for line in p.stderr.splitlines()
                  if line.startswith("    ")]
        self.assertEqual(listed, ["neverbuilt 9.9 2"])


if __name__ == "__main__":
    unittest.main()
