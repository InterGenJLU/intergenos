#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# intergen-welcome 1.0 — InterGenOS first-boot welcome greeter
#
# Wraps the GTK4/libadwaita Python application at
# assets/intergen-welcome/intergen-welcome.py from the repo into a
# proper installable package. The welcomer:
#
#   * Auto-fires once per new user account on first login via the
#     /etc/xdg/autostart/ entry, gated by ~/.config/intergen-welcome/done.
#     After the user completes the wizard cleanly, the marker is written
#     and the autostart entry exits 0 on subsequent logins.
#   * Can be re-run any time from the app grid (the .desktop entry in
#     /usr/share/applications/ invokes `intergen-welcome --force`, which
#     bypasses the marker check). The in-app copy "re-run this welcomer,
#     or use Settings, the Extensions app..." (intergen-welcome.py lines
#     ~763, ~1081-1082) refers to this app-grid invocation path.
#   * Flows from the boot animation: "ECG pulse -> Hello. -> Shall we
#     get started? -> GDM -> this greeter".
#   * 7 pages: Welcome / Appearance (10 curated theme combos with live
#     gsettings preview + per-theme 1280x800 thumbnails) / Extensions
#     (24 toggleable across 4 categories) / Keyboard Shortcuts / Meet
#     InterGen / Community / All Set.
#
# Source layout (in the tarball):
#   iw-pkg/intergen-welcome.py   (the GTK4/libadwaita app)
#   iw-pkg/previews/             (10 deterministic 1280x800 PNG theme
#                                 thumbnails rendered via Python+cairosvg
#                                 pipeline at assets/intergen-welcome/
#                                 previews/generate.py; Walk #18 closure
#                                 at 638660c6 -- replaces FLUX-placeholder)
#
# Install layout:
#   /usr/libexec/intergen-welcome/intergen-welcome.py  (the actual app)
#   /usr/libexec/intergen-welcome/previews/            (asset dir)
#   /usr/bin/intergen-welcome                          (shell wrapper)
#   /usr/share/applications/intergen-welcome.desktop   (app-grid entry)
#   /etc/xdg/autostart/intergen-welcome.desktop        (first-login autostart)
#
# The wrapper script gates execution on the done-marker UNLESS invoked
# with --force/-f (the app-grid .desktop passes --force; the autostart
# .desktop does not). On a normal completion (rc=0), the wrapper writes
# the marker regardless of how it was invoked, so a user who launches
# from the app grid before the autostart fires still gets first-run
# behavior on subsequent logins.
#
# The autostart .desktop is system-wide (in /etc/xdg/autostart/), not
# in /etc/skel/.config/autostart/, so it picks up newly-created users
# without skel-copy timing issues. The wrapper script's done-marker
# logic provides the once-per-user gate for the autostart path; the
# app-grid path is gateless by design.

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
    local libexec="${DESTDIR}/usr/libexec/intergen-welcome"
    local bindir="${DESTDIR}/usr/bin"
    local appdir="${DESTDIR}/usr/share/applications"
    local autostartdir="${DESTDIR}/etc/xdg/autostart"

    install -dm755 "${libexec}/previews" "${bindir}" "${appdir}" "${autostartdir}"

    # App icon (Icon=intergen-welcome) — framed glass squircle + pulse +
    # welcome sparkle (operator branding §F; was Icon=preferences-desktop-
    # personal → a generic blue avatar in the overview/ArcMenu).
    install -dm755 "${DESTDIR}/usr/share/icons/hicolor/scalable/apps"
    install -m644 intergen-welcome.svg \
        "${DESTDIR}/usr/share/icons/hicolor/scalable/apps/intergen-welcome.svg"
    # First-party wiki mark: the Community page's Documentation & Wiki row
    # names org.intergenos.Wiki, but no package shipped the asset — the row
    # rendered image-missing on the ge9b-12 installed system (the ge9b-11
    # fallback-icon fix replaced a pruned emblem-* name with a first-party
    # name that did not exist on disk yet). The consumer ships its own mark,
    # same pattern as the backup engine's application icon.
    install -m644 org.intergenos.Wiki.svg \
        "${DESTDIR}/usr/share/icons/hicolor/scalable/apps/org.intergenos.Wiki.svg"

    # Python application + assets
    install -m755 intergen-welcome.py "${libexec}/intergen-welcome.py"
    # Privileged helper for the "Enable Services" page (run via pkexec; the page
    # invokes it for print/discovery/SSH enable+disable). Root-owned, 0755.
    install -m755 intergen-welcome-privhelper "${libexec}/intergen-welcome-privhelper"
    # The shared name-lookup-failure module. The Welcomer loads it from beside
    # itself; `intergen setup` imports the same source file from its own
    # package. One file in the tree so the two surfaces cannot tell the same
    # user two different stories about the same machine. It is declared in
    # verify_paths, so a build that failed to ship it halts at the
    # pre-squashfs audit instead of reaching a user with a Welcomer that
    # cannot start.
    install -m644 net_diagnostics.py "${libexec}/net_diagnostics.py"
    if [ -d previews ] && [ "$(ls -A previews 2>/dev/null)" ] ; then
        cp -a previews/. "${libexec}/previews/" 2>/dev/null || true
    fi

    # Wrapper script: marker-gated by default, bypassed by --force/-f.
    cat > "${bindir}/intergen-welcome" <<'WRAPPER'
#!/bin/bash
# intergen-welcome launcher.
#
# Default invocation (no flags) is the first-login autostart path: it
# checks the per-user done-marker and exits 0 if already complete.
#
# Invocation with --force / -f bypasses the marker check (the app-grid
# .desktop entry uses this) so users can re-run the welcomer any time
# via the "InterGenOS Welcome" application icon.
#
# On a clean completion (rc=0) the wrapper writes the marker regardless
# of how it was invoked, so a user who runs the wizard via the app grid
# before the autostart fires still gets first-run behavior on subsequent
# logins.
#
# The one exception is the re-arm sentinel, handled at the bottom: the
# AI-assistant page writes it when the user starts an NVIDIA driver
# install, because that install requires a reboot and the page tells the
# user the welcomer will be back afterwards so they can finish setting
# InterGen up. The marker has to be cleared HERE rather than by the app,
# precisely because of the rule above -- this wrapper writes the marker
# on every clean exit, so a marker deleted inside the app would be
# re-created moments later and the page's promise would be false.
set -e

force_run=0
case "$1" in
    -f|--force)
        force_run=1
        shift
        ;;
esac

done_marker="${HOME}/.config/intergen-welcome/done"
rearm_marker="${HOME}/.config/intergen-welcome/rearm"

# Clear a re-arm request left over from a previous run before deciding
# anything. The request is written when a driver install starts and is
# meant to be consumed by the SAME run that wrote it; one can survive if
# that run never reached its own bookkeeping -- the user rebooted from
# the terminal without closing the window, or abandoned the install at
# the password prompt. A stale request costs one extra welcomer
# appearance, which is self-correcting, but it is still a request that
# no longer corresponds to anything, so it does not get to persist.
rm -f "${rearm_marker}"

if [ "${force_run}" -eq 0 ] && [ -e "${done_marker}" ] ; then
    exit 0
fi

# Live-ISO guard. The marker check above relies on a persistent $HOME
# (per-user state survives across logins). The live ISO has $HOME on an
# overlay-on-squashfs upper layer that's ephemeral -- every "Try
# InterGenOS" boot looks like a first login, so the marker check passes
# and the welcomer re-fires every time. The welcomer is for installed
# systems ONLY; skip on ANY ISO session. The ISO UKIs set igos.mode=
# {live,install-gui,install-tui} (installer also recognizes igos.mode=try);
# an INSTALLED system carries NO igos.mode (Forge writes root=+verity, no
# igos.mode). So the gate is "skip if igos.mode= present at all" -- not just
# 'live'. GBC001.3-rebuild fix mirroring intergen-firstboot's extension.js:
# the old 'live'-only check let the welcomer's gate miss install-gui (there
# Forge's Hidden=true autostart shadow still suppressed it, but defense-in-
# depth wants the guard correct on its own).
#
# --force/-f STILL bypasses this guard so the app-grid invocation works
# in the unlikely event a user runs the welcomer manually from a live
# session (force_run==1 path skips both gates). Read-failure on
# /proc/cmdline falls through to "no guard match" -- a missed skip on
# an unusual host is preferable to refusing to launch on an installed
# system where /proc/cmdline is unreadable for some reason.
if [ "${force_run}" -eq 0 ] && grep -qE 'igos\.mode=' /proc/cmdline 2>/dev/null ; then
    exit 0
fi

# Force GSK's cairo (software) renderer. GTK4's default GL renderer goes
# through Mesa, which on virtualized GPUs (QXL, virtio-vga) tries the ZINK
# Vulkan-on-GL backend and segfaults on context creation when the host
# can't expose a real Vulkan device:
#   "MESA: error: ZINK: failed to choose pdev"
#   -> "libEGL warning: egl: failed to create dri2 screen"
#   -> Aborted (core dumped)
# Symptom: the welcomer launches, "Next" works (no context recreation
# needed), but the first click that triggers a re-paint with a fresh GL
# context (e.g., a theme preview row, an extension toggle) core-dumps
# the process. Cairo is software-only — slower, but never touches the
# GL stack. For a 7-page wizard that's launched once on first login
# (and rarely re-run from the app grid), the perceptual cost on real
# hardware is negligible.
export GSK_RENDERER=cairo
# set -e is lifted across the app run so a non-zero exit does not abort the
# wrapper before the marker bookkeeping below. Without this, an app that
# crashed or was killed would skip the re-arm check entirely and the driver
# install's "you'll be shown this again after reboot" promise would quietly
# not happen. The rc is still propagated unchanged at the end.
set +e
python3 /usr/libexec/intergen-welcome/intergen-welcome.py "$@"
rc=$?
set -e

# Re-arm: a driver install was started from the AI-assistant page, which
# needs a reboot to take effect. Clear the done-marker instead of writing
# it, so the next login shows the welcomer again and the user can finish
# setting InterGen up on hardware the system can finally read.
if [ -e "${rearm_marker}" ] ; then
    rm -f "${rearm_marker}" "${done_marker}"
    exit "${rc}"
fi

if [ "${rc}" -eq 0 ] ; then
    mkdir -p "$(dirname "${done_marker}")"
    touch "${done_marker}"
fi
exit "${rc}"
WRAPPER
    chmod 755 "${bindir}/intergen-welcome"

    # App-grid entry: always re-runs (--force bypasses the done-marker
    # so the in-app "re-run this welcomer" wording works as advertised).
    cat > "${appdir}/intergen-welcome.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=InterGenOS Welcomer
Comment=Re-run InterGenOS first-boot setup and personalization
Exec=intergen-welcome --force
Icon=intergen-welcome
Categories=GTK;Settings;X-InterGenOS;
OnlyShowIn=GNOME;
StartupNotify=false
StartupWMClass=com.intergenos.welcome
NoDisplay=false
DESKTOP

    # Autostart entry (system-wide; once-per-user gating in wrapper)
    cat > "${autostartdir}/intergen-welcome.desktop" <<'AUTOSTART'
[Desktop Entry]
Type=Application
Name=InterGenOS Welcomer
Comment=First-boot setup and personalization for InterGenOS
Exec=intergen-welcome
Icon=intergen-welcome
Categories=GTK;Settings;
OnlyShowIn=GNOME;
StartupNotify=false
NoDisplay=true
X-GNOME-Autostart-Delay=3
AUTOSTART

    # Skip the autostart unit entirely once the user is done.
    #
    # There is no gnome-session-binary on GNOME 49: systemd-xdg-autostart-
    # generator converts the entry above into a user service, and for this
    # entry that unit is Type=exec with ExitType=cgroup. On an already-set-up
    # system the wrapper finds the done-marker and exits within milliseconds,
    # and on one of three cold boots of an installed machine systemd lost the
    # race to account for that process: the unit ended result=resources with
    # "No PIDs left". Nothing was broken, but a service reporting a resource
    # failure on a normal boot is noise in the first place a person looks when
    # something really is wrong.
    #
    # This condition means no process is started at all in that case, so there
    # is nothing for systemd to lose. Measured on systemd 259.1: a drop-in on
    # the generated unit is found and merged, and the skip is logged as an
    # unmet condition with Result=success, not a failure.
    #
    # AutostartCondition=unless-exists, the key gnome-initial-setup uses, was
    # measured and rejected: the generator delegates it to
    # gnome-systemd-autostart-condition, which is not shipped here, so the
    # generator writes "ExecCondition using gnome-systemd-autostart-condition
    # skipped due to missing binary" and runs the entry unconditionally. The
    # same measurement is why X-GNOME-Autostart-Delay above has no effect on
    # this stack — the generator does not translate it. Neither key is relied
    # on; both are left in place for sessions that do read them.
    #
    # The wrapper's own marker check stays the authority: it also covers the
    # app-grid --force path and the live-ISO guard, which no unit condition
    # can see. If a future rename changes the generated unit name this drop-in
    # simply stops matching, behaviour is unchanged, and only the race
    # returns — tests/welcome/test_autostart_skip_when_done.py derives the
    # name from the entry's basename so a rename is caught.
    local unitdropin
    unitdropin="${DESTDIR}/usr/lib/systemd/user/app-intergen\\x2dwelcome@autostart.service.d"
    install -dm755 "${unitdropin}"
    cat > "${unitdropin}/50-skip-when-done.conf" <<'DROPIN'
[Unit]
ConditionPathExists=!%h/.config/intergen-welcome/done
DROPIN
    chmod 644 "${unitdropin}/50-skip-when-done.conf"
}

post_install() {
    set -e
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache --quiet --force /usr/share/icons/hicolor 2>/dev/null || true
    fi
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
}
