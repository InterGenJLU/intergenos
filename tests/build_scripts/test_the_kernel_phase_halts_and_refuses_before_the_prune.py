# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The kernel phase halts on a failure inside its build, and builds only what
its recipe declares, refusing an empty value before it prunes staged kernels.

scripts/chroot-build-ch10.sh runs inside the build chroot as root, prunes every
staged kernel under /usr/lib/modules and /boot that is not the one it is about
to build, then builds it. These cases fire the driver itself, unmodified, inside
a bubblewrap sandbox: a scratch tree stands in at /mnt/intergenos with a
stand-in package library (the real get_package_release), the real
scripts/download-sources.py, a fixture kernel recipe, a stand-in build script
and a stand-in staged-kernel gate; /usr/lib/modules, /boot and /tmp are empty
tmpfs mounts holding two staged kernels; the network is unshared. Nothing of a
real kernel build runs and nothing outside the sandbox is written. Where
bubblewrap is absent, or cannot make a sandbox here, the cases are skipped
with that reason.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The fixture recipe's version and release, declared once here; the archive
# name is whatever the real resolver makes of the fixture's source URL.
VERSION = "4.5.6"
RELEASE = 3
THIS = f"{VERSION}-igos-{RELEASE}"
PREVIOUS = f"{VERSION}-igos-{RELEASE - 1}"
RECIPE = f'''name: linux-kernel
version: "{VERSION}"
release: {RELEASE}
description: Linux kernel
source:
- url: https://example.invalid/linux-${{version}}.tar.xz
  sha256: "{"0" * 64}"
'''
MARKER = "staged-before-the-run"

LIBRARY = r'''# Stand-in package library: every function the driver calls, none touching
# anything outside the sandbox.
igos_timestamp() { date '+%H:%M:%S'; }
pkg_init() { :; }
source_profile_d() { :; }
extract_source() { echo "EXTRACT $1"; }
pkg_run_phase() { PKG_PHASE_RC=0; "$1" >> "$2" 2>&1 || PKG_PHASE_RC=$?; }
pkg_install() {
    mkdir -p "/usr/lib/modules/$2-igos-$4"
    : > "/boot/vmlinuz-$2-igos-$4"
}
pkg_hook_baseline() {
    if [ -e /case/fail-inside-the-body ]; then
        echo "FAILING INSIDE THE BODY"
        return 1
    fi
}
pkg_record_hook_changes() { :; }
pkg_trace_finish() { :; }
'''
BUILD = '''configure() { :; }
build() { :; }
post_install() { :; }
'''
GATE = '''#!/usr/bin/bash
# Stand-in staged-kernel gate: exactly one module tree, named as --expect says.
n=0
for d in /usr/lib/modules/*/; do [ -d "$d" ] && n=$((n + 1)) && last=$(basename "$d"); done
[ "$1" = --expect ] && [ "$n" -eq 1 ] && [ "$last" = "$2" ]
'''
INSIDE = f'''#!/usr/bin/bash
for t in {THIS} {PREVIOUS}; do
    mkdir -p "/usr/lib/modules/$t"
    : > "/usr/lib/modules/$t/{MARKER}"
    : > "/boot/vmlinuz-$t"
done
/usr/bin/bash /mnt/intergenos/scripts/chroot-build-ch10.sh
echo "DRIVER EXIT $?"
for m in /usr/lib/modules/*/{MARKER}; do
    [ -e "$m" ] && echo "KEPT $(basename "$(dirname "$m")")"
done
'''


def source(path):
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:{path}"],
            check=True, capture_output=True, text=True,
        ).stdout
    return (ROOT / path).read_text()


def function(text, name):
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", text, re.M | re.S)
    if not match:
        raise AssertionError(name)
    return match.group()


def host_paths():
    """bwrap arguments that carry the host's /usr layout into the sandbox."""
    args = ["--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc"]
    for path in ("/bin", "/sbin", "/lib", "/lib64"):
        if os.path.islink(path):
            args += ["--symlink", os.readlink(path), path]
        elif os.path.isdir(path):
            args += ["--ro-bind", path, path]
    return args


class TheKernelPhase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bwrap = shutil.which("bwrap")
        if cls.bwrap is None:
            raise unittest.SkipTest(
                "bubblewrap is not installed: the kernel phase driver is only "
                "fired inside a sandbox, never against this machine's kernels")
        probe = subprocess.run(
            [cls.bwrap, "--die-with-parent", "--unshare-all"] + host_paths()
            + ["/usr/bin/true"], capture_output=True, text=True, timeout=30)
        if probe.returncode != 0:
            raise unittest.SkipTest(
                f"bubblewrap cannot make a sandbox here (exit {probe.returncode}): "
                f"{probe.stderr.strip()}")

    def fire(self, recipe=RECIPE, resolver=True, fail_inside=False):
        driver = source("scripts/chroot-build-ch10.sh")
        resolver_text = source("scripts/download-sources.py")
        reader = function(source("scripts/pkg-functions.sh"), "get_package_release")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "tree"
            scripts = tree / "scripts"
            kernel = tree / "packages/core/linux-kernel"
            scripts.mkdir(parents=True)
            kernel.mkdir(parents=True)
            (tree / "build/logs").mkdir(parents=True)
            (scripts / "chroot-build-ch10.sh").write_text(driver)
            (scripts / "pkg-functions.sh").write_text(LIBRARY + reader + "\n")
            (scripts / "preflight-single-kernel.sh").write_text(GATE)
            if resolver:
                (scripts / "download-sources.py").write_text(resolver_text)
            (kernel / "build.sh").write_text(BUILD)
            if recipe is not None:
                (kernel / "package.yml").write_text(recipe)
            case = root / "case"
            case.mkdir()
            (case / "inside.sh").write_text(INSIDE)
            if fail_inside:
                (case / "fail-inside-the-body").write_text("")
            result = subprocess.run(
                [self.bwrap, "--die-with-parent", "--unshare-all", "--clearenv",
                 "--setenv", "PATH", "/usr/bin:/usr/sbin", "--setenv", "HOME", "/tmp"]
                + host_paths()
                + ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                   "--tmpfs", "/usr/lib/modules", "--tmpfs", "/boot",
                   "--bind", str(tree), "/mnt/intergenos",
                   "--ro-bind", str(case), "/case",
                   "/usr/bin/bash", "/case/inside.sh"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
            out = result.stdout + result.stderr
            exit_line = re.search(r"^DRIVER EXIT (\d+)$", result.stdout, re.M)
            self.assertIsNotNone(exit_line, out)
            return {
                "out": out,
                "exit": int(exit_line.group(1)),
                "kept": sorted(re.findall(r"^KEPT (\S+)$", result.stdout, re.M)),
                "pruned": re.findall(r"PRUNE: superseded staged kernel (\S+)", out),
                "built": re.findall(r">>> Chapter 10: linux-kernel (\S*)", out),
                "extracted": re.findall(r"^EXTRACT (\S*)$", result.stdout, re.M),
            }

    def archive(self):
        """The archive name the real resolver gives for the fixture recipe."""
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "download_sources.py"
            path.write_text(source("scripts/download-sources.py"))
            spec = importlib.util.spec_from_file_location("igos_download_sources", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.get_source_info(yaml.safe_load(RECIPE))[0]["filename"]

    def assert_refused_before_the_prune(self, run, name):
        self.assertNotEqual(run["exit"], 0, run["out"])
        self.assertRegex(run["out"], rf"error: {name} is empty: .* refusing before "
                                     r"the staged-kernel prune")
        self.assertEqual(run["pruned"], [], run["out"])
        self.assertEqual(run["built"], [], run["out"])
        self.assertEqual(run["kept"], sorted([THIS, PREVIOUS]), run["out"])

    def test_the_phase_builds_the_recipes_version_from_the_archive_its_resolver_names(self):
        run = self.fire()
        self.assertEqual(run["exit"], 0, run["out"])
        self.assertEqual(run["built"], [VERSION], run["out"])
        self.assertEqual(run["extracted"], [self.archive()], run["out"])
        self.assertEqual(run["pruned"], [PREVIOUS], run["out"])
        self.assertEqual(run["kept"], [THIS], run["out"])
        self.assertIn("SUCCESS: linux-kernel", run["out"])

    def test_a_failure_inside_the_kernel_build_halts_the_phase(self):
        run = self.fire(fail_inside=True)
        self.assertIn("FAILING INSIDE THE BODY", run["out"])
        self.assertNotEqual(run["exit"], 0, run["out"])
        self.assertNotIn("SUCCESS: linux-kernel", run["out"])
        self.assertNotIn(">>> Chapter 10 build complete", run["out"])

    def test_an_absent_recipe_is_refused_before_the_prune(self):
        self.assert_refused_before_the_prune(self.fire(recipe=None), "KVER")

    def test_an_empty_version_is_refused_before_the_prune(self):
        recipe = RECIPE.replace(f'version: "{VERSION}"', 'version: ""')
        self.assert_refused_before_the_prune(self.fire(recipe=recipe), "KVER")

    def test_an_absent_release_is_refused_before_the_prune(self):
        recipe = RECIPE.replace(f"release: {RELEASE}\n", "")
        self.assert_refused_before_the_prune(self.fire(recipe=recipe), "KREL")

    def test_an_archive_the_resolver_cannot_name_is_refused_before_the_prune(self):
        self.assert_refused_before_the_prune(self.fire(resolver=False), "KTARBALL")


if __name__ == "__main__":
    unittest.main()
