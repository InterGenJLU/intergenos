#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# forge 1.0.0 — InterGenOS system installer (NEW package)
#
# Authors the missing /usr/bin/forge wrapper + Python module install +
# systemd service files for the installer that has lived in installer/
# source-tree-only since the Forge architecture commits but has never
# been packaged. This package closes that gap.
#
# What ships:
#   /usr/bin/forge                                    — shell wrapper
#   /usr/lib/python3.14/site-packages/installer/      — Python module tree
#   /usr/lib/systemd/system/forge-tui.service         — TUI install service
#   /usr/share/polkit-1/actions/...forge.policy       — pkexec action
#   /usr/share/polkit-1/rules.d/49-...-forge.rules    — liveuser-YES rule
#   /usr/share/applications/forge-gui.desktop         — Live-mode launcher
#   /usr/share/man/man1/forge.1                       — manpage
#
# Mode dispatch (matches the UKI cmdline shipped by build-uki.sh):
#   igos.mode=install-gui  → init.sh writes XDG autostart for liveuser
#                             session → forge --mode gui
#   igos.mode=install-tui  → forge-tui.service fires on tty1 (matched via
#                             ConditionKernelCommandLine=igos.mode=install-tui)
#   live mode + user-click → /usr/share/applications launcher → forge --mode gui
#
# License: GPL-3.0-or-later (matches the broader InterGenOS in-house
# licensing posture). All source is original work.

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

    # ---- Python module: drop installer/ into site-packages -----------------
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages"
    cp -a ./installer "${DESTDIR}/usr/lib/python3.14/site-packages/installer"

    # Strip any data/ subdir from the Python module — service files install
    # to /usr/lib/systemd/system, not into the Python module tree.
    rm -rf "${DESTDIR}/usr/lib/python3.14/site-packages/installer/data"

    # C-009: smoke tree lives canonically at /usr/lib/intergenos/ (see
    # smoke-test.sh:13-16 header). Drop the site-packages duplicate so the
    # /usr/lib/intergenos/ copy is the only invocable framework — avoids
    # drift between two trees + matches the documented runtime location.
    rm -rf "${DESTDIR}/usr/lib/python3.14/site-packages/installer/smoke"

    # ---- C-004: installer post_install hook source tree --------------------
    # run_post_install_hooks scans packages_dir for tier/pkg/build.sh shape.
    # The pkm manifest dir at /var/lib/igos/packages has flat <name>-<ver>/
    # layout (no tier shape) — hooks never fired prior to this. The forge
    # tarball stages packages/*/<pkg>/{build.sh,package.yml} at
    # ./installer-hooks/ (see scripts/build-forge-tarball.sh); ship that to
    # /usr/share/intergenos/installer-hooks/ and point forge-tui.service +
    # forge-gui-runner at the new path via their ExecStart lines.
    install -dm755 "${DESTDIR}/usr/share/intergenos"
    cp -a ./installer-hooks "${DESTDIR}/usr/share/intergenos/installer-hooks"

    # ---- C-009: smoke tree at canonical /usr/lib/intergenos/ ---------------
    # smoke-test.sh:25-27 self-locates via readlink so it runs from either
    # the source tree or the installed path. Install the framework files
    # + create the /usr/bin/intergenos-smoke-test symlink that the
    # smoke-test.sh header documents as the user-facing entry point.
    install -dm755 "${DESTDIR}/usr/lib/intergenos"
    install -m755 ./installer/smoke/smoke-test.sh \
        "${DESTDIR}/usr/lib/intergenos/smoke-test.sh"
    install -m644 ./installer/smoke/lib.sh \
        "${DESTDIR}/usr/lib/intergenos/lib.sh"
    install -dm755 "${DESTDIR}/usr/lib/intergenos/checks"
    install -m644 ./installer/smoke/checks/*.sh \
        "${DESTDIR}/usr/lib/intergenos/checks/"
    # RT-2 (GE): the composed-path assertor that checks/gaming.sh pipes
    # in-container steam-runtime-system-info JSON through — referenced as
    # ${SCRIPT_DIR}/ge-composed-path-assert.py, so it ships beside
    # smoke-test.sh (Rule 21: no referenced-but-unshipped path).
    install -m755 ./installer/smoke/ge-composed-path-assert.py \
        "${DESTDIR}/usr/lib/intergenos/ge-composed-path-assert.py"
    # RT-11 (GE): the mirror-install eval stage — sync + install the
    # mirror-only gaming meta + verify + the strict smoke battery. Part of
    # the eval process (coordinator/operator-run), shipped beside the smoke
    # framework it drives.
    install -m755 ./installer/smoke/ge-eval-stage.sh \
        "${DESTDIR}/usr/lib/intergenos/ge-eval-stage.sh"
    # The symlink below must not depend on the caller having pre-seeded the
    # usr/bin skeleton in DESTDIR — create it explicitly.
    install -dm755 "${DESTDIR}/usr/bin"
    ln -sf /usr/lib/intergenos/smoke-test.sh \
        "${DESTDIR}/usr/bin/intergenos-smoke-test"

    # ---- Boot-order checker (installed system) -----------------------------
    # Verifies at every boot that the UEFI boot entry the installer registered
    # is still first in BootOrder, and restores it when the install recorded
    # InterGenOS as the default boot target. Firmware that enumerates the
    # ESP's fallback loader as its own boot option was measured placing that
    # option ahead of the registered entry, so the machine booted through
    # \EFI\BOOT\BOOTX64.EFI instead of the registered \EFI\InterGenOS path.
    # Ships with forge because it verifies what forge wrote.
    install -m755 ./installer/bootorder/bootorder-check.sh \
        "${DESTDIR}/usr/lib/intergenos/bootorder-check.sh"
    ln -sf /usr/lib/intergenos/bootorder-check.sh \
        "${DESTDIR}/usr/bin/intergenos-bootorder-check"

    # ---- GBC001.8: ship the shared forensic-trace module ------------------
    # installer/backend/_trace.py is a loader shim that imports the shared
    # igos_trace.py framework. On the live ISO + installed system the
    # source-tree path (/mnt/intergenos/scripts/lib/igos_trace.py) is gone,
    # so the shim falls back to /usr/lib/intergenos/igos_trace.py — but
    # nothing staged it there, so the Forge GUI crashed at import with
    # "packaging error — must ship scripts/lib/igos_trace.py to
    # /usr/lib/intergenos/" (caught on the GBC001.2 boot: clicking Install
    # failed). The build pipeline is meant to ship igos_trace.py to
    # /usr/lib/intergenos/ but never did until now. The chroot has the synced source at
    # /mnt/intergenos/scripts/lib/igos_trace.py.
    install -m644 /mnt/intergenos/scripts/lib/igos_trace.py \
        "${DESTDIR}/usr/lib/intergenos/igos_trace.py"

    # ---- /usr/bin/forge wrapper ---------------------------------------------
    install -dm755 "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/forge" <<'FORGE'
#!/bin/bash
# /usr/bin/forge — InterGenOS Forge installer entry point.
# Dispatches to installer/__main__.py with all original args.
exec /usr/bin/python3 -m installer "$@"
FORGE
    chmod 755 "${DESTDIR}/usr/bin/forge"

    # ---- GUI launcher (Architecture B 2026-05-25) --------------------------
    # The GUI runs as the calling intergenos user — NOT pkexec'd to root
    # anymore. Privileged install work goes through the system D-Bus to
    # the org.intergenos.ForgeInstaller1 backend (see service files
    # below). forge-gui-launch is now a thin exec wrapper that just
    # starts `forge --mode gui` as the calling user.
    install -m755 ./installer/data/forge-gui-launch \
        "${DESTDIR}/usr/bin/forge-gui-launch"

    # ---- systemd units: TUI install + Forge D-Bus backend ------------------
    install -dm755 "${DESTDIR}/usr/lib/systemd/system"
    install -m644 ./installer/data/forge-tui.service \
        "${DESTDIR}/usr/lib/systemd/system/forge-tui.service"
    install -m644 ./installer/data/forge-installer-backend.service \
        "${DESTDIR}/usr/lib/systemd/system/forge-installer-backend.service"
    # Boot-order checker unit. Condition-gated on EFI firmware + the
    # installer's recorded intent, so it is inert on the live medium and on a
    # non-EFI system. The installer enables it on the target after
    # preset-all (installer/backend/users.py: enable_bootorder_check).
    install -m644 ./installer/bootorder/intergenos-bootorder-check.service \
        "${DESTDIR}/usr/lib/systemd/system/intergenos-bootorder-check.service"

    # ---- D-Bus integration (system bus, on-demand activation) --------------
    # Bus policy: who's allowed to send messages to + own the service name.
    install -dm755 "${DESTDIR}/usr/share/dbus-1/system.d"
    install -m644 ./installer/data/org.intergenos.ForgeInstaller1.conf \
        "${DESTDIR}/usr/share/dbus-1/system.d/org.intergenos.ForgeInstaller1.conf"
    # Activation: dbus-daemon launches the backend on first message.
    install -dm755 "${DESTDIR}/usr/share/dbus-1/system-services"
    install -m644 ./installer/data/org.intergenos.ForgeInstaller1.service \
        "${DESTDIR}/usr/share/dbus-1/system-services/org.intergenos.ForgeInstaller1.service"

    # ---- /usr/share/applications launcher (live-mode click-to-install) -----
    # NoDisplay=true by DEFAULT (operator branding pass 2026-06-11 §C): the
    # squashfs is shared between the live ISO and the installed system, and
    # "Install InterGenOS" is meaningless once installed — so it must be hidden
    # there. The live session re-shows it: init.sh writes a user-level override
    # at ~/.local/share/applications/forge-gui.desktop (NoDisplay=false) for the
    # liveuser, which shadows this system entry per the XDG desktop-entry spec.
    # An installed system carries no igos.mode and gets no override → stays hidden.
    install -dm755 "${DESTDIR}/usr/share/applications"
    cat > "${DESTDIR}/usr/share/applications/forge-gui.desktop" <<'LAUNCHER'
[Desktop Entry]
Type=Application
Name=Install InterGenOS
Comment=Install InterGenOS to disk
Exec=/usr/bin/forge-gui-launch
Icon=intergenos
Categories=System;Settings;X-InterGenOS;
OnlyShowIn=GNOME;
StartupNotify=true
StartupWMClass=org.intergenos.forge
NoDisplay=true
LAUNCHER

    # ---- Manpage ------------------------------------------------------------
    install -dm755 "${DESTDIR}/usr/share/man/man1"
    install -m644 ./forge.1 "${DESTDIR}/usr/share/man/man1/forge.1"
    install -dm755 "${DESTDIR}/usr/share/man/man8"
    install -m644 ./intergenos-bootorder-check.8 \
        "${DESTDIR}/usr/share/man/man8/intergenos-bootorder-check.8"

    # ---- User-facing docs --------------------------------------------------
    # The Forge installer's inline doc viewer (UserPage MOK row's
    # "First-boot walkthrough" link, _find_doc() candidates list) reads
    # from /usr/share/doc/intergenos/users/. Ship the markdown sources
    # straight from docs/users/ — the viewer's _markdown_to_pango()
    # handles rendering inline so no html generation is required at
    # build time. Same path serves live ISO + installed system.
    if [ -d ./docs/users ]; then
        install -dm755 "${DESTDIR}/usr/share/doc/intergenos/users"
        install -m644 ./docs/users/*.md \
            "${DESTDIR}/usr/share/doc/intergenos/users/"
        # Images referenced by those docs (docs/users/images/*.png — the
        # MokManager enrollment captures). The viewer resolves an image
        # reference relative to the directory it read the doc from, so
        # the images must sit beside the markdown at the SAME installed
        # path or the walkthrough renders placeholders instead of the
        # captures. Absent dir = older doc set, nothing to ship.
        if [ -d ./docs/users/images ]; then
            install -dm755 "${DESTDIR}/usr/share/doc/intergenos/users/images"
            install -m644 ./docs/users/images/* \
                "${DESTDIR}/usr/share/doc/intergenos/users/images/"
        fi
    fi
}

post_install() {
    set -e
    # Enable forge-tui.service so systemd creates the
    # /etc/systemd/system/multi-user.target.wants/forge-tui.service symlink.
    # ConditionKernelCommandLine=igos.mode=install-tui still gates ACTUAL
    # invocation to install-tui boots only — but the enable is required for
    # systemd to "reach" the unit at all (an un-enabled unit is never
    # considered, condition-check or not).
    #
    # Unmasked. The mask was justified as keeping the call idempotent on
    # rebuilds and tolerant of non-chroot install paths; neither needs it.
    # Measured 2026-08-19 in a chroot built from this systemd 259.1: enabling
    # a PRESENT unit returns 0 and writes the symlink both with and without
    # /proc mounted, and a SECOND enable of the same unit also returns 0 — the
    # operation is idempotent on its own. The only reachable failure is a unit
    # that does not exist, which returns 1. This package installs
    # forge-tui.service itself, so a non-zero means the installer's text-mode
    # entry point would ship unreachable.
    systemctl enable forge-tui.service
}
