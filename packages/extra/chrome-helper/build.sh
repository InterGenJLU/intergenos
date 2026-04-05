#!/bin/bash
# chrome-helper 1.0 — Download and install Google Chrome
# InterGenOS extra tier
#
# Google Chrome is proprietary software. This helper downloads it
# from Google's official distribution channel. The user accepts
# Google's license terms by running this installer.

configure() {
    :
}

build() {
    :
}

do_install() {
    # Install the helper script
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-chrome" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Google Chrome Installer
#
# Downloads and installs Google Chrome from Google's official source.
# License: https://www.google.com/intl/en/chrome/terms/

set -e

CHROME_URL="https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo ""
echo "  InterGenOS Google Chrome Installer"
echo "  ==================================="
echo ""
echo "  Google Chrome is proprietary software."
echo "  License: https://www.google.com/intl/en/chrome/terms/"
echo ""

# Check for root
if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Must run as root (sudo igos-install-chrome)"
    exit 1
fi

echo "  Downloading Google Chrome..."
wget -q --show-progress -O "$TMPDIR/chrome.deb" "$CHROME_URL"

echo "  Extracting..."
cd "$TMPDIR"
ar x chrome.deb
tar xf data.tar.xz

echo "  Installing to /opt/google/chrome/..."
cp -a opt/google /opt/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true
cp -a usr/share/man/* /usr/share/man/ 2>/dev/null || true

# Create symlink
ln -sf /opt/google/chrome/google-chrome /usr/bin/google-chrome

# Update icon cache
gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true

echo ""
echo "  Google Chrome installed successfully!"
echo "  Run: google-chrome"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-chrome"
}
