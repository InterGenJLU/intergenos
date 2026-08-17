#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

# Shared build-output library — one house style across the shell pipeline
# (TTY-aware color, the ✓/✗/⚠ markers, severity voice). build-iso emits its
# own `[build-iso]`-prefixed lines directly; the library is sourced so its
# color/marker vars are available and the voice stays consistent.
# shellcheck source=lib/logging.sh
[ -f "$(dirname "$0")/lib/logging.sh" ] && source "$(dirname "$0")/lib/logging.sh"

# --------------------------------------------------------------------------
# D-007 compliance gate (Class A — blocks ISO assembly on violation)
# --------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "${SCRIPT_DIR}/check-d007-compliance.sh" ]; then
    echo "[build-iso] Running SSH and credentials posture gate..." >&2  # D-007 gate
    if ! "${SCRIPT_DIR}/check-d007-compliance.sh"; then
        echo "" >&2
        echo "[build-iso] error: SSH and credentials posture gate failed." >&2
        echo "[build-iso] refusing to assemble ISO with SSH/credentials posture violations." >&2
        echo "[build-iso] See scripts/check-d007-compliance.sh output above." >&2
        exit 1
    fi
    echo "[build-iso] SSH and credentials posture gate passed" >&2
fi

# --------------------------------------------------------------------------
# D-010 compliance gate (Class A — blocks ISO assembly on violation)
# --------------------------------------------------------------------------
if [ -x "${SCRIPT_DIR}/check-d010-compliance.sh" ]; then
    echo "[build-iso] Running AI assistant opt-in posture gate..." >&2  # D-010 gate
    if ! "${SCRIPT_DIR}/check-d010-compliance.sh"; then
        echo "" >&2
        echo "[build-iso] error: AI assistant opt-in posture gate failed." >&2
        echo "[build-iso] refusing to assemble ISO with InterGen AI opt-in posture violations." >&2
        echo "[build-iso] See scripts/check-d010-compliance.sh output above." >&2
        exit 1
    fi
    echo "[build-iso] AI assistant opt-in posture gate passed" >&2
fi

# --------------------------------------------------------------------------
# D-011 compliance gate (Class A — blocks ISO assembly on violation)
# --------------------------------------------------------------------------
if [ -x "${SCRIPT_DIR}/check-d011-compliance.sh" ]; then
    echo "[build-iso] Running default-deny firewall posture gate..." >&2  # D-011 gate
    if ! "${SCRIPT_DIR}/check-d011-compliance.sh"; then
        echo "" >&2
        echo "[build-iso] error: default-deny firewall posture gate failed." >&2
        echo "[build-iso] refusing to assemble ISO with firewall-policy violations." >&2
        echo "[build-iso] See scripts/check-d011-compliance.sh output above." >&2
        exit 1
    fi
    echo "[build-iso] default-deny firewall posture gate passed" >&2
fi

# --------------------------------------------------------------------------
# D-008 compliance gate (Class A v1.0 ship-block; blocks ISO assembly on
# violation when InterGen is included. K15 v1.0 minimum sub-item 7.)
# --------------------------------------------------------------------------
if [ -x "${SCRIPT_DIR}/check-d008-compliance.sh" ]; then
    echo "[build-iso] Running InterGen provenance-gate gate..." >&2  # D-008 gate
    if ! "${SCRIPT_DIR}/check-d008-compliance.sh"; then
        echo "" >&2
        echo "[build-iso] error: InterGen provenance-gate gate failed." >&2
        echo "[build-iso] refusing to assemble ISO with InterGen provenance-gate violations." >&2
        echo "[build-iso] See scripts/check-d008-compliance.sh output above." >&2
        exit 1
    fi
    echo "[build-iso] InterGen provenance-gate gate passed" >&2
fi

# Install-integrity staging gate (Class A — blocks ISO assembly on violation)
# build-squashfs Step 4.8 emits this marker AFTER staging + fail-closed
# asserting the verity-sealed trust triplet (release) or the
# IGOS_DEV_ALLOW_UNVERIFIED marker (UNSIGNED_TEST). build-iso consumes an
# already-sealed squashfs and cannot cheaply peer inside it, so it asserts the
# marker's presence here instead. Absence => Step 4.8 never ran (or failed and
# cleared the marker) => refuse to assemble. Mirrors the D-007/D-008 shape.
INTEGRITY_MARKER="${INTEGRITY_MARKER:-/mnt/intergenos/build/.install-integrity-staging.marker}"
echo "[build-iso] Running install-integrity staging gate..." >&2
if [ ! -s "$INTEGRITY_MARKER" ]; then
    echo "[build-iso] error: install-integrity staging marker absent at $INTEGRITY_MARKER." >&2
    echo "[build-iso] build-squashfs Step 4.8 did not stage+assert the trust triplet" >&2
    echo "[build-iso] (or the IGOS_DEV_ALLOW_UNVERIFIED dev marker). The squashfs may" >&2
    echo "[build-iso] ship without a verifiable install-integrity set. Refusing to assemble ISO." >&2
    exit 1
fi
echo "[build-iso] install-integrity staging gate passed ($(head -1 "$INTEGRITY_MARKER"))" >&2

# --------------------------------------------------------------------------
# Inputs + defaults
# --------------------------------------------------------------------------

# UNSIGNED_TEST=1 — build a test-only ISO from unsigned grub + UKIs. Owner-
# authorized 2026-05-15 to facilitate Option A pre-signing-ceremony smoke
# tests. The script does NOT validate signatures (xorriso just packages
# files), so this flag relaxes only the documentation contract and adds a
# loud banner + a marker on the output filename so an unsigned-test ISO
# cannot be silently confused with a release ISO. Secure Boot OFF is
# required on any VM booting an unsigned-test ISO; SB ON will reject the
# unsigned grub/UKI at firmware-verify time.
UNSIGNED_TEST="${UNSIGNED_TEST:-0}"
if [ "$UNSIGNED_TEST" = "1" ]; then
    echo "[build-iso] >>> unsigned-test mode" >&2
    echo "[build-iso]     ISO will boot only on VMs/hosts with Secure Boot disabled." >&2
    echo "[build-iso]     Output filename will carry an .unsigned-test marker suffix." >&2
    echo "[build-iso]     Do not release this artifact; release builds require signing." >&2
fi

SHIM="${SHIM:?missing SHIM env var (signed shimx64.efi)}"
# PI-ge9b04-A: MokManager is REQUIRED. shim only looks for mmx64.efi in the
# directory it was launched from; a live ESP without it dead-ends an SB=ON
# boot on virgin hardware at Verification Failed (0x1A) with no enrollment
# path (and bootloops when a MokNew enrollment is pending). Same
# Fedora-MS-signed provenance as shim — never re-signed by us.
MOKMANAGER="${MOKMANAGER:?missing MOKMANAGER env var (mmx64.efi from shim-signed)}"
# Optional: the InterGenOS CA cert, staged in DER form so MokManager's
# "Enroll key from disk" can consume it on a virgin box (PEM is not
# MokManager-readable). Empty/absent skips with a loud note.
CA_CERT="${CA_CERT:-}"
GRUB="${GRUB:?missing GRUB env var (signed grubx64.efi or unsigned grubx64.efi if UNSIGNED_TEST=1)}"
UKI_LIVE="${UKI_LIVE:?missing UKI_LIVE env var (signed igos-live.efi or unsigned if UNSIGNED_TEST=1)}"
UKI_INSTALL_GUI="${UKI_INSTALL_GUI:?missing UKI_INSTALL_GUI env var}"
UKI_INSTALL_TUI="${UKI_INSTALL_TUI:?missing UKI_INSTALL_TUI env var}"
SQUASHFS="${SQUASHFS:?missing SQUASHFS env var (filesystem.squashfs)}"
OUTPUT="${OUTPUT:?missing OUTPUT env var (.iso path)}"

# Append the unsigned-test marker to the OUTPUT path so a release ISO and
# a test ISO cannot share a filename even if the caller forgot to scope it.
if [ "$UNSIGNED_TEST" = "1" ]; then
    case "$OUTPUT" in
        *.unsigned-test.iso) ;;                       # already marked
        *.iso) OUTPUT="${OUTPUT%.iso}.unsigned-test.iso" ;;
        *)     OUTPUT="${OUTPUT}.unsigned-test.iso"   ;;
    esac
    echo "[build-iso] UNSIGNED-TEST output path: $OUTPUT" >&2
fi

GRUB_CFG="${GRUB_CFG:-installer/iso/grub/grub.cfg}"
# K1 closure 2026-05-21: default THEME_DIR to the intergenos-grub-theme
# package's theme source dir so the live-ISO GRUB menu ships with the
# operator-authored resolution-aware background scaffold automatically.
# Caller may still override THEME_DIR= explicitly. Resolved relative to
# this script's location so the build doesn't depend on a particular cwd.
_BUILD_ISO_REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
THEME_DIR="${THEME_DIR:-${_BUILD_ISO_REPO_ROOT}/packages/desktop/intergenos-grub-theme/assets/grub-theme/intergenos}"
UNICODE_PF2="${UNICODE_PF2:-/usr/share/grub/unicode.pf2}"
LOG_DIR="${LOG_DIR:-build/logs/iso}"
ESP_HEADROOM_MB="${ESP_HEADROOM_MB:-16}"

# SOURCE_DATE_EPOCH propagation. xorriso 1.5.6+ honours the env var directly;
# mtools honours it via a config knob. If unset, derive from build start so
# downstream attestation can record what we actually used (rather than "now").
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
    SOURCE_DATE_EPOCH=$(date -u +%s)
    export SOURCE_DATE_EPOCH
    echo "[build-iso] warning: SOURCE_DATE_EPOCH not set; using build start ${SOURCE_DATE_EPOCH}" >&2
    echo "[build-iso]       Reproducibility requires the caller to set this explicitly." >&2
fi

# --------------------------------------------------------------------------
# ISO SBOM gate (Class A — blocks ISO assembly on refusal)
# scripts/iso-sbom-gen.py writes the SPDX 2.3 document for the EXACT shipped
# set (the effective-iso_include partition, same resolution as the exclusion
# deriver). An identity refusal means we cannot prove what this ISO ships —
# refuse to assemble, EVERY lane. Release builds additionally require every
# shipped package's staged archive present and hashed (--require-archives);
# unsigned-test ISOs may build from a bare checkout (identity-only SBOM).
# Runs after SOURCE_DATE_EPOCH resolution so the document's created stamp is
# deterministic. Output lands beside the ISO as <output>.sbom.spdx.json.
# --------------------------------------------------------------------------
SBOM_OUTPUT="${SBOM_OUTPUT:-${OUTPUT%.iso}.sbom.spdx.json}"
SBOM_ARCHIVES="${SBOM_ARCHIVES:-/mnt/igos/var/lib/igos/archives}"
SBOM_ARGS=( --output "$SBOM_OUTPUT" --iso-tag "$(basename "$OUTPUT" .iso)" )
if [ -d "$SBOM_ARCHIVES" ]; then
    SBOM_ARGS+=( --archives "$SBOM_ARCHIVES" )
fi
if [ "$UNSIGNED_TEST" != "1" ]; then
    SBOM_ARGS+=( --require-archives )
fi
echo "[build-iso] Running ISO SBOM gate (SPDX 2.3)..." >&2
if ! python3 "${_BUILD_ISO_REPO_ROOT}/scripts/iso-sbom-gen.py" "${SBOM_ARGS[@]}" >&2; then
    echo "[build-iso] error: ISO SBOM generation refused." >&2
    echo "[build-iso] The shipped package set cannot be proven; refusing to assemble ISO." >&2
    exit 1
fi
echo "[build-iso] ISO SBOM gate passed — document at $SBOM_OUTPUT" >&2

# Derive default VOLID from the candidate tag (OUTPUT basename) + the
# SOURCE_DATE_EPOCH date, so a stick identifies WHICH build it carries, not
# just when it was built (two sticks side by side were indistinguishable —
# the date-only label needed the builder's date-to-candidate mapping).
# Deterministic for a given OUTPUT + SDE. ISO9660 volume ids are A-Z0-9_,
# max 32 chars; the tag is sanitized and the whole id truncated to fit.
if [ -z "${VOLID:-}" ]; then
    _tag=$(basename "$OUTPUT")
    _tag="${_tag#intergenos-}"
    _tag="${_tag%.unsigned-test.iso}"
    _tag="${_tag%.iso}"
    _tag=$(printf '%s' "$_tag" | tr '[:lower:]-' '[:upper:]_' | tr -cd 'A-Z0-9_')
    VOLID="IGOS_${_tag}_$(date -u -d "@${SOURCE_DATE_EPOCH}" +%Y%m%d)"
    VOLID="${VOLID:0:32}"
fi

# --------------------------------------------------------------------------
# Required-file checks
# --------------------------------------------------------------------------

[ -f "$SHIM" ]      || { echo "error: SHIM not found: $SHIM" >&2; exit 1; }
[ -f "$GRUB" ]      || { echo "error: GRUB not found: $GRUB" >&2; exit 1; }
for var in UKI_LIVE UKI_INSTALL_GUI UKI_INSTALL_TUI; do
    path="${!var}"
    [ -f "$path" ] || { echo "error: $var not found: $path" >&2; exit 1; }
done
[ -f "$SQUASHFS" ]  || { echo "error: SQUASHFS not found: $SQUASHFS" >&2; exit 1; }
[ -f "$GRUB_CFG" ]  || { echo "error: GRUB_CFG not found: $GRUB_CFG" >&2; exit 1; }
[ -f "$UNICODE_PF2" ] || { echo "error: UNICODE_PF2 not found: $UNICODE_PF2" >&2; \
                            echo "Set UNICODE_PF2 env var to the path of unicode.pf2 " \
                                 "(usually shipped by grub2 / grub-common)." >&2; \
                            exit 1; }

if [ -n "$THEME_DIR" ] && [ ! -d "$THEME_DIR" ]; then
    echo "error: THEME_DIR set but directory not found: $THEME_DIR" >&2
    exit 1
fi

# PE-binary shape probe — pre-trust-boundary cheap check that the upstream
# signing-helper actually wrote PE32+ binaries. Catches truncated writes,
# wrong-architecture binaries, plain text smuggled in.
for binary in "$SHIM" "$GRUB" "$UKI_LIVE" "$UKI_INSTALL_GUI" "$UKI_INSTALL_TUI"; do
    if ! file -b "$binary" | grep -q "PE32+"; then
        echo "error: $binary is not a PE32+ binary" >&2
        echo "       file says: $(file -b "$binary")" >&2
        exit 1
    fi
done

# --------------------------------------------------------------------------
# Signed-input signature gate (2026-07-11): release ⇒ every artifact WE sign
# carries a VALID Authenticode signature at assembly time.
# --------------------------------------------------------------------------
# The ceremony verifies signatures at delivery, but the assembly is the last
# writer-adjacent step before the ISO seals them in — anything that corrupts
# a signed input between delivery and here would otherwise ship a boot that
# shim/GRUB rejects under Secure Boot. This is not hypothetical: the first
# ge9b-01 mint (2026-07-11) was destroyed because this script's own roothash
# gate ran objcopy with the UKI as its only file argument, which REWRITES the
# input in place and drops the Authenticode table — three signature-stripped
# UKIs assembled into the ISO with every then-existing check green. This gate
# makes that class impossible to ship. Verified against the in-tree PUBLIC
# vendor CA (an earlier comment claimed the build VM lacks the cert — stale:
# the cert is public and rides the repo tree). shim is excluded: it carries
# Microsoft's signature, not ours; the PE32+ probe above still covers it.
if [ "$UNSIGNED_TEST" != "1" ]; then
    command -v sbverify >/dev/null 2>&1 || {
        echo "error: 'sbverify' not in PATH (sbsigntool) — required to verify signed inputs" >&2
        exit 1
    }
    VENDOR_CERT="${VENDOR_CERT:-${_BUILD_ISO_REPO_ROOT}/docker/shim-build/vendor-cert/intergenos-secure-boot-ca.pem}"
    if [ ! -f "$VENDOR_CERT" ]; then
        echo "error: vendor cert not found: $VENDOR_CERT (set VENDOR_CERT= to override)" >&2
        exit 1
    fi
    for var in GRUB UKI_LIVE UKI_INSTALL_GUI UKI_INSTALL_TUI; do
        path="${!var}"
        if ! sbverify --cert "$VENDOR_CERT" "$path" >/dev/null 2>&1; then
            echo "FAIL: $var ($path) has no valid signature against the vendor cert —" >&2
            echo "      $(sbverify --cert "$VENDOR_CERT" "$path" 2>&1 | tr '\n' ' ')" >&2
            echo "      A release input must be the ceremony's verified .signed artifact," >&2
            echo "      unmodified. Re-deliver from the signing staging dir and re-run." >&2
            exit 1
        fi
        echo "[build-iso] ✓ $var signature valid (vendor cert)"
    done
fi

# --------------------------------------------------------------------------
# S3-F2 gate (decided 2026-07-08): release ⇒ verity SEALED.
# --------------------------------------------------------------------------
# init.sh no longer has an unauthenticated fallback (S3-F1): a boot without
# igos.verity.roothash= on the cmdline fail-closes unless the explicit dev
# marker is present. So an ISO whose UKIs do NOT seal the roothash would ship
# a boot that halts on real hardware — catch that HERE, at assembly, not on
# the target. Every UKI's embedded .cmdline must carry igos.verity.roothash=
# equal to ROOT_HASH in the squashfs's .verity-params (the same equality the
# §5.2 pre-ceremony re-stage checks by hand; this encodes it). A MISMATCH is
# always fatal — the signed-stale-UKI class (June-2-UKIs-on-June-4-squashfs).
# ABSENCE is fatal on a release build; under UNSIGNED_TEST=1 it downgrades to
# a loud warning (the booter must then add the explicit
# igos.dev.allow_unverified=1 cmdline marker, cmdline being editable with
# Secure Boot off).
command -v objcopy >/dev/null 2>&1 || {
    echo "error: 'objcopy' not in PATH (binutils) — required to read UKI .cmdline sections" >&2
    exit 1
}
VERITY_PARAMS="${SQUASHFS}.verity-params"
if [ ! -f "$VERITY_PARAMS" ]; then
    echo "FAIL: verity params not found: $VERITY_PARAMS (phase_squashfs must run first)" >&2
    exit 1
fi
EXPECTED_ROOTHASH=$(grep '^ROOT_HASH=' "$VERITY_PARAMS" | head -1 | cut -d= -f2)
if [ -z "$EXPECTED_ROOTHASH" ]; then
    echo "FAIL: could not parse ROOT_HASH from $VERITY_PARAMS" >&2
    exit 1
fi
for var in UKI_LIVE UKI_INSTALL_GUI UKI_INSTALL_TUI; do
    path="${!var}"
    _cmdline_tmp=$(mktemp)
    # objcopy with a single file argument REWRITES that file in place (fresh
    # PE layout, Authenticode table dropped) — it destroyed the signed UKIs
    # on the first ge9b-01 mint. The explicit discard output file makes the
    # read side-effect-free; the input is never opened for writing.
    _pe_discard=$(mktemp)
    if ! objcopy --dump-section .cmdline="$_cmdline_tmp" "$path" "$_pe_discard" 2>/dev/null; then
        rm -f "$_cmdline_tmp" "$_pe_discard"
        echo "FAIL: cannot extract .cmdline section from $var ($path)" >&2
        exit 1
    fi
    rm -f "$_pe_discard"
    _sealed=$(tr -d '\0' < "$_cmdline_tmp" | awk -v RS=' ' '/^igos\.verity\.roothash=/ {sub(/^igos\.verity\.roothash=/, ""); print; exit}')
    rm -f "$_cmdline_tmp"
    if [ -z "$_sealed" ]; then
        if [ "$UNSIGNED_TEST" = "1" ]; then
            echo "[build-iso] WARNING: $var seals NO igos.verity.roothash= — dev/test ISO only;" >&2
            echo "[build-iso]          booting it requires the explicit igos.dev.allow_unverified=1 cmdline marker" >&2
            continue
        fi
        echo "FAIL: $var ($path) seals no igos.verity.roothash= in its .cmdline —" >&2
        echo "      a release UKI MUST seal the verity roothash (S3-F2); init.sh fail-closes without it." >&2
        exit 1
    fi
    if [ "$_sealed" != "$EXPECTED_ROOTHASH" ]; then
        echo "FAIL: $var seals roothash $_sealed" >&2
        echo "      but $VERITY_PARAMS says   $EXPECTED_ROOTHASH —" >&2
        echo "      stale UKI vs current squashfs (the June-2/June-4 class). Re-run phase_ukis_verity + re-sign." >&2
        exit 1
    fi
    echo "[build-iso] ✓ $var seals the current verity roothash"
done

# --------------------------------------------------------------------------
# Tool checks
# --------------------------------------------------------------------------

for tool in xorriso mkfs.vfat mcopy mmd; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "error: '$tool' not in PATH." >&2
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
    echo "error: could not determine xorriso version from \`xorriso --version\`." >&2
    exit 1
fi
# Compare via sort -V (version sort); abort if older than 1.5.6.
if [ "$(printf '1.5.6\n%s\n' "$XORRISO_VER" | sort -V | head -1)" != "1.5.6" ]; then
    echo "error: xorriso version $XORRISO_VER is older than the required 1.5.6" >&2
    echo "       (1.5.6 is the first version that honours SOURCE_DATE_EPOCH)" >&2
    exit 1
fi

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

mkdir -p "$LOG_DIR" || {
    echo "error: cannot create LOG_DIR: $LOG_DIR" >&2
    exit 1
}

# Pre-flight that LOG_DIR is actually writable. If LOG_DIR is on a network
# mount that drops mid-run, tee dies and stdout disappears; the build then
# proceeds blind. Catching it now (before the long-running xorriso step)
# fails fast with a clear error rather than silent dataloss.
if ! ( : > "${LOG_DIR}/.build-iso-write-probe" ) 2>/dev/null; then
    echo "error: LOG_DIR is not writable: $LOG_DIR" >&2
    exit 1
fi
rm -f "${LOG_DIR}/.build-iso-write-probe"

LOG_TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOG_FILE="${LOG_DIR}/build_${LOG_TIMESTAMP}.log"

# Tee subsequent stdout+stderr to log file via process substitution.
# (Subshell-on-exit copy would lose mid-run output if we crash.)
exec > >(tee -a "$LOG_FILE") 2>&1

# Source the forensic-trace bash companion (no-op when verbose unset).
# Init AFTER the tee redirect so trace_init's own output lands in the log.
# This script has its own existing EXIT traps so we emit iso_assemble_end
# explicitly at the script's natural success boundary.
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "host-build-iso"
    _ISO_START_MS=$(date +%s%3N)
    trace_event iso_assemble_start \
        output_path="$OUTPUT" volid="$VOLID" log_file="$LOG_FILE" \
        sde="$SOURCE_DATE_EPOCH" \
        inputs::="[\"$SHIM\",\"$GRUB\",\"$UKI_LIVE\",\"$UKI_INSTALL_GUI\",\"$UKI_INSTALL_TUI\",\"$SQUASHFS\"]"
fi

echo "[build-iso] starting at $(date -u --iso-8601=seconds)"
echo "[build-iso] >>> inputs"
echo "[build-iso]   SHIM:       $SHIM"
echo "[build-iso]     sha256:   $(sha256sum "$SHIM" | awk '{print $1}')"
echo "[build-iso]   GRUB:       $GRUB"
echo "[build-iso]     sha256:   $(sha256sum "$GRUB" | awk '{print $1}')"
echo "[build-iso]   UKI_LIVE:        $UKI_LIVE"
echo "[build-iso]     sha256:        $(sha256sum "$UKI_LIVE" | awk '{print $1}')"
echo "[build-iso]   UKI_INSTALL_GUI: $UKI_INSTALL_GUI"
echo "[build-iso]     sha256:        $(sha256sum "$UKI_INSTALL_GUI" | awk '{print $1}')"
echo "[build-iso]   UKI_INSTALL_TUI: $UKI_INSTALL_TUI"
echo "[build-iso]     sha256:        $(sha256sum "$UKI_INSTALL_TUI" | awk '{print $1}')"
echo "[build-iso]   SQUASHFS:   $SQUASHFS"
_SQ_SHA="$(sha256sum "$SQUASHFS" | awk '{print $1}')"
echo "[build-iso]     sha256:   $_SQ_SHA"
# install-integrity (FLAG B): assert the staging marker is bound to THIS
# squashfs (build-squashfs Step 6 stamped squashfs-sha256 into it). Guards a
# standalone build-iso run from trusting a stale prior-build marker over a
# rebuilt tree. Reuses the sha just computed (no extra hash). Skips the compare
# only if the marker carries no sha (older build-squashfs) — the marker
# presence Class-A gate above still applies.
_marker_sha="$(grep -m1 '^squashfs-sha256:' "$INTEGRITY_MARKER" 2>/dev/null | awk '{print $2}')"
if [ -n "$_marker_sha" ] && [ "$_marker_sha" != "$_SQ_SHA" ]; then
    echo "[build-iso] error: install-integrity staging marker squashfs-sha256 ($_marker_sha)" >&2
    echo "[build-iso]   != actual SQUASHFS sha ($_SQ_SHA). The marker belongs to a DIFFERENT" >&2
    echo "[build-iso]   squashfs (stale marker over a rebuilt tree). Refusing to assemble ISO." >&2
    exit 1
fi
echo "[build-iso]   GRUB_CFG:   $GRUB_CFG"
echo "[build-iso]   THEME_DIR:  ${THEME_DIR:-<none>}"
echo "[build-iso]   UNICODE_PF2: $UNICODE_PF2"
echo "[build-iso]   OUTPUT:     $OUTPUT"
echo "[build-iso]   VOLID:      $VOLID"
echo "[build-iso]   SDE:        $SOURCE_DATE_EPOCH"
echo "[build-iso]   LOG:        $LOG_FILE"

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
# when no NVRAM Boot#### entry exists (e.g. fresh install, USB boot,
# removable media). Per UEFI spec convention this must be the shim. Shim's
# built-in default chainload target is `grubx64.efi` adjacent to itself —
# so the standalone GRUB binary must also live in /EFI/BOOT/ for the
# fallback path to work without UEFI Shell intervention.
cp "$SHIM" "${ESP_TREE}/EFI/BOOT/BOOTX64.EFI"
cp "$GRUB" "${ESP_TREE}/EFI/BOOT/grubx64.efi"
# MokManager beside EVERY shim instance — shim resolves mmx64.efi relative
# to its own launch directory (PI-ge9b04-A: the ge9b-04 stick dead-ended /
# bootlooped SB=ON boots because neither ESP dir carried it).
cp "$MOKMANAGER" "${ESP_TREE}/EFI/BOOT/mmx64.efi"

# Canonical InterGenOS paths. The embedded grub.cfg inside grubx64.efi
# self-locates the ESP via $cmdpath (the device this binary was loaded
# from) and then configfiles /EFI/InterGenOS/grub.cfg from there — works
# whether grub is loaded from /EFI/BOOT/ (fallback) or /EFI/InterGenOS/
# (NVRAM Boot#### post-install), and — unlike the former search-by-label —
# does NOT race a previously-installed InterGenOS ESP (also labeled
# IGOS_ESP) when this USB boots an already-installed machine.
cp "$SHIM"            "${ESP_TREE}/EFI/InterGenOS/shimx64.efi"
cp "$MOKMANAGER"      "${ESP_TREE}/EFI/InterGenOS/mmx64.efi"
cp "$GRUB"            "${ESP_TREE}/EFI/InterGenOS/grubx64.efi"
cp "$UKI_LIVE"        "${ESP_TREE}/EFI/InterGenOS/igos-live.efi"
cp "$UKI_INSTALL_GUI" "${ESP_TREE}/EFI/InterGenOS/igos-install-gui.efi"
cp "$UKI_INSTALL_TUI" "${ESP_TREE}/EFI/InterGenOS/igos-install-tui.efi"
cp "$GRUB_CFG"        "${ESP_TREE}/EFI/InterGenOS/grub.cfg"
cp "$UNICODE_PF2"     "${ESP_TREE}/EFI/InterGenOS/fonts/unicode.pf2"

if [ -n "$THEME_DIR" ]; then
    cp -r "$THEME_DIR" "${ESP_TREE}/EFI/InterGenOS/themes/"
    echo "[build-iso]       theme dir staged: ${THEME_DIR##*/}"
fi

# InterGenOS CA cert in DER form for MokManager's "Enroll key from disk"
# (PI-ge9b04-A companion): gives a virgin box a manual enrollment path from
# the live medium itself. PEM input is converted; a DER input is staged
# as-is. Optional — absence is a loud note, never a silent skip.
if [ -n "$CA_CERT" ] && [ -f "$CA_CERT" ]; then
    if openssl x509 -in "$CA_CERT" -inform PEM -noout 2>/dev/null; then
        openssl x509 -in "$CA_CERT" -inform PEM -outform DER \
            -out "${ESP_TREE}/EFI/InterGenOS/intergenos-secure-boot-ca.cer"
    else
        cp "$CA_CERT" "${ESP_TREE}/EFI/InterGenOS/intergenos-secure-boot-ca.cer"
    fi
    echo "[build-iso]       CA cert staged (DER): EFI/InterGenOS/intergenos-secure-boot-ca.cer"
else
    echo "[build-iso]       NOTE: CA_CERT not provided/found — no Enroll-key-from-disk cert on this ESP" >&2
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
# FAT label: ESP-identification, distinct from ISO9660 VOLID (volume-id).
# The embedded grub.cfg now self-locates via $cmdpath (race-free), but the
# IGOS_ESP label is retained as grub's firmware-edge fallback (firmware that
# doesn't populate $cmdpath) and for userspace ESP discovery. Decoupling the
# FAT label from VOLID is semantically correct: FAT label identifies the ESP
# filesystem; VOLID identifies the ISO image.
mkfs.vfat -F 32 -i "$VOLSERIAL" -n "IGOS_ESP" "$ESP_IMG" >/dev/null

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
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/BOOT/BOOTX64.EFI"                ::EFI/BOOT/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/BOOT/grubx64.efi"                ::EFI/BOOT/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/BOOT/mmx64.efi"                  ::EFI/BOOT/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/shimx64.efi"          ::EFI/InterGenOS/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/mmx64.efi"            ::EFI/InterGenOS/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/grubx64.efi"          ::EFI/InterGenOS/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/igos-live.efi"        ::EFI/InterGenOS/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/igos-install-gui.efi" ::EFI/InterGenOS/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/igos-install-tui.efi" ::EFI/InterGenOS/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/grub.cfg"             ::EFI/InterGenOS/
mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/fonts/unicode.pf2"    ::EFI/InterGenOS/fonts/
if [ -f "${ESP_TREE}/EFI/InterGenOS/intergenos-secure-boot-ca.cer" ]; then
    mcopy -i "$ESP_IMG" "${ESP_TREE}/EFI/InterGenOS/intergenos-secure-boot-ca.cer" ::EFI/InterGenOS/
fi

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

# dm-verity hashtree (primary integrity verification path per lever 4
# 2026-05-28). build-squashfs.sh emits this alongside the squashfs;
# init.sh activates the verity device + mounts /dev/mapper/igos-root
# so the kernel verifies each 4 KiB block as it's read. Replaces the
# 73-second whole-file sha256sum boot wait. Required input — refuse
# to assemble an ISO without it.
VERITY_HASHTREE="${SQUASHFS}.verity"
if [ ! -f "$VERITY_HASHTREE" ]; then
    echo "FAIL: verity hashtree not found alongside squashfs: $VERITY_HASHTREE" >&2
    echo "       phase_squashfs must run veritysetup format before phase_iso." >&2
    echo "       Re-run scripts/build-squashfs.sh OR re-resume from phase_squashfs." >&2
    exit 1
fi
cp "$VERITY_HASHTREE" "${ISO_ROOT}/live/filesystem.verity"

# Whole-file sha256 — DIAGNOSTIC ONLY since S3-F1 (2026-07-08). init.sh no
# longer consumes this file for boot trust (the unauthenticated fallback it
# fed was removed — a reference on the same media the attacker controls
# verifies nothing). It ships so a USER can hand-verify their media offline
# (Prime Directive); dm-verity via the UKI-sealed roothash is the sole
# boot-trust path, gated at assembly above (S3-F2).
sha256sum "${ISO_ROOT}/live/filesystem.squashfs" \
    | awk '{print $1, "filesystem.squashfs"}' \
    > "${ISO_ROOT}/live/filesystem.sha256"

# Both verify files must land non-empty — surface ISO-build-time failure
# rather than as a kernel-panic-class fatal in the shipped initramfs.
[ -s "${ISO_ROOT}/live/filesystem.verity" ] || {
    echo "FAIL: filesystem.verity empty or missing after cp" >&2
    exit 1
}
[ -s "${ISO_ROOT}/live/filesystem.sha256" ] || {
    echo "FAIL: filesystem.sha256 empty or missing after sha256sum" >&2
    exit 1
}

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
_XORRISO_START_MS=$(date +%s%3N)
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_event xorriso_invoke output="$OUTPUT" volid="$VOLID" esp_img="$ESP_IMG"
fi
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
_XORRISO_RC=$?
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_event xorriso_done output="$OUTPUT" rc::=$_XORRISO_RC \
        duration_ms::=$(( $(date +%s%3N) - _XORRISO_START_MS ))
fi

if [ ! -f "$OUTPUT" ]; then
    echo "FAIL: xorriso did not produce $OUTPUT" >&2
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        build_failure_emit --where build-iso.sh:xorriso --why "xorriso did not produce $OUTPUT" --phase iso
    fi
    exit 1
fi

# --------------------------------------------------------------------------
# Step 5: self-verify
# --------------------------------------------------------------------------

echo "[build-iso] [5/6] self-verify"

# 5a. xorriso -indev report — confirms GPT, ESP partition, El Torito boot
# record. Captured to a separate report file so the harness can grep it.
INDEV_REPORT="${LOG_DIR}/indev_${LOG_TIMESTAMP}.txt"
# Capture exit code rather than `|| true`-swallowing — empty INDEV_REPORT
# is still caught by the grep checks below (defensive design), but a
# non-zero indev RC is itself diagnostic and worth surfacing.
INDEV_RC=0
xorriso -indev "$OUTPUT" -report_about ALL > "$INDEV_REPORT" 2>&1 || INDEV_RC=$?
if [ "$INDEV_RC" -ne 0 ]; then
    echo "[build-iso]       warning: xorriso -indev returned $INDEV_RC " \
         "(non-fatal; downstream grep checks will assert content)" >&2
fi

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
    # Remove the half-baked OUTPUT so a subsequent operator who doesn't read
    # stderr can't mistake the stale file for a good build. The trap that
    # cleans STAGING is separate; this only removes the failed OUTPUT.
    if [ -f "$OUTPUT" ]; then
        rm -f "$OUTPUT"
        echo "[build-iso]       removed partial $OUTPUT (verify-fail cleanup)" >&2
    fi
    exit 1
fi

# --------------------------------------------------------------------------
# Step 6a: SBOM-style manifest emission (Q22 reproducibility attestation)
# --------------------------------------------------------------------------
#
# Plaintext, sha256-sorted, alongside OUTPUT as <OUTPUT>.manifest. Format
# is diff-friendly (not cyclonedx / spdx-tag-value — those are heavier
# ecosystem-bound formats; our consumers are bash scripts + reviewers).
# Tool versions + SDE + script SHA + volid/volserial form a self-describing
# reproduction recipe so anyone wanting to re-verify a stale ISO has all
# the inputs to recompute.
MANIFEST="${OUTPUT}.manifest"
SCRIPT_SHA256=$(sha256sum "${BASH_SOURCE[0]}" | awk '{print $1}')
SHIM_SHA256=$(sha256sum "$SHIM" | awk '{print $1}')
GRUB_SHA256=$(sha256sum "$GRUB" | awk '{print $1}')
UKI_LIVE_SHA256=$(sha256sum "$UKI_LIVE" | awk '{print $1}')
UKI_INSTALL_GUI_SHA256=$(sha256sum "$UKI_INSTALL_GUI" | awk '{print $1}')
UKI_INSTALL_TUI_SHA256=$(sha256sum "$UKI_INSTALL_TUI" | awk '{print $1}')
SQUASHFS_SHA256=$(sha256sum "$SQUASHFS" | awk '{print $1}')
GRUB_CFG_SHA256=$(sha256sum "$GRUB_CFG" | awk '{print $1}')

XORRISO_VERSION_LINE=$(xorriso --version 2>&1 | head -1)
MKFS_VFAT_VERSION_LINE=$(mkfs.vfat --help 2>&1 | head -1)

# Hash-prefixed lines sorted by hash for stable ordering across same-SDE runs.
# basename(...) used rather than the full path because $SHIM/$GRUB/etc are
# operator-supplied via env var; two operators with different working-dir
# layouts would otherwise produce manifests that diverge on the path columns
# even with identical SHAs, breaking the byte-identity-across-operators
# claim. The label-prefix (`shim:`, `grub:`, etc.) is the actual
# disambiguator — basename on the right is illustrative.
{
    printf '%s  shim:     %s\n' "$SHIM_SHA256"     "$(basename "$SHIM")"
    printf '%s  grub:     %s\n' "$GRUB_SHA256"     "$(basename "$GRUB")"
    printf '%s  uki_live:        %s\n' "$UKI_LIVE_SHA256"        "$(basename "$UKI_LIVE")"
    printf '%s  uki_install_gui: %s\n' "$UKI_INSTALL_GUI_SHA256" "$(basename "$UKI_INSTALL_GUI")"
    printf '%s  uki_install_tui: %s\n' "$UKI_INSTALL_TUI_SHA256" "$(basename "$UKI_INSTALL_TUI")"
    printf '%s  squashfs: %s\n' "$SQUASHFS_SHA256" "$(basename "$SQUASHFS")"
    printf '%s  grub_cfg: %s\n' "$GRUB_CFG_SHA256" "$(basename "$GRUB_CFG")"
    printf '%s  output:   %s\n' "$ISO_SHA256"      "$(basename "$OUTPUT")"
} | sort > "$MANIFEST"

# Self-describing reproduction-recipe tail (NOT sorted with the above —
# these are key:value lines, not sha256-prefixed entries).
{
    echo "xorriso_version: $XORRISO_VERSION_LINE"
    echo "mkfs.vfat_version: $MKFS_VFAT_VERSION_LINE"
    echo "script_sha256: $SCRIPT_SHA256"
    echo "source_date_epoch: $SOURCE_DATE_EPOCH"
    echo "volid: $VOLID"
    echo "volserial: $VOLSERIAL"
} >> "$MANIFEST"

# --------------------------------------------------------------------------
# Step 6b: emit summary
# --------------------------------------------------------------------------

echo "[build-iso] [6/6] PASS"
echo "[build-iso] >>> output"
echo "[build-iso] output:    $OUTPUT"
echo "[build-iso]   size:    ${ISO_MB} MB ($ISO_BYTES bytes)"
echo "[build-iso]   sha256:  $ISO_SHA256"
echo "[build-iso]   volid:   $VOLID"
echo "[build-iso]   sde:     $SOURCE_DATE_EPOCH"
echo "[build-iso] manifest:  $MANIFEST"
echo "[build-iso] log:       $LOG_FILE"
echo "[build-iso] indev:     $INDEV_REPORT"
echo ""
echo "Next steps:"
echo "  Test boot in QEMU+OVMF+swtpm:"
echo "    qemu-system-x86_64 -bios /usr/share/OVMF/OVMF_CODE.fd \\"
echo "        -drive file=$OUTPUT,format=raw,if=virtio -m 4G"
echo "  Run the verify-b2-reproducibility harness against two builds:"
echo "    SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH scripts/build-iso.sh ... # second run"
echo "    scripts/verify-b2-reproducibility.sh <iso-1> <iso-2>"

if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_event iso_assemble_end output_path="$OUTPUT" \
        size_bytes::=$ISO_BYTES sha="$ISO_SHA256" \
        manifest="$MANIFEST" \
        rc::=0 duration_ms::=$(( $(date +%s%3N) - _ISO_START_MS ))
    trace_close
fi
