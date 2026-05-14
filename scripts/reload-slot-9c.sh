#!/usr/bin/env bash
# reload-slot-9c.sh — Reprovision NK#1 PIV slot 9c (keypair + cert) to fix
# the C6 cert/keypair desync bug.
#
# Bug: ceremony.py's chicken-egg-fix wrote a dummy cert to slot 9c before
# the selfsign step. OpenSC PIV emulation sources CKA_MODULUS from the cert
# currently in the slot (per OpenSC PIV docs), so openssl req -x509 -new
# embedded the DUMMY pubkey instead of the on-card keypair pubkey. Result:
# the production EFI vendor cert had subject pubkey D (dummy) but signature
# made by key K (real keypair). The cert's self-signature does not validate
# against its own embedded pubkey.
#
# Fix: regenerate the keypair, get a CSR that carries the new pubkey K'
# directly, then use `openssl x509 -req -signkey pkcs11:URI -force_pubkey K'.pem`
# to convert CSR → cert. The -force_pubkey flag forces the cert's subject
# pubkey to the contents of the file (= K'), overriding the pubkey openssl
# would otherwise pull from the -signkey (which would chicken-egg via the
# old cert's CKA_MODULUS on slot 9c). The slot 9c privkey signs the TBS;
# its actual on-card keypair pubkey IS K' (because we just regenerated),
# so the cert self-verifies under -check_ss_sig.
#
# INPUTS:
#   /dev/shm/piv-mgmt.txt   — 64 hex chars (AES-256), with or without colons.
#                              Operator writes this carefully, visually
#                              verifies against paper. Script shreds it after
#                              the first successful read.
#   PIV User PIN            — prompted via read -s (short, low fat-finger risk)
#
# OUTPUTS (staged in /tmp, NOT committed to repo automatically):
#   /tmp/c6r2-csr.pem            CSR from nitropy (contains K' as subject pubkey)
#   /tmp/c6r2-pubkey.{pem,der}   K' pubkey extracted from CSR (audit witness)
#   /tmp/c6r2-cert.{pem,der}     New self-signed vendor cert
#   /tmp/c6r2-on-card.der        Cert read back from card after install
#
# NOT TOUCHED:
#   - NK#1 OpenPGP applet (master sig binding, [S1], [E])
#   - Drive #3 LUKS, paperkeys, keyservers, NK#2/#3/#4
#   - /mnt/intergenos git repo (until operator manually copies staged files)
#
# Owner: InterGenJLU.  Generated 2026-05-13 evening to remediate C6 cert bug.

set -euo pipefail

# Patched OpenSC 0.27.1 at /usr/local — has CI_RSA_4096 forced on for all
# PIV cards. System OpenSC at /usr is untouched.
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"

# ============================================================
# CONSTANTS
# ============================================================
PKCS11_MOD="/usr/local/lib/opensc-pkcs11.so"
PIV_URI="pkcs11:id=%02;type=private"
SUBJ_CN="InterGenOS Secure Boot CA"
CERT_DAYS="730"
CERT_SERIAL="2"   # incremented from C6's serial=01 to make this cert distinct in audit logs

MGMT_KEY_PATH="/dev/shm/piv-mgmt.txt"

CSR_PATH="/tmp/c6r2-csr.pem"
PUBKEY_PEM_PATH="/tmp/c6r2-pubkey.pem"
PUBKEY_DER_PATH="/tmp/c6r2-pubkey.der"
CERT_PEM_PATH="/tmp/c6r2-cert.pem"
CERT_DER_PATH="/tmp/c6r2-cert.der"
ON_CARD_CERT_PATH="/tmp/c6r2-on-card.der"
CERT_PUBKEY_PEM="/tmp/c6r2-cert-pubkey.pem"
CURRENT_CERT_PATH="/tmp/c6r2-current-cert.der"
VERIFY_PLAINTEXT="/tmp/c6r2-verify-test.bin"
VERIFY_SIG="/tmp/c6r2-verify-test.sig"
VERIFY_PLAINTEXT_POST="/tmp/c6r2-verify-test-post.bin"
VERIFY_SIG_POST="/tmp/c6r2-verify-test-post.sig"
CERT_PUBKEY_POST_PEM="/tmp/c6r2-cert-pubkey-post.pem"

EXPECTED_BROKEN_MODULUS="AFADB330039DBA9084248D54A8483AF4EFA494A682D7C80D3D104FDC2C09D3689BF111EE9E19310BEC067F24BD40BA1A9CFE0763B43B7A98E56F13A8BC1D178E21D19CE226089CDC022A59D5D53A1D3141215E436D2809BF00A3F05EAFBBC70E153EC772DED34FDC7409CB6B46861292C3AB127A2F325601DA0A5CDFCE64042109626DA48C87892EBFA4C4025D57710CFAF6B876BA187D16860642CF259AD4D77F71D0F693E9C8F355693520B0EB3046D976E9116C18A684A2AE22BF6C860049BB866BCF3AA4D5480AC462658608DCFA4400EBAD641B44A9181374CCB70E7D06B5CA11CC2410F081FEF9A011109777766F429BD65B8403408A6CA8EE31517A89"
# NOTE: Original C6 broken modulus. Used as a sentinel that WON'T match the
# current card state (K'' RSA-4096) — this is intentional so that resume
# branch fires (CSR file has K'' modulus, matches current card cert K'').
# Resume mode skips nitropy generate-key and uses existing slot 9c K'' to
# build + install our final cert via the patched OpenSC 0.27.1.

REPO_CERT_PEM="/mnt/intergenos/docker/shim-build/vendor-cert/intergenos-secure-boot-ca.pem"
REPO_CERT_DER="/mnt/intergenos/docker/shim-build/vendor-cert/intergenos-secure-boot-ca.der"
SIGNING_KEY_MD="/mnt/intergenos/docs/signing-key.md"

# ============================================================
# HELPERS
# ============================================================
die() { echo "FATAL: $*" >&2; exit 1; }
info() { echo "[*] $*"; }
ok() { echo "[OK] $*"; }
banner() { echo; echo "============================================================"; echo "  $*"; echo "============================================================"; }

# Trap cleanup: shred mgmt key file ONLY on full success.
# On failure (early exit, ctrl-C, etc.), leave the file in /dev/shm so the
# operator can retry without re-typing the key. Shell vars (MK, PIN) are
# always cleared.
SUCCESS=0
cleanup() {
    if [[ "${SUCCESS:-0}" == "1" ]]; then
        if [[ -f "$MGMT_KEY_PATH" ]]; then
            shred -u "$MGMT_KEY_PATH" 2>/dev/null || rm -f "$MGMT_KEY_PATH" 2>/dev/null || true
        fi
    else
        if [[ -f "$MGMT_KEY_PATH" ]]; then
            echo
            echo "[!] Mgmt key file LEFT at $MGMT_KEY_PATH (script did not complete fully)"
            echo "[!] Re-run will reuse it. To shred manually: shred -u $MGMT_KEY_PATH"
        fi
    fi
    unset MK 2>/dev/null || true
    unset PIN 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ============================================================
# PRE-FLIGHT
# ============================================================
banner "Pre-flight checks"

for tool in nitropy openssl pkcs11-tool shred python3; do
    command -v "$tool" >/dev/null 2>&1 || die "Required tool not found: $tool"
done
ok "Tools present: nitropy openssl pkcs11-tool shred python3"

[[ -f "$PKCS11_MOD" ]] || die "OpenSC PKCS#11 module not found at $PKCS11_MOD"
ok "OpenSC PKCS#11 module present"

python3 -c "import pexpect, cryptography" 2>/dev/null || die "Python deps missing: pexpect, cryptography"
ok "Python deps present (pexpect, cryptography)"

lsusb | grep -q "Clay Logic Nitrokey 3" || die "Nitrokey 3 not detected on USB bus"
ok "Nitrokey 3 detected on USB"

# Auto-detect state: fresh run (cert is OLD broken AFADB330...) vs resume
# (cert is nitropy-placeholder K' from a prior partial run). Resume skips
# nitropy generate-key but does everything else.
info "Reading current slot 9c cert (state detection)..."
pkcs11-tool --module "$PKCS11_MOD" --read-object --type cert --id 02 \
    -o "$CURRENT_CERT_PATH" >/dev/null 2>&1 || die "Could not read cert from slot 9c"

CURRENT_MODULUS=$(openssl x509 -inform DER -in "$CURRENT_CERT_PATH" -noout -modulus 2>/dev/null | sed 's/Modulus=//')
STATE=""

if [[ "$CURRENT_MODULUS" == "$EXPECTED_BROKEN_MODULUS" ]]; then
    # RSA-4096 upgrade path: current cert is the RSA-2048 reload cert (K').
    # It IS self-consistent (passes -check_ss_sig) — that's expected — but we
    # want to upgrade to RSA-4096 to match the OpenPGP master + Holy Grail
    # alignment. The -check_ss_sig guard is skipped here because the cert
    # being replaced is intentionally a valid one, not a broken one.
    STATE="fresh"
    rm -f "$CSR_PATH" "$PUBKEY_PEM_PATH" "$PUBKEY_DER_PATH" "$CERT_PEM_PATH" "$CERT_DER_PATH" 2>/dev/null
    ok "STATE=fresh: slot 9c has the RSA-2048 reload cert (K'); upgrading to RSA-4096"
elif [[ -s "$CSR_PATH" ]]; then
    # Resume scenario: cert on card != broken modulus; check if it matches a prior CSR's K'
    # Tolerate DER or PEM for the CSR (nitropy outputs DER despite .pem ext)
    CSR_INFORM=""
    if ! openssl req -in "$CSR_PATH" -noout >/dev/null 2>&1; then
        CSR_INFORM="-inform DER"
    fi
    CSR_MODULUS=$(openssl req -in "$CSR_PATH" $CSR_INFORM -noout -modulus 2>/dev/null | sed 's/Modulus=//')
    if [[ -n "$CSR_MODULUS" && "$CSR_MODULUS" == "$CURRENT_MODULUS" ]]; then
        STATE="resume"
        ok "STATE=resume: slot 9c cert matches CSR pubkey K'=${CURRENT_MODULUS:0:16}..."
        ok "Will skip nitropy generate-key (already completed); replace placeholder cert with proper one"
    else
        die "Slot 9c cert modulus (${CURRENT_MODULUS:0:16}...) is neither the expected broken modulus NOR matches CSR at $CSR_PATH. Aborting to avoid touching the wrong card."
    fi
else
    die "Slot 9c cert modulus (${CURRENT_MODULUS:0:16}...) is unrecognized and no CSR at $CSR_PATH for resume detection. Aborting."
fi

# ============================================================
# READ MGMT KEY FROM /dev/shm
# ============================================================
banner "Read PIV admin mgmt key from $MGMT_KEY_PATH"

[[ -f "$MGMT_KEY_PATH" ]] || die "Mgmt key file not found at $MGMT_KEY_PATH — write the 64-char hex value (with or without colons) to that path before running this script."

MK_RAW=$(cat "$MGMT_KEY_PATH")
MK=$(echo -n "$MK_RAW" | tr -d ':[:space:]' | tr '[:lower:]' '[:upper:]')

[[ ${#MK} -eq 64 ]] || die "Mgmt key must be 64 hex chars (AES-256); got ${#MK} after normalization"
[[ "$MK" =~ ^[0-9A-F]{64}$ ]] || die "Mgmt key must contain only hex digits after normalization"
ok "Mgmt key parsed: 64 hex chars, first 8 visible for paper-check: ${MK:0:8}..."
info "Mgmt key file held at $MGMT_KEY_PATH; will be shredded ONLY on full script success."

# ============================================================
# PROMPT FOR USER PIN
# ============================================================
banner "PIV User PIN"
read -s -p "PIV User PIN: " PIN; echo
[[ ${#PIN} -ge 6 && ${#PIN} -le 8 ]] || die "PIN length looks wrong (got ${#PIN} chars, expected 6-8)"
ok "PIN captured"

# ============================================================
# FINAL CONFIRMATION (destructive operation)
# ============================================================
banner "FINAL CONFIRMATION — STATE=$STATE"
if [[ "$STATE" == "fresh" ]]; then
cat <<EOF

This script will:
  1. GENERATE a NEW RSA-3072 keypair on NK#1 PIV slot 9c
     — DESTROYS the existing K'' RSA-4096 keypair on that slot (irrecoverable)
     — RSA-3072 = NIST SP 800-57 current recommendation (128-bit security)
     — OpenSC PIV mechanism table caps at 3072 (even in 0.27.1); 4096 not usable
  2. SELF-SIGN a NEW vendor cert via asn1crypto + pkcs11-tool path
     subject="$SUBJ_CN", validity=$CERT_DAYS days, serial=$CERT_SERIAL, RSA-3072
  3. INSTALL the new cert on slot 9c
  4. VERIFY end-to-end + STAGE outputs in /tmp — does NOT touch git repo
EOF
else
cat <<EOF

RESUME mode detected — slot 9c keypair K'' (RSA-4096) already in place from
prior run. Patched OpenSC 0.27.1 at /usr/local/ now supports RSA-4096 PIV sign.

This script will:
  1. SKIP nitropy generate-key (already complete; CSR with K'' at $CSR_PATH)
  2. BUILD a NEW vendor cert via asn1crypto + pkcs11-tool --sign (SHA256-RSA-PKCS,
     RSA-4096, against slot 9c K''). subject="$SUBJ_CN", $CERT_DAYS days, serial=$CERT_SERIAL
  3. INSTALL the new cert on slot 9c — REPLACES the nitropy placeholder cert
     (placeholder has 99-year validity + random serial — bad for EFI root CA)
  4. VERIFY end-to-end + STAGE outputs in /tmp — does NOT touch git repo
EOF
fi
cat <<EOF

NOT touched: OpenPGP applet, Drive #3, paperkeys, NK#2/#3/#4, Ethan-pack, repo.

Type 'reload slot 9c' to proceed (anything else aborts):
EOF
read -r CONFIRM
[[ "$CONFIRM" == "reload slot 9c" ]] || die "Operator did not confirm. Aborting."

# ============================================================
# STEP 1: REGENERATE KEYPAIR ON SLOT 9c → CSR (skipped on resume)
# ============================================================
banner "Step 1: nitropy generate-key on slot 9c → CSR (new keypair K')"

if [[ "$STATE" == "fresh" ]]; then
    # Best-effort pcscd refresh
    sudo -n systemctl restart pcscd 2>/dev/null || \
        info "(pcscd restart skipped — sudo may be required; continuing)"
    gpgconf --kill scdaemon 2>/dev/null || true
    sleep 1

    info "Running nitropy generate-key..."
    nitropy nk3 piv --experimental generate-key \
        --admin-key "$MK" \
        --key 9c \
        --algo rsa3072 \
        --subject-name "CN=$SUBJ_CN" \
        --pin "$PIN" \
        --path "$CSR_PATH"

    [[ -s "$CSR_PATH" ]] || die "nitropy did not produce a CSR at $CSR_PATH"
    ok "CSR produced ($(stat -c %s "$CSR_PATH") bytes)"
else
    info "RESUME: skipping nitropy generate-key (CSR already exists at $CSR_PATH)"
fi

# nitropy outputs DER despite .pem extension — convert to PEM so downstream
# openssl calls (which default to PEM) work without per-call -inform flags.
if ! openssl req -in "$CSR_PATH" -noout >/dev/null 2>&1; then
    info "CSR is DER-encoded; converting to PEM in place..."
    openssl req -in "$CSR_PATH" -inform DER -outform PEM -out "${CSR_PATH}.tmp"
    mv "${CSR_PATH}.tmp" "$CSR_PATH"
    ok "CSR converted to PEM"
fi

# Extract pubkey from CSR — this is K', the actual new on-card keypair pubkey
openssl req -in "$CSR_PATH" -pubkey -noout > "$PUBKEY_PEM_PATH"
openssl pkey -in "$PUBKEY_PEM_PATH" -pubin -outform DER -out "$PUBKEY_DER_PATH"

NEW_MODULUS=$(openssl req -in "$CSR_PATH" -noout -modulus 2>/dev/null | sed 's/Modulus=//')
info "New keypair K' modulus: ${NEW_MODULUS:0:32}..."

[[ "$NEW_MODULUS" != "$EXPECTED_BROKEN_MODULUS" ]] \
    || die "FATAL: New keypair modulus matches old broken modulus. Should be cryptographically impossible. Aborting."
ok "K' differs from broken modulus (sanity check passed)"

# CSR self-validity check — proves nitropy gave us a well-formed CSR
openssl req -in "$CSR_PATH" -verify -noout >/dev/null 2>&1 \
    || die "CSR self-signature does not verify. nitropy output is malformed."
ok "CSR self-signature verifies (CSR is internally consistent)"

# ============================================================
# STEP 2: BUILD CERT (asn1crypto TBS + pkcs11-tool sign) — bypasses
# pkcs11-provider 0.3's CSR-verify-precedence bug entirely
# ============================================================
banner "Step 2: Build cert via asn1crypto TBS + pkcs11-tool --sign"

# OpenSC PIV PKCS#11 state initialization (ceremony.py:847-858 found necessary)
info "Initializing OpenSC PIV PKCS#11 state..."
pkcs11-tool --module "$PKCS11_MOD" -L >/dev/null 2>&1 || true
pkcs11-tool --module "$PKCS11_MOD" -O >/dev/null 2>&1 || true
pkcs11-tool --module "$PKCS11_MOD" --list-objects --type privkey >/dev/null 2>&1 || true
ok "PKCS#11 state initialized"

info "Building TBSCertificate, signing TBS via pkcs11-tool, assembling cert..."

PIN_FOR_PKCS11="$PIN" \
PKCS11_MOD="$PKCS11_MOD" \
PUBKEY_DER_PATH="$PUBKEY_DER_PATH" \
CERT_PEM_PATH="$CERT_PEM_PATH" \
CERT_DER_PATH="$CERT_DER_PATH" \
SUBJ_CN="$SUBJ_CN" \
CERT_DAYS="$CERT_DAYS" \
CERT_SERIAL="$CERT_SERIAL" \
python3 <<'PYEOF'
import os, sys, subprocess, base64
from datetime import datetime, timedelta, timezone
from asn1crypto import x509, keys, algos, core

# Load K' pubkey (DER)
with open(os.environ["PUBKEY_DER_PATH"], "rb") as f:
    pubkey_der = f.read()
pubkey_info = keys.PublicKeyInfo.load(pubkey_der)

# Build Name (issuer == subject, self-signed)
cn = x509.Name.build({"common_name": os.environ["SUBJ_CN"]})

# Validity — UTCTime for years < 2050 (X.509 convention)
not_before = datetime.now(timezone.utc).replace(microsecond=0)
not_after = not_before + timedelta(days=int(os.environ["CERT_DAYS"]))

tbs = x509.TbsCertificate({
    "version": "v3",
    "serial_number": int(os.environ["CERT_SERIAL"]),
    "signature": algos.SignedDigestAlgorithm({"algorithm": "sha256_rsa"}),
    "issuer": cn,
    "validity": x509.Validity({
        "not_before": x509.Time({"utc_time": not_before}),
        "not_after": x509.Time({"utc_time": not_after}),
    }),
    "subject": cn,
    "subject_public_key_info": pubkey_info,
    # Standard v3 extensions for a CA cert (SubjectKeyIdentifier omitted — not
    # required for self-signed CA validity; can be added post-issue if shim-review needs it)
    "extensions": [
        {
            "extn_id": "basic_constraints",
            "critical": True,
            "extn_value": x509.BasicConstraints({"ca": True, "path_len_constraint": None}),
        },
        {
            "extn_id": "key_usage",
            "critical": True,
            "extn_value": x509.KeyUsage({"key_cert_sign", "crl_sign", "digital_signature"}),
        },
    ],
})

tbs_bytes = tbs.dump()
tbs_path = "/tmp/c6r2-tbs.bin"
sig_path = "/tmp/c6r2-tbs.sig"
with open(tbs_path, "wb") as f:
    f.write(tbs_bytes)

# Sign TBS via pkcs11-tool with SHA256-RSA-PKCS mechanism
# (hashes input with SHA-256, wraps in DigestInfo, PKCS#1 v1.5 pads, signs)
cmd = [
    "pkcs11-tool",
    "--module", os.environ["PKCS11_MOD"],
    "--sign",
    "--mechanism", "SHA256-RSA-PKCS",
    "--id", "02",
    "--login",
    "--pin", os.environ["PIN_FOR_PKCS11"],
    "--input-file", tbs_path,
    "--output-file", sig_path,
]
result = subprocess.run(cmd, capture_output=True, text=True)
if result.returncode != 0:
    print("pkcs11-tool stdout:", result.stdout, file=sys.stderr)
    print("pkcs11-tool stderr:", result.stderr, file=sys.stderr)
    sys.exit(f"pkcs11-tool sign failed (rc={result.returncode})")

with open(sig_path, "rb") as f:
    sig_bytes = f.read()

if len(sig_bytes) not in (256, 384, 512):
    sys.exit(f"Signature length unexpected: {len(sig_bytes)} bytes (expected 256/RSA-2048, 384/RSA-3072, or 512/RSA-4096)")

# Assemble cert: TBS + signature_algorithm + signature_value (BIT STRING)
cert = x509.Certificate({
    "tbs_certificate": tbs,
    "signature_algorithm": algos.SignedDigestAlgorithm({"algorithm": "sha256_rsa"}),
    "signature_value": sig_bytes,
})

# Output DER
cert_der = cert.dump()
with open(os.environ["CERT_DER_PATH"], "wb") as f:
    f.write(cert_der)

# Output PEM
pem_lines = ["-----BEGIN CERTIFICATE-----"]
b64 = base64.b64encode(cert_der).decode("ascii")
for i in range(0, len(b64), 64):
    pem_lines.append(b64[i:i+64])
pem_lines.append("-----END CERTIFICATE-----")
with open(os.environ["CERT_PEM_PATH"], "w") as f:
    f.write("\n".join(pem_lines) + "\n")

print(f"    Built cert: DER={len(cert_der)} bytes, sig={len(sig_bytes)} bytes")
PYEOF

[[ -s "$CERT_PEM_PATH" ]] || die "cert build did not produce $CERT_PEM_PATH"
[[ -s "$CERT_DER_PATH" ]] || die "cert build did not produce $CERT_DER_PATH"
ok "Cert built and saved (PEM: $CERT_PEM_PATH, DER: $CERT_DER_PATH)"

# ============================================================
# STEP 3: VERIFICATION GATES (3 layers — ALL must pass)
# ============================================================
banner "Step 3: Verification gates"

# Gate 1: openssl verify with -check_ss_sig
info "Gate 1: openssl verify -check_ss_sig..."
openssl verify -check_ss_sig -CAfile "$CERT_PEM_PATH" "$CERT_PEM_PATH" \
    || die "GATE 1 FAILED: cert does not self-verify under -check_ss_sig"
ok "GATE 1 PASSED"

# Gate 2: cryptography library
info "Gate 2: cryptography.verify_directly_issued_by..."
CERT_PEM_PATH="$CERT_PEM_PATH" python3 <<'PYEOF'
import os, sys
from cryptography import x509
from cryptography.exceptions import InvalidSignature
try:
    with open(os.environ["CERT_PEM_PATH"], "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    cert.verify_directly_issued_by(cert)
    print("    cryptography: cert is self-consistent")
except InvalidSignature as e:
    sys.exit(f"    GATE 2 FAILED: InvalidSignature: {e}")
except Exception as e:
    sys.exit(f"    GATE 2 FAILED: {e}")
PYEOF
ok "GATE 2 PASSED"

# Gate 3: cert subject pubkey modulus == CSR (K') modulus
CERT_MODULUS=$(openssl x509 -in "$CERT_PEM_PATH" -noout -modulus | sed 's/Modulus=//')
[[ "$CERT_MODULUS" == "$NEW_MODULUS" ]] \
    || die "GATE 3 FAILED: cert modulus != CSR modulus. openssl x509 -req misbehaved."
ok "GATE 3 PASSED: cert subject pubkey == CSR pubkey K'"

# ============================================================
# STEP 4: SIGN-AND-VERIFY ROUNDTRIP
# ============================================================
banner "Step 4: Sign-and-verify roundtrip (proves keypair on slot 9c matches cert)"

echo "InterGenOS pre-install roundtrip - $(date -u +%FT%TZ)" > "$VERIFY_PLAINTEXT"

info "Signing test plaintext via slot 9c privkey (pkcs11-tool path, same as step 2)..."
PIN_FOR_PKCS11="$PIN" PKCS11_MOD="$PKCS11_MOD" \
VERIFY_PLAINTEXT="$VERIFY_PLAINTEXT" VERIFY_SIG="$VERIFY_SIG" \
python3 <<'PYEOF'
import os, sys, subprocess
cmd = [
    "pkcs11-tool", "--module", os.environ["PKCS11_MOD"],
    "--sign", "--mechanism", "SHA256-RSA-PKCS",
    "--id", "02", "--login", "--pin", os.environ["PIN_FOR_PKCS11"],
    "--input-file", os.environ["VERIFY_PLAINTEXT"],
    "--output-file", os.environ["VERIFY_SIG"],
]
r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode != 0 or not os.path.exists(os.environ["VERIFY_SIG"]) or os.path.getsize(os.environ["VERIFY_SIG"]) == 0:
    print("pkcs11-tool stderr:", r.stderr, file=sys.stderr)
    sys.exit(f"pkcs11-tool sign failed (rc={r.returncode})")
PYEOF

[[ -s "$VERIFY_SIG" ]] || die "GATE 4a FAILED: sign produced no signature"
ok "Signature produced ($(stat -c %s "$VERIFY_SIG") bytes)"

openssl x509 -in "$CERT_PEM_PATH" -pubkey -noout > "$CERT_PUBKEY_PEM"
openssl dgst -sha256 -verify "$CERT_PUBKEY_PEM" -signature "$VERIFY_SIG" "$VERIFY_PLAINTEXT" \
    || die "GATE 4b FAILED: signature does not verify against cert pubkey. Bug NOT fixed."
ok "GATE 4 PASSED: sign-and-verify roundtrip succeeded"

# ============================================================
# STEP 5: INSTALL CERT ON SLOT 9c
# ============================================================
banner "Step 5: Install new cert on slot 9c via nitropy write-certificate"

nitropy nk3 piv --experimental write-certificate \
    --format der \
    --key 9c \
    --path "$CERT_DER_PATH" \
    "$MK"

ok "Cert install command completed"

# ============================================================
# STEP 6: POST-INSTALL VERIFICATION
# ============================================================
banner "Step 6: Post-install verification"

# Read cert back from card and compare byte-for-byte
pkcs11-tool --module "$PKCS11_MOD" --read-object --type cert --id 02 \
    -o "$ON_CARD_CERT_PATH" >/dev/null 2>&1 \
    || die "Could not read cert back from card post-install"

diff -q "$CERT_DER_PATH" "$ON_CARD_CERT_PATH" >/dev/null \
    || die "POST-INSTALL FAIL: cert on card differs from what we sent"
ok "Cert on card matches what was installed (byte-for-byte)"

# Final roundtrip: sign new plaintext, verify against cert READ FROM the card
echo "InterGenOS post-install roundtrip - $(date -u +%FT%TZ)" > "$VERIFY_PLAINTEXT_POST"

PIN_FOR_PKCS11="$PIN" PKCS11_MOD="$PKCS11_MOD" \
VP="$VERIFY_PLAINTEXT_POST" VS="$VERIFY_SIG_POST" \
python3 <<'PYEOF'
import os, sys, subprocess
cmd = [
    "pkcs11-tool", "--module", os.environ["PKCS11_MOD"],
    "--sign", "--mechanism", "SHA256-RSA-PKCS",
    "--id", "02", "--login", "--pin", os.environ["PIN_FOR_PKCS11"],
    "--input-file", os.environ["VP"],
    "--output-file", os.environ["VS"],
]
r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode != 0 or not os.path.exists(os.environ["VS"]) or os.path.getsize(os.environ["VS"]) == 0:
    print("pkcs11-tool stderr:", r.stderr, file=sys.stderr)
    sys.exit(f"post-install pkcs11-tool sign failed (rc={r.returncode})")
PYEOF

openssl x509 -inform DER -in "$ON_CARD_CERT_PATH" -pubkey -noout > "$CERT_PUBKEY_POST_PEM"
openssl dgst -sha256 -verify "$CERT_PUBKEY_POST_PEM" -signature "$VERIFY_SIG_POST" "$VERIFY_PLAINTEXT_POST" \
    || die "POST-INSTALL ROUNDTRIP FAILED — investigate before proceeding"
ok "POST-INSTALL ROUNDTRIP PASSED"

# ============================================================
# STEP 7: SUMMARY + MANUAL COMMIT INSTRUCTIONS
# ============================================================
banner "Reload complete — verification gates ALL passed"

NEW_CERT_PEM_SHA=$(sha256sum "$CERT_PEM_PATH" | awk '{print $1}')
NEW_CERT_DER_SHA=$(sha256sum "$CERT_DER_PATH" | awk '{print $1}')
NEW_CERT_FP=$(openssl x509 -in "$CERT_PEM_PATH" -noout -fingerprint -sha256 | sed 's/.*=//')

cat <<EOF

================================================================
NEW EFI VENDOR CERT
  PEM:                $CERT_PEM_PATH
  DER:                $CERT_DER_PATH
  PEM SHA-256:        $NEW_CERT_PEM_SHA
  DER SHA-256:        $NEW_CERT_DER_SHA
  Cert fingerprint:   $NEW_CERT_FP
  Subject:            CN=$SUBJ_CN
  Validity:           $CERT_DAYS days from $(date -u +%FT%TZ)
  Serial:             $CERT_SERIAL

NEW KEYPAIR PUBKEY WITNESS (commit for future audit)
  $PUBKEY_DER_PATH
  $PUBKEY_PEM_PATH
  Modulus first 32:   ${NEW_MODULUS:0:32}...

TO COMMIT (manual operator steps — script will NOT auto-commit):

  cp $CERT_PEM_PATH $REPO_CERT_PEM
  cp $CERT_DER_PATH $REPO_CERT_DER

  # Edit $SIGNING_KEY_MD to update the EFI cert SHA row:
  #   OLD PEM SHA:  8ce749e7e77169205e4761d82b48a4333f48cdec2ee0f711b8cff560fe150514
  #   NEW PEM SHA:  $NEW_CERT_PEM_SHA
  #   OLD FP:       7B:8F:21:50:B5:D0:0C:7B:28:DD:51:8F:AD:D7:0B:C0:E8:37:AE:43:DF:7B:5E:23:D6:18:5E:9C:75:30:C8:76
  #   NEW FP:       $NEW_CERT_FP

  # Optionally also commit pubkey witness:
  cp $PUBKEY_DER_PATH /mnt/intergenos/docker/shim-build/vendor-cert/intergenos-secure-boot-ca-pubkey.der

  cd /mnt/intergenos
  git add docker/shim-build/vendor-cert/ docs/signing-key.md
  git commit -m "fix(shim): replace EFI vendor cert with self-consistent cert (C6 reload)"
  git push

POST-COMMIT:
  1. Rebuild shim against new cert (docker/shim-build/ rebuild)
  2. Re-stage v1-bootloader-sign-bundle with new cert (URI unchanged)
  3. Sign bootloader artifacts via working slot 9c chain
EOF

ok "Script complete. NO git-repo changes made. Review outputs above."
SUCCESS=1
exit 0
