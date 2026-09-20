#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# usbmuxd — the daemon that multiplexes connections over USB to Apple mobile
# devices. libimobiledevice and libusbmuxd are already in this tree; they all
# speak to this daemon's socket at /var/run/usbmuxd, so without it an iPhone
# or iPad plugged into an installed machine is neither mounted nor pairable
# and the libraries simply answer nothing.
#
# Build system verified against the PINNED source (commit 3ded00c9, extracted
# and read, not assumed):
#   - autotools, and the commit archive ships configure.ac WITHOUT a generated
#     ./configure (autogen.sh only), so autoreconf runs here — which is why
#     autoconf/automake/libtool are build dependencies of this recipe.
#   - configure.ac derives the package version by running ./git-version-gen at
#     autoconf time. That script reads .git, which a tarball does not carry,
#     and then falls back to a .tarball-version file; with neither present it
#     prints nothing and configure.ac aborts with "PACKAGE_VERSION is not
#     defined. Make sure to configure a source tree checked out from git or
#     that .tarball-version is present." Writing .tarball-version below is
#     upstream's own documented answer to that, not a workaround, and it is
#     written FROM this recipe's version pin so the string exists once.
#   - The daemon installs into sbindir (src/Makefile.am: sbin_PROGRAMS) and the
#     man page into section 8. It is started by udev, never typed by a user.
#
# ACTIVATION: udev raises a device-triggered systemd unit, and this is the
# shape of the package. configure.ac offers two activation methods and
# generates the udev rule to match the one chosen: with systemd support it
# writes ENV{SYSTEMD_WANTS}="usbmuxd.service" into the rule and installs a
# service unit; with --without-systemd it writes
# RUN+="/usr/sbin/usbmuxd --user usbmux --udev" instead and installs no unit.
#
# The recipe first took the second (2026-09-18) so that no unit exists to be
# enabled. MEASURED on an installed machine 2026-09-20 (the hub, an iPhone
# attached): systemd-udevd kills every process a RUN rule starts once the
# event has been handled — the daemon answered at +0 s, was alive at +1 s and
# +3 s, and was gone at +6 s with no exit line of its own — so on that path
# the phone never pairs and nothing ever mounts. udev(7) states it: a RUN
# program "is not allowed to start daemons or other long-running processes;
# the forked processes, detached or not, will be unconditionally killed after
# the event handling has finished." The systemd method is upstream's answer to
# exactly that, and it keeps every property the first choice was made for:
#   - upstream's unit carries NO [Install] section, so it cannot be enabled at
#     boot and no preset can reach it; the ONLY thing that starts it is the
#     udev add event of an Apple device (ENV{SYSTEMD_WANTS});
#   - --systemd implies --enable-exit, so the daemon exits by itself when no
#     device is attached, and the remove rule still runs `usbmuxd -x` when the
#     last device is unplugged — nothing listens at rest.
# do_install asserts the STAGED result of both halves (the unit present and
# uninstallable, the rule wanting it), not the flags meant to produce them.
#
# USER: upstream ships no sysusers fragment, so this recipe carries one
# (usbmux.sysusers.conf). systemd-sysusers creates the locked usbmux account
# at boot from /usr/lib/sysusers.d; the daemon drops to it and the udev rule
# gives it ownership of the device node.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e

    # Written from the recipe's own pin so the version string is declared in
    # exactly one place (package.yml). The builder exports PKG_VERSION; if it
    # ever stops doing so this fails loudly instead of configuring a package
    # that cannot state which build it is.
    printf '%s' "${PKG_VERSION:?FATAL: the builder did not export PKG_VERSION}" \
        > .tarball-version

    autoreconf -fi

    # systemd support (configure's default) selects the SYSTEMD_WANTS udev
    # rule AND installs the unit; the unit directory is passed explicitly so
    # the install does not depend on what pkg-config reports on the builder
    # (see the ACTIVATION note above for why udev-only activation was dropped).
    # --runstatedir=/run: the unit's PIDFile= is written from @runstatedir@,
    # which autoconf defaults to ${localstatedir}/run = /var/run; systemd
    # accepts that but logs "PIDFile= references a path below legacy directory
    # /var/run … please update the unit file" on every start (measured on the
    # hub 2026-09-20 06:37). /run is the directory; the unit names it directly.
    ./configure                                           \
        --prefix=/usr                                     \
        --sbindir=/usr/sbin                               \
        --sysconfdir=/etc                                 \
        --localstatedir=/var                              \
        --runstatedir=/run                                \
        --with-systemd                                    \
        --with-systemdsystemunitdir=/usr/lib/systemd/system \
        --with-udevrulesdir=/usr/lib/udev/rules.d
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    install -Dm644 "$BUILD_DIR/usbmux.sysusers.conf" \
                   "$DESTDIR/usr/lib/sysusers.d/usbmux.conf"

    # The properties this package exists to have, asserted against what was
    # actually staged rather than against the configure flags that were meant
    # to produce them. Each has a failure mode that is otherwise silent: a flag
    # change could drop the unit and leave a udev rule that wants a service
    # nothing installs; an upstream change could add an [Install] section and
    # turn a device-triggered daemon into a boot-time one; a version string
    # that does not reach the binary leaves an installed daemon unable to say
    # which build it is.
    _unit="$DESTDIR/usr/lib/systemd/system/usbmuxd.service"
    _rule="$DESTDIR/usr/lib/udev/rules.d/39-usbmuxd.rules"
    if [ ! -f "$_unit" ]; then
        echo "ERROR: no service unit was staged at ${_unit#"$DESTDIR"}:" >&2
        echo "       udev activates this daemon through ENV{SYSTEMD_WANTS}, which" >&2
        echo "       needs the unit; a RUN-started daemon is killed by udevd." >&2
        exit 1
    fi
    if grep -q '^\[Install\]' "$_unit"; then
        echo "ERROR: the staged unit carries an [Install] section:" >&2
        cat "$_unit" >&2
        echo "       This daemon is started only by the udev add event of an" >&2
        echo "       Apple device; it must not be enableable at boot." >&2
        exit 1
    fi
    if ! grep -q '^PIDFile=/run/usbmuxd.pid$' "$_unit"; then
        echo "ERROR: the staged unit's PIDFile= is not /run/usbmuxd.pid:" >&2
        grep '^PIDFile=' "$_unit" >&2
        echo "       systemd warns on every start about a path below /var/run." >&2
        exit 1
    fi
    if ! grep -q 'ENV{SYSTEMD_WANTS}="usbmuxd.service"' "$_rule"; then
        echo "ERROR: the staged udev rule does not want the unit:" >&2
        cat "$_rule" >&2
        exit 1
    fi
    if grep -q 'RUN+="[^"]*usbmuxd --user usbmux --udev"' "$_rule"; then
        echo "ERROR: the staged udev rule starts the daemon with RUN+= —" >&2
        echo "       systemd-udevd kills it when the event ends (measured)." >&2
        exit 1
    fi
    if ! grep -q 'ACTION=="remove".*RUN+="[^"]*usbmuxd -x"' "$_rule"; then
        echo "ERROR: the staged udev rule no longer exits the daemon on the" >&2
        echo "       last device's removal." >&2
        exit 1
    fi
    echo "[activation] unit staged without [Install], PIDFile under /run; rule wants usbmuxd.service; remove rule exits it"

    _reported="$("$DESTDIR/usr/sbin/usbmuxd" --version 2>&1 || true)"
    case "$_reported" in
        "usbmuxd ${PKG_VERSION}"*) ;;
        *)
            echo "ERROR: the staged daemon reports '${_reported}'," >&2
            echo "       expected 'usbmuxd ${PKG_VERSION}'." >&2
            exit 1
            ;;
    esac
    echo "[version] staged daemon reports: ${_reported}"
}
