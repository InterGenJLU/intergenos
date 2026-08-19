#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# wireplumber 0.5.13 — PipeWire session manager
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release       \
          --wrap-mode=nofallback    \
          -Dsystem-lua=true ..
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

post_install() {
    set -e
    # The PulseAudio-conflict file removals + client.conf autospawn edit moved
    # to the pulseaudio recipe's staging (hook-contract wave: no cross-package
    # mutation from hooks).
    # Enable the audio stack's user units for every user account.
    #
    # All three unmasked. `systemctl --global enable` is an offline file
    # operation into /etc/systemd/user and needs no running manager: measured
    # 2026-08-19 in a chroot built from this systemd 259.1, a --global enable
    # of a PRESENT user unit returns 0 and writes the symlink, a repeat call
    # returns 0 unchanged, and the only reachable failure is a unit that does
    # not exist, which returns 1.
    #
    # The two pipewire units are owned by the pipewire package, which this
    # recipe declares as both a build and a runtime dependency — so pipewire
    # is built first in the chroot and installed first by the dependency-
    # derived install order, and its units are in place before this hook runs.
    # A non-zero here therefore means an ordering or packaging break, which
    # must surface rather than ship a machine with no audio routing.
    #
    # wireplumber.service is spelled with its suffix: the shipped unit is
    # wireplumber.service and the bare name only worked because systemd
    # resolves a missing suffix.
    systemctl enable --global pipewire.socket
    systemctl enable --global pipewire-pulse.socket
    systemctl enable --global wireplumber.service
}
