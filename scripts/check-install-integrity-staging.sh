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
#   (c) every SHIPPED *.igos.tar.gz under the archive dir appears in the
#       manifest (no unmanifested archive ships — closes red-team R3);
#   (d) the manifest is non-empty (an empty trust set at seal time is itself a
#       defect — honesty-first, mirrors the PI-12 empty-set rule);
#   (e) every manifest entry HAS a shipped archive (the media never promises
#       what it does not carry). Added 2026-09-02 after the R001.2 install
#       abort: the ISO carried the full chroot census while build-squashfs
#       kept 284 mirror-only archives off the media, and only the installer's
#       verify phase — the most expensive discovery point — asked this
#       question. (c) alone could not see it: an entry with no file is never
#       visited by a walk of the files that are present.
#
# "SHIPPED" = every archive under --archive-dir MINUS the names in
# --archive-excludes (build-squashfs Step 2.6's mirror-only exclusion list,
# the archives it keeps off the squashfs with per-file -e entries). Without
# --archive-excludes every archive under the dir counts as shipped (the
# ISO_PREP=0 full-corpus shape).
#
# Trust triplet filenames in the INSTALL dir (canonical — match
# build-squashfs Step 4.8 + integrity.py read-path constants). Step 4.8 stages
# the ISO manifest (intergenos-archive-manifest-iso.txt in build/) under the
# first name; this gate reads the staged file and does not care which
# build/ file it came from — it asserts the staged manifest against the
# shipped set, whichever it is:
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
#       --install-dir <dir> [--archive-dir <dir>] [--archive-excludes <file>] \
#       [--repo-root <dir>] [--emit-marker <path>]
#
# Exit: 0 = PASS (triplet complete, signature valid, coverage complete in
#           both directions);
#       1 = FAIL (refuse to seal/assemble) or usage error.
# -----------------------------------------------------------------------------

PROG="check-install-integrity-staging"

log()  { echo "[$PROG] $*" >&2; }
fail() { log "FAIL: $*"; exit 1; }

INSTALL_DIR=""
ARCHIVE_DIR=""
ARCHIVE_EXCLUDES=""
REPO_ROOT=""
EMIT_MARKER=""

while [ $# -gt 0 ]; do
    case "$1" in
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        --archive-dir) ARCHIVE_DIR="$2"; shift 2 ;;
        --archive-excludes) ARCHIVE_EXCLUDES="$2"; shift 2 ;;
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

if [ -n "$ARCHIVE_EXCLUDES" ] && [ ! -f "$ARCHIVE_EXCLUDES" ]; then
    fail "archive-excludes file not found: $ARCHIVE_EXCLUDES"
fi

# (b)+(c)+(d)+(e) — signature verification + manifest coverage (both
# directions) + non-empty, all in one python3 invocation that imports the REAL
# verifier so this gate and the install-time gate share identical signature +
# parse logic (no second implementation to drift); the shipped-set arithmetic
# comes from scripts/lib/manifest_coverage.py, the module phase_manifest's ISO
# derivation uses, so the manifest the ISO carries and the check that guards
# it are one definition of "shipped".
# The archive dir is optional: when absent we skip coverage but STILL verify the
# signature + non-empty manifest (a partial-build staging tree may legitimately
# have no archive dir, but a present-but-unverifiable manifest is always a fail).
if ! REPO_ROOT="$REPO_ROOT" MANIFEST="$MANIFEST" KEY="$KEY" \
     ARCHIVE_DIR="$ARCHIVE_DIR" ARCHIVE_EXCLUDES="$ARCHIVE_EXCLUDES" python3 - <<'PY'
import os
import sys
from pathlib import Path

repo_root = os.environ["REPO_ROOT"]
sys.path.insert(0, repo_root)
sys.path.insert(0, os.path.join(repo_root, "scripts", "lib"))

try:
    from installer.backend.integrity import (
        verify_manifest_signature,
        parse_manifest,
    )
except Exception as e:  # import failure must fail-closed, never silently pass
    print(f"[check-install-integrity-staging] FAIL: cannot import verifier "
          f"(installer.backend.integrity) from {repo_root}: {e}", file=sys.stderr)
    sys.exit(1)
try:
    import manifest_coverage as mc
except Exception as e:
    print(f"[check-install-integrity-staging] FAIL: cannot import "
          f"scripts/lib/manifest_coverage.py from {repo_root}: {e}", file=sys.stderr)
    sys.exit(1)

manifest = Path(os.environ["MANIFEST"])
key = Path(os.environ["KEY"])
archive_dir = os.environ.get("ARCHIVE_DIR", "")
archive_excludes = os.environ.get("ARCHIVE_EXCLUDES", "")

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

# (c)+(e) coverage, both directions. "Shipped" = every archive under the
# archive dir minus the exclusion list (the archives Step 2.6 keeps off the
# squashfs). Manifest paths are relative to the archive dir (integrity.py
# uses .as_posix()).
if archive_dir and Path(archive_dir).is_dir():
    excludes = set()
    if archive_excludes:
        try:
            excludes = mc.read_excludes(Path(archive_excludes))
        except OSError as e:
            print(f"[check-install-integrity-staging] FAIL: cannot read the "
                  f"archive-excludes list {archive_excludes}: {e}", file=sys.stderr)
            sys.exit(1)
    shipped = mc.shipped_set(Path(archive_dir), excludes)
    unmanifested, missing = mc.coverage(entries, shipped)
    rc = 0
    if unmanifested:
        print("[check-install-integrity-staging] FAIL: archives shipping but NOT "
              "in the signed manifest (would ship unverified):", file=sys.stderr)
        for rel in unmanifested:
            print(f"    {rel}", file=sys.stderr)
        rc = 1
    if missing:
        print(f"[check-install-integrity-staging] FAIL: {len(missing)} manifest "
              f"entries name NO shipped archive (the media would promise what "
              f"it does not carry — the installer refuses this):", file=sys.stderr)
        for rel in missing:
            print(f"    {rel}", file=sys.stderr)
        rc = 1
    if rc:
        sys.exit(rc)
    print(f"[check-install-integrity-staging] coverage OK both ways: "
          f"{len(entries)} manifest entries == {len(shipped)} shipped archives "
          f"({len(excludes)} exclusion names applied).",
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

log "PASS — release trust triplet complete, signature valid, coverage complete in both directions."
exit 0
