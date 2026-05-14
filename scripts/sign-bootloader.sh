#!/usr/bin/env bash
# sign-bootloader.sh — Sign InterGenOS bootloader EFI binaries via NK#1 PIV slot 9c
# using sbsign + libengine-pkcs11 + patched OpenSC 0.27.1 (RSA-4096 cert).
#
# Inputs:
#   /tmp/c6r2-bootloader/grubx64.efi   (unsigned, copied from build VM)
#   /tmp/c6r2-bootloader/igos-live.efi (unsigned, copied from build VM)
#   PIV User PIN (prompted via read -s; short, low fat-finger risk)
#
# Outputs (in /tmp/c6r2-bootloader/):
#   grubx64.efi.signed
#   igos-live.efi.signed
#
# Verifies each with sbverify before declaring success.

set -euo pipefail

# Patched OpenSC for RSA-4096 PIV support
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"
PKCS11_MOD="/usr/local/lib/opensc-pkcs11.so"
VENDOR_CERT="/mnt/intergenos/docker/shim-build/vendor-cert/intergenos-secure-boot-ca.pem"
BOOTLOADER_DIR="/tmp/c6r2-bootloader"

# Expected: cert on slot 9c MUST match repo's vendor cert (which we just installed)
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

[[ -f "$PKCS11_MOD" ]] || die "Patched OpenSC PKCS#11 module not found at $PKCS11_MOD"
ok "Patched OpenSC module: $PKCS11_MOD"

[[ -f "$VENDOR_CERT" ]] || die "Vendor cert not found at $VENDOR_CERT"
REPO_CERT_SHA=$(sha256sum "$VENDOR_CERT" | awk '{print $1}')
[[ "$REPO_CERT_SHA" == "$EXPECTED_CERT_PEM_SHA" ]] \
    || die "Repo vendor cert SHA mismatch. Expected $EXPECTED_CERT_PEM_SHA, got $REPO_CERT_SHA"
ok "Vendor cert in repo matches expected RSA-4096 cert ($REPO_CERT_SHA)"

lsusb | grep -q "Clay Logic Nitrokey 3" || die "Nitrokey 3 not detected on USB bus"
ok "Nitrokey 3 detected"

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

# Verify sbsigntool's binaries
[[ -f "$BOOTLOADER_DIR/grubx64.efi" ]] || die "Missing $BOOTLOADER_DIR/grubx64.efi"
[[ -f "$BOOTLOADER_DIR/igos-live.efi" ]] || die "Missing $BOOTLOADER_DIR/igos-live.efi"
ok "Bootloader artifacts present:"
ls -la "$BOOTLOADER_DIR"/*.efi

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
  1. Sign grubx64.efi and igos-live.efi via NK#1 PIV slot 9c
     using sbsign + libengine-pkcs11 + patched OpenSC 0.27.1
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
PKCS11_URI="pkcs11:id=%02;type=private;pin-value=$PIN"

for BINARY in grubx64.efi igos-live.efi; do
    banner "Signing $BINARY"

    UNSIGNED="$BOOTLOADER_DIR/$BINARY"
    SIGNED="$BOOTLOADER_DIR/$BINARY.signed"

    info "Input: $UNSIGNED ($(stat -c %s "$UNSIGNED") bytes)"
    info "Output: $SIGNED"

    rm -f "$SIGNED"

    if ! sbsign \
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
banner "Sign complete — both bootloader artifacts signed and verified"

cat <<EOF

Signed binaries staged at:
  $BOOTLOADER_DIR/grubx64.efi.signed
  $BOOTLOADER_DIR/igos-live.efi.signed

SHAs:
$(cd "$BOOTLOADER_DIR" && sha256sum *.signed)

To deliver to build VM (manual step — script will not auto-copy):
  scp $BOOTLOADER_DIR/*.signed christopher@192.168.122.249:/tmp/bootloader-signed/
  # Then sudo cp /tmp/bootloader-signed/*.signed /mnt/igos/mnt/intergenos/build/bootloader/
  # (or wherever the orchestrator expects them)

Verification on build VM (or any host with sbverify + the vendor cert):
  sbverify --cert intergenos-secure-boot-ca.pem grubx64.efi.signed
  sbverify --cert intergenos-secure-boot-ca.pem igos-live.efi.signed

Bundle update (optional, for next ceremony's audit trail):
  cp $BOOTLOADER_DIR/*.signed \\
     ~/intergenos/research/ceremony/v1-bootloader-sign-bundle/artifacts/
EOF

# Clean up the openssl conf (contained no secrets but no need to persist)
rm -f "$SSL_CONF"
unset OPENSSL_CONF

ok "Script complete. Both bootloader artifacts signed + verified."
exit 0
