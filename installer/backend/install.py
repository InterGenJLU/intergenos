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

import shutil
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

# C-021 pre-flight (live-ISO PATH check before PHASE_PARTITION). These are
# binaries the install pipeline invokes directly from the LIVE env (not via
# run_chroot into target — those are validated by the M-002 chroot-binary-
# presence gate at build time). Missing them means the live ISO is broken
# and the install would die mid-partition with cryptic stderr; pre-flight
# catches it BEFORE the destructive disk write.
PREFLIGHT_LIVE_BINARIES_ALWAYS = (
    "parted", "wipefs",
    "mkfs.ext4", "mkfs.fat",
    "blkid", "mount", "umount", "chroot", "lsblk",
)
PREFLIGHT_LIVE_BINARIES_LUKS = ("cryptsetup",)

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


class _CancelRequested(Exception):
    """Sentinel raised inside run_install when cancel_event has been set.

    args[0] is the PHASE_* string of the phase boundary that observed the
    cancel. Caught by run_install's outer except to populate
    InstallResult.cancelled + run the same best-effort cleanup the
    generic-failure path runs.
    """


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
    warnings: list of human-readable non-fatal warning strings collected
                     during the install. Frontends should render these to
                     the user on the done screen even when success=True.
                     Examples: audit-log copy failed during cleanup; MOK
                     enrollment queueing failed but system is bootable.
    cancelled: True iff the install was cancelled via the cancel_event arg
                     before completion. When True, success is False and
                     phase_completed names the last phase that finished
                     before the cancel was honored. error_message names
                     the phase boundary at which the cancel landed.
    """
    success: bool
    phase_completed: Optional[str] = None
    error_message: Optional[str] = None
    failed_packages: list = field(default_factory=list)
    package_success_count: int = 0
    package_fail_count: int = 0
    integrity_overrides_granted: int = 0
    integrity_aborted_at: Optional[str] = None
    warnings: list = field(default_factory=list)
    cancelled: bool = False


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


def preflight_check_binaries(install_io):
    """C-021 pre-flight: verify required binaries are on the live-ISO PATH.

    Called from PHASE_VALIDATE (before PHASE_PARTITION). PHASE_PARTITION
    onward invokes parted/wipefs/mkfs.*/cryptsetup directly via subprocess
    on the live env. Missing binaries surface as "command not found"
    wrapped in mid-phase RuntimeError with cryptic stderr; this pre-flight
    catches them upfront so the operator gets a clear "live ISO is broken"
    message BEFORE any destructive write to the target disk.

    Conditional sets:
      - PREFLIGHT_LIVE_BINARIES_LUKS adds cryptsetup when luks_enabled
        (D-001 LUKS-at-install opt-in)

    Target-chroot binaries (efibootmgr / mokutil / sbsign / openssl /
    localedef / chpasswd / etc.) are NOT checked here — they live in the
    target chroot via packages installed during PHASE_PACKAGES + are
    validated at build time by the M-002 chroot-binary-presence gate
    (scripts/check-installer-runtime-deps.py).

    Raises:
        RuntimeError listing every missing binary if any are absent.
    """
    required = set(PREFLIGHT_LIVE_BINARIES_ALWAYS)
    if install_io.get("luks_enabled"):
        required.update(PREFLIGHT_LIVE_BINARIES_LUKS)
    missing = sorted(b for b in required if shutil.which(b) is None)
    if missing:
        raise RuntimeError(
            f"live-ISO is missing required installer-runtime binaries: "
            f"{', '.join(missing)}. This live media cannot drive an install — "
            f"re-create the ISO from a build that includes all "
            f"installer-runtime packages (T0-3 sub-cluster 1)."
        )


def preflight_check_archive_availability(cfg, archive_dir, packages_dir):
    """C-021 (extended): verify each selected package group resolves to >=1 archive.

    Composes with C-065's PHASE_PACKAGES hard-fail (belt-and-suspenders):
    C-065 catches the empty-archive-dir case AFTER PHASE_PARTITION has
    already modified the target disk. This pre-flight catches the
    per-group case BEFORE any destructive write — operator can fix the
    live ISO without recovering from a partial install.

    Per windows-docs-coordinator 2026-05-19T01:07:32Z peer-review proposal
    on the T0-3 sub-cluster 3 plan (absorbed into C-021 scope at this
    commit per feedback_audit_multi_wiring_lands_single_commit).

    Raises:
        RuntimeError listing every group with zero matching archives.
    """
    selected_groups = cfg.get("package_groups", []) or []
    empty_groups = []
    for group in selected_groups:
        # get_group_packages returns [(name, version, archive_path), ...].
        # Empty list = no archives matched (either group unknown to GROUPS,
        # tier dir absent, or no archives in archive_dir for the tier).
        result = packages.get_group_packages(
            [group], archive_dir, packages_dir
        )
        if not result:
            empty_groups.append(group)
    if empty_groups:
        raise RuntimeError(
            f"selected package group(s) {empty_groups!r} resolve to zero "
            f"archives on this live ISO (archive_dir={archive_dir!r}, "
            f"packages_dir={packages_dir!r}). Either pick a different "
            f"group set or use an ISO that includes the missing tier(s)."
        )


def validate_install_inputs(cfg, install_io):
    """Validate yaml + install_io. Aggregates errors; raises ValueError once
    listing every problem so frontends can surface them together rather than
    one-at-a-time loop with the user."""
    errors = []

    for field_name in REQUIRED_YAML_FIELDS:
        if field_name not in cfg:
            errors.append(f"yaml missing required field: {field_name}")

    if "hostname" in cfg:
        # Defensive validation even after frontend re-prompt: a hand-edited
        # install.yaml that bypasses the TUI/GUI must still be rejected
        # before /etc/hosts generation. Same validator the frontends call.
        from ._validators import validate_hostname
        err = validate_hostname(cfg["hostname"])
        if err:
            errors.append(f"yaml hostname invalid: {err}")

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

    # D-001 LUKS opt-in: when luks_enabled is truthy, luks_passphrase
    # MUST be present + non-empty. Pre-flight cryptsetup-available check
    # lives in disks.partition_disk so the live media's actual state is
    # tested — we only validate the frontend contract here.
    if install_io.get("luks_enabled"):
        if not install_io.get("luks_passphrase"):
            errors.append(
                "install_io luks_enabled=True but luks_passphrase missing/empty "
                "(D-001 LUKS-at-install contract: frontend MUST capture + confirm "
                "passphrase before invoking the backend)"
            )

    # D-001 EXPERIMENTAL TPM2 / FIDO2 unlock methods compose with LUKS:
    # enabling either without luks_enabled is incoherent (no LUKS
    # keyslot to add the derived key to). Hardware + tools pre-flight
    # happens in disks.partition_disk where the live state can be
    # tested.
    if install_io.get("tpm2_enabled") or install_io.get("fido2_enabled"):
        if not install_io.get("luks_enabled"):
            errors.append(
                "install_io tpm2_enabled / fido2_enabled require luks_enabled=True "
                "(EXPERIMENTAL unlock methods bind to LUKS slots)"
            )

    if errors:
        raise ValueError(
            "install validation failed:\n  - " + "\n  - ".join(errors)
        )


def run_install(yaml_path, install_io, archive_dir, packages_dir=None,
                progress_callback=None, dry_run=False, target=DEFAULT_TARGET,
                verify_config=None, cancel_event=None):
    """Run the full Forge install pipeline.

    Args:
        yaml_path: path to install.yaml (TUI-emitted or GUI-emitted).
        install_io: dict with 'disk', 'username', 'user_password',
                    'root_password' (required); 'mok_password',
                    'user_groups' (optional).
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
                       this — anti-supply-chain v1.0 ship-gate.
        cancel_event: optional threading.Event (or any object with .is_set()
                      method). Polled at every phase boundary. When set,
                      the orchestrator returns early with
                      InstallResult(cancelled=True). Cancellation granularity
                      is phase-boundary, not mid-phase — once a destructive
                      phase has started (PHASE_PARTITION onward), the
                      operation in flight completes before cancel is honored.
                      None disables cancellation (TUI + headless flows pass
                      None; the GUI passes a threading.Event tied to the
                      Cancel button).

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

    def _check_cancel(at_phase):
        """Raise _CancelRequested if cancel_event has been set.

        Called at every phase boundary except PHASE_CLEANUP (cleanup must
        always run to unmount the target safely, regardless of cancel
        intent). Exception path routes through the outer except block
        which performs best-effort unmount based on `result.phase_completed`.
        """
        if cancel_event is not None and cancel_event.is_set():
            raise _CancelRequested(at_phase)

    partitions = None
    mok_keypair = None

    try:
        # 1: validate
        _emit(PHASE_VALIDATE, 0, "loading + validating install config")
        cfg = load_yaml_config(yaml_path)
        validate_install_inputs(cfg, install_io)

        # C-021: live-ISO PATH pre-flight + per-group archive availability.
        # Both raise BEFORE PHASE_PARTITION so a broken live ISO or empty
        # archive_dir is caught without modifying the target disk. Composes
        # with C-065's PHASE_PACKAGES hard-fail (belt-and-suspenders for
        # the unlikely case the pre-flight is bypassed).
        _emit(PHASE_VALIDATE, 1, "pre-flight: live-ISO binaries + archive availability")
        preflight_check_binaries(install_io)
        if archive_dir:
            preflight_check_archive_availability(cfg, archive_dir, packages_dir)

        result.phase_completed = PHASE_VALIDATE
        _emit(PHASE_VALIDATE, 1, "config valid")

        _check_cancel(PHASE_VERIFY)

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

        _check_cancel(PHASE_PARTITION)

        # 3: partition + format (partition_disk does both)
        # NOTE: this is the last chance to cancel before any destructive
        # disk write. Once partition_disk runs, the target disk is
        # modified and cancel-cleanup cannot undo the change.
        _emit(PHASE_PARTITION, 2, f"partitioning {install_io['disk']}")
        efi = disks.is_efi()
        if efi:
            # C-003 pre-flight: shim-signed binaries must exist on the
            # live ISO before we touch the target disk. Fails closed if
            # the live ISO build pipeline regressed (audit A-001). Raises
            # RuntimeError so the destructive partition_disk() never runs.
            bootloader.verify_shim_assets_present()
        # D-001 LUKS opt-in: pass through luks_enabled + luks_passphrase
        # if frontend captured them. partition_disk's pre-flight rejects
        # malformed combinations (luks_enabled with empty passphrase, or
        # luks_enabled without cryptsetup on PATH) before any disk write.
        # NB: do NOT log luks_passphrase; the _emit message logs only the
        # disk + the LUKS-enabled flag.
        luks_enabled = bool(install_io.get("luks_enabled"))
        tpm2_enabled = bool(install_io.get("tpm2_enabled"))
        fido2_enabled = bool(install_io.get("fido2_enabled"))
        if luks_enabled:
            extra = []
            if tpm2_enabled:
                extra.append("TPM2-EXPERIMENTAL")
            if fido2_enabled:
                extra.append("FIDO2-EXPERIMENTAL")
            tail = f" + {' + '.join(extra)}" if extra else ""
            _emit(PHASE_PARTITION, 2,
                  f"LUKS opt-in: wrapping root in LUKS2 (argon2id){tail}")

        def _fido2_status(msg):
            _emit(PHASE_PARTITION, 2, f"FIDO2 enrollment: {msg}")

        partitions = disks.partition_disk(
            install_io["disk"],
            efi=efi,
            luks_enabled=luks_enabled,
            luks_passphrase=install_io.get("luks_passphrase") if luks_enabled else None,
            tpm2_enabled=tpm2_enabled,
            fido2_enabled=fido2_enabled,
            fido2_progress_callback=_fido2_status if fido2_enabled else None,
        )
        result.phase_completed = PHASE_PARTITION
        _emit(PHASE_PARTITION, 3, "partitioned + formatted")

        _check_cancel(PHASE_MOUNT)

        # 4: mount target
        _emit(PHASE_MOUNT, 3, f"mounting target {target}")
        disks.mount_target(partitions, target=target)
        result.phase_completed = PHASE_MOUNT
        _emit(PHASE_MOUNT, 4, "target mounted")

        _check_cancel(PHASE_VIRTUAL_FS)

        # 5: mount virtual fs (proc/sys/dev) for chroot operations
        _emit(PHASE_VIRTUAL_FS, 4, "mounting virtual filesystems")
        hooks.mount_virtual_fs(target)
        result.phase_completed = PHASE_VIRTUAL_FS
        _emit(PHASE_VIRTUAL_FS, 5, "virtual fs mounted")

        _check_cancel(PHASE_PACKAGES)

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

        # C-065 hard-fail: if PHASE_PACKAGES installed zero packages despite
        # the user requesting one or more groups, halt the install loudly
        # rather than continuing on to bootloader phase with an empty
        # target ("successful" install with nothing on disk). C-021's
        # extended pre-flight (preflight_check_archive_availability)
        # should catch this case BEFORE PHASE_PARTITION; reaching here
        # indicates a regression or race. The legitimate "0 packages
        # requested" case (empty package_groups) stays no-op — the
        # `cfg.get("package_groups")` truthiness guard handles it.
        if ok_count == 0 and fail_count == 0 and cfg.get("package_groups"):
            raise RuntimeError(
                f"PHASE_PACKAGES installed zero packages despite "
                f"{len(cfg['package_groups'])} package group(s) requested "
                f"({cfg['package_groups']!r}). archive_dir={archive_dir!r} "
                f"resolved to no archives. C-021 pre-flight should have "
                f"caught this at PHASE_VALIDATE; reaching here indicates "
                f"a pre-flight regression or race condition."
            )

        if fail_count:
            _emit(PHASE_PACKAGES, ok_count + fail_count,
                  f"{fail_count} package(s) failed; continuing")
        result.phase_completed = PHASE_PACKAGES
        _emit(PHASE_PACKAGES, 6, f"{ok_count} packages installed")

        _check_cancel(PHASE_CONFIG)

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

        _check_cancel(PHASE_USERS)

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

        _check_cancel(PHASE_MOK)

        # 9: MOK keypair (EFI only — bootloader needs it to sign GRUB)
        if efi:
            _emit(PHASE_MOK, 8, "generating MOK keypair (Secure Boot)")
            mok_keypair = mok.generate_mok_keypair(target)
            result.phase_completed = PHASE_MOK
            _emit(PHASE_MOK, 9, "MOK keypair generated")
        else:
            _emit(PHASE_MOK, 9, "MOK skipped (BIOS install)")
            result.phase_completed = PHASE_MOK

        _check_cancel(PHASE_BOOTLOADER)

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

        _check_cancel(PHASE_HOOKS)

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

        _check_cancel(PHASE_SERVICES)

        # 12: services
        _emit(PHASE_SERVICES, 11, "enabling services")
        users.enable_services(target)

        # D-010 InterGen AI opt-in: per the operator-directive, the
        # AI assistant is opt-in. The Forge prompt at install time
        # (Packages screen — GUI; walking sequence — TUI) writes
        # `intergen_ai_enable` into install_io. The YES path enables
        # the user service in the target's chroot; the NO path leaves
        # the service installed-but-disabled (user can opt in later
        # via `systemctl --user enable intergen.service`). The
        # packages/ai/intergen/build.sh post_install path no longer
        # enables the service unconditionally; the gate enforces this
        # at ISO build time via scripts/check-d010-compliance.sh.
        if install_io.get("intergen_ai_enable"):
            _emit(PHASE_SERVICES, 11, "enabling InterGen AI assistant (opt-in)")
            hooks.run_chroot(
                target,
                "systemctl --global enable intergen.service 2>/dev/null || true",
            )

        result.phase_completed = PHASE_SERVICES
        _emit(PHASE_SERVICES, 12, "services enabled")

        # MOK enrollment last — failure here leaves system bootable; user
        # can re-enroll via mokutil from running install if needed. Catch
        # the failure explicitly + surface as a warning rather than letting
        # it propagate to the outer except, which would mark the install
        # FAILED even though the system IS fully usable. The user can
        # then act on the warning instead of redoing the install.
        if efi and install_io.get("mok_password") and mok_keypair:
            _emit(PHASE_MOK, 12, "queueing MOK enrollment for first boot")
            try:
                mok.queue_mok_enrollment(
                    target,
                    mok_keypair["der_path"],
                    install_io["mok_password"],
                )
                _emit(PHASE_MOK, 12, "MOK enrollment queued")
            except Exception as e:
                msg = (
                    f"MOK enrollment queueing failed "
                    f"({type(e).__name__}: {e}); system IS bootable — "
                    f"re-enroll via mokutil from running install"
                )
                result.warnings.append(msg)
                _emit(PHASE_MOK, 12, f"warning: {msg}")

        # 13: cleanup (unmount in reverse + copy integrity artifacts to target)
        _emit(PHASE_CLEANUP, 12, "unmounting target")
        if verify_config is not None:
            try:
                integrity.copy_audit_log_to_target(
                    Path(verify_config.audit_log_path), Path(target)
                )
            except Exception as e:
                # Don't fail the install over an audit-log copy issue;
                # the live log still exists in the install environment.
                # BUT surface as a warning so the user knows the trust-
                # trail wasn't preserved onto the target — silent loss of
                # the audit log undermines post-incident forensics.
                msg = (
                    f"audit log not copied to target "
                    f"({type(e).__name__}: {e}); review "
                    f"{verify_config.audit_log_path} on the install media "
                    f"manually before retiring it"
                )
                result.warnings.append(msg)
                _emit(PHASE_CLEANUP, 12, f"warning: {msg}")
            # Preserve manifest + signature + release key onto the
            # target's /var/lib/igos/manifest/ so the post-install smoke
            # check (installer/smoke/checks/signing.sh) can revalidate
            # the chain independently of the install media still being
            # around. Best-effort: same failure-mode posture as the
            # audit-log copy above (warning, not install-fail).
            try:
                integrity.copy_signed_manifest_to_target(
                    Path(verify_config.manifest_path),
                    Path(verify_config.public_key_path),
                    Path(target),
                )
            except Exception as e:
                msg = (
                    f"signed manifest not copied to target "
                    f"({type(e).__name__}: {e}); post-install smoke "
                    f"check sign/manifest will skip"
                )
                result.warnings.append(msg)
                _emit(PHASE_CLEANUP, 12, f"warning: {msg}")
        hooks.unmount_virtual_fs(target)
        disks.unmount_target(target)
        result.phase_completed = PHASE_CLEANUP
        result.success = True
        _emit(PHASE_CLEANUP, 13, "install complete")

    except _CancelRequested as cr:
        # User-requested cancel via cancel_event. The phase boundary that
        # observed the cancel is in cr.args[0]; we never started that
        # phase's work, so result.phase_completed correctly names the
        # last phase that DID finish. Same best-effort cleanup as the
        # generic-failure path runs.
        result.cancelled = True
        cancel_at = cr.args[0] if cr.args else "unknown phase"
        result.error_message = f"install cancelled by user at {cancel_at}"
        _emit(cancel_at, total, f"cancelled at {cancel_at}")
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
