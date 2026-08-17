#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# sign-bootloader.sh — Sign InterGenOS bootloader EFI binaries via NK#1 PIV slot 9c
# using sbsign + libengine-pkcs11 + patched OpenSC 0.27.1 (RSA-4096 cert).
#
# Inputs (in /tmp/c6r2-bootloader/):
#   grubx64.efi             (unsigned, copied from build VM)
#   igos-live.efi           (live ISO UKI)
#   igos-install-gui.efi    (Forge GUI installer UKI; B-036 T0-2)
#   igos-install-tui.efi    (Forge TUI installer UKI; B-036 T0-2)
#   PIV User PIN (prompted via read -s; short, low fat-finger risk)
#
# Outputs (in /tmp/c6r2-bootloader/):
#   grubx64.efi.signed
#   igos-live.efi.signed
#   igos-install-gui.efi.signed
#   igos-install-tui.efi.signed
#
# Verifies each with sbverify before declaring success.
#
# B-036 (T0-2 2026-05-18): expanded from 2-binary loop to 4-binary loop
# so install-gui + install-tui UKIs are signed by this ceremony path.
# Pre-fix the hardcoded `grubx64.efi igos-live.efi` loop silently dropped
# the install UKIs, matching the B-002 glob gap in sign-release.sh.

set -euo pipefail

# ============================================================
# scdaemon refresh to ensure the Nitrokey is in a good state.
# ============================================================
gpgconf --kill scdaemon 2>&1

# Patched OpenSC for RSA-4096 PIV support. Two legitimate homes:
# /usr/local/lib = the hand-built module on a non-InterGenOS signing host;
# /usr/lib = the packaged core/opensc module (carries the same
# 0001-piv-force-rsa4096.patch) on an InterGenOS host. Prefer the hand-built
# one where present, fall back to the packaged one.
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"
PKCS11_MOD="/usr/local/lib/opensc-pkcs11.so"
[[ -f "$PKCS11_MOD" ]] || PKCS11_MOD="/usr/lib/opensc-pkcs11.so"
VENDOR_CERT="/mnt/intergenos/docker/shim-build/vendor-cert/intergenos-secure-boot-ca.pem"
BOOTLOADER_DIR="/tmp/c6r2-bootloader"
# Where the signed binaries are delivered for phase_iso. /mnt/intergenos is
# virtiofs-shared between the signing host and the build VM, so this is a local
# copy target (NOT an scp to a hardcoded VM IP, and NOT the chroot path which
# phase_image wipes). Override via env if your layout differs.
SIGNED_DELIVER_DIR="${SIGNED_DELIVER_DIR:-/mnt/intergenos/build/bootloader}"
# Repo root, derived from this script's own location, so the resume command in
# the summary prints a real runnable path without hardcoding /mnt/intergenos.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Expected: cert on slot 9c MUST match repo's vendor cert (which we just installed)
EXPECTED_CERT_PEM_SHA="cd34977e6efa37a572a9835c111a7d563809edbe838b1764be35100279d2c172"

# ============================================================
# HELPERS
# ============================================================
# Source the shared build-output library for the ✓/✗/⚠ markers + TTY-aware
# color. These local helpers below are kept (the ceremony scripts predate the
# library and several call sites depend on them) but re-voiced to the house
# style: error: severity, the ✓ verdict marker, and the one >>> section header.
# shellcheck source=lib/logging.sh
[ -f "$(dirname "$0")/lib/logging.sh" ] && source "$(dirname "$0")/lib/logging.sh"

die() { echo "error: $*" >&2; exit 1; }
info() { echo "$*"; }
ok() { echo "${IGOS_MARK_OK:-✓} $*"; }
banner() { echo; echo ">>> $*"; }

cleanup() {
    unset PIN 2>/dev/null || true
    # One-PIN flow (E3): destroy the tmpfs PIN file on every exit path.
    # shred is defense-in-depth on tmpfs (no disk to scrub); the rm is the
    # load-bearing step.
    if [[ -n "${PIN_FILE:-}" && -f "$PIN_FILE" ]]; then
        shred -u "$PIN_FILE" 2>/dev/null || rm -f "$PIN_FILE"
    fi
}
trap cleanup EXIT INT TERM

# ============================================================
# PRE-FLIGHT
# ============================================================
banner "Pre-flight checks"

for tool in sbsign sbverify openssl pkcs11-tool; do
    command -v "$tool" >/dev/null 2>&1 || die "Required tool not found: $tool"
done
ok "Tools present: sbsign sbverify openssl pkcs11-tool"

[[ -f "$PKCS11_MOD" ]] || die "Patched OpenSC PKCS#11 module not found at $PKCS11_MOD"
ok "Patched OpenSC module: $PKCS11_MOD"

[[ -f "$VENDOR_CERT" ]] || die "Vendor cert not found at $VENDOR_CERT"
REPO_CERT_SHA=$(sha256sum "$VENDOR_CERT" | awk '{print $1}')
[[ "$REPO_CERT_SHA" == "$EXPECTED_CERT_PEM_SHA" ]] \
    || die "Repo vendor cert SHA mismatch. Expected $EXPECTED_CERT_PEM_SHA, got $REPO_CERT_SHA"
ok "Vendor cert in repo matches expected RSA-4096 cert ($REPO_CERT_SHA)"

# Nitrokey presence: prefer lsusb where the host ships usbutils; fall back to
# the OpenSC reader enumeration on hosts that don't (InterGenOS ships opensc,
# not usbutils — the lsusb-only check killed the first on-box ceremony attempt,
# 2026-07-18). Both paths are positive detection, fail-closed.
if command -v lsusb >/dev/null 2>&1; then
    lsusb | grep -q "Clay Logic Nitrokey 3" || die "Nitrokey 3 not detected on USB bus"
    ok "Nitrokey 3 detected (lsusb)"
else
    pkcs11-tool --module "$PKCS11_MOD" --list-slots 2>/dev/null | grep -qi "nitrokey" \
        || die "Nitrokey 3 not detected via PKCS#11 reader enumeration (and no lsusb on this host)"
    ok "Nitrokey 3 detected (PKCS#11 reader enumeration)"
fi

# Confirm cert on slot 9c matches repo cert (so signing operations sign against the right key)
CARD_CERT_DER="/tmp/c6r2-sign-cardcert.der"
CARD_CERT_PEM="/tmp/c6r2-sign-cardcert.pem"
pkcs11-tool --module "$PKCS11_MOD" --read-object --type cert --id 02 -o "$CARD_CERT_DER" >/dev/null 2>&1 \
    || die "Could not read cert from slot 9c"
openssl x509 -inform DER -in "$CARD_CERT_DER" -outform PEM -out "$CARD_CERT_PEM"
CARD_PEM_SHA=$(sha256sum "$CARD_CERT_PEM" | awk '{print $1}')
# Card-pem and repo-pem may differ in byte layout (line endings) but the modulus must match
CARD_MODULUS=$(openssl x509 -in "$CARD_CERT_PEM" -noout -modulus | sed 's/Modulus=//')
REPO_MODULUS=$(openssl x509 -in "$VENDOR_CERT" -noout -modulus | sed 's/Modulus=//')
[[ "$CARD_MODULUS" == "$REPO_MODULUS" ]] \
    || die "Slot 9c cert modulus differs from repo cert modulus. Signing against wrong key?"
ok "Slot 9c cert modulus matches repo vendor cert"

# Verify sbsigntool's binaries (B-036 T0-2: 4-binary set)
BOOTLOADER_BINARIES=( grubx64.efi igos-live.efi igos-install-gui.efi igos-install-tui.efi )
for B in "${BOOTLOADER_BINARIES[@]}"; do
    [[ -f "$BOOTLOADER_DIR/$B" ]] || die "Missing $BOOTLOADER_DIR/$B"
done
ok "Bootloader artifacts present:"
ls -la "$BOOTLOADER_DIR"/*.efi

# Verify OpenSSL engine pkcs11 loads (will be used by sbsign)
if ! openssl engine pkcs11 -t 2>&1 | grep -q '\[ available \]'; then
    die "openssl engine pkcs11 not available — install libengine-pkcs11-openssl"
fi
ok "OpenSSL engine pkcs11 available"

# ============================================================
# PIN HANDLING — one capture, pin-source=file on tmpfs (E3; ceremony-2 finding)
# ============================================================
# Both prior shapes of this section were wrong in different ways:
#   * pin-value= in the URI (pre S2-F1): the PIN sat in every sbsign argv —
#     world-readable /proc/<pid>/cmdline. The B-049 leak class.
#   * engine-prompts-only (S2-F1): removed the leak, but the ceremony-2 live
#     run proved the engine prompts TWICE per binary (engine pass phrase +
#     PKCS#11 PIN) = 8 PIN entries per ceremony.
# The ratified shape (Rule-D banked at the ceremony-2 row): capture the PIN
# ONCE with read -s, write it to an owner-only 0600 file on tmpfs
# (/dev/shm — never disk), hand the engine a pin-source=file: URI. The PIN
# itself never enters argv, env, or any log — argv carries only the file
# PATH; the file is shredded on every exit path (cleanup() above). The
# capture happens AFTER the final confirmation below, so an aborted run
# never captures at all.
#   * pin-source alone was STILL 5 entries (ge9b-03 + ge9b-04 ceremonies,
#     deterministic): the PIV 9c key is ALWAYS_AUTHENTICATE, and libp11's
#     context-specific re-login prompts per signature regardless of the URI
#     PIN (E3-F1, grounded in libp11 0.4.13 p11_key.c). The pty feeder
#     (sign-pty-feeder.py, invoked below) answers those prompts from this
#     same capture — that is what makes "one entry" true in practice.
banner "PIV User PIN: ONE entry after confirmation, used for all 4 binaries"

# ============================================================
# CONFIRMATION
# ============================================================
banner "FINAL CONFIRMATION"
cat <<EOF

This script will:
  1. Sign 4 binaries via NK#1 PIV slot 9c using sbsign + libengine-pkcs11
     + patched OpenSC 0.27.1:
       - grubx64.efi
       - igos-live.efi
       - igos-install-gui.efi
       - igos-install-tui.efi
  2. Verify each signed binary with sbverify against the vendor cert
  3. Stage signed outputs at $BOOTLOADER_DIR/*.signed

NOT touched: OpenPGP applet, master keys, repo, build VM filesystem (signed
binaries stay in /tmp; you'll explicitly copy them back).

Each signing operation will require an on-card touch (UIF policy) IF that
policy is enabled. Watch the Nitrokey's LED.

Type 'sign bootloader' to proceed:
EOF
read -r CONFIRM
[[ "$CONFIRM" == "sign bootloader" ]] || die "Operator did not confirm. Aborting."

# ============================================================
# OPENSSL ENGINE CONFIG — load pkcs11 + patched module
# ============================================================
# Build an OPENSSL_CONF that tells the openssl engine where to find the
# patched OpenSC module (overrides system default).
SSL_CONF="/tmp/c6r2-openssl-pkcs11.cnf"
cat > "$SSL_CONF" <<CONF
openssl_conf = openssl_init

[openssl_init]
engines = engine_section

[engine_section]
pkcs11 = pkcs11_section

[pkcs11_section]
engine_id = pkcs11
dynamic_path = /usr/lib/x86_64-linux-gnu/engines-3/pkcs11.so
MODULE_PATH = $PKCS11_MOD
init = 0
CONF
export OPENSSL_CONF="$SSL_CONF"

# Test the engine actually loads with our patched module path
openssl engine pkcs11 -t 2>&1 | head -3
ok "OPENSSL_CONF set to $SSL_CONF; engine should load patched OpenSC"

# ============================================================
# SIGN EACH BINARY
# ============================================================
# One-PIN capture (E3) — after confirmation, before any signing. The PIN
# lives in a shell variable only long enough to reach the tmpfs file;
# printf is a builtin, so it never appears in any argv.
banner "PIV User PIN (NK#1 slot 9c) — one entry for the whole ceremony"
PIN_FILE=$(mktemp /dev/shm/sign-bootloader-pin.XXXXXX) || die "cannot create tmpfs PIN file under /dev/shm"
chmod 600 "$PIN_FILE"
read -rs -p "Enter PIV User PIN: " PIN
echo
[[ -n "${PIN}" ]] || die "empty PIN entered"
printf '%s' "$PIN" > "$PIN_FILE"   # verbatim content, no trailing newline
unset PIN
ok "PIN staged at $PIN_FILE (0600, tmpfs; shredded on exit)"

PKCS11_URI="pkcs11:id=%02;type=private?pin-source=file:${PIN_FILE}"

# B-049 guard, E3-amended: pin-value (the PIN itself in the URI/argv) stays
# refused unconditionally. pin-source is sanctioned ONLY in the exact form
# constructed above — a file: reference to OUR owner-only tmpfs file. Any
# other pin-source (env:, a foreign path, a loose-permission file) dies.
if [[ "$PKCS11_URI" == *"pin-value="* ]]; then
    die "PKCS11_URI must not embed the PIN itself (B-049)"
fi
if [[ "$PKCS11_URI" != "pkcs11:id=%02;type=private?pin-source=file:${PIN_FILE}" ]]; then
    die "PKCS11_URI drifted from the sanctioned one-PIN form (B-049/E3)"
fi
[[ "$PIN_FILE" == /dev/shm/* && -f "$PIN_FILE" ]] || die "PIN file is not a regular file on tmpfs: $PIN_FILE"
[[ "$(stat -c %a "$PIN_FILE")" == "600" ]] || die "PIN file permissions are not 0600: $PIN_FILE"

# E3-F1 (grounded 2026-07-18): the per-binary prompts are NOT the engine
# ignoring pin-source — the PIV 9c key is ALWAYS_AUTHENTICATE (per-signature
# PIN is the PIV standard for the signature slot), and libp11 0.4.13's
# context-specific re-login (p11_key.c pkcs11_authenticate) always prompts via
# the OpenSSL UI with no URI-PIN reuse. sign-pty-feeder.py answers exactly
# those prompts from the one-time capture: per-operation authentication stays
# intact, the operator types the PIN once. Feeder ABSENT -> the old manual
# per-binary prompts (degraded UX, never a leak). Feeder FAILURE -> hard stop.
PTY_FEEDER="$REPO_ROOT/scripts/sign-pty-feeder.py"
if [[ -f "$PTY_FEEDER" ]]; then
    banner "Signing: PIN prompts are answered automatically (one-PIN flow); watch for errors only"
else
    banner "Signing: feeder missing — enter the PIV User PIN at each prompt"
fi

for BINARY in "${BOOTLOADER_BINARIES[@]}"; do
    banner "Signing $BINARY"

    UNSIGNED="$BOOTLOADER_DIR/$BINARY"
    SIGNED="$BOOTLOADER_DIR/$BINARY.signed"

    info "Input: $UNSIGNED ($(stat -c %s "$UNSIGNED") bytes)"
    info "Output: $SIGNED"

    rm -f "$SIGNED"

    if [[ -f "$PTY_FEEDER" ]]; then
        # PIN never on argv/env: the feeder gets the FILE PATH; the PIN
        # travels pin-file -> pty only. Unrecognized prompts get no input
        # and die on the stall timeout (fail-closed).
        # Prompt forms by libp11 generation: 0.4.13 consumes the pin-source
        # URI for token login and re-prompts 'Enter PKCS#11 (key|token) PIN'
        # per ALWAYS_AUTHENTICATE signature; 0.4.18 (InterGenOS host, found
        # live 2026-07-18) does not consume pin-source and asks via OpenSSL's
        # generic 'Enter engine key pass phrase' UI instead — same PIN, so the
        # feeder answers both forms and the operator never types on the pty
        # (an unmatched prompt passed through echoes typed input in plain
        # text — the leak class this flow exists to prevent).
        if ! python3 "$PTY_FEEDER" \
            --pin-file "$PIN_FILE" \
            --expect-regex 'Enter PKCS#11 (key|token) PIN|Enter engine key pass phrase' \
            --max-answers 3 \
            --timeout 120 \
            -- sbsign \
                --engine pkcs11 \
                --key "$PKCS11_URI" \
                --cert "$VENDOR_CERT" \
                --output "$SIGNED" \
                "$UNSIGNED"; then
            die "sbsign (via pty feeder) failed for $BINARY"
        fi
    elif ! sbsign \
        --engine pkcs11 \
        --key "$PKCS11_URI" \
        --cert "$VENDOR_CERT" \
        --output "$SIGNED" \
        "$UNSIGNED" 2>&1; then
        die "sbsign failed for $BINARY"
    fi

    [[ -s "$SIGNED" ]] || die "sbsign produced empty file for $BINARY"
    ok "sbsign completed: $SIGNED ($(stat -c %s "$SIGNED") bytes)"

    info "Verifying with sbverify..."
    if ! sbverify --cert "$VENDOR_CERT" "$SIGNED" 2>&1; then
        die "sbverify FAILED for $SIGNED — signature does not validate against vendor cert"
    fi
    ok "sbverify PASSED for $BINARY"
done

# ============================================================
# SUMMARY
# ============================================================
banner "Sign complete — all 4 bootloader artifacts signed and verified"

cat <<EOF

Signed binaries staged at:
  $BOOTLOADER_DIR/grubx64.efi.signed
  $BOOTLOADER_DIR/igos-live.efi.signed
  $BOOTLOADER_DIR/igos-install-gui.efi.signed
  $BOOTLOADER_DIR/igos-install-tui.efi.signed

SHAs:
$(cd "$BOOTLOADER_DIR" && sha256sum *.signed)

To deliver:
  sudo cp $BOOTLOADER_DIR/*.signed ${SIGNED_DELIVER_DIR}/
Then resume:
  sudo bash $REPO_ROOT/scripts/build-intergenos.sh --user <user> --start-at iso

Verification on build VM (or any host with sbverify + the vendor cert):
  for B in grubx64 igos-live igos-install-gui igos-install-tui; do
    sbverify --cert intergenos-secure-boot-ca.pem \$B.efi.signed
  done

Audit trail: the post-sign sha256 sums printed above are the provenance record.
EOF

# Clean up the openssl conf (contained no secrets but no need to persist)
rm -f "$SSL_CONF"
unset OPENSSL_CONF

ok "Script complete. All 4 bootloader artifacts signed + verified."
exit 0
