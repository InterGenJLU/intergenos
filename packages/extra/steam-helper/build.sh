#!/bin/bash
# steam-helper 1.0 — Download and install Steam
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-steam" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Steam Installer
#
# Downloads and installs the Steam client from Valve's official source.
# License: https://store.steampowered.com/subscriber_agreement/
#
# NOTE: Steam requires 32-bit library support. On a pure 64-bit system,
# Steam's built-in runtime provides most dependencies, but some system
# libraries may need 32-bit counterparts.

set -e

STEAM_URL="https://cdn.akamai.steamstatic.com/client/installer/steam.deb"
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo ""
echo "  InterGenOS Steam Installer"
echo "  ============================"
echo ""
echo "  Steam is proprietary software."
echo "  License: https://store.steampowered.com/subscriber_agreement/"
echo ""
echo "  NOTE: Steam requires 32-bit library support."
echo "  Steam's runtime provides most deps, but some games"
echo "  may require additional libraries."
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Must run as root (sudo igos-install-steam)"
    exit 1
fi

echo "  Downloading Steam..."
wget -q --show-progress -O "$TMPDIR/steam.deb" "$STEAM_URL"

echo "  Extracting..."
cd "$TMPDIR"
ar x steam.deb
tar xf data.tar.xz

echo "  Installing..."
cp -a usr/* /usr/
cp -a etc/* /etc/ 2>/dev/null || true

# Steam bootstraps itself on first run
ln -sf /usr/lib/steam/steam /usr/bin/steam 2>/dev/null || true

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database /usr/share/applications 2>/dev/null || true

echo ""
echo "  Steam bootstrap installed successfully!"
echo "  Run: steam (first launch will download the full client)"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-steam"
}
