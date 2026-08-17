#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# vscode 1.0 — Download and install Microsoft Visual Studio Code
# InterGenOS extra tier
#
# VS Code is proprietary software. This helper downloads it from
# Microsoft's official distribution. The user accepts Microsoft's
# license terms by running this installer.

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e
    # Install the helper script
    mkdir -pv "${DESTDIR}/usr/bin"

    # K21.F: install the pinned Microsoft apt-repo signing key
    # (dearmored binary keyring) at /usr/share/igos/helpers/keyrings/
    # vscode-keyring.gpg. The runtime helper sources helper-lib.sh and
    # calls igos_helper_verify_deb_via_signed_release with this keyring
    # path. Trust-anchor reuse: the Microsoft apt-repo signing key is
    # the SAME key K21.E pinned for edge (microsoft.asc
    # fingerprint BC528686B50D79E339D3721CEB3E94ADBE1229CF, verified
    # against vendor publication at https://learn.microsoft.com/en-us/
    # linux/packages); reading the source .asc from edge's
    # assets dir at build time avoids redundant key duplication in the
    # repo per the K21.F recommendation. Cross-package
    # source coupling: vscode requires edge's assets dir
    # to exist at build time (acceptable since both ship together in
    # packages/extra/).
    install -dm755 "${DESTDIR}/usr/share/igos/helpers/keyrings"
    gpg --dearmor < \
        "${IGOS_SOURCE_ROOT:-/mnt/intergenos}/packages/extra/edge/assets/edge-keyring.asc" \
        > "${DESTDIR}/usr/share/igos/helpers/keyrings/vscode-keyring.gpg"
    chmod 644 "${DESTDIR}/usr/share/igos/helpers/keyrings/vscode-keyring.gpg"

    cat > "${DESTDIR}/usr/bin/igos-install-vscode" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Visual Studio Code Installer
#
# Downloads and installs VS Code from Microsoft's official source.
# License: https://code.visualstudio.com/license
#
# H-007 Phase B migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API.

set -e

source /usr/share/igos/helpers/helper-lib.sh

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/vscode-1.0-accepted.json"

# K21.F: pivot from the unsigned direct tar.gz URL (https://code.
# visualstudio.com/sha/download?build=stable&os=linux-x64) to the
# canonical apt-pool path on packages.microsoft.com. The direct URL
# was HTTPS-only soft trust -- no signed metadata, no per-version
# sha256. The apt-pool path provides the InRelease + Packages chain
# that igos_helper_verify_deb_via_signed_release verifies.
VSCODE_APT_BASE="https://packages.microsoft.com/repos/vscode"
VSCODE_DIST="stable"
VSCODE_PKG_NAME="code"
VSCODE_KEYRING="/usr/share/igos/helpers/keyrings/vscode-keyring.gpg"
TMPDIR=$(mktemp -d)
# BLOCKING-D fix (2026-05-19): register TMPDIR cleanup via the
# helper-lib's IGOS_HELPER_USER_CLEANUP env var instead of `trap EXIT`.
# Installing a native trap would collide with the one igos_helper_init
# installs for partial-manifest sidecar emission (bash trap-replace
# semantics; no native composition).
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS Visual Studio Code Installer"
echo "  ========================================"
echo ""
echo "  Visual Studio Code is proprietary software."
echo "  License: https://code.visualstudio.com/license"
echo ""

# Check for root
if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install vscode' instead."
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
    echo "  Do you accept Microsoft's VS Code license above and authorize"
    echo "  installing Visual Studio Code on this machine for your own use?"
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
  "helper": "vscode",
  "version": "1.0",
  "payload_license": "LicenseRef-Microsoft-VSCode-Software-License",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "vscode"

# K21.C: capture the consent event in pkm's operation log as
# transparency content. The acceptance JSON itself is intentionally
# NOT manifest-tracked (see EULA acceptance gate above).
igos_helper_record_post_install_action \
    "User accepted Microsoft VS Code license (acceptance artifact at $ACCEPTANCE_FILE)"

echo "  Finding latest Visual Studio Code release in signed apt metadata..."
LATEST=$(igos_helper_find_latest_deb_in_packages "$VSCODE_PKG_NAME" "$VSCODE_APT_BASE" "$VSCODE_DIST")
if [ -z "$LATEST" ]; then
    echo "  ERROR: Could not locate code package in the official PMC apt"
    echo "         Packages metadata at ${VSCODE_APT_BASE}/dists/${VSCODE_DIST}/"
    exit 1
fi
DEB_NAME=$(echo "$LATEST" | cut -d'|' -f1)
VSCODE_VERSION=$(echo "$LATEST" | cut -d'|' -f2)
POOL_PATH=$(echo "$LATEST" | cut -d'|' -f3)
igos_helper_set_version "${VSCODE_VERSION:-unknown}"

echo "  Downloading ${DEB_NAME}..."
wget -q --show-progress -O "$TMPDIR/vscode.deb" "${VSCODE_APT_BASE}/${POOL_PATH}"

# K21.F: refuse install if signed-Release verification fails.
echo "  Verifying signed-Release integrity chain..."
if ! igos_helper_verify_deb_via_signed_release \
    "$DEB_NAME" \
    "$TMPDIR/vscode.deb" \
    "$VSCODE_APT_BASE" \
    "$VSCODE_KEYRING" \
    "$VSCODE_DIST"; then
    echo ""
    echo "  ERROR: Signed-Release verification FAILED for ${DEB_NAME}."
    echo "  Refusing to install. The downloaded .deb did not match the"
    echo "  vendor's signed apt-repo metadata. This may indicate network"
    echo "  corruption, a man-in-the-middle attack, or a vendor key"
    echo "  rotation. Do NOT extract the .deb manually."
    exit 1
fi

echo "  Extracting to /opt/vscode/..."
cd "$TMPDIR"
ar x vscode.deb
# The code .deb has historically used data.tar.xz; probe alternatives
# (gz / zst) for forward-compatibility with future compressor swaps.
if [ -f data.tar.xz ]; then
    tar -xf data.tar.xz
elif [ -f data.tar.zst ]; then
    tar --zstd -xf data.tar.zst
elif [ -f data.tar.gz ]; then
    tar -xzf data.tar.gz
fi
rm -rf /opt/vscode
mkdir -p /opt/vscode
cp -a usr/share/code/. /opt/vscode/
mkdir -p /usr/share/applications
cp -a usr/share/applications/code*.desktop /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/man/* /usr/share/man/ 2>/dev/null || true

# Install VS Code's bundled icon under the name the .desktop references
# (Icon=vscode). The .deb ships usr/share/pixmaps/vscode.png; fall back to the
# app's own bundled icon if the layout changes. Without this the launcher shows
# no icon even though the .desktop installed fine.
mkdir -p /usr/share/pixmaps
if [ -f usr/share/pixmaps/vscode.png ]; then
    cp -a usr/share/pixmaps/vscode.png /usr/share/pixmaps/vscode.png
elif [ -f /opt/vscode/resources/app/resources/linux/code.png ]; then
    install -m644 /opt/vscode/resources/app/resources/linux/code.png /usr/share/pixmaps/vscode.png
fi

# The .deb's .desktop files hardcode Exec=/usr/share/code/code, but we install
# to /opt/vscode — rewrite so the launcher actually starts VS Code (otherwise
# the icon is dead: the Exec path does not exist on this system).
sed -i 's|/usr/share/code/code|/opt/vscode/code|g' /usr/share/applications/code*.desktop 2>/dev/null || true

# H-007: record everything deposited under /opt/vscode + the .desktop
# launchers + man pages.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/vscode -type f -o -type l 2>/dev/null)
for f in /usr/share/applications/code*.desktop /usr/share/man/man*/code* /usr/share/pixmaps/vscode.png; do
    if [ -f "$f" ]; then
        igos_helper_record_file "$f"
    fi
done

# Create /usr/bin/code -> the CLI WRAPPER /opt/vscode/bin/code, NOT the raw
# Electron binary /opt/vscode/code. BOTH ship in the .deb layout (usr/share/code/
# carries `code` AND `bin/code`, the latter a sh launcher); a prior change wrongly
# assumed bin/code was tar.gz-only and pointed /usr/bin/code at the raw binary.
# The raw Electron binary SEGFAULTS on every CLI invocation (`code --version` ->
# SIGSEGV, `code --install-extension` -> crash; SIGTRAP when run as root) because
# it tries to bring up the GUI with no display; the bin/code wrapper routes CLI
# args headlessly and works (`code --version` -> RC 0). This was the root cause of
# `pkm install claude-code` failing to install the VS Code extension. Proven on
# .241 2026-06-28: with /usr/bin/code -> the wrapper, `code --install-extension
# <vsix>` succeeds (RC 0) and the extension lands. The .desktop GUI launcher
# correctly keeps the raw binary (sed above) — only the CLI symlink changes.
ln -sf /opt/vscode/bin/code /usr/bin/code
igos_helper_record_symlink /usr/bin/code /opt/vscode/bin/code

igos_helper_record_dep glibc

igos_helper_commit

echo ""
echo "  Visual Studio Code installed."
echo "  Run: code"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-vscode"
}
