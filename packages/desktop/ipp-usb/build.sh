#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# ipp-usb — the daemon that makes an IPP-over-USB printer reachable as if it
# were a network printer on this machine's loopback address.
#
# WHY IT IS NEEDED. A printer that speaks IPP over USB exposes a USB interface
# with class/subclass/protocol 7/1/4 and no character device. The print
# scheduler cannot talk to that interface, so without this daemon the printer
# is plugged in and invisible. With it, the scheduler prints to it — and a
# scanner in the same machine scans through it — with no driver, no PPD and no
# vendor package.
#
# Build system verified against the PINNED source (tag 0.9.34, extracted and
# read, not assumed):
#   - a hand-written Makefile whose `all` target runs
#     `go build -ldflags "-s -w" -tags nethttpomithttp2 -mod=vendor`;
#   - vendor/ ships IN the tag archive, so the build reaches no network. The
#     GOFLAGS/GOPROXY settings below state that rather than relying on it;
#   - its `install` target is written for a system whose root prefix is
#     passed as PREFIX, staging into $(PREFIX)/sbin, $(PREFIX)/lib/udev and
#     $(PREFIX)/lib/systemd — the pre-usrmerge layout the shipped unit's
#     ExecStart=/sbin/ipp-usb also assumes. Measured here on 2026-09-19: with
#     PREFIX=/usr it stages /usr/etc/ipp-usb and /usr/usr/share/man, and with
#     PREFIX unset it stages into /sbin and /lib rather than /usr/sbin and
#     /usr/lib. This recipe therefore stages the files itself, one install
#     command per file, at the paths this tree uses, instead of bending the
#     upstream target into a layout it was not written for;
#   - its `all` target begins with `-ctags -R`, which is why a build on a
#     machine without ctags prints "make: ctags: No such file or directory"
#     and "Error 127 (ignored)". The leading dash is upstream's: the tags file
#     is a developer convenience and nothing in the package uses it. ctags is
#     deliberately NOT added as a build dependency for a file nothing stages;
#   - the man page ships pre-generated (ipp-usb.8), so ronn is not needed.
#
# ACTIVATION: udev, and nothing else. systemd-udev/71-ipp-usb.rules tags a
# device whose USB interface list carries 070104 (or the non-standard HP
# ff0901) with ENV{SYSTEMD_WANTS}+="ipp-usb.service", so the daemon exists only
# while such a printer is plugged in. The shipped unit carries NO [Install]
# section — it cannot be enabled, and the install-time preset pass has nothing
# to decide about it. That is why this package adds no line to the tree's
# enable-list preset file: there is no enablement state to state.
#
# NETWORK POSTURE, and the two things this recipe changes from upstream's:
#   - interface = loopback stays as upstream ships it. The daemon's HTTP
#     listener binds 127.0.0.1 and ::1 only, so the printer is reachable from
#     this machine and from nowhere else.
#   - dns-sd = enable becomes dns-sd = disable. Upstream advertises the
#     printer over DNS-SD through an mDNS responder. On InterGenOS the
#     responder (avahi) ships OFF and is one of the Welcomer's opt-in switches,
#     so with upstream's default the daemon would try to publish, fail, and
#     write "DNS-SD: publishing failed" to /var/log/ipp-usb every two seconds
#     (DNSSdRetryInterval in the pinned source) for as long as the printer is
#     plugged in — a permanent error log on a machine that is working exactly
#     as configured. Turning Network Discovery on in the Welcomer sets this
#     value back to enable and restarts the daemon, so one choice still governs
#     whether this machine announces anything.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

build() {
    set -e

    # The vendored tree is used, and no proxy is consulted. GOFLAGS carries
    # -mod=vendor (upstream's Makefile passes it too; stating it here means a
    # future Makefile change cannot quietly re-enable module fetching), and
    # GOPROXY=off makes any attempt to reach the network a build failure
    # rather than a download.
    export GOFLAGS="-mod=vendor -buildvcs=false"
    export GOPROXY=off
    export GOSUMDB=off
    export CGO_ENABLED=1          # the USB transport binds libusb through cgo

    make
}

do_install() {
    set -e

    # Staged by hand, at this tree's paths (see the note above about the
    # upstream install target's layout). Every destination is written out, so
    # a reader can see exactly what this package puts on a machine.
    install -Dm755 ipp-usb "$DESTDIR/usr/sbin/ipp-usb"
    install -Dm644 systemd-udev/71-ipp-usb.rules \
                   "$DESTDIR/usr/lib/udev/rules.d/71-ipp-usb.rules"
    install -Dm644 systemd-udev/ipp-usb.service \
                   "$DESTDIR/usr/lib/systemd/system/ipp-usb.service"
    install -Dm644 ipp-usb.conf "$DESTDIR/etc/ipp-usb/ipp-usb.conf"

    for _q in ipp-usb-quirks/*.conf; do
        install -Dm644 "$_q" "$DESTDIR/usr/share/ipp-usb/quirks/$(basename "$_q")"
    done
    install -Dm644 ipp-usb-quirks/README \
                   "$DESTDIR/usr/share/ipp-usb/quirks/README"

    install -dm755 "$DESTDIR/usr/share/man/man8"
    gzip -9 -n -c ipp-usb.8 > "$DESTDIR/usr/share/man/man8/ipp-usb.8.gz"
    chmod 644 "$DESTDIR/usr/share/man/man8/ipp-usb.8.gz"

    # The unit upstream ships names /sbin/ipp-usb. This tree stages the binary
    # at /usr/sbin/ipp-usb; /sbin is a symlink to usr/sbin on an installed
    # machine, so the upstream path would resolve, but a unit that names a
    # path the package did not stage is a unit nobody can check. The
    # substitution is asserted below, because a sed that matches nothing
    # changes nothing.
    sed -i 's|^ExecStart=/sbin/ipp-usb|ExecStart=/usr/sbin/ipp-usb|' \
        "$DESTDIR/usr/lib/systemd/system/ipp-usb.service"
    if ! grep -q '^ExecStart=/usr/sbin/ipp-usb' \
         "$DESTDIR/usr/lib/systemd/system/ipp-usb.service"; then
        echo "ERROR: the staged unit does not start /usr/sbin/ipp-usb:" >&2
        grep -n 'ExecStart' "$DESTDIR/usr/lib/systemd/system/ipp-usb.service" >&2
        exit 1
    fi

    # Upstream's unit carries Wants=avahi-daemon.service. `Wants=` does not
    # only order — it PULLS THE UNIT IN, so plugging a printer into a machine
    # whose owner has left Network Discovery off would start the mDNS
    # responder, and start it without the firewall rule the Welcomer's
    # discovery switch installs alongside it: the machine would announce
    # itself and be unable to answer. The dependency is dropped here and the
    # ordering kept, so if the responder IS running the daemon still starts
    # after it. Asserted below, because a sed that matches nothing changes
    # nothing.
    sed -i '/^Wants=avahi-daemon\.service$/d' \
        "$DESTDIR/usr/lib/systemd/system/ipp-usb.service"
    if grep -q '^Wants=' "$DESTDIR/usr/lib/systemd/system/ipp-usb.service"; then
        echo "ERROR: the staged unit still pulls in another unit:" >&2
        grep -n '^Wants=' "$DESTDIR/usr/lib/systemd/system/ipp-usb.service" >&2
        exit 1
    fi

    # The one configuration value this system decides differently from
    # upstream (see the NETWORK POSTURE note above). Both edits are asserted
    # afterwards, because a sed that matches nothing changes nothing and would
    # otherwise ship upstream's default while this file claims it does not.
    conf="$DESTDIR/etc/ipp-usb/ipp-usb.conf"
    sed -i 's/^\( *dns-sd *= *\)enable/\1disable/' "$conf"

    if ! grep -qE '^ *dns-sd *= *disable' "$conf"; then
        echo "ERROR: the staged ipp-usb.conf does not carry dns-sd = disable:" >&2
        grep -n 'dns-sd' "$conf" >&2
        exit 1
    fi
    if ! grep -qE '^ *interface *= *loopback' "$conf"; then
        echo "ERROR: the staged ipp-usb.conf does not bind the loopback" >&2
        echo "       interface only; upstream's default has changed and this" >&2
        echo "       package's network posture must be re-decided." >&2
        grep -n 'interface' "$conf" >&2
        exit 1
    fi

    # The hardening drop-in for the unit, in the shape the tree's other
    # daemons use (files/usr/lib/systemd/system/<unit>.d/20-hardening.conf).
    install -Dm644 "$BUILD_DIR/files/usr/lib/systemd/system/ipp-usb.service.d/20-hardening.conf" \
                   "$DESTDIR/usr/lib/systemd/system/ipp-usb.service.d/20-hardening.conf"

    # The two directories the hardened unit declares writable. systemd refuses
    # to start a unit whose ReadWritePaths entry does not exist, and under
    # ProtectSystem=strict the daemon cannot create them itself.
    install -Dm644 "$BUILD_DIR/files/usr/lib/tmpfiles.d/ipp-usb.conf" \
                   "$DESTDIR/usr/lib/tmpfiles.d/ipp-usb.conf"

    # Three properties asserted against what was STAGED, not against the flags
    # meant to produce it.
    #
    # 1. The unit must stay un-enablable. An [Install] section would make the
    #    daemon something the preset pass decides, and it would then run at
    #    boot on machines with no printer.
    if grep -q '^\[Install\]' "$DESTDIR/usr/lib/systemd/system/ipp-usb.service"; then
        echo "ERROR: the staged unit carries an [Install] section, so it can be" >&2
        echo "       enabled to run at boot. This package activates through udev." >&2
        exit 1
    fi

    # 2. The staged binary must run and must accept the staged configuration.
    #    Upstream builds no version string into the program — there is no
    #    --version flag and no version constant in the pinned source, so a
    #    "does it report the pinned version" assertion of the kind this tree's
    #    other daemons carry cannot be written here, and claiming one would be
    #    a test that proves nothing. What CAN be proven is stronger about the
    #    thing that matters: `ipp-usb check` parses the configuration files it
    #    is pointed at and says so, which exercises the binary and the
    #    configuration this package just staged, together.
    _check="$("$DESTDIR/usr/sbin/ipp-usb" check \
              -path-conf-files-srch "$DESTDIR/etc/ipp-usb" 2>&1 || true)"
    case "$_check" in
        *"Configuration files: OK"*) ;;
        *)
            echo "ERROR: the staged daemon did not accept the staged" >&2
            echo "       configuration. It said:" >&2
            echo "$_check" >&2
            exit 1
            ;;
    esac
    echo "[config] the staged daemon parsed the staged configuration: OK"

    # 3. The udev rule must be the thing that starts it.
    if ! grep -q 'SYSTEMD_WANTS' "$DESTDIR/usr/lib/udev/rules.d/71-ipp-usb.rules"; then
        echo "ERROR: the staged udev rule does not start the service, so the" >&2
        echo "       daemon would never run for a printer that is plugged in." >&2
        exit 1
    fi
    echo "[activation] udev rule starts ipp-usb.service; the unit has no [Install] section"
}
