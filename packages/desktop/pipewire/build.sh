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

# Tell the two cases apart when the rewrite below finds no "volume = merge"
# line to change in a listed stanza. An upstream rename or reshuffle and a
# second run over a tree this step has already rewritten both leave the stanza
# without that line, and only the first is a fault. The message has to name
# the right one: a refusal that reports an upstream change nobody made sends
# whoever reads the failed build to look upstream for it. True when the named
# stanza already carries exactly one "volume = zero" and no "volume = merge".
stanza_already_zeroed() {
    awk -v want="[Element $2]" '
        BEGIN { inside = 0; zero = 0; merge = 0 }
        /^\[/ { inside = ($0 == want) }
        inside && $0 ~ /^volume[[:blank:]]*=[[:blank:]]*zero[[:blank:]]*$/ { zero++ }
        inside && $0 ~ /^volume[[:blank:]]*=[[:blank:]]*merge[[:blank:]]*$/ { merge++ }
        END { if (zero == 1 && merge == 0) exit 0; exit 1 }
    ' "$1"
}

# Write a file without ever following a symbolic link standing at its name.
#
# Content is read from standard input. The temporary is created by mktemp in
# the directory the file is published into: mktemp creates it exclusively and
# fails if it cannot, so the name it returns is a regular file this step has
# just made and can never be an existing link pointing outside the staging
# root, and the rename that publishes it is on one filesystem. rename(2)
# replaces a link standing at the destination rather than writing through it;
# the destination is checked as well, so a link there is refused in words
# instead of being quietly replaced.
#
# Written after the independent read of the first form of this change measured
# what the previous shape did: a link left at the fixed temporary name
# "<file>.zero-boost" was followed by the redirect, the file it pointed at was
# rewritten, and the staged name then became a link, because the move carried
# the link rather than a regular file. The two generated files below were
# written by direct redirection and had the same hole, and the chmod that
# followed each of them crossed it too.
#
# $1 is the path to write, $2 the mode it must end up with.
write_file_without_following_a_link() {
    local dest="$1" mode="$2" dir tmp
    dir=$(dirname "$dest")
    if [ ! -d "$dir" ]; then
        echo "pipewire: directory not found for $dest" >&2
        return 1
    fi
    if [ -L "$dest" ]; then
        echo "pipewire: destination is a symbolic link and is not written: $dest" >&2
        return 1
    fi
    tmp=$(mktemp "${dir}/.pipewire-staging.XXXXXX") || return 1
    if ! cat > "$tmp"; then
        rm -f "$tmp"
        echo "pipewire: the staged copy of $dest could not be written" >&2
        return 1
    fi
    if ! chmod "$mode" "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    if [ -L "$dest" ]; then
        rm -f "$tmp"
        echo "pipewire: destination is a symbolic link and is not written: $dest" >&2
        return 1
    fi
    if ! mv -f "$tmp" "$dest"; then
        rm -f "$tmp"
        return 1
    fi
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
    local conf element file mode tmp

    if [ ! -d "$paths_dir" ]; then
        echo "pipewire: mixer path directory not found: $paths_dir" >&2
        return 1
    fi

    while IFS='|' read -r conf element; do
        [ -n "$conf" ] || continue
        file="${paths_dir}/${conf}"
        if [ -L "$file" ]; then
            # The rewrite writes a new file beside this one and moves it into
            # place. Done to a symbolic link, that replaces the link with a
            # regular file while the file it points at keeps "volume = merge",
            # and the count still reaches twelve: a machine shipped with its
            # boost in the volume walk and a build that said nothing. No
            # shipped path set holds one; if upstream ever ships one, this
            # stops the build instead of passing.
            echo "pipewire: mixer path file is a symbolic link and is not rewritten: $file" >&2
            return 1
        fi
        if [ ! -f "$file" ]; then
            echo "pipewire: mixer path file not found: $file" >&2
            return 1
        fi
        mode=$(stat -c %a "$file")
        # A name this step has just created, in the directory the file is
        # published into: it cannot be a link someone left at a predictable
        # name, and the move that publishes it replaces the staged file rather
        # than writing through anything.
        tmp=$(mktemp "${paths_dir}/.zero-boost.XXXXXX") || return 1
        if ! awk -v want="[Element ${element}]" '
                BEGIN { inside = 0; hits = 0 }
                /^\[/ { inside = ($0 == want) }
                inside && $0 ~ /^volume[[:blank:]]*=[[:blank:]]*merge[[:blank:]]*$/ {
                    sub(/merge/, "zero"); hits++
                }
                { print }
                END { if (hits != 1) exit 1 }
            ' "$file" > "$tmp"; then
            rm -f "$tmp"
            if stanza_already_zeroed "$file" "$element"; then
                echo "pipewire: [Element ${element}] of ${conf} already reads 'volume = zero'; this step rewrites a freshly staged path set once, it is not idempotent, and it cannot carry on from a partly rewritten set: stage the mixer path files again before running it" >&2
            else
                echo "pipewire: expected exactly one 'volume = merge' line in [Element ${element}] of ${conf}" >&2
            fi
            return 1
        fi
        chmod "$mode" "$tmp"
        mv -f "$tmp" "$file"
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

# Install the boost-zeroing helper and the drop-in that runs it.
# $1 is the staging root (DESTDIR).
install_boost_zeroing_helper() {
    local DESTDIR="$1"
    # ---- Put a directly written microphone boost back to 0 dB -----------
    # "volume = zero", installed just below, is asserted by the audio server
    # when it APPLIES a route — at session start, on a port or profile change,
    # on a session-manager restart — and not at any other moment. Anything
    # that writes the mixer directly in between therefore holds a boost up
    # until the next apply. The one such writer this system ships is
    # alsa-utils: alsactl restore, run by alsa-restore.service from the saved
    # state in /var/lib/alsa/asound.state at boot and again from the udev rule
    # 90-alsa-restore.rules whenever a sound control device appears. A machine
    # whose saved state carries a non-zero boost — saved by the same unit's
    # ExecStop at the previous shutdown — comes up with the boost raised, and
    # a card that appears after the session is already running is restored
    # after the last route apply, where nothing takes it down again.
    #
    # The correction runs where that writer runs: a drop-in on alsa-utils'
    # own unit, whose ExecStartPost puts every boost element this package
    # zeroes back to 0 dB after the restore has written it. It needs no new
    # dependency, because it can only ever run when alsa-utils is installed —
    # without it there is no alsa-restore.service to drop into, and no
    # restore to correct. The element list is generated here from
    # boost_volume_stanzas, so the two cannot drift apart.
    #
    # What this does NOT cover, stated plainly: the other state scheme, the
    # alsactl daemon behind alsa-state.service, which runs only when
    # /etc/alsa/state-daemon.conf exists. This system ships no such file and
    # alsa-restore.service's own ConditionPathExists refuses when it does.
    install -dm755 "${DESTDIR}/usr/libexec"
    {
        echo '#!/bin/sh'
        cat <<'ZERO_BOOST_HEADER'
# Put every microphone boost element back to 0 dB.
#
# Installed by the pipewire package and run from a drop-in on
# alsa-restore.service, after alsactl has written the saved mixer state.
# The audio server asserts "volume = zero" on these elements only when it
# applies a route, so a state restore between two applies would otherwise
# leave a boost raised. Absent elements and cards are not an error: this
# runs on every machine and the list names every boost the shipped mixer
# paths mark, not the ones any one codec has.
set -u

ZERO_BOOST_HEADER
        echo 'boost_elements() {'
        echo "    cat <<'ELEMENTS'"
        # LC_ALL=C so the order of this list is the same file on every
        # build host: sort's collation is locale-dependent, and
        # "Int Mic Boost" and "Internal Mic Boost" swap places between
        # a C locale and a UTF-8 one, which would make the installed
        # helper differ between two builds of the same source.
        boost_volume_stanzas | cut -d'|' -f2 | LC_ALL=C sort -u
        echo 'ELEMENTS'
        echo '}'
        cat <<'ZERO_BOOST_BODY'

# The control devices to work on. Overridable so this helper can be exercised
# against a directory of fixtures on a build host that has no sound card at
# all; nothing in the product sets it, and the default is the real one.
: "${PIPEWIRE_BOOST_CONTROL_GLOB:=/dev/snd/controlC*}"

zeroed=0
absent=0
failed=0

# One reading per control device, so the card index comes from the device
# that exists rather than from a guess about numbering.
for control in $PIPEWIRE_BOOST_CONTROL_GLOB; do
    [ -e "$control" ] || continue
    card=${control##*/controlC}
    # Ask the card which simple controls it has, ONCE. An element this codec
    # does not have is an ordinary fact -- this list names every boost the
    # shipped mixer paths mark, not the ones any one codec carries -- and an
    # element the card DOES have that will not take 0 dB is a failure. Without
    # this reading the two are the same non-zero status from amixer, and
    # discarding it made a machine whose boost was never zeroed look exactly
    # like a machine that has no boost to zero.
    if ! controls=$(amixer -c "$card" scontrols 2>/dev/null); then
        echo "the mixer controls of card $card could not be read" >&2
        failed=$((failed + 1))
        continue
    fi
    while IFS= read -r element; do
        [ -n "$element" ] || continue
        case "$controls" in
            *"'$element'"*) ;;
            *) absent=$((absent + 1)); continue ;;
        esac
        if amixer -c "$card" -q sset "$element" 0dB 2>/dev/null; then
            zeroed=$((zeroed + 1))
        else
            echo "card $card has $element and setting it to 0 dB failed" >&2
            failed=$((failed + 1))
        fi
    done <<ELEMENT_LIST
$(boost_elements)
ELEMENT_LIST
done

echo "microphone boost: $zeroed element(s) set to 0 dB, $absent not present on this machine, $failed failed"
[ "$failed" -eq 0 ] || exit 1
exit 0
ZERO_BOOST_BODY
    } | write_file_without_following_a_link \
        "${DESTDIR}/usr/libexec/pipewire-zero-microphone-boost" 755 || return 1

    install -dm755 "${DESTDIR}/usr/lib/systemd/system/alsa-restore.service.d"
    write_file_without_following_a_link \
        "${DESTDIR}/usr/lib/systemd/system/alsa-restore.service.d/10-microphone-boost-to-zero.conf" \
        644 <<'DROP_IN' || return 1
# The saved ALSA state is written to the mixer by alsactl restore; this puts
# the microphone boost elements back to 0 dB immediately afterwards, so a
# stored non-zero boost does not survive a boot or a card appearing later.
# The leading "-" keeps a failure here from failing the restore itself.
[Service]
ExecStartPost=-/usr/libexec/pipewire-zero-microphone-boost
DROP_IN
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
    # ---- Put a directly written microphone boost back to 0 dB -----------
    # See install_boost_zeroing_helper above for what this installs and why.
    install_boost_zeroing_helper "${DESTDIR}"

    # ---- Keep the microphone boost out of the volume walk ---------------
    # See zero_boost_volume_elements above for what this changes and why.
    # It fails the build if it does not change exactly its twelve stanzas.
    zero_boost_volume_elements \
        "${DESTDIR}/usr/share/alsa-card-profile/mixer/paths"
}
