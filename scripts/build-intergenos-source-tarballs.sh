#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# build-intergenos-source-tarballs.sh — regenerate in-tree source tarballs
# from canonical asset directories with deterministic byte-output.
#
# Closes audit-rows D-003 / D-004 / D-017 / J-003 / J-018 reproducibility-
# script gap (theming-arc Item O at docs/research/theming/
# 2026-05-22-pre-iso-theming-audit-prep.md).
#
# What this script does:
#   For each in-tree-generated package, snapshots its canonical asset tree
#   into build/sources/<name>-<version>.tar.xz with deterministic tar flags
#   so byte-identical regeneration is achievable from a clean repo clone.
#   These are `generated: true` sources (NO committed sha256 pin): the build
#   regenerates them every run and trusts the deterministic generation — a
#   pin would be unportable across builders' tar/xz. See parser.py Source.
#
# Packages handled (8):
#   - intergen-welcome                    — assets/intergen-welcome/ → iw-pkg/...
#   - intergenos-theme                    — assets/intergen-shell-theme/
#                                           → intergenos-theme-<v>/...
#   - intergenos-extensions-appearance    \
#   - intergenos-extensions-layout         | unzip each UUID.zip from
#   - intergenos-extensions-productivity   | assets/theming/extensions/
#   - intergenos-extensions-utilities     / into ./<UUID>/...
#   - bibata-cursor-theme                 — bundle 3-of-4 variant tarballs
#                                           from assets/theming/cursor-themes/
#                                           Bibata-Modern-{Classic,Amber,Ice}.tar.xz
#                                           (Original-Classic deliberately omitted
#                                           per build.sh comment)
#   - catppuccin-gtk-theme                — unzip catppuccin-mocha-blue.zip from
#                                           assets/theming/gtk-themes/ + retar
#                                           with ONLY the standard+default
#                                           variant (drops hdpi + xhdpi siblings
#                                           per build.sh do_install)
#
# Handled by other pipelines:
#   - forge                      — see scripts/build-forge-tarball.sh
#   - upstream tarball URLs       — most file:/// upstream-cached packages flipped
#                                  to canonical https:// in commit d114b54e
#                                  (macos-cursor-theme + phinger-cursors + user-
#                                  theme join that pattern in this commit)
#   - ca-certificates            — Mozilla NSS extract; separate pipeline
#   - lego-*-vendor.tar.xz       — `go mod vendor` output; separate pipeline
#
# Deterministic-tar flags (per theming-arc Item O spec):
#   tar --sort=name --owner=0 --group=0 --numeric-owner
#       --format=ustar --mtime='@<SOURCE_DATE_EPOCH>'
#   XZ_OPT='-9 -T1 --no-warn'   (single-threaded; -T>1 is non-deterministic)
#
# SOURCE_DATE_EPOCH choice:
#   A FIXED epoch (EPOCH_FALLBACK = 2025-01-01T00:00:00Z = 1735689600) sets the
#   tar --mtime of every bundle. This was the per-path git commit time
#   (`git log -1 --format=%ct -- <path>`) until 2026-06-08; that scheme had a
#   bootstrap-drift bug — the sha pin committed alongside a source change is
#   computed at the OLD commit time, but the NEXT regeneration (after the change
#   is committed) uses the NEW commit time, so the tarball bytes and the pin
#   drift, leaving git dirty after every build (observed: the intergen-welcome
#   pin went stale after 7006db88 touched assets/intergen-welcome). A fixed
#   mtime makes the tarball bytes — and thus the pin — a pure function of
#   CONTENT, stable across commits, changing only when the asset content
#   actually changes (the content hash still catches that). Byte-identical
#   across machines requires the same tar/xz (GNU tar 1.35 + XZ 5.4.5; verified
#   identical on the host and the build VM).
#
# Intended usage:
#   - Direct: scripts/build-intergenos-source-tarballs.sh   (after editing assets)
#   - Orchestrator: phase_verify_sources invokes alongside build-forge-tarball.sh
#     so a fresh build always uses current in-tree content.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"/.. && pwd)"
SOURCES_DIR="$REPO_ROOT/build/sources"
PACKAGES_DIR="$REPO_ROOT/packages/desktop"
ASSETS_DIR="$REPO_ROOT/assets"
EPOCH_FALLBACK=1735689600   # 2025-01-01T00:00:00Z

mkdir -p "$SOURCES_DIR"

emit_log() { echo "[build-intergenos-source-tarballs] $*"; }

# Required tools.
for cmd in tar xz sha256sum sed grep awk unzip git install mktemp; do
    command -v "$cmd" >/dev/null || { echo "FATAL: $cmd missing on PATH" >&2; exit 1; }
done

# Single script-level temp root; per-package functions get subdirs.
STAGE_ROOT="$(mktemp -d -t igos-src-tarballs.XXXXXXXX)"
trap 'rm -rf "$STAGE_ROOT"' EXIT

# pkg_version <package.yml> — extract version field.
pkg_version() {
    grep '^version:' "$1" | head -1 | awk -F'"' '{print $2}'
}

# epoch_for <relpath-from-repo-root> — returns the FIXED tarball epoch (mtime).
# Deliberately NOT the per-path git commit time anymore (see "SOURCE_DATE_EPOCH
# choice" in the header): the commit-time scheme drifted the committed sha pin
# on every source change. A fixed epoch makes the pin track CONTENT only.
# $relpath is retained for call-site compatibility / logging.
epoch_for() {
    local relpath="$1"   # retained for call-site compatibility; no longer used to derive the epoch
    echo "$EPOCH_FALLBACK"
}

# det_tar <out> <epoch> <cwd> <args...> — deterministic xz tar.
# --mode normalizes member permissions (files u+rw,go=r + x-propagated when
# executable; dirs 755): staged perms otherwise inherit the generation context
# — mkdir/unzip/cp honor the ambient umask, so the same content tarred under
# umask 002 vs 022 yields DIFFERENT bytes (dir modes 775 vs 755) with zero
# content change. That context leak is the ledger item-8b class that phantom-
# drifted 7 asset tarballs on 2026-07-05; fingerprints now hash the declared
# INPUTS (package.yml source_tree), and this flag restores the header's
# stated byte-identical-regeneration property for the artifact itself.
det_tar() {
    local out="$1"; shift
    local epoch="$1"; shift
    local cwd="$1"; shift
    XZ_OPT='-9 -T1 --no-warn' tar \
        --sort=name \
        --owner=0 --group=0 --numeric-owner \
        --format=ustar \
        --mode='u+rw,go=rX' \
        --mtime="@${epoch}" \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.DS_Store' \
        -C "$cwd" \
        -cJf "$out" "$@"
}

# These are `generated: true` sources: each package.yml carries NO sha256 pin
# (the compressed bytes depend on the local tar/xz, so a committed pin would be
# unportable across builders). This script PRODUCES the tarballs only — it never
# writes package.yml. Integrity comes from git (the asset content) + the
# deterministic generator + the verify-sources "generator produced it" check.
# See igos-build/parser.py Source.generated.

# ----- intergen-welcome -----
build_intergen_welcome() {
    local pkg_yml="$PACKAGES_DIR/intergen-welcome/package.yml"
    [ -f "$pkg_yml" ] || { emit_log "SKIP intergen-welcome: package.yml missing"; return; }

    local version; version="$(pkg_version "$pkg_yml")"
    [ -n "$version" ] || { emit_log "ERROR intergen-welcome: unreadable version"; return 1; }

    local src="$ASSETS_DIR/intergen-welcome"
    [ -d "$src" ] || { emit_log "ERROR intergen-welcome: $src missing"; return 1; }
    [ -f "$src/intergen-welcome.py" ] || { emit_log "ERROR intergen-welcome: intergen-welcome.py missing"; return 1; }

    local stage="$STAGE_ROOT/intergen-welcome"
    mkdir -p "$stage/iw-pkg/previews"

    install -m644 "$src/intergen-welcome.py" "$stage/iw-pkg/intergen-welcome.py"
    # Privileged helper for the "Enable Services" page (pkexec'd; exec bit set
    # at install). Required — the page's toggles invoke it.
    install -m755 "$src/intergen-welcome-privhelper" "$stage/iw-pkg/intergen-welcome-privhelper"
    # The polkit action that authorizes that helper. build.sh do_install
    # installs it from the extracted tree, so it MUST be staged here — the
    # same class as org.intergenos.Wiki.svg below: the file joined the asset
    # dir and do_install (2026-08-25) without joining this stage list, and the
    # tarball-membership gate refused the next build pre-flight. Guarded so a
    # missing asset fails the generator loudly.
    [ -f "$src/org.intergenos.welcome.policy" ] || { emit_log "ERROR intergen-welcome: org.intergenos.welcome.policy missing"; return 1; }
    install -m644 "$src/org.intergenos.welcome.policy" "$stage/iw-pkg/org.intergenos.welcome.policy"
    # App icon (Icon=intergen-welcome) — framed glass squircle + pulse + welcome
    # sparkle (operator branding §F; replaces the generic preferences avatar).
    [ -f "$src/intergen-welcome.svg" ] || { emit_log "ERROR intergen-welcome: intergen-welcome.svg missing"; return 1; }
    install -m644 "$src/intergen-welcome.svg" "$stage/iw-pkg/intergen-welcome.svg"
    # First-party wiki mark (Community page's Documentation & Wiki row names
    # org.intergenos.Wiki). build.sh do_install installs it from the extracted
    # tree, so it MUST be staged here — it was added to the asset dir and to
    # do_install without being staged, which made do_install fail on `install:
    # cannot stat` for every build from a freshly generated tarball. Guarded
    # like the app icon so a missing asset fails the generator loudly instead
    # of producing a tarball the package cannot install from.
    [ -f "$src/org.intergenos.Wiki.svg" ] || { emit_log "ERROR intergen-welcome: org.intergenos.Wiki.svg missing"; return 1; }
    install -m644 "$src/org.intergenos.Wiki.svg" "$stage/iw-pkg/org.intergenos.Wiki.svg"
    # The shared "why can't this machine reach the download sources" module,
    # taken from the intergen tree rather than copied into the assets dir.
    # The Welcomer and `intergen setup` both have to decide what a failed name
    # lookup means and what to tell the user about it, and they are separate
    # packages that cannot import each other — so the ONE source file is
    # staged into both. A second copy in the tree would be two answers to the
    # same question waiting to disagree. package.yml lists this path in
    # source_tree, so a change to it moves this package's fingerprint and
    # rebuilds it. Guarded like the icons: a missing input fails the generator
    # loudly rather than producing a tarball the package cannot install from.
    [ -f "$REPO_ROOT/intergen/net_diagnostics.py" ] || { emit_log "ERROR intergen-welcome: intergen/net_diagnostics.py missing"; return 1; }
    install -m644 "$REPO_ROOT/intergen/net_diagnostics.py" "$stage/iw-pkg/net_diagnostics.py"
    # Copy all preview PNGs + .gitkeep; skip generate.py (build-time tool,
    # not runtime asset) and __pycache__ (det_tar excludes it anyway).
    if [ -d "$src/previews" ]; then
        for f in "$src/previews/".gitkeep "$src/previews/"*.png; do
            [ -f "$f" ] && install -m644 "$f" "$stage/iw-pkg/previews/$(basename "$f")"
        done
    fi

    local out="$SOURCES_DIR/intergen-welcome-${version}.tar.xz"
    local epoch; epoch="$(epoch_for "assets/intergen-welcome")"
    det_tar "$out" "$epoch" "$stage" iw-pkg
    local sha; sha="$(sha256sum "$out" | awk '{print $1}')"
    emit_log "intergen-welcome ${version}: epoch=${epoch} sha=${sha:0:16}..."
}

# ----- intergenos-backup (Chronicle) -----
# Source: assets/intergenos-backup/ → chronicle-pkg/... The whole engine
# (chronicle/ package + the four entrypoints) plus every shipped asset
# (systemd units, the sysusers.d group fragment, default conf, .desktop, polkit
# policy, icon, man trio) is staged under one chronicle-pkg/ prefix; det_tar
# drops __pycache__/*.pyc so the build tree's stray bytecode never enters the
# pinless generated tarball.
build_intergenos_backup() {
    local pkg_yml="$PACKAGES_DIR/intergenos-backup/package.yml"
    [ -f "$pkg_yml" ] || { emit_log "SKIP intergenos-backup: package.yml missing"; return; }

    local version; version="$(pkg_version "$pkg_yml")"
    [ -n "$version" ] || { emit_log "ERROR intergenos-backup: unreadable version"; return 1; }

    local src="$ASSETS_DIR/intergenos-backup"
    [ -d "$src" ] || { emit_log "ERROR intergenos-backup: $src missing"; return 1; }
    # Load-bearing sources that MUST be present (fail loud if the tree is wrong).
    for req in chronicle/engine.py chronicled chronicle-cli chronicle-gui \
               chronicle-pretxn-handler systemd/chronicled.service \
               sysusers/chronicle.conf \
               config/chronicle.conf desktop/org.intergenos.Chronicle.desktop \
               polkit/org.intergenos.Chronicle.policy \
               icons/org.intergenos.Chronicle.svg man/chronicle.1; do
        [ -e "$src/$req" ] || { emit_log "ERROR intergenos-backup: $req missing"; return 1; }
    done

    local stage="$STAGE_ROOT/intergenos-backup"
    local pkg="$stage/chronicle-pkg"
    mkdir -p "$pkg"

    # The engine package + the four entrypoints (executable bits set at install
    # by build.sh, not carried from the tarball). Copy the whole chronicle/
    # package dir; det_tar excludes __pycache__/*.pyc.
    cp -a "$src/chronicle" "$pkg/chronicle"
    install -m644 "$src/chronicled" "$pkg/chronicled"
    install -m644 "$src/chronicle-cli" "$pkg/chronicle-cli"
    install -m644 "$src/chronicle-gui" "$pkg/chronicle-gui"
    install -m644 "$src/chronicle-pretxn-handler" "$pkg/chronicle-pretxn-handler"

    # Shipped assets, each in its own subdir mirroring the source layout.
    for d in systemd sysusers config desktop polkit icons man; do
        mkdir -p "$pkg/$d"
        # Copy every regular file in the source subdir.
        find "$src/$d" -maxdepth 1 -type f -exec install -m644 {} "$pkg/$d/" \;
    done

    local out="$SOURCES_DIR/intergenos-backup-${version}.tar.xz"
    local epoch; epoch="$(epoch_for "assets/intergenos-backup")"
    det_tar "$out" "$epoch" "$stage" chronicle-pkg
    local sha; sha="$(sha256sum "$out" | awk '{print $1}')"
    emit_log "intergenos-backup ${version}: epoch=${epoch} sha=${sha:0:16}..."
}

# ----- intergenos-theme -----
# Source: assets/intergen-shell-theme/ (legacy directory name).
# Output layout: intergenos-theme-<version>/{gnome-shell,gtk-3.0,gtk-4.0,index.theme}.
build_intergenos_theme() {
    local pkg_yml="$PACKAGES_DIR/intergenos-theme/package.yml"
    [ -f "$pkg_yml" ] || { emit_log "SKIP intergenos-theme: package.yml missing"; return; }

    local version; version="$(pkg_version "$pkg_yml")"
    [ -n "$version" ] || { emit_log "ERROR intergenos-theme: unreadable version"; return 1; }

    local src="$ASSETS_DIR/intergen-shell-theme"
    [ -d "$src" ] || { emit_log "ERROR intergenos-theme: $src missing"; return 1; }

    local stage="$STAGE_ROOT/intergenos-theme"
    local layout="intergenos-theme-${version}"
    mkdir -p "$stage/$layout"

    for subdir in gnome-shell gtk-3.0 gtk-4.0; do
        [ -d "$src/$subdir" ] && cp -a "$src/$subdir" "$stage/$layout/"
    done
    [ -f "$src/index.theme" ] && install -m644 "$src/index.theme" "$stage/$layout/index.theme"

    local out="$SOURCES_DIR/intergenos-theme-${version}.tar.xz"
    local epoch; epoch="$(epoch_for "assets/intergen-shell-theme")"
    det_tar "$out" "$epoch" "$stage" "$layout"
    local sha; sha="$(sha256sum "$out" | awk '{print $1}')"
    emit_log "intergenos-theme ${version}: epoch=${epoch} sha=${sha:0:16}..."
}

# ----- intergenos-extensions-<category> -----
# Source: assets/theming/extensions/<UUID>.zip (one per UUID listed in the
# package's verify_paths).
# Output layout: ./<UUID>/<extension-content> (flat per-UUID dirs).
build_intergenos_extensions_cat() {
    local category="$1"
    local pkg_yml="$PACKAGES_DIR/intergenos-extensions-${category}/package.yml"
    [ -f "$pkg_yml" ] || { emit_log "SKIP intergenos-extensions-${category}: package.yml missing"; return; }

    local version; version="$(pkg_version "$pkg_yml")"
    [ -n "$version" ] || { emit_log "ERROR intergenos-extensions-${category}: unreadable version"; return 1; }

    # Parse UUIDs from verify_paths.
    local uuids
    mapfile -t uuids < <(grep -oE '/usr/share/gnome-shell/extensions/[^ ]+' "$pkg_yml" | awk -F/ '{print $NF}')
    [ ${#uuids[@]} -gt 0 ] || { emit_log "ERROR intergenos-extensions-${category}: no UUIDs in verify_paths"; return 1; }

    local stage="$STAGE_ROOT/intergenos-extensions-${category}"
    mkdir -p "$stage"
    local zip_src="$ASSETS_DIR/theming/extensions"
    for uuid in "${uuids[@]}"; do
        local z="$zip_src/${uuid}.zip"
        [ -f "$z" ] || { emit_log "ERROR intergenos-extensions-${category}: missing $z"; return 1; }
        mkdir -p "$stage/$uuid"
        unzip -q -o "$z" -d "$stage/$uuid"
    done

    local out="$SOURCES_DIR/intergenos-extensions-${category}-${version}.tar.xz"
    local epoch; epoch="$(epoch_for "assets/theming/extensions")"
    # Pack with `.` at top so output entries are ./<UUID>/... — pkg-functions.sh
    # extract_source uses --strip-components=1 unconditionally, which strips the
    # `.` and leaves the per-UUID dirs at src/ root for do_install to find.
    # Same pattern as bibata-cursor-theme below. --sort=name keeps order
    # deterministic.
    det_tar "$out" "$epoch" "$stage" .
    local sha; sha="$(sha256sum "$out" | awk '{print $1}')"
    emit_log "intergenos-extensions-${category} ${version}: ${#uuids[@]} UUIDs epoch=${epoch} sha=${sha:0:16}..."
}

# ----- bibata-cursor-theme -----
# Source: 3-of-4 upstream variant tarballs at assets/theming/cursor-themes/
# Bibata-Modern-{Classic,Amber,Ice}.tar.xz. Bibata-Modern-Original-Classic
# is intentionally NOT shipped (per build.sh comment).
# Output layout: ./Bibata-Modern-{Amber,Classic,Ice}/<contents> (leading
# ./ matches what `tar -cJf ... .` produces — builder.py strip-components=1
# strips the . and leaves the per-variant dirs at cwd for build.sh).
build_bibata_cursor_theme() {
    local pkg_yml="$PACKAGES_DIR/bibata-cursor-theme/package.yml"
    [ -f "$pkg_yml" ] || { emit_log "SKIP bibata-cursor-theme: package.yml missing"; return; }

    local version; version="$(pkg_version "$pkg_yml")"
    [ -n "$version" ] || { emit_log "ERROR bibata-cursor-theme: unreadable version"; return 1; }

    local src="$ASSETS_DIR/theming/cursor-themes"
    [ -d "$src" ] || { emit_log "ERROR bibata-cursor-theme: $src missing"; return 1; }

    local stage="$STAGE_ROOT/bibata-cursor-theme"
    mkdir -p "$stage"

    local variants=(Bibata-Modern-Classic Bibata-Modern-Amber Bibata-Modern-Ice)
    for variant in "${variants[@]}"; do
        local v_tar="$src/${variant}.tar.xz"
        [ -f "$v_tar" ] || { emit_log "ERROR bibata-cursor-theme: missing $v_tar"; return 1; }
        tar -xJf "$v_tar" -C "$stage"
        [ -d "$stage/$variant" ] || { emit_log "ERROR bibata-cursor-theme: $v_tar did not extract a ${variant}/ top dir"; return 1; }
    done

    local out="$SOURCES_DIR/bibata-cursor-theme-${version}.tar.xz"
    local epoch; epoch="$(epoch_for "assets/theming/cursor-themes")"
    # Pack with '.' to produce ./Bibata-Modern-X/ entries matching cached layout.
    det_tar "$out" "$epoch" "$stage" .
    local sha; sha="$(sha256sum "$out" | awk '{print $1}')"
    emit_log "bibata-cursor-theme ${version}: 3 variants epoch=${epoch} sha=${sha:0:16}..."
}

# ----- catppuccin-gtk-theme -----
# Source: assets/theming/gtk-themes/catppuccin-mocha-blue.zip (contains 3 top-
# level dirs: standard+default, standard+default-hdpi, standard+default-xhdpi).
# Output layout: catppuccin-mocha-blue-standard+default/<contents> (drops the
# two HiDPI siblings; only the standard variant is the one build.sh installs).
build_catppuccin_gtk_theme() {
    local pkg_yml="$PACKAGES_DIR/catppuccin-gtk-theme/package.yml"
    [ -f "$pkg_yml" ] || { emit_log "SKIP catppuccin-gtk-theme: package.yml missing"; return; }

    local version; version="$(pkg_version "$pkg_yml")"
    [ -n "$version" ] || { emit_log "ERROR catppuccin-gtk-theme: unreadable version"; return 1; }

    local zip_src="$ASSETS_DIR/theming/gtk-themes/catppuccin-mocha-blue.zip"
    [ -f "$zip_src" ] || { emit_log "ERROR catppuccin-gtk-theme: $zip_src missing"; return 1; }

    local stage="$STAGE_ROOT/catppuccin-gtk-theme"
    mkdir -p "$stage"

    # Unzip ONLY the standard+default variant (drop -hdpi + -xhdpi siblings).
    unzip -q -o "$zip_src" 'catppuccin-mocha-blue-standard+default/*' -d "$stage"
    local theme_dir="catppuccin-mocha-blue-standard+default"
    [ -d "$stage/$theme_dir" ] || { emit_log "ERROR catppuccin-gtk-theme: $theme_dir absent after unzip"; return 1; }

    local out="$SOURCES_DIR/catppuccin-mocha-blue-${version}.tar.xz"
    local epoch; epoch="$(epoch_for "assets/theming/gtk-themes")"
    # Pack with the explicit variant dir to match cached layout (no leading ./).
    det_tar "$out" "$epoch" "$stage" "$theme_dir"
    local sha; sha="$(sha256sum "$out" | awk '{print $1}')"
    emit_log "catppuccin-gtk-theme ${version}: standard+default variant epoch=${epoch} sha=${sha:0:16}..."
}

# ----- main -----
emit_log "REPO_ROOT=$REPO_ROOT"
emit_log "SOURCES_DIR=$SOURCES_DIR"

build_intergen_welcome
build_intergenos_backup
build_intergenos_theme
build_intergenos_extensions_cat appearance
build_intergenos_extensions_cat layout
build_intergenos_extensions_cat productivity
build_intergenos_extensions_cat utilities
build_bibata_cursor_theme
build_catppuccin_gtk_theme

emit_log "done — 9 first-party tarballs generated (generated: true sources; no pins written)"
