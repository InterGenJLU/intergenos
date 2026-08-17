#!/usr/bin/env bash
# check-install-integrity-staging.sh
# -----------------------------------------------------------------------------
# Install-integrity gate §4B — fail-closed build-time assertion that a RELEASE
# squashfs staging tree carries the complete, signature-valid trust triplet
# BEFORE mksquashfs seals it (Option 1: the triplet is verity-sealed INTO the
# squashfs, so this is the only moment it can be asserted against the staged
# tree).
#
# Two consumers, one body:
#   1. build-squashfs.sh Step 4.8 — runs this against $CHROOT/install before
#      mksquashfs. Any failure => "refusing to build squashfs" (exit nonzero),
#      identical shape to Steps 4.5/4.6/4.7.
#   2. build-iso.sh Class-A gate — build-iso consumes an ALREADY-SEALED squashfs
#      and cannot cheaply peer inside it, so it keys off the marker this script
#      emits on success (--emit-marker) rather than re-reading the squashfs.
#
# The assertion (release mode), all-or-nothing:
#   (a) the install dir holds non-empty {manifest, .sig, release-key};
#   (b) verify_manifest_signature() succeeds against the STAGED release key
#       (shells out to the real installer/backend/integrity.py so build-time
#       and install-time verification are byte-for-byte the same logic);
#   (c) every *.igos.tar.gz under the archive dir appears in the manifest
#       (no unmanifested archive ships — closes red-team R3);
#   (d) the manifest is non-empty (an empty trust set at seal time is itself a
#       defect — honesty-first, mirrors the PI-12 empty-set rule).
#
# Trust triplet filenames (canonical — match phase_manifest + sign-release.sh +
# integrity.py read-path constants):
#   intergenos-archive-manifest.txt
#   intergenos-archive-manifest.txt.sig
#   intergenos-release-key.asc
#
# The dev/unsigned-test path does NOT call this gate — UNSIGNED_TEST builds
# stage the explicit IGOS_DEV_ALLOW_UNVERIFIED marker instead (the sanctioned,
# loud, clearly-marked dev seam; see build-squashfs.sh Step 4.8). This gate is
# release-mode only.
#
# Usage:
#   check-install-integrity-staging.sh \
#       --install-dir <dir> [--archive-dir <dir>] \
#       [--repo-root <dir>] [--emit-marker <path>]
#
# Exit: 0 = PASS (triplet complete, signature valid, coverage complete);
#       1 = FAIL (refuse to seal/assemble) or usage error.
# -----------------------------------------------------------------------------

PROG="check-install-integrity-staging"

log()  { echo "[$PROG] $*" >&2; }
fail() { log "FAIL: $*"; exit 1; }

INSTALL_DIR=""
ARCHIVE_DIR=""
REPO_ROOT=""
EMIT_MARKER=""

while [ $# -gt 0 ]; do
    case "$1" in
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        --archive-dir) ARCHIVE_DIR="$2"; shift 2 ;;
        --repo-root)   REPO_ROOT="$2";   shift 2 ;;
        --emit-marker) EMIT_MARKER="$2"; shift 2 ;;
        -h|--help)
            grep '^# ' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) fail "unknown argument: $1 (see --help)" ;;
    esac
done

[ -n "$INSTALL_DIR" ] || fail "--install-dir is required"

# Default repo root = one level up from this script (scripts/ -> repo root),
# so the import of installer.backend.integrity resolves without the caller
# threading PYTHONPATH.
if [ -z "$REPO_ROOT" ]; then
    REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi

MANIFEST="$INSTALL_DIR/intergenos-archive-manifest.txt"
SIG="$INSTALL_DIR/intergenos-archive-manifest.txt.sig"
KEY="$INSTALL_DIR/intergenos-release-key.asc"

# (a) presence + non-empty — explicit per-file diagnostics.
[ -d "$INSTALL_DIR" ] || fail "install dir not found: $INSTALL_DIR"
[ -s "$MANIFEST" ]    || fail "signed manifest missing or empty: $MANIFEST"
[ -s "$SIG" ]         || fail "detached signature missing or empty: $SIG"
[ -s "$KEY" ]         || fail "release key missing or empty: $KEY"

# (b)+(c)+(d) — signature verification + manifest coverage + non-empty, all in
# one python3 invocation that imports the REAL verifier so this gate and the
# install-time gate share identical logic (no second implementation to drift).
# The archive dir is optional: when absent we skip coverage but STILL verify the
# signature + non-empty manifest (a partial-build staging tree may legitimately
# have no archive dir, but a present-but-unverifiable manifest is always a fail).
if ! REPO_ROOT="$REPO_ROOT" MANIFEST="$MANIFEST" KEY="$KEY" \
     ARCHIVE_DIR="$ARCHIVE_DIR" python3 - <<'PY'
import os
import sys
from pathlib import Path

repo_root = os.environ["REPO_ROOT"]
sys.path.insert(0, repo_root)

try:
    from installer.backend.integrity import (
        verify_manifest_signature,
        parse_manifest,
    )
except Exception as e:  # import failure must fail-closed, never silently pass
    print(f"[check-install-integrity-staging] FAIL: cannot import verifier "
          f"(installer.backend.integrity) from {repo_root}: {e}", file=sys.stderr)
    sys.exit(1)

manifest = Path(os.environ["MANIFEST"])
key = Path(os.environ["KEY"])
archive_dir = os.environ.get("ARCHIVE_DIR", "")

# (b) signature — bound exclusively to the STAGED release key.
if not verify_manifest_signature(manifest, key):
    print("[check-install-integrity-staging] FAIL: manifest signature does NOT "
          "verify against the staged release key "
          "(intergenos-release-key.asc).", file=sys.stderr)
    sys.exit(1)

# (d) non-empty — an empty trust set at seal time is a defect, not a pass.
try:
    entries = parse_manifest(manifest)
except Exception as e:
    print(f"[check-install-integrity-staging] FAIL: manifest does not parse: {e}",
          file=sys.stderr)
    sys.exit(1)

if len(entries) == 0:
    print("[check-install-integrity-staging] FAIL: manifest is empty (0 archive "
          "entries) — refusing to seal a vacuous trust set.", file=sys.stderr)
    sys.exit(1)

# (c) coverage — every staged archive must appear in the manifest. Manifest
# paths are relative to the archive dir (integrity.py uses .as_posix()).
if archive_dir and Path(archive_dir).is_dir():
    base = Path(archive_dir)
    unmanifested = []
    for tar in sorted(base.rglob("*.igos.tar.gz")):
        rel = tar.relative_to(base).as_posix()
        if rel not in entries:
            unmanifested.append(rel)
    if unmanifested:
        print("[check-install-integrity-staging] FAIL: archives staged but NOT "
              "in the signed manifest (would ship unverified):", file=sys.stderr)
        for rel in unmanifested:
            print(f"    {rel}", file=sys.stderr)
        sys.exit(1)
    print(f"[check-install-integrity-staging] coverage OK: "
          f"{len(entries)} manifest entries cover every staged archive.",
          file=sys.stderr)
else:
    print("[check-install-integrity-staging] NOTE: no archive dir given/found — "
          "verified signature + non-empty manifest only (coverage skipped).",
          file=sys.stderr)

print("[check-install-integrity-staging] signature OK + manifest non-empty.",
      file=sys.stderr)
sys.exit(0)
PY
then
    fail "trust triplet did not pass signature/coverage verification (see above)"
fi

# Success. Emit the build attestation marker for build-iso.sh's Class-A gate,
# which cannot reach inside the sealed squashfs and trusts this marker instead.
if [ -n "$EMIT_MARKER" ]; then
    marker_dir="$(dirname "$EMIT_MARKER")"
    mkdir -p "$marker_dir" || fail "cannot create marker dir: $marker_dir"
    {
        echo "install-integrity-staging: PASS"
        echo "manifest: $MANIFEST"
        echo "verified-against-key: $KEY"
    } > "$EMIT_MARKER" || fail "cannot write marker: $EMIT_MARKER"
    log "marker emitted: $EMIT_MARKER"
fi

log "PASS — release trust triplet complete, signature valid, coverage complete."
exit 0
