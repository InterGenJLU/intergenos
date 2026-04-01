#!/bin/bash
# Bash 5.3
# LFS 13.0 Section 8.37

configure() {
    ./configure --prefix=/usr             \
        --without-bash-malloc             \
        --with-installed-readline         \
        --docdir=/usr/share/doc/bash-5.3
}

build() {
    make -j${IGOS_JOBS}
}

check() {
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
    make DESTDIR="$DESTDIR" install
}

# Post-install: replace running shell with newly installed bash
post_install() {
    exec /usr/bin/bash --login
}
