#!/bin/bash
# cracklib 2.10.3 — Password checking library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --with-default-dict=/usr/lib/cracklib/pw_dict
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}

post_install() {
    set -e
    # Create the cracklib dictionary — without this, password checking is broken
    install -v -m755 -d /usr/lib/cracklib

    # Use the words file from the system if available, otherwise create minimal one
    if [ -f /usr/share/dict/words ]; then
        create-cracklib-dict /usr/share/dict/words
    else
        # Create a minimal word list
        echo "password" > /tmp/cracklib-words
        echo "$(hostname)" >> /tmp/cracklib-words
        create-cracklib-dict /tmp/cracklib-words
        rm -f /tmp/cracklib-words
    fi
}
