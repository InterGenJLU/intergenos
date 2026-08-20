#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# intergenos-backup 1.0 — Chronicle, the InterGenOS backup utility.
#
# Wraps the engine + clients at assets/intergenos-backup/ (staged into the
# generated source tarball under a chronicle-pkg/ prefix by
# scripts/build-intergenos-source-tarballs.sh) into an installable package.
#
# Source layout (in the extracted tarball, cwd during do_install):
#   chronicle/                     the engine library package (Python)
#   chronicled                     engine + sentinel entrypoint
#   chronicle-cli                  CLI entrypoint
#   chronicle-gui                  GTK4/libadwaita GUI entrypoint
#   chronicle-pretxn-handler       pkm pre-transaction restore-point handler
#   systemd/*.service *.timer      the schedule + the restore leg
#   sysusers/chronicle.conf        the engine socket's access group
#   config/chronicle.conf          default configuration
#   desktop/  polkit/  icons/  man/   the shipped desktop assets
#
# Install layout:
#   /usr/libexec/chronicle/chronicle/       the engine package
#   /usr/libexec/chronicle/{chronicled,chronicle-cli,chronicle-gui,
#                           chronicle-pretxn-handler}
#   /usr/bin/{chronicle,chronicle-gui,chronicled}      thin wrappers
#   /usr/lib/systemd/system/                 units (enabled in post_install)
#   /usr/lib/sysusers.d/chronicle.conf       the chronicle access group
#   /usr/lib/pkm/pre-transaction.d/chronicle-restore-point   pkm hook (exec)
#   /etc/chronicle/chronicle.conf
#   /usr/share/applications/  polkit-1/actions/  icons/hicolor/  man/

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e
    local libexec="${DESTDIR}/usr/libexec/chronicle"
    local bindir="${DESTDIR}/usr/bin"
    local unitdir="${DESTDIR}/usr/lib/systemd/system"
    local pretxndir="${DESTDIR}/usr/lib/pkm/pre-transaction.d"
    local confdir="${DESTDIR}/etc/chronicle"
    local appdir="${DESTDIR}/usr/share/applications"
    local polkitdir="${DESTDIR}/usr/share/polkit-1/actions"
    local icondir="${DESTDIR}/usr/share/icons/hicolor/scalable/apps"

    install -dm755 "${libexec}" "${bindir}" "${unitdir}" "${pretxndir}" \
        "${confdir}" "${appdir}" "${polkitdir}" "${icondir}"

    # -- engine package + entrypoints ----------------------------------------
    cp -a chronicle "${libexec}/chronicle"
    # Strip any stray bytecode the tarball generation excluded but a rebuild in
    # place might reintroduce.
    find "${libexec}/chronicle" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    install -m755 chronicled "${libexec}/chronicled"
    install -m755 chronicle-cli "${libexec}/chronicle-cli"
    install -m755 chronicle-gui "${libexec}/chronicle-gui"
    install -m755 chronicle-pretxn-handler "${libexec}/chronicle-pretxn-handler"

    # -- /usr/bin wrappers ---------------------------------------------------
    cat > "${bindir}/chronicle" <<'WRAP'
#!/bin/sh
exec python3 /usr/libexec/chronicle/chronicle-cli "$@"
WRAP
    chmod 755 "${bindir}/chronicle"

    # The GUI forces GSK's cairo (software) renderer: GTK4's default GL renderer
    # goes through Mesa, which on virtualized GPUs tries the ZINK Vulkan-on-GL
    # backend and aborts when no real Vulkan device is present (the same class
    # the welcomer wrapper guards against). Cairo never touches the GL stack.
    cat > "${bindir}/chronicle-gui" <<'WRAP'
#!/bin/sh
export GSK_RENDERER=cairo
exec python3 /usr/libexec/chronicle/chronicle-gui "$@"
WRAP
    chmod 755 "${bindir}/chronicle-gui"

    cat > "${bindir}/chronicled" <<'WRAP'
#!/bin/sh
exec python3 /usr/libexec/chronicle/chronicled "$@"
WRAP
    chmod 755 "${bindir}/chronicled"

    # -- systemd units -------------------------------------------------------
    install -m644 systemd/chronicled.service "${unitdir}/chronicled.service"
    install -m644 systemd/chronicle-userdata.service "${unitdir}/chronicle-userdata.service"
    install -m644 systemd/chronicle-userdata.timer "${unitdir}/chronicle-userdata.timer"
    install -m644 systemd/chronicle-offpeak.service "${unitdir}/chronicle-offpeak.service"
    install -m644 systemd/chronicle-offpeak.timer "${unitdir}/chronicle-offpeak.timer"
    install -m644 systemd/chronicle-scrub.service "${unitdir}/chronicle-scrub.service"
    install -m644 systemd/chronicle-scrub.timer "${unitdir}/chronicle-scrub.timer"
    install -m644 systemd/chronicle-restore@.service "${unitdir}/chronicle-restore@.service"
    # Preset whitelist: without this, the installer's post-hook preset-all
    # pass strips the post_install-made enablement (image-wide default-
    # disable) and the engine ships dead — see 90-chronicle.preset header.
    install -Dm644 systemd/90-chronicle.preset \
        "${DESTDIR}/usr/lib/systemd/system-preset/90-chronicle.preset"

    # -- the engine socket's access group ------------------------------------
    # Declarative, so systemd-sysusers creates it: at squashfs time into the
    # sealed image (scripts/build-squashfs.sh) and at install time onto the
    # target (installer/backend/users.py). Without the group the engine leaves
    # its socket owner-only and says so in the journal — it does not guess.
    install -Dm644 sysusers/chronicle.conf \
        "${DESTDIR}/usr/lib/sysusers.d/chronicle.conf"

    # -- pkm pre-transaction hook (exec; runs before every install/upgrade/
    #    remove and captures a restore point of exactly that footprint) -------
    install -m755 chronicle-pretxn-handler "${pretxndir}/chronicle-restore-point"

    # -- default config ------------------------------------------------------
    install -m644 config/chronicle.conf "${confdir}/chronicle.conf"

    # -- desktop / polkit / icon ---------------------------------------------
    install -m644 desktop/org.intergenos.Chronicle.desktop \
        "${appdir}/org.intergenos.Chronicle.desktop"
    install -m644 polkit/org.intergenos.Chronicle.policy \
        "${polkitdir}/org.intergenos.Chronicle.policy"
    install -m644 icons/org.intergenos.Chronicle.svg \
        "${icondir}/org.intergenos.Chronicle.svg"

    # -- man pages -----------------------------------------------------------
    install -Dm644 man/chronicle.1 "${DESTDIR}/usr/share/man/man1/chronicle.1"
    install -Dm644 man/chronicled.8 "${DESTDIR}/usr/share/man/man8/chronicled.8"
    install -Dm644 man/chronicle.conf.5 "${DESTDIR}/usr/share/man/man5/chronicle.conf.5"
}

post_install() {
    set -e
    # Create the chronicle access group NOW, not at the next boot.
    #
    # systemd-sysusers.service would create it at boot from the fragment just
    # installed. On a live upgrade that leaves a window: the engine restarts
    # below, finds no group to hand its socket to, and correctly leaves the
    # socket owner-only — so the backup application stops working for the
    # console user until the machine is rebooted. Creating the group here
    # closes the window. Guarded for chroot/offline installs exactly like the
    # systemctl calls below, and a failure is reported rather than swallowed:
    # the boot-time pass is the fallback, and the user should know which one
    # they are relying on.
    if command -v systemd-sysusers >/dev/null 2>&1; then
        systemd-sysusers /usr/lib/sysusers.d/chronicle.conf || \
            echo "chronicle: could not create the chronicle group now; systemd-sysusers.service will create it at the next boot, and the backup application will not work for ordinary users until then" >&2
    fi
    # No `systemctl enable` here. Whether the engine and its three timers run
    # by default is decided in this package's own
    # assets/intergenos-backup/systemd/90-chronicle.preset, installed to
    # /usr/lib/systemd/system-preset/90-chronicle.preset by do_install above,
    # and applied by the `systemctl preset-all` the image build and the
    # installer both run. Measured 2026-08-19 against that same engine
    # (`systemctl --root <root> preset-all` over the tree's own preset files):
    # the policy resolves chronicled.service, chronicle-userdata.timer,
    # chronicle-offpeak.timer and chronicle-scrub.timer all to ENABLED, so the
    # enable call that used to sit here changed nothing on a fresh install.
    #
    # What it did change was an upgrade. pkm fires a package's sealed
    # post_install on every install AND every upgrade, and nothing re-runs
    # preset-all afterwards — so on a machine where the user had turned the
    # engine OFF, the next upgrade of this package turned it back ON and said
    # nothing. The preset file is the single place this default is decided.
    # Decided 2026-08-19.
    #
    # The start below is a DIFFERENT operation and is kept: enablement decides
    # what happens at the next boot, and without a start a freshly-installed or
    # live-upgraded system has no running engine until then. It is guarded so a
    # chroot/offline install does not fail when systemd is not the running init.
    if command -v systemctl >/dev/null 2>&1; then
        systemctl start chronicled.service \
            chronicle-userdata.timer \
            chronicle-offpeak.timer \
            chronicle-scrub.timer 2>/dev/null || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache --quiet --force /usr/share/icons/hicolor 2>/dev/null || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications 2>/dev/null || true
    fi
}
