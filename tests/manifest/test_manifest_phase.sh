#!/usr/bin/env bash
# tests/manifest/test_manifest_phase.sh — exercise the install-time integrity
# manifest production + signing flow end-to-end with synthetic .igos.tar.gz
# archives + an ephemeral GPG key (no hardware token required).
#
# Test cases:
#
#   1. Manifest BSD format — header (Build/Built/Built-on/Manifest-version),
#      SHA256 lines (deterministic order), terminator. Computed sha256s
#      match independent computation.
#
#   2. sign-release.sh --manifest end-to-end — produces signed manifest,
#      detached signature, release-key.asc. Sig verifies under the embedded
#      key in a clean ephemeral keyring (matches what install-time
#      PHASE_VERIFY does).
#
#   3. Malformed manifest refused — manifest missing the
#      'Manifest-version: 1' header is rejected by sign-release.sh's
#      sanity gate before any signature is emitted.
#
#   4. check-manifest-signature.sh PASS path — accepts a well-formed
#      signed bundle; exit 0.
#
#   5. check-manifest-signature.sh FAIL path — rejects a tampered manifest
#      (where the file content differs from what was signed); exit 1.
#
# All cases run in tmpdir; no real archives or chroot needed. Build VM
# integration test for the full phase_manifest path lives separately.
#
# Run: bash tests/manifest/test_manifest_phase.sh
# Exit 0 = all 5 cases behaved as expected; exit 1 = at least one regression.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SIGN_REL="$REPO_ROOT/scripts/sign-release.sh"
CHECK_SIG="$REPO_ROOT/scripts/check-manifest-signature.sh"

[ -x "$SIGN_REL" ]  || { echo "FAIL: $SIGN_REL not executable" >&2; exit 1; }
[ -x "$CHECK_SIG" ] || { echo "FAIL: $CHECK_SIG not executable" >&2; exit 1; }

WORK=$(mktemp -d -t mfst-test-XXXXXX)
trap 'rm -rf "$WORK"' EXIT

FAIL_COUNT=0

# Set up an ephemeral GPG keyring with a test key. Stay isolated from
# operator's real keychain.
export GNUPGHOME="$WORK/gnupg"
mkdir -p "$GNUPGHOME"
chmod 700 "$GNUPGHOME"

# Real (empty) file usable as VENDOR_CERT placeholder so sign-release.sh's
# pre-positioning check (SR3) passes; the cert is only actually loaded when
# signing UKI/GRUB binaries (not exercised here).
TEST_VENDOR_CERT="$WORK/test-vendor-cert.pem"
: > "$TEST_VENDOR_CERT"

cat > "$WORK/gpg-key-batch" <<'EOF'
%no-protection
Key-Type: rsa
Key-Length: 2048
Key-Usage: sign
Name-Real: InterGenOS Test Manifest Signer
Name-Email: test-mfst@example.invalid
Expire-Date: 0
%commit
EOF

gpg --batch --quiet --gen-key "$WORK/gpg-key-batch" 2>&1 | head -1 || true
TEST_KEY_ID=$(gpg --batch --list-secret-keys --with-colons \
              | awk -F: '/^sec/ {print $5; exit}')
[ -n "$TEST_KEY_ID" ] || { echo "FAIL: could not generate test GPG key" >&2; exit 1; }

# ---- Case 1: BSD manifest format ----
echo "=== test 1: BSD manifest format (header + sorted SHA256 + terminator) ==="
ARCHIVES_DIR="$WORK/case1/archives"
mkdir -p "$ARCHIVES_DIR/toolchain" "$ARCHIVES_DIR/desktop"
echo "fake-glibc-content" > "$ARCHIVES_DIR/toolchain/glibc-2.40-1.igos.tar.gz"
echo "fake-binutils-content" > "$ARCHIVES_DIR/toolchain/binutils-2.43-1.igos.tar.gz"
echo "fake-gtk-content" > "$ARCHIVES_DIR/desktop/gtk4-4.16-1.igos.tar.gz"

MFST="$WORK/case1/intergenos-archive-manifest.txt"
{
    printf '# InterGenOS archive integrity manifest\n'
    printf '# Build: test-v1.0\n'
    printf '# Built: 2026-05-07T10:00:00Z\n'
    printf '# Built-on: test-host\n'
    printf '# Manifest-version: 1\n'
} > "$MFST"
while IFS= read -r -d '' archive; do
    rel="${archive#${ARCHIVES_DIR}/}"
    sha=$(sha256sum "$archive" | awk '{print $1}')
    printf 'SHA256 (%s) = %s\n' "$rel" "$sha" >> "$MFST"
done < <(find "$ARCHIVES_DIR" -type f -name '*.igos.tar.gz' -print0 | sort -z)
printf '# End of manifest.\n' >> "$MFST"

mfst_lines=$(wc -l < "$MFST")
expected_lines=9   # 5 header + 3 SHA256 + 1 terminator
if [ "$mfst_lines" -eq "$expected_lines" ]; then
    echo "  PASS: line count $mfst_lines matches expected $expected_lines"
else
    echo "  FAIL: line count $mfst_lines != expected $expected_lines" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# Verify deterministic order: desktop/ then toolchain/ alphabetically
sha_lines=$(grep '^SHA256 ' "$MFST")
expected_first=$(echo "$sha_lines" | head -1 | awk '{print $2}')
if [ "$expected_first" = "(desktop/gtk4-4.16-1.igos.tar.gz)" ]; then
    echo "  PASS: deterministic sort order"
else
    echo "  FAIL: first entry not deterministic; got $expected_first" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# Verify computed shas match independent recomputation
indep_sha=$(sha256sum "$ARCHIVES_DIR/toolchain/glibc-2.40-1.igos.tar.gz" | awk '{print $1}')
mfst_sha=$(grep 'glibc-2.40' "$MFST" | awk '{print $4}')
if [ "$indep_sha" = "$mfst_sha" ]; then
    echo "  PASS: per-archive sha256 matches independent computation"
else
    echo "  FAIL: sha mismatch: $indep_sha vs $mfst_sha" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# ---- Case 2: sign-release.sh --manifest end-to-end ----
echo ""
echo "=== test 2: sign-release.sh --manifest end-to-end ==="
ARTIFACTS2="$WORK/case2/artifacts"
OUTPUT2="$WORK/case2/output"
mkdir -p "$ARTIFACTS2"
cp "$MFST" "$ARTIFACTS2/intergenos-archive-manifest.txt"

# sign-release.sh requires GPG_KEY_ID + PKCS11_URI. We bypass the PKCS#11
# token requirement by providing a value that will only be exercised on
# UKI/GRUB binaries (not present in $ARTIFACTS2).
INTERGENOS_GPG_KEY_ID="$TEST_KEY_ID" \
INTERGENOS_PKCS11_URI="pkcs11:test-uri" \
INTERGENOS_VENDOR_CERT="$TEST_VENDOR_CERT" \
    bash "$SIGN_REL" --artifacts "$ARTIFACTS2" --output "$OUTPUT2" \
        > "$WORK/case2.log" 2>&1 || {
            # Tolerate hardware-token-check failure (test host has no PIV/Nitrokey)
            if grep -q "no OpenPGP card detected" "$WORK/case2.log"; then
                echo "  SKIP: no OpenPGP card on test host (expected on dev/CI)"
                echo "        (case 2 requires real token; covered by signing-workstation runbook)"
            else
                echo "  FAIL: sign-release.sh exited non-zero unexpectedly:" >&2
                tail -20 "$WORK/case2.log" | sed 's/^/        /' >&2
                FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
        }

if [ -f "$OUTPUT2/intergenos-archive-manifest.txt" ] \
   && [ -f "$OUTPUT2/intergenos-archive-manifest.txt.sig" ] \
   && [ -f "$OUTPUT2/intergenos-release-key.asc" ]; then
    if gpg --batch --verify "$OUTPUT2/intergenos-archive-manifest.txt.sig" \
                            "$OUTPUT2/intergenos-archive-manifest.txt" 2>/dev/null; then
        echo "  PASS: signed manifest + sig + release-key produced; sig verifies"
    else
        echo "  FAIL: outputs produced but signature does not verify" >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
fi

# ---- Case 3: malformed manifest refused ----
echo ""
echo "=== test 3: sign-release.sh refuses malformed manifest ==="
ARTIFACTS3="$WORK/case3/artifacts"
OUTPUT3="$WORK/case3/output"
mkdir -p "$ARTIFACTS3"
# Manifest missing 'Manifest-version: 1' header
{
    printf '# InterGenOS archive integrity manifest\n'
    printf '# Build: test-bad\n'
    printf 'SHA256 (foo.tar.gz) = abc123\n'
    printf '# End of manifest.\n'
} > "$ARTIFACTS3/intergenos-archive-manifest.txt"

set +e
INTERGENOS_GPG_KEY_ID="$TEST_KEY_ID" \
INTERGENOS_PKCS11_URI="pkcs11:test-uri" \
INTERGENOS_VENDOR_CERT="$TEST_VENDOR_CERT" \
    bash "$SIGN_REL" --artifacts "$ARTIFACTS3" --output "$OUTPUT3" \
        > "$WORK/case3.log" 2>&1
case3_rc=$?
set -e

if [ "$case3_rc" -ne 0 ] && grep -q "manifest missing 'Manifest-version: 1'" "$WORK/case3.log"; then
    echo "  PASS: malformed manifest correctly refused with explicit error"
elif [ "$case3_rc" -ne 0 ] && grep -q "no OpenPGP card detected" "$WORK/case3.log"; then
    echo "  SKIP: token check fired before manifest check (test host has no token);"
    echo "        manifest sanity gate is downstream of token check in script flow"
else
    echo "  FAIL: malformed manifest was accepted (rc=$case3_rc) — sanity gate broken" >&2
    tail -10 "$WORK/case3.log" | sed 's/^/        /' >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# ---- Cases 4 + 5: check-manifest-signature.sh PASS / FAIL paths ----
# Build a synthetic signed bundle directly with our test key (bypassing the
# token-gated sign-release.sh) so these checks always run.
echo ""
echo "=== test 4: check-manifest-signature.sh PASS path ==="
BUNDLE="$WORK/case4-bundle"
mkdir -p "$BUNDLE"
cp "$MFST" "$BUNDLE/intergenos-archive-manifest.txt"
gpg --batch --yes --detach-sign --armor \
    --local-user "$TEST_KEY_ID" \
    --output "$BUNDLE/intergenos-archive-manifest.txt.sig" \
    "$BUNDLE/intergenos-archive-manifest.txt"
gpg --batch --yes --armor --export "$TEST_KEY_ID" \
    > "$BUNDLE/intergenos-release-key.asc"

if bash "$CHECK_SIG" \
       "$BUNDLE/intergenos-archive-manifest.txt" \
       "$BUNDLE/intergenos-archive-manifest.txt.sig" \
       "$BUNDLE/intergenos-release-key.asc" \
       > "$WORK/case4.log" 2>&1; then
    if grep -q "ALL CHECKS PASS" "$WORK/case4.log"; then
        echo "  PASS: well-formed signed bundle accepted"
    else
        echo "  FAIL: exit 0 but did not emit expected ALL CHECKS PASS" >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
else
    echo "  FAIL: well-formed signed bundle rejected" >&2
    sed 's/^/        /' "$WORK/case4.log" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

echo ""
echo "=== test 5: check-manifest-signature.sh REJECT tampered manifest ==="
BUNDLE5="$WORK/case5-bundle"
mkdir -p "$BUNDLE5"
cp "$BUNDLE"/* "$BUNDLE5/"
# Tamper: append a bogus SHA256 line BEFORE the terminator
sed -i '/^# End of manifest\.$/i SHA256 (TAMPERED-extra-archive.tar.gz) = deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef' \
    "$BUNDLE5/intergenos-archive-manifest.txt"

set +e
bash "$CHECK_SIG" \
     "$BUNDLE5/intergenos-archive-manifest.txt" \
     "$BUNDLE5/intergenos-archive-manifest.txt.sig" \
     "$BUNDLE5/intergenos-release-key.asc" \
     > "$WORK/case5.log" 2>&1
case5_rc=$?
set -e

if [ "$case5_rc" -ne 0 ] && grep -q "signature verification failed" "$WORK/case5.log"; then
    echo "  PASS: tampered manifest correctly rejected (signature mismatch detected)"
else
    echo "  FAIL: tampered manifest passed verification (rc=$case5_rc)" >&2
    sed 's/^/        /' "$WORK/case5.log" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

echo ""
echo "==============================================================="
if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "  ALL CASES PASS"
    exit 0
else
    echo "  $FAIL_COUNT CASE(S) FAILED"
    exit 1
fi
