#!/bin/bash
# atop 2.12.1 — Advanced system and process monitor
# From upstream (not in BLFS)

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    # Upstream Makefile sets SYSDPATH=/lib/systemd/system which creates a bare
    # lib/ dir in DESTDIR — on LFS systems /lib is a symlink to usr/lib, and
    # deploying a real lib/ directory over that symlink kills the dynamic linker.
    # Override to the correct FHS/systemd path.
    make DESTDIR="$DESTDIR" SYSDPATH=/usr/lib/systemd/system install
}
