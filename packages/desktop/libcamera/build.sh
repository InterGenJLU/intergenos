#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libcamera 0.7.2 — Camera support library for complex camera pipelines
#
# The capability layer for built-in laptop cameras that are NOT plain UVC
# devices (MIPI sensors behind Intel IPU3-class and simple-pipeline ISPs):
# without libcamera those cameras enumerate but produce no frames. PipeWire
# is rebuilt against it in the same wave (-Dlibcamera=enabled), which is how
# camera frames reach GNOME/applications.

configure() {
    set -e
    mkdir -p build
    cd    build

    # Explicit flags throughout (the silent-auto class). Pipelines/IPAs are
    # the x86 set: ipu3 (Intel IPU3 MIPI), simple (software-ISP MIPI
    # class), uvcvideo (USB class-compliant), vimc (virtual test pipeline —
    # keeps the stack exercisable in a VM with no camera hardware). The
    # ARM-platform pipelines (rkisp1, mali-c55, rpi/*) are excluded by
    # architecture, matching upstream's own 'auto' arch mapping.
    # qcam is a Qt diagnostic GUI (no Qt stack ships — same scoping as
    # v4l-utils' qv4l2); pycamera is upstream-experimental AND its pybind11
    # dependency lives in the compute tier, which builds after desktop —
    # revisit only if a consumer materializes. libunwind is a redundant
    # backtrace provider (libdw from elfutils serves that role and is
    # enabled explicitly). tracing needs lttng-ust (not in tree; tracing is
    # a developer diagnostic, not a capture capability). softisp-gpu stays
    # auto deliberately: the CPU software-ISP path is the capability; the
    # GPU acceleration is an optimization detected from the GLES stack.
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dpipelines=ipu3,simple,uvcvideo,vimc \
          -Dipas=ipu3,simple,vimc \
          -Dv4l2=enabled      \
          -Dgstreamer=enabled \
          -Dcam=enabled       \
          -Dcam-jpeg=enabled  \
          -Dcam-output-kms=disabled \
          -Dcam-output-sdl2=disabled \
          -Dqcam=disabled     \
          -Dpycamera=disabled \
          -Dlc-compliance=disabled \
          -Ddocumentation=disabled \
          -Dtracing=disabled  \
          -Dlibdw=enabled     \
          -Dlibunwind=disabled \
          -Dudev=enabled      \
          -Dtest=false
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
