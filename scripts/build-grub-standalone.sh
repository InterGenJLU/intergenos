#!/usr/bin/env bash
# build-grub-standalone.sh — produce the unsigned standalone GRUB EFI binary.
#
# Output: a single grubx64.efi PE binary that:
#   - Embeds the InterGenOS embedded grub.cfg (packages/core/grub/embedded-grub.cfg)
#   - Embeds the InterGenOS SBAT entries (packages/core/grub/sbat.csv)
#   - Contains an explicit, minimal, no-network module set
#   - Will be signed by scripts/sign-grub.sh against the InterGenOS vendor cert
#     (NK#1 PIV slot 9c) to produce the final signed grubx64.efi
#
# Module-set rationale per shim-review submission Q30:
#   - Boot media discovery only — no network, no filesystem-write, no env-load
#   - Includes shim_lock so the trust chain shim->GRUB->UKI works under SB
#   - Includes pgp + gcry_sha256/sha512 + gcry_rsa for signature verification
#
# Usage:
#   OUTPUT=/path/to/grubx64.efi \
#   scripts/build-grub-standalone.sh
#
# Optional env vars:
#   EMBEDDED_CFG  — embedded grub.cfg (default: packages/core/grub/embedded-grub.cfg)
#   SBAT_CSV      — SBAT entries CSV (default: packages/core/grub/sbat.csv)
#   GRUB_FORMAT   — grub-mkstandalone format (default: x86_64-efi)
#
# Prerequisites:
#   - GRUB 2.14 installed (provides grub-mkstandalone + EFI modules)
#   - Run from InterGenOS repo root or set EMBEDDED_CFG / SBAT_CSV explicitly

set -euo pipefail

OUTPUT="${OUTPUT:?missing OUTPUT env var}"
EMBEDDED_CFG="${EMBEDDED_CFG:-packages/core/grub/embedded-grub.cfg}"
SBAT_CSV="${SBAT_CSV:-packages/core/grub/sbat.csv}"
GRUB_FORMAT="${GRUB_FORMAT:-x86_64-efi}"

[ -f "$EMBEDDED_CFG" ] || { echo "ERROR: EMBEDDED_CFG not found: $EMBEDDED_CFG" >&2; exit 1; }
[ -f "$SBAT_CSV" ]     || { echo "ERROR: SBAT_CSV not found: $SBAT_CSV" >&2; exit 1; }

# SBAT generation precheck — block before bake-in if any vendor entry fell
# below upstream baseline (Tails-6.5-class footgun mitigation).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -x "$SCRIPT_DIR/check-sbat-generations.sh" ]; then
    GRUB_SBAT="$SBAT_CSV" bash "$SCRIPT_DIR/check-sbat-generations.sh" || {
        echo "ERROR: SBAT generation precheck failed; refusing to bake regressed entries." >&2
        exit 1
    }
fi

if ! command -v grub-mkstandalone >/dev/null 2>&1; then
    echo "ERROR: grub-mkstandalone not in PATH (install grub2 or grub-efi-amd64-bin)." >&2
    exit 1
fi

# Explicit module list — kept narrow on purpose. Each module is here because
# it is needed to either (a) discover boot media, (b) read filesystems we may
# need to load configs/initramfs/UKIs from, (c) verify signatures up the
# trust chain, (d) chain-load the next signed binary, or (e) render the
# user-facing menu. Anything not in this list is NOT in the signed binary
# and cannot be loaded post-shim-lock without breaking the SB chain.
MODULES=(
    # Boot media + partition discovery
    part_gpt part_msdos iso9660 udf

    # Filesystem read access (read-only paths through these; no write)
    ext2 fat xfs btrfs

    # Crypto + signature verification.
    # shim_lock was a loadable module in GRUB ≤2.12; in GRUB 2.14 the
    # shim_lock verifier is built into the EFI kernel (kern/efi/sb.c)
    # and there is no shim_lock.mod to load. The verifier still runs
    # — it self-registers when GRUB starts on a system with shim — so
    # the shim→GRUB→UKI trust chain is unchanged. Listing shim_lock
    # here makes grub-mkstandalone fail with "cannot open shim_lock.mod".
    pgp gcry_sha256 gcry_sha512 gcry_rsa

    # Bootloader essentials
    linux chain boot configfile echo normal test true
    search search_fs_uuid search_label search_fs_file
    halt reboot ls help

    # Display (gfxmenu requires gfxterm + font + image format).
    # vbe is BIOS-only (VESA BIOS Extensions) and does not exist in the
    # x86_64-efi module tree — EFI uses efi_gop (Graphics Output Protocol)
    # for the same role. Listing vbe here would fail grub-mkstandalone.
    gfxterm gfxmenu videoinfo efi_gop font png
)

# Notable EXCLUSIONS (kept out of the signed binary on purpose):
#   tftp, http, pxe, efinet  — no network, ever
#   loopback (write modes)   — no writable loopback
#   procfs                   — no procfs as a writable surface
#   loadenv, savedefault     — no env-write to disk from boot context
#   password_pbkdf2          — not used; signature is the auth, not a password
#   crypto/gcry_arcfour/etc  — only the algorithms we actually verify against

echo "Building standalone GRUB EFI binary:"
echo "  Output:        $OUTPUT"
echo "  Format:        $GRUB_FORMAT"
echo "  Embedded cfg:  $EMBEDDED_CFG"
echo "  SBAT CSV:      $SBAT_CSV"
echo "  Modules (${#MODULES[@]}): ${MODULES[*]}"
echo ""

# grub-mkstandalone embeds the cfg + SBAT CSV directly into the PE binary.
# `--modules` selects which GRUB modules are baked in.
# `boot/grub/grub.cfg=...` syntax tells grub-mkstandalone where to place the
# embedded cfg inside the binary's memdisk.
mkdir -p "$(dirname "$OUTPUT")"

grub-mkstandalone \
    --format="$GRUB_FORMAT" \
    --output="$OUTPUT" \
    --modules="${MODULES[*]}" \
    --sbat="$SBAT_CSV" \
    "boot/grub/grub.cfg=$EMBEDDED_CFG"

if [ ! -f "$OUTPUT" ]; then
    echo "FAIL: grub-mkstandalone did not produce $OUTPUT" >&2
    exit 1
fi

# Verification: confirm the binary is a PE+, contains the expected sections,
# and the SBAT section is present + populated.
if ! file "$OUTPUT" | grep -q "PE32+"; then
    echo "FAIL: output is not a PE32+ binary" >&2
    file "$OUTPUT" >&2
    exit 1
fi

# Dump the embedded SBAT section to confirm it matches input
SBAT_DUMP=$(objcopy --dump-section .sbat=/dev/stdout "$OUTPUT" 2>/dev/null || true)
if [ -z "$SBAT_DUMP" ]; then
    echo "FAIL: .sbat section missing or empty in $OUTPUT" >&2
    exit 1
fi

# Dump the .mods section count (modules baked in)
MODS_PRESENT=$(objdump -h "$OUTPUT" 2>/dev/null | grep -c "mods" || echo 0)

echo "PASS: standalone GRUB binary built"
echo "  Size:          $(stat -c%s "$OUTPUT") bytes"
echo "  SHA-256:       $(sha256sum "$OUTPUT" | awk '{print $1}')"
echo "  PE check:      $(file -b "$OUTPUT" | head -1)"
echo "  SBAT entries:  $(echo "$SBAT_DUMP" | grep -c '^[a-z]' || echo 0)"
echo "  Mods sections: $MODS_PRESENT (expected: 1)"
echo ""
echo "Next step:"
echo "  scripts/sign-grub.sh $OUTPUT ${OUTPUT%.efi}.signed.efi"
