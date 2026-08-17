#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# discord 1.0 — Download and install Discord
# InterGenOS extra tier

configure() { :; }
build() { :; }

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-discord" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Discord Installer
#
# Downloads and installs Discord from official source.
# License: https://discord.com/terms
#
# H-007 Phase B migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API.

set -e

source /usr/share/igos/helpers/helper-lib.sh

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/discord-1.0-accepted.json"

DISCORD_URL="https://discord.com/api/download?platform=linux&format=tar.gz"
TMPDIR=$(mktemp -d)
# BLOCKING-D fix (2026-05-19): register TMPDIR cleanup via the
# helper-lib's IGOS_HELPER_USER_CLEANUP env var instead of `trap EXIT`.
# Installing a native trap would collide with the one igos_helper_init
# installs for partial-manifest sidecar emission (bash trap-replace
# semantics; no native composition).
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS Discord Installer"
echo "  =============================="
echo ""
echo "  Discord is proprietary software."
echo "  License: https://discord.com/terms"
echo ""
echo "  READ THIS BEFORE YOU AGREE — this download is less protected"
echo "  than the others:"
echo ""
echo "    Discord does not publish a signed package repository. This"
echo "    installer downloads Discord's tarball over HTTPS from"
echo "    discord.com and CANNOT check a cryptographic signature over"
echo "    its contents. The only thing protecting the download is the"
echo "    HTTPS connection to discord.com. If that server were"
echo "    compromised, or a certificate for it were stolen, this"
echo "    installer would have no way to tell that the file it received"
echo "    was not the one Discord published."
echo ""
echo "    Every other vendor installer here checks a signature over what"
echo "    it downloads, and refuses to install if the check fails."
echo "    Discord is the exception, and the gap is stated here rather"
echo "    than left for you to find out."
echo ""
echo "    Continuing records that you accept Discord's licence terms and"
echo "    that you have read this."
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install discord' instead."
    echo "  Installing this way does not record the files with pkm;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

# EULA + K21.F-Option-B trust-gap acceptance gate. The acceptance
# record at /var/lib/intergen/legal/ is intentionally NOT manifest-
# tracked: pkm remove leaves it in place so a subsequent reinstall
# reads existing acceptance and skips the re-prompt (Row F decision
# 2026-05-19; ffmpeg-nonfree canonical K21.C pattern). The
# record IS captured in pkm's operation log as transparency content
# via the post_install_action below. K21.F Option B (decided
# 2026-05-21): combined acceptance covers both license terms AND
# awareness of the HTTPS-only supply-chain trust posture; the JSON
# fields trust_anchor + trust_chain_caveat document the gap inline.
if [ -f "$ACCEPTANCE_FILE" ]; then
    echo "  Acceptance already recorded at $ACCEPTANCE_FILE"
    echo "  Proceeding to install."
else
    echo "  Do you accept Discord's license terms above AND understand"
    echo "  the HTTPS-only trust-posture disclosure above? Authorize"
    echo "  installing Discord on this machine for your own use?"
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
  "helper": "discord",
  "version": "1.0",
  "payload_license": "LicenseRef-Discord-ToS",
  "trust_anchor": "HTTPS-only (no cryptographic signature on tarball)",
  "trust_chain_caveat": "Discord does not publish a signed apt repository; the Snap-Store alternative is rejected by the project-canonical no-snapd directive (decided 2026-05-21); the K21.F Option B trust-gap disclosure was presented and accepted at install time.",
  "k21_f_option": "B",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "discord"

# K21.C + K21.F Option B: capture the consent event in pkm's
# operation log as transparency content. The acceptance JSON itself
# is intentionally NOT manifest-tracked (see EULA acceptance gate
# above). The recorded action explicitly cites BOTH license terms
# AND K21.F Option B trust-gap acknowledgment for cold-read
# auditability.
igos_helper_record_post_install_action \
    "User accepted Discord license terms + K21.F Option B HTTPS-only trust-gap disclosure (acceptance artifact at $ACCEPTANCE_FILE)"

# Discord's tarball doesn't have a version stamp in the URL; extract
# from the build info file inside the tarball after extract.
igos_helper_set_version "latest"

echo "  Downloading Discord..."
wget -q --show-progress -O "$TMPDIR/discord.tar.gz" "$DISCORD_URL"

echo "  Extracting..."
rm -rf /opt/discord
tar -xzf "$TMPDIR/discord.tar.gz" -C /opt/
# The tarball's top-level dir is "Discord" (capital), but the bundled launcher
# script resolves its updater bootstrap at /opt/discord/updater_bootstrap
# (lowercase) and the binary is the lowercase script /opt/discord/discord —
# normalize the install dir to lowercase or Discord cannot find its bootstrap
# and fails to start (the old capital /opt/Discord/Discord path no longer
# exists in Discord's updater-based packaging).
[ -d /opt/Discord ] && mv /opt/Discord /opt/discord

# H-007: record everything deposited under /opt/discord plus the
# .desktop launcher created below.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/discord -type f -o -type l 2>/dev/null)

echo "  Creating launcher..."
ln -sf /opt/discord/discord /usr/bin/discord
igos_helper_record_symlink /usr/bin/discord /opt/discord/discord

# Themed icon name, not the absolute vendor path: an absolute Icon= pins
# the vendor mark and no icon theme can ever restyle it. The shipped
# themes carry a "discord" entry; the vendor png is additionally staged
# into hicolor below as the universal fallback, so the name resolves on
# any theme.
if [ -f /opt/discord/discord.png ]; then
    mkdir -p /usr/share/icons/hicolor/256x256/apps
    cp /opt/discord/discord.png /usr/share/icons/hicolor/256x256/apps/discord.png
    igos_helper_record_file /usr/share/icons/hicolor/256x256/apps/discord.png
    command -v gtk-update-icon-cache >/dev/null 2>&1 &&         gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
fi

cat > /usr/share/applications/discord.desktop << 'DESKEOF'
[Desktop Entry]
Name=Discord
Comment=All-in-one voice and text chat
Exec=/opt/discord/discord
Icon=discord
Type=Application
Categories=Network;InstantMessaging;
StartupWMClass=discord
DESKEOF
igos_helper_record_file /usr/share/applications/discord.desktop

igos_helper_record_dep glibc

igos_helper_commit

echo ""
echo "  Discord installed."
echo "  Run: discord"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-discord"
}
