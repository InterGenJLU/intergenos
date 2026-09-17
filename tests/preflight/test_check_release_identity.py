# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for scripts/check-release-identity.py and scripts/set-release-identity.py.

The release is declared once (igos-release in the base-files recipe); the gate
refuses every other identity source that disagrees — the three files beside it
in the tree, the four files as built into a chroot, the ISO name of the launch
chain, the BUILD_ID/IMAGE_VERSION stamp, and the squashfs-recorded tag the
minted ISO must carry. The origin case (R001.1 at the top of `cat /etc/*release`
and rc001.2 at the bottom of one installed system) is reproduced as fixtures.
"""

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), REPO_ROOT / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GATE = _load("check-release-identity.py")
SETTER = _load("set-release-identity.py")


def write_identity(etc: Path, release="R001.2", codename="revival", *,
                   os_release=None, lsb=None, issue=None, igos=None):
    """Write a consistent identity set under <etc>, with per-file overrides."""
    etc.mkdir(parents=True, exist_ok=True)
    display = codename.capitalize()
    pretty = f"InterGenOS {release} ({display})"
    (etc / "igos-release").write_text(igos if igos is not None else f"{release}\n")
    (etc / "os-release").write_text(os_release if os_release is not None else (
        'NAME="InterGenOS"\n'
        f'VERSION="{release} ({display})"\n'
        "ID=intergenos\nID_LIKE=lfs\n"
        f"VERSION_ID={release.lower()}\n"
        f"VERSION_CODENAME={codename}\n"
        f'PRETTY_NAME="{pretty}"\n'
        'HOME_URL="https://github.com/InterGenJLU/intergenos"\n'
        "LOGO=intergenos\n"))
    (etc / "lsb-release").write_text(lsb if lsb is not None else (
        'DISTRIB_ID="InterGenOS"\n'
        f'DISTRIB_RELEASE="{release}"\n'
        f'DISTRIB_CODENAME="{codename}"\n'
        f'DISTRIB_DESCRIPTION="{pretty}"\n'))
    (etc / "issue").write_text(issue if issue is not None else f"\n  {pretty}\n  Kernel \\r on \\m (\\l)\n\n")


def make_tree(root: Path, **kw) -> Path:
    packages = root / "packages"
    write_identity(packages / "core" / "intergenos-base-files" / "files" / "etc", **kw)
    return packages


def run_gate(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = GATE.main(list(argv))
    return rc, out.getvalue() + err.getvalue()


class TreeAgreement(unittest.TestCase):
    def test_consistent_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp))
            rc, out = run_gate("--packages", str(packages))
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS: every checked source states the declared release R001.2", out)

    def test_rc_prefix_in_the_declaration_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), igos="rc001.3\n")
            rc, out = run_gate("--packages", str(packages))
        self.assertEqual(rc, 1, out)
        self.assertIn("no rc/candidate prefix", out)

    def test_lsb_release_drift_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), lsb=(
                'DISTRIB_ID="InterGenOS"\nDISTRIB_RELEASE="R001.1"\n'
                'DISTRIB_CODENAME="revival"\nDISTRIB_DESCRIPTION="InterGenOS R001.1 (Revival)"\n'))
            rc, out = run_gate("--packages", str(packages))
        self.assertEqual(rc, 1, out)
        self.assertIn("lsb-release: DISTRIB_RELEASE = 'R001.1' (expected 'R001.2')", out)
        self.assertIn("DISTRIB_DESCRIPTION", out)

    def test_issue_banner_drift_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), issue="\n  InterGenOS R001.1 (Revival)\n  Kernel \\r on \\m (\\l)\n\n")
            rc, out = run_gate("--packages", str(packages))
        self.assertEqual(rc, 1, out)
        self.assertIn("issue: banner = 'InterGenOS R001.1 (Revival)' (expected 'InterGenOS R001.2 (Revival)')", out)

    def test_os_release_version_id_drift_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp))
            osr = packages / "core/intergenos-base-files/files/etc/os-release"
            osr.write_text(osr.read_text().replace("VERSION_ID=r001.2", "VERSION_ID=rc001.2"))
            rc, out = run_gate("--packages", str(packages))
        self.assertEqual(rc, 1, out)
        self.assertIn("VERSION_ID = 'rc001.2' (expected 'r001.2')", out)

    def test_missing_identity_file_is_a_disagreement_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp))
            (packages / "core/intergenos-base-files/files/etc/issue").unlink()
            rc, out = run_gate("--packages", str(packages))
        self.assertEqual(rc, 1, out)
        self.assertIn("issue = 'absent'", out)

    def test_the_real_tree_states_one_release(self):
        """Prove against reality: this checkout's base-files agree with themselves."""
        rc, out = run_gate("--packages", str(REPO_ROOT / "packages"))
        self.assertEqual(rc, 0, out)


class ChrootLeg(unittest.TestCase):
    def test_chroot_carrying_the_previous_release_is_refused(self):
        """The origin case: the tree moved to R001.2 but the chroot still held R001.1's base-files."""
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), release="R001.2")
            chroot = Path(tmp) / "chroot"
            write_identity(chroot / "etc", release="R001.1")
            rc, out = run_gate("--packages", str(packages), "--chroot", str(chroot))
        self.assertEqual(rc, 1, out)
        self.assertIn("chroot/igos-release: release = 'R001.1' (expected 'R001.2')", out)

    def test_chroot_matching_the_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp))
            chroot = Path(tmp) / "chroot"
            write_identity(chroot / "etc")
            rc, out = run_gate("--packages", str(packages), "--chroot", str(chroot))
        self.assertEqual(rc, 0, out)

    def test_chroot_without_the_files_is_a_disagreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp))
            chroot = Path(tmp) / "chroot"
            (chroot / "etc").mkdir(parents=True)
            rc, out = run_gate("--packages", str(packages), "--chroot", str(chroot))
        self.assertEqual(rc, 1, out)
        self.assertIn("chroot: igos-release = 'absent'", out)


class IsoNameLeg(unittest.TestCase):
    def _tree(self, tmp):
        return make_tree(Path(tmp), release="R001.3")

    def test_release_name_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run_gate("--packages", str(self._tree(tmp)), "--iso-name", "intergenos-r001.3.iso")
        self.assertEqual(rc, 0, out)

    def test_remint_ordinal_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run_gate("--packages", str(self._tree(tmp)), "--iso-name", "intergenos-r001.3-02.iso")
        self.assertEqual(rc, 0, out)

    def test_rc_prefix_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run_gate("--packages", str(self._tree(tmp)), "--iso-name", "intergenos-rc001.3.iso")
        self.assertEqual(rc, 1, out)
        self.assertIn("iso name: file name = 'intergenos-rc001.3.iso' (expected 'intergenos-r001.3.iso", out)

    def test_another_release_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run_gate("--packages", str(self._tree(tmp)), "--iso-name", "intergenos-r001.2-03.iso")
        self.assertEqual(rc, 1, out)

    def test_dev_candidate_name_fails_in_release_mode_and_warns_in_test_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = self._tree(tmp)
            rc, out = run_gate("--packages", str(packages), "--iso-name", "intergenos-ge9b-13-dev.iso")
            self.assertEqual(rc, 1, out)
            rc, out = run_gate("--packages", str(packages), "--iso-name", "intergenos-ge9b-13-dev.iso",
                               "--mode", "test")
        self.assertEqual(rc, 0, out)
        self.assertIn("warning (test mode, not a release)", out)

    def test_test_mode_keeps_the_four_file_agreement_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), igos="R001.1\n")
            rc, out = run_gate("--packages", str(packages), "--iso-name", "x-dev.iso", "--mode", "test")
        self.assertEqual(rc, 1, out)

    def test_persisted_name_file_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = self._tree(tmp)
            name_file = Path(tmp) / ".iso-name"
            name_file.write_text("intergenos-rc001.3.iso\n")
            rc, out = run_gate("--packages", str(packages), "--iso-name-file", str(name_file))
        self.assertEqual(rc, 1, out)

    def test_missing_name_is_refused_only_when_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = self._tree(tmp)
            absent = str(Path(tmp) / ".iso-name")
            rc, _ = run_gate("--packages", str(packages), "--iso-name-file", absent)
            self.assertEqual(rc, 0)
            rc, out = run_gate("--packages", str(packages), "--iso-name-file", absent, "--require-iso-name")
        self.assertEqual(rc, 1, out)
        self.assertIn("no --iso-name this launch chain", out)


class StampLeg(unittest.TestCase):
    def _stamped(self, tmp, build_id, image_version=None):
        image_version = build_id if image_version is None else image_version
        p = Path(tmp) / "os-release"
        write_identity(Path(tmp) / "etc", release="R001.3")
        text = (Path(tmp) / "etc" / "os-release").read_text()
        p.write_text(text + f'BUILD_ID="{build_id}"\nIMAGE_VERSION="{image_version}"\n')
        return p

    def test_the_origin_stamp_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), release="R001.2")
            p = Path(tmp) / "os-release"
            p.write_text((packages / "core/intergenos-base-files/files/etc/os-release").read_text()
                         + 'IMAGE_VERSION="rc001.2-03"\n')
            rc, out = run_gate("--packages", str(packages), "--os-release", str(p))
        self.assertEqual(rc, 1, out)
        self.assertIn("IMAGE_VERSION = 'rc001.2-03'", out)
        self.assertIn("BUILD_ID = 'absent'", out)

    def test_matching_stamp_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), release="R001.3")
            p = self._stamped(tmp, "r001.3-02")
            rc, out = run_gate("--packages", str(packages), "--os-release", str(p),
                               "--iso-name", "intergenos-r001.3-02.iso")
        self.assertEqual(rc, 0, out)

    def test_stamp_disagreeing_with_the_name_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), release="R001.3")
            p = self._stamped(tmp, "r001.3")
            rc, out = run_gate("--packages", str(packages), "--os-release", str(p),
                               "--iso-name", "intergenos-r001.3-02.iso")
        self.assertEqual(rc, 1, out)
        self.assertIn("(expected 'r001.3-02')", out)

    def test_build_id_and_image_version_must_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), release="R001.3")
            p = self._stamped(tmp, "r001.3", "r001.3-02")
            rc, out = run_gate("--packages", str(packages), "--os-release", str(p))
        self.assertEqual(rc, 1, out)
        self.assertIn("BUILD_ID vs IMAGE_VERSION", out)

    def test_unnamed_stamp_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), release="R001.3")
            p = self._stamped(tmp, "unnamed-20260914")
            rc, out = run_gate("--packages", str(packages), "--os-release", str(p))
        self.assertEqual(rc, 1, out)


class ImageVersionRecordLeg(unittest.TestCase):
    def test_minted_name_must_match_the_recorded_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), release="R001.3")
            rec = Path(tmp) / ".image-version"
            rec.write_text("r001.3\n")
            rc, out = run_gate("--packages", str(packages), "--iso-name", "intergenos-r001.3.iso",
                               "--image-version-file", str(rec))
            self.assertEqual(rc, 0, out)
            rc, out = run_gate("--packages", str(packages), "--iso-name", "intergenos-r001.3-02.iso",
                               "--image-version-file", str(rec))
        self.assertEqual(rc, 1, out)
        self.assertIn("stamped tag = 'r001.3' (expected 'r001.3-02 (", out)

    def test_record_without_a_name_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), release="R001.3")
            rec = Path(tmp) / ".image-version"
            rec.write_text("r001.3\n")
            rc, out = run_gate("--packages", str(packages), "--image-version-file", str(rec))
        self.assertEqual(rc, 2, out)


class Setter(unittest.TestCase):
    def test_moves_all_four_files_and_the_gate_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), release="R001.2")
            changed = SETTER.apply(packages, "R001.3", None)
            self.assertEqual(changed, ["igos-release", "os-release", "lsb-release", "issue"])
            etc = packages / "core/intergenos-base-files/files/etc"
            self.assertEqual((etc / "igos-release").read_text(), "R001.3\n")
            osr = GATE.read_kv(etc / "os-release")
            self.assertEqual(osr["VERSION"], "R001.3 (Revival)")
            self.assertEqual(osr["VERSION_ID"], "r001.3")
            self.assertEqual(osr["PRETTY_NAME"], "InterGenOS R001.3 (Revival)")
            self.assertEqual(osr["LOGO"], "intergenos", "untouched lines stay")
            lsb = GATE.read_kv(etc / "lsb-release")
            self.assertEqual(lsb["DISTRIB_DESCRIPTION"], "InterGenOS R001.3 (Revival)")
            self.assertIn("  InterGenOS R001.3 (Revival)\n  Kernel", (etc / "issue").read_text())
            rc, out = run_gate("--packages", str(packages))
            self.assertEqual(rc, 0, out)
            self.assertEqual(SETTER.apply(packages, "R001.3", None), [], "idempotent")

    def test_codename_can_move_with_the_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp), release="R001.2")
            SETTER.apply(packages, "R002", "ascent")
            etc = packages / "core/intergenos-base-files/files/etc"
            self.assertEqual(GATE.read_kv(etc / "os-release")["VERSION"], "R002 (Ascent)")
            self.assertEqual(GATE.read_kv(etc / "lsb-release")["DISTRIB_CODENAME"], "ascent")
            rc, out = run_gate("--packages", str(packages))
            self.assertEqual(rc, 0, out)

    def test_refuses_a_candidate_spelling(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = make_tree(Path(tmp))
            with self.assertRaises(ValueError):
                SETTER.apply(packages, "rc001.3", None)
            with self.assertRaises(ValueError):
                SETTER.apply(packages, "R001.3-02", None)

    def test_cli_round_trip_on_a_copy_of_the_real_tree(self):
        """Prove against reality: the real recipe's files move cleanly to a next release and back.

        The next release is derived from the one the tree declares (its last
        number plus one), so the test does not fix a release the tree will one
        day already state — which would make the forward move a no-op.
        """
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            src = REPO_ROOT / "packages/core/intergenos-base-files/files/etc"
            dst = Path(tmp) / "packages/core/intergenos-base-files/files/etc"
            dst.mkdir(parents=True)
            for name in GATE.IDENTITY_FILES:
                shutil.copy2(src / name, dst / name)
            before = {n: (dst / n).read_text() for n in GATE.IDENTITY_FILES}
            current, disagreements = GATE.read_declared(dst)
            self.assertEqual(disagreements, [], "the real tree's identity files must agree before the move")
            stem, _, last = current.rpartition(".")
            self.assertTrue(stem and last.isdigit(), current)
            target = f"{stem}.{int(last) + 1}"
            self.assertNotEqual(target, current)
            out = io.StringIO()
            with redirect_stdout(out):
                rc = SETTER.main([target, "--packages", str(Path(tmp) / "packages")])
            self.assertEqual(rc, 0, out.getvalue())
            self.assertIn("rewrote igos-release, os-release, lsb-release, issue", out.getvalue())
            declared, _ = GATE.read_declared(dst)
            self.assertEqual(declared, target)
            with redirect_stdout(out):
                rc = SETTER.main([declared_back := before["igos-release"].strip(),
                                  "--packages", str(Path(tmp) / "packages")])
            self.assertEqual(rc, 0, out.getvalue())
            after = {n: (dst / n).read_text() for n in GATE.IDENTITY_FILES}
            self.assertEqual(after, before, "moving forward and back reproduces the real files byte for byte")


if __name__ == "__main__":
    unittest.main()
