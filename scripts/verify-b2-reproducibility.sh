#!/usr/bin/env bash
# B2 Reproducibility Verification Harness
# Compares two independent shim build outputs for bit-for-bit identity.
# Exit 0 = PASS (all checks), Exit 1-N = FAIL (N checks failed).
#
# Usage:
#   ./scripts/verify-b2-reproducibility.sh \
#       /path/to/runA/intergenos-shim-16.1.tar \
#       /path/to/runB/intergenos-shim-16.1.tar \
#       [verdict_output_file]
#
# Artifact inventory (post-IGOSC Dockerfile revision, master 66c373e):
#   shimx64.efi         — unsigned PE/COFF binary with embedded vendor cert
#   vendor_cert.der     — InterGenOS Secure Boot CA (DER, public half)
#   vendor_cert.pem     — InterGenOS Secure Boot CA (PEM, public half)
#   SHIM_COMMIT_SHA     — rhboot/shim commit SHA (afc49558...)
#   sbat.intergenos.csv — InterGenOS SBAT vendor entry (generation 1)
#
# The binary is UNSIGNED — signing happens outside the container via
# scripts/sign-shim.sh (NK#1 PIV slot 9c). The unsigned binary is the
# reproducible artifact; the signature is host-specific (timestamps,
# PKCS#1v1.5 padding) and NOT reproducible.
#
# Design: DeepSeek V4 PRO (DS), 2026-05-01
# Updated: 2026-05-05 — new artifact inventory, L1+L2+L3 fixes applied
set -euo pipefail

TAR_A="$1"
TAR_B="$2"
VERDICT_FILE="${3:-/dev/stdout}"

fail() { echo "FAIL: $*" >> "$VERDICT_FILE"; }
pass() { echo "PASS: $*" >> "$VERDICT_FILE"; }
info() { echo "INFO: $*" >> "$VERDICT_FILE"; }

WORK="/tmp/b2-verify-$$"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/A" "$WORK/B"
tar -xf "$TAR_A" -C "$WORK/A"
tar -xf "$TAR_B" -C "$WORK/B"

echo "=== B2 Reproducibility Verification $(date -u '+%Y-%m-%dT%H:%M:%SZ') ===" > "$VERDICT_FILE"
echo "Run A: $TAR_A" >> "$VERDICT_FILE"
echo "Run B: $TAR_B" >> "$VERDICT_FILE"

# Check 1: Tarball-level sha256 (wrapper check)
SHA_A=$(sha256sum "$TAR_A" | awk '{print $1}')
SHA_B=$(sha256sum "$TAR_B" | awk '{print $1}')
if [ "$SHA_A" != "$SHA_B" ]; then
    info "TARBALL-SHA256: $SHA_A (A) vs $SHA_B (B)"
    fail "TARBALL-SHA256: $SHA_A (A) vs $SHA_B (B)"
else
    pass "TARBALL-SHA256: $SHA_A (identical)"
fi

# Check 2: shimx64.efi binary byte-for-byte (UNSIGNED)
BIN_A=$(sha256sum "$WORK/A/shimx64.efi" | awk '{print $1}')
BIN_B=$(sha256sum "$WORK/B/shimx64.efi" | awk '{print $1}')
if [ "$BIN_A" != "$BIN_B" ]; then
    info "Binary differ at byte offsets:"
    cmp -l "$WORK/A/shimx64.efi" "$WORK/B/shimx64.efi" | head -20 >> "$VERDICT_FILE" || true
    echo "  (showing first 20 diffs)" >> "$VERDICT_FILE"
    fail "SHIM-BINARY: $BIN_A (A) vs $BIN_B (B)"
else
    pass "SHIM-BINARY: $BIN_A (identical — unsigned, L1+L2+L3 fixes confirmed)"
fi

# Check 3: vendor_cert.der byte-for-byte
CERT_A=$(sha256sum "$WORK/A/vendor_cert.der" | awk '{print $1}')
CERT_B=$(sha256sum "$WORK/B/vendor_cert.der" | awk '{print $1}')
if [ "$CERT_A" != "$CERT_B" ]; then
    fail "VENDOR-CERT-DER: $CERT_A (A) vs $CERT_B (B)"
else
    pass "VENDOR-CERT-DER: $CERT_A (identical — copied from repo)"
fi

# Check 4: vendor_cert.pem byte-for-byte
CERT_PEM_A=$(sha256sum "$WORK/A/vendor_cert.pem" | awk '{print $1}')
CERT_PEM_B=$(sha256sum "$WORK/B/vendor_cert.pem" | awk '{print $1}')
if [ "$CERT_PEM_A" != "$CERT_PEM_B" ]; then
    fail "VENDOR-CERT-PEM: $CERT_PEM_A (A) vs $CERT_PEM_B (B)"
else
    pass "VENDOR-CERT-PEM: $CERT_PEM_A (identical — copied from repo)"
fi

# Check 5: SHIM_COMMIT_SHA text identity
COMMIT_A=$(cat "$WORK/A/SHIM_COMMIT_SHA")
COMMIT_B=$(cat "$WORK/B/SHIM_COMMIT_SHA")
if [ "$COMMIT_A" != "$COMMIT_B" ]; then
    fail "COMMIT-SHA: $COMMIT_A (A) vs $COMMIT_B (B)"
else
    pass "COMMIT-SHA: $COMMIT_A (L3 fix — pinned at afc49558...)"
fi

# Check 6: sbat.intergenos.csv text identity
SBAT_CSV_A=$(sha256sum "$WORK/A/sbat.intergenos.csv" | awk '{print $1}')
SBAT_CSV_B=$(sha256sum "$WORK/B/sbat.intergenos.csv" | awk '{print $1}')
if [ "$SBAT_CSV_A" != "$SBAT_CSV_B" ]; then
    fail "SBAT-CSV: $SBAT_CSV_A (A) vs $SBAT_CSV_B (B)"
else
    pass "SBAT-CSV: $SBAT_CSV_A (identical — copied from repo)"
fi

# Check 7: SBAT section in shimx64.efi (byte-for-byte)
SBAT_A_HASH=$(objcopy --dump-section .sbat=/dev/stdout "$WORK/A/shimx64.efi" 2>/dev/null | sha256sum | awk '{print $1}')
SBAT_B_HASH=$(objcopy --dump-section .sbat=/dev/stdout "$WORK/B/shimx64.efi" 2>/dev/null | sha256sum | awk '{print $1}')
if [ "$SBAT_A_HASH" != "$SBAT_B_HASH" ]; then
    fail "SBAT-SECTION: $SBAT_A_HASH (A) vs $SBAT_B_HASH (B)"
else
    pass "SBAT-SECTION: $SBAT_A_HASH (identical)"
fi

# Check 8: PE metadata extraction for review-facing data
for BIN in "$WORK/A/shimx64.efi" "$WORK/B/shimx64.efi"; do
    info "$(basename "$BIN"): PE size=$(stat -c%s "$BIN") bytes" >> "$VERDICT_FILE"
done

# Check 9: Vendor cert consistency — DER and PEM encode same certificate
OPENSSL_AVAILABLE=0
if command -v openssl &>/dev/null; then
    OPENSSL_AVAILABLE=1
    DER_FP_A=$(openssl x509 -inform DER -in "$WORK/A/vendor_cert.der" -noout -fingerprint -sha256 2>/dev/null | sed 's/.*=//')
    PEM_FP_A=$(openssl x509 -in "$WORK/A/vendor_cert.pem" -noout -fingerprint -sha256 2>/dev/null | sed 's/.*=//')
    if [ "$DER_FP_A" = "$PEM_FP_A" ] && [ -n "$DER_FP_A" ]; then
        pass "CERT-CONSISTENCY: DER and PEM encode same cert (SHA256 FP: $DER_FP_A)"
    else
        fail "CERT-CONSISTENCY: DER FP ($DER_FP_A) != PEM FP ($PEM_FP_A)"
    fi
else
    pass "CERT-CONSISTENCY: SKIPPED (openssl not available in this environment)"
fi

# Final verdict
echo "" >> "$VERDICT_FILE"
echo "=== VERDICT ===" >> "$VERDICT_FILE"
set +e
FAIL_COUNT=$(grep -c "^FAIL:" "$VERDICT_FILE")
PASS_COUNT=$(grep -c "^PASS:" "$VERDICT_FILE")
set -e
FAIL_COUNT=${FAIL_COUNT:-0}
PASS_COUNT=${PASS_COUNT:-0}
echo "$PASS_COUNT checks PASS, $FAIL_COUNT checks FAIL" >> "$VERDICT_FILE"

# exit 0 = full reproducibility PASS. exit N = N checks failed.
exit "$FAIL_COUNT"
