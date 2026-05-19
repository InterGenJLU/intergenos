#!/bin/bash
# build-forge-tarball.sh — regenerate forge-<version>.tar.xz from in-tree sources.
#
# The forge package (packages/desktop/forge/) ships InterGenOS's installer
# (the source-tree `installer/` directory plus `man/forge.1`). Until this
# script existed, the source tarball at build/sources/forge-<version>.tar.xz
# was hand-curated: any edit to installer/* silently did not reach the
# chroot via the package because the tarball still contained the old
# snapshot and its sha256 hadn't changed. Same staleness shape as the
# chroot-rsync-coverage-gap class of bugs.
#
# What this script does:
#   1. Reads the forge package.yml to get the canonical version
#   2. Assembles a staging dir with the canonical tarball layout:
#        forge-<version>/
#          installer/...   (copy of /mnt/intergenos/installer)
#          forge.1         (copy of /mnt/intergenos/man/forge.1)
#   3. Tars and xz-compresses into build/sources/forge-<version>.tar.xz
#   4. Computes the new sha256
#   5. Updates the sha256 field in packages/desktop/forge/package.yml
#      in place so the next igos-build.py invocation sees the matching hash
#
# Intended usage:
#   - Direct: scripts/build-forge-tarball.sh   (run after editing installer/)
#   - Orchestrator: phase_setup invokes this before phase_verify_sources
#     so a fresh build always uses the current in-tree content.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"/.. && pwd)"
PKG_YML="$REPO_ROOT/packages/desktop/forge/package.yml"
SOURCES_DIR="$REPO_ROOT/build/sources"
INSTALLER_DIR="$REPO_ROOT/installer"
MANPAGE_SRC="$REPO_ROOT/man/forge.1"

[ -f "$PKG_YML" ]       || { echo "ERROR: $PKG_YML missing" >&2; exit 1; }
[ -d "$INSTALLER_DIR" ] || { echo "ERROR: $INSTALLER_DIR missing" >&2; exit 1; }
[ -f "$MANPAGE_SRC" ]   || { echo "ERROR: $MANPAGE_SRC missing" >&2; exit 1; }

VERSION=$(grep '^version:' "$PKG_YML" | head -1 | awk -F'"' '{print $2}')
[ -n "$VERSION" ] || { echo "ERROR: could not parse version from $PKG_YML" >&2; exit 1; }

TARBALL="$SOURCES_DIR/forge-${VERSION}.tar.xz"
STAGE_ROOT=$(mktemp -d)
trap 'rm -rf "$STAGE_ROOT"' EXIT

STAGE_DIR="$STAGE_ROOT/forge-${VERSION}"
mkdir -p "$STAGE_DIR"

echo "[build-forge-tarball] staging forge-${VERSION}..."
# Skip __pycache__ + .pyc to keep the tarball deterministic and small.
rsync -a --exclude='__pycache__' --exclude='*.pyc' \
    "$INSTALLER_DIR/" "$STAGE_DIR/installer/"
cp "$MANPAGE_SRC" "$STAGE_DIR/forge.1"

# C-004 fix: stage per-package post_install hook content (build.sh +
# package.yml only — NOT source tarballs / patches) at installer-hooks/
# in the tarball. Shipping into /usr/share/intergenos/installer-hooks/ via
# forge package's do_install gives run_post_install_hooks a source-tree
# shape to iterate (tier_dir/pkg_dir/build.sh) — fixes the long-standing
# silent-no-op against the flat pkm manifest dir at /var/lib/igos/packages.
#
# Same path also serves packages.install_packages's tier-filtering at
# packages.py:90-99 — single packages_dir argument feeds both consumers.
HOOKS_STAGE="$STAGE_DIR/installer-hooks"
mkdir -p "$HOOKS_STAGE"
HOOK_PKG_COUNT=0
for tier_dir in "$REPO_ROOT/packages"/*/; do
    tier_name=$(basename "$tier_dir")
    # Skip toolchain — these are bootstrap-only packages with no
    # install-time post_install relevance (executed during chroot
    # construction, never at user-install time).
    [ "$tier_name" = "toolchain" ] && continue
    for pkg_dir in "$tier_dir"*/; do
        [ -d "$pkg_dir" ] || continue
        pkg_name=$(basename "$pkg_dir")
        build_sh="$pkg_dir/build.sh"
        [ -f "$build_sh" ] || continue
        dest="$HOOKS_STAGE/$tier_name/$pkg_name"
        mkdir -p "$dest"
        cp "$build_sh" "$dest/build.sh"
        if [ -f "$pkg_dir/package.yml" ]; then
            cp "$pkg_dir/package.yml" "$dest/package.yml"
        fi
        HOOK_PKG_COUNT=$((HOOK_PKG_COUNT+1))
    done
done
echo "[build-forge-tarball] staged installer-hooks for $HOOK_PKG_COUNT package(s)"

mkdir -p "$SOURCES_DIR"

# Force-overwrite if a stale tarball exists.
rm -f "$TARBALL"

# Deterministic tar invocation — sorted names, fixed mtime so the sha256
# changes only when content changes, not when re-running on the same input.
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$REPO_ROOT" log -1 --format=%ct 2>/dev/null || echo 0)}"
tar -C "$STAGE_ROOT" \
    --sort=name \
    --owner=0 --group=0 --numeric-owner \
    --mtime="@${SOURCE_DATE_EPOCH}" \
    -cJf "$TARBALL" \
    "forge-${VERSION}"

NEW_SHA=$(sha256sum "$TARBALL" | awk '{print $1}')
SIZE=$(stat -c%s "$TARBALL")

echo "[build-forge-tarball] wrote $TARBALL ($SIZE bytes, sha256 $NEW_SHA)"

# Patch the sha256 in package.yml. The line is the unique sha256 entry
# immediately under the file:///forge-<version>.tar.xz url declaration.
# Use a python one-liner to keep the YAML structurally valid rather than
# blind sed.
python3 - "$PKG_YML" "$NEW_SHA" <<'PY'
import sys, re, pathlib
yml_path = pathlib.Path(sys.argv[1])
new_sha  = sys.argv[2]
text     = yml_path.read_text()
# Match the sha256 line under the forge tarball source entry. Avoid
# accidentally rewriting a hypothetical second sha256 elsewhere by
# anchoring on the prior `url:` referencing forge-*.tar.xz.
pat = re.compile(
    r'(- url: file:///forge-[^\n]+\n\s+sha256:\s*)[0-9a-fA-F]+',
    re.MULTILINE,
)
new_text, n = pat.subn(rf'\g<1>{new_sha}', text)
if n != 1:
    sys.stderr.write(f"ERROR: expected exactly 1 forge sha256 line, found {n}\n")
    sys.exit(1)
yml_path.write_text(new_text)
print(f"[build-forge-tarball] updated sha256 in {yml_path}")
PY

echo "[build-forge-tarball] done."
