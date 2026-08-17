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

# ---------------------------------------------------------------------------
# HOST CAPABILITY PROBE for cases 2 and 3.
#
# WHY THIS EXISTS — a measured cross-box failure, not a hypothetical. Both
# cases need sign-release.sh to get PAST its hardware-token gate, which no
# machine without the ceremony hardware can do. The previous code inferred
# that from the TEXT of the error sign-release.sh happened to print, matching
# one exact string ("no OpenPGP card detected") and treating every other
# message as a real failure.
#
# That made the outcome depend on WHICH precondition a given host failed
# FIRST. A machine carrying PARTIAL signing state — gpg configuration and a
# keyring, no token plugged in — gets past or around the card line and dies
# later on a different one, the match misses, and the case reports FAIL on a
# host where nothing is actually wrong. Measured 2026-08-05: this suite passes
# on a machine with no signing state and fails on the ceremony machine, which
# is also the machine the canonical test suite runs on.
#
# So the probe decides from OBSERVABLE STATE, in the same order
# sign-release.sh checks it, and NAMES the first thing that is missing. Same
# answer on every host; no dependence on another script's wording; and
# sign-release.sh is not invoked at all when a precondition is unmet, so there
# is no error text left to misread.
#
# Both inputs are overridable ONLY so the probe can be proven to detect both
# outcomes (case 2b below) — an instrument never shown to detect a true
# positive cannot certify a zero. Nothing in a normal run passes them.
# ---------------------------------------------------------------------------
CARD_PROBE_CMD="${MFST_TEST_CARD_CMD:-gpg --card-status}"
PKCS11_MODULE_PATH="${INTERGENOS_PKCS11_MODULE:-/usr/local/lib/opensc-pkcs11.so}"

ceremony_blocker() {
    if ! $CARD_PROBE_CMD >/dev/null 2>&1; then
        echo "no OpenPGP card is readable on this host"
        return 0
    fi
    if [ ! -f "$PKCS11_MODULE_PATH" ]; then
        echo "the patched OpenSC PKCS#11 module is absent ($PKCS11_MODULE_PATH)"
        return 0
    fi
    # Reached only on a real signing workstation. This harness supplies an
    # EMPTY placeholder vendor cert, so sign-release.sh's modulus-match guard
    # would refuse there for a reason that is the harness's doing rather than
    # a defect — say so instead of reporting a failure.
    if [ ! -s "$TEST_VENDOR_CERT" ]; then
        echo "this harness supplies a placeholder vendor cert, which the modulus-match guard correctly refuses"
        return 0
    fi
    echo ""
}

# ---- Case 2: sign-release.sh --manifest end-to-end ----
echo ""
echo "=== test 2: sign-release.sh --manifest end-to-end ==="
BLOCKER="$(ceremony_blocker)"
if [ -n "$BLOCKER" ]; then
    echo "  SKIP: $BLOCKER"
    echo "        The end-to-end signing path needs the real ceremony materials;"
    echo "        it is covered by the signing-workstation runbook. Skipped the"
    echo "        same way on every host, from observed state — NOT from the"
    echo "        wording of an error."
else
    ARTIFACTS2="$WORK/case2/artifacts"
    OUTPUT2="$WORK/case2/output"
    mkdir -p "$ARTIFACTS2"
    cp "$MFST" "$ARTIFACTS2/intergenos-archive-manifest.txt"

    case2_rc=0
    INTERGENOS_GPG_KEY_ID="$TEST_KEY_ID" \
    INTERGENOS_PKCS11_URI="pkcs11:test-uri" \
    INTERGENOS_VENDOR_CERT="$TEST_VENDOR_CERT" \
        bash "$SIGN_REL" --artifacts "$ARTIFACTS2" --output "$OUTPUT2" \
            > "$WORK/case2.log" 2>&1 || case2_rc=$?

    if [ "$case2_rc" -ne 0 ]; then
        # The host said it was capable, so a non-zero exit is a real failure.
        echo "  FAIL: sign-release.sh exited $case2_rc on a host reporting full ceremony state:" >&2
        tail -20 "$WORK/case2.log" | sed 's/^/        /' >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
    elif [ ! -f "$OUTPUT2/intergenos-archive-manifest.txt" ] \
      || [ ! -f "$OUTPUT2/intergenos-archive-manifest.txt.sig" ] \
      || [ ! -f "$OUTPUT2/intergenos-release-key.asc" ]; then
        # Previously this state produced NO line at all — neither pass nor
        # fail. A case that can finish silently is a case that can rot.
        echo "  FAIL: sign-release.sh exited 0 but did not produce the signed bundle" >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
    elif gpg --batch --verify "$OUTPUT2/intergenos-archive-manifest.txt.sig" \
                              "$OUTPUT2/intergenos-archive-manifest.txt" 2>/dev/null; then
        echo "  PASS: signed manifest + sig + release-key produced; sig verifies"
    else
        echo "  FAIL: outputs produced but signature does not verify" >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
fi

# ---- Case 2b: the probe itself must be able to say BOTH things ----
# Without this, "SKIP" above is unfalsifiable: a probe that always reports a
# blocker would skip on every host forever and read as coverage. This drives
# the probe against constructed states, including the exact shape that broke
# the suite cross-box — a readable card with the PKCS#11 module absent.
echo ""
echo "=== test 2b: the capability probe detects each state it claims to ==="
probe_with() {                     # $1 = card cmd, $2 = module path
    ( CARD_PROBE_CMD="$1"; PKCS11_MODULE_PATH="$2"; ceremony_blocker )
}
FAKE_MODULE="$WORK/fake-opensc-pkcs11.so"
: > "$FAKE_MODULE"

# (a) production default on THIS host — never an override, so the real path
#     is exercised and not just the injectable one.
default_blocker="$(ceremony_blocker)"
if [ -n "$default_blocker" ] || [ -f "$PKCS11_MODULE_PATH" ]; then
    echo "  PASS: the real default resolves to a definite answer ('${default_blocker:-capable}')"
else
    echo "  FAIL: the real default reported capable with no PKCS#11 module present" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# (b) card unreadable -> names the card, whatever else is true.
if [ "$(probe_with "false" "$FAKE_MODULE")" = "no OpenPGP card is readable on this host" ]; then
    echo "  PASS: an unreadable card is named as the blocker"
else
    echo "  FAIL: an unreadable card was not named as the blocker" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# (c) THE CROSS-BOX SHAPE: card readable, module absent. The old string-match
#     reported FAIL here; the probe must name the module and skip.
case "$(probe_with "true" "$WORK/definitely-not-here.so")" in
    "the patched OpenSC PKCS#11 module is absent"*)
        echo "  PASS: card-present + module-absent is named, not reported as a failure" ;;
    *)
        echo "  FAIL: card-present + module-absent did not name the module" >&2
        FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
esac

# (d) everything present except the harness's own placeholder cert -> says so.
case "$(probe_with "true" "$FAKE_MODULE")" in
    "this harness supplies a placeholder vendor cert"*)
        echo "  PASS: a fully-equipped host is told what the harness itself withholds" ;;
    *)
        echo "  FAIL: a fully-equipped host did not get the placeholder-cert reason" >&2
        FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
esac

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

# Same gate as case 2, and for the same reason: the manifest sanity check sits
# DOWNSTREAM of the token check in sign-release.sh's flow, so a host without
# the ceremony materials cannot reach it. Decided from observed state rather
# than from which error string came back.
if [ -n "$BLOCKER" ]; then
    echo "  SKIP: $BLOCKER"
    echo "        The manifest sanity gate is downstream of the token check, so"
    echo "        it is unreachable here. Skipped from observed state, the same"
    echo "        way on every host."
else
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
    else
        echo "  FAIL: malformed manifest was accepted (rc=$case3_rc) — sanity gate broken" >&2
        tail -10 "$WORK/case3.log" | sed 's/^/        /' >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
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
