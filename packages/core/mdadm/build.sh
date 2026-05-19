#!/bin/bash
# mdadm 4.4 — MD software RAID admin (BLFS 13.0 / postlfs/mdadm).
# T0-3 sub-cluster 1 — installer runtime dep (RAID detection via os-prober).

configure() {
    set -e
    # BLFS-canonical gcc-15 patch: append __attribute__((nonstring)) to the
    # signature array declaration in platform-intel.h:27 so gcc-15's
    # tightened string-warning class does not error the build.
    #
    # Pattern MUST match the actual decl in the tarball — which is
    # `__u8 signature[4];`, NOT `char signature[N]`. (An earlier revision
    # of this build.sh used `char signature\[` and produced a silent
    # no-op; caught 2026-05-19 via D-009 item 1 programmatic research
    # against the extracted tarball + BLFS-verbatim sed expression.)
    sed -e "s/__u8 signature\[4\]/& __attribute__ ((nonstring))/" \
        -i platform-intel.h
    # Guard against silent no-op regressing again: fail the build if the
    # annotation didn't land.
    grep -q '__u8 signature\[4\] __attribute__ ((nonstring))' platform-intel.h
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" BINDIR=/usr/sbin install
}
