"""Tests for installer/backend/install.py — Phase 4 orchestrator.

Covers: yaml load + validate, happy-path 12-phase pipeline (EFI + BIOS
branches), per-phase progress events, failure rollback (unmount based on
phase_completed), package-failure-non-fatal surface, dry_run propagation.

All disk / chroot / mount / bootloader operations are mocked at the
orchestrator's import path (installer.backend.install.<module>) so tests
exercise ONLY orchestrator logic, not the underlying backend modules.
Backend modules have their own test coverage (e.g. test_mok.py,
test_backend_packages.py at tests/installer/).
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from installer.backend.install import (
    InstallResult,
    PHASE_BOOTLOADER,
    PHASE_CLEANUP,
    PHASE_CONFIG,
    PHASE_HOOKS,
    PHASE_MOK,
    PHASE_MOUNT,
    PHASE_ORDER,
    PHASE_PACKAGES,
    PHASE_PARTITION,
    PHASE_SERVICES,
    PHASE_USERS,
    PHASE_VALIDATE,
    PHASE_VIRTUAL_FS,
    load_yaml_config,
    run_install,
    validate_install_inputs,
)


VALID_CFG = {
    "version": 1,
    "locale": "en_US.UTF-8",
    "timezone": "UTC",
    "hostname": "intergenos",
    "package_groups": ["core", "base"],
}

VALID_INSTALL_IO = {
    "disk": "/dev/sda",
    "username": "user",
    "user_password": "pw1234",
    "root_password": "root1234",
}


def _write_yaml(tmpdir, cfg):
    p = Path(tmpdir) / "install.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


class TestLoadYamlConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_loads_valid_yaml(self):
        p = _write_yaml(self.tmp, VALID_CFG)
        cfg = load_yaml_config(p)
        self.assertEqual(cfg["hostname"], "intergenos")
        self.assertEqual(cfg["package_groups"], ["core", "base"])

    def test_missing_file_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            load_yaml_config(Path(self.tmp) / "nonexistent.yaml")

    def test_non_mapping_raises_valueerror(self):
        p = Path(self.tmp) / "list.yaml"
        p.write_text("- not\n- a\n- mapping\n")
        with self.assertRaises(ValueError):
            load_yaml_config(p)


class TestValidateInstallInputs(unittest.TestCase):
    def test_valid_passes(self):
        validate_install_inputs(VALID_CFG, VALID_INSTALL_IO)

    def test_yaml_missing_field_raises(self):
        cfg = {**VALID_CFG}
        del cfg["timezone"]
        with self.assertRaises(ValueError) as ctx:
            validate_install_inputs(cfg, VALID_INSTALL_IO)
        self.assertIn("timezone", str(ctx.exception))

    def test_install_io_missing_field_raises(self):
        io = {**VALID_INSTALL_IO}
        del io["disk"]
        with self.assertRaises(ValueError) as ctx:
            validate_install_inputs(VALID_CFG, io)
        self.assertIn("disk", str(ctx.exception))

    def test_install_io_empty_password_raises(self):
        io = {**VALID_INSTALL_IO, "user_password": ""}
        with self.assertRaises(ValueError) as ctx:
            validate_install_inputs(VALID_CFG, io)
        self.assertIn("user_password", str(ctx.exception))

    def test_package_groups_must_include_core(self):
        cfg = {**VALID_CFG, "package_groups": ["base"]}
        with self.assertRaises(ValueError) as ctx:
            validate_install_inputs(cfg, VALID_INSTALL_IO)
        self.assertIn("core", str(ctx.exception))

    def test_package_groups_must_be_non_empty_list(self):
        cfg = {**VALID_CFG, "package_groups": []}
        with self.assertRaises(ValueError) as ctx:
            validate_install_inputs(cfg, VALID_INSTALL_IO)
        self.assertIn("non-empty list", str(ctx.exception))

    def test_aggregates_multiple_errors(self):
        cfg = {"hostname": "h"}
        io = {}
        with self.assertRaises(ValueError) as ctx:
            validate_install_inputs(cfg, io)
        msg = str(ctx.exception)
        self.assertIn("locale", msg)
        self.assertIn("timezone", msg)
        self.assertIn("disk", msg)
        self.assertIn("username", msg)


class _RunInstallTestBase(unittest.TestCase):
    """Shared setup for run_install tests — patches every backend module
    at the orchestrator's import path."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.yaml_path = _write_yaml(self.tmp, VALID_CFG)
        self.archive_dir = Path(self.tmp) / "archives"
        self.archive_dir.mkdir()
        self.packages_dir = Path(self.tmp) / "packages"
        self.packages_dir.mkdir()

        self.partitions = {"esp": "/dev/sda1", "root": "/dev/sda2", "efi": True}

        self._patches = [
            patch("installer.backend.install.disks"),
            patch("installer.backend.install.packages"),
            patch("installer.backend.install.config"),
            patch("installer.backend.install.users"),
            patch("installer.backend.install.mok"),
            patch("installer.backend.install.bootloader"),
            patch("installer.backend.install.hooks"),
        ]
        self.disks = self._patches[0].start()
        self.packages = self._patches[1].start()
        self.config = self._patches[2].start()
        self.users = self._patches[3].start()
        self.mok = self._patches[4].start()
        self.bootloader = self._patches[5].start()
        self.hooks = self._patches[6].start()

        self.disks.is_efi.return_value = True
        self.disks.partition_disk.return_value = self.partitions
        self.packages.install_packages.return_value = (5, 0, [])
        self.mok.generate_mok_keypair.return_value = {
            "key_path": "/var/lib/intergen/mok/mok.key",
            "cert_path": "/var/lib/intergen/mok/mok.crt",
            "der_path": "/var/lib/intergen/mok/mok.der",
        }

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp)


class TestRunInstallHappyPath(_RunInstallTestBase):
    def test_full_pipeline_efi_completes(self):
        result = run_install(
            self.yaml_path, VALID_INSTALL_IO,
            str(self.archive_dir), str(self.packages_dir),
        )
        self.assertTrue(result.success, msg=result.error_message)
        self.assertEqual(result.phase_completed, PHASE_CLEANUP)
        self.assertIsNone(result.error_message)
        self.assertEqual(result.package_success_count, 5)
        self.assertEqual(result.package_fail_count, 0)

    def test_full_pipeline_efi_calls_each_backend_in_order(self):
        run_install(
            self.yaml_path, VALID_INSTALL_IO,
            str(self.archive_dir), str(self.packages_dir),
        )
        self.disks.partition_disk.assert_called_once_with("/dev/sda", efi=True)
        self.disks.mount_target.assert_called_once()
        self.hooks.mount_virtual_fs.assert_called_once()
        self.packages.install_packages.assert_called_once()
        self.config.generate_all.assert_called_once()
        self.users.set_root_password.assert_called_once()
        self.users.create_user.assert_called_once()
        self.mok.generate_mok_keypair.assert_called_once()
        self.bootloader.install_bootloader.assert_called_once()
        self.hooks.run_post_install_hooks.assert_called_once()
        self.users.enable_services.assert_called_once()
        self.hooks.unmount_virtual_fs.assert_called_once()
        self.disks.unmount_target.assert_called_once()

    def test_bios_install_skips_mok_keypair(self):
        self.disks.is_efi.return_value = False
        self.disks.partition_disk.return_value = {**self.partitions, "efi": False}
        result = run_install(
            self.yaml_path, VALID_INSTALL_IO,
            str(self.archive_dir), str(self.packages_dir),
        )
        self.assertTrue(result.success, msg=result.error_message)
        self.mok.generate_mok_keypair.assert_not_called()
        self.bootloader.install_bootloader.assert_called_once()
        _, kwargs = self.bootloader.install_bootloader.call_args
        self.assertIsNone(kwargs.get("mok_keypair"))

    def test_efi_with_mok_password_queues_enrollment(self):
        io = {**VALID_INSTALL_IO, "mok_password": "enroll1234"}
        run_install(self.yaml_path, io,
                    str(self.archive_dir), str(self.packages_dir))
        self.mok.queue_mok_enrollment.assert_called_once()
        args = self.mok.queue_mok_enrollment.call_args
        self.assertEqual(args[0][1], "/var/lib/intergen/mok/mok.der")
        self.assertEqual(args[0][2], "enroll1234")

    def test_efi_without_mok_password_skips_enrollment(self):
        run_install(self.yaml_path, VALID_INSTALL_IO,
                    str(self.archive_dir), str(self.packages_dir))
        self.mok.queue_mok_enrollment.assert_not_called()


class TestRunInstallProgressCallback(_RunInstallTestBase):
    def test_progress_callback_fires_for_every_phase(self):
        seen_phases = set()

        def cb(phase, current, total, message):
            seen_phases.add(phase)

        result = run_install(
            self.yaml_path, VALID_INSTALL_IO,
            str(self.archive_dir), str(self.packages_dir),
            progress_callback=cb,
        )
        self.assertTrue(result.success, msg=result.error_message)
        for phase in PHASE_ORDER:
            self.assertIn(phase, seen_phases, f"phase {phase} did not fire")

    def test_progress_callback_passes_total_eq_phase_count(self):
        events = []

        def cb(phase, current, total, message):
            events.append((phase, current, total))

        run_install(self.yaml_path, VALID_INSTALL_IO,
                    str(self.archive_dir), str(self.packages_dir),
                    progress_callback=cb)
        # Phase-boundary events have total == len(PHASE_ORDER) == 12.
        # Per-package events from PHASE_PACKAGES fan-out have a different total.
        phase_boundary_totals = [t for p, _, t in events if p != PHASE_PACKAGES
                                 and p != PHASE_HOOKS]
        self.assertTrue(all(t == len(PHASE_ORDER) for t in phase_boundary_totals))

    def test_no_callback_no_error(self):
        result = run_install(
            self.yaml_path, VALID_INSTALL_IO,
            str(self.archive_dir), str(self.packages_dir),
            progress_callback=None,
        )
        self.assertTrue(result.success, msg=result.error_message)


class TestRunInstallPackageFailureNonFatal(_RunInstallTestBase):
    def test_package_failures_surface_but_dont_abort(self):
        self.packages.install_packages.return_value = (
            3, 2, [("badpkg1", "extract failed"), ("badpkg2", "queue order")]
        )
        result = run_install(
            self.yaml_path, VALID_INSTALL_IO,
            str(self.archive_dir), str(self.packages_dir),
        )
        self.assertTrue(result.success, msg=result.error_message)
        self.assertEqual(result.package_success_count, 3)
        self.assertEqual(result.package_fail_count, 2)
        self.assertEqual(len(result.failed_packages), 2)
        self.assertEqual(result.phase_completed, PHASE_CLEANUP)
        self.bootloader.install_bootloader.assert_called_once()


class TestRunInstallFailurePaths(_RunInstallTestBase):
    def test_validation_failure_does_not_partition(self):
        bad_io = {**VALID_INSTALL_IO}
        del bad_io["disk"]
        result = run_install(
            self.yaml_path, bad_io,
            str(self.archive_dir), str(self.packages_dir),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.phase_completed, None)
        self.assertIn("disk", result.error_message)
        self.disks.partition_disk.assert_not_called()
        self.disks.unmount_target.assert_not_called()

    def test_partition_failure_does_not_unmount(self):
        self.disks.partition_disk.side_effect = RuntimeError("parted failed")
        result = run_install(
            self.yaml_path, VALID_INSTALL_IO,
            str(self.archive_dir), str(self.packages_dir),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.phase_completed, PHASE_VALIDATE)
        self.assertIn("parted failed", result.error_message)
        self.disks.unmount_target.assert_not_called()
        self.hooks.unmount_virtual_fs.assert_not_called()

    def test_mount_failure_does_not_unmount_virtfs(self):
        self.disks.mount_target.side_effect = RuntimeError("mount failed")
        result = run_install(
            self.yaml_path, VALID_INSTALL_IO,
            str(self.archive_dir), str(self.packages_dir),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.phase_completed, PHASE_PARTITION)
        self.disks.unmount_target.assert_not_called()
        self.hooks.unmount_virtual_fs.assert_not_called()

    def test_bootloader_failure_unmounts_cleanly(self):
        self.bootloader.install_bootloader.side_effect = RuntimeError(
            "grub-install failed"
        )
        result = run_install(
            self.yaml_path, VALID_INSTALL_IO,
            str(self.archive_dir), str(self.packages_dir),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.phase_completed, PHASE_MOK)
        self.assertIn("grub-install failed", result.error_message)
        self.hooks.unmount_virtual_fs.assert_called_once()
        self.disks.unmount_target.assert_called_once()

    def test_cleanup_error_does_not_mask_original(self):
        self.bootloader.install_bootloader.side_effect = RuntimeError("boot fail")
        self.disks.unmount_target.side_effect = RuntimeError("umount fail")
        result = run_install(
            self.yaml_path, VALID_INSTALL_IO,
            str(self.archive_dir), str(self.packages_dir),
        )
        self.assertFalse(result.success)
        self.assertIn("boot fail", result.error_message)
        self.assertNotIn("umount fail", result.error_message)


class TestRunInstallDryRun(_RunInstallTestBase):
    def test_dry_run_propagates_to_disks(self):
        run_install(
            self.yaml_path, VALID_INSTALL_IO,
            str(self.archive_dir), str(self.packages_dir),
            dry_run=True,
        )
        self.disks.set_dry_run.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
