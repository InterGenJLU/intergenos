#!/bin/bash
# Shadow 4.19.3
# LFS 13.0 Section 8.27

configure() {
    # Disable installation of the groups program (provided by coreutils)
    sed -i 's/groups$(EXEEXT) //' src/Makefile.in
    find man -name Makefile.in -exec sed -i 's/groups\.1 / /'   {} \;
    find man -name Makefile.in -exec sed -i 's/getspnam\.3 / /' {} \;
    find man -name Makefile.in -exec sed -i 's/passwd\.5 / /'   {} \;

    # Use SHA-512 instead of default DES for password hashing
    sed -e 's:#ENCRYPT_METHOD DES:ENCRYPT_METHOD YESCRYPT:' \
        -e 's:/var/spool/mail:/var/mail:'                   \
        -e '/PATH=/{s@/sbin:@@;s@/bin:@@}'                  \
        -i etc/login.defs

    touch /usr/bin/passwd

    ./configure --sysconfdir=/etc   \
        --disable-static            \
        --with-{b,yes}crypt         \
        --without-libbsd            \
        --with-group-name-max-length=32
}

build() {
    make -j${IGOS_JOBS}
}

install() {
    make exec_prefix=/usr install
    make -C man install-man

    # Configure useradd defaults
    mkdir -p /etc/default
    useradd -D --gid 999
}
