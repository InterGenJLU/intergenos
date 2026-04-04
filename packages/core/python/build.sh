#!/bin/bash
# Python 3.14.3
# LFS 13.0 Section 8.52

configure() {
    ./configure --prefix=/usr             \
        --enable-shared                   \
        --with-system-expat               \
        --enable-optimizations            \
        --without-static-libpython
}

build() {
    # Exclude test_generators from PGO profiling — it fails under PGO
    # instrumentation in KVM/chroot due to signal delivery timing changes.
    # 1 of 46 PGO tests excluded; negligible impact on optimization quality.
    make PROFILE_TASK="-m test --pgo -x test_generators --timeout 120" \
        -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install

    mkdir -pv "${DESTDIR}/etc"
    cat > "${DESTDIR}/etc/pip.conf" << PIPEOF
[install]
root = /usr
compile = no

[global]
root-user-action = ignore
disable-pip-version-check = true
break-system-packages = true

[freeze]
user = false
user-site = false
PIPEOF

    install -v -dm755 "${DESTDIR}/usr/share/doc/python-3.14.3/html"

    tar --no-same-owner \
        -xvf $IGOS_SOURCES/python-3.14.3-docs-html.tar.bz2
    cp -R --no-preserve=mode python-3.14.3-docs-html/* \
        "${DESTDIR}/usr/share/doc/python-3.14.3/html"
}
