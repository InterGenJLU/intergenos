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
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=true \
          -Dman=true \
          -Dsession_tracking=logind \
          -Dos_type=lfs
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Set setuid bits — pkexec and polkit-agent-helper-1 need setuid for
    # privilege escalation. Must be set here because tar-based deployment
    # strips setuid bits.
    chmod 4755 "${DESTDIR}/usr/bin/pkexec"
    # polkit-agent-helper-1 uses 4711 (execute-only, not readable)
    chmod 4711 "${DESTDIR}/usr/libexec/polkit-agent-helper-1"
}
