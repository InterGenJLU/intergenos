#!/bin/bash
# polkit 127 — PolicyKit authorization toolkit
# BLFS 13.0

configure() {
    # Create polkitd system user/group
    groupadd -fg 27 polkitd 2>/dev/null || true
    useradd -c "PolicyKit Daemon Owner" -d /etc/polkit-1 \
            -u 27 -g polkitd -s /bin/false polkitd 2>/dev/null || true

    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dtests=false \
          -Dman=false \
          -Dsession_tracking=logind
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
