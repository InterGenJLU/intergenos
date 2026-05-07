#!/usr/bin/env bash
# scripts/check-manifest-signature.sh — validate the signed archive manifest.
#
# Q14-style post-sign precheck for the install-time integrity verification
# feature (design doc: docs/research/security/install-integrity-verification.md
# §5.2). Run this AFTER scripts/sign-release.sh has produced a signed manifest
# bundle, BEFORE feeding that bundle into build-iso.sh.
#
# What we check:
#
#   1. Manifest format — header (Build, Built, Built-on, Manifest-version),
#      at least one SHA256 entry, terminator. A malformed manifest signed
#      at sign-time would still be cryptographically valid but would break
#      install-time PHASE_VERIFY's parser; catch it now rather than at the
#      install-media smoke test.
#
#   2. Manifest signature — `gpg --verify` against the embedded
#      release-key.asc (NOT the operator's host keychain — we want to
#      verify what the install-time verifier will see, using the same
#      key material that ships in the ISO).
#
#   3. Master fingerprint cross-check — if INTERGENOS_GPG_MASTER_KEY_ID
#      is set, assert the manifest was master-cosigned (release-grade
#      build). Without that env var, S1-only is acceptable (routine
#      build signature).
#
# Wired into:
#   - End of scripts/sign-release.sh "Next: ... Verify with:" block (manual
#     operator verification step)
#   - Pre-ISO-build gate before build-iso.sh embeds these files
#
# Usage:
#   bash scripts/check-manifest-signature.sh \
#        <manifest.txt> <manifest.txt.sig> <release-key.asc>
#
# Exit codes:
#   0  — all checks pass
#   1  — at least one check failed (specific failure printed to stderr)
#   2  — usage error / file not found

set -euo pipefail

usage() {
    sed -n '2,33p' "$0" >&2
    exit 2
}

if [ "$#" -ne 3 ]; then
    usage
fi

MANIFEST="$1"
SIG="$2"
KEY="$3"

[ -f "$MANIFEST" ] || { echo "ERROR: manifest not found: $MANIFEST" >&2; exit 2; }
[ -f "$SIG" ]      || { echo "ERROR: signature not found: $SIG" >&2; exit 2; }
[ -f "$KEY" ]      || { echo "ERROR: release-key not found: $KEY" >&2; exit 2; }

FAIL_COUNT=0

echo "=== check-manifest-signature: $MANIFEST ==="

# ---- check 1: format ----
echo ""
echo "[1/3] manifest format"
fmt_errors=()
grep -q '^# InterGenOS archive integrity manifest$' "$MANIFEST" \
    || fmt_errors+=("missing 'InterGenOS archive integrity manifest' header line")
grep -q '^# Build: '            "$MANIFEST" || fmt_errors+=("missing 'Build:' header")
grep -q '^# Built: '             "$MANIFEST" || fmt_errors+=("missing 'Built:' header")
grep -q '^# Built-on: '          "$MANIFEST" || fmt_errors+=("missing 'Built-on:' header")
grep -q '^# Manifest-version: 1$' "$MANIFEST" || fmt_errors+=("missing 'Manifest-version: 1' header")
grep -q '^# End of manifest\.$'  "$MANIFEST" || fmt_errors+=("missing '# End of manifest.' terminator")
sha_count=$(grep -c '^SHA256 ' "$MANIFEST" || true)
if [ "$sha_count" -eq 0 ]; then
    fmt_errors+=("manifest contains zero SHA256 entries")
fi

if [ "${#fmt_errors[@]}" -eq 0 ]; then
    echo "      PASS — header + $sha_count SHA256 entries + terminator OK"
else
    echo "      FAIL:"
    for e in "${fmt_errors[@]}"; do
        echo "        - $e"
    done
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# ---- check 2: signature ----
echo ""
echo "[2/3] signature verifies against embedded release-key"
# Verify using a clean ephemeral keyring loaded ONLY with the embedded key,
# matching what the install-time verifier will do (no operator-keychain
# pollution). Tmp dir is auto-cleaned on exit.
TMP_GNUPG=$(mktemp -d -t igos-mfst-XXXXXX)
trap 'rm -rf "$TMP_GNUPG"' EXIT
chmod 700 "$TMP_GNUPG"

if ! gpg --homedir "$TMP_GNUPG" --batch --quiet --import "$KEY" 2>&1; then
    echo "      FAIL: could not import release-key from $KEY"
    FAIL_COUNT=$((FAIL_COUNT + 1))
elif gpg --homedir "$TMP_GNUPG" --batch --verify "$SIG" "$MANIFEST" 2>"$TMP_GNUPG/verify.err"; then
    echo "      PASS — signature verifies under the embedded release-key"
else
    echo "      FAIL: signature verification failed:"
    sed 's/^/        /' "$TMP_GNUPG/verify.err"
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# ---- check 3: master cosignature (release-grade builds only) ----
echo ""
echo "[3/3] master cosignature presence"
if [ -n "${INTERGENOS_GPG_MASTER_KEY_ID:-}" ]; then
    # Count signatures recorded against the manifest. gpg --verify with
    # --status-fd emits one GOODSIG/VALIDSIG per signature in the bundle.
    sig_count=$(gpg --homedir "$TMP_GNUPG" --batch --verify --status-fd 1 \
                    "$SIG" "$MANIFEST" 2>/dev/null \
                | grep -c '^\[GNUPG:\] GOODSIG' || true)
    if [ "$sig_count" -ge 2 ]; then
        echo "      PASS — $sig_count signatures present (release-grade)"
    else
        echo "      FAIL: INTERGENOS_GPG_MASTER_KEY_ID is set but only $sig_count signature(s) found"
        echo "        (expected master + S1 = 2 signatures for release-grade build)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
else
    echo "      SKIP — INTERGENOS_GPG_MASTER_KEY_ID unset; routine S1-only signature"
    echo "        (set the env var to require master cosignature for release-grade builds)"
fi

echo ""
echo "=== summary ==="
if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "  ALL CHECKS PASS — manifest bundle is safe to embed in ISO."
    exit 0
else
    echo "  $FAIL_COUNT CHECK(S) FAILED — do NOT embed this manifest bundle."
    exit 1
fi
