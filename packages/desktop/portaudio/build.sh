#!/bin/bash
# portaudio v19.7.0 (pa_stable_v190700_20210406) — Cross-platform portable audio I/O
#
# Notes
# - The v19.7.0 autotools configure script supports ALSA, JACK, and OSS host APIs
#   only. It has no PulseAudio backend flag; on InterGenOS we route audio through
#   ALSA (PipeWire's pipewire-alsa shim or the native ALSA path), so --with-alsa
#   is sufficient. JACK is explicitly disabled because we do not ship a jack
#   package, and OSS is disabled because we are an ALSA/PipeWire system.
# - Tarball extracts to a "portaudio" directory (no version suffix); pkm
#   handles the cd into the source dir.

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --with-alsa \
                --without-jack \
                --without-oss
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
