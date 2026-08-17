#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# R 4.6.1 — environment for statistical computing and graphics
# BLFS ships R; InterGenOS core-tier language toolchain (RC001 unlock lane).
#
# Standard autotools build. --enable-R-shlib builds libR.so (required for
# Rscript embedding and the shared-R consumers). --with-x=no / --with-tcltk=no
# keep the core build free of desktop-tier dependencies (the X11/Tk graphics
# devices are a desktop concern, not part of the language runtime). BLAS/LAPACK
# use R's bundled reference implementation — no external openblas/atlas dep at
# the core tier. R requires a Fortran compiler; gcc-core provides gfortran.

configure() {
    set -e
    # LIBnn=lib: R's configure defaults the library subdir to lib64 on 64-bit
    # hosts, but the native libdir here is /usr/lib (/usr/lib64 holds only
    # multilib artifacts). Without the pin R lands at /usr/lib64/R and the
    # archive-seal gate rejects the declared /usr/lib/R (caught at the first
    # build-verify, 2026-07-23).
    ./configure --prefix=/usr \
        LIBnn=lib \
        --enable-R-shlib \
        --with-x=no \
        --with-tcltk=no \
        --with-blas \
        --with-lapack \
        --enable-memory-profiling \
        rdocdir=/usr/share/doc/R-"${PKG_VERSION}"
}

build() {
    set -e
    make -j"$(nproc)"
}

check() {
    set -e
    # A minimal evaluation proves the interpreter + shared libR link work; the
    # full `make check` regression suite is a build-verify concern, not authoring.
    bin/Rscript -e 'cat(sprintf("R %s OK\n", getRversion()))'
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" install
}
