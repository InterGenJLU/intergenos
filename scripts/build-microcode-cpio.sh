#!/usr/bin/env bash
# build-microcode-cpio.sh — generate Intel + AMD CPU microcode early-load cpio archives
#
# Produces two optional artifacts in OUTPUT_DIR:
#   intel-ucode.img  — early-firmware cpio of /lib/firmware/intel-ucode/ (via iucode_tool)
#   amd-ucode.img    — newc cpio of /kernel/x86/microcode/AuthenticAMD.bin
#                      (concatenation of /lib/firmware/amd-ucode/microcode_amd*.bin{,.xz})
#
# Kernel format reference: Documentation/x86/microcode.rst (Linux mainline).
# Intel uses iucode_tool's --write-earlyfw which produces the kernel-expected
# layout (kernel/x86/microcode/GenuineIntel.bin wrapped in cpio). AMD uses
# the same layout under AuthenticAMD.bin assembled here from linux-firmware's
# per-family blobs.
#
# Each output is independent — only present if its source firmware is present.
# Caller checks for file existence before passing to ukify or GRUB.
#
# Usage:
#   OUTPUT_DIR=/path/to/out  scripts/build-microcode-cpio.sh
#
# Optional env:
#   OUTPUT_DIR     — where to write the cpio archives (default: current dir)
#   FIRMWARE_ROOT  — firmware tree root (default: /lib/firmware)
#                    intel-ucode/ + amd-ucode/ are read from $FIRMWARE_ROOT/
#   IUCODE_TOOL    — iucode_tool binary path (default: /usr/sbin/iucode_tool;
#                    falls back to PATH lookup if absent)
#
# Exit 0 on success (zero or more artifacts produced). Non-zero only on
# unexpected error (missing iucode_tool when intel firmware present;
# write-permission failure on OUTPUT_DIR; cpio tool missing).

set -euo pipefail

OUTPUT_DIR="${OUTPUT_DIR:-.}"
FIRMWARE_ROOT="${FIRMWARE_ROOT:-/lib/firmware}"
IUCODE_TOOL="${IUCODE_TOOL:-/usr/sbin/iucode_tool}"

mkdir -p "$OUTPUT_DIR"

# ---- Intel microcode -------------------------------------------------------
# iucode_tool builds the early-firmware cpio in the kernel-expected format
# (kernel/x86/microcode/GenuineIntel.bin inside a newc cpio). The tool also
# performs CPU-signature filtering when run on the target host; here we
# include the full microcode pack because the resulting cpio runs on
# whichever CPU boots the resulting UKI (the kernel picks the matching
# signature at boot).
INTEL_SRC="$FIRMWARE_ROOT/intel-ucode"
if [ -d "$INTEL_SRC" ]; then
    if ! command -v "$IUCODE_TOOL" >/dev/null 2>&1 && ! command -v iucode_tool >/dev/null 2>&1; then
        echo "ERROR: $INTEL_SRC exists but iucode_tool is not available (expected at $IUCODE_TOOL or in PATH)" >&2
        exit 1
    fi
    TOOL="$IUCODE_TOOL"
    command -v "$TOOL" >/dev/null 2>&1 || TOOL=iucode_tool
    if "$TOOL" -S "$INTEL_SRC/" --write-earlyfw="$OUTPUT_DIR/intel-ucode.img" 2>/dev/null; then
        if [ -s "$OUTPUT_DIR/intel-ucode.img" ]; then
            echo "intel-ucode.img written ($(stat -c %s "$OUTPUT_DIR/intel-ucode.img") bytes)"
        else
            # iucode_tool exited 0 but produced empty output (no signatures matched
            # the filter). Remove the zero-byte file so callers' file-existence
            # checks behave as "absent" rather than "present but empty".
            rm -f "$OUTPUT_DIR/intel-ucode.img"
            echo "intel-ucode.img skipped (iucode_tool produced no signatures)"
        fi
    else
        # iucode_tool failed (corrupt firmware blob, etc.) — surface but don't break.
        echo "WARNING: iucode_tool failed for $INTEL_SRC — intel-ucode.img not produced" >&2
    fi
else
    echo "intel-ucode.img skipped ($INTEL_SRC absent)"
fi

# ---- AMD microcode ---------------------------------------------------------
# AMD format: concatenate all per-family microcode_amd*.bin blobs into a
# single AuthenticAMD.bin, then wrap that inside a newc cpio at
# kernel/x86/microcode/AuthenticAMD.bin. The kernel walks the resulting
# blob at early-firmware load and selects the matching family/model.
# linux-firmware ships these blobs at /lib/firmware/amd-ucode/ either
# uncompressed (.bin) or xz-compressed (.bin.xz) depending on distro
# firmware-package conventions.
AMD_SRC="$FIRMWARE_ROOT/amd-ucode"
if [ -d "$AMD_SRC" ]; then
    if ! command -v cpio >/dev/null 2>&1; then
        echo "ERROR: $AMD_SRC exists but cpio is not available" >&2
        exit 1
    fi
    WORK=$(mktemp -d)
    # Standard cleanup trap; the IGOS_HELPER_USER_CLEANUP convention used by
    # other helpers in this directory isn't needed here (single-purpose
    # helper with no user cleanup hook to compose with).
    trap 'rm -rf "$WORK"' EXIT
    mkdir -p "$WORK/kernel/x86/microcode"
    for blob in "$AMD_SRC"/microcode_amd*.bin "$AMD_SRC"/microcode_amd*.bin.xz; do
        [ -f "$blob" ] || continue
        case "$blob" in
            *.xz) xz -dc "$blob" >> "$WORK/kernel/x86/microcode/AuthenticAMD.bin" ;;
            *)    cat "$blob" >> "$WORK/kernel/x86/microcode/AuthenticAMD.bin" ;;
        esac
    done
    if [ -s "$WORK/kernel/x86/microcode/AuthenticAMD.bin" ]; then
        ( cd "$WORK" && find kernel | cpio --create --quiet --format=newc ) > "$OUTPUT_DIR/amd-ucode.img"
        if [ -s "$OUTPUT_DIR/amd-ucode.img" ]; then
            echo "amd-ucode.img written ($(stat -c %s "$OUTPUT_DIR/amd-ucode.img") bytes)"
        fi
    else
        echo "amd-ucode.img skipped (no amd-ucode blobs in $AMD_SRC)"
    fi
else
    echo "amd-ucode.img skipped ($AMD_SRC absent)"
fi

exit 0
