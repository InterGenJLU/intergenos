#!/bin/bash
# edge-helper 1.0 — Download and install Microsoft Edge
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-edge" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Microsoft Edge Installer
#
# Downloads and installs Edge from Microsoft's official Linux repo.
# License: https://www.microsoft.com/en-us/servicesagreement/
#
# H-007 Phase B migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API so pkm files/verify/remove
# see edge's deposited files.

set -e

source /usr/share/igos/helpers/helper-lib.sh

EDGE_URL="https://packages.microsoft.com/repos/edge/pool/main/m/microsoft-edge-stable/"
TMPDIR=$(mktemp -d)
# BLOCKING-D fix (2026-05-19): register TMPDIR cleanup via the
# helper-lib's IGOS_HELPER_USER_CLEANUP env var instead of `trap EXIT`.
# Installing a native trap would collide with the one igos_helper_init
# installs for partial-manifest sidecar emission (bash trap-replace
# semantics; no native composition).
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS Microsoft Edge Installer"
echo "  ======================================"
echo ""
echo "  Microsoft Edge is proprietary software."
echo "  License: https://www.microsoft.com/en-us/servicesagreement/"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install-helper edge' instead."
    echo "  Direct invocation bypasses pkm's manifest ingestion;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

igos_helper_init "edge"

echo "  Finding latest Edge release..."
DEB_NAME=$(wget -q -O - "$EDGE_URL" | grep -oP 'microsoft-edge-stable_[^"]+_amd64\.deb' | sort -V | tail -1)

if [ -z "$DEB_NAME" ]; then
    echo "  ERROR: Could not find latest Edge package"
    exit 1
fi

# Extract upstream version from the .deb filename (microsoft-edge-stable_VERSION_amd64.deb)
EDGE_VERSION=$(echo "$DEB_NAME" | sed 's/^microsoft-edge-stable_//; s/_amd64\.deb$//')
igos_helper_set_version "${EDGE_VERSION:-unknown}"

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

# H-007: record every deposited file under /opt/microsoft/msedge plus
# the .desktop launcher + man pages copied system-wide.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/microsoft/msedge -type f -o -type l 2>/dev/null)
for f in /usr/share/applications/microsoft-edge*.desktop \
         /usr/share/man/man*/microsoft-edge*; do
    if [ -f "$f" ]; then
        igos_helper_record_file "$f"
    fi
done

ln -sf /opt/microsoft/msedge/microsoft-edge /usr/bin/microsoft-edge
igos_helper_record_symlink /usr/bin/microsoft-edge /opt/microsoft/msedge/microsoft-edge

igos_helper_record_dep glibc

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
igos_helper_record_post_install_action "gtk-update-icon-cache /usr/share/icons/hicolor"

igos_helper_commit

echo ""
echo "  Microsoft Edge installed successfully!"
echo "  Run: microsoft-edge"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-edge"
}
