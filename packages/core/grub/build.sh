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

install() {
    make install
    mv -v /etc/bash_completion.d/grub /usr/share/bash-completion/completions
}
