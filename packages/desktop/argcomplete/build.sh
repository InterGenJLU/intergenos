#!/bin/bash
# argcomplete 3.6.3 — Python tab-completion for argparse
# Required by mutter build tools

configure() {
    set -e
    :
}

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --no-index --find-links dist --no-user \
         --root="$DESTDIR" --no-deps argcomplete
}
