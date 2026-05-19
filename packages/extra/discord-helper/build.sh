#!/bin/bash
# discord-helper 1.0 — Download and install Discord
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-discord" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Discord Installer
#
# Downloads and installs Discord from official source.
# License: https://discord.com/terms
#
# H-007 Phase B migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API.

set -e

source /usr/share/igos/helpers/helper-lib.sh

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
    echo "  ERROR: Run via 'sudo pkm install-helper discord' instead."
    echo "  Direct invocation bypasses pkm's manifest ingestion;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

igos_helper_init "discord"
# Discord's tarball doesn't have a version stamp in the URL; extract
# from the build info file inside the tarball after extract.
igos_helper_set_version "latest"

echo "  Downloading Discord..."
wget -q --show-progress -O "$TMPDIR/discord.tar.gz" "$DISCORD_URL"

echo "  Extracting..."
tar -xzf "$TMPDIR/discord.tar.gz" -C /opt/

# H-007: record everything deposited under /opt/Discord plus the
# .desktop launcher created below.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/Discord -type f -o -type l 2>/dev/null)

echo "  Creating launcher..."
ln -sf /opt/Discord/Discord /usr/bin/discord
igos_helper_record_symlink /usr/bin/discord /opt/Discord/Discord

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
igos_helper_record_file /usr/share/applications/discord.desktop

igos_helper_record_dep glibc

igos_helper_commit

echo ""
echo "  Discord installed successfully!"
echo "  Run: discord"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-discord"
}
