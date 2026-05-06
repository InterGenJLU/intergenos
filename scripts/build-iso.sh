#!/usr/bin/env bash
# build-iso.sh — assemble the InterGenOS bootable ISO from signed components.
#
# Phase 3 of the 3-part Secure Boot ISO plan. Consumes already-signed shim,
# GRUB, and UKI binaries plus a built squashfs and produces a single hybrid-
# bootable .iso file with:
#
#   * GPT + ESP partition (UEFI Secure Boot path)
#   * El Torito boot record (BIOS-legacy path; same UEFI binary loaded)
#   * /live/filesystem.squashfs (root filesystem the UKI's initramfs mounts)
#
# Multiple distros' xorriso invocations were studied for the standard
# hybrid-ISO incantation (archiso's mkarchiso, debian-live, casper,
# xorriso's own docs). The flags are not original to any one of them; they
# are an idiom convergence. This script does NOT vendor any of them.
# xorriso is the actual ISO-authoring engine; we orchestrate it directly
# so the trust boundary is clean.
#
# Usage:
#   SHIM=/path/to/shimx64.efi.signed \
#   GRUB=/path/to/grubx64.efi.signed \
#   UKI=/path/to/igos-live.efi.signed \
#   SQUASHFS=/path/to/filesystem.squashfs \
#   OUTPUT=build/intergenos-1.0-dev1.iso \
#   scripts/build-iso.sh
#
# Optional env vars:
#   GRUB_CFG       — ESP-side grub.cfg (default: installer/iso/grub/grub.cfg)
#   THEME_DIR      — GRUB theme directory to embed (default: skipped if absent)
#   UNICODE_PF2    — GRUB unicode font (default: /usr/share/grub/unicode.pf2)
#   VOLID          — ISO volume ID (default: IGOS_X86_64_<YYYYMMDD-from-SDE>)
#   SOURCE_DATE_EPOCH — for reproducibility; passed through to xorriso/mtools
#   LOG_DIR        — log directory (default: build/logs/iso)
#   ESP_HEADROOM_MB — extra MB to add to ESP image beyond computed file size
#                    (default: 16; covers FAT32 metadata + spare)

set -euo pipefail

# --------------------------------------------------------------------------
# Inputs + defaults
# --------------------------------------------------------------------------

SHIM="${SHIM:?missing SHIM env var (signed shimx64.efi)}"
GRUB="${GRUB:?missing GRUB env var (signed grubx64.efi)}"
UKI="${UKI:?missing UKI env var (signed igos-live.efi)}"
SQUASHFS="${SQUASHFS:?missing SQUASHFS env var (filesystem.squashfs)}"
OUTPUT="${OUTPUT:?missing OUTPUT env var (.iso path)}"

GRUB_CFG="${GRUB_CFG:-installer/iso/grub/grub.cfg}"
THEME_DIR="${THEME_DIR:-}"
UNICODE_PF2="${UNICODE_PF2:-/usr/share/grub/unicode.pf2}"
LOG_DIR="${LOG_DIR:-build/logs/iso}"
ESP_HEADROOM_MB="${ESP_HEADROOM_MB:-16}"

# SOURCE_DATE_EPOCH propagation. xorriso 1.5.6+ honours the env var directly;
# mtools honours it via a config knob. If unset, derive from build start so
# downstream attestation can record what we actually used (rather than "now").
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
    SOURCE_DATE_EPOCH=$(date -u +%s)
    export SOURCE_DATE_EPOCH
    echo "[build-iso] WARN: SOURCE_DATE_EPOCH not set; using build start ${SOURCE_DATE_EPOCH}" >&2
    echo "[build-iso]       Reproducibility requires the caller to set this explicitly." >&2
fi

# Derive default VOLID from SOURCE_DATE_EPOCH so two runs with the same SDE
# produce the same volume id.
if [ -z "${VOLID:-}" ]; then
    VOLID="IGOS_X86_64_$(date -u -d "@${SOURCE_DATE_EPOCH}" +%Y%m%d)"
fi

# --------------------------------------------------------------------------
# Required-file checks
# --------------------------------------------------------------------------

[ -f "$SHIM" ]      || { echo "ERROR: SHIM not found: $SHIM" >&2; exit 1; }
[ -f "$GRUB" ]      || { echo "ERROR: GRUB not found: $GRUB" >&2; exit 1; }
[ -f "$UKI" ]       || { echo "ERROR: UKI not found: $UKI" >&2; exit 1; }
[ -f "$SQUASHFS" ]  || { echo "ERROR: SQUASHFS not found: $SQUASHFS" >&2; exit 1; }
[ -f "$GRUB_CFG" ]  || { echo "ERROR: GRUB_CFG not found: $GRUB_CFG" >&2; exit 1; }
[ -f "$UNICODE_PF2" ] || { echo "ERROR: UNICODE_PF2 not found: $UNICODE_PF2" >&2; \
                            echo "Set UNICODE_PF2 env var to the path of unicode.pf2 " \
                                 "(usually shipped by grub2 / grub-common)." >&2; \
                            exit 1; }

if [ -n "$THEME_DIR" ] && [ ! -d "$THEME_DIR" ]; then
    echo "ERROR: THEME_DIR set but directory not found: $THEME_DIR" >&2
    exit 1
fi

# PE-binary shape probe — pre-trust-boundary cheap check that the upstream
# signing-helper actually wrote PE32+ binaries. Catches truncated writes,
# wrong-architecture binaries, plain text smuggled in. Full sbverify is
# correctly out-of-scope (build VM doesn't hold the cert).
for binary in "$SHIM" "$GRUB" "$UKI"; do
    if ! file -b "$binary" | grep -q "PE32+"; then
        echo "ERROR: $binary is not a PE32+ binary" >&2
        echo "       file says: $(file -b "$binary")" >&2
        exit 1
    fi
done

# --------------------------------------------------------------------------
# Tool checks
# --------------------------------------------------------------------------

for tool in xorriso mkfs.vfat mcopy mmd; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: '$tool' not in PATH." >&2
        case "$tool" in
            xorriso)
                echo "        Install: libisoburn / xorriso package." >&2 ;;
            mkfs.vfat)
                echo "        Install: dosfstools package." >&2 ;;
            mcopy|mmd)
                echo "        Install: mtools package." >&2 ;;
        esac
        exit 1
    fi
done

# xorriso version assertion — 1.5.6+ honours SOURCE_DATE_EPOCH directly.
# A stale xorriso silently drops SDE → reproducibility hole that masquerades
# as host divergence. -appended_part_as_gpt requires 1.4.6; -isohybrid-gpt-
# basdat requires 1.4.0; SDE honoring 1.5.6. Asserting 1.5.6 covers all.
XORRISO_VER=$(xorriso --version 2>&1 | awk '/^xorriso /{print $2; exit}')
if [ -z "$XORRISO_VER" ]; then
    echo "ERROR: could not determine xorriso version from \`xorriso --version\`." >&2
    exit 1
fi
# Compare via sort -V (version sort); abort if older than 1.5.6.
if [ "$(printf '1.5.6\n%s\n' "$XORRISO_VER" | sort -V | head -1)" != "1.5.6" ]; then
    echo "ERROR: xorriso version $XORRISO_VER is older than the required 1.5.6" >&2
    echo "       (1.5.6 is the first version that honours SOURCE_DATE_EPOCH)" >&2
    exit 1
fi

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

mkdir -p "$LOG_DIR"
LOG_TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOG_FILE="${LOG_DIR}/build_${LOG_TIMESTAMP}.log"

# Tee subsequent stdout+stderr to log file via process substitution.
# (Subshell-on-exit copy would lose mid-run output if we crash.)
exec > >(tee -a "$LOG_FILE") 2>&1

echo "[build-iso] starting at $(date -u --iso-8601=seconds)"
echo "[build-iso] ============================================================"
echo "[build-iso] inputs:"
echo "[build-iso]   SHIM:       $SHIM"
echo "[build-iso]     sha256:   $(sha256sum "$SHIM" | awk '{print $1}')"
echo "[build-iso]   GRUB:       $GRUB"
echo "[build-iso]     sha256:   $(sha256sum "$GRUB" | awk '{print $1}')"
echo "[build-iso]   UKI:        $UKI"
echo "[build-iso]     sha256:   $(sha256sum "$UKI" | awk '{print $1}')"
echo "[build-iso]   SQUASHFS:   $SQUASHFS"
echo "[build-iso]     sha256:   $(sha256sum "$SQUASHFS" | awk '{print $1}')"
echo "[build-iso]   GRUB_CFG:   $GRUB_CFG"
echo "[build-iso]   THEME_DIR:  ${THEME_DIR:-<none>}"
echo "[build-iso]   UNICODE_PF2: $UNICODE_PF2"
echo "[build-iso]   OUTPUT:     $OUTPUT"
echo "[build-iso]   VOLID:      $VOLID"
echo "[build-iso]   SDE:        $SOURCE_DATE_EPOCH"
echo "[build-iso]   LOG:        $LOG_FILE"
echo "[build-iso] ============================================================"

# --------------------------------------------------------------------------
# Staging
# --------------------------------------------------------------------------

STAGING=$(mktemp -d -t build-iso-XXXXXX)
trap 'rm -rf "$STAGING"' EXIT

ESP_TREE="${STAGING}/esp-tree"
ESP_IMG="${STAGING}/efi.img"
ISO_ROOT="${STAGING}/iso-root"

mkdir -p "${ESP_TREE}/EFI/BOOT" \
         "${ESP_TREE}/EFI/InterGenOS/themes" \
         "${ESP_TREE}/EFI/InterGenOS/fonts" \
         "${ISO_ROOT}/live"

# --------------------------------------------------------------------------
# Step 1: ESP layout
# --------------------------------------------------------------------------

echo "[build-iso] [1/6] staging ESP layout"

# Firmware-fallback path: /EFI/BOOT/BOOTX64.EFI is what the firmware loads
# when no NVRAM Boot#### entry exists (e.g. fresh install, USB boot, removable
# media). Per UEFI spec convention this must be the shim — the shim chains
# to GRUB, GRUB chains to the UKI.
cp "$SHIM" "${ESP_TREE}/EFI/BOOT/BOOTX64.EFI"

# Canonical InterGenOS paths (NVRAM Boot#### entries point here once the
# system is installed; the live ISO's own NVRAM-less path is /EFI/BOOT/...
# above, but the binaries are also reachable via these named paths from
# inside GRUB).
cp "$SHIM"  "${ESP_TREE}/EFI/InterGenOS/shimx64.efi"
cp "$GRUB"  "${ESP_TREE}/EFI/InterGenOS/grubx64.efi"
cp "$UKI"   "${ESP_TREE}/EFI/InterGenOS/igos-live.efi"
cp "$GRUB_CFG" "${ESP_TREE}/EFI/InterGenOS/grub.cfg"
cp "$UNICODE_PF2" "${ESP_TREE}/EFI/InterGenOS/fonts/unicode.pf2"

if [ -n "$THEME_DIR" ]; then
    cp -r "$THEME_DIR" "${ESP_TREE}/EFI/InterGenOS/themes/"
    echo "[build-iso]       theme dir staged: ${THEME_DIR##*/}"
fi

# --------------------------------------------------------------------------
# Step 2: ESP FAT32 image
# --------------------------------------------------------------------------

echo "[build-iso] [2/6] building ESP FAT32 image"

# Compute size needed (sum of file sizes + headroom)
ESP_BYTES=$(du -sb "${ESP_TREE}" | awk '{print $1}')
# Round up to nearest MB then add headroom
ESP_MB=$(( (ESP_BYTES + 1024*1024 - 1) / (1024*1024) + ESP_HEADROOM_MB ))
# Minimum FAT32 size is technically 32 MB; below that we'd be in FAT16 territory.
# Most ESPs end up >= 32 MB anyway between shim+grub+UKI; bump if smaller.
[ "$ESP_MB" -lt 33 ] && ESP_MB=33

echo "[build-iso]       computed ESP size: ${ESP_MB} MB (content $((ESP_BYTES/1024/1024)) MB + ${ESP_HEADROOM_MB} headroom)"

dd if=/dev/zero of="$ESP_IMG" bs=1M count="$ESP_MB" status=none

# mkfs.vfat: -F32 forces FAT32 (above the 32MB FAT16/FAT32 boundary it picks
# FAT32 anyway, but explicit is clearer); -n VOLID sets the volume label.
# `MTOOLS_SKIP_CHECK=1` lets mcopy work on a raw image without partition table.
#
# The 32-bit FAT volume serial ID defaults to a time-derived number per
# `man mkfs.vfat`. Two same-SDE runs would otherwise produce different
# volume serials → different ESP_IMG bytes → different ISO bytes.
# Derive it deterministically from the lower 32 bits of SOURCE_DATE_EPOCH.
VOLSERIAL=$(printf '%08x' $((SOURCE_DATE_EPOCH & 0xffffffff)))
mkfs.vfat -F 32 -i "$VOLSERIAL" -n "${VOLID:0:11}" "$ESP_IMG" >/dev/null

# Lock every staged-ESP file's mtime to SOURCE_DATE_EPOCH before mcopy reads
# them. Without this, mcopy bakes in whatever mtime the local `cp` produced
# (host-local "now"), which diverges across hosts even with identical SDE.
# mtools' 2-second FAT precision normalisation doesn't help when the source
# inputs differ by hours/days.
find "$ESP_TREE" -exec touch -d "@${SOURCE_DATE_EPOCH}" {} +

# Copy the ESP tree into the FAT32 image. With the touch+find above, mcopy
# now writes deterministic FAT timestamps across hosts.
export MTOOLS_SKIP_CHECK=1

mmd -i "$ESP_IMG" ::EFI
mmd -i "$ESP_IMG" ::EFI/BOOT
mmd -i "$ESP_IMG" ::EFI/InterGenOS
mmd -i "$ESP_IMG" ::EFI/InterGenOS/themes
mmd -i "$ESP_IMG" ::EFI/InterGenOS/fonts

# Copy files into ESP image preserving the directory structure
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/BOOT/BOOTX64.EFI"            ::EFI/BOOT/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/shimx64.efi"     ::EFI/InterGenOS/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/grubx64.efi"     ::EFI/InterGenOS/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/igos-live.efi"   ::EFI/InterGenOS/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/grub.cfg"        ::EFI/InterGenOS/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/fonts/unicode.pf2" ::EFI/InterGenOS/fonts/

if [ -n "$THEME_DIR" ]; then
    # mcopy -s copies recursively
    mcopy -i "$ESP_IMG" -s "${ESP_TREE}/EFI/InterGenOS/themes/${THEME_DIR##*/}" ::EFI/InterGenOS/themes/
fi

echo "[build-iso]       ESP image: $(stat -c%s "$ESP_IMG") bytes"

# --------------------------------------------------------------------------
# Step 3: ISO9660 root layout
# --------------------------------------------------------------------------

echo "[build-iso] [3/6] staging ISO9660 root"

cp "$SQUASHFS" "${ISO_ROOT}/live/filesystem.squashfs"
sha256sum "${ISO_ROOT}/live/filesystem.squashfs" \
    | awk '{print $1, "filesystem.squashfs"}' \
    > "${ISO_ROOT}/live/filesystem.sha256"

# A volume marker file at the root makes it trivial for an external test or
# a recovery initramfs to identify "yes this is the InterGenOS live ISO".
echo "${VOLID}" > "${ISO_ROOT}/IGOS_LIVE"

# --------------------------------------------------------------------------
# Step 4: xorriso invocation
# --------------------------------------------------------------------------

echo "[build-iso] [4/6] running xorriso"

mkdir -p "$(dirname "$OUTPUT")"

# Flags rationale:
#   -as mkisofs                   : mkisofs-compatible flag dialect (most
#                                   distros' build scripts speak this)
#   -iso-level 3                  : large file support (>4GB), Joliet ext.
#   -full-iso9660-filenames       : 31-char filenames (vs 8.3); easier paths
#   -volid "$VOLID"               : ISO9660 volume id; used by /init's
#                                   `root=live:LABEL=$VOLID` discovery
#   -append_partition 2 0xef ESP  : embed ESP image as MBR/GPT partition 2
#                                   with EFI System Partition type (0xef)
#   -appended_part_as_gpt         : route the appended partition through GPT
#                                   too (UEFI firmware reads GPT)
#   -e --interval:appended_partition_2:all:: : El Torito alt-boot points at
#                                   the appended ESP partition (not a separate
#                                   img file inside the ISO)
#   -no-emul-boot                 : not floppy-emulating
#   -isohybrid-gpt-basdat         : write a hybrid MBR with the GPT bootable
#                                   data partition; lets BIOS-legacy boot
#                                   from the same .iso file when burned
#   --mbr-force-bootable          : force the MBR partition flag bootable
#                                   (some BIOSes require this)
xorriso \
    -as mkisofs \
    -iso-level 3 \
    -full-iso9660-filenames \
    -volid "$VOLID" \
    -appended_part_as_gpt \
    -append_partition 2 0xef "$ESP_IMG" \
    -partition_offset 16 \
    -e --interval:appended_partition_2:all:: \
    -no-emul-boot \
    -isohybrid-gpt-basdat \
    --mbr-force-bootable \
    -output "$OUTPUT" \
    "$ISO_ROOT"

if [ ! -f "$OUTPUT" ]; then
    echo "FAIL: xorriso did not produce $OUTPUT" >&2
    exit 1
fi

# --------------------------------------------------------------------------
# Step 5: self-verify
# --------------------------------------------------------------------------

echo "[build-iso] [5/6] self-verify"

# 5a. xorriso -indev report — confirms GPT, ESP partition, El Torito boot
# record. Captured to a separate report file so the harness can grep it.
INDEV_REPORT="${LOG_DIR}/indev_${LOG_TIMESTAMP}.txt"
xorriso -indev "$OUTPUT" -report_about ALL > "$INDEV_REPORT" 2>&1 || true

VERIFY_FAIL=0

if ! grep -qE '\b[Gg][Pp][Tt]\b' "$INDEV_REPORT"; then
    echo "FAIL: GPT not detected in xorriso -report_about output" >&2
    VERIFY_FAIL=1
fi

if ! grep -qE '\b(El Torito|eltorito|0xef)\b' "$INDEV_REPORT"; then
    echo "FAIL: ESP/El Torito boot record not detected" >&2
    VERIFY_FAIL=1
fi

# 5b. file probe — quick sanity that this is a hybrid bootable image
FILE_PROBE=$(file -b "$OUTPUT")
echo "[build-iso]       file: $FILE_PROBE"
if ! echo "$FILE_PROBE" | grep -qE "ISO 9660|DOS/MBR boot sector"; then
    echo "FAIL: output doesn't look like an ISO9660/hybrid image" >&2
    VERIFY_FAIL=1
fi

# 5c. checksum
ISO_SHA256=$(sha256sum "$OUTPUT" | awk '{print $1}')
ISO_BYTES=$(stat -c%s "$OUTPUT")
ISO_MB=$((ISO_BYTES / 1024 / 1024))

if [ "$VERIFY_FAIL" -ne 0 ]; then
    echo "FAIL: self-verify failed; see $INDEV_REPORT for details" >&2
    exit 1
fi

# --------------------------------------------------------------------------
# Step 6: emit summary
# --------------------------------------------------------------------------

echo "[build-iso] [6/6] PASS"
echo "[build-iso] ============================================================"
echo "[build-iso] output:  $OUTPUT"
echo "[build-iso]   size:  ${ISO_MB} MB ($ISO_BYTES bytes)"
echo "[build-iso]   sha256: $ISO_SHA256"
echo "[build-iso]   volid:  $VOLID"
echo "[build-iso]   sde:    $SOURCE_DATE_EPOCH"
echo "[build-iso] log:     $LOG_FILE"
echo "[build-iso] indev:   $INDEV_REPORT"
echo "[build-iso] ============================================================"
echo ""
echo "Next steps:"
echo "  Test boot in QEMU+OVMF+swtpm:"
echo "    qemu-system-x86_64 -bios /usr/share/OVMF/OVMF_CODE.fd \\"
echo "        -drive file=$OUTPUT,format=raw,if=virtio -m 4G"
echo "  Run the verify-b2-reproducibility harness against two builds:"
echo "    SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH scripts/build-iso.sh ... # second run"
echo "    scripts/verify-b2-reproducibility.sh <iso-1> <iso-2>"
