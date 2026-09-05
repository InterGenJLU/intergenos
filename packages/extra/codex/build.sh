#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# codex 1.0 — Install OpenAI Codex (the command-line coding agent and its
# VS Code extension)
# InterGenOS extra tier
#
# The Codex CLI is published by OpenAI under Apache-2.0 on the npm registry
# (@openai/codex). The VS Code extension ("Codex – OpenAI's coding agent",
# identifier openai.chatgpt) and the Codex service are used under OpenAI's
# Terms of Use. This helper installs both from OpenAI's official channels.
# The user accepts OpenAI's terms by running this installer. Same shape as
# the claude-code helper (decided 2026-09-05).

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
    cat > "${DESTDIR}/usr/bin/igos-install-codex" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Codex Installer
#
# Installs the OpenAI Codex CLI and the Codex VS Code extension from
# OpenAI's official channels.
# Terms: https://openai.com/policies/terms-of-use/
#
# Records the install footprint via the /usr/share/igos/helpers/helper-lib.sh
# API so pkm files/verify/remove see what was installed.

set -e

source /usr/share/igos/helpers/helper-lib.sh

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/codex-1.0-accepted.json"

echo ""
echo "  InterGenOS Codex Installer"
echo "  =========================="
echo ""
echo "  Codex is OpenAI's coding agent. The command-line tool is open"
echo "  source (Apache-2.0); the VS Code extension and the Codex service"
echo "  are used under OpenAI's Terms of Use:"
echo "    https://openai.com/policies/terms-of-use/"
echo ""

# Canonical invocation guard. The npm global install needs root, and pkm's
# manifest ingestion at /var/lib/igos/helpers needs root, so a direct run
# only works as root anyway — and it bypasses the path that threads the
# manifest into the package database. Send users to the supported entry.
if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install codex' instead."
    echo "  Installing this way does not record the files with pkm;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

# Check for npm
if ! command -v npm >/dev/null 2>&1; then
    echo "  ERROR: npm not found. Install Node.js first."
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
    echo "  installing Codex on this machine for your own use?"
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
  "helper": "codex",
  "version": "1.0",
  "payload_license": "Apache-2.0 AND LicenseRef-OpenAI-Terms-of-Use",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "codex"

# Capture the consent event in pkm's operation log as transparency
# content. The acceptance JSON itself is intentionally NOT manifest-tracked
# (see the acceptance gate above).
igos_helper_record_post_install_action \
    "User accepted OpenAI's Terms of Use (acceptance artifact at $ACCEPTANCE_FILE)"

# Trust anchor and integrity posture for the npm-registry install path —
# the same posture the claude-code helper uses:
#
#   - The npm registry is the trust boundary. OpenAI publishes @openai/codex
#     with a registry signature (dist.signatures present in the registry
#     metadata, verified 2026-09-05); npm 9+ checks that signature on each
#     install. The platform binary arrives as the optional dependency
#     @openai/codex-linux-x64, published the same way.
#
#   - PIN to a specific version rather than @latest: reproducibility, and a
#     defense against a silently replaced package on a future install. Bump
#     the pin in a helper release when OpenAI publishes a new version.
#
#   - Run `npm audit --audit-level=critical` against the pinned version's
#     dependency tree before installing; a critical-severity advisory
#     refuses the install with a loud error.

# Pinned npm CLI version (bump in a helper release on an OpenAI release).
# Set 2026-09-05 to 0.153.4 (npm dist-tags.latest = 0.153.4, present on
# registry.npmjs.org; registry dist.integrity sha512-wbHDmit7S/YvBGVX1DQmk1
# 3xtWblZ2cApeJ/pB7xDZ10Cna+DZc5ij7f0F4OxdsXN4FW1oLT48OpogUI1+8Y2w==).
# This is the npm CLI pin ONLY; the VS Code extension is versioned
# independently and pinned separately in the extension block below.
CODEX_PINNED_VERSION="0.153.4"

# Pre-install audit against critical-severity npm advisories. Runs
# `npm audit` against a transient package.json in a temporary directory
# (the pinned version's dependency tree) and refuses install on critical
# findings. --audit-level=critical means lower-severity advisories are
# reported for information but do not refuse the install.
echo "  Checking the package tree for critical security advisories..."
AUDIT_TMPDIR=$(mktemp -d -t igos-codex-audit-XXXXXX)
cat > "$AUDIT_TMPDIR/package.json" << JSONEOF
{
  "name": "igos-codex-audit-shim",
  "version": "0.0.0",
  "private": true,
  "dependencies": {
    "@openai/codex": "${CODEX_PINNED_VERSION}"
  }
}
JSONEOF
(cd "$AUDIT_TMPDIR" && npm install --package-lock-only --no-audit --no-fund --silent 2>/dev/null) || {
    echo "  WARNING: the advisory check could not run. Continuing; the"
    echo "  signature check on the install itself still applies."
    AUDIT_SKIPPED=1
}
if [ "${AUDIT_SKIPPED:-0}" != "1" ]; then
    if ! (cd "$AUDIT_TMPDIR" && npm audit --audit-level=critical 2>&1); then
        echo ""
        echo "  ERROR: npm audit found CRITICAL-severity advisories in the"
        echo "  @openai/codex@${CODEX_PINNED_VERSION} dependency tree."
        echo "  Refusing to install. Nothing was installed."
        echo "  The advisory is printed above; this needs a fixed upstream"
        echo "  release before the package will install."
        rm -rf "$AUDIT_TMPDIR"
        exit 1
    fi
fi
rm -rf "$AUDIT_TMPDIR"
echo "  No critical security advisories."

echo "  Installing the Codex CLI via npm (pinned version ${CODEX_PINNED_VERSION})..."
npm install -g "@openai/codex@${CODEX_PINNED_VERSION}"

# The pinned version IS what was installed; record it directly rather than
# re-parsing `npm list` output. Falls back to npm list if the package name
# changed under the hood.
CODEX_VERSION="$CODEX_PINNED_VERSION"
if [ -z "$CODEX_VERSION" ]; then
    CODEX_VERSION=$(npm list -g @openai/codex 2>/dev/null \
                      | grep '@openai/codex@' \
                      | sed 's/.*@openai\/codex@//' \
                      | head -1)
fi
igos_helper_set_version "${CODEX_VERSION:-unknown}"

NPM_GLOBAL_ROOT=$(npm root -g 2>/dev/null || echo "/usr/lib/node_modules")
CODEX_DIR="$NPM_GLOBAL_ROOT/@openai/codex"

# Record every file under the npm-installed module dir, including the
# platform package npm nests under it. npm's global prefix typically lands
# under /usr/lib/node_modules, which matches the manifest's /usr/ allowlist.
if [ -d "$CODEX_DIR" ]; then
    while IFS= read -r f; do
        igos_helper_record_file "$f"
    done < <(find "$CODEX_DIR" -type f -o -type l 2>/dev/null)
fi

# Verify installation
if command -v codex >/dev/null 2>&1; then
    echo "  Codex CLI installed: $(codex --version 2>/dev/null || echo 'OK')"
    # npm creates a symlink at <prefix>/bin/codex pointing into the module
    # dir. Record it so pkm remove unlinks the binary surface.
    CODEX_BIN=$(command -v codex)
    CODEX_TARGET=$(readlink -f "$CODEX_BIN" 2>/dev/null || echo "$CODEX_BIN")
    if [ -L "$CODEX_BIN" ]; then
        igos_helper_record_symlink "$CODEX_BIN" "$CODEX_TARGET"
    elif [ -f "$CODEX_BIN" ]; then
        igos_helper_record_file "$CODEX_BIN"
    fi
else
    echo "  WARNING: codex command not found in PATH"
    echo "  You may need to add npm's global bin directory to your PATH"
fi

igos_helper_record_dep nodejs

# Install the VS Code / Code-OSS extension.
#
# The extension is a platform-specific package (it bundles its own copy of
# the Codex binary), versioned independently of the npm CLI, so it is
# pinned on its own: a decoupled version plus explicit targetPlatform,
# fetched by bytes from the gallery asset API, sha256-verified against a
# pinned known-good value (a mismatch refuses the install), then installed
# from the LOCAL file as the invoking user — never root, with that user's
# HOME — because `code --install-extension` run as root is refused by VS
# Code's super-user guard, the live gallery flow times out on constrained
# networks, and Code-OSS cannot reach the marketplace at all.
# Failures are LOUD and accurate: an integrity mismatch refuses the install;
# a headless-install miss leaves the VERIFIED .vsix behind with the exact
# manual command. Bump on an extension release: update CODEX_VSIX_VERSION
# and _SHA256 together. The asset is platform-specific, so _SHA256 is the
# sha of the ${CODEX_VSIX_PLATFORM} asset.
# Pinned 2026-09-05: gallery version 26.5901.22334 (the extension's own
# package.json carries the same string), publisher openai, name chatgpt.
CODEX_VSIX_VERSION="26.5901.22334"
CODEX_VSIX_PLATFORM="linux-x64"
CODEX_VSIX_SHA256="cd9cd06c5bfcc8e18972587d04ac9d08b04152ebf6de426233ddc812b05933ff"

ext_installed=0
ext_vsix="/tmp/codex-${CODEX_VSIX_VERSION}-${CODEX_VSIX_PLATFORM}.vsix"
code_bin="$(command -v code 2>/dev/null || command -v code-oss 2>/dev/null || true)"
if [ -n "${code_bin}" ]; then
    echo "  Installing the Codex extension (v${CODEX_VSIX_VERSION}, ${CODEX_VSIX_PLATFORM}) for VS Code..."
    gallery_url="https://openai.gallery.vsassets.io/_apis/public/gallery/publisher/openai/extension/chatgpt/${CODEX_VSIX_VERSION}/assetbyname/Microsoft.VisualStudio.Services.VSIXPackage?targetPlatform=${CODEX_VSIX_PLATFORM}"
    if curl --compressed -fSL -m 600 --retry 3 --retry-delay 2 -o "${ext_vsix}" "${gallery_url}"; then
        # Integrity gate: the gallery exposes no per-extension signed
        # manifest we can verify, so we pin the known-good sha256 of the
        # platform-specific asset. A mismatch refuses the install.
        dl_sha="$(sha256sum "${ext_vsix}" | awk '{print $1}')"
        if [ "${dl_sha}" != "${CODEX_VSIX_SHA256}" ]; then
            echo ""
            echo "  ERROR: the downloaded extension .vsix failed sha256 verification."
            echo "    expected ${CODEX_VSIX_SHA256}"
            echo "    got      ${dl_sha}"
            echo "  Refusing to install a file that does not match what this package"
            echo "  expects. Either the publisher replaced this version, or the"
            echo "  download was tampered with. The extension was NOT installed."
            rm -f "${ext_vsix}" 2>/dev/null || true
        else
            echo "  Extension .vsix verified (sha256 OK)."
            # Drop to the invoking user (with their HOME so the extension
            # lands in their profile) and install from the local file. The
            # drop needs pkm to pass SUDO_USER through the helper
            # environment; if SUDO_USER is stripped this branch runs as root
            # and VS Code's guard refuses the install.
            if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
                ext_as="sudo -u ${SUDO_USER} -H"
            else
                ext_as=""
            fi
            # Capture the install command's real output and exit status so a
            # failure reports what actually happened. The helper runs under
            # `set -e`, so the capture MUST be guarded with `|| ext_rc=$?`.
            ext_rc=0
            ext_out="$(timeout 300 ${ext_as} "${code_bin}" --install-extension "${ext_vsix}" 2>&1)" || ext_rc=$?
            if [ "${ext_rc}" -eq 0 ]; then
                printf '%s\n' "${ext_out}"
                echo "  VS Code extension installed (pinned, verified .vsix ${CODEX_VSIX_VERSION})."
                ext_installed=1
                rm -f "${ext_vsix}" 2>/dev/null || true
            else
                echo ""
                if [ "$(id -u)" = "0" ] && [ -z "${ext_as}" ]; then
                    echo "  WARNING: the extension install ran AS ROOT (no invoking user was"
                    echo "  visible) and VS Code's super-user guard refused it (exit ${ext_rc})."
                    echo "  VS Code will not install extensions while running as root. The"
                    echo "  command-line tool is installed and usable; to add the extension,"
                    echo "  run this as your own user:"
                    echo "    ${code_bin} --install-extension ${ext_vsix}"
                else
                    echo "  WARNING: the extension install failed (exit ${ext_rc})."
                fi
                echo "  Command output (tail):"
                printf '%s\n' "${ext_out}" | tail -n 12 | sed 's/^/      /'
                echo "  The VERIFIED .vsix is left at ${ext_vsix} — in VS Code run"
                echo "  'Extensions: Install from VSIX...' and pick that file, or run:"
                echo "    ${code_bin} --install-extension ${ext_vsix}"
            fi
        fi
    else
        echo ""
        echo "  WARNING: could not download the extension .vsix from the gallery"
        echo "  (network / timeout). The Codex CLI is installed and usable;"
        echo "  install the VS Code extension later, or run 'codex' in VS Code's"
        echo "  integrated terminal. Gallery asset:"
        echo "    ${gallery_url}"
    fi
else
    echo ""
    echo "  NOTE: VS Code is not installed — the Codex VS Code extension was"
    echo "  skipped (the CLI above is fully usable on its own). To add it later:"
    echo "    sudo pkm install vscode   # then:"
    echo "    sudo pkm install codex    # re-run; the extension step will run"
fi

# Record the VS Code extension install ONLY when it actually succeeded —
# the footprint must never assert state that is not true. The extension
# files live under the user's home, outside the manifest path allowlist,
# so pkm does not track this per-user state.
if [ "${ext_installed}" = "1" ]; then
    igos_helper_record_post_install_action \
        "VS Code extension openai.chatgpt (Codex) installed (per-user; not pkm-tracked)"
fi

igos_helper_commit

echo ""
echo "  Codex installed!"
echo ""
echo "  To sign in:"
echo "    codex           # Opens the browser to sign in with your ChatGPT account"
echo "    # OR"
echo "    export OPENAI_API_KEY=your-key-here"
echo "    codex"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-codex"
}
