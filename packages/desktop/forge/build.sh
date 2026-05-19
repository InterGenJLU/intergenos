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
    ln -sf /usr/lib/intergenos/smoke-test.sh \
        "${DESTDIR}/usr/bin/intergenos-smoke-test"

    # ---- /usr/bin/forge wrapper ---------------------------------------------
    install -dm755 "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/forge" <<'FORGE'
#!/bin/bash
# /usr/bin/forge — InterGenOS Forge installer entry point.
# Dispatches to installer/__main__.py with all original args.
exec /usr/bin/python3 -m installer "$@"
FORGE
    chmod 755 "${DESTDIR}/usr/bin/forge"

    # ---- Live-ISO GUI launcher pair (env-preservation across pkexec) -------
    # Two-stage shim that captures the calling liveuser session's display +
    # bus environment, then pkexec's into forge as root with that env
    # restored. Required because pkexec scrubs the environment to a tiny
    # whitelist; without this xdg-desktop-portal-gtk fails to render any
    # cross-session dialog (file chooser, partition picker, account
    # assistant) because the D-Bus-auto-activated portal-gtk process
    # inherits root's session-less env. The launcher runs as liveuser
    # (from the .desktop Exec= line); the runner runs as root (from
    # pkexec, with the polkit-rule passwordless YES for liveuser).
    install -m755 ./installer/data/forge-gui-launch \
        "${DESTDIR}/usr/bin/forge-gui-launch"
    install -m755 ./installer/data/forge-gui-runner \
        "${DESTDIR}/usr/bin/forge-gui-runner"

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
Exec=/usr/bin/forge-gui-launch
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
    # Enable forge-tui.service so systemd creates the
    # /etc/systemd/system/multi-user.target.wants/forge-tui.service symlink.
    # ConditionKernelCommandLine=igos.mode=install-tui still gates ACTUAL
    # invocation to install-tui boots only — but the enable is required for
    # systemd to "reach" the unit at all (an un-enabled unit is never
    # considered, condition-check or not). 2>/dev/null||true so this stays
    # idempotent on rebuilds and tolerates non-chroot install paths.
    systemctl enable forge-tui.service 2>/dev/null || true
}
