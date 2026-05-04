#!/bin/bash
# portmidi 2.0.7 — Portable real-time MIDI I/O library
# Upstream: https://github.com/PortMidi/portmidi  (modern fork; SF mirror dead)
# License: MIT
# Linux backend: ALSA sequencer (PMALSA, default on Linux)
# Provides: libportmidi.so, portmidi.pc

configure() {
    # Defaults are correct for our needs:
    #   BUILD_SHARED_LIBS=ON   → libportmidi.so
    #   LINUX_DEFINES=PMALSA   → ALSA sequencer backend (alsa-lib at runtime)
    #   BUILD_PORTMIDI_TESTS=OFF → don't build mm/midiclock/etc. (need device)
    cmake -B build                          \
          -DCMAKE_INSTALL_PREFIX=/usr       \
          -DCMAKE_BUILD_TYPE=Release        \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

check() {
    # PortMidi's "tests" (pm_test/) are interactive demos that open a real
    # MIDI device (BUILD_PORTMIDI_TESTS, OFF by default). There is no
    # offline unit-test suite. Skip — return 0 so the build doesn't trip.
    return 0
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
