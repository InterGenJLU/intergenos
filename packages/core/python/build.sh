#!/bin/bash
# Python 3.14.3
# LFS 13.0 Section 8.52

configure() {
    ./configure --prefix=/usr        \
        --enable-shared              \
        --with-system-expat          \
        --enable-optimizations
}

build() {
    make -j${IGOS_JOBS}
}

install() {
    make install

    cat > /etc/pip.conf << PIPEOF
[install]
root = /usr
compile = no

[global]
break-system-packages = true

[freeze]
user = false
user-site = false
PIPEOF

    install -v -dm755 /usr/share/doc/python-3.14.3/html

    tar --no-same-owner \
        -xvf $IGOS_SOURCES/python-3.14.3-docs-html.tar.bz2
    cp -R --no-preserve=mode python-3.14.3-docs-html/* \
        /usr/share/doc/python-3.14.3/html
}
