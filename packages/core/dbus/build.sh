#!/bin/bash
# D-Bus 1.16.2
# LFS 13.0 Section 8.79
#
# Uses meson. DESTDIR supported.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup --prefix=/usr     \
        --libdir=/usr/lib         \
        --buildtype=release       \
        --wrap-mode=nofallback ..
}

build() {
    set -e
    cd build
    ninja -j${IGOS_JOBS}
}

check() {
    set -e
    cd build
    ninja test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Symlink machine-id for D-Bus compatibility
    mkdir -pv "${DESTDIR}/var/lib/dbus"
    ln -sfv /etc/machine-id "${DESTDIR}/var/lib/dbus"

    # Set setuid bit on dbus-daemon-launch-helper — required for the
    # system bus to spawn activated services on demand. Mode 4750 with
    # group restriction to messagebus per BLFS 13.0 dbus-1.16.2
    # canonical (only the dbus-daemon process running as messagebus
    # can invoke the helper). Must be set here because tar-based
    # deployment strips setuid bits during extraction (pkm restores
    # them from tarball metadata post-extract; see
    # pkm/installer.py:475-490). Ownership is set in post_install on
    # the live system because the PEP 706 data filter in the
    # deploy-extract path strips uid/gid.
    chmod 4750 "${DESTDIR}/usr/libexec/dbus-daemon-launch-helper"
}

post_install() {
    set -e
    # Ensure messagebus user/group exists on the live system + chown the
    # launch-helper so the 4750 mode means messagebus-group + root only,
    # not root-only. D-Bus daemon runs as messagebus user; without this
    # the system bus cannot spawn activated services.
    getent group messagebus >/dev/null || groupadd -g 18 messagebus
    getent passwd messagebus >/dev/null || useradd -d /var/run/dbus -u 18 -g messagebus -s /bin/false messagebus
    chown root:messagebus /usr/libexec/dbus-daemon-launch-helper
}
