#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
#   4. Computes the sha256 for the build log (informational only)
#   5. Does NOT pin it — forge is a `generated: true` source (no committed sha;
#      see igos-build/parser.py Source.generated)
#      in place so the next igos-build.py invocation sees the matching hash
#
# Intended usage:
#   - Direct: scripts/build-forge-tarball.sh   (run after editing installer/)
#   - Orchestrator: phase_verify_sources invokes this on a full build, AND
#     ensure_sources_staged invokes it at the --start-at resume entry (which
#     skips verify-sources), so both a fresh build and a resume always use the
#     current in-tree content rather than a stale pinned tarball.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"/.. && pwd)"
PKG_YML="$REPO_ROOT/packages/desktop/forge/package.yml"
SOURCES_DIR="$REPO_ROOT/build/sources"
INSTALLER_DIR="$REPO_ROOT/installer"
MANPAGE_SRC="$REPO_ROOT/man/forge.1"
# The boot-order checker ships with forge (it verifies the boot entry forge
# registered), so its manual page stages beside forge's own.
BOOTORDER_MANPAGE_SRC="$REPO_ROOT/man/intergenos-bootorder-check.8"

[ -f "$PKG_YML" ]       || { echo "ERROR: $PKG_YML missing" >&2; exit 1; }
[ -d "$INSTALLER_DIR" ] || { echo "ERROR: $INSTALLER_DIR missing" >&2; exit 1; }
[ -f "$MANPAGE_SRC" ]   || { echo "ERROR: $MANPAGE_SRC missing" >&2; exit 1; }
[ -f "$BOOTORDER_MANPAGE_SRC" ] || \
    { echo "ERROR: $BOOTORDER_MANPAGE_SRC missing" >&2; exit 1; }

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
cp "$BOOTORDER_MANPAGE_SRC" "$STAGE_DIR/intergenos-bootorder-check.8"

# Stage user-facing docs (docs/users/*.md) so the Forge package can
# install them to /usr/share/doc/intergenos/users/ — both the live ISO
# and the installed system get the same doc tree. UserPage's inline
# doc viewer reads from that path; without staging the tree, the
# viewer falls back to the "doc not installed" path with a GitHub URL.
USER_DOCS_SRC="$REPO_ROOT/docs/users"
if [ -d "$USER_DOCS_SRC" ]; then
    # rsync only creates the final dest component, not missing parents — create
    # the docs/ parent first or it dies with "mkdir docs/users: No such file or
    # directory" (this staging block is newer than the last forge tarball, so
    # the first full build to run it surfaced the gap).
    mkdir -p "$STAGE_DIR/docs/users"
    rsync -a "$USER_DOCS_SRC/" "$STAGE_DIR/docs/users/"
    DOC_COUNT=$(find "$STAGE_DIR/docs/users" -name '*.md' -type f | wc -l)
    # The rsync above carries docs/users/images/ with the markdown; count
    # it separately so a staging run says plainly whether the images the
    # docs reference came along (the viewer resolves them relative to the
    # doc's own directory, so they ship together or not at all).
    IMG_COUNT=$(find "$STAGE_DIR/docs/users/images" -type f 2>/dev/null | wc -l)
    echo "[build-forge-tarball] staged $DOC_COUNT user-facing doc(s) and $IMG_COUNT doc image(s) from docs/users/"
fi

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
        pkg_yml="$pkg_dir/package.yml"
        # Stage a package if it has EITHER file. A yml-only recipe (pure
        # build_style, no custom build.sh — e.g. plutosvg/plutovg/sdl3-ttf)
        # MUST still ship its package.yml: Forge's install-set discovery,
        # runtime-dep closure, and hardware/EULA gates all read it. Skipping
        # the whole dir on a missing build.sh made those packages invisible
        # to the installer — the ge9b-04 dogfood install silently dropped
        # plutosvg+plutovg (sdl3-ttf's runtime deps), leaving libSDL3_ttf
        # unloadable on the target (PI-ge9b04-C). The hook RUNNER iterates
        # build.sh only, so a yml-only dir is inert to it.
        [ -f "$build_sh" ] || [ -f "$pkg_yml" ] || continue
        dest="$HOOKS_STAGE/$tier_name/$pkg_name"
        mkdir -p "$dest"
        if [ -f "$build_sh" ]; then
            cp "$build_sh" "$dest/build.sh"
        fi
        if [ -f "$pkg_yml" ]; then
            cp "$pkg_yml" "$dest/package.yml"
        fi
        HOOK_PKG_COUNT=$((HOOK_PKG_COUNT+1))
    done
done
echo "[build-forge-tarball] staged installer-hooks for $HOOK_PKG_COUNT package(s)"

# Break the self-referential non-determinism: forge's OWN package.yml carries
# the sha256 of THIS tarball (forge IS this package), so staging it verbatim
# makes the tarball's content depend on its own hash — a fixpoint that never
# converges (each regen embeds the previous run's pin -> new content -> new
# pin...), which churned the forge pin on every regen even with identical
# sources. The pin inside the staged hooks tree is unused at install time (the
# installer resolves archives, not this build-time source pin), so blank
# forge's own pin in the STAGED copy for a reproducible tarball. The real
# packages/desktop/forge/package.yml pin (written below) is untouched.
forge_self_yml="$HOOKS_STAGE/desktop/forge/package.yml"
if [ -f "$forge_self_yml" ]; then
    sed -i -E 's|^([[:space:]]*sha256:[[:space:]]*)[0-9a-fA-F]+|\1SELF|' "$forge_self_yml"
fi

# Break the bump-metadata feedback loop (ledger item 8a, 2026-07-05): the
# staged hook ymls embed `release:` + `content_hash:` — auto-written bump
# artifacts consumed by NOTHING at install time (the installer reads only
# name / dependencies.runtime / requires_pci_vendor / eula_helper from these
# copies; verified against installer/backend/packages.py). Left in place,
# every bump wave rewrites those lines, which changes THIS tarball's bytes,
# which feeds forge's own content fingerprint, which re-drifts forge one
# cycle later — +1/cycle release inflation (reproduced live: forge double-
# bumped 10->11->12 in one prep window). Strip both top-level lines from
# EVERY staged copy so the hooks tree depends on real content only. The real
# packages/*/*/package.yml files are untouched.
find "$HOOKS_STAGE" -name package.yml -exec sed -i -E '/^(release|content_hash):/d' {} +

mkdir -p "$SOURCES_DIR"

# Force-overwrite if a stale tarball exists.
rm -f "$TARBALL"

# Deterministic tar invocation — sorted names, fixed mtime so the sha256
# changes only when content changes, not when re-running on the same input.
# --format=ustar + XZ_OPT='-T1' are LOAD-BEARING for that: without an explicit
# format tar emits the pax format whose per-entry extended headers carry
# varying metadata, and multi-threaded xz (-T>1) splits blocks
# non-deterministically — either makes the .tar.xz sha churn run-to-run on
# identical content (observed: forge pin flapped every regen). Mirrors the
# det_tar flags the intergenos-source-tarballs pipeline already uses.
# Fixed epoch (NOT the HEAD commit time). Deriving it from
# `git log -1 --format=%ct` (whole-repo HEAD time) made the forge tarball mtime
# — and thus its sha256 pin — change on EVERY commit: the committed pin was
# stale the moment any other commit landed, and phase_verify_sources rewrote it
# on each build (perpetual dirty git; observed f572fb20 -> edbb0dc6 across the
# GBC002.5 commits). A fixed mtime makes the tarball bytes depend on CONTENT
# only, so the pin changes solely when forge's bundled source actually changes
# (the content hash catches that). 1735689600 = 2025-01-01T00:00:00Z, matching
# build-intergenos-source-tarballs.sh's EPOCH_FALLBACK. Byte-identical across
# machines requires the same tar/xz (GNU tar 1.35 + XZ 5.4.5; host == build VM).
#
# PIN A LOCAL EPOCH — do NOT honor the ambient $SOURCE_DATE_EPOCH. The build
# EXPORTS SOURCE_DATE_EPOCH (a build-moment value, written to the manifest); the
# prior "${SOURCE_DATE_EPOCH:-...}" form INHERITED it, so forge's source-tarball
# mtime (and thus its content_hash) tracked the build moment. A fresh/standalone
# regen (env unset -> fallback) then never matched the committed baseline, so
# forge ALONE showed perpetual content-hash drift. Forge was the one generated-
# tarball generator honoring the ambient epoch; the sibling pins its own
# (epoch_for -> EPOCH_FALLBACK, env-independent). Dedicated local var so forge's
# tarball depends on CONTENT only. (Proven env-SENSITIVE before, env-INDEPENDENT
# after.)
FORGE_TARBALL_EPOCH=1735689600   # 2025-01-01T00:00:00Z; == sibling's EPOCH_FALLBACK
# --mode normalizes member permissions (files u+rw,go=r + x-propagated when
# executable; dirs 755): staged perms otherwise inherit the generation
# context — mkdir/rsync honor the ambient umask, so the same content tarred
# under umask 002 vs 022 yields DIFFERENT bytes (dir modes 775 vs 755) with
# zero content change — the ledger item-8b regeneration-context drift class.
# Mirrors det_tar in build-intergenos-source-tarballs.sh.
XZ_OPT='-9 -T1 --no-warn' tar -C "$STAGE_ROOT" \
    --sort=name \
    --owner=0 --group=0 --numeric-owner \
    --format=ustar \
    --mode='u+rw,go=rX' \
    --mtime="@${FORGE_TARBALL_EPOCH}" \
    -cJf "$TARBALL" \
    "forge-${VERSION}"

NEW_SHA=$(sha256sum "$TARBALL" | awk '{print $1}')
SIZE=$(stat -c%s "$TARBALL")

echo "[build-forge-tarball] wrote $TARBALL ($SIZE bytes, sha256 $NEW_SHA)"

# forge is a `generated: true` source: packages/desktop/forge/package.yml carries
# NO sha256 pin, so this script does NOT write package.yml. forge's tarball is a
# snapshot of the whole packages/ tree (build.sh + package.yml for the installer
# post-install hooks), so its bytes change on any package edit — a committed pin
# could never stay current, and wouldn't be portable across builders' tar/xz
# anyway. verify-sources asserts the generator produced the tarball instead of
# byte-pinning it. The sha above is logged for the build record only.
# See igos-build/parser.py Source.generated.

echo "[build-forge-tarball] done."
