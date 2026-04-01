#!/bin/bash
# GRUB 2.14
# LFS 13.0 Section 8.64

configure() {
    # Unset any GRUB-related environment variables
    unset {C,CXX,CPP,LD}FLAGS

    ./configure --prefix=/usr          \
        --sysconfdir=/etc              \
        --disable-efiemu               \
        --disable-werror
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
    mkdir -pv "${DESTDIR}/usr/share/bash-completion/completions"
    mv -v "${DESTDIR}/etc/bash_completion.d/grub" "${DESTDIR}/usr/share/bash-completion/completions"
}
