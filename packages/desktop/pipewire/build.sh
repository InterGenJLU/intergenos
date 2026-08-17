#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# pipewire 1.6.0 — Multimedia processing framework
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    # Explicit feature flags. Build #5 audit found vulkan + the BlueZ HFP
    # (ModemManager) backend silently disabled because we relied on meson's
    # default "auto" detection. =enabled makes meson HALT if a dep is
    # missing rather than dropping the feature.
    # ffmpeg is a declared dep and ships in-tree (built before pipewire), but
    # was silently dropped because we never enabled its meson feature — the same
    # auto-detection class this comment warns about. =enabled builds the ffmpeg
    # SPA plugin (pw-cat FFmpeg integration) and HALTS if ffmpeg ever goes
    # missing. (silent-loss audit 2026-06-25.)
    # libcamera + echo-cancel-webrtc =enabled (capture wave): the libcamera
    # SPA plugin is how MIPI/IPU built-in cameras reach applications, and
    # the WebRTC engine is what makes module-echo-cancel real (only the
    # null AEC shipped before). Both are the same silent-auto class this
    # comment block exists for — =enabled HALTS if either dep goes missing.
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dsession-managers=[] \
          -Dtests=disabled \
          -Dman=disabled \
          -Dvulkan=enabled \
          -Dffmpeg=enabled \
          -Dbluez5-backend-native-mm=enabled \
          -Dlibcamera=enabled \
          -Decho-cancel-webrtc=enabled
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
