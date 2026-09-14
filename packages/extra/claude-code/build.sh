#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# claude-code 1.0 — Install Anthropic Claude Code
# InterGenOS extra tier
#
# Claude Code is proprietary software by Anthropic. This helper
# installs it via npm from Anthropic's official package. The user
# accepts Anthropic's license terms by running this installer.

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
    local recipe_dir
    recipe_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    install -Dm644 \
        "${recipe_dir}/claude-code-helper-functions.sh" \
        "${DESTDIR}/usr/libexec/igos-install-claude-code-functions.sh"
    # Install the helper script
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-claude-code" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Claude Code Installer
#
# Installs Claude Code CLI and VS Code extension from Anthropic.
# License: https://code.claude.com/docs/en/legal-and-compliance
#
# The reviewed version is the default. To request the registry's current
# stable CLI release explicitly, run:
#   sudo CLAUDE_CODE_INSTALL_CURRENT=1 pkm install claude-code
# The VS Code extension remains separately pinned and sha256-verified because
# its gallery does not provide a package signature this helper can verify.
# `--print-plan` shows the resolved mode and versions without installing.
#
# H-007 Phase B migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API.

set -e

source /usr/libexec/igos-install-claude-code-functions.sh

CLAUDE_CODE_PINNED_VERSION="2.1.270"
CLAUDE_CODE_VSIX_VERSION="2.1.270"
CLAUDE_CODE_VSIX_PLATFORM="linux-x64"
CLAUDE_CODE_VSIX_SHA256="e33b1b88312302953857b0a09fa65dc13f72d58730e71107385a26a30ddd8640"

if [ "${1:-}" = "--print-plan" ]; then
    claude_code_select_install_version "$CLAUDE_CODE_PINNED_VERSION"
    claude_code_print_plan \
        "$CLAUDE_CODE_VSIX_VERSION" \
        "$CLAUDE_CODE_VSIX_PLATFORM" \
        "$CLAUDE_CODE_VSIX_SHA256"
    exit 0
elif [ "$#" -ne 0 ]; then
    echo "  ERROR: unknown argument: $1" >&2
    exit 2
fi

source /usr/share/igos/helpers/helper-lib.sh

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/claude-code-1.0-accepted.json"

echo ""
echo "  InterGenOS Claude Code Installer"
echo "  ================================="
echo ""
echo "  Claude Code is proprietary software by Anthropic."
echo "  License: https://code.claude.com/docs/en/legal-and-compliance"
echo ""

# Canonical invocation guard. claude-code's npm install -g needs root,
# AND pkm's manifest ingestion at /var/lib/igos/helpers needs root,
# so direct invocation only works as root anyway — but it also
# bypasses pkm's _run_helper which is what threads the manifest into
# the DB. Send users at the supported entry point.
if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install claude-code' instead."
    echo "  Installing this way does not record the files with pkm;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

# Check for npm
if ! command -v npm >/dev/null 2>&1; then
    echo "  ERROR: npm not found. Install Node.js first."
    exit 1
fi

claude_code_select_install_version "$CLAUDE_CODE_PINNED_VERSION"
echo "  CLI install mode: ${CLAUDE_CODE_SELECTED_MODE}"
echo "  Reason: ${CLAUDE_CODE_SELECTED_REASON}"
echo "  CLI release selected: ${CLAUDE_CODE_SELECTED_VERSION}"
if [ "$CLAUDE_CODE_SELECTED_MODE" = "pinned" ]; then
    echo "  To request the registry's current stable CLI release instead:"
    echo "    sudo CLAUDE_CODE_INSTALL_CURRENT=1 pkm install claude-code"
else
    echo "  Current mode was explicitly requested with CLAUDE_CODE_INSTALL_CURRENT=1."
fi
echo "  VS Code extension: pinned ${CLAUDE_CODE_VSIX_VERSION} (${CLAUDE_CODE_VSIX_PLATFORM})"
if [ "$CLAUDE_CODE_SELECTED_VERSION" != "$CLAUDE_CODE_VSIX_VERSION" ]; then
    echo "  NOTE: CLI and extension versions differ. The extension remains pinned"
    echo "  because the gallery provides no package signature this helper can verify."
fi
echo ""

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
    echo "  Do you accept Anthropic's commercial terms above and authorize"
    echo "  installing Claude Code on this machine for your own use?"
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
  "helper": "claude-code",
  "version": "1.0",
  "payload_license": "LicenseRef-Anthropic-Commercial-Terms",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "claude-code"

# K21.C: capture the consent event in pkm's operation log as
# transparency content. The acceptance JSON itself is intentionally
# NOT manifest-tracked (see EULA acceptance gate above).
igos_helper_record_post_install_action \
    "User accepted Anthropic commercial terms (acceptance artifact at $ACCEPTANCE_FILE)"

# K21.F: trust-anchor + integrity-gate hardening for the npm-registry
# install path. Differences from the K21.E apt-Release pattern (used by
# brave/chrome/edge/vscode/spotify):
#
#   - npm registry is the trust boundary (Anthropic publishes signed
#     packages to https://registry.npmjs.org/@anthropic-ai/claude-code/).
#     `npm audit signatures` verifies each downloaded package against the
#     registry's published signing keys before the global install proceeds.
#
#   - Anthropic's @anthropic-ai/claude-code publishes WITH npm registry
#     signature (signatures[0].sig + keyid present in registry metadata)
#     but WITHOUT npm provenance attestations (dist.attestations = none when
#     the 2.1.270 pin was checked on 2026-09-14). Provenance is an upstream-side opt-in
#     (npm publish --provenance) -- our helper cannot add it.
#
#   - PIN to a specific reviewed version by default. An explicit environment
#     option resolves dist-tags.latest, validates it as a release version, and
#     applies the same advisory and signature gates to the selected release.
#
#   - Run `npm audit --audit-level=critical` pre-install against the
#     selected version's dep tree; if a critical-severity advisory exists,
#     refuse install with loud error per security-only-alignment rule #10 default-deny.

# Pinned npm CLI version (bump in a reviewed helper change on an Anthropic
# release). Bumped 2026-09-14 to 2.1.270: npm dist-tags.latest = 2.1.270;
# registry dist.integrity =
# sha512-0zMkfIWQu7/SG56VP8r780HZWvrNShzK28AbAnhKRK0ns+ToGXPT0W8UqyZmZCUKAkJDd5//TrwSOhk1+hysiw==.
# This is the npm CLI pin ONLY; the VS Code extension is versioned
# INDEPENDENTLY and pinned separately in the extension block below.

# This is the same function the unprivileged proof invokes with an isolated
# npm prefix: dependency-tree preparation, advisory gate, registry-signature
# gate, exact install, and executable version read-back are one code path.
claude_code_install_selected_cli \
    "$CLAUDE_CODE_SELECTED_MODE" "$CLAUDE_CODE_SELECTED_VERSION"
CLAUDE_VERSION="$CLAUDE_CODE_INSTALLED_VERSION"
CLAUDE_VERSION_OUTPUT="$CLAUDE_CODE_INSTALLED_VERSION_OUTPUT"
CLAUDE_BIN="$CLAUDE_CODE_INSTALLED_BIN"
NPM_GLOBAL_ROOT="$CLAUDE_CODE_INSTALLED_ROOT"
igos_helper_set_version "${CLAUDE_VERSION}"
CLAUDE_DIR="$NPM_GLOBAL_ROOT/@anthropic-ai/claude-code"

# H-007: record every file under the npm-installed module dir. npm's
# global prefix typically lands under /usr/lib/node_modules which
# matches the manifest's /usr/ allowlist.
if [ -d "$CLAUDE_DIR" ]; then
    while IFS= read -r f; do
        igos_helper_record_file "$f"
    done < <(find "$CLAUDE_DIR" -type f -o -type l 2>/dev/null)
fi

# npm creates a symlink at <prefix>/bin/claude pointing into the module dir.
# Record it so pkm remove unlinks the binary surface.
CLAUDE_TARGET=$(readlink -f "$CLAUDE_BIN" 2>/dev/null || echo "$CLAUDE_BIN")
if [ -L "$CLAUDE_BIN" ]; then
    igos_helper_record_symlink "$CLAUDE_BIN" "$CLAUDE_TARGET"
elif [ -f "$CLAUDE_BIN" ]; then
    igos_helper_record_file "$CLAUDE_BIN"
fi

igos_helper_record_dep nodejs

# Install the VS Code / Code-OSS extension.
#
# History: the old `code --install-extension anthropic.claude-code` one-liner
# failed three ways on real installs — (1) run as root, `code` is refused by
# VS Code's super-user guard (a nonzero exit on current versions; older ones
# SIGTRAP-crashed), and this helper runs under sudo; (2) the live marketplace gallery
# flow times out on hardened / constrained networks; (3) Code-OSS can't reach
# the MS marketplace at all (Open VSX only). The first rewrite fetched a .vsix
# from the gallery asset API and installed it from the local file as the
# invoking user — but it STILL failed because (a) it reused the npm CLI pin as
# the extension version, and the extension is versioned INDEPENDENTLY of the npm
# CLI, and (b) it omitted targetPlatform for what is a PLATFORM-SPECIFIC
# extension (it bundles a native binary), so the gallery returned the wrong /
# absent asset and the install fell through to a soft note.
#
# This block fixes both: a decoupled, pinned extension version + explicit
# targetPlatform, fetched by bytes from the gallery asset API, sha256-verified
# against a pinned known-good (default-deny on mismatch), then installed from
# the LOCAL file as the invoking user (never root, with that user's HOME).
# Failures are LOUD and accurate — an integrity mismatch refuses the install; a
# headless-install miss leaves the VERIFIED .vsix behind with the exact manual
# command. Bump on an extension release: update CLAUDE_CODE_VSIX_VERSION +
# _SHA256 together (atomic provenance, the keyring pattern). The asset is
# platform-specific, so _SHA256 is the sha of the ${CLAUDE_CODE_VSIX_PLATFORM}
# asset. Version 2.1.270's linux-x64 bytes were pinned on 2026-09-14.

ext_installed=0
ext_vsix="/tmp/claude-code-${CLAUDE_CODE_VSIX_VERSION}-${CLAUDE_CODE_VSIX_PLATFORM}.vsix"
code_bin="$(command -v code 2>/dev/null || command -v code-oss 2>/dev/null || true)"
if [ -n "${code_bin}" ]; then
    echo "  Installing Claude Code extension (v${CLAUDE_CODE_VSIX_VERSION}, ${CLAUDE_CODE_VSIX_PLATFORM}) for VS Code..."
    gallery_url="https://anthropic.gallery.vsassets.io/_apis/public/gallery/publisher/anthropic/extension/claude-code/${CLAUDE_CODE_VSIX_VERSION}/assetbyname/Microsoft.VisualStudio.Services.VSIXPackage?targetPlatform=${CLAUDE_CODE_VSIX_PLATFORM}"
    if curl --compressed -fSL -m 300 --retry 3 --retry-delay 2 -o "${ext_vsix}" "${gallery_url}"; then
        # Integrity gate (default-deny): the gallery exposes no per-extension
        # signed manifest we can verify, so we pin the known-good sha256 of the
        # platform-specific asset. A mismatch refuses the install.
        dl_sha="$(sha256sum "${ext_vsix}" | awk '{print $1}')"
        if [ "${dl_sha}" != "${CLAUDE_CODE_VSIX_SHA256}" ]; then
            echo ""
            echo "  ERROR: the downloaded extension .vsix failed sha256 verification."
            echo "    expected ${CLAUDE_CODE_VSIX_SHA256}"
            echo "    got      ${dl_sha}"
            echo "  Refusing to install a file that does not match what this package"
            echo "  expects. Either the publisher replaced this version, or the"
            echo "  download was tampered with. The extension was NOT installed."
            rm -f "${ext_vsix}" 2>/dev/null || true
        else
            echo "  Extension .vsix verified (sha256 OK)."
            # `code --install-extension` run as root is REFUSED by VS Code's
            # super-user guard (observed on the first bare-metal run 2026-07-06:
            # a clean nonzero exit; older driver versions SIGTRAP-crashed
            # instead). So we drop to the invoking user (with their HOME so the
            # extension lands in their profile) and install from the local file.
            # The drop needs pkm to pass SUDO_USER through the helper env
            # (H-024 HELPER_ENV_ALLOWLIST in pkm/installer.py); if SUDO_USER is
            # stripped this branch runs as root and the guard refuses the install.
            if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
                ext_as="sudo -u ${SUDO_USER} -H"
            else
                ext_as=""
            fi
            # Capture the install command's real output + exit status so a
            # failure reports what ACTUALLY happened instead of guessing. The
            # helper runs under `set -e`, so the capture MUST be guarded with
            # `|| ext_rc=$?`: a bare `ext_out="$(...)"` assignment inherits the
            # command's nonzero status and errexit would kill the helper right
            # here — making this entire diagnosis + .vsix-fallback block dead
            # code on exactly the failure path it exists to explain.
            ext_rc=0
            ext_out="$(timeout 120 ${ext_as} "${code_bin}" --install-extension "${ext_vsix}" 2>&1)" || ext_rc=$?
            if [ "${ext_rc}" -eq 0 ]; then
                printf '%s\n' "${ext_out}"
                echo "  VS Code extension installed (pinned, verified .vsix ${CLAUDE_CODE_VSIX_VERSION})."
                ext_installed=1
                rm -f "${ext_vsix}" 2>/dev/null || true
            else
                echo ""
                if [ "$(id -u)" = "0" ] && [ -z "${ext_as}" ]; then
                    # (a) ran as root with no invoking user visible — the H-024
                    # allowlist stripping SUDO_USER is the usual cause.
                    echo "  WARNING: the extension install ran AS ROOT (no invoking user was"
                    echo "  visible) and VS Code's super-user guard refused it (exit ${ext_rc})."
                    echo "  VS Code will not install extensions while running as root. The"
                    echo "  command-line tool is installed and usable; to add the extension,"
                    echo "  run this as your own user:"
                    echo "    ${code_bin} --install-extension ${ext_vsix}"
                else
                    # (b) some other nonzero failure — show the real output.
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
        echo "  (network / timeout). The Claude Code CLI is installed and usable;"
        echo "  install the VS Code extension later, or run 'claude' in VS Code's"
        echo "  integrated terminal. Gallery asset:"
        echo "    ${gallery_url}"
    fi
else
    # Loud-not-silent: without this line the whole extension block skipped
    # invisibly when no VS Code was on the PATH (operator hit this on a live
    # run 2026-07-02 — the transcript showed no extension mention at all).
    # The skip is correct behavior; the silence was the bug.
    echo ""
    echo "  NOTE: VS Code is not installed — the Claude Code VS Code extension was"
    echo "  skipped (the CLI above is fully usable on its own). To add it later:"
    echo "    sudo pkm install vscode        # then:"
    echo "    sudo pkm install claude-code   # re-run; the extension step will run"
fi

# Record the VS Code extension install ONLY when it actually succeeded —
# recording it unconditionally claimed "installed" even when the install
# crashed/timed out (the footprint must never assert state that isn't true).
# The extension files live under the user's home, outside the manifest path
# allowlist, so pkm doesn't track this per-user state in v1.0.
if [ "${ext_installed}" = "1" ]; then
    igos_helper_record_post_install_action \
        "VS Code extension anthropic.claude-code installed (per-user; not pkm-tracked)"
fi

igos_helper_commit

echo ""
echo "  Claude Code installed!"
echo ""
echo "  To authenticate:"
echo "    claude          # Opens browser for OAuth login"
echo "    # OR"
echo "    export ANTHROPIC_API_KEY=your-key-here"
echo "    claude"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-claude-code"
}
