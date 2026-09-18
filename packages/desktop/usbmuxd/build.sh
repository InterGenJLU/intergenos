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
# ACTIVATION: udev, deliberately, and this is the shape of the package.
# configure.ac offers two activation methods and generates the udev rule to
# match the one chosen: with systemd support it writes
# ENV{SYSTEMD_WANTS}="usbmuxd.service" into the rule and installs a service
# unit; with --without-systemd it writes
# RUN+="/usr/sbin/usbmuxd --user usbmux --udev" instead and its Makefile.am
# installs no unit at all (the systemd subdirectory is not even entered —
# SUBDIRS is built from SYSTEMD_SUB, which configure leaves empty). We take
# the second: the daemon exists only while an Apple device is plugged in. The
# rule starts it on the add/bind of an Apple vendor-id device (5ac) and the
# remove rule runs `usbmuxd -x`, which exits the daemon when the last such
# device is unplugged. Nothing listens at rest, so there is no idle daemon to
# attack and no unit anyone has to remember to disable.
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

    # --without-systemd selects udev activation AND suppresses the unit (see
    # the ACTIVATION note above); --with-systemdsystemunitdir=no states the
    # same intent a second way, so no reader has to trace the conditional to
    # learn that no unit directory is even offered to the install.
    ./configure                                           \
        --prefix=/usr                                     \
        --sbindir=/usr/sbin                               \
        --sysconfdir=/etc                                 \
        --localstatedir=/var                              \
        --without-systemd                                 \
        --with-systemdsystemunitdir=no                    \
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

    # The two properties this package exists to have, asserted against what
    # was actually staged rather than against the configure flags that were
    # meant to produce it. Both are cheap and both have a failure mode that is
    # otherwise silent: a future flag change could reinstate the service unit,
    # and a version string that does not reach the binary leaves an installed
    # daemon unable to say which build it is.
    if [ -e "$DESTDIR/usr/lib/systemd/system" ]; then
        echo "ERROR: a systemd system unit directory was staged:" >&2
        find "$DESTDIR/usr/lib/systemd/system" >&2
        echo "       This package activates through udev and installs no unit." >&2
        exit 1
    fi

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
