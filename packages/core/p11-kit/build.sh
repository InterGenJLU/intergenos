#!/bin/bash
# p11-kit 0.26.2 — PKCS#11 module loading library
# BLFS 13.0

configure() {
    set -e
    # Prepare distribution-specific anchor hook
    sed '20,$ d' -i trust/trust-extract-compat

    cat >> trust/trust-extract-compat << "EOF"
# Copy existing anchor modifications to /etc/ssl/local
/usr/libexec/make-ca/copy-trust-modifications

# Update trust stores
/usr/sbin/make-ca -r
EOF

    mkdir -p p11-build
    cd    p11-build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -D trust_paths=/etc/pki/anchors
}

build() {
    set -e
    cd p11-build
    ninja
}

check() {
    set -e
    cd p11-build
    ninja test || true
}

do_install() {
    set -e
    cd p11-build
    DESTDIR="$DESTDIR" ninja install

    # Create update-ca-certificates symlink
    ln -sfv /usr/libexec/p11-kit/trust-extract-compat \
            "${DESTDIR}/usr/bin/update-ca-certificates"

    # Make p11-kit trust module available to NSS
    ln -sfv ./pkcs11/p11-kit-trust.so "${DESTDIR}/usr/lib/libnssckbi.so"
}
