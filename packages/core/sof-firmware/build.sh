#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# sof-firmware 2025.12.2 — Sound Open Firmware binaries for Intel audio DSPs
#
# Pre-compiled, Intel-signed DSP firmware + topology files from the SOF
# project's sof-bin distribution. The kernel's snd_sof_pci drivers request
# these under /usr/lib/firmware/intel/ at probe time; without them every
# Intel laptop with an audio DSP has no microphone (and often no speakers).

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    : # No build step — pre-compiled, signed firmware binaries
}

do_install() {
    set -e
    # Upstream's install.sh is a thin optional cp wrapper ("some
    # distributions don't use it at all" — its own header); the release
    # tarball is laid out for a recursive copy into firmware/intel/.
    # cp -a preserves the sof-ace-tplg -> sof-ipc4-tplg symlink upstream
    # ships. The tools/ directory (sof-ctl, sof-logger — DSP debug
    # utilities, not firmware) is deliberately not installed with the
    # firmware payload.
    install -d "${DESTDIR}/usr/lib/firmware/intel"
    for tree in sof sof-tplg sof-ipc4 sof-ipc4-lib sof-ipc4-tplg sof-ace-tplg; do
        cp -a "${tree}" "${DESTDIR}/usr/lib/firmware/intel/"
    done

    install -d "${DESTDIR}/usr/share/licenses/sof-firmware"
    install -m644 LICENCE.Intel LICENCE.NXP \
        "${DESTDIR}/usr/share/licenses/sof-firmware/"

    # Defensive post-install assertion (the linux-firmware phantom-package
    # lesson): hard-check the load-bearing subtrees actually landed rather
    # than shipping empty parent directories.
    for required in sof sof-tplg sof-ipc4 sof-ipc4-tplg; do
        if [ -z "$(ls -A "${DESTDIR}/usr/lib/firmware/intel/${required}" 2>/dev/null)" ]; then
            echo "ERROR: sof-firmware install missing/empty subtree: ${required}" >&2
            ls "${DESTDIR}/usr/lib/firmware/intel/" >&2
            exit 1
        fi
    done
}
