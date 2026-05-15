#!/bin/bash
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

    # ---- /usr/bin/forge wrapper ---------------------------------------------
    install -dm755 "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/forge" <<'FORGE'
#!/bin/bash
# /usr/bin/forge — InterGenOS Forge installer entry point.
# Dispatches to installer/__main__.py with all original args.
exec /usr/bin/python3 -m installer "$@"
FORGE
    chmod 755 "${DESTDIR}/usr/bin/forge"

    # ---- systemd service: forge-tui.service ---------------------------------
    install -dm755 "${DESTDIR}/usr/lib/systemd/system"
    install -m644 ./installer/data/forge-tui.service \
        "${DESTDIR}/usr/lib/systemd/system/forge-tui.service"

    # ---- polkit policy + rule (pkexec elevation for liveuser) ---------------
    # The autostart and launcher both invoke forge via pkexec because
    # /usr/bin/forge itself requires uid=0 (installer/__main__.py). The
    # liveuser account has no password (shadow entry = `*`), so the polkit
    # rule grants passwordless YES when subject.user == "liveuser"; the
    # policy file defaults to auth_admin_keep so installed-system invocations
    # require a real admin password.
    install -dm755 "${DESTDIR}/usr/share/polkit-1/actions"
    install -m644 ./installer/data/org.intergenos.forge.policy \
        "${DESTDIR}/usr/share/polkit-1/actions/org.intergenos.forge.policy"
    install -dm755 "${DESTDIR}/usr/share/polkit-1/rules.d"
    install -m644 ./installer/data/49-intergenos-forge.rules \
        "${DESTDIR}/usr/share/polkit-1/rules.d/49-intergenos-forge.rules"

    # ---- /usr/share/applications launcher (live-mode click-to-install) -----
    install -dm755 "${DESTDIR}/usr/share/applications"
    cat > "${DESTDIR}/usr/share/applications/forge-gui.desktop" <<'LAUNCHER'
[Desktop Entry]
Type=Application
Name=Install InterGenOS
Comment=Install InterGenOS to disk
Exec=pkexec /usr/bin/forge --mode gui --archives /var/lib/igos/archives --packages /var/lib/igos/packages
Icon=system-software-install
Categories=System;Settings;
OnlyShowIn=GNOME;
StartupNotify=true
NoDisplay=false
LAUNCHER

    # ---- Manpage ------------------------------------------------------------
    install -dm755 "${DESTDIR}/usr/share/man/man1"
    install -m644 ./forge.1 "${DESTDIR}/usr/share/man/man1/forge.1"
}

post_install() {
    set -e
    # No-op: forge-tui.service is gated by ConditionKernelCommandLine, not
    # WantedBy/preset, so no symlink wiring is needed at install time. The
    # service only fires when igos.mode=install-tui appears on /proc/cmdline.
    :
}
