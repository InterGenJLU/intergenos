#!/bin/bash
# vscode-helper 1.0 — Download and install Microsoft Visual Studio Code
# InterGenOS extra tier
#
# VS Code is proprietary software. This helper downloads it from
# Microsoft's official distribution. The user accepts Microsoft's
# license terms by running this installer.

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
    # Install the helper script
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-vscode" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Visual Studio Code Installer
#
# Downloads and installs VS Code from Microsoft's official source.
# License: https://code.visualstudio.com/license
#
# H-007 Phase B migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API.

set -e

source /usr/share/igos/helpers/helper-lib.sh

VSCODE_URL="https://code.visualstudio.com/sha/download?build=stable&os=linux-x64"
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo ""
echo "  InterGenOS Visual Studio Code Installer"
echo "  ========================================"
echo ""
echo "  Visual Studio Code is proprietary software."
echo "  License: https://code.visualstudio.com/license"
echo ""

# Check for root
if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install-helper vscode' instead."
    echo "  Direct invocation bypasses pkm's manifest ingestion;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

igos_helper_init "vscode"
igos_helper_set_version "latest"

echo "  Downloading Visual Studio Code..."
wget -q --show-progress -O "$TMPDIR/vscode.tar.gz" "$VSCODE_URL"

echo "  Extracting to /opt/vscode/..."
rm -rf /opt/vscode
mkdir -p /opt/vscode
tar -xzf "$TMPDIR/vscode.tar.gz" -C /opt/vscode --strip-components=1

# H-007: record everything deposited under /opt/vscode.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/vscode -type f -o -type l 2>/dev/null)

# Create symlink
ln -sf /opt/vscode/bin/code /usr/bin/code
igos_helper_record_symlink /usr/bin/code /opt/vscode/bin/code

# Desktop file
mkdir -p /usr/share/applications
cat > /usr/share/applications/code.desktop << 'DESKTOP'
[Desktop Entry]
Name=Visual Studio Code
Comment=Code Editing. Redefined.
GenericName=Text Editor
Exec=/opt/vscode/code --unity-launch %F
Icon=/opt/vscode/resources/app/resources/linux/code.png
Type=Application
StartupNotify=false
StartupWMClass=Code
Categories=TextEditor;Development;IDE;
MimeType=text/plain;inode/directory;
Actions=new-empty-window;
Keywords=vscode;

[Desktop Action new-empty-window]
Name=New Empty Window
Exec=/opt/vscode/code --new-window %F
Icon=/opt/vscode/resources/app/resources/linux/code.png
DESKTOP
igos_helper_record_file /usr/share/applications/code.desktop

igos_helper_record_dep glibc

igos_helper_commit

echo ""
echo "  Visual Studio Code installed successfully!"
echo "  Run: code"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-vscode"
}
