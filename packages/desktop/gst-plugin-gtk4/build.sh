#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gst-plugin-gtk4 1.28.1 — GStreamer GTK4 video sink (gtk4paintablesink)
# from the gst-plugins-rs workspace. Rust cdylib plugin — built from
# pre-vendored Cargo crates for the offline chroot build.
#
# Why this package exists (decided 2026-08-21): GNOME Snapshot (the camera
# application) hard-requires the gtk4paintablesink GStreamer element and
# aborts at launch when it is absent. The C gstreamer 1.28.1 plugin sets
# ship it nowhere — the element lives only in gst-plugins-rs (Rust).

configure() {
    set -e
    # Extract the pre-vendored Cargo crates so the offline chroot's cargo
    # resolves every dependency without network. The workspace pins five
    # git sources besides crates.io — each one must be mapped to the vendor
    # directory below, or cargo attempts a network fetch and the build
    # fails loudly under --offline.
    tar xf "${IGOS_SOURCES}/gst-plugin-gtk4-1.28.1-vendor.tar.gz"

    mkdir -p .cargo
    cat > .cargo/config.toml <<'CARGOEOF'
[source.crates-io]
replace-with = "vendored-sources"

[source."git+https://github.com/gtk-rs/gtk-rs-core?branch=0.22"]
git = "https://github.com/gtk-rs/gtk-rs-core"
branch = "0.22"
replace-with = "vendored-sources"

[source."git+https://github.com/gtk-rs/gtk4-rs?branch=0.11"]
git = "https://github.com/gtk-rs/gtk4-rs"
branch = "0.11"
replace-with = "vendored-sources"

[source."git+https://github.com/rust-av/ffv1.git?rev=bd9eabfc14c9ad53c37b32279e276619f4390ab8"]
git = "https://github.com/rust-av/ffv1.git"
rev = "bd9eabfc14c9ad53c37b32279e276619f4390ab8"
replace-with = "vendored-sources"

[source."git+https://github.com/rust-av/flavors"]
git = "https://github.com/rust-av/flavors"
replace-with = "vendored-sources"

[source."git+https://gitlab.freedesktop.org/gstreamer/gstreamer-rs?branch=0.25"]
git = "https://gitlab.freedesktop.org/gstreamer/gstreamer-rs"
branch = "0.25"
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
CARGOEOF
}

build() {
    set -e
    export PATH="/opt/rustc/bin:$PATH"
    # Feature set (decided 2026-08-21): the GNOME-Wayland zero-copy path
    # (waylandegl + dmabuf) plus both X11 GL transports; gtk_v4_20 matches
    # the shipped gtk4 4.20.x. dmabuf requires gtk_v4_14+ and gst-video
    # v1_24+ — both satisfied by this stack. --locked pins the workspace
    # Cargo.lock; --offline fail-closes on anything the vendor set lacks.
    cargo build --package gst-plugin-gtk4 --release --locked --offline \
        --no-default-features \
        --features "waylandegl,x11egl,x11glx,dmabuf,gtk_v4_20"
}

do_install() {
    set -e
    install -vDm755 target/release/libgstgtk4.so \
        "${DESTDIR}/usr/lib/gstreamer-1.0/libgstgtk4.so"
}
