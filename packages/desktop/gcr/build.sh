#!/bin/bash
# gcr 3.41.2 — GLib crypto and PKCS#11 framework (GTK3 version)
# BLFS 13.0

configure() {
    set -e
    # ssh_agent=true: GNOME Keyring's SSH agent UI for managing SSH keys.
    # Security-aligned feature for a security-aligned distro.
    # gcr 3.41.2 has a meson.build bug: lines 55-56 call .path() on a
    # find_program() result even when required=false. The bug is fixed
    # upstream in gcr 4.x. We work around it here by ensuring openssh IS
    # installed at build time (added to dependencies.build) so the
    # find_program() succeeds for both ssh-add and ssh-agent.
    # The historical sed-removal of those lines was a pre-openssh-dep
    # workaround and is no longer needed.

    # BLFS: fix schema path
    sed -i 's:"/desktop:"/org:' schema/*.xml

    mkdir -p build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -Dgtk_doc=false       \
          -Dssh_agent=true
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
