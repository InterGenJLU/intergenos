#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# spotify 1.0 — Download and install Spotify
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"

    # K21.F: install the pinned Spotify apt-repo signing key (armored
    # .asc in repo for review-ability) as a dearmored binary keyring
    # at /usr/share/igos/helpers/keyrings/spotify-keyring.gpg. The
    # runtime helper sources helper-lib.sh and calls
    # igos_helper_verify_deb_via_signed_release with this keyring
    # path. Trust-anchor introduction methodology: the PGP public key
    # block was obtained from Spotify's primary publication at
    # https://download.spotify.com/debian/pubkey_5384CE82BA52C83A.gpg
    # (the URL itself encodes the 16-char keyid Spotify documents at
    # https://www.spotify.com/us/download/linux/ as the apt-repo
    # trust anchor). The full 40-char fingerprint is DERIVED from the
    # key block per RFC 4880 (sha1 of the public key packet):
    # E1096BCBFF6D418796DE78515384CE82BA52C83A (last 16 chars
    # 5384CE82BA52C83A match the documented keyid per OpenPGP
    # convention). Empirical trust anchor: gpgv-against-live-InRelease
    # at clear-time reported active signing fingerprint
    # E1096BCBFF6D418796DE78515384CE82BA52C83A (active key issued
    # 2025-11-21, expires 2027-02-14).
    install -dm755 "${DESTDIR}/usr/share/igos/helpers/keyrings"
    gpg --dearmor < \
        "${IGOS_SOURCE_ROOT:-/mnt/intergenos}/packages/extra/spotify/assets/spotify-keyring.asc" \
        > "${DESTDIR}/usr/share/igos/helpers/keyrings/spotify-keyring.gpg"
    chmod 644 "${DESTDIR}/usr/share/igos/helpers/keyrings/spotify-keyring.gpg"

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

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/spotify-1.0-accepted.json"

# K21.F: pivot from the unsigned pool-listing URL to the canonical
# apt repository root at https://repository.spotify.com. The prior
# pool-listing flow relied on HTTPS-only soft trust (no signed
# metadata, no per-version sha256). The new flow uses the signed
# InRelease + Packages chain that igos_helper_verify_deb_via_signed_release
# verifies. Spotify's apt repo uses the `non-free` component (not
# `main`) so the helper-lib function takes the optional 6th arg.
SPOTIFY_APT_BASE="https://repository.spotify.com"
SPOTIFY_DIST="stable"
SPOTIFY_PKG_NAME="spotify-client"
SPOTIFY_COMPONENT="non-free"
SPOTIFY_KEYRING="/usr/share/igos/helpers/keyrings/spotify-keyring.gpg"
TMPDIR=$(mktemp -d)
# BLOCKING-D fix (2026-05-19): register TMPDIR cleanup via the
# helper-lib's IGOS_HELPER_USER_CLEANUP env var instead of `trap EXIT`.
# Installing a native trap would collide with the one igos_helper_init
# installs for partial-manifest sidecar emission (bash trap-replace
# semantics; no native composition).
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS Spotify Installer"
echo "  =============================="
echo ""
echo "  Spotify is proprietary software."
echo "  License: https://www.spotify.com/legal/end-user-agreement/"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install spotify' instead."
    echo "  Installing this way does not record the files with pkm;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

# EULA acceptance gate. The acceptance record at /var/lib/intergen/legal/
# is intentionally NOT manifest-tracked: pkm remove leaves it in place
# so a subsequent reinstall reads existing acceptance and skips the
# re-prompt (Row F decision 2026-05-19; ffmpeg-nonfree canonical
# K21.C pattern). The record IS captured in pkm's operation log as
# transparency content via the post_install_action below.
if [ -f "$ACCEPTANCE_FILE" ]; then
    echo "  Acceptance already recorded at $ACCEPTANCE_FILE"
    echo "  Proceeding to install."
else
    echo ""
    echo "  Do you accept Spotify's end-user agreement above and authorize"
    echo "  installing Spotify on this machine for your own use?"
    echo "  Type 'I ACCEPT' (exact match, capitals) to proceed:"
    echo ""
    read -r REPLY
    if [ "$REPLY" != "I ACCEPT" ]; then
        echo "  Acceptance not given. Exiting."
        exit 10
    fi
    mkdir -p "$ACCEPTANCE_DIR"
    cat > "$ACCEPTANCE_FILE" <<JSON
{
  "helper": "spotify",
  "version": "1.0",
  "payload_license": "LicenseRef-Spotify-ToS",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "spotify"

# K21.C: capture the consent event in pkm's operation log as
# transparency content. The acceptance JSON itself is intentionally
# NOT manifest-tracked (see EULA acceptance gate above).
igos_helper_record_post_install_action \
    "User accepted Spotify end-user agreement (acceptance artifact at $ACCEPTANCE_FILE)"

echo "  Finding latest Spotify release in signed apt metadata..."
LATEST=$(igos_helper_find_latest_deb_in_packages "$SPOTIFY_PKG_NAME" "$SPOTIFY_APT_BASE" "$SPOTIFY_DIST" "$SPOTIFY_COMPONENT")
if [ -z "$LATEST" ]; then
    echo "  ERROR: Could not locate spotify-client in the official Spotify"
    echo "         apt Packages metadata at ${SPOTIFY_APT_BASE}/dists/${SPOTIFY_DIST}/${SPOTIFY_COMPONENT}/"
    exit 1
fi
DEB_NAME=$(echo "$LATEST" | cut -d'|' -f1)
SPOTIFY_VERSION=$(echo "$LATEST" | cut -d'|' -f2)
POOL_PATH=$(echo "$LATEST" | cut -d'|' -f3)
igos_helper_set_version "${SPOTIFY_VERSION:-unknown}"

echo "  Downloading ${DEB_NAME}..."
wget -q --show-progress -O "$TMPDIR/spotify.deb" "${SPOTIFY_APT_BASE}/${POOL_PATH}"

# K21.F: refuse install if signed-Release verification fails.
echo "  Verifying signed-Release integrity chain..."
if ! igos_helper_verify_deb_via_signed_release \
    "$DEB_NAME" \
    "$TMPDIR/spotify.deb" \
    "$SPOTIFY_APT_BASE" \
    "$SPOTIFY_KEYRING" \
    "$SPOTIFY_DIST" \
    "$SPOTIFY_COMPONENT"; then
    echo ""
    echo "  ERROR: Signed-Release verification FAILED for ${DEB_NAME}."
    echo "  Refusing to install. The downloaded .deb did not match the"
    echo "  vendor's signed apt-repo metadata. This may indicate network"
    echo "  corruption, a man-in-the-middle attack, or a vendor key"
    echo "  rotation. Do NOT extract the .deb manually."
    exit 1
fi

echo "  Extracting..."
cd "$TMPDIR"
ar x spotify.deb
tar xf data.tar.gz 2>/dev/null || tar xf data.tar.xz 2>/dev/null

echo "  Installing to /opt/spotify/..."
mkdir -p /opt/spotify
cp -a usr/share/spotify/* /opt/spotify/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true

# Spotify's .deb ships its launcher + icons under usr/share/spotify (now copied
# to /opt/spotify), NOT under usr/share/applications or usr/share/icons — so the
# two copies above are no-ops. Install the launcher into the menu and register
# the icons under hicolor (Icon=spotify-client) ourselves, or Spotify has no
# menu entry and no icon.
if [ -f /opt/spotify/spotify.desktop ]; then
    install -Dm644 /opt/spotify/spotify.desktop /usr/share/applications/spotify.desktop
fi
for n in 16 22 24 32 48 64 128 256 512; do
    [ -f "/opt/spotify/icons/spotify-linux-$n.png" ] && \
        install -Dm644 "/opt/spotify/icons/spotify-linux-$n.png" \
        "/usr/share/icons/hicolor/${n}x${n}/apps/spotify-client.png"
done

# Fix desktop file path (harmless for the PATH-based Exec=spotify; keeps any
# absolute /usr/share/spotify reference pointed at /opt/spotify).
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
for n in 16 22 24 32 48 64 128 256 512; do
    f="/usr/share/icons/hicolor/${n}x${n}/apps/spotify-client.png"
    [ -f "$f" ] && igos_helper_record_file "$f"
done

ln -sf /opt/spotify/spotify /usr/bin/spotify
igos_helper_record_symlink /usr/bin/spotify /opt/spotify/spotify

igos_helper_record_dep glibc

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
igos_helper_record_post_install_action "gtk-update-icon-cache /usr/share/icons/hicolor"

igos_helper_commit

echo ""
echo "  Spotify installed."
echo "  Run: spotify"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-spotify"
}
