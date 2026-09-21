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

# The twelve mixer-path stanzas whose boost element upstream marks
# "volume = merge", as "<path file>|<element name>" lines. Kept in one place
# so the rewrite below and its read-back cannot drift apart.
boost_volume_stanzas() {
    cat <<'STANZAS'
analog-input-internal-mic.conf|Internal Mic Boost
analog-input-internal-mic.conf|Int Mic Boost
analog-input-internal-mic-always.conf|Internal Mic Boost
analog-input-internal-mic-always.conf|Int Mic Boost
analog-input-mic.conf|Mic Boost
analog-input-mic.conf|Mic Boost (+20dB)
analog-input-front-mic.conf|Front Mic Boost
analog-input-rear-mic.conf|Rear Mic Boost
analog-input-dock-mic.conf|Dock Mic Boost
analog-input-headphone-mic.conf|Headphone Mic Boost
analog-input-headset-mic.conf|Headset Mic Boost
analog-input-linein.conf|Line Boost
STANZAS
}

# Keep the microphone boost out of the audio server's volume walk.
#
# The ALSA card-profile mixer path files that this package installs mark BOTH
# the capture element and the microphone boost element "volume = merge". The
# format's own documentation, in the shipped analog-output.conf.common lines
# 31-42, says what merge means: the server walks the merge elements in file
# order, drives the first one to the top of its range, and puts the remainder
# on the next one. Capture is listed first and the boost second, so a source
# volume of 100% is the capture element at its maximum WITH the boost on top.
# Measured 2026-09-21 on a Realtek ALC285: capture +30.00 dB and boost
# +30 dB, sixty decibels of analog gain, which saturates the microphone; the
# same shape was read on a Realtek ALC236. A boost is an amplifier of last
# resort, not part of a linear volume range.
#
# "volume = zero" is the documented key for this. Same file, line 103:
# "volume = ignore | merge | off | zero | <volume step>", where zero means
# "always set it to 0 dB". zero is used and not off because off is the
# element's own minimum, which is 0 dB only where a boost's range happens to
# start there, while zero is 0 dB on every codec. Upstream already uses zero
# in four of its own output path files.
#
# This runs on the staged copies instead of carrying patched files in the
# repository, because the files come from the upstream tarball and are put in
# place by "ninja install": a carried copy would silently discard whatever a
# newer tarball changes. Running here means a version bump cannot drop the
# setting. And because the step fails unless it changed exactly the twelve
# stanzas listed above, an upstream rename or reshuffle stops the build with
# the file named, instead of quietly shipping a machine back at +60 dB.
#
# $1 is the directory holding the installed mixer path files.
zero_boost_volume_elements() {
    local paths_dir="$1"
    local expected=12
    local changed=0
    local conf element file mode

    if [ ! -d "$paths_dir" ]; then
        echo "pipewire: mixer path directory not found: $paths_dir" >&2
        return 1
    fi

    while IFS='|' read -r conf element; do
        [ -n "$conf" ] || continue
        file="${paths_dir}/${conf}"
        if [ ! -f "$file" ]; then
            echo "pipewire: mixer path file not found: $file" >&2
            return 1
        fi
        mode=$(stat -c %a "$file")
        if ! awk -v want="[Element ${element}]" '
                BEGIN { inside = 0; hits = 0 }
                /^\[/ { inside = ($0 == want) }
                inside && $0 ~ /^volume[[:blank:]]*=[[:blank:]]*merge[[:blank:]]*$/ {
                    sub(/merge/, "zero"); hits++
                }
                { print }
                END { if (hits != 1) exit 1 }
            ' "$file" > "${file}.zero-boost"; then
            rm -f "${file}.zero-boost"
            echo "pipewire: expected exactly one 'volume = merge' line in [Element ${element}] of ${conf}" >&2
            return 1
        fi
        mv -f "${file}.zero-boost" "$file"
        chmod "$mode" "$file"
        changed=$((changed + 1))
    done <<< "$(boost_volume_stanzas)"

    if [ "$changed" -ne "$expected" ]; then
        echo "pipewire: changed $changed boost stanzas, expected $expected" >&2
        return 1
    fi

    # Read every changed stanza back from the file on disk: exactly one
    # "volume = zero" and no "volume = merge" left inside it.
    while IFS='|' read -r conf element; do
        [ -n "$conf" ] || continue
        if ! awk -v want="[Element ${element}]" '
                BEGIN { inside = 0; zero = 0; merge = 0 }
                /^\[/ { inside = ($0 == want) }
                inside && $0 ~ /^volume[[:blank:]]*=[[:blank:]]*zero[[:blank:]]*$/ { zero++ }
                inside && $0 ~ /^volume[[:blank:]]*=[[:blank:]]*merge[[:blank:]]*$/ { merge++ }
                END { if (zero != 1 || merge != 0) exit 1 }
            ' "${paths_dir}/${conf}"; then
            echo "pipewire: read-back failed for [Element ${element}] in ${conf}" >&2
            return 1
        fi
    done <<< "$(boost_volume_stanzas)"

    echo "pipewire: microphone boost taken out of the volume walk in $changed stanzas"
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
    # ---- Keep the microphone boost out of the volume walk ---------------
    # See zero_boost_volume_elements above for what this changes and why.
    # It fails the build if it does not change exactly its twelve stanzas.
    zero_boost_volume_elements \
        "${DESTDIR}/usr/share/alsa-card-profile/mixer/paths"
}
