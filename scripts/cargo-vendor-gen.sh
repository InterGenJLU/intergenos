#!/bin/bash
# cargo-vendor-gen.sh — Host-side helper to generate reproducible Rust vendor tarballs.
#
# Produces the artifacts declared as `build_artifacts:` in package.yml for any
# Rust package that builds offline in the chroot via the cargo-c / aardvark-dns
# pattern (extract vendored crates at configure time, run `cargo build` with
# vendored-sources replacing crates-io).
#
# USAGE:
#   scripts/cargo-vendor-gen.sh <pkg-name> <version> <source-url-or-path>
#
# OUTPUTS (to ${OUTPUT_DIR:-build/vendor-artifacts/}, absolute path resolved):
#   <pkg-name>-<version>-vendor.tar.xz   wrapper dir containing vendor/ + .cargo/
#   <pkg-name>-<version>-Cargo.lock      the lockfile used during vendoring
#
# CONSUMER PATTERN (package's build.sh configure()):
#   tar xf "${IGOS_SOURCES}/<pkg>-${PKG_VERSION}-vendor.tar.xz" --strip-components=1
#   # vendor/ and .cargo/ now in cwd; if Cargo.lock wasn't in upstream:
#   cp -v "${IGOS_SOURCES}/<pkg>-${PKG_VERSION}-Cargo.lock" Cargo.lock
#   # build phase:
#   cargo build --release --frozen --offline
#
# REPRODUCIBILITY:
#   - Tar uses --sort=name + --owner=0 --group=0 --numeric-owner + fixed --mtime
#   - Xz uses -T 1 (single-thread; multi-thread breaks byte-identity)
#   - cargo vendor --locked --versioned-dirs (refuse if Cargo.lock missing/stale)
#   - Byte-identical across runs when cargo version + crate versions identical
#
# REQUIRES:
#   - cargo (rust toolchain on PATH)
#   - tar, xz, sha256sum, mktemp, curl (curl only when source is a URL)
#   - Network access to crates.io + (when URL) the source URL
#
# DOES NOT REQUIRE: chroot, sudo, build VM. Pure host-side maintainer tool.
#
# See docs/research/build_system/cargo_vendor_helper_v1.md for full design.

set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
OUTPUT_DIR="${OUTPUT_DIR:-build/vendor-artifacts}"
SOURCE_EPOCH="${SOURCE_DATE_EPOCH:-0}"
KEEP_WORK="${KEEP_WORK:-0}"

WORK_DIR=""

usage() {
    cat >&2 <<EOF
Usage: ${SCRIPT_NAME} <pkg-name> <version> <source-url-or-path>

Generates a reproducible vendor tarball + Cargo.lock for a Rust package.
Outputs go to \${OUTPUT_DIR} (default: build/vendor-artifacts/).

Arguments:
  pkg-name             Package name (matches package.yml 'name:' field)
  version              Package version (matches package.yml 'version:' field)
  source-url-or-path   Upstream source tarball URL, OR local path to a .tar.gz / .tar.xz

Environment:
  OUTPUT_DIR           Where to write outputs (default: build/vendor-artifacts/)
  SOURCE_DATE_EPOCH    Mtime for tar entries (default: 0; commit timestamp recommended)
  KEEP_WORK            Set to 1 to retain extracted work tree for inspection

Example:
  ${SCRIPT_NAME} cargo-c 0.10.20 \\
    https://github.com/lu-zero/cargo-c/archive/v0.10.20/cargo-c-0.10.20.tar.gz
EOF
    exit 2
}

log()  { printf '[cargo-vendor-gen] %s\n' "$*" >&2; }
die()  { log "ERROR: $*"; exit 1; }

cleanup() {
    if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
        if [ "$KEEP_WORK" = "1" ]; then
            log "KEEP_WORK=1 — preserving work dir: $WORK_DIR"
        else
            rm -rf "$WORK_DIR"
        fi
    fi
}
trap cleanup EXIT

# ---------- args + preflight ----------
[ "$#" -eq 3 ] || usage
PKG_NAME="$1"
PKG_VERSION="$2"
SOURCE_ARG="$3"

# Validate identifier shape (matches package.yml conventions)
[[ "$PKG_NAME"    =~ ^[a-zA-Z0-9][a-zA-Z0-9._+-]*$ ]] || die "invalid pkg-name: $PKG_NAME"
[[ "$PKG_VERSION" =~ ^[a-zA-Z0-9][a-zA-Z0-9._+-]*$ ]] || die "invalid version: $PKG_VERSION"

command -v cargo     >/dev/null 2>&1 || die "cargo not in PATH (need rust toolchain)"
command -v tar       >/dev/null 2>&1 || die "tar not in PATH"
command -v xz        >/dev/null 2>&1 || die "xz not in PATH"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum not in PATH"
command -v mktemp    >/dev/null 2>&1 || die "mktemp not in PATH"

CARGO_VERSION="$(cargo --version 2>/dev/null | awk '{print $2}')"
log "cargo version: $CARGO_VERSION"

WORK_DIR="$(mktemp -d -t cargo-vendor-gen.XXXXXX)"
log "work dir: $WORK_DIR"
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR_ABS="$(cd "$OUTPUT_DIR" && pwd)"
log "output dir (absolute): $OUTPUT_DIR_ABS"

# ---------- 1. resolve source ----------
SOURCE_TARBALL=""
if [[ "$SOURCE_ARG" =~ ^https?:// ]]; then
    command -v curl >/dev/null 2>&1 || die "curl not in PATH (required for URL source)"
    SOURCE_TARBALL="$WORK_DIR/source.tar.gz"
    log "downloading: $SOURCE_ARG"
    curl --fail --silent --show-error --location \
        --max-time 180 \
        --output "$SOURCE_TARBALL" \
        "$SOURCE_ARG" || die "curl failed for $SOURCE_ARG"
else
    [ -f "$SOURCE_ARG" ] || die "source not a file: $SOURCE_ARG"
    SOURCE_TARBALL="$(cd "$(dirname "$SOURCE_ARG")" && pwd)/$(basename "$SOURCE_ARG")"
    log "using local source: $SOURCE_TARBALL"
fi

SOURCE_SHA="$(sha256sum "$SOURCE_TARBALL" | awk '{print $1}')"
log "source sha256: $SOURCE_SHA"

# ---------- 2. extract source ----------
EXTRACT_DIR="$WORK_DIR/src"
mkdir -p "$EXTRACT_DIR"
log "extracting source into $EXTRACT_DIR"

# tar handles .tar.gz / .tar.xz / .tar.bz2 auto-detect via -a (or by leaving format auto)
tar -xaf "$SOURCE_TARBALL" -C "$EXTRACT_DIR" || die "source tarball extract failed"

# Locate project root (GitHub archive convention: single top-level dir)
mapfile -t TOPLEVEL < <(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 -type d)
if [ "${#TOPLEVEL[@]}" -eq 1 ]; then
    PROJECT_ROOT="${TOPLEVEL[0]}"
else
    PROJECT_ROOT="$EXTRACT_DIR"
fi
log "project root: $PROJECT_ROOT"

[ -f "$PROJECT_ROOT/Cargo.toml" ] || die "no Cargo.toml at project root — not a cargo project"

# ---------- 3. Cargo.lock handling ----------
LOCK_FILE="$PROJECT_ROOT/Cargo.lock"
LOCK_ORIGIN="upstream"
if [ ! -f "$LOCK_FILE" ]; then
    log "Cargo.lock absent from upstream — generating with: cargo generate-lockfile"
    log "  NOTE: generated lockfiles are NON-deterministic (resolves to latest compatible)."
    log "  For reproducible re-generation, pin Cargo.toml dependencies tightly upstream OR"
    log "  preserve this Cargo.lock as a side artifact alongside vendor.tar.xz."
    (cd "$PROJECT_ROOT" && cargo generate-lockfile) >&2 \
        || die "cargo generate-lockfile failed"
    LOCK_ORIGIN="generated"
fi
[ -f "$LOCK_FILE" ] || die "Cargo.lock still missing after generate-lockfile attempt"
LOCK_SHA="$(sha256sum "$LOCK_FILE" | awk '{print $1}')"
log "Cargo.lock origin: $LOCK_ORIGIN  sha256: $LOCK_SHA"

# ---------- 4. cargo vendor ----------
log "running: cargo vendor --locked --versioned-dirs"
# --locked       : refuse to update Cargo.lock; fail if stale
# --versioned-dirs : <crate>-<version>/ dirs (multi-version safe + more reproducible)
# We suppress stdout (the config.toml hint), capture stderr for log.
(cd "$PROJECT_ROOT" && cargo vendor --locked --versioned-dirs >/dev/null) \
    || die "cargo vendor failed (Cargo.lock stale, or crates.io unreachable)"

VENDOR_DIR="$PROJECT_ROOT/vendor"
[ -d "$VENDOR_DIR" ] || die "vendor/ directory not created by cargo vendor"

VENDOR_CRATE_COUNT="$(find "$VENDOR_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)"
log "vendored $VENDOR_CRATE_COUNT crates"

# ---------- 5. write .cargo/config.toml ----------
CARGO_DIR="$PROJECT_ROOT/.cargo"
mkdir -p "$CARGO_DIR"
cat > "$CARGO_DIR/config.toml" <<'EOF'
# Generated by cargo-vendor-gen.sh — extracts into the chroot's package source root
# alongside Cargo.lock and vendor/, redirecting cargo to use vendored crates
# instead of crates.io. Pairs with `cargo build --release --frozen --offline`.

[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
EOF
log "wrote .cargo/config.toml (vendored-sources redirect)"

# ---------- 6. stage wrapper dir ----------
# Archive shape: ${PKG_NAME}-${PKG_VERSION}/{vendor/,.cargo/}
# Consumer build.sh extracts with --strip-components=1 → puts vendor/+.cargo/ in cwd.
STAGE_DIR="$WORK_DIR/stage"
WRAP_NAME="${PKG_NAME}-${PKG_VERSION}"
WRAP_DIR="$STAGE_DIR/$WRAP_NAME"
mkdir -p "$WRAP_DIR"
mv "$VENDOR_DIR" "$WRAP_DIR/vendor"
mv "$CARGO_DIR"  "$WRAP_DIR/.cargo"

# ---------- 7. reproducible tar+xz ----------
OUTPUT_TARBALL="$OUTPUT_DIR_ABS/${PKG_NAME}-${PKG_VERSION}-vendor.tar.xz"
log "building reproducible archive: $OUTPUT_TARBALL"

# Reproducibility flags:
#   --sort=name         deterministic file order
#   --owner=0 --group=0 zero out uid/gid names
#   --numeric-owner     numeric ids (no /etc/passwd lookup)
#   --mtime=@<EPOCH>    fixed mtime
#   --format=ustar      stable archive format
# Xz flags:
#   -T 1                single-thread (multi-thread varies block boundaries)
#   -9                  max compression (deterministic at fixed thread count)
(cd "$STAGE_DIR" && \
    tar --sort=name \
        --owner=0 --group=0 --numeric-owner \
        --mtime="@${SOURCE_EPOCH}" \
        --format=ustar \
        -cf - "$WRAP_NAME" \
    | xz -T 1 -9 > "$OUTPUT_TARBALL") || die "tar+xz failed"

# ---------- 8. emit Cargo.lock as side artifact ----------
OUTPUT_LOCK="$OUTPUT_DIR_ABS/${PKG_NAME}-${PKG_VERSION}-Cargo.lock"
cp "$WRAP_DIR/Cargo.lock" "$OUTPUT_LOCK" 2>/dev/null \
    || cp "$LOCK_FILE" "$OUTPUT_LOCK" 2>/dev/null \
    || true  # Cargo.lock was moved with PROJECT_ROOT — find it
# After the mv to stage, original LOCK_FILE path is gone. Search wrapper dir.
if [ ! -f "$OUTPUT_LOCK" ]; then
    FOUND_LOCK="$(find "$STAGE_DIR" -name Cargo.lock -maxdepth 4 | head -1)"
    [ -f "$FOUND_LOCK" ] || die "Cargo.lock lost during stage move (should not happen)"
    cp "$FOUND_LOCK" "$OUTPUT_LOCK"
fi

# ---------- 9. report ----------
VENDOR_TAR_SHA="$(sha256sum "$OUTPUT_TARBALL" | awk '{print $1}')"
LOCK_OUT_SHA="$(sha256sum "$OUTPUT_LOCK"     | awk '{print $1}')"
VENDOR_TAR_SIZE="$(stat -c '%s' "$OUTPUT_TARBALL")"

cat >&2 <<EOF

=== cargo-vendor-gen complete ===
Package:           ${PKG_NAME} ${PKG_VERSION}
Cargo:             ${CARGO_VERSION}
SOURCE_DATE_EPOCH: ${SOURCE_EPOCH}
Source tarball:    ${SOURCE_TARBALL}
  sha256:          ${SOURCE_SHA}
Cargo.lock:        ${LOCK_ORIGIN}
  sha256:          ${LOCK_SHA}
Vendored crates:   ${VENDOR_CRATE_COUNT}

Outputs:
  ${OUTPUT_TARBALL}
    size:          ${VENDOR_TAR_SIZE} bytes
    sha256:        ${VENDOR_TAR_SHA}
  ${OUTPUT_LOCK}
    sha256:        ${LOCK_OUT_SHA}

Next steps:
  1. Stage outputs to build mirror (rsync to build coordinator host or build/sources/)
  2. Confirm package.yml build_artifacts: declares both names
  3. Confirm build.sh extracts vendor.tar.xz with --strip-components=1 at configure
  4. (If Cargo.lock was 'generated') Confirm build.sh cp's it before cargo vendor consumption
EOF

# Machine-readable summary on stdout (TSV: kind\tpath\tsha256\tmeta)
printf '%s\t%s\t%s\t%s\n' "vendor-tarball" "$OUTPUT_TARBALL" "$VENDOR_TAR_SHA" "crates=${VENDOR_CRATE_COUNT}"
printf '%s\t%s\t%s\t%s\n' "cargo-lock"     "$OUTPUT_LOCK"    "$LOCK_OUT_SHA"   "origin=${LOCK_ORIGIN}"
