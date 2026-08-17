#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# check-stable-urls.sh -- lint for moving-target URLs in package source declarations.
#
# Catches URL patterns that target a moving symbolic reference instead of a
# stable version-pinned path. Even with sha256 pinning (which igos-build
# always requires), moving-target URLs are NOT a security defect -- the SHA
# pin defends against silent corruption -- but they ARE a build-stability
# concern: an upstream /latest/ promotion turns a previously-working build
# into a hard fetch failure. Fail-loud on upstream churn beats silent rot,
# but stable-by-design beats fail-loud.
#
# Authored 2026-05-22 per USA-1 Thing 28 walk: ibus/package.yml previously
# fetched https://www.unicode.org/Public/UCD/latest/ucd/UCD.zip; the swap to
# the versioned path /Public/17.0.0/ucd/UCD.zip serves byte-identical content
# (sha256 verified) and survives Unicode rev-bumps. This script is the
# preventive gate that keeps the moving-target class from regressing.
#
# Patterns rejected (unambiguous moving-target refs):
#   /latest/                                -- universal latest-symlink (e.g.
#                                              unicode.org /Public/UCD/latest/)
#   /HEAD/                                  -- explicit HEAD ref
#   releases/latest                         -- GitHub Releases /latest endpoint
#   archive/refs/heads/(main|master)        -- GitHub default-branch tarball
#   raw.githubusercontent.com/X/Y/(main|master|HEAD)/  -- raw-URL branch ref
#   github.com/X/Y/(raw|blob)/(main|master|HEAD)/      -- github.com branch URL
#
# Plain /main/ and /master/ and /trunk/ subdirs are NOT rejected -- they're
# legitimate in Debian apt pool paths (pool/main/X/) and Launchpad series
# paths (intltool/trunk/0.51.0/+download/). Only branch-ref-equivalent uses
# are caught.
#
# Templated placeholders like \${version} are NOT moving targets (the parser
# substitutes from the package.yml version: field at build time) and are
# explicitly fine.
#
# Exit codes:
#   0  -- no violations; build / commit may proceed
#   1  -- one or more moving-target URLs found
#   2  -- script usage / environment error

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT" || { echo "[check-stable-urls] not in a git repo" >&2; exit 2; }

# Single grep pass: match `url:` lines (with or without leading `-`) whose
# value contains one of the unambiguous moving-target patterns. We avoid
# flagging plain /main/, /master/, /trunk/ subdirs because those are
# legitimate path components in archive layouts (Debian pool/main/,
# Launchpad project/trunk/<version>/, etc.). Only branch-ref-equivalent
# constructions are rejected.
PATTERN='(/latest/|/HEAD/|releases/latest|archive/refs/heads/(main|master)|raw\.githubusercontent\.com/[^/[:space:]]+/[^/[:space:]]+/(main|master|HEAD)/|github\.com/[^/[:space:]]+/[^/[:space:]]+/(raw|blob)/(main|master|HEAD)/)'

VIOLATIONS=$(grep -rEn \
    "^[[:space:]]*-?[[:space:]]*url:[[:space:]]+\S*${PATTERN}" \
    packages/ --include="package.yml" 2>/dev/null || true)

if [ -n "$VIOLATIONS" ]; then
    COUNT=$(echo "$VIOLATIONS" | wc -l)
    echo "[check-stable-urls] FAIL: ${COUNT} moving-target URL(s) found:"
    echo
    echo "$VIOLATIONS" | sed 's/^/  /'
    echo
    echo "  Fix: pin to a versioned path (e.g. /<version>/ instead of /latest/)."
    echo "  Even with sha256 pinning, moving-target URLs cause builds to break"
    echo "  on upstream rev-bumps. Use the version that corresponds to your"
    echo "  sha256 -- you can compute it by inspecting the cached source file"
    echo "  in build/sources/ (e.g. unzip -p UCD.zip ReadMe.txt | head)."
    exit 1
fi

YML_COUNT=$(find packages -name "package.yml" 2>/dev/null | wc -l)
echo "[check-stable-urls] OK: ${YML_COUNT} package.yml files scanned; no moving-target URLs"
exit 0
