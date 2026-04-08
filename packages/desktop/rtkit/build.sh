#!/bin/bash
# rtkit 0.13 — RealtimeKit D-Bus service for real-time scheduling
# Required by PipeWire and GNOME Shell for real-time thread scheduling

configure() {
    mkdir build
    cd    build

    meson setup ..                \
          --prefix=/usr           \
          --buildtype=release     \
          -Dinstalled_tests=false \
          -Dlibsystemd=enabled
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
    # Create rtkit system user for privilege dropping
    if ! getent group rtkit > /dev/null 2>&1; then
        groupadd -fg 133 rtkit
    fi
    if ! getent passwd rtkit > /dev/null 2>&1; then
        useradd -c "RealtimeKit Daemon" -d /proc -u 133 -g rtkit -s /bin/false rtkit
    fi

    # Enable the service
    systemctl enable rtkit-daemon.service 2>/dev/null || true
}
