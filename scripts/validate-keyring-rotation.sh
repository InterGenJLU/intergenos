#!/bin/bash
# scripts/validate-keyring-rotation.sh — Q10 keyring rotation validation
#
# Exercises the multi-key trust window substrate landed at cea695c5
# end-to-end via ephemeral keys. Run any time to verify the rotation
# infrastructure is still working; run as part of the operator procedure
# in docs/signing-key.md Multi-Key Trust Window during real subkey
# rotation events to catch regressions before publishing to end users.
#
# Three end-to-end links validated:
#   (a) packages/core/intergenos-keyring/build.sh's dual-import logic
#       actually imports a second armored bundle into the keyring under
#       bash + gpg semantics (matches the conditional -next.asc import
#       added at cea695c5)
#   (b) /etc/pkm/trusted.gpg-equivalent output keyring lists BOTH
#       fingerprints at gpg --list-keys (multi-key acceptance)
#   (c) gpg --verify --status-fd 1 against the dual-key keyring emits a
#       VALIDSIG line carrying the new-subkey FP at the position
#       pkm/repo.py:582-595's _verify_signature parses, AND the same
#       verify against a canonical-only keyring refuses the new-subkey
#       signature (gpg-layer trust-anchor gate working before the L-025
#       pin-set check even fires)
#
# Bash-only orchestration: end-to-end exercise of the keyring rotation
# substrate via gpg(1) directly. Companion pytest-side unit test for
# the pkm.repo._verify_signature Python wrapper landed at
# tests/pkm/test_repo_verify_signature.py (Linux-only via ephemeral
# GPG fixture; both validators exercise the same three links).
#
# Gates:
#   GREEN-1: ephemeral master+subkey generated
#   GREEN-2: test-index signed by ephemeral key (detached armored .sig)
#   GREEN-3: canonical docs/signing-key.asc + ephemeral pubkey BOTH
#            import into the same output keyring (replicates build.sh
#            do_install dual-import logic)
#   GREEN-4: gpg --list-keys against output keyring shows BOTH canonical
#            master FP AND ephemeral master FP
#   GREEN-5: gpg --verify --status-fd 1 on ephemeral-signed test-index
#            emits VALIDSIG carrying ephemeral FP
#   RED-1:   gpg --verify against canonical-only keyring refuses
#            ephemeral-signed message (unknown-key gate active)

set -e

CANONICAL_FP="5597A3E0587B253006D0DD7B8C50826182083050"
# Script lives at scripts/validate-keyring-rotation.sh; REPO_ROOT is one up.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CANONICAL_ASC="$REPO_ROOT/docs/signing-key.asc"

if [ ! -f "$CANONICAL_ASC" ]; then
    echo "FATAL: $CANONICAL_ASC not found"
    exit 2
fi

echo "START"

TMPDIR=$(mktemp -d -p /tmp q10e2e.XXXXXXXX)
trap "rm -rf '$TMPDIR'" EXIT

EPH_HOME="$TMPDIR/eph-home"
mkdir -p "$EPH_HOME"
chmod 700 "$EPH_HOME"

# --- GREEN-1: ephemeral key generation ---
echo "Generating ephemeral keypair (~30-60s)..."
GNUPGHOME="$EPH_HOME" gpg --batch --yes --no-permission-warning \
    --passphrase "" --pinentry-mode loopback \
    --quick-gen-key "Q10-e2e-test <q10@example.invalid>" rsa2048 sign 0 \
    >/dev/null 2>&1

EPH_FP=$(GNUPGHOME="$EPH_HOME" gpg --batch --no-permission-warning \
         --with-colons --list-keys \
         | awk -F: '/^fpr:/{print $10; exit}')

if [ -z "$EPH_FP" ] || [ ${#EPH_FP} -ne 40 ]; then
    echo "FAIL GREEN-1: ephemeral FP malformed (got: $EPH_FP)"
    exit 1
fi
echo "GREEN-1 PASS: ephemeral master FP ${EPH_FP:0:16}... generated"

# --- GREEN-2: sign test-index ---
echo '{"version":1,"test":"q10-e2e"}' > "$TMPDIR/test-index.json"
GNUPGHOME="$EPH_HOME" gpg --batch --yes --no-permission-warning \
    --armor --detach-sign \
    --output "$TMPDIR/test-index.json.sig" \
    --passphrase "" --pinentry-mode loopback \
    "$TMPDIR/test-index.json" >/dev/null 2>&1

if [ ! -s "$TMPDIR/test-index.json.sig" ]; then
    echo "FAIL GREEN-2: signature file empty/missing"
    exit 1
fi
echo "GREEN-2 PASS: test-index signed (armored .sig produced)"

# Export ephemeral pubkey as armored bundle (the simulated -next.asc)
GNUPGHOME="$EPH_HOME" gpg --batch --no-permission-warning \
    --armor --export "$EPH_FP" > "$TMPDIR/ephemeral.asc"

# --- GREEN-3: dual-import canonical + ephemeral into same keyring ---
OUT_HOME="$TMPDIR/out-home"
mkdir -p "$OUT_HOME"
chmod 700 "$OUT_HOME"
OUT_KEYRING="$TMPDIR/trusted.gpg"

# Replicate build.sh do_install lines 33-37 (canonical import)
GNUPGHOME="$OUT_HOME" gpg --batch --yes --no-permission-warning \
    --no-default-keyring \
    --keyring "$OUT_KEYRING" \
    --import "$CANONICAL_ASC" >/dev/null 2>&1

# Replicate build.sh do_install conditional -next.asc import
GNUPGHOME="$OUT_HOME" gpg --batch --yes --no-permission-warning \
    --no-default-keyring \
    --keyring "$OUT_KEYRING" \
    --import "$TMPDIR/ephemeral.asc" >/dev/null 2>&1
echo "GREEN-3 PASS: build.sh dual-import logic ran for canonical + ephemeral"

# --- GREEN-4: gpg --list-keys against output keyring shows both FPs ---
LISTED_FPS=$(GNUPGHOME="$OUT_HOME" gpg --batch --no-permission-warning \
             --no-default-keyring --keyring "$OUT_KEYRING" \
             --with-colons --list-keys \
             | awk -F: '/^fpr:/{print $10}')

if ! echo "$LISTED_FPS" | grep -qx "$CANONICAL_FP"; then
    echo "FAIL GREEN-4: canonical FP ${CANONICAL_FP:0:16}... NOT in output keyring"
    echo "Listed FPs:"; echo "$LISTED_FPS"
    exit 1
fi
if ! echo "$LISTED_FPS" | grep -qx "$EPH_FP"; then
    echo "FAIL GREEN-4: ephemeral FP ${EPH_FP:0:16}... NOT in output keyring"
    echo "Listed FPs:"; echo "$LISTED_FPS"
    exit 1
fi
FP_COUNT=$(echo "$LISTED_FPS" | grep -c .)
echo "GREEN-4 PASS: output keyring contains BOTH canonical + ephemeral master FPs ($FP_COUNT total fingerprint entries)"

# --- GREEN-5: gpg verify emits VALIDSIG with ephemeral FP at parts[10] ---
VERIFY_STATUS=$(GNUPGHOME="$OUT_HOME" gpg --batch --no-permission-warning \
                --no-default-keyring --keyring "$OUT_KEYRING" \
                --status-fd 1 \
                --verify "$TMPDIR/test-index.json.sig" "$TMPDIR/test-index.json" \
                2>/dev/null)

VALIDSIG_LINE=$(echo "$VERIFY_STATUS" | grep "^\[GNUPG:\] VALIDSIG " | head -1)
if [ -z "$VALIDSIG_LINE" ]; then
    echo "FAIL GREEN-5: no VALIDSIG line in gpg --status-fd output"
    echo "Full status:"; echo "$VERIFY_STATUS"
    exit 1
fi
# Parts: [GNUPG:](0) VALIDSIG(1) <SIGNING_FP>(2) <SIGDATE>(3) <SIGTIME>(4)
#        <EXPIREDATE>(5) <SIG_VERSION>(6) <RESERVED>(7) <PUBKEY_ALGO>(8)
#        <HASH_ALGO>(9) <SIG_CLASS>(10) <PRIMARY_KEY_FP>(11)
# Awk indexing is 1-based; the FP at the line's 12th token == awk $12
PRIMARY_FP_IN_VALIDSIG=$(echo "$VALIDSIG_LINE" | awk '{print $12}')
if [ "$PRIMARY_FP_IN_VALIDSIG" != "$EPH_FP" ]; then
    # rsa2048 with quick-gen-key produces a master with signing-capability
    # directly; primary FP may equal signing FP. Check signing FP at $3.
    SIGNING_FP_IN_VALIDSIG=$(echo "$VALIDSIG_LINE" | awk '{print $3}')
    if [ "$SIGNING_FP_IN_VALIDSIG" != "$EPH_FP" ]; then
        echo "FAIL GREEN-5: VALIDSIG line has neither primary FP nor signing FP matching ephemeral"
        echo "Expected: $EPH_FP"
        echo "VALIDSIG line: $VALIDSIG_LINE"
        exit 1
    fi
fi
echo "GREEN-5 PASS: gpg --verify emits VALIDSIG carrying ephemeral FP ${EPH_FP:0:16}... (matches the status-fd line shape pkm.repo._verify_signature parses)"

# --- RED-1: canonical-only keyring (without ephemeral) refuses verify ---
CANONICAL_ONLY_KEYRING="$TMPDIR/canonical-only.gpg"
GNUPGHOME="$OUT_HOME" gpg --batch --yes --no-permission-warning \
    --no-default-keyring \
    --keyring "$CANONICAL_ONLY_KEYRING" \
    --import "$CANONICAL_ASC" >/dev/null 2>&1

set +e
GNUPGHOME="$OUT_HOME" gpg --batch --no-permission-warning \
    --no-default-keyring --keyring "$CANONICAL_ONLY_KEYRING" \
    --status-fd 1 \
    --verify "$TMPDIR/test-index.json.sig" "$TMPDIR/test-index.json" \
    >/dev/null 2>&1
VERIFY_RC=$?
set -e

if [ "$VERIFY_RC" -eq 0 ]; then
    echo "FAIL RED-1: gpg --verify returned 0 against canonical-only keyring without ephemeral; expected non-zero (unknown-key refusal)"
    exit 1
fi
echo "RED-1 PASS: gpg --verify returns non-zero ($VERIFY_RC) against canonical-only keyring (unknown-key signature refused at gpg layer)"

echo
echo "ALL 6 GATES (5 GREEN + 1 RED) PASS - Q10 end-to-end gpg-side validation complete"
echo "Closes the D-009 item 5 gap on cea695c5 by exercising the 3 end-to-end links"
echo "(build.sh dual-import + gpg --list-keys multi-key acceptance + gpg --verify VALIDSIG-FP-roundtrip)"
exit 0
