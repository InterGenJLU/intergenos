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
    # read. The visible effect: pcm.!default stayed alsa-lib's built-in
    # dmix/dsnoop pair, "arecord -D default" failed with dsnoop's "unable to
    # open slave" whenever PipeWire held the capture device, and neither
    # aplay -L nor arecord -L listed a pipewire PCM at all. Playback still
    # worked, because dmix can share an output card while dsnoop cannot open
    # a capture device another process owns — which is why the loss showed up
    # only on the recording side and went unnoticed.
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
