#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# linux-firmware — Firmware files for Linux kernel drivers
# BLFS 13.0
#
# Provides firmware blobs for WiFi (Intel, Realtek, Broadcom, Atheros,
# MediaTek), GPU (AMD, Intel), audio (MediaTek SOF only — the upstream
# tarball ships NO Intel or AMD audio-DSP firmware; Intel SOF comes from
# the separate sof-firmware package), Bluetooth, and more.
# Essential for bare metal hardware support.

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    : # No build step — pre-compiled firmware binaries
}

do_install() {
    set -e
    # Plain `make install` is the only reliably-working path with this
    # upstream's copy-firmware.sh. The install-xz target failed silently
    # mid-install on 2026-05-23 (errexit-suspension hazard in
    # chroot-build-core-extra.sh masked the failure) — copy-firmware.sh
    # encountered a WHENCE entry it choked on while compressing with xz,
    # printed its usage banner, exited 1, and the archive step captured
    # only the empty parent directories created by `install -d` (252-byte
    # archive, zero firmware files). Plain `make install` runs the same
    # copy-firmware.sh with `-j1` and no --xz, has been verified end-to-end
    # (4218 files including /usr/lib/firmware/amd-ucode/* and
    # /usr/lib/firmware/amdgpu/*), and is the path Debian/Arch use too.
    #
    # Trade-off: uncompressed firmware uses ~1.6GB vs ~300MB compressed.
    # That's a disk-cost decision the squashfs layer handles uniformly
    # (squashfs compresses the whole tree), so the on-disk cost of the
    # live ISO is comparable. Reliability beats install-time compression
    # for a Class A artifact (no firmware = no WiFi/GPU/audio).
    #
    # NUM_JOBS=1 is REQUIRED: upstream's Makefile derives NUM_JOBS from
    # MAKEFLAGS -j / nproc (so -j16 on our build host), and the 20260309
    # copy-firmware.sh routes any -jN>1 through GNU `parallel` — which is
    # NOT in the build chroot ("ERROR: the GNU parallel command is required
    # to use -j"). num_jobs=1 takes copy-firmware.sh's serial path (no
    # parallel dependency), which is the Debian/Arch behaviour too.
    make DESTDIR="$DESTDIR" FIRMWAREDIR=/usr/lib/firmware NUM_JOBS=1 install

    # Defensive post-install assertion: copy-firmware.sh has a history of
    # silent-partial-install (see errexit-suspension class hazard). Hard-
    # check that load-bearing subtrees actually landed. Failure here halts
    # the build cleanly rather than shipping a 252-byte phantom package.
    for required in amd-ucode amdgpu i915 iwlwifi rtl_nic; do
        if [ ! -d "${DESTDIR}/usr/lib/firmware/${required}" ] && \
           ! ls "${DESTDIR}/usr/lib/firmware/${required}"* >/dev/null 2>&1; then
            echo "ERROR: linux-firmware install missing required subtree: ${required}" >&2
            ls "${DESTDIR}/usr/lib/firmware/" >&2
            exit 1
        fi
    done
}
