#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# chrome 1.0 — Download and install Google Chrome
# InterGenOS extra tier
#
# Google Chrome is proprietary software. This helper downloads it
# from Google's official distribution channel. The user accepts
# Google's license terms by running this installer.

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

    # K21.E: install the pinned Google Chrome apt-repo signing key
    # (armored .asc in repo for review-ability) as a dearmored binary
    # keyring at /usr/share/igos/helpers/keyrings/chrome-keyring.gpg.
    # The runtime helper sources helper-lib.sh and calls
    # igos_helper_verify_deb_via_signed_release with this keyring
    # path. Primary fingerprint verified against vendor publication
    # at https://www.google.com/linuxrepositories/: primary key
    # EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796 ("Linux Packages
    # Signing Authority"). The shipped keyring also contains the
    # rotating subkeys Google's apt repo signs InRelease with at any
    # given time (subkey rotation cadence ~yearly).
    install -dm755 "${DESTDIR}/usr/share/igos/helpers/keyrings"
    gpg --dearmor < \
        "${IGOS_SOURCE_ROOT:-/mnt/intergenos}/packages/extra/chrome/assets/chrome-keyring.asc" \
        > "${DESTDIR}/usr/share/igos/helpers/keyrings/chrome-keyring.gpg"
    chmod 644 "${DESTDIR}/usr/share/igos/helpers/keyrings/chrome-keyring.gpg"

    cat > "${DESTDIR}/usr/bin/igos-install-chrome" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Google Chrome Installer
#
# Downloads and installs Google Chrome from Google's official source.
# License: https://www.google.com/intl/en/chrome/terms/
#
# H-007 canary migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API so pkm files/verify/remove
# see chrome's deposited files.

set -e

# H-007: source the helper-lib API for footprint tracking.
source /usr/share/igos/helpers/helper-lib.sh

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/chrome-1.0-accepted.json"

# K21.E: pivot from the unsigned direct-deb URL to the apt-pool path.
# The direct URL (https://dl.google.com/linux/direct/google-chrome-
# stable_current_amd64.deb) is HTTPS-only soft trust -- no signed
# metadata, no per-version sha256. The apt-pool path provides the
# canonical signed-Release + Packages chain that
# igos_helper_verify_deb_via_signed_release verifies.
CHROME_APT_BASE="https://dl.google.com/linux/chrome/deb"
CHROME_DIST="stable"
CHROME_PKG_NAME="google-chrome-stable"
CHROME_KEYRING="/usr/share/igos/helpers/keyrings/chrome-keyring.gpg"
TMPDIR=$(mktemp -d)
# BLOCKING-D fix (2026-05-19): register TMPDIR cleanup via the
# helper-lib's IGOS_HELPER_USER_CLEANUP env var instead of `trap EXIT`.
# Installing a native trap would collide with the one igos_helper_init
# installs for partial-manifest sidecar emission (bash trap-replace
# semantics; no native composition).
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS Google Chrome Installer"
echo "  ==================================="
echo ""
echo "  Google Chrome is proprietary software."
echo "  License: https://www.google.com/intl/en/chrome/terms/"
echo ""

# Check for root
if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install chrome' instead."
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
    echo "  Do you accept Google's license terms above and authorize"
    echo "  installing Google Chrome on this machine for your own use?"
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
  "helper": "chrome",
  "version": "1.0",
  "payload_license": "LicenseRef-Google-Chrome-ToS",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

# H-007: initialize the manifest. Must come before any record_* call.
igos_helper_init "chrome"

# K21.C: capture the consent event in pkm's operation log as
# transparency content. The acceptance JSON itself is intentionally
# NOT manifest-tracked (see EULA acceptance gate above).
igos_helper_record_post_install_action \
    "User accepted Google license terms (acceptance artifact at $ACCEPTANCE_FILE)"

echo "  Finding latest Google Chrome release in signed apt metadata..."
LATEST=$(igos_helper_find_latest_deb_in_packages "$CHROME_PKG_NAME" "$CHROME_APT_BASE" "$CHROME_DIST")
if [ -z "$LATEST" ]; then
    echo "  ERROR: Could not locate google-chrome-stable in the official"
    echo "         apt Packages metadata at ${CHROME_APT_BASE}/dists/${CHROME_DIST}/"
    echo ""
    echo "  This may be a transient outage. Retry later or check"
    echo "  https://www.google.com/linuxrepositories/ for advisories."
    exit 1
fi
DEB_NAME=$(echo "$LATEST" | cut -d'|' -f1)
CHROME_VERSION=$(echo "$LATEST" | cut -d'|' -f2)
POOL_PATH=$(echo "$LATEST" | cut -d'|' -f3)
igos_helper_set_version "${CHROME_VERSION:-unknown}"

echo "  Downloading ${DEB_NAME}..."
wget -q --show-progress -O "$TMPDIR/chrome.deb" "${CHROME_APT_BASE}/${POOL_PATH}"

# K21.E: refuse install if signed-Release verification fails. This
# checks InRelease GPG signature (against the pinned keyring at
# ${CHROME_KEYRING}) + Packages sha256 (against InRelease) + .deb
# sha256 (against Packages). Any step failure refuses install.
echo "  Verifying signed-Release integrity chain..."
if ! igos_helper_verify_deb_via_signed_release \
    "$DEB_NAME" \
    "$TMPDIR/chrome.deb" \
    "$CHROME_APT_BASE" \
    "$CHROME_KEYRING" \
    "$CHROME_DIST"; then
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
ar x chrome.deb
tar xf data.tar.xz

echo "  Installing to /opt/google/chrome/..."
cp -a opt/google /opt/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true
cp -a usr/share/man/* /usr/share/man/ 2>/dev/null || true

# The .desktop uses Exec=/usr/bin/google-chrome-stable, which the .deb's
# postinst would create. We do not run postinst, so copy the vendor's usr/bin
# launcher symlink(s) — otherwise the menu launcher's Exec points at a path
# that does not exist and the icon is dead.
cp -a usr/bin/* /usr/bin/ 2>/dev/null || true

# Chrome ships its icons as /opt/google/chrome/product_logo_<N>.png and relies
# on postinst to register them under hicolor; replicate that so Icon=google-chrome
# resolves (without this the launcher shows no icon even though it installed).
for n in 16 24 32 48 64 128 256; do
    [ -f "/opt/google/chrome/product_logo_$n.png" ] && \
        install -Dm644 "/opt/google/chrome/product_logo_$n.png" \
        "/usr/share/icons/hicolor/${n}x${n}/apps/google-chrome.png"
done

# H-007: record every file deposited into /opt/google/chrome (the bulk
# of chrome's install footprint). Walk recursively + record each
# regular file. Symlinks inside /opt/google/chrome are recorded as
# regular files too — pkm-remove's os.remove() unlinks the symlink
# itself (POSIX unlink semantics) which is the desired behavior.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/google/chrome -type f -o -type l 2>/dev/null)

# Record .desktop launchers + icons + man pages that were copied
# system-wide (best-effort; the cp -a above may have skipped some
# subtrees if the .deb didn't include them).
for subtree in /usr/share/applications/google-chrome*.desktop \
               /usr/share/man/man*/google-chrome*; do
    for f in $subtree; do
        if [ -f "$f" ]; then
            igos_helper_record_file "$f"
        fi
    done
done

# Create the /usr/bin/google-chrome symlink + record it.
ln -sf /opt/google/chrome/google-chrome /usr/bin/google-chrome
igos_helper_record_symlink /usr/bin/google-chrome /opt/google/chrome/google-chrome

# Record the vendor launcher symlink (the .desktop Exec target) + the hicolor
# icons we installed, so pkm tracks + removes them.
[ -e /usr/bin/google-chrome-stable ] && igos_helper_record_file /usr/bin/google-chrome-stable
for n in 16 24 32 48 64 128 256; do
    f="/usr/share/icons/hicolor/${n}x${n}/apps/google-chrome.png"
    [ -f "$f" ] && igos_helper_record_file "$f"
done

# Record glibc as runtime dependency (chrome is dynamically linked
# against libc); other shared-library deps come along transitively.
igos_helper_record_dep glibc

# Update icon cache — descriptive only in v1.0 (per H-007 design Q3);
# pkm logs the action to operation history but does not replay it on
# remove.
gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
igos_helper_record_post_install_action \
    "gtk-update-icon-cache /usr/share/icons/hicolor"

# H-007: finalize the manifest. Atomic mv ensures pkm sees either the
# complete manifest or nothing at all — never a half-finished
# intermediate state.
igos_helper_commit

echo ""
echo "  Google Chrome installed."
echo "  Run: google-chrome"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-chrome"
}
