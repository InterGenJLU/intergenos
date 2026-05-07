#!/bin/bash
# ladspa-sdk 1.17 — Linux Audio Developer's Simple Plugin API SDK
#
# Provides:
#   - /usr/include/ladspa.h          (the LADSPA C API header)
#   - /usr/bin/{analyseplugin,applyplugin,listplugins}
#   - /usr/lib/ladspa/{amp,delay,filter,noise,sine}.so  (5 example plugins)
#
# Upstream ships a hand-written Makefile in src/ that hardcodes /usr paths
# via INSTALL_*_DIR variables and does not honor DESTDIR. We override the
# variables on the install line so DESTDIR-style staging works correctly.
#
# The example tools (analyseplugin/applyplugin/listplugins) link libsndfile
# for reading/writing wave files.

configure() {
    set -e
    # No configure step — upstream Makefile is plain.
    :
}

build() {
    set -e
    cd src
    make -j${IGOS_JOBS} targets
}

do_install() {
    set -e
    cd src
    install -d "$DESTDIR/usr/lib/ladspa"
    install -d "$DESTDIR/usr/include"
    install -d "$DESTDIR/usr/bin"
    make INSTALL_PLUGINS_DIR="$DESTDIR/usr/lib/ladspa/" \
         INSTALL_INCLUDE_DIR="$DESTDIR/usr/include/"    \
         INSTALL_BINARY_DIR="$DESTDIR/usr/bin/"         \
         install
}
