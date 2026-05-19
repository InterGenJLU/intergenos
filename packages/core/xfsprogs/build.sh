#!/bin/bash
# xfsprogs 7.0.0 — XFS filesystem utilities (BLFS 13.0 / postlfs/xfsprogs).
# T0-3 sub-cluster 1 — installer runtime dep (mkfs.xfs for XFS-rooted installs).

configure() {
    set -e
    # xfsprogs uses its own configure system invoked via top-level Makefile,
    # but BLFS calls the bare-make invocation with LOCAL_CONFIGURE_OPTIONS
    # which propagates --localstatedir to the internal configure step. No
    # explicit ./configure call needed at this layer.
    :
}

build() {
    set -e
    # BLFS-recommended env vars:
    #   DEBUG=-DNDEBUG          — strip debug-assert overhead
    #   INSTALL_USER/GROUP=root — DESTDIR-staged ownership
    #   LOCAL_CONFIGURE_OPTIONS — propagate --localstatedir=/var to xfsprogs'
    #                             internal configure step (state files in /var
    #                             rather than /usr/var).
    make -j${IGOS_JOBS} \
         DEBUG=-DNDEBUG \
         INSTALL_USER=root \
         INSTALL_GROUP=root \
         LOCAL_CONFIGURE_OPTIONS="--localstatedir=/var"
}

do_install() {
    set -e
    # xfsprogs splits install (binaries+man) from install-dev (headers+
    # static-archives); both required for downstream consumers.
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" install-dev
    # Drop static archives — BLFS-canonical cleanup. libhandle.a is the only
    # one xfsprogs ships and we don't link statically against it anywhere.
    rm -f "$DESTDIR/usr/lib/libhandle.a"
}
