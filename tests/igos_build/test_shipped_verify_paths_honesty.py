#!/usr/bin/env python3
"""Shipped-set verify_paths honesty — the other half of the archive-seal work.

BACKGROUND. Rule 20 says every package declares verify_paths that live in ITS OWN
payload. Two gates split the space:
  * Gate 4.5 (scripts/pre-squashfs-audit.py) checks declared verify_paths against
    the ISO CHROOT, and it covers iso_include:true (shipped) packages.
  * The archive-seal gate (igos-build/tracker.py
    PackageTracker._enforce_mirror_archive_verify_paths) checks declared
    verify_paths against the package's OWN sealed .igos.tar.gz, and it runs ONLY
    for mirror-only (iso_include:false) packages (tracker.py:216-283).

THE SHIPPED-SET GAP. The June-2 repoint (ecb8028d) pointed ~99 hooks-class
packages' verify_paths at their FORGE-shipped installer-hooks path
(/usr/share/intergenos/installer-hooks/<tier>/<name>). For a SHIPPED package that
path is verified by gate 4.5 against the chroot — where forge's hooks tree exists
— so the declaration verifies FORGE's file, not the package's own payload: a
VACUOUS pass, Rule 20 defeated. (The mirror-only half of the same repoint is the
burn-halting set handled on a separate companion branch; this file is the shipped
half.)

THIS CHANGE. The 13 effective-iso_include=true hooks-class packages are repointed
to 2-3 load-bearing files from their OWN archives. TestShippedForgePathRegression
pins that repoint. TestMirrorSealForgePath exercises the REAL seal gate for the
forge-path class on a mirror-only package. TestOwnershipLintProposal is a
reference implementation (RED/GREEN) of the PROPOSED enforcement for the shipped
class — it is NOT wired into any production gate (widen no gate before the
change is decided; the proposal and its code citations are delivered separately).
"""
import importlib
import logging
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "igos-build"))

from .factories import make_dependencies, make_package  # noqa: E402
_tracker_mod = importlib.import_module("igos-build.tracker")
PackageTracker = _tracker_mod.PackageTracker

FORGE_HOOKS_PREFIX = "/usr/share/intergenos/installer-hooks/"

# The 13 effective-iso_include=true hooks-class packages repointed by this change.
# (Derived by importing the parser's iso_include logic over the 84 hooks-class
# names, not hand-picked — parser.py:571-575.)
SHIPPED_HOOKS_CLASS = [
    "core/liburing", "core/typing-extensions", "core/vala-pass1",
    "desktop/cairomm", "desktop/mesa", "desktop/pangomm", "desktop/serd",
    "desktop/sord", "desktop/sratom", "desktop/zix",
    "extra/gtkmm3", "extra/libvdpau", "extra/pangomm1",
]


def _declared_verify_paths(tier_name: str) -> list:
    p = _REPO_ROOT / "packages" / tier_name / "package.yml"
    return (yaml.safe_load(p.read_text()) or {}).get("verify_paths") or []


class TestShippedForgePathRegression(unittest.TestCase):
    """GREEN regression: no shipped hooks-class package may declare a verify_path
    under forge's installer-hooks tree — that path is forge's payload, not the
    package's own. Guards against a re-introduction of the vacuous-pass class."""

    def test_no_shipped_package_declares_a_forge_hooks_path(self):
        offenders = {}
        for tn in SHIPPED_HOOKS_CLASS:
            bad = [p for p in _declared_verify_paths(tn)
                   if p.startswith(FORGE_HOOKS_PREFIX)]
            if bad:
                offenders[tn] = bad
        self.assertEqual(offenders, {},
                         f"shipped packages still declaring forge-owned "
                         f"verify_paths: {offenders}")

    def test_each_shipped_package_declares_at_least_one_own_path(self):
        for tn in SHIPPED_HOOKS_CLASS:
            vps = _declared_verify_paths(tn)
            self.assertTrue(vps, f"{tn}: verify_paths is empty")
            self.assertTrue(all(not p.startswith(FORGE_HOOKS_PREFIX) for p in vps),
                            f"{tn}: {vps}")


class TestMirrorSealForgePath(unittest.TestCase):
    """The REAL archive-seal gate rejects a mirror-only package that declares a
    forge-owned path absent from its own archive (the foreign-path class, at the
    live chokepoint) — and passes it when the declared path is its own file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.archives = Path(self.tmp) / "archives"; self.archives.mkdir()
        self.staging = Path(self.tmp) / "stage"; self.staging.mkdir()
        self.tracker = PackageTracker()
        self.tracker.logger = logging.getLogger("test_shipped_vp")
        self.tracker.pkg_archives = self.archives

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def _stage(self, rel):
        p = self.staging / rel; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x"); return p

    def _pkg(self, verify_paths, iso_include=False, name="mirrorhooks"):
        yml = Path(self.tmp) / f"{name}.yml"
        lines = [f"name: {name}", 'version: "1.0"', "verify_paths:"]
        lines += [f"  - {p}" for p in verify_paths]
        yml.write_text("\n".join(lines) + "\n")
        return make_package(name=name, description="t",
            license="t", tier="extra", iso_include=iso_include,
            dependencies=make_dependencies(), template_path=yml)

    def test_mirror_only_forge_path_absent_from_archive_is_rejected(self):
        # RED against the pre-fix tracker; GREEN with the seal gate: a mirror-only
        # package declaring the forge-hooks path (which its own archive never
        # contains) seals FALSE.
        self._stage("usr/lib/libmirror.so.1")  # its real file
        pkg = self._pkg([
            "/usr/lib/libmirror.so.1",
            FORGE_HOOKS_PREFIX + "extra/mirrorhooks",  # forge's, not in this archive
        ])
        with self.assertLogs("test_shipped_vp", level="ERROR") as cm:
            self.assertFalse(self.tracker.pkg_archive(pkg, self.staging))
        self.assertTrue(any("installer-hooks" in m for m in cm.output))
        self.assertTrue(any("missing" in m.lower() for m in cm.output))

    def test_mirror_only_own_path_present_passes(self):
        self._stage("usr/lib/libmirror.so.1")
        pkg = self._pkg(["/usr/lib/libmirror.so.1"])
        self.assertTrue(self.tracker.pkg_archive(pkg, self.staging))


class TestOwnershipLintProposal(unittest.TestCase):
    """Reference implementation (RED/GREEN) of the PROPOSED shipped-class
    enforcement: an ownership lint that flags any declared verify_path not present
    in the package's OWN archive member set, for shipped AND mirror packages alike.

    It is ownership-based (declared-vs-own-members), NOT chroot-presence-based, so
    it sidesteps the reason the runtime seal gate skips shipped archives:
    ldconfig-made SONAME symlinks live in the chroot but not the DESTDIR archive
    (tracker.py test_iso_included_not_enforced), so a naive gate-widen would
    false-positive. The lint checks the DECLARATION against what the package ships,
    which is exactly Rule 20's claim. NOT wired into a production gate here — the
    proposal + code citations accompany this change in review; the maintainers
    rule the landing site (extend the seal gate vs. a standalone preflight lint)."""

    @staticmethod
    def _lint(declared_paths, own_archive_members):
        """Return the declared verify_paths that the package does NOT itself ship
        (the foreign-path findings). own_archive_members: absolute paths the
        package's archive contains."""
        members = set(own_archive_members)
        return [p for p in declared_paths if p not in members]

    def test_forge_path_is_flagged_red(self):
        own = ["/usr/lib/libgtkmm-3.0.so.1", "/usr/lib/libgdkmm-3.0.so.1"]
        declared = ["/usr/lib/libgtkmm-3.0.so.1",
                    FORGE_HOOKS_PREFIX + "extra/gtkmm3"]
        self.assertEqual(self._lint(declared, own),
                         [FORGE_HOOKS_PREFIX + "extra/gtkmm3"])

    def test_own_paths_pass_green(self):
        own = ["/usr/lib/libgtkmm-3.0.so.1", "/usr/lib/libgdkmm-3.0.so.1"]
        declared = ["/usr/lib/libgtkmm-3.0.so.1", "/usr/lib/libgdkmm-3.0.so.1"]
        self.assertEqual(self._lint(declared, own), [])

    def test_repointed_shipped_set_would_pass_the_lint(self):
        # Every repointed shipped package declares only paths outside forge's tree
        # (the lint's foreign-path class) — the honest post-repoint state.
        for tn in SHIPPED_HOOKS_CLASS:
            declared = _declared_verify_paths(tn)
            foreign = [p for p in declared if p.startswith(FORGE_HOOKS_PREFIX)]
            self.assertEqual(foreign, [], f"{tn}: {foreign}")


if __name__ == "__main__":
    unittest.main()
