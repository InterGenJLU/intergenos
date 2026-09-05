#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# chatgpt 1.0 — Download and install the ChatGPT desktop app for Linux
# (ChatGPT, Work and Codex in one application)
# InterGenOS extra tier
#
# The app is proprietary software by OpenAI. This helper downloads it from
# OpenAI's official Linux package repository and verifies it through that
# repository's signed metadata. The user accepts OpenAI's terms by running
# this installer. Same shape as the chrome and vscode helpers (decided
# 2026-09-05).

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
    # Install the pinned OpenAI Linux-repository signing key (dearmored
    # binary keyring) at /usr/share/igos/helpers/keyrings/chatgpt-keyring.gpg.
    # The runtime helper sources helper-lib.sh and calls
    # igos_helper_verify_deb_via_signed_release with this keyring path.
    # Source of the key: the key OpenAI's own .deb installs as
    # /usr/share/keyrings/chatgpt-archive-keyring.gpg ("Codex Linux
    # Repository", fingerprint 3BFA 0E4A E8B8 CC16 A2D9 BA68 4A3B 4A56 6C46
    # 60E4); it signs both the repository's InRelease and the .deb itself
    # (verified 2026-09-05 against chatgpt_26.901.41600_amd64.deb).
    install -dm755 "${DESTDIR}/usr/share/igos/helpers/keyrings"
    gpg --dearmor < \
        "${IGOS_SOURCE_ROOT:-/mnt/intergenos}/packages/extra/chatgpt/assets/chatgpt-keyring.asc" \
        > "${DESTDIR}/usr/share/igos/helpers/keyrings/chatgpt-keyring.gpg"
    chmod 644 "${DESTDIR}/usr/share/igos/helpers/keyrings/chatgpt-keyring.gpg"
    cat > "${DESTDIR}/usr/bin/igos-install-chatgpt" << 'HELPEREOF'
#!/bin/bash
# InterGenOS ChatGPT Desktop App Installer
#
# Downloads and installs the ChatGPT desktop app for Linux (ChatGPT, Work
# and Codex) from OpenAI's official Linux package repository.
# Terms: https://openai.com/policies/terms-of-use/
#
# Records the install footprint via the /usr/share/igos/helpers/helper-lib.sh
# API so pkm files/verify/remove see what was installed.

set -e

source /usr/share/igos/helpers/helper-lib.sh

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/chatgpt-1.0-accepted.json"

# OpenAI's Linux repository. Its InRelease is signed by the key shipped in
# this package's keyring, the Packages file is checked against InRelease,
# and the .deb is checked against Packages — the same chain the chrome and
# vscode helpers verify.
CHATGPT_APT_BASE="https://persistent.oaistatic.com/codex-app-prod/linux/deb"
CHATGPT_DIST="stable"
CHATGPT_PKG_NAME="chatgpt"
CHATGPT_KEYRING="/usr/share/igos/helpers/keyrings/chatgpt-keyring.gpg"
TMPDIR=$(mktemp -d)
# Register TMPDIR cleanup via the helper library's cleanup variable instead
# of `trap EXIT`; the library installs its own EXIT trap for partial-manifest
# recovery and bash keeps only one trap per signal.
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS ChatGPT Desktop App Installer"
echo "  ========================================="
echo ""
echo "  The ChatGPT desktop app (ChatGPT, Work and Codex) is proprietary"
echo "  software by OpenAI, used under OpenAI's Terms of Use:"
echo "    https://openai.com/policies/terms-of-use/"
echo "  The download is about 410 MB; the app takes about 1.4 GB installed."
echo ""

# Check for root
if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install chatgpt' instead."
    echo "  Installing this way does not record the files with pkm;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

# Terms acceptance gate. The acceptance record at /var/lib/intergen/legal/
# is intentionally NOT manifest-tracked: pkm remove leaves it in place so
# a later reinstall reads the existing acceptance and skips the re-prompt
# (the same rule every vendor-terms helper follows). The record IS
# captured in pkm's operation log as transparency content via the
# post_install_action below.
if [ -f "$ACCEPTANCE_FILE" ]; then
    echo "  Acceptance already recorded at $ACCEPTANCE_FILE"
    echo "  Proceeding to install."
else
    echo ""
    echo "  Do you accept OpenAI's Terms of Use above and authorize"
    echo "  installing the ChatGPT desktop app on this machine for your own use?"
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
  "helper": "chatgpt",
  "version": "1.0",
  "payload_license": "LicenseRef-OpenAI-Terms-of-Use",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "chatgpt"

# Capture the consent event in pkm's operation log as transparency
# content. The acceptance JSON itself is intentionally NOT manifest-tracked
# (see the acceptance gate above).
igos_helper_record_post_install_action \
    "User accepted OpenAI's Terms of Use (acceptance artifact at $ACCEPTANCE_FILE)"

echo "  Finding the latest ChatGPT desktop app release in signed repository metadata..."
LATEST=$(igos_helper_find_latest_deb_in_packages "$CHATGPT_PKG_NAME" "$CHATGPT_APT_BASE" "$CHATGPT_DIST")
if [ -z "$LATEST" ]; then
    echo "  ERROR: Could not locate the chatgpt package in OpenAI's repository"
    echo "         metadata at ${CHATGPT_APT_BASE}/dists/${CHATGPT_DIST}/"
    exit 1
fi
DEB_NAME=$(echo "$LATEST" | cut -d'|' -f1)
CHATGPT_VERSION=$(echo "$LATEST" | cut -d'|' -f2)
POOL_PATH=$(echo "$LATEST" | cut -d'|' -f3)
igos_helper_set_version "${CHATGPT_VERSION:-unknown}"

echo "  Downloading ${DEB_NAME}..."
wget -q --show-progress -O "$TMPDIR/chatgpt.deb" "${CHATGPT_APT_BASE}/${POOL_PATH}"

# Refuse install if signed-Release verification fails.
echo "  Verifying signed-Release integrity chain..."
if ! igos_helper_verify_deb_via_signed_release \
    "$DEB_NAME" \
    "$TMPDIR/chatgpt.deb" \
    "$CHATGPT_APT_BASE" \
    "$CHATGPT_KEYRING" \
    "$CHATGPT_DIST"; then
    echo ""
    echo "  ERROR: Signed-Release verification FAILED for ${DEB_NAME}."
    echo "  Refusing to install. The downloaded .deb did not match the"
    echo "  vendor's signed repository metadata. This may indicate network"
    echo "  corruption, a man-in-the-middle attack, or a vendor key"
    echo "  rotation. Do NOT extract the .deb manually."
    exit 1
fi

echo "  Extracting to /opt/chatgpt/..."
cd "$TMPDIR"
ar x chatgpt.deb
# The .deb uses data.tar.xz today; probe the alternatives (gz / zst) so a
# future compressor change does not break the install silently.
if [ -f data.tar.xz ]; then
    tar -xf data.tar.xz
elif [ -f data.tar.zst ]; then
    tar --zstd -xf data.tar.zst
elif [ -f data.tar.gz ]; then
    tar -xzf data.tar.gz
fi
if [ ! -d usr/lib/chatgpt ]; then
    echo "  ERROR: the package layout changed — usr/lib/chatgpt is not in the"
    echo "  extracted archive. Nothing was installed."
    exit 1
fi

# The .deb's own maintainer scripts are NOT run. They would add an apt
# source and an AppArmor profile; neither applies here. The app's profile
# only marks it unconfined with user namespaces allowed, and unprivileged
# user namespaces are already available on InterGenOS, so the app's
# sandbox works without a profile.
rm -rf /opt/chatgpt
mkdir -p /opt/chatgpt
cp -a usr/lib/chatgpt/. /opt/chatgpt/
mkdir -p /usr/share/applications /usr/share/pixmaps
# The .desktop file is vendor-shipped as-is: it runs `chatgpt` from PATH and
# names the icon `chatgpt`, which the pixmap below resolves.
cp -a usr/share/applications/chatgpt.desktop /usr/share/applications/chatgpt.desktop
if [ -f usr/share/pixmaps/chatgpt.png ]; then
    install -m644 usr/share/pixmaps/chatgpt.png /usr/share/pixmaps/chatgpt.png
elif [ -f /opt/chatgpt/resources/icon-chatgpt.png ]; then
    install -m644 /opt/chatgpt/resources/icon-chatgpt.png /usr/share/pixmaps/chatgpt.png
fi

# The app's bundled node tree carries prebuilt native modules for other
# architectures as well as this one (an Android ARM build of a database
# module sits beside the x86-64 one, for example). They are inert on this
# machine, but the recorder's default contract is "every deposited binary
# is 64-bit" and it refused the first foreign-width file, leaving the
# whole payload on disk untracked (observed 2026-09-05 on the first real
# run). The vendor ships those widths on purpose; say so. The declaration
# is recorded in the package's manifest, so the fact stays with the
# install. Same declaration the CUDA toolkit helper makes.
export IGOS_HELPER_ELF_CLASS=mixed

# Record everything deposited under /opt/chatgpt plus the launcher entry
# and the icon.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/chatgpt -type f -o -type l 2>/dev/null)
for f in /usr/share/applications/chatgpt.desktop /usr/share/pixmaps/chatgpt.png; do
    if [ -f "$f" ]; then
        igos_helper_record_file "$f"
    fi
done

# /usr/bin/chatgpt -> the vendor's launcher script, which starts the app
# from its own directory (the .deb ships the same link, relative to
# /usr/lib/chatgpt).
ln -sf /opt/chatgpt/codex-launcher /usr/bin/chatgpt
igos_helper_record_symlink /usr/bin/chatgpt /opt/chatgpt/codex-launcher

igos_helper_record_dep glibc

igos_helper_commit

echo ""
echo "  ChatGPT desktop app installed."
echo "  Run: chatgpt      # then sign in with your ChatGPT account"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-chatgpt"
}
