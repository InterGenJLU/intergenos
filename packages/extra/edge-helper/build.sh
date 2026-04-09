#!/bin/bash
# edge-helper 1.0 — Download and install Microsoft Edge
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-edge" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Microsoft Edge Installer
#
# Downloads and installs Edge from Microsoft's official Linux repo.
# License: https://www.microsoft.com/en-us/servicesagreement/

set -e

EDGE_URL="https://packages.microsoft.com/repos/edge/pool/main/m/microsoft-edge-stable/"
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo ""
echo "  InterGenOS Microsoft Edge Installer"
echo "  ======================================"
echo ""
echo "  Microsoft Edge is proprietary software."
echo "  License: https://www.microsoft.com/en-us/servicesagreement/"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Must run as root (sudo igos-install-edge)"
    exit 1
fi

echo "  Finding latest Edge release..."
DEB_NAME=$(wget -q -O - "$EDGE_URL" | grep -oP 'microsoft-edge-stable_[^"]+_amd64\.deb' | sort -V | tail -1)

if [ -z "$DEB_NAME" ]; then
    echo "  ERROR: Could not find latest Edge package"
    exit 1
fi

echo "  Downloading ${DEB_NAME}..."
wget -q --show-progress -O "$TMPDIR/edge.deb" "${EDGE_URL}${DEB_NAME}"

echo "  Extracting..."
cd "$TMPDIR"
ar x edge.deb
tar xf data.tar.xz

echo "  Installing to /opt/microsoft/msedge/..."
cp -a opt/microsoft /opt/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true
cp -a usr/share/man/* /usr/share/man/ 2>/dev/null || true

ln -sf /opt/microsoft/msedge/microsoft-edge /usr/bin/microsoft-edge

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true

echo ""
echo "  Microsoft Edge installed successfully!"
echo "  Run: microsoft-edge"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-edge"
}
