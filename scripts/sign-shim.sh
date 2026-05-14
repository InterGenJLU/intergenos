#!/usr/bin/env bash
# sign-shim.sh — sign an unsigned shim EFI binary via NK#1 PIV slot 9c
# using sbsign + libengine-pkcs11 + patched OpenSC 0.27.1 (RSA-4096).
#
# Hardened 2026-05-13 to mirror scripts/sign-bootloader.sh's proven patterns:
#   - Uses PATCHED OpenSC at /usr/local (RSA-4096 PIV support — see
#     [[feedback_opensc_piv_rsa4096_force_enable]]). System OpenSC at /usr
#     does NOT support RSA-4096 PIV signing and will fail with
#     CKR_FUNCTION_NOT_SUPPORTED.
#   - Verifies the repo vendor cert SHA matches the deployed RSA-4096 cert
#   - Verifies the cert on slot 9c has the same modulus as the repo vendor
#     cert (catches the C6-class desync where cert and keypair diverge)
#   - Dynamically generates OPENSSL_CONF so libengine-pkcs11 loads the
#     patched module (system pkcs11 engine would otherwise pick up
#     unpatched system OpenSC)
#   - sbverify gate after sbsign before declaring success
#
# Two output paths:
#   - Self-signed (for our own dev / MOK-enrolled testing during shim-review wait)
#   - The Microsoft-signed binary that ships in production comes from the
#     shim-review process post-PR-merge, not from this script
#
# Prerequisites:
#   - Nitrokey #1 plugged in
#   - sbsigntool + libengine-pkcs11-openssl packages installed
#   - Patched OpenSC at /usr/local (built from OpenSC 0.27.1 with CI_RSA_4096
#     force-enabled — see [[feedback_opensc_piv_rsa4096_force_enable]])
#   - pcscd running (or pcsclite-managed)
#
# Usage:
#   scripts/sign-shim.sh <unsigned-shim.efi> <signed-shim.efi>
#
# Optional override env vars:
#   VENDOR_CERT  - path to vendor cert PEM (default: docker/shim-build/vendor-cert/intergenos-secure-boot-ca.pem)

set -euo pipefail

UNSIGNED="${1:?usage: sign-shim.sh <unsigned-shim.efi> <signed-shim.efi>}"
SIGNED="${2:?usage: sign-shim.sh <unsigned-shim.efi> <signed-shim.efi>}"

# Patched OpenSC for RSA-4096 PIV support
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"
PKCS11_MOD="/usr/local/lib/opensc-pkcs11.so"
VENDOR_CERT="${VENDOR_CERT:-/mnt/intergenos/docker/shim-build/vendor-cert/intergenos-secure-boot-ca.pem}"

# Expected: cert on slot 9c MUST match repo's vendor cert (RSA-4096, deployed 2026-05-13)
EXPECTED_CERT_PEM_SHA="cd34977e6efa37a572a9835c111a7d563809edbe838b1764be35100279d2c172"

# ============================================================
# HELPERS
# ============================================================
die() { echo "FATAL: $*" >&2; exit 1; }
info() { echo "[*] $*"; }
ok() { echo "[OK] $*"; }
banner() { echo; echo "============================================================"; echo "  $*"; echo "============================================================"; }

cleanup() { unset PIN 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# ============================================================
# PRE-FLIGHT
# ============================================================
banner "Pre-flight checks"

for tool in sbsign sbverify openssl pkcs11-tool; do
    command -v "$tool" >/dev/null 2>&1 || die "Required tool not found: $tool"
done
ok "Tools present: sbsign sbverify openssl pkcs11-tool"

[[ -f "$PKCS11_MOD" ]] || die "Patched OpenSC PKCS#11 module not found at $PKCS11_MOD (need RSA-4096 patch — see feedback_opensc_piv_rsa4096_force_enable)"
ok "Patched OpenSC module: $PKCS11_MOD"

[[ -f "$VENDOR_CERT" ]] || die "Vendor cert not found at $VENDOR_CERT"
REPO_CERT_SHA=$(sha256sum "$VENDOR_CERT" | awk '{print $1}')
[[ "$REPO_CERT_SHA" == "$EXPECTED_CERT_PEM_SHA" ]] \
    || die "Repo vendor cert SHA mismatch. Expected $EXPECTED_CERT_PEM_SHA, got $REPO_CERT_SHA"
ok "Vendor cert in repo matches expected RSA-4096 cert ($REPO_CERT_SHA)"

[[ -f "$UNSIGNED" ]] || die "Unsigned shim binary not found: $UNSIGNED"
ok "Unsigned shim binary present: $UNSIGNED ($(stat -c %s "$UNSIGNED") bytes)"

lsusb | grep -q "Clay Logic Nitrokey 3" || die "Nitrokey 3 not detected on USB bus"
ok "Nitrokey 3 detected"

# Confirm cert on slot 9c matches repo cert (so signing operations sign against the right key)
CARD_CERT_DER="/tmp/sign-shim-cardcert.der"
CARD_CERT_PEM="/tmp/sign-shim-cardcert.pem"
pkcs11-tool --module "$PKCS11_MOD" --read-object --type cert --id 02 -o "$CARD_CERT_DER" >/dev/null 2>&1 \
    || die "Could not read cert from slot 9c"
openssl x509 -inform DER -in "$CARD_CERT_DER" -outform PEM -out "$CARD_CERT_PEM"
CARD_MODULUS=$(openssl x509 -in "$CARD_CERT_PEM" -noout -modulus | sed 's/Modulus=//')
REPO_MODULUS=$(openssl x509 -in "$VENDOR_CERT" -noout -modulus | sed 's/Modulus=//')
[[ "$CARD_MODULUS" == "$REPO_MODULUS" ]] \
    || die "Slot 9c cert modulus differs from repo cert modulus. Signing against wrong key?"
ok "Slot 9c cert modulus matches repo vendor cert"

# Verify OpenSSL engine pkcs11 loads (will be used by sbsign)
if ! openssl engine pkcs11 -t 2>&1 | grep -q '\[ available \]'; then
    die "openssl engine pkcs11 not available — install libengine-pkcs11-openssl"
fi
ok "OpenSSL engine pkcs11 available"

# ============================================================
# PROMPT PIN
# ============================================================
banner "PIV User PIN"
read -s -p "PIV User PIN: " PIN; echo
[[ ${#PIN} -ge 6 && ${#PIN} -le 8 ]] || die "PIN length looks wrong (got ${#PIN}, expected 6-8)"
ok "PIN captured (length=${#PIN})"

# ============================================================
# CONFIRMATION
# ============================================================
banner "FINAL CONFIRMATION"
cat <<EOF

This script will:
  1. Sign $UNSIGNED via NK#1 PIV slot 9c
     using sbsign + libengine-pkcs11 + patched OpenSC 0.27.1 (RSA-4096)
  2. Verify the signed binary with sbverify against the vendor cert
  3. Write signed output to $SIGNED

NOT touched: OpenPGP applet, master keys, repo vendor cert, build VM filesystem.

The signing operation will require an on-card touch (UIF policy) IF that
policy is enabled. Watch the Nitrokey's LED.

Type 'sign shim' to proceed:
EOF
read -r CONFIRM
[[ "$CONFIRM" == "sign shim" ]] || die "Operator did not confirm. Aborting."

# ============================================================
# OPENSSL ENGINE CONFIG — load pkcs11 engine + patched module
# ============================================================
# Build an OPENSSL_CONF that tells libengine-pkcs11 where to find the
# patched OpenSC module (overrides system default at /usr/lib/.../opensc-pkcs11.so).
SSL_CONF="/tmp/sign-shim-openssl-pkcs11.cnf"
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
# SIGN
# ============================================================
banner "Signing shim"
PKCS11_URI="pkcs11:id=%02;type=private;pin-value=$PIN"

info "Input:  $UNSIGNED ($(stat -c %s "$UNSIGNED") bytes)"
info "Output: $SIGNED"

rm -f "$SIGNED"

if ! sbsign \
    --engine pkcs11 \
    --key "$PKCS11_URI" \
    --cert "$VENDOR_CERT" \
    --output "$SIGNED" \
    "$UNSIGNED" 2>&1; then
    die "sbsign failed"
fi

[[ -s "$SIGNED" ]] || die "sbsign produced empty file"
ok "sbsign completed: $SIGNED ($(stat -c %s "$SIGNED") bytes)"

info "Verifying with sbverify..."
if ! sbverify --cert "$VENDOR_CERT" "$SIGNED" 2>&1; then
    die "sbverify FAILED — signature does not validate against vendor cert"
fi
ok "sbverify PASSED"

# ============================================================
# SUMMARY
# ============================================================
banner "Sign complete — shim signed and verified"

cat <<EOF

Signed shim staged at: $SIGNED

SHAs:
  Unsigned: $(sha256sum "$UNSIGNED" | awk '{print $1}')
  Signed:   $(sha256sum "$SIGNED" | awk '{print $1}')

Verification on any host with sbverify + the vendor cert:
  sbverify --cert intergenos-secure-boot-ca.pem $SIGNED
EOF

# Clean up the openssl conf + scratch card-cert files (no secrets, but no need to persist)
rm -f "$SSL_CONF" "$CARD_CERT_DER" "$CARD_CERT_PEM"
unset OPENSSL_CONF

ok "Script complete. Shim signed + verified."
exit 0
