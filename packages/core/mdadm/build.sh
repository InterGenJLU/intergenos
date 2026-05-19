#!/bin/bash
# mdadm 4.4 — MD software RAID admin (BLFS 13.0 / postlfs/mdadm).
# T0-3 sub-cluster 1 — installer runtime dep (RAID detection via os-prober).

configure() {
    set -e
    # BLFS gcc-15 patch: add __attribute__((nonstring)) annotation to the
    # signature array declaration in platform-intel.h so gcc-15's tightened
    # string-warning class does not error the build.
    sed -i 's@^\(\s*char signature\)\[\(.*\)\];@\1[\2] __attribute__ ((nonstring));@' \
        platform-intel.h
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" BINDIR=/usr/sbin install
}
