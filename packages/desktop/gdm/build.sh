#!/bin/bash
# gdm 49.2 — GNOME Display Manager
# BLFS 13.0

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # Create gdm system user/group
    groupadd -g 21 gdm 2>/dev/null || true
    useradd -c "GDM Daemon Owner" -d /var/lib/gdm \
            -u 21 -g gdm -s /bin/false gdm 2>/dev/null || true
    passwd -ql gdm 2>/dev/null || true

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgdm-xsession=true \
          -Drun-dir=/run/gdm \
          -Ddefault-pam-config=lfs
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

    # Ship a systemd preset that enables gdm.service by default. Real-distro
    # convention: systemctl preset-all consumes /usr/lib/systemd/system-preset/
    # at install time and creates the /etc/systemd/system/display-manager.service
    # symlink to gdm.service, making graphical.target reachable.
    install -Dm644 "$BUILD_DIR/90-gdm.preset" \
                   "$DESTDIR/usr/lib/systemd/system-preset/90-gdm.preset"
}

post_install() {
    set -e
    # /var/lib/gdm is gdm's state dir (X authority, dconf-profile state, etc.).
    # gdm refuses to start cleanly if this is absent or owned by root.
    install -dm700 -o gdm -g gdm /var/lib/gdm 2>/dev/null || true

    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
    update-desktop-database /usr/share/applications 2>/dev/null || true
}
