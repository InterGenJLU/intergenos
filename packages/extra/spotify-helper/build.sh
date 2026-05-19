#!/bin/bash
# spotify-helper 1.0 — Download and install Spotify
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-spotify" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Spotify Installer
#
# Downloads and installs Spotify from official source.
# License: https://www.spotify.com/legal/end-user-agreement/
#
# H-007 Phase B migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API.

set -e

source /usr/share/igos/helpers/helper-lib.sh

SPOTIFY_URL="https://repository-origin.spotify.com/pool/non-free/s/spotify-client/"
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo ""
echo "  InterGenOS Spotify Installer"
echo "  =============================="
echo ""
echo "  Spotify is proprietary software."
echo "  License: https://www.spotify.com/legal/end-user-agreement/"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Must run as root (sudo igos-install-spotify)"
    exit 1
fi

igos_helper_init "spotify"

echo "  Finding latest Spotify release..."
# Get the latest .deb filename from the repo listing
DEB_NAME=$(wget -q -O - "$SPOTIFY_URL" | grep -oP 'spotify-client_[^"]+_amd64\.deb' | sort -V | tail -1)

if [ -z "$DEB_NAME" ]; then
    echo "  ERROR: Could not find latest Spotify package"
    exit 1
fi

# Extract version from .deb filename (spotify-client_VERSION_amd64.deb)
SPOTIFY_VERSION=$(echo "$DEB_NAME" | sed 's/^spotify-client_//; s/_amd64\.deb$//')
igos_helper_set_version "${SPOTIFY_VERSION:-unknown}"

echo "  Downloading ${DEB_NAME}..."
wget -q --show-progress -O "$TMPDIR/spotify.deb" "${SPOTIFY_URL}${DEB_NAME}"

echo "  Extracting..."
cd "$TMPDIR"
ar x spotify.deb
tar xf data.tar.gz 2>/dev/null || tar xf data.tar.xz 2>/dev/null

echo "  Installing to /opt/spotify/..."
mkdir -p /opt/spotify
cp -a usr/share/spotify/* /opt/spotify/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true

# Fix desktop file path
sed -i 's|/usr/share/spotify|/opt/spotify|g' /usr/share/applications/spotify.desktop 2>/dev/null || true

# H-007: record /opt/spotify contents + the system-wide .desktop launcher.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/spotify -type f -o -type l 2>/dev/null)
for f in /usr/share/applications/spotify*.desktop; do
    if [ -f "$f" ]; then
        igos_helper_record_file "$f"
    fi
done

ln -sf /opt/spotify/spotify /usr/bin/spotify
igos_helper_record_symlink /usr/bin/spotify /opt/spotify/spotify

igos_helper_record_dep glibc

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
igos_helper_record_post_install_action "gtk-update-icon-cache /usr/share/icons/hicolor"

igos_helper_commit

echo ""
echo "  Spotify installed successfully!"
echo "  Run: spotify"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-spotify"
}
