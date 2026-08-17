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
#   intergenos-archive-manifest.txt   (unsigned, copied from build VM)
#   PIN (prompted via read -s; OpenPGP User PIN, short)
#
# Outputs (in /tmp/c6r2-manifest/):
#   intergenos-archive-manifest.txt.sig   (ASCII-armored detached signature)
#
# Verifies the signature with `gpg --verify` before declaring success.
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
MANIFEST_BASENAME="intergenos-archive-manifest.txt"

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

[[ -d "$MANIFEST_DIR" ]] || die "Manifest dir not found at $MANIFEST_DIR — pre-stage the unsigned manifest before invoking this script"
MANIFEST="$MANIFEST_DIR/$MANIFEST_BASENAME"
[[ -f "$MANIFEST" ]] || die "Unsigned manifest not found at $MANIFEST"
ok "Unsigned manifest present: $MANIFEST ($(stat -c %s "$MANIFEST") bytes)"

# Sanity-check the manifest looks well-formed before signing it (matches
# scripts/sign-release.sh's checks). Refusing to sign garbage is the
# whole point of an inline sanity gate.
grep -q '^# Manifest-version: 1$' "$MANIFEST" \
    || die "Manifest missing 'Manifest-version: 1' header — refusing to sign"
grep -q '^# End of manifest\.$' "$MANIFEST" \
    || die "Manifest missing '# End of manifest.' terminator — refusing to sign"
grep -qE '^SHA256 \(' "$MANIFEST" \
    || die "Manifest contains no SHA256 entries — refusing to sign empty manifest"
ok "Manifest structurally valid (header + terminator + SHA256 entries present)"

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
  1. Sign the archive manifest at:
       $MANIFEST
     via Nitrokey #1's OpenPGP signing subkey [S1] using
     gpg --detach-sign --armor.
  2. Verify the resulting signature with gpg --verify against the
     same manifest.
  3. Stage signed output at:
       $MANIFEST.sig (ASCII-armored detached signature)

NOT touched: PIV applet (bootloader signing key), master keys, repo,
build VM filesystem (signed manifest stays in $MANIFEST_DIR; you'll
explicitly copy it back to the build host).

The signing operation will require the OpenPGP User PIN AND an on-card
touch (UIF policy) — watch the Nitrokey's LED.

Type 'sign manifest' to proceed:
EOF
read -r CONFIRM
[[ "$CONFIRM" == "sign manifest" ]] || die "Operator did not confirm. Aborting."

# ============================================================
# SIGN
# ============================================================
banner "Signing manifest"

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

info "Invoking gpg (you'll be prompted for the OpenPGP User PIN via pinentry, and the Nitrokey will request a touch)..."

if ! gpg "${SIGN_ARGS[@]}"; then
    die "gpg --detach-sign failed"
fi

[[ -s "$SIGNATURE" ]] || die "gpg produced empty signature file"
ok "Signature written: $SIGNATURE ($(stat -c %s "$SIGNATURE") bytes)"

# ============================================================
# VERIFY
# ============================================================
banner "Verifying signature"

if ! gpg --verify "$SIGNATURE" "$MANIFEST" 2>&1; then
    die "gpg --verify FAILED — signature does not validate against $MANIFEST"
fi
ok "gpg --verify PASSED"

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
banner "Manifest signing complete — signature staged + verified"

cat <<EOF

Signed manifest staged at:
  $SIGNATURE

SHAs:
  manifest:  $(sha256sum "$MANIFEST" | awk '{print $1}')
  signature: $(sha256sum "$SIGNATURE" | awk '{print $1}')

The full release trust triplet is now staged in $MANIFEST_DIR/:
  intergenos-archive-manifest.txt        (manifest)
  intergenos-archive-manifest.txt.sig    (detached signature)
  intergenos-release-key.asc             (release public key)

All THREE must land in /mnt/intergenos/build/ for build-squashfs Step 4.8.
The coordinator handles that copy and the --start-at squashfs resume.

Verification (any host with the release public key):
  gpg --verify intergenos-archive-manifest.txt.sig intergenos-archive-manifest.txt

EOF

ok "Script complete. Manifest signed + verified."
exit 0
