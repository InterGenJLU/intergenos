#!/bin/bash
# keyutils 1.6.3 — Linux key management utilities
# BLFS 13.0

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make NO_ARLIB=1 \
         LIBDIR=/usr/lib \
         BINDIR=/usr/bin \
         SBINDIR=/usr/sbin \
         DESTDIR="$DESTDIR" install

    # keyutils Makefile creates absolute-target symlinks in /usr/lib
    # (e.g., libkeyutils.so → /usr/lib/libkeyutils.so.1). The InterGenOS
    # build framework correctly rejects packages whose staged-path
    # resolves outside the staging root as a security violation. Convert
    # absolute-target symlinks under $DESTDIR/usr/lib to relative-target
    # so the staged paths stay within the staging tree.
    if [ -d "$DESTDIR/usr/lib" ]; then
        for sl in "$DESTDIR/usr/lib"/*; do
            if [ -L "$sl" ]; then
                target=$(readlink "$sl")
                if [[ "$target" = /* ]]; then
                    # Convert /usr/lib/libfoo.so.1 → libfoo.so.1 (relative)
                    rel_target=$(basename "$target")
                    ln -sf "$rel_target" "$sl"
                fi
            fi
        done
    fi
}
