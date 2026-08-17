#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# open-vm-tools 13.1.0 — open-source VMware guest tools (vmtoolsd)
#
# MINIMAL guest-integration build (security-only-alignment / default-deny posture):
#   ENABLED : vmtoolsd daemon, timeSync, resolutionKMS (Wayland-friendly dynamic
#             resize via DRM/KMS — no X), powerOps (graceful shutdown), guestInfo,
#             vmware-toolbox-cmd.
#   DISABLED: vgauth (guest auth — drops pam/ssl/xmlsec1/xml2), deployPkg (guest
#             customization — drops libmspack), containerInfo, FUSE/HGFS shared
#             folders, X / GTK clipboard (dndcp), dnet (libdnet not shipped),
#             out-of-tree kernel modules (the vmw_* drivers are in mainline =y).
# Every flag below was verified against this exact pinned tarball's
# ./configure --help + configure.ac (not memory / not a distro config) per
# build-rules §2.8 (upstream-drift / stale-recipe-assumption class).

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # The release tarball ships a pre-generated ./configure (autotools already
    # bootstrapped) — no autogen/autoreconf needed.
    #
    # Modern-gcc/glib2 build fixes (build-rules §2.8, upstream-drift): newer gcc
    # promotes open-vm-tools 13.1.0's own const-qualifier warnings to fatal under
    # the package's hardcoded `-Werror` (configure.ac:1437, ungated — no
    # --disable-werror opt-out exists in this version, verified vs configure.ac +
    # the upstream README.md build section). Rather than de-fatalise -Werror (which
    # would drop a real warning gate), we FIX the warnings upstream-faithfully and
    # KEEP -Werror on. The patch (re-authored from Arch's open-vm-tools-gcc16.patch
    # for this same pinned version) does two things, both -Werror-clean:
    #   1. adds explicit (char *) casts to strchr/strrchr/strstr/Str_Strrchr
    #      returns in 7 files (hgfsEscape, hgfsServerLinux, strutil, nicInfoPosix,
    #      i18n, vixTools) — the legit fix for -Wdiscarded-qualifiers;
    #   2. #undef g_malloc0/g_malloc0_n/g_free in the RPCI-only glib_stubs.c before
    #      its stub definitions, so glib2's new fortify MACROS don't expand over
    #      the function definitions.
    # Security-first: fix the code rather than mask the warning gate (-Werror stays
    # on). Distro-proven on the identical 13.1.0 source.
    patch -Np1 -i "$BUILD_DIR/0001-gcc-const-quals-and-glib-stubs.patch"

    ./configure --prefix=/usr               \
                --sysconfdir=/etc           \
                --localstatedir=/var        \
                --disable-static            \
                --without-kernel-modules    \
                --disable-vgauth            \
                --without-pam               \
                --without-ssl               \
                --without-xmlsec1           \
                --without-xml2              \
                --disable-deploypkg         \
                --disable-containerinfo     \
                --without-fuse              \
                --without-x                 \
                --without-gtk3              \
                --without-gtk4              \
                --without-gtkmm3            \
                --without-gtkmm4            \
                --without-icu               \
                --without-dnet              \
                --enable-resolutionkms      \
                --enable-vmwgfxctrl         \
                --disable-tests             \
                --disable-docs
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Upstream ships NO systemd unit (no init/ dir in the tarball) — distros
    # provide their own. Ship a real unit pointing at the installed
    # /usr/bin/vmtoolsd, gated ConditionVirtualization=vmware so it is a literal
    # no-op outside a VMware guest (NOT a stub — Rule 21 — the target binary
    # exists and is installed above).
    install -Dm644 "$BUILD_DIR/vmtoolsd.service" \
                   "$DESTDIR/usr/lib/systemd/system/vmtoolsd.service"

    # Per-package preset enabling vmtoolsd by default (gdm/nftables precedent:
    # a 90-<name>.preset sorts before the 99- `disable *` catch-all → first-match
    # wins → enabled). Safe to enable globally because the unit's
    # ConditionVirtualization=vmware confines actual execution to VMware guests.
    install -Dm644 "$BUILD_DIR/90-open-vm-tools.preset" \
                   "$DESTDIR/usr/lib/systemd/system-preset/90-open-vm-tools.preset"
}

post_install() {
    set -e
    # plugins are gmodule-loaded .so's under /usr/lib/open-vm-tools/plugins/*;
    # refresh the linker cache for the installed shared libs.
    ldconfig 2>/dev/null || true
}
