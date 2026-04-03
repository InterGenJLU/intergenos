#!/bin/bash
# wireplumber 0.5.13 — PipeWire session manager
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dsystem-lua=true \
          -Dtests=disabled \
          -Ddoc=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    # Resolve PulseAudio/PipeWire conflict — PipeWire replaces PulseAudio
    # Remove PA autostart entries that would conflict
    rm -vf /etc/xdg/autostart/pulseaudio.desktop 2>/dev/null || true
    rm -vf /etc/xdg/Xwayland-session.d/00-pulseaudio-x11 2>/dev/null || true

    # Disable PA autospawn so it doesn't fight PipeWire
    if [ -f /etc/pulse/client.conf ]; then
        sed -e '$a autospawn = no' -i /etc/pulse/client.conf
    fi

    # Enable PipeWire and WirePlumber for all user sessions
    systemctl enable --global pipewire.socket 2>/dev/null || true
    systemctl enable --global pipewire-pulse.socket 2>/dev/null || true
    systemctl enable --global wireplumber 2>/dev/null || true
}
