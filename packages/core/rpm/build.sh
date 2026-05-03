#!/bin/bash
# RPM 4.18.2 — RPM package manager (build-target: rpm2cpio + librpm)
# The shim-signed package needs rpm2cpio to extract the MS-signed shim
# binary from Fedora's RPM archive. We build the minimal RPM toolchain:
# librpm + librpmio + rpm-common + rpm2cpio binary.
# Upstream: https://rpm.org/

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --localstatedir=/var \
                --disable-static \
                --disable-plugins \
                --without-lua \
                --without-cap \
                --without-acl
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # rpm test suite requires system-level rpm database;
    # skip in chroot build
    :
}

do_install() {
    make DESTDIR="${DESTDIR}" install

    # Remove components not needed for shim-signed's rpm2cpio use:
    # rpmbuild, rpmdb, rpmkeys, rpmsign, rpmspec, rpmquery are unused.
    rm -f "${DESTDIR}/usr/bin/rpm"
    rm -f "${DESTDIR}/usr/bin/rpmbuild"
    rm -f "${DESTDIR}/usr/bin/rpmdb"
    rm -f "${DESTDIR}/usr/bin/rpmkeys"
    rm -f "${DESTDIR}/usr/bin/rpmsign"
    rm -f "${DESTDIR}/usr/bin/rpmspec"
    rm -f "${DESTDIR}/usr/bin/rpmquery"
    rm -f "${DESTDIR}/usr/bin/rpmverify"
}
