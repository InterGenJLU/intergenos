"""Forge install orchestrator — yaml-consume + write-to-disk pipeline.

Phase 4 backend that consumes the install-time yaml (locale/timezone/
hostname/package_groups per `installer/data/install-schema.yaml` v1) +
the interactively-collected disk/password choices, then drives the
backend modules in order to install InterGenOS to disk.

Both frontends (TUI + GUI) invoke `run_install()`. Same backend behind
both per Q-TUI-INTERACTIVITY=B + Q-GUI-SCREENS=7 architecture.

Phase order:
    validate → partition → mount → virtual_fs → packages → config →
    users → mok (keypair) → bootloader → hooks → services → cleanup
    (mok enrollment is queued AFTER services so a queue-fail leaves
    the system bootable; the user can re-enroll via mokutil from the
    running install if needed.)

Progress is reported via a caller-supplied callback fn(phase, current,
total, message). Phase identifiers are stable strings (PHASE_*); current
counts up by phase-index. The packages phase fans out per-package events
so the frontend can render package-level progress.

Failure handling: any phase raising halts the pipeline + best-effort
unmounts what was mounted. The original error surfaces in
InstallResult.error_message; phase_completed names the last successful
phase so the frontend can render which step we got to.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from . import bootloader, config, disks, hooks, integrity, mok, packages, users


PHASE_VALIDATE = "validate"
PHASE_VERIFY = "verify"
PHASE_PARTITION = "partition"
PHASE_MOUNT = "mount"
PHASE_VIRTUAL_FS = "virtual_fs"
PHASE_PACKAGES = "packages"
PHASE_CONFIG = "config"
PHASE_USERS = "users"
PHASE_MOK = "mok"
PHASE_BOOTLOADER = "bootloader"
PHASE_HOOKS = "hooks"
PHASE_SERVICES = "services"
PHASE_CLEANUP = "cleanup"

PHASE_ORDER = [
    PHASE_VALIDATE,
    PHASE_VERIFY,
    PHASE_PARTITION,
    PHASE_MOUNT,
    PHASE_VIRTUAL_FS,
    PHASE_PACKAGES,
    PHASE_CONFIG,
    PHASE_USERS,
    PHASE_MOK,
    PHASE_BOOTLOADER,
    PHASE_HOOKS,
    PHASE_SERVICES,
    PHASE_CLEANUP,
]

REQUIRED_YAML_FIELDS = ("locale", "timezone", "hostname", "package_groups")
REQUIRED_INSTALL_IO_FIELDS = ("disk", "username", "user_password", "root_password")

DEFAULT_TARGET = "/mnt/target"

# Phases past which we must best-effort unmount on failure to leave the
# system in a known state. Anything earlier (validate / partition) didn't
# mount anything to clean up.
_PHASES_NEEDING_UNMOUNT = {
    PHASE_MOUNT,
    PHASE_VIRTUAL_FS,
    PHASE_PACKAGES,
    PHASE_CONFIG,
    PHASE_USERS,
    PHASE_MOK,
    PHASE_BOOTLOADER,
    PHASE_HOOKS,
    PHASE_SERVICES,
}
_PHASES_NEEDING_VIRTFS_UNMOUNT = _PHASES_NEEDING_UNMOUNT - {PHASE_MOUNT}


@dataclass
class VerifyConfig:
    """Configuration for the install-time integrity verification phase.

    Pass to run_install() to enable PHASE_VERIFY (which then halts the install
    before any disk write if the archive manifest doesn't validate). Pass
    None to skip the phase entirely (useful in dev/test contexts that don't
    have a signed manifest available).

    manifest_path:    path to signed BSD-format manifest copied from install
                      media (intergenos-archive-manifest.txt).
    public_key_path:  path to release-key public component (single keyring
                      file containing master + S1 release keys).
    audit_log_path:   path where the hash-chained JSONL audit log is appended.
                      Created if missing. Survives onto target during cleanup.
    warning_callback: fn(package_name, expected_sha, actual_sha) — frontend
                      renders the warning text from
                      integrity.INTEGRITY_WARNING_TEMPLATE.
    ack_callback:     fn(package_name) → bool. Returns True iff the user
                      typed integrity.expected_override_phrase(package_name)
                      exactly. False = abort.
    """
    manifest_path: Path
    public_key_path: Path
    audit_log_path: Path
    warning_callback: Callable[[str, str, str], None]
    ack_callback: Callable[[str], bool]


@dataclass
class InstallResult:
    """Outcome of run_install().

    success: True only when every phase completed cleanly.
    phase_completed: name of the LAST phase that completed; on failure,
                     this names where we got to (the phase that raised is
                     NOT marked completed).
    error_message: '<ExceptionType>: <message>' on failure; None on success.
    failed_packages: list of (name, msg) tuples from packages.install_packages
                     for any package that failed during the packages phase.
                     Note: package failures do NOT abort the install (per
                     orchestrator policy — surface partial state, keep going).
    package_success_count / package_fail_count: counts from packages phase.
    integrity_overrides_granted: count of integrity-mismatch overrides the
                     user accepted via typed phrase during PHASE_VERIFY.
                     Surface to user in install-complete summary.
    integrity_aborted_at: package name where user declined to override
                     during PHASE_VERIFY; None unless verify-phase abort.
    """
    success: bool
    phase_completed: Optional[str] = None
    error_message: Optional[str] = None
    failed_packages: list = field(default_factory=list)
    package_success_count: int = 0
    package_fail_count: int = 0
    integrity_overrides_granted: int = 0
    integrity_aborted_at: Optional[str] = None


def load_yaml_config(yaml_path):
    """Parse install yaml. Raises FileNotFoundError / yaml.YAMLError /
    ValueError on missing or malformed input."""
    p = Path(yaml_path)
    if not p.exists():
        raise FileNotFoundError(f"yaml config not found: {yaml_path}")
    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(
            f"yaml config must be a top-level mapping, got {type(cfg).__name__}"
        )
    return cfg


def validate_install_inputs(cfg, install_io):
    """Validate yaml + install_io. Aggregates errors; raises ValueError once
    listing every problem so frontends can surface them together rather than
    one-at-a-time loop with the user."""
    errors = []

    for field_name in REQUIRED_YAML_FIELDS:
        if field_name not in cfg:
            errors.append(f"yaml missing required field: {field_name}")

    if "package_groups" in cfg:
        groups = cfg["package_groups"]
        if not isinstance(groups, list) or not groups:
            errors.append("yaml package_groups must be a non-empty list")
        elif "core" not in groups:
            # Schema says core is required; frontends should force-include it
            # even when the user un-toggles. If we see it missing here, the
            # frontend's contract is broken and we refuse rather than silently
            # producing an unbootable system.
            errors.append(
                "yaml package_groups must include 'core' (required tier)"
            )

    for field_name in REQUIRED_INSTALL_IO_FIELDS:
        if field_name not in install_io or not install_io[field_name]:
            errors.append(f"install_io missing required field: {field_name}")

    if errors:
        raise ValueError(
            "install validation failed:\n  - " + "\n  - ".join(errors)
        )


def run_install(yaml_path, install_io, archive_dir, packages_dir=None,
                progress_callback=None, dry_run=False, target=DEFAULT_TARGET,
                verify_config=None):
    """Run the full Forge install pipeline.

    Args:
        yaml_path: path to install.yaml (TUI-emitted or GUI-emitted).
        install_io: dict with 'disk', 'username', 'user_password',
                    'root_password' (required); 'mok_password',
                    'install_mode', 'user_groups' (optional).
        archive_dir: path to .igos.tar.gz package archives.
        packages_dir: path to packages/ for tier mapping + post-install
                      hooks. Optional; some flows pass None.
        progress_callback: fn(phase, current, total, message). Called at
                           phase boundaries + per-package within the
                           packages phase. None disables progress events.
        dry_run: if True, set disks._DRY_RUN globally so destructive disk
                 operations log instead of executing. Mounts + chroot +
                 package installs still execute.
        target: target root mountpoint. Defaults to /mnt/target.
        verify_config: optional VerifyConfig enabling PHASE_VERIFY (signed-
                       manifest integrity check before partition). Pass None
                       to skip the phase (dev/test contexts without a signed
                       manifest). Production install media always provides
                       this — Holy Grail v1.0 ship-gate.

    Returns:
        InstallResult dataclass.
    """
    result = InstallResult(success=False)

    if dry_run:
        disks.set_dry_run(True)

    total = len(PHASE_ORDER)

    def _emit(phase, idx, message=""):
        if progress_callback:
            progress_callback(phase, idx, total, message)

    partitions = None
    mok_keypair = None

    try:
        # 1: validate
        _emit(PHASE_VALIDATE, 0, "loading + validating install config")
        cfg = load_yaml_config(yaml_path)
        validate_install_inputs(cfg, install_io)
        result.phase_completed = PHASE_VALIDATE
        _emit(PHASE_VALIDATE, 1, "config valid")

        # 2: verify (signed-manifest integrity check before any disk write)
        if verify_config is not None:
            _emit(PHASE_VERIFY, 1, "verifying archive integrity against signed manifest")
            verify_result = integrity.verify_archives(
                archive_dir=Path(archive_dir),
                manifest_path=Path(verify_config.manifest_path),
                public_key_path=Path(verify_config.public_key_path),
                warning_callback=verify_config.warning_callback,
                ack_callback=verify_config.ack_callback,
                audit_log_path=Path(verify_config.audit_log_path),
            )
            result.integrity_overrides_granted = verify_result.overrides_granted
            if not verify_result.success:
                # Verify-phase failure: set error + return without touching disk.
                # phase_completed stays at VALIDATE — VERIFY itself didn't complete.
                result.error_message = (
                    verify_result.error
                    or f"integrity verification aborted at {verify_result.aborted_at}"
                )
                result.integrity_aborted_at = verify_result.aborted_at
                _emit(PHASE_VERIFY, 1,
                      f"integrity verification FAILED: {result.error_message}")
                return result
            result.phase_completed = PHASE_VERIFY
            override_msg = (
                f" ({verify_result.overrides_granted} override(s) granted)"
                if verify_result.overrides_granted else ""
            )
            _emit(PHASE_VERIFY, 2, f"archives verified{override_msg}")
        else:
            # Verify skipped — log a single event for observability.
            _emit(PHASE_VERIFY, 2, "verify phase skipped (no verify_config)")

        # 3: partition + format (partition_disk does both)
        _emit(PHASE_PARTITION, 2, f"partitioning {install_io['disk']}")
        efi = disks.is_efi()
        partitions = disks.partition_disk(install_io["disk"], efi=efi)
        result.phase_completed = PHASE_PARTITION
        _emit(PHASE_PARTITION, 3, "partitioned + formatted")

        # 4: mount target
        _emit(PHASE_MOUNT, 3, f"mounting target {target}")
        disks.mount_target(partitions, target=target)
        result.phase_completed = PHASE_MOUNT
        _emit(PHASE_MOUNT, 4, "target mounted")

        # 5: mount virtual fs (proc/sys/dev) for chroot operations
        _emit(PHASE_VIRTUAL_FS, 4, "mounting virtual filesystems")
        hooks.mount_virtual_fs(target)
        result.phase_completed = PHASE_VIRTUAL_FS
        _emit(PHASE_VIRTUAL_FS, 5, "virtual fs mounted")

        # 6: install packages (queue-threaded for supersede ordering)
        _emit(PHASE_PACKAGES, 5,
              f"installing {len(cfg['package_groups'])} group(s)")

        def _pkg_progress(current, total_pkgs, name):
            # Per-package fanout — caller can render package-level UI by
            # filtering on phase==PHASE_PACKAGES. current/total here is the
            # per-package count, not the phase count, hence the alternate
            # callback shape.
            if progress_callback:
                progress_callback(PHASE_PACKAGES, current, total_pkgs, name)

        ok_count, fail_count, failed = packages.install_packages(
            target,
            archive_dir,
            cfg["package_groups"],
            package_dir=packages_dir,
            progress_callback=_pkg_progress,
        )
        result.package_success_count = ok_count
        result.package_fail_count = fail_count
        result.failed_packages = failed
        if fail_count:
            _emit(PHASE_PACKAGES, ok_count + fail_count,
                  f"{fail_count} package(s) failed; continuing")
        result.phase_completed = PHASE_PACKAGES
        _emit(PHASE_PACKAGES, 6, f"{ok_count} packages installed")

        # 7: system config
        _emit(PHASE_CONFIG, 6, "generating system config")
        config.generate_all(
            target, partitions,
            hostname=cfg["hostname"],
            locale=cfg["locale"],
            keymap=cfg.get("keymap", "us"),
            timezone=cfg["timezone"],
        )
        result.phase_completed = PHASE_CONFIG
        _emit(PHASE_CONFIG, 7, "system config written")

        # 8: users (root + first user)
        _emit(PHASE_USERS, 7, "configuring root + user accounts")
        users.set_root_password(target, install_io["root_password"])
        users.create_user(
            target,
            install_io["username"],
            install_io["user_password"],
            groups=install_io.get("user_groups"),
        )
        result.phase_completed = PHASE_USERS
        _emit(PHASE_USERS, 8, "accounts configured")

        # 9: MOK keypair (EFI only — bootloader needs it to sign GRUB)
        if efi:
            _emit(PHASE_MOK, 8, "generating MOK keypair (Secure Boot)")
            mok_keypair = mok.generate_mok_keypair(target)
            result.phase_completed = PHASE_MOK
            _emit(PHASE_MOK, 9, "MOK keypair generated")
        else:
            _emit(PHASE_MOK, 9, "MOK skipped (BIOS install)")
            result.phase_completed = PHASE_MOK

        # 10: bootloader (signs binaries with mok_keypair on EFI)
        _emit(PHASE_BOOTLOADER, 9, "installing bootloader")
        bootloader.install_bootloader(
            target,
            install_io["disk"],
            partitions,
            mok_keypair=mok_keypair,
        )
        result.phase_completed = PHASE_BOOTLOADER
        _emit(PHASE_BOOTLOADER, 10, "bootloader installed")

        # 11: post-install hooks
        _emit(PHASE_HOOKS, 10, "running post-install hooks")
        hooks.run_post_install_hooks(
            target, packages_dir,
            progress_callback=lambda i, t, n: (
                progress_callback(PHASE_HOOKS, i, t, n)
                if progress_callback else None
            ),
        )
        result.phase_completed = PHASE_HOOKS
        _emit(PHASE_HOOKS, 11, "post-install hooks complete")

        # 12: services
        _emit(PHASE_SERVICES, 11, "enabling services")
        users.enable_services(target)
        result.phase_completed = PHASE_SERVICES
        _emit(PHASE_SERVICES, 12, "services enabled")

        # MOK enrollment last — failure here leaves system bootable; user
        # can re-enroll via mokutil from running install if needed.
        if efi and install_io.get("mok_password") and mok_keypair:
            _emit(PHASE_MOK, 12, "queueing MOK enrollment for first boot")
            mok.queue_mok_enrollment(
                target,
                mok_keypair["der_path"],
                install_io["mok_password"],
            )
            _emit(PHASE_MOK, 12, "MOK enrollment queued")

        # 13: cleanup (unmount in reverse + copy integrity audit log to target)
        _emit(PHASE_CLEANUP, 12, "unmounting target")
        if verify_config is not None:
            try:
                integrity.copy_audit_log_to_target(
                    Path(verify_config.audit_log_path), Path(target)
                )
            except Exception:
                # Don't fail the install over an audit-log copy issue;
                # the live log still exists in the install environment.
                pass
        hooks.unmount_virtual_fs(target)
        disks.unmount_target(target)
        result.phase_completed = PHASE_CLEANUP
        result.success = True
        _emit(PHASE_CLEANUP, 13, "install complete")

    except Exception as e:
        result.error_message = f"{type(e).__name__}: {e}"
        # Best-effort cleanup based on how far we got. Don't mask the
        # original error if cleanup itself fails.
        try:
            if result.phase_completed in _PHASES_NEEDING_VIRTFS_UNMOUNT:
                hooks.unmount_virtual_fs(target)
        except Exception:
            pass
        try:
            if result.phase_completed in _PHASES_NEEDING_UNMOUNT:
                disks.unmount_target(target)
        except Exception:
            pass

    return result
