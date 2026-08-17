#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# nv-codec-headers 13.1.15.0 — the FFmpeg-maintained copy of NVIDIA's codec API
# headers, published as `ffnvcodec` via pkg-config.
#
# WHAT THIS SHIPS, EXACTLY: five header files under /usr/include/ffnvcodec and
# one pkg-config file. There is nothing to compile and no library. The upstream
# Makefile's `all` target does one job — substitute PREFIX into ffnvcodec.pc.in
# to produce ffnvcodec.pc.
#
# WHY NO NVIDIA BINARY IS INVOLVED: ffmpeg's NVENC/NVDEC paths dlopen the
# driver's own libraries (libcuda.so.1, libnvidia-encode.so.1, libnvcuvid.so.1)
# at RUNTIME. These headers only describe that interface, so building ffmpeg
# against them requires no CUDA toolkit, no nvcc, and no redistributable-NVIDIA
# question at build time. That is the whole reason this package can exist in
# the tree at all while cuda-toolkit has to be a download-helper.
#
# DRIVER-VERSION FLOOR, stated because it is a real user-facing limit and not
# visible anywhere else in the tree: upstream's README pairs this release with
# Video Codec SDK 13.1.15 and states a minimum NVIDIA driver of 610.0. A system
# with an older driver does not get a silently degraded encoder — ffmpeg's
# nvenc initialisation checks the API version against the loaded driver and
# fails with a named error. The failure is loud, which is the property that
# matters here, but the floor is worth knowing before assuming NVENC will work
# on any NVIDIA box.

configure() { : ; }

build() {
    set -e
    # PREFIX must match the install-time PREFIX: it is baked into ffnvcodec.pc
    # as the `prefix=` line, which is what tells ffmpeg's configure where the
    # headers are. Passing it here but not to install (or the reverse) yields a
    # .pc file pointing at /usr/local while the headers sit in /usr.
    make PREFIX=/usr
}

do_install() {
    set -e
    make PREFIX=/usr DESTDIR="$DESTDIR" install
}
