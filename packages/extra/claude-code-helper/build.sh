#!/bin/bash
# claude-code-helper 1.0 — Install Anthropic Claude Code
# InterGenOS extra tier
#
# Claude Code is proprietary software by Anthropic. This helper
# installs it via npm from Anthropic's official package. The user
# accepts Anthropic's license terms by running this installer.

configure() {
    :
}

build() {
    :
}

do_install() {
    # Install the helper script
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-claude-code" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Claude Code Installer
#
# Installs Claude Code CLI and VS Code extension from Anthropic.
# License: https://code.claude.com/docs/en/legal-and-compliance

set -e

echo ""
echo "  InterGenOS Claude Code Installer"
echo "  ================================="
echo ""
echo "  Claude Code is proprietary software by Anthropic."
echo "  License: https://code.claude.com/docs/en/legal-and-compliance"
echo ""

# Check for npm
if ! command -v npm >/dev/null 2>&1; then
    echo "  ERROR: npm not found. Install Node.js first."
    exit 1
fi

echo "  Installing Claude Code CLI via npm..."
npm install -g @anthropic-ai/claude-code

# Verify installation
if command -v claude >/dev/null 2>&1; then
    echo "  Claude Code CLI installed: $(claude --version 2>/dev/null || echo 'OK')"
else
    echo "  WARNING: claude command not found in PATH"
    echo "  You may need to add npm's global bin directory to your PATH"
fi

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
