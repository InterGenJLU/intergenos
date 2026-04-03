#!/bin/bash
# unifdef 2.12 — Conditional compilation directive remover
# BLFS 13.0

configure() {
    # Fix C23 keyword conflict with GCC 15
    sed -i 's/constexpr/unifdef_&/g' unifdef.c
    # Fix symlink creation during install
    sed -i 's/ln -s/ln -sf/' Makefile
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make prefix=/usr DESTDIR="$DESTDIR" install
}
