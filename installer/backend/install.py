# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

from . import bootloader, config, disks, hooks, integrity, mok, packages, trace, users


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
    integrity_manifest_entry_count / integrity_archives_checked: how many
                     archives the signed manifest promised, and how many were
                     actually found and hashed. Reported together, always:
                     either number alone reads as a complete check.
    integrity_missing_archives: manifest entries with no archive on the media.
                     Non-empty only when the user explicitly overrode a short
                     media; the install then carries a recorded gap and the
                     done screen has to say so.
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
    integrity_manifest_entry_count: int = 0
    integrity_archives_checked: int = 0
    integrity_missing_archives: list = field(default_factory=list)
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


def preflight_check_install_set_complete(cfg, archive_dir, packages_dir,
                                         detected_vendors=None):
    """Silent-loss net: assert every staged archive that belongs to a selected
    tier actually makes it into the resolved install set (hardware-gated drops
    excepted). Raises BEFORE PHASE_PARTITION so a silently-dropped declared
    package fails LOUD and named, never producing a silently-incomplete system.

    This is the install-set audit earmarked after the llama-cpp engine drop and
    the websockets nested-dir drop (G3-10): a declared package that does not
    install is a defect, not a "no functional impact" footnote. Operates on the
    SAME authoritative discovery (igos-build's rglob + yaml `name:` contract)
    the resolver now uses, so it is sound against dir-name / nesting mismatch.

    Raises:
        RuntimeError naming every selected-tier archive that would be dropped.
    """
    selected_groups = cfg.get("package_groups", []) or []
    if not selected_groups:
        return
    gap = packages.compute_install_set_gap(
        selected_groups, archive_dir, packages_dir, detected_vendors
    )
    if gap:
        listed = ", ".join(f"{name}-{ver}" for name, ver in gap)
        raise RuntimeError(
            f"install-set integrity check failed: {len(gap)} package(s) are "
            f"staged on this ISO and belong to a selected tier, yet would NOT "
            f"install: {listed}. This is a silent-loss bug (the install set "
            f"must install what it declares). Aborting before any disk write. "
            f"(archive_dir={archive_dir!r}, packages_dir={packages_dir!r})"
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
        from installer.backend import disks as _disks_mod
        if not _disks_mod.EXPERIMENTAL_UNLOCK_OFFERED:
            errors.append(
                "install_io requests tpm2_enabled / fido2_enabled, but the "
                "EXPERIMENTAL unlock methods are not offered in this release "
                "(functionality review 2026-08-08: enrollment/unseal defects; "
                "see installer/backend/disks.py EXPERIMENTAL_UNLOCK_OFFERED). "
                "Remove the flags — LUKS with passphrase unlock is unaffected."
            )
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
                verify_config=None, cancel_event=None, install_id=None):
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

    # Verbose forensic trace (FORGE_DEBUG_VERBOSE=1 enables). The /tmp sink
    # opens immediately so the validate/verify/partition phases get traced;
    # the durable target-side sink attaches after PHASE_MOUNT (target isn't
    # mounted yet here).
    trace.init_trace(runid=install_id or "")
    trace.trace_event("run_install_entry",
                      install_id=install_id, target=str(target), dry_run=dry_run,
                      yaml_path=str(yaml_path),
                      install_io_keys=sorted(install_io.keys()))

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
            # Silent-loss net: every selected-tier staged archive must resolve
            # into the install set (hardware-gated drops excepted) — else raise
            # before PHASE_PARTITION. Catches the websockets/llama-cpp class.
            preflight_check_install_set_complete(cfg, archive_dir, packages_dir)

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
            # Both counts travel onto the result whatever the outcome, because
            # either one alone reads as a complete check.
            result.integrity_manifest_entry_count = getattr(
                verify_result, "manifest_entry_count", 0)
            result.integrity_archives_checked = getattr(
                verify_result, "archives_checked", 0)
            result.integrity_missing_archives = list(
                getattr(verify_result, "missing_archives", []) or [])
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
            _emit(PHASE_VERIFY, 2,
                  f"archives verified: {result.integrity_archives_checked} of "
                  f"{result.integrity_manifest_entry_count} manifest "
                  f"entries{override_msg}")
            if result.integrity_missing_archives:
                # An install the user chose to take with a known gap. It must
                # be on the done screen, not only in the log: nothing later
                # will tell them which software is not there.
                missing = result.integrity_missing_archives
                shown = ", ".join(missing[:5])
                more = (f" and {len(missing) - 5} more"
                        if len(missing) > 5 else "")
                result.warnings.append(
                    f"{len(missing)} package archive(s) the signed manifest "
                    f"promised were not on the install media and you chose to "
                    f"install anyway: {shown}{more}. The full list is in the "
                    f"integrity log copied onto this system."
                )
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
        # Target is now mounted — attach durable trace sink. Events written
        # from here on land on the target disk (survives unmount + reboot).
        trace.attach_target_sink(Path(target))
        _emit(PHASE_MOUNT, 4, "target mounted")

        _check_cancel(PHASE_VIRTUAL_FS)

        # 5: mount virtual fs (proc/sys/dev) for chroot operations
        _emit(PHASE_VIRTUAL_FS, 4, "mounting virtual filesystems")
        hooks.mount_virtual_fs(target)
        result.phase_completed = PHASE_VIRTUAL_FS
        _emit(PHASE_VIRTUAL_FS, 5, "virtual fs mounted")

        # 5a: pre-PACKAGES UKI prereqs.
        # The linux-kernel package's `/var/lib/pkm/hooks/linux-kernel/
        # post-install` script (D-005 Phase A — runs `ukify build` +
        # sign-with-user-MOK) fires DURING pkm install of linux-kernel
        # (PHASE_PACKAGES). It needs /etc/kernel/cmdline (UKI source-of-
        # truth) and /var/lib/intergen/mok/* (user MOK keypair for UKI
        # signing) to exist at fire time. Pre-staging both BEFORE
        # PHASE_PACKAGES so the hook produces a signed UKI on first
        # fire rather than gracefully exiting without one and dropping
        # to grub-loads-vmlinuz.
        #
        # Surfaced 2026-05-27 install #27: D-005 UKI primary path
        # never produced a UKI on the ESP because both prereqs were
        # generated AFTER packages. Decided 2026-05-27
        # ratification: UKI primary is the directive; graceful-degrade-
        # on-every-install is a defect, not a design.
        #
        # Composition: packages.INSTALL_ORDER_LATE moves linux-kernel
        # to install AFTER ukify (systemd-pass2) and sbsign (sbsigntool)
        # are deployed on /mnt/target. Together with this pre-stage,
        # the hook fires with all four prereqs present (ukify, sbsign,
        # /etc/kernel/cmdline, MOK material) and produces a signed UKI.
        mok_keypair = None
        _emit(PHASE_CONFIG, 5, "pre-staging UKI prereqs (cmdline + MOK)")
        config.generate_kernel_cmdline(target, partitions)
        if efi:
            mok_keypair = mok.generate_mok_keypair(target)
            _emit(PHASE_CONFIG, 5,
                  f"MOK keypair generated pre-packages "
                  f"(key={mok_keypair['key_path']})")

        # Pre-stage the microcode helper + minimal initramfs.img +
        # /boot/{intel,amd}-ucode.img so the linux-kernel post_install
        # hook (which fires during PHASE_PACKAGES while ukify is being
        # installed) can produce a UKI with .initrd AND .ucode sections.
        # Surfaced 2026-05-27 install #28 trace, anomaly A: UKI was built
        # with only .linux/.cmdline/.osrel/.sbat/.sdmagic — no .initrd
        # or .ucode because their source files didn't exist on the
        # target at hook fire time.
        if efi:
            _emit(PHASE_CONFIG, 5,
                  "pre-staging UKI prereqs (microcode + initramfs)")
            bootloader.stage_uki_prereqs(target)

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

        ok_count, fail_count, failed, installed_names = packages.install_packages(
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

        # Ingest the kernel post_install hook's diagnostic log into the
        # Forge trace so operators can see exactly which ukify cmdline
        # fired, what ukify produced, and which UKI sections landed.
        # Hook output is otherwise swallowed by pkm — only the one-line
        # "hook[depmod] OK" summary survives into Forge's trace via the
        # package_install event. Surfaced 2026-05-27 install #28 trace,
        # anomaly A: UKI built with missing sections but no visibility
        # into why. Fail-open: missing/unreadable log emits a sentinel
        # event and continues.
        if efi:
            bootloader.ingest_kernel_hook_log(target)

        _check_cancel(PHASE_CONFIG)

        # 7: system config
        _emit(PHASE_CONFIG, 6, "generating system config")
        config.generate_all(
            target, partitions,
            hostname=cfg["hostname"],
            locale=cfg["locale"],
            keymap=cfg.get("keymap", "us"),
            timezone=cfg["timezone"],
            # Option C 2026-05-24: thread the user's dual-boot detection
            # choice into /etc/default/grub. Default True (most users
            # want os-prober to find Windows/other-Linux entries on
            # adjacent partitions). User opted out → permanent
            # GRUB_DISABLE_OS_PROBER=true ships in the installed system.
            detect_other_oses=install_io.get("detect_other_oses", True),
        )

        # 7b: Wi-Fi carry (2026-07-11): thread the user's Confirm-screen
        # choice. Tri-state per the D1/r15 shape — an ABSENT key means the
        # ask never rendered (no active Wi-Fi at confirm, or the probe was
        # inconclusive), so nothing is carried; False is an explicit opt-out.
        # The trace records the outcome in every branch so a missing/extra
        # profile on the installed system is always explainable from the
        # forensic trace alone.
        carry_wifi = install_io.get("carry_wifi")
        if carry_wifi:
            _emit(PHASE_CONFIG, 6, "carrying Wi-Fi connection profiles")
            wifi_result = config.carry_wifi_connections(target)
        else:
            wifi_result = {"carried": [], "skipped": {}, "normalized": {}}
        trace.trace_event("wifi_carry",
                          asked=carry_wifi is not None,
                          enabled=bool(carry_wifi),
                          carried=wifi_result["carried"],
                          skipped=wifi_result["skipped"],
                          normalized=wifi_result.get("normalized", {}))

        result.phase_completed = PHASE_CONFIG
        _emit(PHASE_CONFIG, 7, "system config written")

        _check_cancel(PHASE_USERS)

        # 8: users (root + first user)
        _emit(PHASE_USERS, 7, "configuring root + user accounts")
        # Secrets passed by NAME so the trace redactor scrubs them at the
        # kwargs layer too — the positional-name redaction in igos_trace is
        # the enforcement; this is defense-in-depth (PI-ge9b04-D).
        users.set_root_password(target, password=install_io["root_password"])
        users.create_user(
            target,
            install_io["username"],
            password=install_io["user_password"],
            groups=install_io.get("user_groups"),
        )
        result.phase_completed = PHASE_USERS
        _emit(PHASE_USERS, 8, "accounts configured")

        _check_cancel(PHASE_MOK)

        # 9: MOK keypair (EFI only — bootloader needs it to sign GRUB).
        # NOTE: MOK is now pre-staged at PHASE_VIRTUAL_FS+ for UKI hook
        # consumption during PHASE_PACKAGES. This block is the safety
        # net for any edge case where the pre-stage was skipped (it
        # shouldn't be, given the unconditional `mok.generate_mok_
        # keypair(target)` call above for EFI installs); the existing
        # `mok_keypair is None` check covers the BIOS install path.
        if efi:
            if mok_keypair is None:
                _emit(PHASE_MOK, 8, "generating MOK keypair (Secure Boot)")
                mok_keypair = mok.generate_mok_keypair(target)
                _emit(PHASE_MOK, 9, "MOK keypair generated (late path)")
            else:
                _emit(PHASE_MOK, 9,
                      "MOK keypair already generated pre-packages "
                      "(used by linux-kernel UKI hook)")
            result.phase_completed = PHASE_MOK
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
            # Option C 2026-05-24: thread the user's choice through to
            # the install-time grub-mkconfig invocation. Matches the
            # permanent /etc/default/grub written by config.generate_all.
            detect_other_oses=install_io.get("detect_other_oses", True),
            # D1 / work-plan 1.25: whether InterGenOS becomes the default UEFI
            # boot target. Absent key -> True (historical prepend) so BIOS
            # installs and single-OS EFI installs are unchanged; the frontend
            # only sets it (possibly False) when a foreign OS entry is detected.
            make_default_boot=install_io.get("make_default_boot", True),
        )

        # Gap B (FDE Phase-D): on a LUKS install, rebuild the UKI with the REAL
        # FDE initramfs now that /etc/crypttab exists (written in PHASE_CONFIG)
        # and the FDE scripts are staged. The PHASE_PACKAGES UKI shipped the stub
        # initramfs (crypttab didn't exist yet), which cannot unlock the root.
        # No-op on plain installs; fail-closed on LUKS (a UKI that can't unlock
        # is worse than a halted install). Runs before the PHASE_HOOKS checksum
        # reconcile so the rebuilt UKI state is reconciled.
        if efi:
            _emit(PHASE_BOOTLOADER, 10, "rebuilding FDE UKI (LUKS unlock)")
            bootloader.rebuild_fde_uki(target, partitions)

        result.phase_completed = PHASE_BOOTLOADER
        _emit(PHASE_BOOTLOADER, 10, "bootloader installed")

        _check_cancel(PHASE_HOOKS)

        # 11: post-install hooks
        _emit(PHASE_HOOKS, 10, "running post-install hooks")
        hooks.run_post_install_hooks(
            target, packages_dir,
            installed_names=installed_names,
            progress_callback=lambda i, t, n: (
                progress_callback(PHASE_HOOKS, i, t, n)
                if progress_callback else None
            ),
        )
        result.phase_completed = PHASE_HOOKS
        _emit(PHASE_HOOKS, 11, "post-install hooks complete")

        # PKM-E: signing (PHASE_BOOTLOADER) + post-install hooks have now mutated
        # some installed files AFTER pkm recorded their archive hashes — the
        # MOK-signed kernel/UKI, hook-edited .desktop/headers/XML-catalogs. Re-
        # record those checksums from the live filesystem so `pkm verify`
        # validates the true installed state (preserving tamper detection) rather
        # than false-flagging legitimate post-install mutations. Non-fatal: a
        # reconcile error must not abort an otherwise-complete install.
        try:
            reconciled = packages.reconcile_checksums(
                target, installed_names=installed_names)
            _emit(PHASE_HOOKS, 11,
                  f"reconciled {reconciled} file checksums to installed state")
        except Exception as exc:
            _emit(PHASE_HOOKS, 11, f"warning: checksum reconcile skipped ({exc})")

        _check_cancel(PHASE_SERVICES)

        # 12: services
        _emit(PHASE_SERVICES, 11, "enabling services")
        users.enable_services(target)

        # Greeter monitor-layout sync (decided 2026-07-21): enable the
        # templated instance for the wizard-created primary user — the
        # instance name cannot live in a preset (username unknown at
        # package build time), so PHASE_SERVICES enables it here.
        users.enable_greeter_monitor_sync(target, install_io["username"])

        # Boot-order checker: the installer registers a UEFI boot entry and
        # states it is first, but firmware can and does reorder NVRAM at the
        # next boot. The unit re-checks that at every boot and restores the
        # registered entry when this install recorded it as the default.
        # Enabled after preset-all above, whose catch-all would revert it.
        users.enable_bootorder_check(target)

        # Pre-configuration greeter seed: derive a single-primary monitor
        # layout from the live session's own display state so the target's
        # FIRST greeter renders on one monitor at the right mode — the sync
        # above only takes over once the user saves a session layout.
        # Best-effort by design (skip is traced): a headless or serial-TUI
        # install has no session compositor to read.
        users.seed_greeter_monitor_layout(target)

        # The SAME layout seeds the created user's ~/.config/monitors.xml:
        # the user's first login otherwise races monitor settling against
        # the first background paint (solid-color desktop + mis-thrown
        # windows on a multi-GPU box, measured 2026-07-31) — a stored
        # configuration is applied before painting and closes the window.
        # Best-effort like the greeter seed; ownership from target passwd.
        users.seed_user_monitor_layout(target, install_io["username"])

        # D-010 InterGen AI opt-in: per the recorded requirement, the
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
            # Defence-in-depth against the silent-AI-absent class: the user
            # asked for the assistant, so its binaries MUST be on the target
            # (the 'ai' package group is force-coupled to this opt-in in both
            # the GUI Packages screen and the TUI walking sequence). If
            # /usr/bin/intergen is missing, the enable below would silently
            # no-op on an absent unit (`|| true`) and the AI would be GONE on
            # first boot with no feedback -- surface that loudly instead.
            if not (Path(target) / "usr/bin/intergen").exists():
                msg = ("InterGen AI was requested but its binaries are not on "
                       "the target (the 'ai' package group was not applied); "
                       "the assistant will be ABSENT on first boot")
                result.warnings.append(msg)
                _emit(PHASE_SERVICES, 11, f"warning: {msg}")
            else:
                hooks.run_chroot(
                    target,
                    "systemctl --global enable intergen.service 2>/dev/null || true",
                )
                # Opt-in (D-010): enable the AI panel GNOME extension so the
                # top-bar icon + Super+I appear on first login. The default
                # gschema override deliberately omits intergen@intergenos.org;
                # this writes a higher-priority 95 override derived from the
                # default list + recompiles the schemas. Never abort the install
                # over this.
                try:
                    if config.enable_ai_panel_extension(target):
                        hooks.run_chroot(
                            target,
                            "glib-compile-schemas "
                            "/usr/share/glib-2.0/schemas 2>/dev/null || true",
                        )
                        _emit(PHASE_SERVICES, 11,
                              "InterGen AI panel extension enabled (opt-in)")
                except Exception as e:  # noqa: BLE001 — diagnostics, not fatal
                    wmsg = f"could not enable the AI panel extension: {e}"
                    result.warnings.append(wmsg)
                    _emit(PHASE_SERVICES, 11, f"warning: {wmsg}")

        # D-019 SSH server opt-in (amends D-007 sshd-default arm): the
        # SSH server is opt-in via the Forge UI. YES enables sshd.service
        # AND adds a TCP/22 accept rule to /etc/nftables.conf so the
        # server is actually reachable from the network (matching user
        # intent; the D-011 default-deny firewall would otherwise leave
        # an opt-in SSH server unreachable). NO leaves both the service
        # disabled AND the firewall port closed. User can opt in later
        # by running `systemctl enable --now sshd` + adding a TCP/22
        # accept rule to /etc/nftables.conf.
        if install_io.get("ssh_server_enable"):
            _emit(PHASE_SERVICES, 11, "enabling SSH server (opt-in)")
            # Optional public-key + keys-only posture (decided
            # 2026-05-22 Option C; sshd-password-auth audit closure).
            # When the user pasted a public key in the Forge UI, install
            # authorized_keys + ship the keys-only sshd drop-in. Username
            # comes from install_io (set by Forge prior to PHASE_USERS).
            users.enable_ssh_server(
                target,
                username=install_io.get("username"),
                public_key=install_io.get("ssh_public_key"),
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
        elif efi and not install_io.get("mok_password"):
            # D2 / work-plan 1.22 (PI-Z18): EFI install with MOK enrollment
            # skipped. Staging is (correctly) skipped — a blank MOK password
            # stays a valid choice — but record the decision + the current
            # Secure Boot state so the trace shows the future-SB-flip footgun
            # (a later Secure-Boot enable would need a manual `mokutil --import`)
            # was a surfaced, known choice, not a silent one. The Confirm screen
            # (GUI) and the MOK prompt (TUI) state that consequence loudly. Not
            # an error: the system boots fine as installed.
            from .secureboot import is_secure_boot_enabled
            trace.trace_event(
                "mok_enrollment_skipped_efi",
                secure_boot_enabled=is_secure_boot_enabled(),
                consequence="if Secure Boot is later enabled, first boot will "
                            "require a manual `mokutil --import` of the MOK cert",
                intent="D2/1.22 — user left the MOK password blank on an EFI "
                       "install; consequence surfaced pre-install, staging "
                       "intentionally skipped, blank is a valid choice",
            )

        # Defense-in-depth: guarantee no LFS `tester` test account ships on the
        # installed system, regardless of which archive post-install hooks ran
        # (shadow's post_install no longer creates it, but it leaked once on the
        # GBC001.5 install). Runs while the target is still mounted. A survivor
        # is surfaced as a warning, not a hard failure on an otherwise-complete
        # install.
        try:
            scrub = users.remove_test_accounts(target)
            if scrub["removed"]:
                _emit(PHASE_CLEANUP, 12,
                      f"scrubbed stray test account(s): "
                      f"{', '.join(scrub['removed'])}")
            if scrub["survivors"]:
                msg = (f"LFS test account(s) {scrub['survivors']} could not be "
                       f"removed from the target")
                result.warnings.append(msg)
                _emit(PHASE_CLEANUP, 12, f"warning: {msg}")
        except Exception as e:
            msg = f"test-account scrub failed ({type(e).__name__}: {e})"
            result.warnings.append(msg)
            _emit(PHASE_CLEANUP, 12, f"warning: {msg}")

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
                missing_trust = integrity.copy_signed_manifest_to_target(
                    Path(verify_config.manifest_path),
                    Path(verify_config.public_key_path),
                    Path(target),
                )
                if missing_trust:
                    # An incomplete trust set on the target means the
                    # post-install smoke check can't fully revalidate the
                    # chain. Surface it loudly + in the trace rather than
                    # letting the missing files skip in silence (§4C).
                    msg = (
                        f"incomplete trust set copied to target "
                        f"(missing: {', '.join(missing_trust)}); post-install "
                        f"sign/manifest smoke check will be partial"
                    )
                    result.warnings.append(msg)
                    _emit(PHASE_CLEANUP, 12, f"warning: {msg}")
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

    finally:
        # Always close trace sinks. Final event captures the outcome so the
        # log tail tells the operator if the install completed, was cancelled,
        # or errored.
        trace.trace_event(
            "run_install_exit",
            success=result.success,
            cancelled=result.cancelled,
            phase_completed=result.phase_completed,
            error_message=result.error_message,
            package_success_count=result.package_success_count,
            package_fail_count=result.package_fail_count,
        )
        trace.close_trace()

    return result
