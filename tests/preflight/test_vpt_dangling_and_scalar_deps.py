"""Tier validation surfaces dangling dep names and rejects scalar deps (review finding M2).

A declared dep matching NO package in the tree was silently skipped
(natural.get falsy -> no finding), and a scalar string under
dependencies.build list()-coerced into a character list. Both now fail
named: DANGLING-DEP verdict rows, and scalar deps become a
MALFORMED-MANIFEST row via the fail-closed loader.
"""

import importlib.util
import io
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "vpt_m2_test", REPO_ROOT / "scripts" / "validate-package-tiers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pkg(root: Path, tier: str, name: str, deps_build=None, deps_yaml=None):
    d = root / tier / name
    d.mkdir(parents=True)
    if deps_yaml is None:
        deps_yaml = ""
        if deps_build:
            deps_yaml = ("dependencies:\n  build:\n"
                         + "".join(f"    - {x}\n" for x in deps_build))
    (d / "package.yml").write_text(
        f"name: {name}\nversion: '1.0'\ntier: {tier}\n" + deps_yaml)


class TestDanglingAndScalarDeps(unittest.TestCase):
    def _run(self, packages_dir: Path, extra_args=()):
        mod = _load()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main(["validate-package-tiers.py",
                           "--packages-dir", str(packages_dir), *extra_args])
        return rc, out.getvalue() + err.getvalue()

    def test_dangling_dep_fails_named(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Real corpus names so classify() resolves both cleanly.
            _pkg(root, "core", "zlib")
            _pkg(root, "core", "openssl", deps_build=["zlib", "no-such-pkg"])
            rc, txt = self._run(root)
            self.assertEqual(rc, 1, txt)
            self.assertIn("DANGLING-DEP", txt)
            self.assertIn("no-such-pkg", txt)

    def test_resolved_deps_stay_clean(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _pkg(root, "core", "zlib")
            _pkg(root, "core", "openssl", deps_build=["zlib"])
            rc, txt = self._run(root)
            self.assertIn("DANGLING-DEP=0", txt)

    def test_scalar_dep_is_malformed_not_char_list(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _pkg(root, "core", "zlib")
            _pkg(root, "core", "openssl",
                 deps_yaml="dependencies:\n  build: zlib\n")
            rc, txt = self._run(root)
            self.assertEqual(rc, 1, txt)
            self.assertIn("MALFORMED-MANIFEST", txt)
            self.assertIn("list of strings", txt)
            # The character-list symptom must be gone: no dangling single
            # letters from list("zlib").
            self.assertNotIn("'z'", txt)


if __name__ == "__main__":
    unittest.main()
