#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# wine 11.12 — new-WoW64 build, PE side cross-compiled for both widths
# (GE extra-tier wave). Grounded against the pinned tarball's own
# configure (flag list + hard-fail machinery + soname cache variables all
# read from the extracted 11.12 source, never from memory), GLFS's WoW64
# recipe, and the research doc in docs/sessions/. The mingw triplet
# compilers are auto-detected on PATH (wine's configure probes the
# standard triplet names per arch; no CROSSCC needed).
#
# THE RT-3 GATE, three layers, all in THIS commit (the binding rule):
# 1. FORCE-ENABLE WALL: every expected gaming feature is requested with
#    --with-<x>. In wine's own WINE_NOTICE_WITH/WINE_WARNING_WITH
#    machinery a requested-but-missing feature is AC_MSG_ERROR (46
#    hard-fail sites verified in this configure) — so a missing dep
#    HALTS configure instead of silently disabling the feature.
# 2. WITHOUT WALL: the intentionally-absent set (no in-tree dep, none
#    gaming-relevant: kerberos/gssapi, smartcard, video-capture, opencl,
#    camera, scanner, oss, isdn, hwloc) is pinned --without so absence
#    is a recorded decision, not an autodetect accident.
# 3. POST-CONFIGURE/POST-BUILD ASSERTS below re-check the auto-detected
#    soname surface + the bundled-FFmpeg build products; the package.yml
#    validation block asserts both PE widths on the staged artifact.

configure() {
    set -e
    mkdir -p build
    cd    build

    ../configure --prefix=/usr                \
                 --disable-tests              \
                 --enable-archs=x86_64,i386   \
                 --with-mingw                 \
                 --with-x                     \
                 --with-wayland               \
                 --with-vulkan                \
                 --with-opengl                \
                 --with-alsa                  \
                 --with-pulse                 \
                 --with-gstreamer             \
                 --with-gnutls                \
                 --with-sdl                   \
                 --with-freetype              \
                 --with-fontconfig            \
                 --with-dbus                  \
                 --with-usb                   \
                 --with-udev                  \
                 --with-pcap                  \
                 --with-cups                  \
                 --with-netapi                \
                 --with-ffmpeg                \
                 --with-xcomposite            \
                 --with-xcursor               \
                 --with-xfixes                \
                 --with-xinerama              \
                 --with-xinput                \
                 --with-xinput2               \
                 --with-xrandr                \
                 --with-xrender               \
                 --with-xshape                \
                 --with-xshm                  \
                 --with-xxf86vm               \
                 --without-capi               \
                 --without-gphoto             \
                 --without-gssapi             \
                 --without-hwloc              \
                 --without-krb5               \
                 --without-opencl             \
                 --without-oss                \
                 --without-pcsclite           \
                 --without-sane               \
                 --without-v4l2

    # RT-3 layer 3a — re-assert the auto-detected dlopen surface from the
    # stable config.log cache variables (names read from THIS pinned
    # configure: ac_cv_lib_soname_<lib>). The anchor requires the SONAME
    # SHAPE — a value containing ".so" — so an empty value, =no, or
    # =none can never read as a pass (the wave-boundary adversarial
    # verify's finding: a bareword like "no" starts with a letter and
    # slipped the earlier letter-anchored grep; the gate now matches its
    # own contract instead of relying on wine's empty-on-miss
    # convention).
    local lib soname_shape
    soname_shape="='?[A-Za-z][^']*\\.so"
    for lib in vulkan GL EGL SDL2 gnutls freetype fontconfig X11 dbus_1 cups; do
        if ! grep -qE "^ac_cv_lib_soname_${lib}${soname_shape}" config.log; then
            echo "RT-3 FAIL: wine configure did not resolve lib ${lib} to a real soname" >&2
            return 1
        fi
    done
    # The pinned-OFF set must have NO resolved soname — a future silent
    # flip (a dep appearing in the chroot and autodetecting ON) is a
    # feature-surface change and must be decided, not discovered. Same
    # shape anchor: only a REAL soname counts as resolved.
    for lib in krb5 gssapi_krb5 v4l2; do
        if grep -qE "^ac_cv_lib_soname_${lib}${soname_shape}" config.log; then
            echo "RT-3 FAIL: pinned-off lib ${lib} resolved a soname (surface changed without a decision)" >&2
            return 1
        fi
    done
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}

    # RT-3 layer 3b — the bundled FFmpeg (the 11.12 widened surface) must
    # have really compiled: its PE objects live under the build tree's
    # libs/ffmpeg. An empty tree means the media backend silently died
    # (missing nasm is the classic cause) while everything else built.
    if ! find libs/ffmpeg -name '*.o' -o -name '*.a' 2>/dev/null | grep -q .; then
        echo "RT-3 FAIL: bundled FFmpeg produced no objects (media backend silently absent)" >&2
        return 1
    fi
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" install

    # Compat symlink: new-WoW64 ships ONE wine loader; some launchers and
    # winetricks still exec wine64 (Arch's wow64 package ships the same
    # link).
    ln -sf wine "${DESTDIR}/usr/bin/wine64"

    # PE payload note: the builder has no generic strip pass (verified),
    # so the PE DLL trees under /usr/lib/wine/*-windows/ are never
    # touched by an ELF strip — running host strip on PE corrupts them.
    # If a strip pass is ever added, these trees must be exempt or
    # stripped only with the matching <triplet>-strip.
}
