#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gdm 49.2 — GNOME Display Manager
# BLFS 13.0

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    mkdir -p build
    cd    build

    # Wayland-only build (-Dx11-support=false). This system ships no Xorg
    # server and no X11 session files, so GDM's compiled-in X11 fallback can
    # only ever fail: on slow-GPU cold boots (Carrizo-class amdgpu takes
    # 16-18s; SEAT0_GRAPHICS_CHECK_TIMEOUT is 10s) the greeter timeout path
    # selected an X11 greeter, aborted on the missing session files
    # (SIGABRT), and the dead greeter session held the seat so the restarted
    # Wayland greeter never received DRM master — a live login screen on a
    # black panel. With X11 support compiled out, the timeout path starts
    # the Wayland greeter and mutter handles the simpledrm->amdgpu handoff.
    # -Dgdm-xsession stays at its upstream default (false): the Xsession
    # script serves X11 user sessions, which cannot exist here.
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dx11-support=false \
          -Drun-dir=/run/gdm \
          -Ddefault-pam-config=lfs
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

    # Replace the smartcard PAM stack, because it names a module this system
    # does not build (decided 2026-08-06).
    #
    # WHAT UPSTREAM INSTALLS, AND WHAT IT COST. The lfs PAM configuration ships
    # /etc/pam.d/gdm-smartcard containing `auth required pam_pkcs11.so
    # wait_for_card card_only`. That module is not packaged here. The supporting
    # smartcard stack IS shipped and running, so inserting a PKCS#11 token was
    # enough for the greeter to route authentication to this service — and then
    # PAM marked the stack faulty because the required module could not be
    # loaded. Authentication could not succeed by any means, and the greeter
    # displayed nothing to explain it. The owner of the machine was locked out
    # of it until the token was physically unplugged. Measured against real PAM
    # on an installed system: authenticating to this service with the CORRECT
    # password returned 28, "Module is unknown".
    #
    # WHY THE FILE IS REWRITTEN AND NOT DELETED. /etc/pam.d/other is
    # pam_warn + pam_deny, which is the correct default for an unknown service
    # and exactly the wrong outcome here: deleting this file would leave the
    # same lockout with a different cause. The stack is therefore replaced with
    # ordinary password authentication — the same one gdm-password performs —
    # so that whatever routes to this service, the owner can still log in. The
    # replacement was fired against real PAM on an installed system: correct
    # password 0, wrong password 6. It authenticates; it does not admit.
    #
    # It is written out in full rather than including another service, so a
    # reader of the file sees what it does without following an indirection.
    # The greeter's own setting for selecting this path is turned off in the
    # intergenos-default-settings greeter database; the two are deliberately
    # independent, so neither has to be correct for login to work.
    #
    # This is NOT smartcard support and does not claim to be. Shipping the
    # PKCS#11 PAM module, certificate mapping and enrolment is a separate piece
    # of work; until it exists, a login method that cannot complete is not
    # offered.
    install -Dm644 /dev/stdin "$DESTDIR/etc/pam.d/gdm-smartcard" <<'PAMEOF'
# Begin /etc/pam.d/gdm-smartcard

# Smartcard authentication is not available on this system: no PKCS#11 PAM
# module is installed. This stack performs ordinary password authentication so
# that a login routed here can still succeed. Replacing it with the upstream
# pam_pkcs11.so stack would make login impossible whenever a token is present.

auth     requisite      pam_nologin.so
auth     required       pam_env.so

auth     required       pam_succeed_if.so uid >= 1000 quiet
auth     include        system-auth
auth     optional       pam_gnome_keyring.so

account  include        system-account
password include        system-password

session  required       pam_limits.so
session  include        system-session
session  optional       pam_gnome_keyring.so auto_start

# End /etc/pam.d/gdm-smartcard
PAMEOF

    # Replace the fingerprint PAM stack, for exactly the reason the smartcard
    # stack above is replaced: it names a module this system does not build
    # (decided 2026-08-06).
    #
    # WHAT UPSTREAM INSTALLS. The lfs PAM configuration ships
    # /etc/pam.d/gdm-fingerprint with `auth required pam_fprintd.so` and
    # `password required pam_fprintd.so`. No recipe in this tree builds fprintd
    # or that module, and it is absent from every security directory on an
    # installed system. A stack whose required module cannot be loaded fails as
    # a whole: measured against real PAM on an installed system, authenticating
    # to this service with the CORRECT password returns 28, "Module is unknown"
    # — the same result the smartcard service returned.
    #
    # WHY THIS ONE HAS NOT BITTEN ANYONE YET, and why it is still fixed. The
    # greeter offers fingerprint only when fprintd answers on the system bus.
    # Nothing does, so the greeter never routes a login here today. That is the
    # single difference from the smartcard case, where the supporting stack WAS
    # shipped and running and the routing did happen. Leaving a stack that
    # cannot complete in place because nothing currently reaches it is a
    # dependency on an absence: the day anything answers that bus name, the
    # lockout is immediate and looks like the machine rejecting a correct
    # password.
    #
    # The file is rewritten and not deleted for the same reason as above:
    # /etc/pam.d/other is pam_warn + pam_deny, so deleting it produces the same
    # lockout with a different cause.
    #
    # This is NOT fingerprint support and does not claim to be. Packaging
    # fprintd, its PAM module and an enrolment path is separate work; until it
    # exists, a login method that cannot complete is not offered a routing.
    install -Dm644 /dev/stdin "$DESTDIR/etc/pam.d/gdm-fingerprint" <<'PAMEOF'
# Begin /etc/pam.d/gdm-fingerprint

# Fingerprint authentication is not available on this system: no fprintd PAM
# module is installed. This stack performs ordinary password authentication so
# that a login routed here can still succeed. Retaining the upstream
# pam_fprintd.so stack would make login impossible for anything that reached it.

auth     requisite      pam_nologin.so
auth     required       pam_env.so

auth     required       pam_succeed_if.so uid >= 1000 quiet
auth     include        system-auth
auth     optional       pam_gnome_keyring.so

account  include        system-account
password include        system-password

session  optional       pam_keyinit.so revoke
session  required       pam_limits.so
session  include        system-session
session  optional       pam_gnome_keyring.so auto_start

# End /etc/pam.d/gdm-fingerprint
PAMEOF

    # Ship a systemd preset that enables gdm.service by default. Real-distro
    # convention: systemctl preset-all consumes /usr/lib/systemd/system-preset/
    # at install time and creates the /etc/systemd/system/display-manager.service
    # symlink to gdm.service, making graphical.target reachable.
    install -Dm644 "$BUILD_DIR/90-gdm.preset" \
                   "$DESTDIR/usr/lib/systemd/system-preset/90-gdm.preset"

    # B10 (USA-1 closure): ship gdm.service drop-in that enforces ordering
    # against systemd-tmpfiles-setup.service so upstream's x11.conf D!
    # directive runs BEFORE gdm-greeter creates /tmp/.X11-unix as its
    # DynamicUser dynamic UID. Resolves the cold-boot race per runtime
    # trace empirical observation. Composes-with-upstream pattern.
    install -Dm644 "$BUILD_DIR/10-x11-unix-race.conf" \
                   "$DESTDIR/usr/lib/systemd/system/gdm.service.d/10-x11-unix-race.conf"

    # Greeter monitor-layout sync (decided 2026-07-21): helper + templated
    # path/service pair that mirror a user's monitors.xml into the greeter
    # seat state with uid-drift-proof modes (file 0644, dirs 0755 — GDM 49
    # chowns the seat state to a per-boot DynamicUser account, so mode bits,
    # not ownership, carry the read guarantee). The installer enables the
    # instance for the primary user it creates.
    install -Dm755 "$BUILD_DIR/igos-greeter-monitors-sync" \
                   "$DESTDIR/usr/libexec/igos-greeter-monitors-sync"
    install -Dm644 "$BUILD_DIR/igos-greeter-monitors-sync@.path" \
                   "$DESTDIR/usr/lib/systemd/system/igos-greeter-monitors-sync@.path"
    install -Dm644 "$BUILD_DIR/igos-greeter-monitors-sync@.service" \
                   "$DESTDIR/usr/lib/systemd/system/igos-greeter-monitors-sync@.service"

    # Pre-configuration greeter seed (r5): before any user layout exists,
    # the first greeter renders mutter's clone-all fallback — stretched
    # across every monitor on a multi-head box. The installer stages a
    # layout derived from the live install session's display state; this
    # condition-gated oneshot installs it into the seat state before
    # gdm.service on any boot where no layout exists yet (GDM's first-boot
    # init has been observed to wipe an install-time-only delivery), and is
    # inert from the first landed layout on. Enabled via 90-gdm.preset.
    install -Dm755 "$BUILD_DIR/igos-greeter-monitors-seed" \
                   "$DESTDIR/usr/libexec/igos-greeter-monitors-seed"
    install -Dm644 "$BUILD_DIR/igos-greeter-monitors-seed.service" \
                   "$DESTDIR/usr/lib/systemd/system/igos-greeter-monitors-seed.service"
}

post_install() {
    set -e
    # Create the gdm user/group (uid/gid 21) from the shipped sysusers
    # fragment before the chown below — systemd-sysusers only runs at boot
    # on an installed system, so the chroot never has the user otherwise.
    # Canonical in-tree pattern (dbus, openldap, fcron, exim, at). The r3
    # recipe masked this failure with `|| true`; dropping the mask (r4)
    # exposed that the chown had silently no-op'd on every prior build.
    systemd-sysusers /usr/lib/sysusers.d/gdm.conf

    # /var/lib/gdm is gdm's state dir. GDM 49 manages it with per-boot
    # DynamicUser accounts and re-shapes it at first start (root:root 0755 +
    # a .migrated-dyn-users marker — observed live 2026-07-21 on a fresh
    # install; the old "gdm refuses to start if owned by root" note described
    # pre-49 behavior and is disproven by that install's four clean evals).
    # Mode 0755 is load-bearing for the greeter monitor-layout sync: the
    # greeter compositor runs as an unpredictable per-boot uid and must
    # TRAVERSE this dir to read seat0/config/monitors.xml — 0700 is exactly
    # the perms wall that made a delivered greeter layout unreadable on a
    # fleet box. Sensitive leaves inside (pulse cookie 0600, ibus 0700)
    # carry their own modes. Ownership stays gdm:gdm for the pre-migration
    # window; GDM's migration re-owns as it sees fit — modes, not ownership,
    # carry the guarantee.
    install -dm755 -o gdm -g gdm /var/lib/gdm

    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
    update-desktop-database /usr/share/applications 2>/dev/null || true
}
