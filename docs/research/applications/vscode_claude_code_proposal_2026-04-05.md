# Proposal: Additional Packages Tier — VS Code & Claude Code

## The Vision

InterGenOS with a built-in AI development environment. Boot the OS, open VS Code, Claude Code is ready. The Prime Directive in action — the user has a system they understand, can modify, and can trust, with an AI assistant that can help them do all three.

## Research Summary

### VS Code

**What it is:** An Electron application (Chromium + Node.js) that serves as a code editor.

**Licensing reality:** Two variants exist:
- **Code-OSS** — MIT licensed, the open source upstream. Can be built from source and distributed freely. Uses the Open VSX Registry for extensions (not Microsoft's marketplace).
- **Visual Studio Code** — Microsoft's branded build with proprietary license. **Cannot be redistributed** as part of InterGenOS. Users must download it themselves from Microsoft.

**Runtime dependencies (all satisfied by our GNOME desktop tier):**
- Graphics: libX11, libXkbfile, libgbm, Mesa, libdrm
- Desktop: GTK3/4, at-spi2-core, cairo, cups, D-Bus
- Security: NSS, libsecret, gnome-keyring
- Audio: ALSA (for notification sounds)
- System: glibc >= 2.28, kernel >= 4.18, GCC libstdc++ >= 3.4.25

**No new packages needed** for VS Code runtime — our desktop tier covers everything.

**Build from source:** Requires Node.js 22.x (BLFS 13.0 has 22.22.0). Build is npm/yarn based, ~5-8 GB disk, pulls Electron/Chromium during build. Complex but documented.

### Claude Code for VS Code

**What it is:** The same Claude Code engine that powers this CLI session, packaged as a VS Code extension. It bundles a 221 MB native binary (compiled with Bun) that does all the actual work — file I/O, shell commands, git operations, API calls.

**How it works:**
- Extension (Node.js) runs in VS Code's extension host → spawns native binary
- Native binary runs local WebSocket server → extension connects to it
- Binary reads/writes files directly, spawns bash for commands
- Connects to `api.anthropic.com` for model inference
- Auth: OAuth via claude.ai (needs a browser for initial login) or API key

**Dependencies beyond VS Code:** Essentially none. The binary bundles its own JavaScript runtime (Bun), its own ripgrep, and only needs glibc. Git is used heavily but not strictly required.

**Licensing:** Proprietary (Anthropic, all rights reserved). Cannot be bundled or redistributed. Must be installed by the user from the VS Code Marketplace or via npm.

## Proposed Architecture: "apps" Tier

### Tier Design

```
packages/
├── toolchain/   # Cross-compilation (LFS Ch 5-7)
├── core/        # Full system (LFS Ch 8)
├── base/        # End-user tools
├── desktop/     # GNOME on Wayland
└── apps/        # User-facing applications
    ├── nodejs/          # Node.js 22.x (BLFS) — needed to build Code-OSS
    ├── code-oss/        # Code-OSS (built from MIT source)
    └── claude-code-helper/  # Helper script for Claude Code installation
```

### Package 1: Node.js

Required by Code-OSS build. BLFS 13.0 includes Node.js 22.22.0.

- **Build:** `./configure --prefix=/usr --shared-zlib --with-intl=system-icu && make && make install`
- **Dependencies:** ICU, openssl, zlib, c-ares — all already in our tree
- **Note:** This also gives users `node` and `npm` for general development

### Package 2: Code-OSS

Built from MIT-licensed source at `github.com/microsoft/vscode`.

- **Build process:** `npm install && npm run compile && npm run package-linux-x64`
- **Extensions:** Uses Open VSX Registry instead of Microsoft Marketplace
- **Branding:** We can brand it "InterGenOS Code" or similar — MIT license allows this
- **Post-install:** Desktop file, icon, file associations, MIME types

### Package 3: Claude Code Helper

Since Claude Code is proprietary, we **cannot** bundle it. Instead, we provide a helper:

```bash
#!/bin/bash
# InterGenOS Claude Code Setup
# Downloads and installs Claude Code from Anthropic's official source

echo "InterGenOS Claude Code Setup"
echo "============================"
echo ""
echo "This will install Claude Code from Anthropic."
echo "License: https://code.claude.com/docs/en/legal-and-compliance"
echo ""

# Install Claude Code CLI
npm install -g @anthropic-ai/claude-code

# Install VS Code extension (if Code-OSS is installed)
if command -v code-oss >/dev/null 2>&1; then
    code-oss --install-extension anthropic.claude-code
fi

echo ""
echo "Claude Code installed. Run 'claude' or open Code-OSS."
```

This respects Anthropic's licensing while giving users a one-command setup.

### Alternative: VS Code Direct Download Helper

For users who want the full Microsoft VS Code (with marketplace access for Claude Code extension):

```bash
#!/bin/bash
# Downloads VS Code from Microsoft (proprietary, user's choice)
wget "https://code.visualstudio.com/sha/download?build=stable&os=linux-x64" -O /tmp/vscode.tar.gz
tar -xzf /tmp/vscode.tar.gz -C /opt/
ln -sf /opt/VSCode-linux-x64/bin/code /usr/bin/code
```

## Build Order

```
desktop tier completes →
  apps/nodejs (needs icu, openssl, zlib, c-ares) →
    apps/code-oss (needs nodejs) →
      apps/claude-code-helper (post-install script only)
```

## Prime Directive Analysis

**Why Code-OSS instead of VS Code?**
The Prime Directive says the user should understand and trust their system. A system built entirely from auditable source code — with an editor that's also built from auditable source code — is more trustworthy than bundling a proprietary binary. Users who want the Microsoft build can use the helper script.

**Why Claude Code as a helper, not a package?**
We cannot legally redistribute it. But we CAN make it effortless to install. One command, proper attribution, user makes the choice. This is the Prime Directive — putting the user in control, not making the decision for them.

**Why "apps" tier instead of extending desktop?**
These are user-facing applications, not system infrastructure. Keeping them in a separate tier means:
- Desktop can be built/tested independently
- Apps tier is optional — a minimal GNOME desktop works without it
- Clear separation of concerns
- Future apps (Firefox, Thunderbird, GIMP, etc.) have a home

## Open Questions for Owner

1. **Code-OSS vs VS Code download helper** — build from source (MIT, full control, longer build time) or provide a download script (quick, but proprietary binary)?
2. **Tier name** — "apps" vs "applications" vs "extras"?
3. **Should Node.js go in desktop or apps?** — Some desktop packages may eventually want it (Electron apps, etc.)
4. **Open VSX vs Microsoft Marketplace** — Code-OSS uses Open VSX by default. Claude Code extension may or may not be published there. If not, we'd need to handle .vsix sideloading.
