#!/bin/bash
# brave-helper 1.0 — Download and install Brave Browser
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-brave" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Brave Browser Installer
#
# Downloads and installs Brave from official source.
# License: https://brave.com/terms-of-use/

set -e

BRAVE_URL="https://github.com/nicehash/nicehash-brave/releases"
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo ""
echo "  InterGenOS Brave Browser Installer"
echo "  ====================================="
echo ""
echo "  Brave is open-source (MPL-2.0) with proprietary components."
echo "  License: https://brave.com/terms-of-use/"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Must run as root (sudo igos-install-brave)"
    exit 1
fi

echo "  Finding latest Brave release..."
# Brave distributes via their own repo
BRAVE_REPO="https://brave-browser-apt-release.s3.brave.com/pool/main/b/brave-browser/"
DEB_NAME=$(wget -q -O - "$BRAVE_REPO" 2>/dev/null | grep -oP 'brave-browser_[^"]+_amd64\.deb' | sort -V | tail -1)

if [ -z "$DEB_NAME" ]; then
    # Fallback: direct GitHub release
    echo "  Trying GitHub releases..."
    DEB_URL=$(wget -q -O - "https://api.github.com/repos/nicehash/nicehash-brave/releases/latest" 2>/dev/null | \
        python3 -c "import sys,json; assets=json.load(sys.stdin).get('assets',[]); [print(a['browser_download_url']) for a in assets if 'amd64.deb' in a['name']]" 2>/dev/null | head -1)
    if [ -n "$DEB_URL" ]; then
        wget -q --show-progress -O "$TMPDIR/brave.deb" "$DEB_URL"
    else
        echo "  ERROR: Could not find Brave package"
        exit 1
    fi
else
    echo "  Downloading ${DEB_NAME}..."
    wget -q --show-progress -O "$TMPDIR/brave.deb" "${BRAVE_REPO}${DEB_NAME}"
fi

echo "  Extracting..."
cd "$TMPDIR"
ar x brave.deb
tar xf data.tar.xz

echo "  Installing to /opt/brave.com/brave/..."
cp -a opt/brave.com /opt/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true

ln -sf /opt/brave.com/brave/brave-browser /usr/bin/brave-browser

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true

echo ""
echo "  Brave Browser installed successfully!"
echo "  Run: brave-browser"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-brave"
}
