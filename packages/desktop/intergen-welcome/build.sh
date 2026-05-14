#!/bin/bash
# intergen-welcome 1.0 — InterGenOS first-boot welcome greeter
#
# Wraps the GTK4/libadwaita Python application at
# assets/intergen-welcome/intergen-welcome.py from the repo into a
# proper installable package. The welcomer:
#
#   * Runs once per new user account (state-tracked via a done-marker
#     at ~/.config/intergen-welcome/done).
#   * Flows from the boot animation: "ECG pulse -> Hello. -> Shall we
#     get started? -> GDM -> this greeter".
#   * 7 pages: Welcome / Appearance (8 curated theme combos with live
#     gsettings preview) / Extensions (24 toggleable across 4
#     categories) / Keyboard Shortcuts / Meet InterGen / Community /
#     All Set.
#
# Source layout (in the tarball):
#   iw-pkg/intergen-welcome.py   (the 991-line GTK4/libadwaita app)
#   iw-pkg/previews/             (theme thumbnail images; placeholder
#                                 ready for FLUX-generated art)
#
# Install layout:
#   /usr/libexec/intergen-welcome/intergen-welcome.py  (the actual app)
#   /usr/libexec/intergen-welcome/previews/            (asset dir)
#   /usr/bin/intergen-welcome                          (shell wrapper)
#   /usr/share/applications/intergen-welcome.desktop   (app-grid entry)
#   /etc/xdg/autostart/intergen-welcome.desktop        (first-login autostart)
#
# The wrapper script gates execution on the done-marker, exits cleanly
# if already complete, and runs the Python app otherwise. The wrapper
# writes the done-marker after a clean exit (return code 0).
#
# The autostart .desktop is system-wide (in /etc/xdg/autostart/), not
# in /etc/skel/.config/autostart/, so it picks up newly-created users
# without skel-copy timing issues. The wrapper script's done-marker
# logic provides the once-per-user gate.

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

    # Python application + assets
    install -m755 intergen-welcome.py "${libexec}/intergen-welcome.py"
    if [ -d previews ] && [ "$(ls -A previews 2>/dev/null)" ] ; then
        cp -a previews/. "${libexec}/previews/" 2>/dev/null || true
    fi

    # Wrapper script with done-marker gate
    cat > "${bindir}/intergen-welcome" <<'WRAPPER'
#!/bin/bash
# intergen-welcome launcher — once-per-user gate.
set -e
done_marker="${HOME}/.config/intergen-welcome/done"
if [ -e "${done_marker}" ] ; then
    exit 0
fi
python3 /usr/libexec/intergen-welcome/intergen-welcome.py "$@"
rc=$?
if [ "${rc}" -eq 0 ] ; then
    mkdir -p "$(dirname "${done_marker}")"
    touch "${done_marker}"
fi
exit "${rc}"
WRAPPER
    chmod 755 "${bindir}/intergen-welcome"

    # App-grid entry
    cat > "${appdir}/intergen-welcome.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=InterGenOS Welcome
Comment=First-boot setup and personalization for InterGenOS
Exec=intergen-welcome
Icon=preferences-desktop-personal
Categories=GTK;Settings;
OnlyShowIn=GNOME;
StartupNotify=false
NoDisplay=false
DESKTOP

    # Autostart entry (system-wide; once-per-user gating in wrapper)
    cat > "${autostartdir}/intergen-welcome.desktop" <<'AUTOSTART'
[Desktop Entry]
Type=Application
Name=InterGenOS Welcome
Comment=First-boot setup and personalization for InterGenOS
Exec=intergen-welcome
Icon=preferences-desktop-personal
Categories=GTK;Settings;
OnlyShowIn=GNOME;
StartupNotify=false
X-GNOME-Autostart-Phase=Applications
NoDisplay=true
AUTOSTART
}

post_install() {
    set -e
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
}
