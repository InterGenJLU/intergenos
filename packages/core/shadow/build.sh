#!/bin/bash
# Shadow 4.19.3
# LFS 13.0 Section 8.29
#
# DESTDIR works (autotools), but post-install commands
# (pwconv, grpconv, useradd, passwd) MUST run on the live system.

configure() {
    # Disable installation of the groups program (provided by coreutils)
    sed -i 's/groups$(EXEEXT) //' src/Makefile.in
    find man -name Makefile.in -exec sed -i 's/groups\.1 / /'   {} \;
    find man -name Makefile.in -exec sed -i 's/getspnam\.3 / /' {} \;
    find man -name Makefile.in -exec sed -i 's/passwd\.5 / /'   {} \;

    # Use YESCRYPT for password hashing, fix mail spool and PATH
    sed -e 's:#ENCRYPT_METHOD DES:ENCRYPT_METHOD YESCRYPT:' \
        -e 's:/var/spool/mail:/var/mail:'                   \
        -e '/PATH=/{s@/sbin:@@;s@/bin:@@}'                  \
        -i etc/login.defs

    # Needed because passwd location is hardcoded in some programs
    touch /usr/bin/passwd

    ./configure --sysconfdir=/etc   \
        --disable-static            \
        --with-{b,yes}crypt         \
        --without-libbsd            \
        --disable-logind            \
        --with-group-name-max-length=32
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" exec_prefix=/usr install
    make DESTDIR="$DESTDIR" -C man install-man

    # Create default directory for useradd config
    mkdir -pv "${DESTDIR}/etc/default"
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    # Enable shadow passwords
    pwconv
    grpconv

    # Set default group for new users
    useradd -D --gid 999

    # Set root password — non-interactive for automated builds
    # TODO: prompt interactively during installer, not during build
    echo "root:intergenos" | chpasswd
    echo "  Root password set (change after first boot)"

    # Create tester user for running test suites as non-root
    if ! id tester >/dev/null 2>&1; then
        useradd -m -s /bin/bash tester
        echo "  Created tester user for test suites"
    fi
}
