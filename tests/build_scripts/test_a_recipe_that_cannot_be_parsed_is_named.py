# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A recipe the build-side readers cannot parse is named, never skipped silently.

``backfill-pkginfo.py``, ``inject-pkginfo.py`` and ``validate-pkm-archive.py``
each read every ``package.yml`` to learn the versions (and, for the first two,
the tiers) the recipes state. A recipe that does not parse is left out of
those maps, and its archives are then read the release-less way. That fallback
is the reading from before archive names carried a release, so a skip changes
what the script concludes without failing it; each reader now says on
standard error which recipe it skipped and why, and goes on with the others.
"""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load(script: str):
    spec = importlib.util.spec_from_file_location(
        script.replace("-", "_").removesuffix(".py"), SCRIPTS / script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ARecipeThatCannotBeParsedIsNamed(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        good = self.repo / "packages" / "core" / "demo"
        good.mkdir(parents=True)
        (good / "package.yml").write_text("name: demo\nversion: '1.0'\nrelease: 3\n")
        broken = self.repo / "packages" / "extra" / "broken"
        broken.mkdir(parents=True)
        self.broken = broken / "package.yml"
        self.broken.write_text("name: broken\nversion: [1.0\n")

    def tearDown(self):
        self._tmp.cleanup()

    def _read(self, loader, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = loader(*args)
        return result, err.getvalue()

    def _assert_named(self, said: str, script: str):
        self.assertIn(str(self.broken), said,
                      f"{script} skipped a recipe it could not parse without naming it")
        self.assertIn("could not be parsed", said)

    def _check(self, script, loader_name, want):
        loader = getattr(_load(script), loader_name)
        result, said = self._read(loader, self.repo)
        self._assert_named(said, script)
        self.assertEqual(result, want, "the recipes that parse are still read")

    def test_backfill_pkginfo_tiers(self):
        self._check("backfill-pkginfo.py", "load_recipe_tiers", {"demo": "core"})

    def test_backfill_pkginfo_versions(self):
        self._check("backfill-pkginfo.py", "load_recipe_versions", {"demo": "1.0"})

    def test_inject_pkginfo_tiers(self):
        self._check("inject-pkginfo.py", "load_recipe_names", {"demo": "core"})

    def test_inject_pkginfo_versions(self):
        self._check("inject-pkginfo.py", "load_recipe_versions", {"demo": "1.0"})

    def test_validate_pkm_archive(self):
        mod = _load("validate-pkm-archive.py")
        mod.PACKAGES_DIR = self.repo / "packages"
        mod._recipe_versions_cache = None
        result, said = self._read(mod.recipe_versions)
        self._assert_named(said, "validate-pkm-archive.py")
        self.assertEqual(result, {"demo": "1.0"}, "the recipes that parse are still read")


if __name__ == "__main__":
    unittest.main()
