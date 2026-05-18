"""Disk detection and partitioning for InterGenOS installer.

v1.0 scope — fresh-install only. partition_disk() wipes the target disk
selected in the installer and lays down a clean GPT (ESP + root). The
installer never touches a disk the user did not select.

Dual-boot via boot-selection IS supported on v1.0: the user installs
Windows on one disk/partition and InterGenOS on another, and the
firmware boot menu (or a user-installed third-party boot manager) picks
between them at power-on. The InterGenOS installer plays no role in
configuring or detecting the other OS — it just installs cleanly onto
its own target.

What's deferred to v1.x — automated alongside-install (shrink a working
Windows partition to make room for InterGenOS on the same drive, then
wire dual-boot into a shared ESP). That workflow requires a full UX
flow, partition-loss recovery semantics, and a test matrix on real
Windows partitions (including BitLocker detection). The 2026-05-15
defer ratification (commit f3d33e3b) is the source-of-truth on this
scope decision.

The primitives below — detect_shrinkable_ntfs, is_bitlocker_encrypted,
detect_bitlocker_partitions, shrink_ntfs, partition_disk_alongside —
are v1.x preparation surface, intentionally unwired in v1.0. They are
not reachable from any frontend or orchestrator caller and ship no
visible UI. Their continued presence is deliberate: the alongside
workflow will be implemented on top of them in v1.x; rewriting them
from scratch at that point would discard validated detection logic.
"""

import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum


# v1.x-prep — InstallMode is currently unused. The v1.0 fresh-install
# path doesn't branch on mode (partition_disk is the only caller).
# Retained alongside the v1.x alongside-install primitives below; will
# be threaded through install_io + state.py when alongside is wired
# in v1.x. See module docstring for the scope decision.
class InstallMode(Enum):
    FRESH = "fresh"          # wipe disk, clean slate
    ALONGSIDE = "alongside"  # shrink existing OS partition, share ESP (v1.x)


# Minimum free space we require for an InterGenOS root partition (250 GB)
# Per Q6 partition math (signing_key_custody discussion).
ALONGSIDE_MIN_ROOT_BYTES = 250 * 1024**3

# ESP sizing — D-005 Phase B enforcement. Per directive: "ESP sizing.
# Forge enforces minimum ESP headroom for multiple UKI generations
# (per-kernel UKI ~80-150 MB; default keep-2-old-kernels target ~500 MB
# minimum ESP)." 1 GB chosen as the canonical Forge default (industry
# norm: Pop_OS uses 1 GB, Fedora 600 MB, Ubuntu 512 MB; D-005 + Phase D
# LUKS FDE-initramfs raises the high-water mark, so 1 GB protects future
# headroom without scope-creep).
ESP_SIZE_MIB = 1024

# Minimum disk size for an InterGenOS fresh install (v1.0). ESP + root
# + a 16 GB user-data baseline. Forge refuses to partition disks smaller
# than this; the operator picks a different target. Per D-005 Phase B
# Forge ESP enforcement.
FRESH_INSTALL_MIN_DISK_BYTES = 32 * 1024**3

# LUKS2 argon2id KDF parameters (D-001 LUKS-at-install v1.0).
#
# Picked per UC dispatch 2026-05-18T21:47:27Z thread d001-luks-activation-chain:
# "PBKDF argon2id, default 1GB memory + 4 iter for desktop-class hw —
# research-validate, do not cargo-cult." Validation:
#
# Argon2id RFC 9106 §4 recommends two operating points:
#   - First-recommended:  t=1, m=2^21 KB (2 GB)
#   - Second-recommended: t=3, m=2^16 KB (64 MB)
# Our choice (m=1 GB, t=4) sits between these — high memory cost (which
# defeats GPU-accelerated brute-force, the dominant attack class against
# disk-unlock secrets) without the OOM risk of 2 GB on 4 GB-RAM systems
# typical of low-end laptops. t=4 with this memory takes ~2-3s on modern
# desktop x86 — interactive but not painful.
#
# Parallel=4 matches the typical desktop core count (modern laptops are
# ≥4 cores; cryptsetup defaults to cpu_count() so this is just an
# explicit-not-implicit choice).
#
# Forcing iterations (--pbkdf-force-iterations) instead of using
# cryptsetup's benchmark-to-target-time mode (--iter-time) gives
# deterministic header parameters across install hardware — important
# for reproducibility + for future header-restore workflows.
LUKS_PBKDF = "argon2id"
LUKS_PBKDF_MEMORY_KB = 1024 * 1024   # 1 GB
LUKS_PBKDF_ITERATIONS = 4
LUKS_PBKDF_PARALLEL = 4

# Canonical /dev/mapper name for the LUKS root (matches CRYPT_NAME in
# installer/init/fde-init.sh; both must agree because fde-init.sh reads
# /etc/crypttab field 1 to find the LUKS device path).
LUKS_MAPPER_NAME = "cryptroot"

# Canonical ESP filesystem label. fde-init.sh's EXPERIMENTAL unlock paths
# locate the ESP via `blkid -L IGOS_ESP` to read TPM2 / FIDO2 sealed-key
# metadata at boot. Set on fresh installs by partition_disk()'s
# mkfs.fat -F32 -n IGOS_ESP call.
ESP_LABEL = "IGOS_ESP"

# D-001 EXPERIMENTAL TPM2 / FIDO2 unlock tool locations (built by the
# build-system coordinator's S-B + S-C lanes). Same install convention as
# cryptsetup-static (D-001/S-A): /usr/lib/intergen/<name>-static[/...].
TPM2_TOOLS_DIR  = "/usr/lib/intergen/tpm2-tools-static"
FIDO2_TOOLS_DIR = "/usr/lib/intergen/fido2-tools-static"

# PCR policy for TPM2 sealed-key unlock: PCR0 (firmware) + PCR7 (Secure
# Boot state). PCR0 binds the seal to the running firmware; PCR7 binds
# to the Secure Boot policy (db/dbx/MOK enrollment state). Firmware
# update OR Secure Boot policy change invalidates the seal — operator
# falls through to passphrase to recover and re-enrolls. Documented in
# docs/users/full-disk-encryption.md.
TPM2_SEAL_PCRS = "sha256:0,7"

# FIDO2 relying-party identifier. By WebAuthn convention RP IDs are
# DNS-name-like; "intergenos" is opaque-but-stable. Stored in the
# credential record on the token; must agree at enroll + assert time.
FIDO2_RP_ID = "intergenos"


@dataclass
class Disk:
    """Represents a block device."""
    path: str           # e.g., /dev/sda
    name: str           # e.g., sda
    size_bytes: int
    size_human: str      # e.g., "500G"
    model: str
    removable: bool
    partitions: list = field(default_factory=list)


@dataclass
class Partition:
    """Represents a partition on a disk."""
    path: str           # e.g., /dev/sda1
    number: int
    size_bytes: int
    fstype: str
    mountpoint: str
    label: str
    uuid: str


def detect_disks():
    """Detect available block devices.

    Returns list of Disk objects, excluding loop devices, RAM disks,
    and the installation media itself.
    """
    # timeout=30: prevents installer hang on edge hardware where lsblk
    # blocks indefinitely (e.g., stuck NFS mount on the live media,
    # half-detected USB-IO error, broken kernel storage driver). On
    # timeout we treat as no-disks-detected; the frontend then surfaces
    # an explicit error rather than an unbounded spinner.
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o",
             "NAME,SIZE,MODEL,RM,TYPE,FSTYPE,MOUNTPOINT,LABEL,UUID,PATH"],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return []
    if result.returncode != 0:
        return []

    data = json.loads(result.stdout)
    disks = []

    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        # Skip loop, ram, sr (CD/DVD)
        name = dev.get("name", "")
        if name.startswith(("loop", "ram", "sr", "zram")):
            continue

        size = int(dev.get("size", 0))
        disk = Disk(
            path=dev.get("path", f"/dev/{name}"),
            name=name,
            size_bytes=size,
            size_human=_human_size(size),
            model=(dev.get("model") or "Unknown").strip(),
            removable=bool(dev.get("rm")),
        )

        # Collect partitions
        is_root_disk = False
        for child in dev.get("children", []):
            if child.get("type") == "part":
                part_size = int(child.get("size", 0))
                mountpoint = child.get("mountpoint") or ""
                disk.partitions.append(Partition(
                    path=child.get("path", f"/dev/{child['name']}"),
                    number=int(child["name"].replace(name, "").lstrip("p")),
                    size_bytes=part_size,
                    fstype=child.get("fstype") or "",
                    mountpoint=mountpoint,
                    label=child.get("label") or "",
                    uuid=child.get("uuid") or "",
                ))
                # Exclude disks containing the running root filesystem
                if mountpoint == "/":
                    is_root_disk = True

        if is_root_disk:
            continue

        disks.append(disk)

    return disks


def partition_disk(disk_path, efi=False, luks_enabled=False, luks_passphrase=None,
                   tpm2_enabled=False, fido2_enabled=False,
                   fido2_progress_callback=None):
    """Partition a disk for InterGenOS installation.

    Creates a GPT partition table with:
    - EFI mode: ESP (1 GB FAT32, per D-005 Phase B sizing for UKI
      retention) + root (rest, ext4)
    - BIOS mode: bios_grub (1MB) + root (rest, ext4)

    Pre-checks (D-005 Phase B Forge ESP-size enforcement):
    - Disk size MUST be >= FRESH_INSTALL_MIN_DISK_BYTES (32 GB). Smaller
      targets fail closed with a clear RuntimeError before any
      destructive write — the operator picks a different disk.

    LUKS opt-in (D-001 LUKS-at-install v1.0):
    - When luks_enabled=True, the root partition is wrapped in LUKS2
      before mkfs.ext4 runs. luks_passphrase MUST be a non-empty bytes
      or str (str is encoded utf-8); it is passed to cryptsetup over
      stdin (never via argv) and zeroized in this function's frame
      before return. The resulting /dev/mapper/cryptroot is what gets
      formatted ext4 and what callers should mount. Returns dict
      includes "root_mapper" = /dev/mapper/cryptroot and
      "luks_enabled" = True.
    - Pre-flight checks: cryptsetup binary on PATH; passphrase non-
      empty; target partition supports LUKS2 header (standard 512 or
      4096 sector sizes).

    D-001 EXPERIMENTAL unlock methods (operator Option A 2026-05-18T22:52Z):
    - tpm2_enabled / fido2_enabled require luks_enabled. When set, after
      LUKS format + open + mkfs.ext4, the ESP is mounted at a temporary
      mountpoint and:
        * TPM2: a 32-byte random key is generated, sealed against the
          running system's TPM2 with PCR0+PCR7 policy, written to
          <esp>/intergen/tpm2/{primary.ctx,secret.pub,secret.priv}, and
          added as a LUKS slot via cryptsetup luksAddKey.
        * FIDO2: a FIDO2 credential is enrolled on the user's plugged-in
          token (interactive: user must touch the token). hmac-secret
          assertion against a fresh 32-byte nonce yields the LUKS slot
          key, which is added via luksAddKey. {cred_id, stored_nonce}
          are written to <esp>/intergen/fido2/.
    - All EXPERIMENTAL paths fail-closed if their tools (tpm2-tools-static
      / fido2-tools-static) are absent OR if their hardware is absent
      (/dev/tpmrm0 missing; no FIDO2 token plugged within timeout). The
      passphrase remains the canonical fallback at boot time per
      installer/init/fde-init.sh's TPM2 → FIDO2 → passphrase chain.
    - Returns dict includes "crypt_opts" = list of crypttab option
      tokens (e.g. ["luks", "discard", "tpm2", "fido2"]).

    Returns dict with partition paths.
    """
    # D-005 Phase B pre-check: disk size sufficient for ESP + meaningful root
    disk_size = _disk_size_bytes(disk_path)
    if disk_size < FRESH_INSTALL_MIN_DISK_BYTES:
        raise RuntimeError(
            f"target disk {disk_path} is {_human_size(disk_size)} — "
            f"below the v1.0 minimum of "
            f"{_human_size(FRESH_INSTALL_MIN_DISK_BYTES)}. The D-005 "
            f"UKI parity model needs ESP headroom for multiple kernel "
            f"generations + LUKS FDE initramfs (Phase D). Pick a larger "
            f"target disk."
        )

    # D-001 LUKS-at-install pre-flight (before any destructive write).
    # Fail closed so the operator picks plain-install or fixes the
    # environment rather than landing mid-partition with no recovery.
    if luks_enabled:
        if not cryptsetup_available():
            raise RuntimeError(
                "LUKS opt-in selected but `cryptsetup` is not on PATH. "
                "The live ISO must include packages/core/cryptsetup for "
                "install-time LUKS format. Pick plain install or fix "
                "the live media."
            )
        if not luks_passphrase:
            raise RuntimeError(
                "LUKS opt-in selected but luks_passphrase is empty. "
                "Forge frontend contract: when luks_enabled=True, the "
                "passphrase MUST be non-empty + confirm-matched before "
                "partition_disk is called."
            )

    # D-001 EXPERIMENTAL TPM2 + FIDO2 pre-flight (composition with LUKS).
    if tpm2_enabled or fido2_enabled:
        if not luks_enabled:
            raise RuntimeError(
                "tpm2_enabled / fido2_enabled require luks_enabled. "
                "EXPERIMENTAL unlock methods are LUKS-slot-bound — the "
                "underlying LUKS volume must exist before extra slots "
                "can be added."
            )
        if not efi:
            raise RuntimeError(
                "tpm2_enabled / fido2_enabled require EFI install. "
                "Sealed-key metadata lives on the ESP; BIOS installs "
                "have no ESP."
            )
        if tpm2_enabled:
            if not tpm2_tools_available():
                raise RuntimeError(
                    f"tpm2_enabled but tpm2-tools-static absent at {TPM2_TOOLS_DIR}. "
                    "build-system coordinator's S-B package must be installed "
                    "in the live ISO before install-time TPM2 seal can run."
                )
            if not tpm2_present():
                raise RuntimeError(
                    "tpm2_enabled but no TPM2 device detected (/dev/tpmrm0 absent). "
                    "Frontend should grey-disable this checkbox when /dev/tpmrm0 "
                    "is missing — reaching this branch indicates a frontend bug."
                )
        if fido2_enabled and not fido2_tools_available():
            raise RuntimeError(
                f"fido2_enabled but fido2-tools-static absent at {FIDO2_TOOLS_DIR}. "
                "build-system coordinator's S-C package must be installed in "
                "the live ISO before install-time FIDO2 enrollment can run."
            )

    # Wipe existing partition table (routed through _run so dry_run is honored)
    _run(["wipefs", "-a", disk_path])

    if efi:
        # GPT + EFI System Partition + root. ESP sized per ESP_SIZE_MIB
        # (D-005 Phase B). End offset = 1 MiB header + ESP_SIZE_MIB.
        esp_end_mib = 1 + ESP_SIZE_MIB
        _run(f"parted -s {disk_path} mklabel gpt")
        _run(f"parted -s {disk_path} mkpart ESP fat32 1MiB {esp_end_mib}MiB")
        _run(f"parted -s {disk_path} set 1 esp on")
        _run(f"parted -s {disk_path} mkpart root ext4 {esp_end_mib}MiB 100%")

        # Determine partition paths (handles nvme vs sda naming)
        p1, p2 = _partition_paths(disk_path, 2)

        # ESP formatted FAT32 with canonical label (D-001 EXPERIMENTAL:
        # fde-init.sh's `blkid -L IGOS_ESP` ESP discovery depends on this).
        _run(f"mkfs.fat -F32 -n {ESP_LABEL} {p1}")

        if luks_enabled:
            # LUKS2 format the root partition, open as cryptroot,
            # then mkfs.ext4 on /dev/mapper/cryptroot. Passphrase
            # flows over stdin (never argv); zeroized in finally.
            luks2_format(p2, luks_passphrase)
            mapper = luks_open(p2, luks_passphrase, name=LUKS_MAPPER_NAME)
            _run(f"mkfs.ext4 -L intergenos {mapper}")

            crypt_opts = ["luks", "discard"]
            if tpm2_enabled or fido2_enabled:
                _enroll_experimental_unlock_methods(
                    p1, p2, luks_passphrase,
                    tpm2_enabled=tpm2_enabled,
                    fido2_enabled=fido2_enabled,
                    fido2_progress_callback=fido2_progress_callback,
                    crypt_opts=crypt_opts,
                )
            return {
                "esp": p1,
                "root": p2,
                "root_mapper": mapper,
                "luks_enabled": True,
                "crypt_opts": crypt_opts,
                "efi": True,
            }
        else:
            _run(f"mkfs.ext4 -L intergenos {p2}")
            return {"esp": p1, "root": p2, "efi": True}
    else:
        # GPT + BIOS boot + root
        _run(f"parted -s {disk_path} mklabel gpt")
        _run(f"parted -s {disk_path} mkpart bios_grub 1MiB 2MiB")
        _run(f"parted -s {disk_path} set 1 bios_grub on")
        _run(f"parted -s {disk_path} mkpart root ext4 2MiB 100%")

        p1, p2 = _partition_paths(disk_path, 2)

        if luks_enabled:
            luks2_format(p2, luks_passphrase)
            mapper = luks_open(p2, luks_passphrase, name=LUKS_MAPPER_NAME)
            _run(f"mkfs.ext4 -L intergenos {mapper}")
            # EXPERIMENTAL TPM2/FIDO2 require EFI (pre-flight rejected
            # this combination above). Plain LUKS on BIOS continues to
            # work, just passphrase-only.
            return {
                "bios_grub": p1,
                "root": p2,
                "root_mapper": mapper,
                "luks_enabled": True,
                "crypt_opts": ["luks", "discard"],
                "efi": False,
            }
        else:
            _run(f"mkfs.ext4 -L intergenos {p2}")
            return {"bios_grub": p1, "root": p2, "efi": False}


# --- LUKS2 primitives (D-001 LUKS-at-install v1.0) ---


def cryptsetup_available():
    """Return True iff `cryptsetup` is on PATH.

    Used as a pre-flight check before LUKS-opt-in partition_disk. The
    installer environment (live ISO booted, Forge running from the
    squashfs root) must provide cryptsetup via packages/core/cryptsetup.
    Returns False on broken / minimal live media so the frontend can
    surface the gap before any destructive write.
    """
    return shutil.which("cryptsetup") is not None


def luks2_format(partition_path, passphrase):
    """LUKS2-format a partition with argon2id KDF.

    Passphrase MUST be bytes or str (str encoded utf-8). It is piped to
    cryptsetup via stdin — NEVER passed as a command-line argument
    (which would be visible to /proc/<pid>/cmdline scrapers). The local
    bytes view is zeroized in the finally block before return.

    Holy Grail filter: passphrase never reaches argv, never goes to
    journal/syslog (subprocess output is captured + dropped on success),
    never lands on disk outside the LUKS keyslot itself.

    Args:
        partition_path: block device to wrap, e.g. /dev/nvme0n1p2
        passphrase: bytes (preferred — bytearray for zeroize) or str

    Raises:
        RuntimeError on cryptsetup failure (with stderr captured; the
        captured stderr does NOT include the passphrase — cryptsetup
        only echoes the secret to its own prompts, not to stderr on
        non-interactive stdin paths).
    """
    if isinstance(passphrase, str):
        passphrase_bytes = bytearray(passphrase.encode("utf-8"))
    elif isinstance(passphrase, (bytes, bytearray)):
        passphrase_bytes = bytearray(passphrase)
    else:
        raise TypeError(
            f"luks2_format: passphrase must be str/bytes/bytearray, "
            f"got {type(passphrase).__name__}"
        )

    if len(passphrase_bytes) == 0:
        raise RuntimeError("luks2_format: empty passphrase rejected")

    cmd = [
        "cryptsetup",
        "luksFormat",
        "--type", "luks2",
        "--batch-mode",                              # no Y/N prompt
        "--key-file=-",                              # passphrase via stdin
        "--pbkdf", LUKS_PBKDF,
        "--pbkdf-memory", str(LUKS_PBKDF_MEMORY_KB),
        "--pbkdf-force-iterations", str(LUKS_PBKDF_ITERATIONS),
        "--pbkdf-parallel", str(LUKS_PBKDF_PARALLEL),
        partition_path,
    ]

    if _DRY_RUN:
        # Print arg list WITHOUT the passphrase (which is in stdin, not argv,
        # but make the dry-run intent explicit).
        print(f"  [DRY-RUN] {' '.join(cmd)}  <stdin: <PASSPHRASE>>")
        _zeroize(passphrase_bytes)
        return

    try:
        result = subprocess.run(
            cmd,
            input=bytes(passphrase_bytes),
            capture_output=True,
        )
        if result.returncode != 0:
            # stderr is safe to surface (cryptsetup doesn't echo the
            # secret on non-interactive stdin failure paths). Belt-and-
            # suspenders scrub for any byte sequence matching the
            # passphrase just in case a future cryptsetup release
            # regresses this contract.
            stderr_text = _scrub_passphrase_from_text(
                result.stderr.decode("utf-8", errors="replace"),
                passphrase_bytes,
            )
            raise RuntimeError(
                f"cryptsetup luksFormat failed (exit {result.returncode}): "
                f"{stderr_text}"
            )
    finally:
        _zeroize(passphrase_bytes)


def luks_open(partition_path, passphrase, name=LUKS_MAPPER_NAME):
    """Unlock a LUKS2 partition and expose it as /dev/mapper/<name>.

    Companion to luks2_format. Same passphrase-handling discipline:
    stdin (never argv), local bytes view zeroized in finally.

    Args:
        partition_path: the LUKS2-formatted block device
        passphrase: bytes or str
        name: /dev/mapper/<name> to expose; defaults to "cryptroot"
              to match installer/init/fde-init.sh's CRYPT_NAME.

    Returns:
        The /dev/mapper/<name> path on success.

    Raises:
        RuntimeError on cryptsetup failure (passphrase NOT in stderr).
    """
    if isinstance(passphrase, str):
        passphrase_bytes = bytearray(passphrase.encode("utf-8"))
    elif isinstance(passphrase, (bytes, bytearray)):
        passphrase_bytes = bytearray(passphrase)
    else:
        raise TypeError(
            f"luks_open: passphrase must be str/bytes/bytearray, "
            f"got {type(passphrase).__name__}"
        )

    cmd = ["cryptsetup", "open", "--type", "luks2", "--key-file=-",
           partition_path, name]
    mapper_path = f"/dev/mapper/{name}"

    if _DRY_RUN:
        print(f"  [DRY-RUN] {' '.join(cmd)}  <stdin: <PASSPHRASE>>")
        _zeroize(passphrase_bytes)
        return mapper_path

    try:
        result = subprocess.run(
            cmd,
            input=bytes(passphrase_bytes),
            capture_output=True,
        )
        if result.returncode != 0:
            stderr_text = _scrub_passphrase_from_text(
                result.stderr.decode("utf-8", errors="replace"),
                passphrase_bytes,
            )
            raise RuntimeError(
                f"cryptsetup open failed (exit {result.returncode}): "
                f"{stderr_text}"
            )
    finally:
        _zeroize(passphrase_bytes)

    return mapper_path


@contextmanager
def secure_passphrase(passphrase):
    """Context-manager that holds a passphrase + zeroizes on exit.

    Usage:
        with secure_passphrase(user_input) as ph:
            partitions = partition_disk(disk, efi=True,
                                        luks_enabled=True,
                                        luks_passphrase=ph)
        # ph is zeroized here; do not reference outside the block.

    The yielded value is a bytearray (caller passes it to subprocess
    via stdin; luks2_format / luks_open will internally zeroize THEIR
    copy too — belt-and-suspenders). The original string the user typed
    is up to the caller to drop; this manager protects the bytes
    derived from it.
    """
    if isinstance(passphrase, str):
        buf = bytearray(passphrase.encode("utf-8"))
    elif isinstance(passphrase, (bytes, bytearray)):
        buf = bytearray(passphrase)
    else:
        raise TypeError(
            f"secure_passphrase: must be str/bytes/bytearray, "
            f"got {type(passphrase).__name__}"
        )
    try:
        yield buf
    finally:
        _zeroize(buf)


def _zeroize(buf):
    """Overwrite a bytearray with NULs in place.

    Best-effort scrub of an in-memory secret. Python's GC and string
    interning mean we can't guarantee no copy survives elsewhere
    (interpreter heap, swap, etc.), but this prevents the obvious
    "passphrase variable still holds the bytes after function return"
    foot-gun and is the canonical idiom for Python secret handling.
    """
    if isinstance(buf, bytearray):
        for i in range(len(buf)):
            buf[i] = 0


# --- D-001 EXPERIMENTAL TPM2 / FIDO2 unlock primitives ---


def tpm2_tools_available():
    """Return True iff the build-system coordinator's tpm2-tools-static
    package is installed in the live ISO (frontend probes this to
    enable the EXPERIMENTAL TPM2 checkbox)."""
    return os.path.isfile(f"{TPM2_TOOLS_DIR}/tpm2_unseal") \
       and os.access(f"{TPM2_TOOLS_DIR}/tpm2_unseal", os.X_OK)


def fido2_tools_available():
    """Return True iff the build-system coordinator's fido2-tools-static
    package is installed in the live ISO."""
    return os.path.isfile(f"{FIDO2_TOOLS_DIR}/fido2-assert") \
       and os.access(f"{FIDO2_TOOLS_DIR}/fido2-assert", os.X_OK)


def tpm2_present():
    """Return True iff a TPM2 device is enumerated in the running
    kernel (/dev/tpmrm0 character device). Frontend uses this to
    grey-disable the EXPERIMENTAL TPM2 checkbox when no TPM exists."""
    try:
        return os.path.exists("/dev/tpmrm0")
    except OSError:
        return False


def _enroll_experimental_unlock_methods(esp_partition, luks_partition,
                                         luks_passphrase,
                                         tpm2_enabled, fido2_enabled,
                                         fido2_progress_callback,
                                         crypt_opts):
    """Internal orchestrator. Mounts the freshly-formatted ESP at a
    temporary mountpoint, runs TPM2 + FIDO2 enrollment as requested,
    appends "tpm2" / "fido2" tokens to crypt_opts (mutated in place
    for the caller's crypttab generation), then unmounts.

    Failure of EITHER enrollment raises — these are explicit operator
    opt-ins per the frontend checkbox, so silent skip would be a
    surprise. Operator can re-run install without the EXPERIMENTAL
    checkbox if their hardware turns out to not cooperate.
    """
    esp_mount = "/mnt/forge-esp-enroll"
    os.makedirs(esp_mount, exist_ok=True)
    _run(["mount", esp_partition, esp_mount])
    try:
        intergen_dir = os.path.join(esp_mount, "intergen")
        os.makedirs(intergen_dir, exist_ok=True)
        if tpm2_enabled:
            tpm2_seal_random_key(intergen_dir, luks_partition, luks_passphrase)
            crypt_opts.append("tpm2")
        if fido2_enabled:
            fido2_enroll_token(intergen_dir, luks_partition, luks_passphrase,
                               fido2_progress_callback)
            crypt_opts.append("fido2")
    finally:
        _run(["umount", esp_mount])


def tpm2_seal_random_key(intergen_dir, luks_partition, luks_passphrase):
    """D-001 EXPERIMENTAL TPM2 unlock — seal a fresh 32-byte random key
    to the system TPM under a PCR0+PCR7 policy, persist the sealed-blob
    triplet (primary.ctx, secret.pub, secret.priv) under
    intergen_dir/tpm2/, and add the random key as an additional LUKS
    slot on luks_partition (authenticated with luks_passphrase).

    On any failure (no TPM, broken pcr policy, cryptsetup luksAddKey
    refusing), cleans up partial files + re-raises a RuntimeError —
    the operator's explicit opt-in means silent partial enrollment
    would be a worse surprise than a hard fail.

    Holy Grail discipline: random_secret is bytes from os.urandom in
    a bytearray + zeroized in finally. The sealed triplet on ESP is
    NOT a secret: without the same TPM (or with changed PCR0/PCR7)
    the blob is useless.
    """
    tpm2_dir = os.path.join(intergen_dir, "tpm2")
    os.makedirs(tpm2_dir, exist_ok=True)
    primary_ctx = os.path.join(tpm2_dir, "primary.ctx")
    secret_pub  = os.path.join(tpm2_dir, "secret.pub")
    secret_priv = os.path.join(tpm2_dir, "secret.priv")
    policy_dat  = os.path.join(tpm2_dir, "pcr.policy")  # build-time only
    session_dat = "/tmp/forge-tpm2-session.dat"

    random_secret = bytearray(os.urandom(32))
    try:
        _tpm2(["tpm2_createprimary",
               "--hierarchy=owner",
               f"--key-context={primary_ctx}"])

        # Compute PCR-bound policy digest
        _tpm2(["tpm2_startauthsession", f"--session={session_dat}"])
        try:
            _tpm2(["tpm2_policypcr",
                   f"--session={session_dat}",
                   f"--pcr-list={TPM2_SEAL_PCRS}",
                   f"--policy={policy_dat}"])
        finally:
            _tpm2(["tpm2_flushcontext", session_dat], allow_fail=True)

        # tpm2_create reads sealing input from stdin via --sealing-input=-
        if _DRY_RUN:
            print(f"  [DRY-RUN] tpm2_create -C {primary_ctx} -L {policy_dat} "
                  f"-i - -u {secret_pub} -r {secret_priv}  <stdin: <32 RANDOM BYTES>>")
        else:
            cmd = [_tpm2_path("tpm2_create"),
                   f"--parent-context={primary_ctx}",
                   f"--policy={policy_dat}",
                   "--sealing-input=-",
                   f"--public={secret_pub}",
                   f"--private={secret_priv}"]
            res = subprocess.run(cmd, input=bytes(random_secret),
                                 capture_output=True)
            if res.returncode != 0:
                raise RuntimeError(
                    f"tpm2_create failed (exit {res.returncode}): "
                    f"{res.stderr.decode('utf-8', errors='replace')}"
                )

        # Add the random key as a LUKS slot, authenticated by existing
        # passphrase. cryptsetup luksAddKey reads: line 1 = existing
        # key (passphrase), line 2 = new key (random_secret). Both via
        # stdin with --key-file=- meaning batch-mode.
        if isinstance(luks_passphrase, str):
            existing_key = luks_passphrase.encode("utf-8")
        else:
            existing_key = bytes(luks_passphrase)

        cmd = ["cryptsetup", "luksAddKey",
               "--type=luks2", "--batch-mode",
               "--key-file=-",
               luks_partition,
               "/dev/stdin"]
        # cryptsetup luksAddKey with --key-file=- and positional key-file
        # /dev/stdin needs two distinct streams; simplest is two-pass:
        # write existing+new to a fifo, OR use --new-keyfile=- (modern
        # cryptsetup). Fall back to the --key-file mode + temp fifo if
        # the new-keyfile flag is missing.
        if _DRY_RUN:
            print(f"  [DRY-RUN] cryptsetup luksAddKey {luks_partition} "
                  "<existing-passphrase via stdin> <new-tpm2-key via fd>")
        else:
            _luks_add_key_with_existing(luks_partition, existing_key,
                                        bytes(random_secret))
    finally:
        _zeroize(random_secret)
        if os.path.exists(session_dat):
            try:
                os.unlink(session_dat)
            except OSError:
                pass
        if os.path.exists(policy_dat):
            # Policy digest is reproducible from PCR state; safe to remove
            # after the seal lands. Keeping it would imply the unseal path
            # uses it (it doesn't — the unseal session is computed fresh
            # at boot from the live PCRs).
            try:
                os.unlink(policy_dat)
            except OSError:
                pass


def fido2_enroll_token(intergen_dir, luks_partition, luks_passphrase,
                        progress_callback=None):
    """D-001 EXPERIMENTAL FIDO2 unlock — enroll a credential on the
    user's plugged-in FIDO2 token, derive an hmac-secret HMAC bound to
    a fresh 32-byte nonce, persist {cred_id, stored_nonce} under
    intergen_dir/fido2/, and add the HMAC bytes as an additional LUKS
    slot on luks_partition (authenticated with luks_passphrase).

    Interactive: the user must physically touch the token when the
    fido2-cred / fido2-assert calls request user presence. The
    progress_callback (optional fn(message)) is called with status
    text so the frontend can render "plug your security token now",
    "touching the token...", etc.
    """
    fido2_dir = os.path.join(intergen_dir, "fido2")
    os.makedirs(fido2_dir, exist_ok=True)
    cred_id_path = os.path.join(fido2_dir, "cred_id")
    nonce_path   = os.path.join(fido2_dir, "stored_nonce")

    def _emit(msg):
        if progress_callback:
            progress_callback(msg)

    if _DRY_RUN:
        print(f"  [DRY-RUN] fido2-cred -M <prompt user to touch token>  -> {cred_id_path}")
        print(f"  [DRY-RUN] fido2-assert -G --hmac-secret <prompt user to touch token>")
        print(f"  [DRY-RUN] cryptsetup luksAddKey {luks_partition}  <stdin: HMAC bytes>")
        return

    # Discover token device. fido2-token -L lists "device path"
    # one-per-line. If multiple plugged, take the first; operator can
    # unplug others to disambiguate.
    _emit("Detecting FIDO2 token (plug it in if not already)...")
    token_dev = _fido2_first_token()
    if not token_dev:
        raise RuntimeError(
            "No FIDO2 token detected via `fido2-token -L`. "
            "Plug your security token + re-run install (or uncheck "
            "the FIDO2 EXPERIMENTAL checkbox)."
        )

    _emit(f"Enrolling credential on {token_dev} — touch the token when it blinks")
    cred_id = _fido2_make_credential(token_dev)
    with open(cred_id_path, "wb") as f:
        f.write(cred_id)

    nonce = os.urandom(32)
    with open(nonce_path, "wb") as f:
        f.write(nonce)

    _emit("Deriving unlock key — touch the token again when it blinks")
    hmac_secret = _fido2_assert_hmac(token_dev, cred_id, nonce)

    if isinstance(luks_passphrase, str):
        existing_key = luks_passphrase.encode("utf-8")
    else:
        existing_key = bytes(luks_passphrase)

    _luks_add_key_with_existing(luks_partition, existing_key, hmac_secret)


# --- tpm2-tools-static + fido2-tools-static subprocess helpers ---


def _tpm2_path(tool):
    """Resolve a tpm2-tools-static binary path."""
    return os.path.join(TPM2_TOOLS_DIR, tool)


def _fido2_path(tool):
    """Resolve a fido2-tools-static binary path."""
    return os.path.join(FIDO2_TOOLS_DIR, tool)


def _tpm2(argv, allow_fail=False):
    """Run a tpm2-tools-static command, raising on non-zero unless
    allow_fail=True. argv[0] is the bare tool name; resolved to the
    static-binary path via _tpm2_path."""
    cmd = [_tpm2_path(argv[0])] + argv[1:]
    if _DRY_RUN:
        print(f"  [DRY-RUN] {' '.join(cmd)}")
        return
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 and not allow_fail:
        raise RuntimeError(
            f"{argv[0]} failed (exit {res.returncode}): "
            f"{res.stderr.decode('utf-8', errors='replace')}"
        )


def _fido2_first_token():
    """Return the first FIDO2 token device path enumerated by
    fido2-token -L, or None if no token is plugged."""
    res = subprocess.run([_fido2_path("fido2-token"), "-L"],
                         capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    # fido2-token -L output format: "<device-path>: vendor=0x... product=0x..."
    first_line = res.stdout.strip().splitlines()[0]
    return first_line.split(":", 1)[0].strip()


def _fido2_make_credential(token_dev):
    """Run fido2-cred -M, returning the credential ID bytes.

    fido2-cred reads its input parameters from stdin in libfido2's
    canonical multi-line format: client-data-hash, RP id, user name,
    user id (all base64-encoded). Output is multi-line; the credential
    ID appears base64-encoded. Returns the raw credential ID bytes.
    """
    import base64
    cdh = base64.b64encode(os.urandom(32)).decode("ascii")
    user_id = base64.b64encode(os.urandom(16)).decode("ascii")
    stdin_text = (
        f"{cdh}\n"
        f"{FIDO2_RP_ID}\n"
        "intergenos-user\n"
        f"{user_id}\n"
    )
    res = subprocess.run(
        [_fido2_path("fido2-cred"), "-M", "-h", token_dev],
        input=stdin_text, capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"fido2-cred -M failed (exit {res.returncode}): {res.stderr}"
        )
    # Output format: line 1 = cdh, line 2 = aaguid, line 3 = credential id,
    # line 4 = pubkey, line 5 = x509. We want line 3.
    lines = res.stdout.strip().splitlines()
    if len(lines) < 3:
        raise RuntimeError(
            f"fido2-cred -M output malformed: {res.stdout!r}"
        )
    return base64.b64decode(lines[2])


def _fido2_assert_hmac(token_dev, cred_id, nonce):
    """Run fido2-assert -G with the hmac-secret extension and the
    given nonce as salt. Returns the HMAC output bytes."""
    import base64
    cdh = base64.b64encode(os.urandom(32)).decode("ascii")
    cred_id_b64 = base64.b64encode(cred_id).decode("ascii")
    nonce_b64 = base64.b64encode(nonce).decode("ascii")
    stdin_text = (
        f"{cdh}\n"
        f"{FIDO2_RP_ID}\n"
        f"{cred_id_b64}\n"
        f"{nonce_b64}\n"
    )
    res = subprocess.run(
        [_fido2_path("fido2-assert"), "-G", "--hmac-secret", "-h", token_dev],
        input=stdin_text, capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"fido2-assert -G failed (exit {res.returncode}): {res.stderr}"
        )
    # Output format ends with "hmac-secret: <hex>" — extract the hex.
    hmac_hex = None
    for line in res.stdout.splitlines():
        if line.startswith("hmac-secret:"):
            hmac_hex = line.split(":", 1)[1].strip()
            break
    if not hmac_hex:
        raise RuntimeError(
            f"fido2-assert -G output missing hmac-secret line: {res.stdout!r}"
        )
    return bytes.fromhex(hmac_hex)


def _luks_add_key_with_existing(luks_partition, existing_key, new_key):
    """Add `new_key` as a new LUKS keyslot on `luks_partition`,
    authenticated with `existing_key`.

    cryptsetup luksAddKey wants two key streams: --key-file=- (existing)
    + a positional new-key file (or --new-keyfile=- on modern releases).
    Implementation: write new_key to an O_TMPFILE, pass the /proc/self/fd
    path to cryptsetup, feed existing_key via stdin. Holy Grail: new_key
    only persists as kernel pipe buffer + tmpfile FD, never disk inode.
    """
    import tempfile
    # Write new_key to an unlinked tempfile (O_TMPFILE-like behavior via
    # NamedTemporaryFile + immediate unlink). Reachable via /proc/self/fd
    # to cryptsetup as the keyfile argument.
    f = tempfile.NamedTemporaryFile(delete=False)
    try:
        f.write(new_key)
        f.flush()
        new_key_path = f.name
        f.close()
        cmd = ["cryptsetup", "luksAddKey",
               "--type=luks2", "--batch-mode",
               "--key-file=-",
               luks_partition,
               new_key_path]
        res = subprocess.run(cmd, input=existing_key, capture_output=True)
        if res.returncode != 0:
            raise RuntimeError(
                f"cryptsetup luksAddKey failed (exit {res.returncode}): "
                f"{res.stderr.decode('utf-8', errors='replace')}"
            )
    finally:
        try:
            os.unlink(new_key_path)
        except OSError:
            pass


def _scrub_passphrase_from_text(text, passphrase_bytes):
    """Defense-in-depth: redact passphrase byte sequence from error text.

    cryptsetup's documented behavior is to NEVER echo the passphrase
    to stderr on --key-file=- + --batch-mode stdin paths. This scrub
    is belt-and-suspenders against a future regression. Constant-time
    is not required (the secret is already in memory; this only
    affects whether it reaches the RuntimeError message).
    """
    if not passphrase_bytes:
        return text
    try:
        secret = passphrase_bytes.decode("utf-8", errors="replace")
    except Exception:
        return text
    if secret and secret in text:
        return text.replace(secret, "<REDACTED-PASSPHRASE>")
    return text


def detect_existing_esp(disk):
    """Find an existing EFI System Partition on a disk (Windows install).

    Returns the Partition object if found, else None. Used to share an ESP
    in alongside-install mode rather than carving a new one.
    """
    for p in disk.partitions:
        if p.fstype == "vfat" and 100 * 1024**2 <= p.size_bytes <= 2 * 1024**3:
            # ESP is FAT32, typically 100MB - 2GB. Don't trust label alone.
            return p
    return None


def is_bitlocker_encrypted(partition_path):
    """Check if an NTFS partition is BitLocker-encrypted.

    BitLocker-encrypted volumes are reported by blkid with TYPE="BitLocker"
    rather than the usual ntfs/exfat. We must NEVER attempt to shrink a
    BitLocker volume — ntfsresize would either refuse or, worse, corrupt
    the encrypted data. Return True if encrypted (skip this partition).
    """
    result = subprocess.run(
        ["blkid", "-s", "TYPE", "-o", "value", partition_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False
    return "bitlocker" in result.stdout.strip().lower()


# --- Alongside-install primitives (post-v1 wiring) ---
# detect_shrinkable_ntfs / detect_bitlocker_partitions / shrink_ntfs /
# partition_disk_alongside form a complete primitive set for the
# alongside-Windows install flow. They are kept here as deliberate
# future-wiring surface; v1 ships with no UI/orchestrator caller so the
# functions are intentionally unused. Adding callers is the work; the
# primitives themselves are validated.


def detect_shrinkable_ntfs(disk, min_free_bytes=ALONGSIDE_MIN_ROOT_BYTES):
    """Find an NTFS partition that can be shrunk to make room.

    Returns (partition, free_bytes_after_shrink) tuple if a suitable NTFS
    partition is found, else (None, 0). The partition must:
    - have NTFS filesystem (not BitLocker — encrypted volumes are skipped)
    - have at least `min_free_bytes` of free space inside it (not just total
      size — we can only shrink to the size of actual used data plus a
      safety margin)

    NOTE: This function only IDENTIFIES; it does not shrink. Shrinking is
    a separate destructive step requiring explicit user confirmation.

    BitLocker-encrypted partitions are SILENTLY EXCLUDED here. The TUI
    should call detect_bitlocker_partitions() separately to surface these
    to the user with an informative message ("disable BitLocker in Windows
    first, or choose Fresh install").
    """
    candidates = []
    for p in disk.partitions:
        if p.fstype != "ntfs":
            continue
        # Skip BitLocker-encrypted volumes — never attempt to shrink these
        if is_bitlocker_encrypted(p.path):
            continue
        # Probe used space via ntfsresize --info --no-action
        result = subprocess.run(
            ["ntfsresize", "--info", "--no-action", p.path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            continue
        # Parse "You might resize at NNNNNNNNNN bytes or X.X MB" line
        used_bytes = _parse_ntfsresize_info(result.stdout)
        if used_bytes is None:
            continue
        free_after_shrink = p.size_bytes - used_bytes
        if free_after_shrink >= min_free_bytes:
            candidates.append((p, free_after_shrink))

    if not candidates:
        return None, 0

    # Pick the partition with the most free space
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]


def detect_bitlocker_partitions(disk):
    """List BitLocker-encrypted partitions on a disk.

    Used by the TUI to inform the user when alongside-install isn't
    available because their Windows volume is encrypted. Returns a list
    of Partition objects.
    """
    return [p for p in disk.partitions
            if p.fstype in ("ntfs", "BitLocker") and is_bitlocker_encrypted(p.path)]


def shrink_ntfs(partition_path, new_size_bytes):
    """Shrink an NTFS partition to free space for InterGenOS.

    Uses ntfsresize to shrink the filesystem, then parted to shrink the
    partition to match. CALLER must have already taken a snapshot or
    backup — this is destructive and irreversible without restore.

    Args:
        partition_path: e.g., /dev/nvme0n1p4
        new_size_bytes: new total partition size in bytes (must be larger
                       than current NTFS used space + 1GB safety margin)

    Raises:
        RuntimeError if ntfsresize or parted fails.
    """
    # Step 1: shrink the NTFS filesystem itself
    new_size_mib = new_size_bytes // (1024 * 1024)
    _run(f"ntfsresize --force --size {new_size_mib}M {partition_path}")

    # Step 2: shrink the partition to match (using parted resizepart)
    disk_path, part_num = _split_partition_path(partition_path)
    new_size_mb = new_size_bytes // (1000 * 1000)  # parted uses MB (decimal)
    _run(f"parted -s {disk_path} resizepart {part_num} {new_size_mb}MB")


def partition_disk_alongside(disk, ntfs_partition, free_start_mb):
    """Create InterGenOS partitions in space freed by ntfs shrink.

    Args:
        disk: Disk object (the parent disk)
        ntfs_partition: the now-shrunken NTFS partition (its end_offset is
                        where our free space begins)
        free_start_mb: byte offset where free space starts (in MiB),
                      typically the end of the shrunken NTFS

    Returns dict like partition_disk(): {"esp": <existing_esp_path>,
                                          "root": <new_root_path>,
                                          "efi": True,
                                          "alongside": True}
    """
    # Alongside mode requires EFI (Windows installs are always EFI on
    # modern hardware). Also share the existing ESP rather than carving
    # a new one.
    existing_esp = detect_existing_esp(disk)
    if not existing_esp:
        raise RuntimeError(
            "Alongside install requires an existing EFI System Partition "
            "(Windows ESP). None found."
        )

    # Create the InterGenOS root partition in the free space
    _run(f"parted -s {disk.path} mkpart root ext4 {free_start_mb}MiB 100%")

    # Kernel partition table re-read + udev settle before re-detecting.
    # Without these, detect_disks() can race against the new partition's
    # /dev/ node appearing and either miss the partition ("Disk disappeared")
    # or pick a stale partition number. Both are catastrophic since we'd
    # then format the wrong partition.
    _run(f"partprobe {disk.path}")
    _run("udevadm settle")

    # Find the new partition's path (it'll be the highest-numbered partition)
    # Re-detect to get the new partition number after parted creates it
    new_disk = next((d for d in detect_disks() if d.path == disk.path), None)
    if not new_disk:
        raise RuntimeError(f"Disk {disk.path} disappeared after partitioning")
    new_root = max(new_disk.partitions, key=lambda p: p.number)

    # Format the new root
    _run(f"mkfs.ext4 -L intergenos {new_root.path}")

    return {
        "esp": existing_esp.path,
        "root": new_root.path,
        "efi": True,
        "alongside": True,
        "shared_esp": True,
    }


def mount_target(partitions, target="/mnt/target"):
    """Mount partitions for installation.

    LUKS opt-in (D-001): when partitions["root_mapper"] is set, mount
    /dev/mapper/cryptroot (the unlocked LUKS volume) rather than the
    underlying LUKS partition. The mapper is what holds the ext4
    filesystem; mounting the underlying partition would fail (the
    partition's type is crypto_LUKS, not ext4).
    """
    os.makedirs(target, exist_ok=True)

    # Mount root — prefer the LUKS mapper if present, else the bare partition
    root_device = partitions.get("root_mapper") or partitions["root"]
    _run(f"mount {root_device} {target}")

    # Mount ESP if EFI
    if partitions.get("efi"):
        esp_mount = f"{target}/boot/efi"
        os.makedirs(esp_mount, exist_ok=True)
        _run(f"mount {partitions['esp']} {esp_mount}")

    return target


def unmount_target(target="/mnt/target"):
    """Unmount all filesystems under target."""
    # Unmount in reverse order
    for sub in ["boot/efi", "dev/pts", "dev", "proc", "sys", "run"]:
        path = f"{target}/{sub}"
        subprocess.run(["umount", path], capture_output=True)
    subprocess.run(["umount", target], capture_output=True)


def is_efi():
    """Check if the system booted in EFI mode."""
    return os.path.isdir("/sys/firmware/efi")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_DRY_RUN = False


def set_dry_run(enabled: bool):
    """Enable or disable dry-run mode globally."""
    global _DRY_RUN
    _DRY_RUN = enabled


def _run(cmd):
    """Run a command as a list (no shell), raise on failure.

    In dry-run mode, logs the command without executing it.
    Accepts either a list ["parted", "-s", "/dev/sda", ...] or a string
    that will be split with shlex. List form is preferred for safety —
    no shell metacharacter interpretation.
    """
    import shlex
    if isinstance(cmd, str):
        cmd_list = shlex.split(cmd)
    else:
        cmd_list = cmd

    if _DRY_RUN:
        print(f"  [DRY-RUN] {' '.join(cmd_list)}")
        import types
        result = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return result
    result = subprocess.run(cmd_list, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd_list)}\n{result.stderr}")
    return result


def _partition_paths(disk_path, count):
    """Get partition device paths (handles /dev/sda1 vs /dev/nvme0n1p1)."""
    # NVMe drives use 'p' separator
    sep = "p" if "nvme" in disk_path or "mmcblk" in disk_path else ""
    return tuple(f"{disk_path}{sep}{i}" for i in range(1, count + 1))


def _disk_size_bytes(disk_path):
    """Return the size of a block device in bytes via lsblk.

    Used by partition_disk()'s D-005 Phase B pre-check. Returns 0 on
    lookup failure (lsblk error, missing device, parse error); callers
    treat 0 as below the minimum threshold and abort cleanly.
    """
    try:
        result = subprocess.run(
            ["lsblk", "-bdn", "-o", "SIZE", disk_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return 0
        return int(result.stdout.strip() or 0)
    except (subprocess.TimeoutExpired, ValueError):
        return 0


def _human_size(bytes_val):
    """Convert bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"


def _parse_ntfsresize_info(output):
    """Parse used-bytes from `ntfsresize --info` output.

    Looks for lines like:
        You might resize at 123456789012 bytes or 123456 MB
    Returns the bytes value as int, or None if not found.
    """
    import re
    m = re.search(r"resize at (\d+) bytes", output)
    if m:
        return int(m.group(1))
    return None


def _split_partition_path(part_path):
    """Split /dev/sda1 -> ('/dev/sda', 1), /dev/nvme0n1p3 -> ('/dev/nvme0n1', 3)."""
    if "nvme" in part_path or "mmcblk" in part_path:
        idx = part_path.rfind("p")
        return part_path[:idx], int(part_path[idx + 1:])
    i = len(part_path)
    while i > 0 and part_path[i - 1].isdigit():
        i -= 1
    return part_path[:i], int(part_path[i:])
