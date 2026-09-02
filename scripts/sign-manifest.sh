#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# sign-manifest.sh — Sign InterGenOS archive manifest via NK#1 OpenPGP
# signing subkey [S1] (gpg --detach-sign --armor).
#
# Rule F (build-rules §1) — signing-ceremony commands MUST be pain-free
# for the operator. This wrapper is the OPERATOR-DRIVEN path for manifest
# signing. The underlying scripts/sign-release.sh is the CI / coordinator-
# driven path (flag surfaces, env vars, automation) and SHOULD NOT be
# recommended directly to the operator during a live ceremony.
#
# Inputs (in /tmp/c6r2-manifest/):
#   intergenos-archive-manifest.txt       (unsigned FULL manifest — the mirror's
#                                          census of every archive the build
#                                          chroot holds; copied from build VM)
#   intergenos-archive-manifest-iso.txt   (unsigned ISO manifest — the full
#                                          census minus the mirror-only archives
#                                          the ISO does not carry; emitted by
#                                          phase_manifest beside the full one)
#   PIN (prompted via read -s; OpenPGP User PIN, short)
#
# Outputs (in /tmp/c6r2-manifest/):
#   intergenos-archive-manifest.txt.sig       (ASCII-armored detached signature)
#   intergenos-archive-manifest-iso.txt.sig   (ASCII-armored detached signature)
#
# ONE ceremony signs BOTH manifests (two gpg operations: the card asks for a
# touch AND PIN entry per signature — the card policy asks each time). The ISO
# manifest is what build-squashfs Step 4.8 seals into the squashfs at
# /install/intergenos-archive-manifest.txt; the full one goes to the mirror
# with publish-repo.sh. A release squashfs refuses to build without the
# signed ISO manifest (the R001.2 install abort, 2026-08-27: the ISO carried
# the full manifest and promised 284 archives the media did not hold).
#
# Verifies every signature with `gpg --verify` before declaring success.
#
# Companion to scripts/sign-bootloader.sh (bootloader EFI binaries via
# PIV slot 9c). This is the FIRST of the build pipeline's two signing
# ceremonies — the archive manifest emitted by phase_manifest, signed
# before the squashfs is sealed (sign-bootloader.sh is the second).

set -euo pipefail

# ============================================================
# scdaemon refresh to ensure the Nitrokey is in a good state.
# ============================================================
gpgconf --kill scdaemon 2>&1

# ============================================================
# CONFIG
# ============================================================
MANIFEST_DIR="/tmp/c6r2-manifest"
# The full manifest is REQUIRED (it has always been the ceremony's subject).
# The ISO manifest is required too unless SIGN_MANIFEST_FULL_ONLY=1 names the
# mirror-only re-sign case explicitly — a release squashfs cannot be built
# from a ceremony that skipped it.
FULL_MANIFEST_BASENAME="intergenos-archive-manifest.txt"
ISO_MANIFEST_BASENAME="intergenos-archive-manifest-iso.txt"

# Expected signing subkey fingerprint (InterGenJLU OpenPGP [S1] subkey
# on Nitrokey #1). Set via env if a different signing key is in use.
EXPECTED_SIGNING_KEY="${INTERGENOS_GPG_KEY_ID:-}"

# ============================================================
# HELPERS
# ============================================================
# Shared build-output library — for the ✓/✗/⚠ markers + TTY-aware color. The
# local ceremony helpers below are kept but re-voiced to the house style.
# shellcheck source=lib/logging.sh
[ -f "$(dirname "$0")/lib/logging.sh" ] && source "$(dirname "$0")/lib/logging.sh"

die() { echo "error: $*" >&2; exit 1; }
info() { echo "$*"; }
ok() { echo "${IGOS_MARK_OK:-✓} $*"; }
banner() { echo; echo ">>> $*"; }

cleanup() { unset PIN 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# ============================================================
# PRE-FLIGHT
# ============================================================
banner "Pre-flight checks"

for tool in gpg sha256sum; do
    command -v "$tool" >/dev/null 2>&1 || die "Required tool not found: $tool"
done
ok "Tools present: gpg sha256sum"

[[ -d "$MANIFEST_DIR" ]] || die "Manifest dir not found at $MANIFEST_DIR — pre-stage the unsigned manifests before invoking this script"
MANIFESTS=()
FULL_MANIFEST="$MANIFEST_DIR/$FULL_MANIFEST_BASENAME"
ISO_MANIFEST="$MANIFEST_DIR/$ISO_MANIFEST_BASENAME"
[[ -f "$FULL_MANIFEST" ]] || die "Unsigned full manifest not found at $FULL_MANIFEST"
MANIFESTS+=("$FULL_MANIFEST")
if [[ -f "$ISO_MANIFEST" ]]; then
    MANIFESTS+=("$ISO_MANIFEST")
elif [[ "${SIGN_MANIFEST_FULL_ONLY:-0}" == "1" ]]; then
    echo "note: SIGN_MANIFEST_FULL_ONLY=1 — signing the full manifest only; no release squashfs can be built from this ceremony" >&2
else
    die "Unsigned ISO manifest not found at $ISO_MANIFEST — phase_manifest emits it beside the full one; stage both (or set SIGN_MANIFEST_FULL_ONLY=1 for a mirror-only re-sign)"
fi

# Sanity-check each manifest looks well-formed before signing it (matches
# scripts/sign-release.sh's checks). Refusing to sign garbage is the
# whole point of an inline sanity gate.
for MANIFEST in "${MANIFESTS[@]}"; do
    ok "Unsigned manifest present: $MANIFEST ($(stat -c %s "$MANIFEST") bytes, $(grep -c '^SHA256 (' "$MANIFEST") entries)"
    grep -q '^# Manifest-version: 1$' "$MANIFEST" \
        || die "$MANIFEST missing 'Manifest-version: 1' header — refusing to sign"
    grep -q '^# End of manifest\.$' "$MANIFEST" \
        || die "$MANIFEST missing '# End of manifest.' terminator — refusing to sign"
    grep -qE '^SHA256 \(' "$MANIFEST" \
        || die "$MANIFEST contains no SHA256 entries — refusing to sign empty manifest"
done
# The ISO manifest must say so: a full manifest staged under the ISO name
# would put the R001.2 abort back on the media.
if [[ -f "$ISO_MANIFEST" ]]; then
    grep -q '^# Manifest-scope: iso$' "$ISO_MANIFEST" \
        || die "$ISO_MANIFEST lacks the '# Manifest-scope: iso' header — not an ISO manifest; refusing to sign it under that name"
fi
ok "Manifest(s) structurally valid (header + terminator + SHA256 entries present)"

# Lock pre-flight BEFORE the card check, because a lock GnuPG will not break
# makes every gpg call wait without a bound — including the card check below,
# which then looks exactly like an absent token and sends the operator to the
# USB port instead of to the lock file. Measured on this project's signing
# workstation: a lock left by the machine's previous install, naming a host
# name this machine no longer has, stalled a ceremony that way.
SM_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SM_SCRIPT_DIR/lib-gnupg-lock-preflight.sh" ]]; then
    # shellcheck disable=SC1090
    source "$SM_SCRIPT_DIR/lib-gnupg-lock-preflight.sh"
    gnupg_lock_preflight "${GNUPGHOME:-$HOME/.gnupg}" 0 \
        || die "GnuPG lock pre-flight refused — see the lock(s) named above. Nothing was signed and nothing was deleted."
    ok "GnuPG lock pre-flight clear"
else
    echo "note: lib-gnupg-lock-preflight.sh not found beside this script; lock states are unchecked" >&2
fi

# GPG side: --card-status lists the OpenPGP card if connected + readable.
if ! gpg --card-status >/dev/null 2>&1; then
    die "Nitrokey 3 OpenPGP applet not detected. Check USB. (gpg --card-status to debug.)"
fi
ok "Nitrokey 3 OpenPGP applet detected"

# Confirm a signing subkey is available
if [[ -n "$EXPECTED_SIGNING_KEY" ]]; then
    if ! gpg --list-secret-keys "$EXPECTED_SIGNING_KEY" 2>&1 | grep -q '^sec'; then
        die "Configured signing key $EXPECTED_SIGNING_KEY not found in keyring. Override via INTERGENOS_GPG_KEY_ID env or unset for default."
    fi
    ok "Signing key in keyring: $EXPECTED_SIGNING_KEY"
else
    SIGNING_KEY=$(gpg --list-secret-keys --keyid-format LONG 2>/dev/null | awk '/^sec/ {split($2, a, "/"); print a[2]; exit}')
    [[ -n "$SIGNING_KEY" ]] || die "No GPG secret keys found in keyring. Connect Nitrokey + import public key."
    ok "Default signing key: $SIGNING_KEY"
fi

# ============================================================
# CONFIRMATION
# ============================================================
banner "FINAL CONFIRMATION"
cat <<EOF

This script will:
  1. Sign ${#MANIFESTS[@]} archive manifest(s):
$(for m in "${MANIFESTS[@]}"; do printf '       %s\n' "$m"; done)
     via Nitrokey #1's OpenPGP signing subkey [S1] using
     gpg --detach-sign --armor (one signature per file).
  2. Verify each resulting signature with gpg --verify against its
     manifest.
  3. Stage signed output beside each manifest as <manifest>.sig
     (ASCII-armored detached signature).

NOT touched: PIV applet (bootloader signing key), master keys, repo,
build VM filesystem (signed manifests stay in $MANIFEST_DIR; you'll
explicitly copy them back to the build host).

Each signature requires an on-card touch (UIF policy) — watch the
Nitrokey's LED — the card asks for the OpenPGP User PIN and a touch
for EACH file: two PIN entries, two touches (measured 2026-09-02).

Type 'sign manifest' to proceed:
EOF
read -r CONFIRM
[[ "$CONFIRM" == "sign manifest" ]] || die "Operator did not confirm. Aborting."

# ============================================================
# SIGN
# ============================================================
for MANIFEST in "${MANIFESTS[@]}"; do
    banner "Signing $(basename "$MANIFEST")"

    SIGNATURE="$MANIFEST.sig"
    rm -f "$SIGNATURE"

    SIGN_ARGS=(
        --batch --yes
        --detach-sign --armor
        --output "$SIGNATURE"
    )
    if [[ -n "$EXPECTED_SIGNING_KEY" ]]; then
        SIGN_ARGS+=(--local-user "$EXPECTED_SIGNING_KEY")
    fi
    SIGN_ARGS+=("$MANIFEST")

    info "Invoking gpg (OpenPGP User PIN via pinentry if not cached; the Nitrokey will request a touch)..."

    if ! gpg "${SIGN_ARGS[@]}"; then
        die "gpg --detach-sign failed for $MANIFEST"
    fi

    [[ -s "$SIGNATURE" ]] || die "gpg produced empty signature file for $MANIFEST"
    ok "Signature written: $SIGNATURE ($(stat -c %s "$SIGNATURE") bytes)"

    # ========================================================
    # VERIFY
    # ========================================================
    banner "Verifying $(basename "$SIGNATURE")"

    if ! gpg --verify "$SIGNATURE" "$MANIFEST" 2>&1; then
        die "gpg --verify FAILED — signature does not validate against $MANIFEST"
    fi
    ok "gpg --verify PASSED"
done

# ============================================================
# RELEASE PUBLIC KEY — the third trust artifact for Step 4.8
# ============================================================
# build-squashfs Step 4.8 (Option-1 install-integrity, added 2026) seals a
# TRIPLET into the squashfs: the manifest, its .sig, AND the release public
# key — so the install-time verifier can self-validate without network
# (install-integrity design §5.2). Export it here so this one operator command
# produces the COMPLETE release set, not just the .sig. Same export
# scripts/sign-release.sh performs: gpg --armor --export of the signing key's
# enclosing primary. (Public-key op — no card/PIN.)
banner "Exporting release public key"
RELEASE_KEY="$MANIFEST_DIR/intergenos-release-key.asc"
EXPORT_KEY="${EXPECTED_SIGNING_KEY:-${SIGNING_KEY:-}}"
[[ -n "$EXPORT_KEY" ]] || die "no signing key resolved for the public-key export"
rm -f "$RELEASE_KEY"
if ! gpg --batch --yes --armor --export "$EXPORT_KEY" > "$RELEASE_KEY"; then
    die "gpg --armor --export of the release public key failed"
fi
[[ -s "$RELEASE_KEY" ]] || die "release public key export is empty"
ok "Release public key exported: $RELEASE_KEY ($(stat -c %s "$RELEASE_KEY") bytes)"

# ============================================================
# SUMMARY
# ============================================================
banner "Manifest signing complete — signature(s) staged + verified"

cat <<EOF

Signed and verified in $MANIFEST_DIR/:
$(for m in "${MANIFESTS[@]}"; do
    printf '  %-42s sha256 %s\n' "$(basename "$m")" "$(sha256sum "$m" | awk '{print $1}')"
    printf '  %-42s sha256 %s\n' "$(basename "$m").sig" "$(sha256sum "$m.sig" | awk '{print $1}')"
  done)
  $(printf '%-42s' "$(basename "$RELEASE_KEY")") (release public key)

Every file above lands in /mnt/intergenos/build/ for build-squashfs Step 4.8,
which seals the ISO manifest (+ .sig + key) into the squashfs and the mirror
publish ships the full manifest. The coordinator handles that copy and the
--start-at squashfs resume.

Verification (any host with the release public key):
  gpg --verify <manifest>.sig <manifest>

EOF

ok "Script complete. Manifest(s) signed + verified."
exit 0
