#!/bin/bash
# nss-mdns 0.15.1 — NSS plugin for mDNS hostname resolution
# Integrates Avahi with glibc NSS for .local hostname resolution

configure() {
    ./configure --prefix=/usr    \
                --sysconfdir=/etc \
                --localstatedir=/var \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}

post_install() {
    # Add mdns to nsswitch.conf hosts line if not already present
    if [ -f /etc/nsswitch.conf ]; then
        if ! grep -q 'mdns' /etc/nsswitch.conf; then
            sed -i 's/^hosts:.*/& mdns4_minimal [NOTFOUND=return]/' /etc/nsswitch.conf
        fi
    fi
}
