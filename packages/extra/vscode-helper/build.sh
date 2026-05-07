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

set -e

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
    echo "  ERROR: Must run as root (sudo igos-install-vscode)"
    exit 1
fi

echo "  Downloading Visual Studio Code..."
wget -q --show-progress -O "$TMPDIR/vscode.tar.gz" "$VSCODE_URL"

echo "  Extracting to /opt/vscode/..."
rm -rf /opt/vscode
mkdir -p /opt/vscode
tar -xzf "$TMPDIR/vscode.tar.gz" -C /opt/vscode --strip-components=1

# Create symlink
ln -sf /opt/vscode/bin/code /usr/bin/code

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

echo ""
echo "  Visual Studio Code installed successfully!"
echo "  Run: code"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-vscode"
}
