#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# signal 1.0 — Download and install Signal Desktop
# InterGenOS extra tier
#
# Signal Desktop is Free Software (AGPL-3.0-only) but is vendored: this
# helper downloads it at runtime from Signal's official apt repository
# rather than building it from source, so it follows the download-helper
# pattern shared with the other extra-tier vendor apps.

configure() { :; }
build() { :; }

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"

    # K21.F: install the pinned Signal apt-repo signing key (armored
    # .asc in repo for review-ability) as a dearmored binary keyring
    # at /usr/share/igos/helpers/keyrings/signal-keyring.gpg. The
    # runtime helper sources helper-lib.sh and calls
    # igos_helper_verify_deb_via_signed_release with this keyring
    # path. Trust-anchor introduction methodology: the PGP public key
    # block was obtained from Signal's primary publication at
    # https://updates.signal.org/desktop/apt/keys.asc (the same key the
    # vendor's own documented install flow at https://signal.org/
    # download/linux/ dearmors into /usr/share/keyrings/signal-desktop-
    # keyring.gpg). The key's User ID is "Open Whisper Systems
    # <support@whispersystems.org>" — the historical Signal-maintainer
    # identity that still signs the Signal Desktop apt repo. The
    # signed InRelease + Packages + .deb sha256 chain is what
    # igos_helper_verify_deb_via_signed_release verifies fail-closed.
    install -dm755 "${DESTDIR}/usr/share/igos/helpers/keyrings"
    gpg --dearmor < \
        "${IGOS_SOURCE_ROOT:-/mnt/intergenos}/packages/extra/signal/assets/signal-keyring.asc" \
        > "${DESTDIR}/usr/share/igos/helpers/keyrings/signal-keyring.gpg"
    chmod 644 "${DESTDIR}/usr/share/igos/helpers/keyrings/signal-keyring.gpg"

    cat > "${DESTDIR}/usr/bin/igos-install-signal" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Signal Desktop Installer
#
# Downloads and installs Signal Desktop from Signal's official source.
# Signal Desktop is Free Software: https://github.com/signalapp/Signal-Desktop
# (AGPL-3.0-only). Use of the Signal service is governed by Signal's
# Terms & Privacy Policy: https://signal.org/legal/
#
# H-007 Phase B migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API.

set -e

source /usr/share/igos/helpers/helper-lib.sh

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/signal-1.0-accepted.json"

# K21.F: signed-Release verification chain against Signal's official
# apt repository. The repo's apt layout uses the historical `xenial`
# suite + `main` component (the same values published in Signal's
# official signal-desktop.sources at https://updates.signal.org/
# static/desktop/apt/signal-desktop.sources). Do NOT add fallback
# repositories without a security review: alternate download sources
# are a supply-chain vector.
SIGNAL_APT_BASE="https://updates.signal.org/desktop/apt"
SIGNAL_DIST="xenial"
SIGNAL_PKG_NAME="signal-desktop"
SIGNAL_KEYRING="/usr/share/igos/helpers/keyrings/signal-keyring.gpg"
TMPDIR=$(mktemp -d)
# BLOCKING-D fix (2026-05-19): register TMPDIR cleanup via the
# helper-lib's IGOS_HELPER_USER_CLEANUP env var instead of `trap EXIT`.
# Installing a native trap would collide with the one igos_helper_init
# installs for partial-manifest sidecar emission (bash trap-replace
# semantics; no native composition).
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS Signal Desktop Installer"
echo "  ====================================="
echo ""
echo "  Signal Desktop is Free Software (AGPL-3.0-only)."
echo "  Use of the Signal service is governed by Signal's terms:"
echo "  https://signal.org/legal/"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install signal' instead."
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
    echo "  Signal Desktop is Free Software, but use of the Signal"
    echo "  messaging service is governed by Signal's Terms & Privacy"
    echo "  Policy. Do you accept Signal's terms above and authorize"
    echo "  installing Signal Desktop on this machine for your own use?"
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
  "helper": "signal",
  "version": "1.0",
  "payload_license": "AGPL-3.0-only",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "signal"

# K21.C: capture the consent event in pkm's operation log as
# transparency content. The acceptance JSON itself is intentionally
# NOT manifest-tracked (see EULA acceptance gate above).
igos_helper_record_post_install_action \
    "User accepted Signal terms (acceptance artifact at $ACCEPTANCE_FILE)"

echo "  Finding latest Signal Desktop release in signed apt metadata..."
LATEST=$(igos_helper_find_latest_deb_in_packages "$SIGNAL_PKG_NAME" "$SIGNAL_APT_BASE" "$SIGNAL_DIST")
if [ -z "$LATEST" ]; then
    echo "  ERROR: Could not locate signal-desktop in the official Signal"
    echo "         apt Packages metadata at ${SIGNAL_APT_BASE}/dists/${SIGNAL_DIST}/"
    echo ""
    echo "  This may be a transient outage. Retry later, or check"
    echo "  https://signal.org/download/linux/ for service advisories."
    exit 1
fi
DEB_NAME=$(echo "$LATEST" | cut -d'|' -f1)
SIGNAL_VERSION=$(echo "$LATEST" | cut -d'|' -f2)
POOL_PATH=$(echo "$LATEST" | cut -d'|' -f3)
igos_helper_set_version "${SIGNAL_VERSION:-unknown}"

echo "  Downloading ${DEB_NAME}..."
wget -q --show-progress -O "$TMPDIR/signal.deb" "${SIGNAL_APT_BASE}/${POOL_PATH}"

# K21.F: refuse install if signed-Release verification fails. This
# checks InRelease GPG signature (against the pinned keyring at
# ${SIGNAL_KEYRING}) + Packages sha256 (against InRelease) + .deb
# sha256 (against Packages). Any step failure refuses install.
echo "  Verifying signed-Release integrity chain..."
if ! igos_helper_verify_deb_via_signed_release \
    "$DEB_NAME" \
    "$TMPDIR/signal.deb" \
    "$SIGNAL_APT_BASE" \
    "$SIGNAL_KEYRING" \
    "$SIGNAL_DIST"; then
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
ar x signal.deb
tar xf data.tar.xz 2>/dev/null || tar xf data.tar.gz 2>/dev/null || tar --zstd -xf data.tar.zst 2>/dev/null

echo "  Installing to /opt/Signal/..."
# Signal's .deb installs the app under /opt/Signal and ships its
# .desktop launcher + hicolor icons under usr/share.
cp -a opt/Signal /opt/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true

# The .deb's .desktop uses Exec=/opt/Signal/signal-desktop ... and
# relies on the .deb's postinst for the /usr/bin launcher. We do not run
# postinst, so create the launcher symlink ourselves — otherwise the
# menu entry's Exec target may not be on PATH.

# H-007: record everything deposited under /opt/Signal + the system-wide
# .desktop launchers + icons.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/Signal -type f -o -type l 2>/dev/null)
for f in /usr/share/applications/signal*.desktop; do
    if [ -f "$f" ]; then
        igos_helper_record_file "$f"
    fi
done
while IFS= read -r f; do
    [ -f "$f" ] && igos_helper_record_file "$f"
done < <(find /usr/share/icons -name 'signal*' -type f 2>/dev/null)

ln -sf /opt/Signal/signal-desktop /usr/bin/signal-desktop
igos_helper_record_symlink /usr/bin/signal-desktop /opt/Signal/signal-desktop

igos_helper_record_dep glibc

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
igos_helper_record_post_install_action "gtk-update-icon-cache /usr/share/icons/hicolor"

igos_helper_commit

echo ""
echo "  Signal Desktop installed."
echo "  Run: signal-desktop"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-signal"
}
