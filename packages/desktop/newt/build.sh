#!/bin/bash
# newt 0.52.25 — Text mode windowing toolkit
# BLFS 13.0

configure() {
    # BLFS: disable static library installation
    sed -e '/install -m 644 $(LIBNEWT)/ s/^/#/' \
        -e '/$(LIBNEWT):/,/rv/ s/^/#/'          \
        -e 's/$(LIBNEWT)/$(LIBNEWTSH)/g'        \
        -i Makefile.in

    ./configure --prefix=/usr \
                --without-gpm-support
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
