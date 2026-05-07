#!/bin/bash
# bluez 5.86 — Bluetooth protocol stack
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i '4967,4968d' src/adapter.c
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --localstatedir=/var \
                --enable-library \
                --disable-manpages
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Convenience symlink for bluetoothd
    ln -svf ../libexec/bluetooth/bluetoothd "${DESTDIR}/usr/sbin/bluetoothd"
}

post_install() {
    set -e
    # Enable bluetooth service
    systemctl enable bluetooth 2>/dev/null || true
}
