#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# build-intergenos-wiki-tarball.sh — regenerate the `intergenos-wiki` package's
# `generated: true` source tarball with deterministic byte-output.
#
# Deliberately STANDALONE (not a function inside build-intergenos-source-tarballs.sh):
# that shared script is listed in the source_tree of all 8 theming/welcome
# packages, so editing it would drift their content_hash with zero output change
# (the ledger item-8b phantom-bump class). A separate script keeps this package's
# generator off those packages' input surface entirely.
#
# The tarball bundles:
#   - book/                    the RELEASE-STAGED rendered mdBook HTML (~75 MB incl.
#                              screenshots), NOT git-vendored — staged from the
#                              separate wiki repo's `mdbook build` output at
#                              $IGOS_WIKI_BOOK_DIR (default build/wiki-book).
#   - pages-manifest.json      the COMMITTED per-page sha256 manifest.
#   - pages-manifest.json.asc  its COMMITTED operator (release-key) signature.
# The manifest + .asc are consumed VERBATIM so the operator's signature stays
# valid over the shipped bytes; the script fails closed if the committed manifest
# does not match the staged book/, and SKIPs (never fabricates content) when the
# staged book/ or the signed manifest is absent.
#
# Deterministic-tar flags mirror build-intergenos-source-tarballs.sh det_tar
# (fixed epoch, ustar, sorted, mode-normalized) so regeneration is byte-stable.
# See docs/research/security/wiki-citation-integrity.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"/.. && pwd)"
SOURCES_DIR="$REPO_ROOT/build/sources"
PKG_DIR="$REPO_ROOT/packages/desktop/intergenos-wiki"
EPOCH_FALLBACK=1735689600   # 2025-01-01T00:00:00Z — same fixed epoch as the sibling generator

emit_log() { echo "[build-intergenos-wiki-tarball] $*"; }

for cmd in tar xz sha256sum awk grep install mktemp cmp python3; do
    command -v "$cmd" >/dev/null || { echo "FATAL: $cmd missing on PATH" >&2; exit 1; }
done

pkg_yml="$PKG_DIR/package.yml"
[ -f "$pkg_yml" ] || { emit_log "SKIP: package.yml missing ($pkg_yml)"; exit 0; }
version="$(grep '^version:' "$pkg_yml" | head -1 | awk -F'"' '{print $2}')"
[ -n "$version" ] || { emit_log "ERROR: unreadable version in $pkg_yml"; exit 1; }

book_dir="${IGOS_WIKI_BOOK_DIR:-$REPO_ROOT/build/wiki-book}"
manifest="$PKG_DIR/pages-manifest.json"
sig="$PKG_DIR/pages-manifest.json.asc"

if [ ! -d "$book_dir" ]; then
    emit_log "SKIP: rendered book dir absent ($book_dir) — stage the wiki mdbook build there at release time (IGOS_WIKI_BOOK_DIR overrides)"
    exit 0
fi
if [ ! -f "$manifest" ] || [ ! -f "$sig" ]; then
    emit_log "SKIP: signed page manifest incomplete (need $manifest + .asc) — regenerate with scripts/build-wiki-page-manifest.py and operator-sign at release time"
    exit 0
fi

mkdir -p "$SOURCES_DIR"
stage_root="$(mktemp -d -t igos-wiki-tarball.XXXXXXXX)"
trap 'rm -rf "$stage_root"' EXIT

# Fail-closed integrity: the committed (signed) manifest MUST match the staged
# book/ byte-for-byte, else the signature does not cover the shipped pages.
check="$stage_root/manifest-check.json"
python3 "$REPO_ROOT/scripts/build-wiki-page-manifest.py" "$book_dir" "$check"
if ! cmp -s "$manifest" "$check"; then
    emit_log "ERROR: committed pages-manifest.json does not match the staged book/ — regenerate + re-sign (the signature would not cover the shipped pages)"
    exit 1
fi

top="intergenos-wiki-${version}"
stage="$stage_root/$top"
mkdir -p "$stage/book"
cp -a "$book_dir/." "$stage/book/"
install -m644 "$manifest" "$stage/pages-manifest.json"
install -m644 "$sig" "$stage/pages-manifest.json.asc"

out="$SOURCES_DIR/${top}.tar.xz"
XZ_OPT='-9 -T1 --no-warn' tar \
    --sort=name \
    --owner=0 --group=0 --numeric-owner \
    --format=ustar \
    --mode='u+rw,go=rX' \
    --mtime="@${EPOCH_FALLBACK}" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    -C "$stage_root" \
    -cJf "$out" "$top"

sha="$(sha256sum "$out" | awk '{print $1}')"
pages="$(python3 -c "import json;print(json.load(open('$manifest'))['page_count'])")"
emit_log "intergenos-wiki ${version}: ${pages} pages epoch=${EPOCH_FALLBACK} sha=${sha:0:16}..."
