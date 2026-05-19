#!/bin/bash
# claude-code-helper 1.0 — Install Anthropic Claude Code
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
    # Install the helper script
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-claude-code" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Claude Code Installer
#
# Installs Claude Code CLI and VS Code extension from Anthropic.
# License: https://code.claude.com/docs/en/legal-and-compliance
#
# H-007 Phase B migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API.

set -e

source /usr/share/igos/helpers/helper-lib.sh

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
    echo "  ERROR: Run via 'sudo pkm install-helper claude-code' instead."
    echo "  Direct invocation bypasses pkm's manifest ingestion;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

# Check for npm
if ! command -v npm >/dev/null 2>&1; then
    echo "  ERROR: npm not found. Install Node.js first."
    exit 1
fi

igos_helper_init "claude-code"

echo "  Installing Claude Code CLI via npm..."
npm install -g @anthropic-ai/claude-code

# Capture installed version + npm global root for footprint recording.
CLAUDE_VERSION=$(npm list -g @anthropic-ai/claude-code 2>/dev/null \
                  | grep '@anthropic-ai/claude-code@' \
                  | sed 's/.*@anthropic-ai\/claude-code@//' \
                  | head -1)
igos_helper_set_version "${CLAUDE_VERSION:-unknown}"

NPM_GLOBAL_ROOT=$(npm root -g 2>/dev/null || echo "/usr/lib/node_modules")
CLAUDE_DIR="$NPM_GLOBAL_ROOT/@anthropic-ai/claude-code"

# H-007: record every file under the npm-installed module dir. npm's
# global prefix typically lands under /usr/lib/node_modules which
# matches the manifest's /usr/ allowlist.
if [ -d "$CLAUDE_DIR" ]; then
    while IFS= read -r f; do
        igos_helper_record_file "$f"
    done < <(find "$CLAUDE_DIR" -type f -o -type l 2>/dev/null)
fi

# Verify installation
if command -v claude >/dev/null 2>&1; then
    echo "  Claude Code CLI installed: $(claude --version 2>/dev/null || echo 'OK')"
    # npm creates a symlink at <prefix>/bin/claude pointing into the
    # module dir. Record it so pkm remove unlinks the binary surface.
    CLAUDE_BIN=$(command -v claude)
    CLAUDE_TARGET=$(readlink -f "$CLAUDE_BIN" 2>/dev/null || echo "$CLAUDE_BIN")
    if [ -L "$CLAUDE_BIN" ]; then
        igos_helper_record_symlink "$CLAUDE_BIN" "$CLAUDE_TARGET"
    elif [ -f "$CLAUDE_BIN" ]; then
        igos_helper_record_file "$CLAUDE_BIN"
    fi
else
    echo "  WARNING: claude command not found in PATH"
    echo "  You may need to add npm's global bin directory to your PATH"
fi

igos_helper_record_dep nodejs

# Install VS Code extension if VS Code or Code-OSS is available
if command -v code >/dev/null 2>&1; then
    echo "  Installing Claude Code extension for VS Code..."
    code --install-extension anthropic.claude-code 2>/dev/null && \
        echo "  VS Code extension installed" || \
        echo "  NOTE: Extension install failed — you can install manually from the marketplace"
elif command -v code-oss >/dev/null 2>&1; then
    echo "  Installing Claude Code extension for Code-OSS..."
    echo "  NOTE: Claude Code may not be available on Open VSX Registry."
    echo "  You may need to download the .vsix from the VS Code Marketplace"
    echo "  and install with: code-oss --install-extension claude-code.vsix"
fi

# Record the VS Code extension install as a descriptive post-install
# action (the extension files live under the user's home directory,
# which is outside the manifest's path allowlist; pkm doesn't track
# per-user state in v1.0).
igos_helper_record_post_install_action \
    "VS Code extension anthropic.claude-code installed (per-user; not pkm-tracked)"

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
