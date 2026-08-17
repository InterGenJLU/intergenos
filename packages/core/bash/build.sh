#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Bash 5.3
# LFS 13.0 Section 8.37

configure() {
    set -e
    ./configure --prefix=/usr             \
        --without-bash-malloc             \
        --with-installed-readline         \
        --docdir=/usr/share/doc/bash-5.3
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    chown -R tester .
    LC_ALL=C.UTF-8 su -s /usr/bin/expect tester << "EOF"
set timeout -1
spawn make tests
expect eof
lassign [wait] _ _ _ value
exit $value
EOF
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # /bin/sh and /usr/bin/sh symlinks → bash. Every #!/bin/sh script
    # on the system (grub-mkconfig, package post-install hooks, sysadmin
    # scripts) depends on these existing. On the build chroot these
    # are created by chroot-setup / temp-tools setup directly in $IGOS
    # and never packaged — same class shape as the /lib64 dynamic-
    # linker gap fixed at 0abd0de3. Surfaced 2026-05-26 install attempt
    # #9: grub-mkconfig died with "/bin/sh: bad interpreter: No such
    # file or directory" because /bin/sh didn't exist on the fresh
    # pkm-install target. Bash is the right semantic home (it IS the
    # shell that /bin/sh resolves to on this system; the symlink
    # belongs with the binary it points at).
    install -dm755 "${DESTDIR}/bin" "${DESTDIR}/usr/bin"
    ln -sfv bash "${DESTDIR}/bin/sh"
    ln -sfv bash "${DESTDIR}/usr/bin/sh"
}

# No post_install needed — the new bash is deployed to /usr/bin/bash
# by pkg_deploy and will be used automatically by subsequent packages.
# In an interactive build, you would run: exec /usr/bin/bash --login
