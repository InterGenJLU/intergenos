"""Unknown tier-validation package filter fails closed (review finding M3).

A typo'd positional package filter matched no row in the validation
loop, every counter stayed zero, and the gate exited 0 — a vacuous
pass certifying nothing. An unknown filter name now exits 2 naming
the filter; a valid filter still validates exactly its row.
"""

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "vpt_m3_test", REPO_ROOT / "scripts" / "validate-package-tiers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pkg(root: Path, tier: str, name: str, deps_build=None):
    d = root / tier / name
    d.mkdir(parents=True)
    deps_yaml = ""
    if deps_build:
        deps_yaml = ("dependencies:\n  build:\n"
                     + "".join(f"    - {x}\n" for x in deps_build))
    (d / "package.yml").write_text(
        f"name: {name}\nversion: '1.0'\ntier: {tier}\n" + deps_yaml)


class TestUnknownFilter(unittest.TestCase):
    def _run(self, packages_dir: Path, extra_args=()):
        mod = _load()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main(["validate-package-tiers.py",
                           "--packages-dir", str(packages_dir), *extra_args])
        return rc, out.getvalue() + err.getvalue()

    def test_unknown_filter_exits_2_named(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _pkg(root, "core", "zlib")
            rc, txt = self._run(root, extra_args=("zlibb",))
            self.assertEqual(rc, 2, txt)
            self.assertIn("zlibb", txt)
            self.assertIn("matches no package", txt)

    def test_valid_filter_on_clean_package_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _pkg(root, "core", "zlib")
            # Dangling dep on a package the filter EXCLUDES must not leak
            # into the filtered verdict.
            _pkg(root, "core", "openssl", deps_build=["no-such-pkg"])
            rc, txt = self._run(root, extra_args=("zlib",))
            self.assertEqual(rc, 0, txt)

    def test_valid_filter_still_catches_its_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _pkg(root, "core", "zlib")
            _pkg(root, "core", "openssl", deps_build=["zlib", "no-such-pkg"])
            rc, txt = self._run(root, extra_args=("openssl",))
            self.assertEqual(rc, 1, txt)
            self.assertIn("DANGLING-DEP", txt)
            self.assertIn("no-such-pkg", txt)


if __name__ == "__main__":
    unittest.main()
