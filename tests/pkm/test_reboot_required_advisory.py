#!/usr/bin/env python3
"""Regression tests for the 3.0-F28 reboot-required activation-semantics chain.

Covers the whole pipeline that closes the silent-activation failure, decided
2026-07-21 (a package whose payload cannot activate until reboot installs
silently — nvidia's kernel modules behind the nouveau blacklist):

  package.yml `reboot_required: true`
    -> parser.Package.reboot_required
    -> tracker._build_pkginfo emits `reboot_required=true` into .PKGINFO
    -> repo._parse_pkginfo reads it back to a bool
    -> database installed.reboot_required column (+ legacy-DB migration)
    -> services.classify_restart_requirement (declared field authoritative)
    -> services.reboot_required_names + format_reboot_banner (aggregated banner)
    -> scripts/check-reboot-required-declared.py gate (module/boot detection)

Real in-tree recipes (nvidia / linux-kernel / linux-kernel-pass2) are asserted
to declare the field; userspace look-alikes (amdgpu / mesa) are asserted NOT to.
"""

import importlib
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pkm.database import PackageDB  # noqa: E402
from pkm.repo import _parse_pkginfo  # noqa: E402
from pkm.services import (  # noqa: E402
    REBOOT_TRIGGER_PACKAGES,
    classify_restart_requirement,
    format_reboot_banner,
    reboot_required_names,
)


def _load_by_path(mod_name, rel_path):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# parser/tracker use intra-package relative imports (`from .parser import ...`),
# so they must load as the real `igos-build.*` package (import_module handles
# the hyphen), not by bare file path. The gate script is standalone (stdlib-only
# top level) and loads fine by path.
_parser = importlib.import_module("igos-build.parser")
_tracker = importlib.import_module("igos-build.tracker")
_gate = _load_by_path("f28_gate", "scripts/check-reboot-required-declared.py")


def _tracker_cls():
    for name in dir(_tracker):
        obj = getattr(_tracker, name)
        if isinstance(obj, type) and hasattr(obj, "_build_pkginfo"):
            return obj
    raise AssertionError("no tracker class with _build_pkginfo")


def _fake_pkg(**overrides):
    Pkg = _parser.Package
    p = Pkg.__new__(Pkg)
    base = dict(
        name="nvidia", version="1.0", release=1, description="d", license="MIT",
        tier="extra", dependencies=None, eula_helper=None, payload_license=None,
        reboot_required=False,
    )
    base.update(overrides)
    for k, v in base.items():
        setattr(p, k, v)
    return p


class ParserFieldTests(unittest.TestCase):
    def test_field_defaults_false(self):
        self.assertIs(
            _parser.Package.__dataclass_fields__["reboot_required"].default, False
        )

    def test_known_field_registered(self):
        self.assertIn("reboot_required", _parser.KNOWN_FIELDS)

    def test_real_recipes_declare_true(self):
        for rel in ("extra/nvidia", "core/linux-kernel", "core/linux-kernel-pass2"):
            pkg = _parser.parse_template(REPO_ROOT / "packages" / rel / "package.yml")
            self.assertTrue(pkg.reboot_required, f"{rel} should declare reboot_required")

    def test_userspace_lookalikes_do_not_declare(self):
        for rel in ("extra/amdgpu", "desktop/mesa"):
            pkg = _parser.parse_template(REPO_ROOT / "packages" / rel / "package.yml")
            self.assertFalse(pkg.reboot_required, f"{rel} must NOT declare reboot_required")


class PkginfoRoundTripTests(unittest.TestCase):
    def test_tracker_emits_only_when_true(self):
        inst = _tracker_cls().__new__(_tracker_cls())
        on = inst._build_pkginfo(_fake_pkg(reboot_required=True), 100, 3)
        off = inst._build_pkginfo(_fake_pkg(reboot_required=False), 100, 3)
        self.assertIn("reboot_required=true", on)
        self.assertNotIn("reboot_required", off)

    def test_parse_pkginfo_reads_bool(self):
        inst = _tracker_cls().__new__(_tracker_cls())
        on = inst._build_pkginfo(_fake_pkg(reboot_required=True), 100, 3)
        off = inst._build_pkginfo(_fake_pkg(reboot_required=False), 100, 3)
        self.assertIs(_parse_pkginfo(on).get("reboot_required"), True)
        self.assertFalse(_parse_pkginfo(off).get("reboot_required"))

    def test_parse_pkginfo_truthy_variants(self):
        for val in ("true", "1", "yes", "TRUE"):
            meta = _parse_pkginfo(f"pkgname=x\nreboot_required={val}\n")
            self.assertTrue(meta.get("reboot_required"), val)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "pkm.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_get_and_list(self):
        db = PackageDB(self.db_path, root=str(self.root), create_if_missing=True)
        try:
            db.add_installed("nvidia", "1.0", tier="extra", reboot_required=1)
            db.add_installed("vim", "9.0", tier="base", reboot_required=0)
            self.assertEqual(db.get_installed("nvidia")["reboot_required"], 1)
            self.assertEqual(db.get_installed("vim")["reboot_required"], 0)
            listed = {p["name"]: p.get("reboot_required") for p in db.list_installed()}
            self.assertEqual(listed["nvidia"], 1)
            self.assertEqual(listed["vim"], 0)
        finally:
            db.close()

    def test_add_installed_coerces_truthy(self):
        db = PackageDB(self.db_path, root=str(self.root), create_if_missing=True)
        try:
            db.add_installed("k", "1", reboot_required=True)
            self.assertEqual(db.get_installed("k")["reboot_required"], 1)
        finally:
            db.close()

    def test_migration_adds_column_to_legacy_db(self):
        legacy = self.root / "legacy.db"
        c = sqlite3.connect(legacy)
        c.execute(
            "CREATE TABLE installed (id INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL UNIQUE, version TEXT NOT NULL)"
        )
        c.execute("INSERT INTO installed (name, version) VALUES ('old', '1.0')")
        c.commit()
        c.close()
        db = PackageDB(legacy, root=str(self.root), create_if_missing=False)
        try:
            cols = [r[1] for r in db.conn.execute("PRAGMA table_info(installed)").fetchall()]
            self.assertIn("reboot_required", cols)
        finally:
            db.close()


class ClassifyTests(unittest.TestCase):
    def test_declared_field_forces_reboot(self):
        c = classify_restart_requirement("nvidia", ["usr/lib/x/nvidia_drv.so"],
                                         declared_reboot_required=True)
        self.assertEqual(c["requirement"], "reboot")

    def test_structural_fallback_without_declaration(self):
        c = classify_restart_requirement("linux-kernel", [], declared_reboot_required=False)
        self.assertEqual(c["requirement"], "reboot")
        self.assertIn("linux-kernel", REBOOT_TRIGGER_PACKAGES)

    def test_plain_userspace_no_reboot(self):
        c = classify_restart_requirement("vim", ["usr/bin/vim"], declared_reboot_required=False)
        self.assertEqual(c["requirement"], "none")

    def test_default_arg_backward_compatible(self):
        # Existing callers that pass only (name, file_list) still work.
        c = classify_restart_requirement("vim", ["usr/bin/vim"])
        self.assertEqual(c["requirement"], "none")


class BannerTests(unittest.TestCase):
    def test_empty_when_none(self):
        self.assertEqual(format_reboot_banner([]), "")

    def test_aggregates_sorts_dedupes(self):
        b = format_reboot_banner(["nvidia", "linux-kernel", "nvidia"])
        self.assertIn("REBOOT REQUIRED", b)
        self.assertIn("- linux-kernel", b)
        self.assertIn("- nvidia", b)
        self.assertEqual(b.count("- nvidia"), 1)
        # sorted: linux-kernel before nvidia
        self.assertLess(b.index("- linux-kernel"), b.index("- nvidia"))
        self.assertIn("sudo reboot", b)

    def test_reboot_required_names_unions_declared_and_structural(self):
        class FakeDB:
            def __init__(self, m):
                self.m = m

            def get_installed(self, n):
                return self.m.get(n)

        db = FakeDB({
            "nvidia": {"reboot_required": 1},
            "vim": {"reboot_required": 0},
            "glibc": {"reboot_required": 0},  # structural via REBOOT_TRIGGER
        })
        got = set(reboot_required_names(db, ["nvidia", "vim", "glibc", "nvidia"]))
        self.assertEqual(got, {"nvidia", "glibc"})


class GateTests(unittest.TestCase):
    def test_detects_kernel_image(self):
        ships, _ = _gate.ships_module_or_boot_payload(
            'cp -iv arch/x86/boot/bzImage "${DESTDIR}/boot/vmlinuz-${KVER}"\n'
        )
        self.assertTrue(ships)

    def test_detects_modprobe_blacklist(self):
        ships, _ = _gate.ships_module_or_boot_payload(
            'cat > "$DESTDIR/etc/modprobe.d/nv.conf" <<EOF\nblacklist nouveau\nEOF\n'
        )
        self.assertTrue(ships)

    def test_ignores_comment_prose(self):
        # amdgpu-style: mentions modprobe.d/modules only in comments/prose.
        text = (
            "# The kernel driver (amdgpu.ko) is already part of the kernel.\n"
            "#   /etc/modprobe.d/amdgpu.conf — not shipped by default.\n"
            'install -Dm644 vulkan.json "$DESTDIR/usr/share/vulkan/icd.d/radeon.json"\n'
        )
        ships, _ = _gate.ships_module_or_boot_payload(text)
        self.assertFalse(ships)

    def test_plain_userspace_not_detected(self):
        ships, _ = _gate.ships_module_or_boot_payload(
            'install -Dm755 vim "$DESTDIR/usr/bin/vim"\n'
        )
        self.assertFalse(ships)

    def test_gate_passes_on_current_tree(self):
        # Every detected module/boot-path package in the real tree declares
        # the field, so the gate exits 0.
        rc = _gate.main(["--packages", str(REPO_ROOT / "packages")])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
