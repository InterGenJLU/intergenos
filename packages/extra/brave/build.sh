#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# brave 1.0 — Download and install Brave Browser
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"

    # K21.E: install the pinned Brave apt-repo signing key (armored
    # .asc in repo for review-ability) as a dearmored binary keyring
    # at /usr/share/igos/helpers/keyrings/brave-keyring.gpg. The
    # runtime helper script sources helper-lib.sh and calls
    # igos_helper_verify_deb_via_signed_release with this keyring
    # path. Trust-anchor introduction methodology: the PGP public
    # key block was obtained from the vendor's primary publication
    # at https://brave.com/signing-keys/ (3 keys listed as "Brave
    # Linux Release" apt-repo signing keys). The 40-char hex
    # fingerprints are DERIVED from those published key blocks per
    # RFC 4880 (sha1 of the public key packet), not separately
    # transcribed from the vendor page. Empirical trust anchor:
    # gpgv-against-live-InRelease at clear-time reported active
    # signing fingerprint DBF1A116C220B8C7164F98230686B78420038257
    # (1 of the 3 keys in the published block); the other two
    # (47D32A74E9A9E013A4B4926C68D513D36A73CD96 and
    # B2A3DCA350E67256740DF904DE4EC67BE4B0DCA0) are rotation
    # successors not yet active.
    install -dm755 "${DESTDIR}/usr/share/igos/helpers/keyrings"
    gpg --dearmor < \
        "${IGOS_SOURCE_ROOT:-/mnt/intergenos}/packages/extra/brave/assets/brave-keyring.asc" \
        > "${DESTDIR}/usr/share/igos/helpers/keyrings/brave-keyring.gpg"
    chmod 644 "${DESTDIR}/usr/share/igos/helpers/keyrings/brave-keyring.gpg"

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

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/brave-1.0-accepted.json"

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
    echo "  ERROR: Run via 'sudo pkm install brave' instead."
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
    echo "  Do you accept Brave's license terms above and authorize"
    echo "  installing Brave Browser on this machine for your own use?"
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
  "helper": "brave",
  "version": "1.0",
  "payload_license": "LicenseRef-Brave-EULA",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "brave"

# K21.C: capture the consent event in pkm's operation log as
# transparency content. The acceptance JSON itself is intentionally
# NOT manifest-tracked (see EULA acceptance gate above).
igos_helper_record_post_install_action \
    "User accepted Brave license terms (acceptance artifact at $ACCEPTANCE_FILE)"

# K21.E: signed-Release verification chain. Source-of-truth for the
# latest .deb filename + sha256 is the apt-style metadata under
# /dists/stable/ on Brave's official apt repo. Helper-lib's
# igos_helper_find_latest_deb_in_packages reads Packages (untrusted
# at this stage; verify function re-fetches + verifies); the
# subsequent igos_helper_verify_deb_via_signed_release call signs
# off on InRelease GPG + Packages sha256 + .deb sha256 in a single
# fail-closed chain. Do NOT add fallback repositories without a
# security review: alternate download sources are a supply-chain
# vector (an earlier revision of this installer referenced an
# unrelated third-party mirror, now removed).
BRAVE_APT_BASE="https://brave-browser-apt-release.s3.brave.com"
BRAVE_DIST="stable"
BRAVE_PKG_NAME="brave-browser"
BRAVE_KEYRING="/usr/share/igos/helpers/keyrings/brave-keyring.gpg"

echo "  Finding latest Brave release in signed apt metadata..."
LATEST=$(igos_helper_find_latest_deb_in_packages "$BRAVE_PKG_NAME" "$BRAVE_APT_BASE" "$BRAVE_DIST")
if [ -z "$LATEST" ]; then
    echo "  ERROR: Could not locate a Brave package in the official apt"
    echo "         Packages metadata at ${BRAVE_APT_BASE}/dists/${BRAVE_DIST}/"
    echo ""
    echo "  This may be a transient outage. Retry later, or check"
    echo "  https://brave.com/ for service advisories."
    echo ""
    echo "  This installer intentionally uses ONLY Brave's official"
    echo "  distribution. Do not patch in alternate sources without"
    echo "  security review."
    exit 1
fi
DEB_NAME=$(echo "$LATEST" | cut -d'|' -f1)
BRAVE_VERSION=$(echo "$LATEST" | cut -d'|' -f2)
POOL_PATH=$(echo "$LATEST" | cut -d'|' -f3)
igos_helper_set_version "${BRAVE_VERSION:-unknown}"

echo "  Downloading ${DEB_NAME}..."
wget -q --show-progress -O "$TMPDIR/brave.deb" "${BRAVE_APT_BASE}/${POOL_PATH}"

# K21.E: refuse install if signed-Release verification fails. This
# checks InRelease GPG signature (against the pinned keyring at
# ${BRAVE_KEYRING}) + Packages sha256 (against InRelease) + .deb
# sha256 (against Packages). Any step failure refuses install.
echo "  Verifying signed-Release integrity chain..."
if ! igos_helper_verify_deb_via_signed_release \
    "$DEB_NAME" \
    "$TMPDIR/brave.deb" \
    "$BRAVE_APT_BASE" \
    "$BRAVE_KEYRING" \
    "$BRAVE_DIST"; then
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
ar x brave.deb
tar xf data.tar.xz

echo "  Installing to /opt/brave.com/brave/..."
cp -a opt/brave.com /opt/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true

# The .desktop uses Exec=/usr/bin/brave-browser-stable, which the .deb's
# postinst would create. We do not run postinst, so copy the vendor's usr/bin
# launcher symlink(s) — otherwise the menu launcher's Exec is dead.
cp -a usr/bin/* /usr/bin/ 2>/dev/null || true

# Brave ships its icons as /opt/brave.com/brave/product_logo_<N>.png and relies
# on postinst to register them under hicolor; replicate so Icon=brave-browser
# resolves (without this the launcher shows no icon).
for n in 16 24 32 48 64 128 256; do
    [ -f "/opt/brave.com/brave/product_logo_$n.png" ] && \
        install -Dm644 "/opt/brave.com/brave/product_logo_$n.png" \
        "/usr/share/icons/hicolor/${n}x${n}/apps/brave-browser.png"
done

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

# Record the vendor launcher symlink (the .desktop Exec target) + hicolor icons.
[ -e /usr/bin/brave-browser-stable ] && igos_helper_record_file /usr/bin/brave-browser-stable
for n in 16 24 32 48 64 128 256; do
    f="/usr/share/icons/hicolor/${n}x${n}/apps/brave-browser.png"
    [ -f "$f" ] && igos_helper_record_file "$f"
done

igos_helper_record_dep glibc

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
igos_helper_record_post_install_action "gtk-update-icon-cache /usr/share/icons/hicolor"

igos_helper_commit

echo ""
echo "  Brave Browser installed."
echo "  Run: brave-browser"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-brave"
}
