#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# sign-destructive-policy-manifest.sh — operator-driven, ONE-COMMAND signing
# ceremony for the destructive-policy critical-essentials never-list manifest.
#
# Rule F (build-rules §1) — signing-ceremony commands MUST be pain-free for the
# operator. The whole interaction is meant to be exactly:
#
#     bash scripts/sign-destructive-policy-manifest.sh
#     <enter Nitrokey OpenPGP User PIN at the pinentry prompt + touch the key>
#     done
#
# This is a THIN wrapper: it locates the in-tree manifest, refuses to sign a
# malformed/empty one (the whole point of a pre-sign gate), then delegates the
# actual signing + verification to the canonical primitive scripts/sign-with-gpg.sh
# (hardware-rooted master key, smartcard setup via lib-gpg-card-setup.sh,
# gpg --detach-sign --armor -> <file>.asc, self-verifies against the master
# fingerprint). We do NOT reimplement the signing/verify logic — that machinery
# is already perfected and shared with the archive-manifest + model-manifest
# ceremonies.
#
# Output: intergen/data/destructive-policy-manifest.json.asc (detached, armored),
# verified, ready to `git add` + ship to /usr/share/intergen/ (dm-verity ro) via
# the intergen package build.
#
# Optional: pass a manifest path as $1 to sign a different file (default is the
# in-tree manifest). No other args — pain-free by design.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MANIFEST="${1:-${REPO_ROOT}/intergen/data/destructive-policy-manifest.json}"
SIGNER="${SCRIPT_DIR}/sign-with-gpg.sh"

# Shared build-output library — for the ✓/✗/⚠ markers + TTY-aware color. The
# local ceremony helpers below are kept but re-voiced to the house style.
# shellcheck source=lib/logging.sh
[ -f "$(dirname "$0")/lib/logging.sh" ] && source "$(dirname "$0")/lib/logging.sh"

die()    { echo "error: $*" >&2; exit 1; }
ok()     { echo "${IGOS_MARK_OK:-✓} $*"; }
banner() { echo; echo ">>> $*"; }

# ============================================================
# PRE-FLIGHT
# ============================================================
banner "Pre-flight — destructive-policy never-list manifest"

for tool in python3 sha256sum gpg; do
    command -v "$tool" >/dev/null 2>&1 || die "Required tool not found: $tool"
done
ok "Tools present: python3 sha256sum gpg"

[[ -f "$SIGNER" ]] || die "Canonical signer missing at $SIGNER (expected sibling of this script)"
ok "Canonical signer present: $SIGNER"

[[ -f "$MANIFEST" ]] || die "Manifest not found: $MANIFEST"
ok "Manifest present: $MANIFEST ($(stat -c %s "$MANIFEST") bytes)"

# ============================================================
# SANITY GATE — refuse to sign a malformed/empty never-list
# ============================================================
banner "Sanity gate — refuse to sign garbage"

# Structural assertions over the JSON: valid JSON, the expected manifest type +
# version, and at least one non-empty protected category. Signing a malformed
# never-list would cryptographically bless a policy that protects nothing.
python3 - "$MANIFEST" <<'PYEOF' || die "Manifest failed the sanity gate — refusing to sign (see errors above)"
import json, sys
p = sys.argv[1]
try:
    with open(p) as f:
        m = json.load(f)
except Exception as e:
    print(f"  - not valid JSON: {e}", file=sys.stderr); sys.exit(1)

errs = []
if m.get("manifest_version") != 1:
    errs.append(f"manifest_version != 1 (got {m.get('manifest_version')!r})")
if m.get("manifest_type") != "intergen-destructive-policy-critical-essentials":
    errs.append(f"unexpected manifest_type {m.get('manifest_type')!r}")
cats = m.get("categories")
if not isinstance(cats, dict) or not cats:
    errs.append("categories missing or empty")
else:
    total_paths = 0
    for name, c in cats.items():
        for key in ("exact", "prefix", "glob"):
            total_paths += len(c.get(key, []) or [])
    if total_paths == 0:
        errs.append("categories contain ZERO protected paths (a never-list that protects nothing)")
    else:
        print(f"  categories={len(cats)} protected_paths={total_paths}")
mr = m.get("match_rules", {})
if not mr.get("resolve_symlinks"):
    errs.append("match_rules.resolve_symlinks must be true (symlink-laundering defense)")
if mr.get("default_on_ambiguity") != "block":
    errs.append("match_rules.default_on_ambiguity must be 'block' (default-deny)")

if errs:
    for e in errs:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)
print("  structural checks PASS")
PYEOF
ok "Manifest structurally valid (type + version + non-empty categories + default-deny)"

MANIFEST_SHA="$(sha256sum "$MANIFEST" | awk '{print $1}')"
ok "Manifest sha256: $MANIFEST_SHA"

# ============================================================
# DELEGATE TO THE CANONICAL SIGNER
# ============================================================
banner "Signing via the canonical hardware-rooted primitive (sign-with-gpg.sh)"
cat <<EOF

About to sign the destructive-policy never-list. The signer will prompt for your
Nitrokey OpenPGP User PIN (pinentry) and require an on-card TOUCH — watch the
key's LED. Nothing else is asked of you.

  manifest: $MANIFEST
  output:   ${MANIFEST}.asc  (detached, ASCII-armored; self-verified)

EOF

# Pass the pre-computed sha256 so the primitive does a byte-fidelity check on the
# exact bytes we gated. The primitive handles card setup, sign, and verify.
exec bash "$SIGNER" --file "$MANIFEST" --sha256 "$MANIFEST_SHA"
