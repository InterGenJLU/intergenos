#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# edge 1.0 — Download and install Microsoft Edge
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"

    # K21.E: install the pinned Microsoft Edge / packages.microsoft.com
    # apt-repo signing key (armored .asc in repo for review-ability) as
    # a dearmored binary keyring at /usr/share/igos/helpers/keyrings/
    # edge-keyring.gpg. The runtime helper sources helper-lib.sh and
    # calls igos_helper_verify_deb_via_signed_release with this keyring
    # path. Fingerprint verified against vendor publication at
    # https://learn.microsoft.com/en-us/linux/packages: key
    # BC528686B50D79E339D3721CEB3E94ADBE1229CF ("Microsoft Release
    # signing" -- microsoft.asc; the key associated with repositories
    # created before May 2025 including the edge stable channel).
    install -dm755 "${DESTDIR}/usr/share/igos/helpers/keyrings"
    gpg --dearmor < \
        "${IGOS_SOURCE_ROOT:-/mnt/intergenos}/packages/extra/edge/assets/edge-keyring.asc" \
        > "${DESTDIR}/usr/share/igos/helpers/keyrings/edge-keyring.gpg"
    chmod 644 "${DESTDIR}/usr/share/igos/helpers/keyrings/edge-keyring.gpg"

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

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/edge-1.0-accepted.json"

# K21.E: signed-Release verification chain. Source-of-truth for the
# latest .deb filename + sha256 is the apt-style metadata under
# /repos/edge/dists/stable/ on Microsoft's official PMC service
# (packages.microsoft.com). Helper-lib's
# igos_helper_find_latest_deb_in_packages reads Packages (untrusted
# at this stage; verify function re-fetches + verifies); the
# subsequent igos_helper_verify_deb_via_signed_release call signs
# off on InRelease GPG + Packages sha256 + .deb sha256.
EDGE_APT_BASE="https://packages.microsoft.com/repos/edge"
EDGE_DIST="stable"
EDGE_PKG_NAME="microsoft-edge-stable"
EDGE_KEYRING="/usr/share/igos/helpers/keyrings/edge-keyring.gpg"
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
    echo "  ERROR: Run via 'sudo pkm install edge' instead."
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
    echo "  Do you accept Microsoft's services agreement above and authorize"
    echo "  installing Microsoft Edge on this machine for your own use?"
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
  "helper": "edge",
  "version": "1.0",
  "payload_license": "LicenseRef-Microsoft-Edge-EULA",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "edge"

# K21.C: capture the consent event in pkm's operation log as
# transparency content. The acceptance JSON itself is intentionally
# NOT manifest-tracked (see EULA acceptance gate above).
igos_helper_record_post_install_action \
    "User accepted Microsoft Edge license terms (acceptance artifact at $ACCEPTANCE_FILE)"

echo "  Finding latest Microsoft Edge release in signed apt metadata..."
LATEST=$(igos_helper_find_latest_deb_in_packages "$EDGE_PKG_NAME" "$EDGE_APT_BASE" "$EDGE_DIST")
if [ -z "$LATEST" ]; then
    echo "  ERROR: Could not locate microsoft-edge-stable in the official"
    echo "         PMC apt Packages metadata at ${EDGE_APT_BASE}/dists/${EDGE_DIST}/"
    exit 1
fi
DEB_NAME=$(echo "$LATEST" | cut -d'|' -f1)
EDGE_VERSION=$(echo "$LATEST" | cut -d'|' -f2)
POOL_PATH=$(echo "$LATEST" | cut -d'|' -f3)
igos_helper_set_version "${EDGE_VERSION:-unknown}"

echo "  Downloading ${DEB_NAME}..."
wget -q --show-progress -O "$TMPDIR/edge.deb" "${EDGE_APT_BASE}/${POOL_PATH}"

# K21.E: refuse install if signed-Release verification fails. This
# checks InRelease GPG signature (against the pinned keyring at
# ${EDGE_KEYRING}) + Packages sha256 (against InRelease) + .deb
# sha256 (against Packages). Any step failure refuses install.
echo "  Verifying signed-Release integrity chain..."
if ! igos_helper_verify_deb_via_signed_release \
    "$DEB_NAME" \
    "$TMPDIR/edge.deb" \
    "$EDGE_APT_BASE" \
    "$EDGE_KEYRING" \
    "$EDGE_DIST"; then
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
ar x edge.deb
tar xf data.tar.xz

echo "  Installing to /opt/microsoft/msedge/..."
cp -a opt/microsoft /opt/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true
cp -a usr/share/man/* /usr/share/man/ 2>/dev/null || true

# The .desktop uses Exec=/usr/bin/microsoft-edge-stable, which the .deb's
# postinst would create. We do not run postinst, so copy the vendor's usr/bin
# launcher symlink(s) — otherwise the menu launcher's Exec is dead.
cp -a usr/bin/* /usr/bin/ 2>/dev/null || true

# Edge ships its icons as /opt/microsoft/msedge/product_logo_<N>.png and relies
# on postinst to register them under hicolor; replicate so Icon=microsoft-edge
# resolves (without this the launcher shows no icon).
for n in 16 24 32 48 64 128 256; do
    [ -f "/opt/microsoft/msedge/product_logo_$n.png" ] && \
        install -Dm644 "/opt/microsoft/msedge/product_logo_$n.png" \
        "/usr/share/icons/hicolor/${n}x${n}/apps/microsoft-edge.png"
done

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

# Record the vendor launcher symlink (the .desktop Exec target) + hicolor icons.
[ -e /usr/bin/microsoft-edge-stable ] && igos_helper_record_file /usr/bin/microsoft-edge-stable
for n in 16 24 32 48 64 128 256; do
    f="/usr/share/icons/hicolor/${n}x${n}/apps/microsoft-edge.png"
    [ -f "$f" ] && igos_helper_record_file "$f"
done

igos_helper_record_dep glibc

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
igos_helper_record_post_install_action "gtk-update-icon-cache /usr/share/icons/hicolor"

igos_helper_commit

echo ""
echo "  Microsoft Edge installed."
echo "  Run: microsoft-edge"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-edge"
}
