#!/bin/bash
# nodejs 22.22.0 — JavaScript runtime built on V8
# BLFS 13.0

configure() {
    # Apply Python 3.14 build fix
    patch -Np1 -i "${IGOS_SOURCES}/node-v22.22.0-python_build_fix-1.patch"

    # Use system libraries instead of bundled copies
    ./configure --prefix=/usr          \
                --shared-brotli        \
                --shared-cares         \
                --shared-libuv         \
                --shared-openssl       \
                --shared-nghttp2       \
                --shared-zlib          \
                --with-intl=system-icu
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # ~10 of 4600+ tests known to fail per BLFS
    make test-only || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}

post_install() {
    ln -sf node /usr/share/doc/node-${PKG_VERSION}
}
