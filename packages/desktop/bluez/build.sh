#!/bin/bash
# bluez 5.86 — Bluetooth protocol stack
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i '4967,4968d' src/adapter.c
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --localstatedir=/var \
                --enable-library \
                --disable-manpages
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # Convenience symlink for bluetoothd
    ln -svf ../libexec/bluetooth/bluetoothd "${DESTDIR}/usr/sbin/bluetoothd"
}

post_install() {
    # Enable bluetooth service
    systemctl enable bluetooth 2>/dev/null || true
}
