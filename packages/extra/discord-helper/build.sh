#!/bin/bash
# discord-helper 1.0 — Download and install Discord
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-discord" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Discord Installer
#
# Downloads and installs Discord from official source.
# License: https://discord.com/terms

set -e

DISCORD_URL="https://discord.com/api/download?platform=linux&format=tar.gz"
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo ""
echo "  InterGenOS Discord Installer"
echo "  =============================="
echo ""
echo "  Discord is proprietary software."
echo "  License: https://discord.com/terms"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Must run as root (sudo igos-install-discord)"
    exit 1
fi

echo "  Downloading Discord..."
wget -q --show-progress -O "$TMPDIR/discord.tar.gz" "$DISCORD_URL"

echo "  Extracting..."
tar -xzf "$TMPDIR/discord.tar.gz" -C /opt/

echo "  Creating launcher..."
ln -sf /opt/Discord/Discord /usr/bin/discord

cat > /usr/share/applications/discord.desktop << 'DESKEOF'
[Desktop Entry]
Name=Discord
Comment=All-in-one voice and text chat
Exec=/opt/Discord/Discord
Icon=/opt/Discord/discord.png
Type=Application
Categories=Network;InstantMessaging;
StartupWMClass=discord
DESKEOF

echo ""
echo "  Discord installed successfully!"
echo "  Run: discord"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-discord"
}
