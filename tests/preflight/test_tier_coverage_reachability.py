"""Tier coverage scans the toolchain tier and verifies special-case wiring (review finding H5).

The toolchain tier was SKIPPED wholesale (an unwired toolchain package was
invisible), and 'linux-kernel' was trusted reachable BY NAME without ever
checking that chroot-build-ch10.sh still references it.
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
        "pf_tier_cov_reach_test",
        REPO_ROOT / "scripts" / "preflight-tier-coverage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pkg(root: Path, tier: str, name: str):
    d = root / "packages" / tier / name
    d.mkdir(parents=True)
    (d / "package.yml").write_text(f"name: {name}\ntier: {tier}\n")


class TestToolchainReachability(unittest.TestCase):
    def _run(self, tmp: Path, inline_text: str):
        mod = _load()
        mod.PACKAGES_DIR = tmp / "packages"
        inline = tmp / "toolchain-build.sh"
        inline.write_text(inline_text)
        mod.TOOLCHAIN_INLINE_SCRIPTS = [inline]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
        return rc, out.getvalue() + err.getvalue()

    def test_named_toolchain_package_reachable(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _pkg(tmp, "toolchain", "binutils-pass1")
            rc, txt = self._run(tmp, "# build binutils pass 1\ntar xf binutils-2.4.tar.xz\n")
            self.assertEqual(rc, 0, txt)

    def test_tmp_suffix_matches_base_name(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _pkg(tmp, "toolchain", "bash-tmp")
            rc, txt = self._run(tmp, "cd bash-5.3 && ./configure\n")
            self.assertEqual(rc, 0, txt)

    def test_unwired_toolchain_package_is_orphan(self):
        # The class the wholesale skip hid: a tier:toolchain package no
        # inline script ever builds.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _pkg(tmp, "toolchain", "ghostpkg")
            rc, txt = self._run(tmp, "echo nothing relevant\n")
            self.assertEqual(rc, 1)
            self.assertIn("ghostpkg", txt)

    def test_missing_inline_scripts_orphan_everything(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _pkg(tmp, "toolchain", "binutils-pass1")
            mod = _load()
            mod.PACKAGES_DIR = tmp / "packages"
            mod.TOOLCHAIN_INLINE_SCRIPTS = [tmp / "absent.sh"]
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = mod.main()
            self.assertEqual(rc, 1)


class TestSpecialCaseWiringVerified(unittest.TestCase):
    def _run(self, tmp: Path, ch10_text: str | None):
        mod = _load()
        mod.PACKAGES_DIR = tmp / "packages"
        core_script = tmp / "chroot-build-ch8.sh"
        core_script.write_text('run_package "gcc" "gcc" "1"\n')
        mod.HARDCODED_LIST_SCRIPTS = {"core": [core_script]}
        ch10 = tmp / "chroot-build-ch10.sh"
        if ch10_text is not None:
            ch10.write_text(ch10_text)
        mod.SPECIAL_CASE_PACKAGES = {
            "linux-kernel": ("phase_kernel via chroot-build-ch10.sh",
                             ch10, "linux-kernel")}
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
        return rc, out.getvalue() + err.getvalue()

    def _tree(self, tmp: Path):
        _pkg(tmp, "core", "gcc")
        _pkg(tmp, "core", "linux-kernel")

    def test_wired_special_case_passes(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._tree(tmp)
            rc, txt = self._run(tmp, "build_ch10_package linux-kernel\n")
            self.assertEqual(rc, 0, txt)

    def test_script_missing_reference_is_orphan(self):
        # The blind-trust hole: the name stays in SPECIAL_CASE_PACKAGES but
        # the phase script stopped referencing it.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._tree(tmp)
            rc, txt = self._run(tmp, "echo kernel build removed\n")
            self.assertEqual(rc, 1)
            self.assertIn("no longer references", txt)

    def test_absent_script_is_orphan(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._tree(tmp)
            rc, txt = self._run(tmp, None)
            self.assertEqual(rc, 1)
            self.assertIn("does not exist", txt)


if __name__ == "__main__":
    unittest.main()
