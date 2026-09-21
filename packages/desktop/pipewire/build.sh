#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# pipewire 1.6.0 — Multimedia processing framework
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    # Explicit feature flags. Build #5 audit found vulkan + the BlueZ HFP
    # (ModemManager) backend silently disabled because we relied on meson's
    # default "auto" detection. =enabled makes meson HALT if a dep is
    # missing rather than dropping the feature.
    # ffmpeg is a declared dep and ships in-tree (built before pipewire), but
    # was silently dropped because we never enabled its meson feature — the same
    # auto-detection class this comment warns about. =enabled builds the ffmpeg
    # SPA plugin (pw-cat FFmpeg integration) and HALTS if ffmpeg ever goes
    # missing. (silent-loss audit 2026-06-25.)
    # libcamera + echo-cancel-webrtc =enabled (capture wave): the libcamera
    # SPA plugin is how MIPI/IPU built-in cameras reach applications, and
    # the WebRTC engine is what makes module-echo-cancel real (only the
    # null AEC shipped before). Both are the same silent-auto class this
    # comment block exists for — =enabled HALTS if either dep goes missing.
    # The three Bluetooth audio codecs are the same class again: aptX, LDAC
    # and LC3 were left at auto with no encoder library in the tree, so meson
    # dropped them silently and a headset negotiated SBC however good it was.
    # Their libraries (libfreeaptx, ldacBT, liblc3) are declared deps now, and
    # =enabled makes a missing one HALT the configure — which is the point:
    # the codecs cannot disappear again without someone being told.
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dsession-managers=[] \
          -Dtests=disabled \
          -Dman=disabled \
          -Dvulkan=enabled \
          -Dffmpeg=enabled \
          -Dbluez5-backend-native-mm=enabled \
          -Dlibcamera=enabled \
          -Decho-cancel-webrtc=enabled \
          -Dbluez5-codec-aptx=enabled \
          -Dbluez5-codec-ldac=enabled \
          -Dbluez5-codec-lc3=enabled
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # ---- Make the ALSA default device reach PipeWire --------------------
    # ninja install puts 50-pipewire.conf and 99-pipewire-default.conf into
    # /usr/share/alsa/alsa.conf.d. alsa-lib does NOT read that directory.
    # Its shipped alsa.conf loads configuration from /var/lib/alsa/conf.d,
    # $sysconfdir/alsa/conf.d and $sysconfdir/asound.conf only, and alsa-lib
    # is configured --prefix=/usr with no --sysconfdir, so $sysconfdir is
    # /usr/etc. The alsa-plugins package bridges the same gap for its own
    # eleven files by installing a symlink per file into /usr/etc/alsa/conf.d;
    # pipewire installed none, so both of its files were installed and never
    # read. The visible effect: pcm.!default was never repointed at PipeWire,
    # so it stayed alsa-lib's built-in default and neither aplay -L nor
    # arecord -L listed a pipewire PCM at all. What that built-in default does
    # depends on the machine, and both observed forms failed to record:
    #
    #   - cards numbered from 0: the built-in default is the dmix/dsnoop pair
    #     on card 0. "arecord -D default" failed with dsnoop's "unable to open
    #     slave" whenever PipeWire held the capture device, while
    #     "aplay -D default" still worked, because dmix can share an output
    #     card and dsnoop cannot open a capture device another process owns —
    #     which is why the loss showed only on the recording side.
    #   - cards NOT numbered from 0: the built-in default does not resolve at
    #     all. alsa.conf sets defaults.pcm.card 0 and defaults.ctl.card 0, so
    #     every path through it names a card that does not exist. Measured on
    #     the development machine 2026-09-21, whose cards are index 1 and
    #     index 2: "cannot find card '0'" then "Unknown PCM default", for
    #     playback as well as capture.
    #
    # 99-pipewire-default.conf fixes both: the definition it installs names no
    # card index at all.
    #
    # The targets are RELATIVE, matching the eleven links alsa-plugins
    # already places in that directory. pkm rewrites an absolute symlink
    # target to its relative equivalent at extraction anyway
    # (pkm/installer.py, _abs_symlink_to_relative), so both forms would land
    # identically; relative is used so the result does not depend on that
    # rewrite running, and so every link in the directory has one shape.
    install -dm755 "${DESTDIR}/usr/etc/alsa/conf.d"
    ln -sfn ../../../share/alsa/alsa.conf.d/50-pipewire.conf \
        "${DESTDIR}/usr/etc/alsa/conf.d/50-pipewire.conf"
    ln -sfn ../../../share/alsa/alsa.conf.d/99-pipewire-default.conf \
        "${DESTDIR}/usr/etc/alsa/conf.d/99-pipewire-default.conf"
}
