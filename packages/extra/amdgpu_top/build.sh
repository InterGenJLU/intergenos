#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# amdgpu_top 0.11.5 — Rust TUI dashboard for AMD GPUs
#
# Build profile: cargo build --release --offline (per-vendor-tarball
# pattern matching base/ripgrep, base/fd, base/bat).
#
# Features chosen:
#   tui            — the TUI back-end (cursive-based)
#   json           — JSON output mode for scripting
#   libdrm_link    — link directly against libdrm_amdgpu at compile time
#                    (vs libdrm_dynamic_loading which dlopens at runtime —
#                    we have libdrm in tree, so link-time is preferable
#                    and surfaces missing-lib errors at build time)
#
# Excluded:
#   gui            — eframe + wgpu + egui_plot adds ~150 MB of vendored
#                    Rust crates and duplicates radeontop's functionality
#                    for headless monitoring. Users who want a Wayland
#                    GUI dashboard install corectrl independently.
#   git_version    — gix dep for embedding the build's git hash; we
#                    install from a source tarball without .git, so this
#                    feature would no-op anyway.
#
# Cross-distro flag comparison:
#   Arch AUR:   cargo build --release (default features, GUI on)
#   Fedora copr: cargo build --release (default features, GUI on)
#   We deviate by disabling GUI. Justification in package.yml.
#
# Security-only-alignment filter: Rust binary; memory-safe by language guarantee at
# the crate-internal level. The libdrm-amdgpu-sys crate exposes
# Rust-unsafe FFI to libdrm — the boundary at FFI is the same trust
# surface as any C app that links libdrm directly. No SUID, no
# daemon, no network surface. Reads sysfs + /proc/<pid>/fdinfo +
# DRM ioctls; needs `video` group membership at runtime.
#
# Pre-staged vendor tarball expected at:
#   $IGOS_SOURCES/amdgpu_top-$PKG_VERSION-vendor.tar.xz
# Generated on the host via the canonical helper (wraps {.cargo,vendor} in a
# <name>-<ver>/ dir, consumed below with --strip-components=1 — matching the
# eza/bottom/just/ripgrep vendored pattern):
#   scripts/cargo-vendor-gen.sh amdgpu_top 0.11.5 build/sources/amdgpu_top-0.11.5.tar.gz
#   # -> build/vendor-artifacts/amdgpu_top-0.11.5-vendor.tar.xz (523 crates,
#   #    upstream Cargo.lock); then stage into build/sources/ + chroot /sources/.

configure() {
    set -e
    # Unpack the pre-vendored crate cache + .cargo/config.toml alongside the
    # source tree so cargo can build offline. The helper wraps the payload in
    # an amdgpu_top-<ver>/ dir, so strip it (matches eza/bottom/just).
    tar xf "$IGOS_SOURCES/amdgpu_top-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build                                                     \
          --release                                                 \
          --frozen                                                  \
          --offline                                                 \
          --no-default-features                                     \
          --features tui,json,libdrm_link
}

do_install() {
    set -e
    install -Dm755 target/release/amdgpu_top "$DESTDIR/usr/bin/amdgpu_top"
}
