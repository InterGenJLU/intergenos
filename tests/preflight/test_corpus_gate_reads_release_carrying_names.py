# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The publish corpus gate reads a package name the way the producers write it.

scripts/check-corpus-correspondence.py (called by publish-repo.sh before the
index is generated and signed) exempts the never-published intermediates --
names ending -pass<N>, -tmp or -bootstrap -- and demands byte correspondence
for everything else. It read the package name out of a filename as
"everything before the last hyphen-and-digits run". Since 2026-09-22 an
archive is named <name>-<version>-<release>.igos.tar.gz, so that reading kept
the version: gcc-pass1-15.2.0-1.igos.tar.gz read as gcc-pass1-15.2.0, no
intermediate, and the gate failed the publish on an archive that is never
published (measured: rc 2, "MISSING from staging").

The gate now reads the name with pkm/archive_names.py against the versions
the recipes state. Which archives are intermediates is still decided by the
name pattern alone. Every case runs the gate as a program against real files.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check-corpus-correspondence.py"

sys.path.insert(0, str(REPO_ROOT))
from pkm.archive_names import archive_filename  # noqa: E402

INTERMEDIATE_NAME = re.compile(r"^.+?-(?:pass\d+|tmp|bootstrap)$")
EXCLUDED_LINE = "excluded (never-publish intermediate)"


def write_recipe(packages: Path, tier: str, name: str, version: str,
                 release: int, ships_as: str = "") -> None:
    d = packages / tier / name
    d.mkdir(parents=True)
    body = {"name": name, "version": version, "release": release, "tier": tier}
    if ships_as:
        body["ships_as"] = ships_as
    (d / "package.yml").write_text(yaml.safe_dump(body))


class CorpusGateReadsBothShapes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.packages = root / "packages"
        self.staging = root / "staging"
        self.staging.mkdir()
        self.manifest = root / "chroot-archives.sha256"
        self.built: dict[str, str] = {}
        write_recipe(self.packages, "toolchain", "gcc-pass1", "15.2.0", 1)
        write_recipe(self.packages, "toolchain", "glibc-tmp", "2.43", 1)
        write_recipe(self.packages, "core", "dbus-pass2", "1.16.2", 1)
        write_recipe(self.packages, "core", "glib2-bootstrap", "2.88.1", 1)
        write_recipe(self.packages, "core", "demo", "1.0", 7)
        write_recipe(self.packages, "base", "dialog", "1.3-20260107", 2)

    def built_archive(self, name: str, stage: bool) -> None:
        data = f"bytes of {name}".encode()
        self.built[name] = sha256(data).hexdigest()
        if stage:
            (self.staging / name).write_bytes(data)

    def run_gate(self, packages: Path | None = None) -> subprocess.CompletedProcess:
        self.manifest.write_text(
            "".join(f"{d}  {n}\n" for n, d in sorted(self.built.items())))
        return subprocess.run(
            [sys.executable, str(GATE), "--staging", str(self.staging),
             "--chroot-manifest", str(self.manifest),
             "--packages", str(packages or self.packages)],
            capture_output=True, text=True)

    def test_release_carrying_intermediates_are_never_publish(self):
        for name in ("gcc-pass1-15.2.0-1.igos.tar.gz",
                     "glibc-tmp-2.43-1.igos.tar.gz",
                     "dbus-pass2-1.16.2-1.igos.tar.gz",
                     "glib2-bootstrap-2.88.1-1.igos.tar.gz"):
            self.built_archive(name, stage=False)
        self.built_archive("demo-1.0-7.igos.tar.gz", stage=True)
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count(EXCLUDED_LINE), 4)
        self.assertIn("(1 publishable, 4 intermediates excluded)", r.stdout)

    def test_both_shapes_of_one_corpus_read_the_same(self):
        # A lineage substrate: some archives banked before the change, some
        # rebuilt since.
        self.built_archive("gcc-pass1-15.2.0.igos.tar.gz", stage=False)
        self.built_archive("glibc-tmp-2.43-1.igos.tar.gz", stage=False)
        self.built_archive("demo-1.0-7.igos.tar.gz", stage=True)
        self.built_archive("dialog-1.3-20260107.igos.tar.gz", stage=True)
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("(2 publishable, 2 intermediates excluded)", r.stdout)

    def test_a_version_whose_tail_looks_like_a_release_stays_publishable(self):
        self.built_archive("dialog-1.3-20260107-2.igos.tar.gz", stage=False)
        r = self.run_gate()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("MISSING from staging (built, publishable, unstaged): "
                      "dialog-1.3-20260107-2.igos.tar.gz", r.stdout)

    def test_a_publishable_release_carrying_archive_must_still_correspond(self):
        self.built_archive("demo-1.0-7.igos.tar.gz", stage=False)
        (self.staging / "demo-1.0-7.igos.tar.gz").write_bytes(b"other bytes")
        r = self.run_gate()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BYTES DIFFER", r.stdout)

    def test_a_staged_release_carrying_intermediate_is_refused(self):
        self.built_archive("demo-1.0-7.igos.tar.gz", stage=True)
        self.built_archive("gcc-pass1-15.2.0-1.igos.tar.gz", stage=True)
        r = self.run_gate()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("STAGED INTERMEDIATE (never-publish archive in staging): "
                      "gcc-pass1-15.2.0-1.igos.tar.gz", r.stdout)

    def test_a_missing_recipe_tree_is_refused_not_guessed_around(self):
        self.built_archive("demo-1.0-7.igos.tar.gz", stage=True)
        r = self.run_gate(packages=self.packages.parent / "no-such-tree")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("packages dir not found", r.stderr)


class TheRealTree(unittest.TestCase):
    """Every intermediate the real recipes describe, named as its producer
    names it, is exempt; the measurement that found the defect."""

    def test_every_real_intermediate_under_its_produced_name_is_excluded(self):
        produced = []
        for yml in sorted((REPO_ROOT / "packages").glob("*/*/package.yml")):
            d = yaml.safe_load(yml.read_text()) or {}
            ship = d.get("ships_as") or d.get("name")
            if ship and INTERMEDIATE_NAME.match(str(ship)):
                produced.append(archive_filename(
                    str(ship), str(d["version"]), d.get("release")))
        self.assertGreaterEqual(len(produced), 20,
                                "the real tree carries its intermediates")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            staging.mkdir()
            (staging / "zlib-1.3.1-1.igos.tar.gz").write_bytes(b"zlib")
            lines = [f"{sha256(b'zlib').hexdigest()}  zlib-1.3.1-1.igos.tar.gz"]
            lines += [f"{sha256(n.encode()).hexdigest()}  {n}" for n in produced]
            manifest = root / "built.sha256"
            manifest.write_text("\n".join(lines) + "\n")
            r = subprocess.run(
                [sys.executable, str(GATE), "--staging", str(staging),
                 "--chroot-manifest", str(manifest)],
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count(EXCLUDED_LINE), len(produced))


if __name__ == "__main__":
    unittest.main()
