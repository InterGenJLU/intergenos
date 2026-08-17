#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# alsa-ucm-conf 1.2.15.3 — ALSA Use Case Manager configuration profiles
#
# Pure configuration data, no compile step. alsa-lib's UCM parser reads
# /usr/share/alsa/ucm2 at runtime; these profiles are what make internal
# laptop microphones and speaker/jack switching work on DSP-routed audio
# hardware (SOF/ACP): without them the devices exist but expose no usable
# capture/playback paths.

configure() {
    set -e
    : # No configure step — configuration data only
}

build() {
    set -e
    : # No build step — configuration data only
}

do_install() {
    set -e
    # ucm2/ is the live profile tree (matches alsa-lib >= 1.2.x UCM parser).
    # The legacy ucm/ directory upstream ships contains only a README
    # explaining it is superseded — deliberately not installed.
    install -d "${DESTDIR}/usr/share/alsa"
    cp -a ucm2 "${DESTDIR}/usr/share/alsa/"

    install -d "${DESTDIR}/usr/share/licenses/alsa-ucm-conf"
    install -m644 LICENSE "${DESTDIR}/usr/share/licenses/alsa-ucm-conf/"

    # Defensive assertion: the parser entry point and the two load-bearing
    # subtrees must land (phantom-package guard).
    for required in ucm2/ucm.conf ucm2/lib ucm2/conf.d; do
        if [ ! -e "${DESTDIR}/usr/share/alsa/${required}" ]; then
            echo "ERROR: alsa-ucm-conf install missing: ${required}" >&2
            exit 1
        fi
    done
}
