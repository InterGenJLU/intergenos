#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# webrtc-audio-processing 2.1 — WebRTC audio processing library
#
# The real acoustic-echo-cancellation engine behind PipeWire's
# module-echo-cancel. Before this package only the null AEC stub shipped
# (libspa-aec-null.so), so echo cancellation was structurally a no-op.
# PipeWire's meson probes webrtc-audio-processing-2 first — this package
# provides exactly that .pc; pipewire is rebuilt against it in the same
# wave with -Decho-cancel-webrtc=enabled.

configure() {
    set -e

    # Modern-toolchain compatibility: the two declared patches are
    # UPSTREAM master commits applied verbatim to the 2.1 tarball —
    # c8896801 (absl-202508: version-gated absl_nullable/absl_nonnull
    # macros replace the removed absl::Nullable/Nonnull aliases) and
    # e9c78dc4 (gcc-15: <cstdint> in trace_event.h +
    # multi_channel_content_detector.h). Full-ninja proven clean on this
    # toolchain in-chroot before adoption (ge9b-10 first build,
    # 2026-07-29). The builder's patch phase applies declared patches
    # itself; do NOT patch again here.

    mkdir -p build
    cd    build

    # Upstream carries an abseil-cpp meson wrap as a network fallback; the
    # chroot has no network, and our abseil-cpp (>= 20240722, satisfying
    # the version floor) resolves via pkg-config first, so the wrap never
    # fires. inline-sse=true is the upstream default and correct for the
    # x86_64 baseline (SSE2 is architectural).
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dinline-sse=true
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
