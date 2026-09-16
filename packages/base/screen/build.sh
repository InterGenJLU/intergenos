#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# GNU Screen 5.0.1 — Terminal multiplexer
# BLFS 13.0

configure() {
    set -e
    # Fix info page build issue
    sed 's/\([a-z]\)@opensuse/\1@@opensuse/' -i doc/screen.texinfo

    # Decided 2026-09-16: no global socket directory. With --enable-socket-dir
    # the program keeps its sockets under a single root-owned /run/screen and
    # needs privilege to create that directory and each user's subdirectory in
    # it — measured: an unprivileged run refuses with "Cannot make directory
    # '/run/screen': Permission denied", and with the directory pre-created the
    # program demands a specific mode computed from its own effective ids
    # (screen.c, the SOCKET_DIR branch). Without the flag, upstream's default
    # applies: each user's sockets live in $HOME/.screen, created 0700 in that
    # user's own context, and no privilege is involved at any point. That is
    # what lets the setuid bit below be dropped rather than narrowed.
    #
    # The cost, stated: sharing one session between two DIFFERENT users
    # (screen -x across accounts) required the setuid-root binary and the shared
    # directory, and is gone with them. Multiple sessions per user, detach and
    # reattach are unaffected.
    ./configure --prefix=/usr                   \
                --infodir=/usr/share/info       \
                --mandir=/usr/share/man         \
                --enable-pam                    \
                --with-pty-group=5              \
                --with-system_screenrc=/etc/screenrc

    sed -i -e "s%/usr/local/etc/screenrc%/etc/screenrc%" {etc,doc}/*
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # Create /etc in DESTDIR before make install — the Makefile tries to
    # copy screenrc there during install and fails if it doesn't exist
    install -v -d -m755 "${DESTDIR}/etc"

    make DESTDIR="$DESTDIR" install

    # Decided 2026-09-16: the program ships unprivileged. Upstream's Makefile
    # install rule runs `chown root ... && chmod 4755` on the versioned binary
    # (Makefile.in install_bin), so the setuid bit arrives from upstream rather
    # than from a line in this recipe and has to be undone here after it.
    #
    # What the bit bought on this system, measured: nothing that is still
    # needed. This build has no utmp support at all — upstream's --enable-utmp
    # defaults to no and this recipe does not pass it, and the shipped binary
    # references no utmp API symbol — so the program never writes the user
    # accounting database and needs no grant for it. Its remaining privileged
    # use was the global socket directory, which the configure change above
    # removes. Detach, reattach and multiple sessions per user were exercised
    # unprivileged with per-user sockets before this change was written.
    #
    # Fail-closed: assert the bit is gone rather than trusting the chmod, so an
    # upstream install-rule change cannot ship a setuid binary under a comment
    # that says it does not.
    chmod 0755 "${DESTDIR}/usr/bin/screen-${version}"
    if [ -u "${DESTDIR}/usr/bin/screen-${version}" ] || \
       [ -g "${DESTDIR}/usr/bin/screen-${version}" ]; then
        echo "screen: /usr/bin/screen-${version} still carries a setuid or setgid bit — halting" >&2
        exit 1
    fi

    install -v -m644 etc/etcscreenrc "${DESTDIR}/etc/screenrc"
}
