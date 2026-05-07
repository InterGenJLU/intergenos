#!/bin/bash
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
}

# No post_install needed — the new bash is deployed to /usr/bin/bash
# by pkg_deploy and will be used automatically by subsequent packages.
# In an interactive build, you would run: exec /usr/bin/bash --login
