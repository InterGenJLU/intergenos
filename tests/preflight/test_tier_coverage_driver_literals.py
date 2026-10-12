"""Tier coverage holds every core/base `run_package` literal to its recipe.

The core and base drivers write `run_package "<dir>" "<name>" "<version>"
"<tarball>"` by hand and verify the tarball against the RECIPE's pinned
sha256. A recipe bump that left the driver's line behind (gzip 1.14 in the
driver, 1.15 in the recipe, 2026-10-12) passed every pre-build gate and
halted the build at that package with a checksum mismatch. The gate now
compares the literal version with the recipe's and the literal tarball with
the filenames the recipe's source entries stage to, resolved exactly as
scripts/download-sources.py resolves them.
"""

import importlib.util
import io
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "pf_tier_cov_literals_test",
        REPO_ROOT / "scripts" / "preflight-tier-coverage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _recipe(root: Path, tier: str, dirname: str, body: str):
    d = root / "packages" / tier / dirname
    d.mkdir(parents=True)
    (d / "package.yml").write_text(textwrap.dedent(body))


def _run(tmp: Path, core_script_text: str):
    mod = _load()
    mod.PACKAGES_DIR = tmp / "packages"
    core_script = tmp / "chroot-build-ch8.sh"
    core_script.write_text(textwrap.dedent(core_script_text))
    mod.HARDCODED_LIST_SCRIPTS = {"core": [core_script]}
    mod.SPECIAL_CASE_PACKAGES = {}
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = mod.main()
    return rc, out.getvalue() + err.getvalue()


GZIP_RECIPE = """\
    name: gzip-core
    ships_as: gzip
    version: '1.15'
    tier: core
    source:
    - url: https://ftpmirror.gnu.org/gzip/gzip-${version}.tar.xz
      sha256: 9aa0cc780dec156b8282844833b342ab7cb08c25d2cd9a1869cdd0df31deff48
    """


class TestDriverLiteralsMatchRecipe(unittest.TestCase):
    def test_matching_version_and_tarball_pass(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _recipe(tmp, "core", "gzip-core", GZIP_RECIPE)
            rc, txt = _run(tmp, '''\
                run_package "gzip-core" "gzip" "1.15" \\
                    "gzip-1.15.tar.xz" \\
                    "GNU compression utility"
                ''')
            self.assertEqual(rc, 0, txt)
            self.assertIn("1 checked, 0 mismatched", txt)

    def test_version_behind_the_recipe_fails_naming_both(self):
        # The 2026-10-12 halt: the recipe moved to 1.15, the driver still
        # extracted 1.14 and verified it against 1.15's sha256.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _recipe(tmp, "core", "gzip-core", GZIP_RECIPE)
            rc, txt = _run(tmp, '''\
                run_package "gzip-core" "gzip" "1.14" \\
                    "gzip-1.14.tar.xz" \\
                    "GNU compression utility"
                ''')
            self.assertEqual(rc, 1)
            self.assertIn("do not match their recipe", txt)
            self.assertIn("chroot-build-ch8.sh:1", txt)
            self.assertIn("builds version 1.14 but the recipe pins 1.15", txt)
            self.assertIn("gzip", txt)

    def test_tarball_the_recipe_does_not_stage_fails(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _recipe(tmp, "core", "gzip-core", GZIP_RECIPE)
            rc, txt = _run(tmp, '''\
                run_package "gzip-core" "gzip" "1.15" \\
                    "gzip-1.15.tar.gz" \\
                    "GNU compression utility"
                ''')
            self.assertEqual(rc, 1)
            self.assertIn("extracts gzip-1.15.tar.gz but the recipe's source entries stage gzip-1.15.tar.xz", txt)

    def test_filename_override_resolves_like_the_sweep(self):
        # The atop/squashfs-tools shape: a GitHub tag archive staged under a
        # descriptive local name through the recipe's `filename:` field.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _recipe(tmp, "core", "atop", """\
                name: atop
                version: '2.12.1'
                tier: core
                source:
                  - url: https://github.com/Atoptool/atop/archive/refs/tags/v${version}.tar.gz
                    sha256: 0f49f3aa5e3449f8c1cf10ac08036e2b67887640fe7980b8bc6ca9fd84d46fdf
                    filename: ${name}-${version}.tar.gz
                """)
            rc, txt = _run(tmp, '''\
                run_package "atop" "atop" "2.12.1" \\
                    "atop-2.12.1.tar.gz" \\
                    "Advanced system and process monitor"
                ''')
            self.assertEqual(rc, 0, txt)

    def test_single_line_call_and_name_template_pass(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _recipe(tmp, "core", "bash", """\
                name: bash
                version: '5.3'
                tier: core
                source:
                - url: https://ftp.gnu.org/gnu/${name}/${name}-${version}.tar.gz
                  sha256: 0d5cd86965f869a26cf64f4b71be7b96f90a3ba8b3d74e27e8e9d9d5550f31ba
                """)
            rc, txt = _run(tmp, 'run_package "bash" "bash" "5.3" "bash-5.3.tar.gz" "GNU Bourne-Again SHell"\n')
            self.assertEqual(rc, 0, txt)

    def test_recipe_without_source_entries_is_checked_on_version_only(self):
        # A first-party package whose tarball the build generates: no source
        # entry to hold the tarball to, so only the version is compared.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _recipe(tmp, "core", "intergenos-base-files", """\
                name: intergenos-base-files
                version: '1.0.0'
                tier: core
                """)
            rc, txt = _run(tmp, '''\
                run_package "intergenos-base-files" "intergenos-base-files" "1.0.0" \\
                    "intergenos-base-files-1.0.0.tar.xz" \\
                    "Base files"
                ''')
            self.assertEqual(rc, 0, txt)
            rc, txt = _run(tmp, '''\
                run_package "intergenos-base-files" "intergenos-base-files" "1.0.1" \\
                    "intergenos-base-files-1.0.1.tar.xz" \\
                    "Base files"
                ''')
            self.assertEqual(rc, 1)
            self.assertIn("builds version 1.0.1 but the recipe pins 1.0.0", txt)

    def test_call_naming_an_absent_recipe_directory_fails(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _recipe(tmp, "core", "gzip-core", GZIP_RECIPE)
            rc, txt = _run(tmp, '''\
                run_package "gzip-core" "gzip" "1.15" \\
                    "gzip-1.15.tar.xz" \\
                    "GNU compression utility"
                run_package "gzip" "gzip" "1.15" \\
                    "gzip-1.15.tar.xz" \\
                    "a directory no recipe lives in"
                ''')
            self.assertEqual(rc, 1)
            self.assertIn("names recipe directory 'gzip' but", txt)
            self.assertIn("does not exist", txt)

    def test_description_on_the_next_line_is_not_read_as_a_tarball(self):
        # A three-literal call followed by its description: the description
        # is not the tarball. For a source-less recipe that is a pass on the
        # version; for a recipe that pins a source it is a mismatch, because
        # the driver would extract nothing.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _recipe(tmp, "core", "pkm", """\
                name: pkm
                version: '0.2.0'
                tier: core
                """)
            rc, txt = _run(tmp, '''\
                run_package "pkm" "pkm" "0.2.0"
                run_package "pkm" "pkm" "0.2.0" \\
                    "" \\
                    "InterGenOS package manager"
                ''')
            self.assertEqual(rc, 0, txt)
            self.assertIn("2 checked, 0 mismatched", txt)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _recipe(tmp, "core", "gzip-core", GZIP_RECIPE)
            rc, txt = _run(tmp, '''\
                run_package "gzip-core" "gzip" "1.15" \\
                    "" \\
                    "GNU compression utility"
                ''')
            self.assertEqual(rc, 1)
            self.assertIn("extracts no tarball but the recipe's source entries stage gzip-1.15.tar.xz", txt)

    def test_every_real_driver_literal_is_held(self):
        # The real drivers: every run_package call is parsed (none skipped),
        # proven by the count against a plain grep of the call lines.
        import re
        mod = _load()
        scripts = mod.HARDCODED_LIST_SCRIPTS["core"] + mod.HARDCODED_LIST_SCRIPTS["base"]
        parsed = mod.collect_run_package_literals(scripts)
        plain = 0
        for sp in scripts:
            plain += len(re.findall(r'^\s*run_package\s+"', sp.read_text(errors="replace"), re.M))
        self.assertEqual(len(parsed), plain)
        import yaml
        for sp, ln, pkg_dir, name, version, tarball in parsed:
            self.assertIsNotNone(version, f"{sp.name}:{ln} has no version literal")
            if tarball is None:
                # an empty tarball literal is only ever a package with no
                # source entry — the build generates it in place
                tier = "base" if sp.name == "chroot-build-base.sh" else "core"
                data = yaml.safe_load((mod.PACKAGES_DIR / tier / pkg_dir / "package.yml").read_text())
                self.assertFalse(data.get("source"),
                                 f"{sp.name}:{ln} extracts no tarball but {pkg_dir} declares a source")


if __name__ == "__main__":
    unittest.main()
