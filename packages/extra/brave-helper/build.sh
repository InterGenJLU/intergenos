#!/bin/bash
# brave-helper 1.0 — Download and install Brave Browser
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-brave" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Brave Browser Installer
#
# Downloads and installs Brave from official source.
# License: https://brave.com/terms-of-use/
#
# H-007 Phase B migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API.

set -e

source /usr/share/igos/helpers/helper-lib.sh

TMPDIR=$(mktemp -d)
# BLOCKING-D fix (2026-05-19): register TMPDIR cleanup via the
# helper-lib's IGOS_HELPER_USER_CLEANUP env var instead of `trap EXIT`.
# Installing a native trap would collide with the one igos_helper_init
# installs for partial-manifest sidecar emission (bash trap-replace
# semantics; no native composition).
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS Brave Browser Installer"
echo "  ====================================="
echo ""
echo "  Brave is open-source (MPL-2.0) with proprietary components."
echo "  License: https://brave.com/terms-of-use/"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install-helper brave' instead."
    echo "  Direct invocation bypasses pkm's manifest ingestion;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

igos_helper_init "brave"

echo "  Finding latest Brave release..."
# Official Brave apt distribution — the only accepted source.
# Do NOT add fallback repositories without a security review: alternate
# download sources are a supply-chain vector (an earlier revision of this
# installer referenced an unrelated third-party mirror, now removed).
BRAVE_REPO="https://brave-browser-apt-release.s3.brave.com/pool/main/b/brave-browser/"
DEB_NAME=$(wget -q -O - "$BRAVE_REPO" 2>/dev/null | grep -oP 'brave-browser_[^"]+_amd64\.deb' | sort -V | tail -1)

if [ -z "$DEB_NAME" ]; then
    echo "  ERROR: Could not locate a Brave package at the official apt repository:"
    echo "         ${BRAVE_REPO}"
    echo ""
    echo "  This may be a transient outage. Retry later, or check"
    echo "  https://brave.com/ for service advisories."
    echo ""
    echo "  This installer intentionally uses ONLY Brave's official"
    echo "  distribution. Do not patch in alternate sources without"
    echo "  security review."
    exit 1
fi

# Extract version from the .deb filename (brave-browser_VERSION_amd64.deb)
BRAVE_VERSION=$(echo "$DEB_NAME" | sed 's/^brave-browser_//; s/_amd64\.deb$//')
igos_helper_set_version "${BRAVE_VERSION:-unknown}"

echo "  Downloading ${DEB_NAME}..."
wget -q --show-progress -O "$TMPDIR/brave.deb" "${BRAVE_REPO}${DEB_NAME}"

echo "  Extracting..."
cd "$TMPDIR"
ar x brave.deb
tar xf data.tar.xz

echo "  Installing to /opt/brave.com/brave/..."
cp -a opt/brave.com /opt/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true

# H-007: record deposited files under /opt/brave.com/brave plus the
# .desktop launcher copied system-wide.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/brave.com/brave -type f -o -type l 2>/dev/null)
for f in /usr/share/applications/brave-browser*.desktop; do
    if [ -f "$f" ]; then
        igos_helper_record_file "$f"
    fi
done

ln -sf /opt/brave.com/brave/brave-browser /usr/bin/brave-browser
igos_helper_record_symlink /usr/bin/brave-browser /opt/brave.com/brave/brave-browser

igos_helper_record_dep glibc

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
igos_helper_record_post_install_action "gtk-update-icon-cache /usr/share/icons/hicolor"

igos_helper_commit

echo ""
echo "  Brave Browser installed successfully!"
echo "  Run: brave-browser"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-brave"
}
