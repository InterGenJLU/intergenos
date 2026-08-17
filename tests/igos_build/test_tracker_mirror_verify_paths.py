#!/usr/bin/env python3
"""Tests for archive-level verify_paths enforcement of mirror-only packages
(PackageTracker._enforce_mirror_archive_verify_paths).

Gate 4.5 (scripts/pre-squashfs-audit.py) checks verify_paths against the chroot
and EXEMPTS iso_include:false packages, so a mirror-only package's verify_paths
were declarations nothing enforced at build time — the gap that let nvidia's
dead symlink parse ship an archive missing /usr/lib/gbm/nvidia-drm_gbm.so
(PI-Z20). This gate fires at the single archive-seal chokepoint shared by BOTH
the DESTDIR (pkg_archive) and the direct_install (pkg_archive_from_files) flows,
so --tracked single-package and full-tier builds are covered identically.

Extended 2026-07-19 (DFB-01/02): iso_include:true packages are enforced at the
archive too — chroot presence (gate 4.5) does not prove archive presence — with
a dot-suffix tolerance so a declared libfoo.so is satisfied by libfoo.so.1.2.3
(the ldconfig-made symlink is chroot-generated, never in the tar).

RED/GREEN: on the pre-fix tracker (no enforcement call) the two *_rejected tests
FAIL — the archive seals True despite the missing verify_path; with the fix they
pass by rejecting.
"""

import importlib
import logging
import sys
import tempfile
import unittest
from pathlib import Path

# Load the tracker from THIS test's own worktree root (<root>/tests/igos_build/
# -> <root>), so the test exercises the tree it ships in — correct both in a
# checked-out worktree and in the build env where the root is /mnt/intergenos.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from .factories import make_dependencies, make_package  # noqa: E402
_tracker_mod = importlib.import_module("igos-build.tracker")
PackageTracker = _tracker_mod.PackageTracker


def _make_tracker(archives_dir):
    t = PackageTracker()
    t.logger = logging.getLogger("test_tracker_mirror_vp")
    t.pkg_archives = Path(archives_dir)
    return t


def _make_pkg(tmp, verify_paths, iso_include=False, name="mirrorpkg", version="1.0"):
    """Write a minimal package.yml declaring verify_paths and return a
    Package-shaped stub pointing template_path at it."""
    yml = Path(tmp) / f"{name}.package.yml"
    lines = [f"name: {name}", f'version: "{version}"']
    if verify_paths is not None:
        lines.append("verify_paths:")
        lines += [f"  - {p}" for p in verify_paths]
    yml.write_text("\n".join(lines) + "\n")
    return make_package(
        name=name, version=version, description="t", license="t",
        tier="extra", iso_include=iso_include,
        dependencies=make_dependencies(),
        template_path=yml,
    )


class TestMirrorArchiveVerifyPaths(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.archives = Path(self.tmp) / "archives"
        self.archives.mkdir()
        self.staging = Path(self.tmp) / "stage"
        self.staging.mkdir()
        self.tracker = _make_tracker(self.archives)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stage(self, rel, content="x"):
        p = self.staging / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    # ---------- DESTDIR flow (pkg_archive) ----------

    def test_pkg_archive_all_present_passes(self):
        self._stage("usr/lib/libfoo.so.1")
        self._stage("usr/share/egl/egl_external_platform.d/15_x.json")
        pkg = _make_pkg(self.tmp, [
            "/usr/lib/libfoo.so.1",
            "/usr/share/egl/egl_external_platform.d/15_x.json",
        ])
        self.assertTrue(self.tracker.pkg_archive(pkg, self.staging))

    def test_pkg_archive_missing_verify_path_rejected(self):
        self._stage("usr/lib/libfoo.so.1")  # present
        pkg = _make_pkg(self.tmp, [
            "/usr/lib/libfoo.so.1",
            "/usr/lib/gbm/nvidia-drm_gbm.so",  # NOT staged -> must reject
        ])
        with self.assertLogs("test_tracker_mirror_vp", level="ERROR") as cm:
            result = self.tracker.pkg_archive(pkg, self.staging)
        self.assertFalse(result)
        self.assertTrue(any("nvidia-drm_gbm.so" in m for m in cm.output))
        self.assertTrue(any("missing" in m.lower() for m in cm.output))

    def test_symlink_with_resolving_chain_passes(self):
        """A verify_path symlink whose link-target chain lands on a real
        member of the same archive is honest payload proof — passes."""
        import os
        self._stage("usr/lib/libnvidia-allocator.so.1")
        (self.staging / "usr/lib/gbm").mkdir(parents=True)
        os.symlink("../libnvidia-allocator.so.1",
                   self.staging / "usr/lib/gbm/nvidia-drm_gbm.so")
        pkg = _make_pkg(self.tmp, ["/usr/lib/gbm/nvidia-drm_gbm.so"])
        self.assertTrue(self.tracker.pkg_archive(pkg, self.staging))

    def test_dangling_symlink_verify_path_rejected(self):
        """Review finding H9: a DANGLING symlink used to satisfy the gate via
        name membership (the PI-Z20 nvidia shape). It must now fail closed."""
        import os
        (self.staging / "usr/lib/gbm").mkdir(parents=True)
        os.symlink("../libnvidia-allocator.so.1",
                   self.staging / "usr/lib/gbm/nvidia-drm_gbm.so")  # dangling
        pkg = _make_pkg(self.tmp, ["/usr/lib/gbm/nvidia-drm_gbm.so"])
        with self.assertLogs("test_tracker_mirror_vp", level="ERROR") as cm:
            result = self.tracker.pkg_archive(pkg, self.staging)
        self.assertFalse(result)
        self.assertTrue(any("dangling" in m for m in cm.output))
        self.assertTrue(any("nvidia-drm_gbm.so" in m for m in cm.output))

    def test_symlink_loop_rejected(self):
        """A verify_path symlink loop can never prove payload — fail closed."""
        import os
        (self.staging / "usr/lib").mkdir(parents=True)
        os.symlink("libb.so", self.staging / "usr/lib/liba.so")
        os.symlink("liba.so", self.staging / "usr/lib/libb.so")
        pkg = _make_pkg(self.tmp, ["/usr/lib/liba.so"])
        with self.assertLogs("test_tracker_mirror_vp", level="ERROR") as cm:
            result = self.tracker.pkg_archive(pkg, self.staging)
        self.assertFalse(result)
        self.assertTrue(any("loop" in m for m in cm.output))

    def test_absolute_target_symlink_chain_passes(self):
        """An absolute-target symlink resolves against the archive root
        (the deploy namespace), not the host filesystem."""
        import os
        self._stage("usr/lib/libreal.so.1")
        (self.staging / "usr/lib/gbm").mkdir(parents=True)
        os.symlink("/usr/lib/libreal.so.1",
                   self.staging / "usr/lib/gbm/linked.so")
        pkg = _make_pkg(self.tmp, ["/usr/lib/gbm/linked.so"])
        self.assertTrue(self.tracker.pkg_archive(pkg, self.staging))

    def test_iso_included_missing_payload_rejected(self):
        """iso_include:true is enforced too (extended 2026-07-19, DFB-01/02):
        gate 4.5 proves chroot presence, not archive presence — a license-only
        archive sealed from an install step that wrote the build environment
        instead of DESTDIR must fail here."""
        self._stage("usr/share/licenses/mirrorpkg/LICENSE")
        pkg = _make_pkg(
            self.tmp,
            ["/usr/lib/python3.14/site-packages/requests/api.py"],
            iso_include=True)
        with self.assertLogs("test_tracker_mirror_vp", level="ERROR") as cm:
            result = self.tracker.pkg_archive(pkg, self.staging)
        self.assertFalse(result)
        self.assertTrue(any("requests/api.py" in m for m in cm.output))

    def test_iso_included_soname_tolerance_passes(self):
        """A declared bare .so satisfied by a dot-suffixed member
        (libfoo.so covered by libfoo.so.1.2.3): the ldconfig-made symlink is
        chroot-generated and never in the tar — the prefix rule keeps that
        legitimate case passing without exempting the whole class."""
        self._stage("usr/lib/libfoo.so.1.2.3")
        pkg = _make_pkg(self.tmp, ["/usr/lib/libfoo.so"], iso_include=True)
        self.assertTrue(self.tracker.pkg_archive(pkg, self.staging))

    def test_mirror_only_gets_no_soname_tolerance(self):
        """The dot-suffix tolerance is iso-included-only; a mirror-only
        archive must still ship exactly what it declares."""
        self._stage("usr/lib/libfoo.so.1.2.3")
        pkg = _make_pkg(self.tmp, ["/usr/lib/libfoo.so"], iso_include=False)
        with self.assertLogs("test_tracker_mirror_vp", level="ERROR") as cm:
            result = self.tracker.pkg_archive(pkg, self.staging)
        self.assertFalse(result)
        self.assertTrue(any("libfoo.so" in m for m in cm.output))

    def test_no_verify_paths_declared_passes(self):
        self._stage("usr/lib/libfoo.so.1")
        pkg = _make_pkg(self.tmp, None)  # mirror-only, no verify_paths
        self.assertTrue(self.tracker.pkg_archive(pkg, self.staging))

    # ---------- direct_install flow (pkg_archive_from_files) ----------

    def test_pkg_archive_from_files_all_present_passes(self):
        f1 = self._stage("usr/lib/libbar.so.1")
        f2 = self._stage("usr/lib/gbm/nvidia-drm_gbm.so")
        # from_files archives at -C / with lstrip('/') members, so declare the
        # verify_paths as the files' real absolute paths (the namespace the
        # direct_install path actually produces).
        pkg = _make_pkg(self.tmp, [str(f1), str(f2)], name="directpkg")
        self.assertTrue(
            self.tracker.pkg_archive_from_files(pkg, [str(f1), str(f2)]))

    def test_pkg_archive_from_files_missing_rejected(self):
        f1 = self._stage("usr/lib/libbar.so.1")
        missing = self.staging / "usr/lib/gbm/nvidia-drm_gbm.so"  # never created
        pkg = _make_pkg(self.tmp, [str(f1), str(missing)], name="directpkg")
        with self.assertLogs("test_tracker_mirror_vp", level="ERROR") as cm:
            result = self.tracker.pkg_archive_from_files(pkg, [str(f1)])
        self.assertFalse(result)
        self.assertTrue(any("nvidia-drm_gbm.so" in m for m in cm.output))


if __name__ == "__main__":
    unittest.main()
