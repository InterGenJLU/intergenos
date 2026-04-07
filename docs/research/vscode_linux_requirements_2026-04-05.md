# VS Code on Linux (LFS/BLFS) -- Comprehensive Research

**Date:** 2026-04-05
**Purpose:** Understand everything needed to run Visual Studio Code on an LFS/BLFS-based system

---

## 1. What VS Code Is Built On

VS Code is an **Electron application**. The stack is:

```
VS Code (TypeScript/JavaScript application)
   |
Electron 39.8.5 (current VS Code main branch)
   |
+-- Chromium 142.0.7444.52 (rendering engine, GPU acceleration, networking)
+-- Node.js 22.20.0 (runtime for extensions and backend)
+-- V8 14.2 (JavaScript engine)
```

**Key point:** VS Code bundles its own Node.js inside Electron. It does NOT use the system Node.js at runtime. The bundled Node.js version is determined by the Electron version (currently 22.20.0 via Electron 39). A system Node.js (22.x+) is only needed if building from source.

---

## 2. Runtime Library Dependencies (from VS Code source: `build/linux/debian/dep-lists.ts`)

### Core Runtime Libraries (x86_64)

| Library | Min Version | InterGenOS Desktop Tier? | Notes |
|---------|-------------|--------------------------|-------|
| libc6 | glibc | YES (core) | Core system |
| libstdc++6 | GCC 6+ | YES (core) | C++ standard library |
| libasound2 (ALSA) | 1.0.17 | YES (alsa-lib) | Audio output |
| libatk1.0 | 2.11.90 | YES (at-spi2-core) | Accessibility toolkit |
| libatk-bridge2.0 | 2.5.3 | YES (at-spi2-core) | AT-SPI bridge |
| libatspi2.0 | 2.9.90 | YES (at-spi2-core) | Accessibility services |
| libcairo2 | 1.6.0 | YES (cairo) | 2D graphics |
| libdbus-1-3 | 1.9.14 | YES (core: dbus) | D-Bus IPC |
| libexpat1 | 2.1 | YES (core: expat) | XML parser |
| libgbm1 | 17.1.0 | YES (mesa) | GPU buffer management |
| libglib2.0 | 2.39.4 | YES (core: glib) | GLib core |
| libgio2.0 | (via glib) | YES (core: glib) | GIO |
| libgobject2.0 | (via glib) | YES (core: glib) | GObject |
| libgtk-3 or libgtk-4 | 3.9.10 / 4.x | YES (gtk3, gtk4) | Widget toolkit |
| libnspr4 | 4.9 | YES (via nss in core) | Netscape runtime |
| libnss3 | 3.26 | YES (core: nss) | Network security services |
| libnssutil3 | (via nss) | YES (core: nss) | NSS utilities |
| libsmime3 | (via nss) | YES (core: nss) | S/MIME |
| libpango1.0 | 1.14.0 | YES (pango) | Text rendering |
| libudev1 | 183 | YES (core: systemd) | Device management |
| libX11-6 | (any) | YES (libX11) | X11 client library |
| libxcb1 | 1.9.2 | YES (libxcb) | X11 C bindings |
| libXcomposite1 | 0.4.4 | YES (libXcomposite) | X11 composite |
| libXdamage1 | 1.1 | YES (libXdamage) | X11 damage tracking |
| libXext6 | (any) | YES (libXext) | X11 extensions |
| libXfixes3 | (any) | YES (libXfixes) | X11 fixes |
| libXrandr2 | (any) | YES (libXrandr) | X11 RandR |
| libxkbcommon0 | 0.5.0 | YES (libxkbcommon) | Keyboard handling |
| libxkbfile1 | 1.1.0 | YES (libxkbfile) | XKB file handling |

### Additional Dependencies (not architecture-specific)

| Dependency | InterGenOS Desktop Tier? | Notes |
|------------|--------------------------|-------|
| ca-certificates | YES (core: ca-certificates) | TLS trust store |
| libcurl (gnutls/nss/openssl) | YES (core: curl) | HTTP client |
| xdg-utils | YES (xdg-utils) | Desktop integration (xdg-open, etc.) |

### Recommended (Optional but Beneficial)

| Dependency | InterGenOS Desktop Tier? | Notes |
|------------|--------------------------|-------|
| libvulkan1 | YES (vulkan-loader) | GPU acceleration |
| bubblewrap | YES (bubblewrap) | Sandbox for extensions |
| socat | NO -- not in any tier | Socket relay (used by some extensions) |

### Additional Runtime Needs (not in dep-lists but required)

| Dependency | InterGenOS Desktop Tier? | Notes |
|------------|--------------------------|-------|
| libsecret | YES (libsecret) | Credential storage (keyring) |
| gnome-keyring | YES (gnome-keyring) | Backend for libsecret |
| gvfs + glib2 | YES (gvfs, glib) | Trash functionality |
| libdrm | YES (libdrm) | DRM/GPU access |
| mesa | YES (mesa) | OpenGL/Vulkan for GPU rendering |

---

## 3. Display Server Requirements

### X11 vs Wayland

VS Code / Electron uses Chromium's **Ozone platform abstraction layer** to support both X11 and Wayland.

**Detection logic** (from `src/vs/base/node/osDisplayProtocolInfo.ts`):
1. Check `XDG_SESSION_TYPE` -- if "wayland" or "x11", use that
2. If unset, check `WAYLAND_DISPLAY` -- if empty, assume X11
3. If `WAYLAND_DISPLAY` is set, verify `$XDG_RUNTIME_DIR/wayland-0` exists

**How to run on each:**

- **X11:** Works out of the box. Default behavior.
- **Wayland native:** Set `--ozone-platform=wayland` flag, or set `ELECTRON_OZONE_PLATFORM_HINT=auto` environment variable (added in Electron 28)
- **XWayland:** This is the fallback -- if running under Wayland without the ozone flag, VS Code runs through XWayland (X11 compatibility layer)

**Snap builds force X11:** The VS Code Snap package explicitly uses `--ozone-platform=x11` (found in `resources/linux/snap/electron-launch`).

**For InterGenOS (GNOME on Wayland):** VS Code will work via XWayland by default. Native Wayland can be enabled with `--ozone-platform-hint=auto` or the environment variable. Both X11 libs AND Wayland libs are needed.

### GPU Acceleration

VS Code uses Chromium's GPU pipeline:
- **libgbm** (Mesa) -- required for GPU buffer management
- **libdrm** -- DRM access
- **libvulkan** -- recommended for Vulkan acceleration
- **Mesa** -- provides OpenGL/EGL

Falls back to software rendering if GPU is unavailable (e.g., `--disable-gpu` flag).

---

## 4. Distribution Method and Licensing

### The Two VS Codes

There are **two distinct products**:

| | Code-OSS | Visual Studio Code |
|---|----------|-------------------|
| **Source** | github.com/microsoft/vscode | Same source + proprietary additions |
| **License** | MIT (open source) | Proprietary Microsoft license |
| **Marketplace** | Cannot use VS Marketplace | Full VS Marketplace access |
| **Extensions** | Open VSX Registry (open-vsx.org) | marketplace.visualstudio.com |
| **Telemetry** | None by default | Microsoft telemetry enabled |
| **Branding** | Generic "Code-OSS" | Microsoft VS Code logo/name |
| **Remote Dev** | Limited (server components proprietary) | Full remote development |
| **C#/.NET debugger** | Not available | Proprietary license required |

### Microsoft's VS Code License Restrictions

The proprietary VS Code license explicitly **prohibits**:
- Sharing, publishing, renting, or leasing the software
- Providing it as a **standalone offering**
- Reverse engineering

**This means InterGenOS CANNOT redistribute the Microsoft VS Code binary.** Bundling it in the distribution would violate the license.

### Distribution Options for InterGenOS

1. **Code-OSS from source** (MIT license) -- Build from the open source repo. This is what Arch Linux, Fedora, and others do. Loses access to Microsoft's marketplace (must use Open VSX instead). No telemetry. No proprietary extensions.

2. **Standalone tarball download** (user-initiated) -- Microsoft provides a `.tar.gz` at `https://update.code.visualstudio.com/latest/linux-x64/stable`. Users can download and extract this themselves. This is NOT redistribution -- the user is obtaining it directly from Microsoft. InterGenOS could provide a helper script that downloads it.

3. **Flatpak/Snap** -- Package manager handles it. Not relevant for initial InterGenOS.

### Open VSX Registry

Open VSX (Eclipse Public License 2.0) is a vendor-neutral, open-source alternative to the VS Marketplace. Code-OSS builds can be configured to use `open-vsx.org` for extension discovery and installation. Most popular extensions are available there. This is the approach used by VSCodium, Gitpod, and Eclipse Theia.

---

## 5. Authentication and Credential Storage

### How Authentication Works

- **Microsoft/GitHub login:** Opens a web browser for OAuth flow. Yes, a browser is needed for initial login.
- **Credential storage:** Uses Electron's `safeStorage` API (since VS Code 1.80), which delegates to Chromium's `oscrypt` module
- **Keyring backends supported:**
  - `gnome-libsecret` -- recommended for GNOME (uses libsecret + gnome-keyring)
  - `kwallet5` -- for KDE
  - `basic` -- in-memory fallback (not recommended, credentials lost on restart)
- **Configuration:** Users can force a backend via `--password-store=gnome-libsecret` flag

### Required for Authentication

| Component | InterGenOS Desktop Tier? | Purpose |
|-----------|--------------------------|---------|
| libsecret | YES | Client library for Secret Service D-Bus API |
| gnome-keyring | YES | Backend daemon implementing Secret Service |
| D-Bus | YES (core) | IPC for Secret Service communication |
| A web browser | YES (via GNOME Web/Epiphany or user-installed) | OAuth login flow |

### Network Connectivity

VS Code needs access to these endpoints for full functionality:

**Core:**
- `update.code.visualstudio.com` -- updates
- `vscode.download.prss.microsoft.com` -- download CDN

**Extensions:**
- `marketplace.visualstudio.com` -- extension marketplace (proprietary VS Code only)
- `*.gallery.vsassets.io` -- extension assets
- `*.gallerycdn.vsassets.io` -- extension CDN
- `open-vsx.org` -- extensions (Code-OSS only)

**Settings Sync:**
- `vscode-sync.trafficmanager.net` -- settings synchronization

**Telemetry (can be disabled):**
- `default.exp-tas.com` -- experiments
- `rink.hockeyapp.net` -- crash reporting

**GitHub:**
- `raw.githubusercontent.com` -- raw file access

---

## 6. System Integration Requirements

### D-Bus -- YES, Required

VS Code uses D-Bus for:
- Secret Service API (credential storage via gnome-keyring)
- Desktop notifications
- File manager integration (trash via gvfs)
- Power management awareness
- libdbus-1-3 is a hard dependency (>= 1.9.14)

### systemd User Services -- Not Required

VS Code does not require systemd user services to function. However, gnome-keyring (which VS Code uses for credentials) is typically started as a systemd user service or PAM module in GNOME sessions.

### Polkit -- Not Directly Required

VS Code itself does not need polkit. However, some operations (like saving files owned by root) may trigger polkit dialogs if pkexec is available. Not a dependency.

### Fonts

VS Code needs fonts for:
- UI rendering (system font, typically a sans-serif)
- Editor (monospace font)
- InterGenOS desktop tier already includes: font-dejavu, font-noto, fontconfig
- VS Code includes fallback fonts but respects system font configuration

### Icons and Themes

- Uses GTK3/GTK4 theme for native window decorations
- Uses its own internal icon theme for the editor
- Adwaita (already in desktop tier) works fine
- hicolor-icon-theme is already in the desktop tier

### Terminal Emulator -- Built-in

VS Code has a built-in terminal emulator (xterm.js). It does NOT need an external terminal emulator. It uses the system shell (bash, zsh, etc.) and pseudo-terminal devices (/dev/pts).

---

## 7. Node.js Details

### Bundled vs System Node.js

**Runtime:** VS Code bundles its own Node.js inside Electron. The bundled version is Node.js 22.20.0 (via Electron 39.8.5). It does NOT use the system Node.js.

**Building from source:** Requires system Node.js 22.x+ and npm/yarn.

### BLFS Node.js

BLFS 13.0 includes Node.js 22.22.0, which would be sufficient for building Code-OSS from source. Its dependencies are:
- Required: which
- Recommended: brotli, c-ares, ICU, libuv, nghttp2
- All already available or easily added to InterGenOS

---

## 8. Building Code-OSS from Source -- What It Takes

### Build Dependencies (on top of InterGenOS desktop tier)

| Package | Purpose | In InterGenOS? |
|---------|---------|----------------|
| Node.js 22.x | Build runtime | NO (not in any tier yet, BLFS has it) |
| Python 3 (with setuptools) | Build scripts, node-gyp | YES (core) |
| Git | Source management | YES (core) |
| GCC/G++ | Native module compilation | YES (core) |
| make | Build automation | YES (core) |
| pkg-config | Library detection | YES (core) |
| libX11-dev headers | Native module builds | YES (libX11) |
| libxkbfile-dev headers | Keyboard handling | YES (libxkbfile) |
| libsecret-dev headers | Credential storage | YES (libsecret) |
| libkrb5-dev headers | Kerberos auth | YES (core: mitkrb) |

### Build Process (simplified)

```bash
git clone https://github.com/microsoft/vscode.git
cd vscode
npm install          # or yarn
npm run compile      # compile TypeScript
npm run gulp vscode-linux-x64  # package for Linux
```

This produces a standalone directory with the Electron binary and all VS Code assets. No system Electron needed -- the build downloads the correct Electron version.

### Build Size

- Source download: ~200 MB
- Build disk space: ~5-8 GB (includes Electron download, node_modules)
- Final package: ~300-400 MB

---

## 9. Practical Options for InterGenOS

### Option A: User Downloads Microsoft VS Code Tarball (Simplest)

Provide a helper script:
```bash
#!/bin/bash
# Downloads and installs VS Code from Microsoft
curl -L "https://update.code.visualstudio.com/latest/linux-x64/stable" -o /tmp/vscode.tar.gz
tar xzf /tmp/vscode.tar.gz -C /opt/
ln -sf /opt/VSCode-linux-x64/bin/code /usr/local/bin/code
```
- **Pros:** Full Microsoft VS Code with marketplace access, no build needed, always latest
- **Cons:** Proprietary license, user must accept terms, telemetry enabled by default
- **Legal:** Fine -- user is downloading it themselves, InterGenOS is not distributing it

### Option B: Build Code-OSS from Source (Most Aligned with Prime Directive)

Add a `code-oss` package template to the desktop tier that builds from the MIT-licensed source.
- **Pros:** Fully open source, no telemetry, user controls everything, can use Open VSX
- **Cons:** Complex build (needs Electron/Chromium download during build), large, no MS marketplace
- **Legal:** Fully compliant -- MIT license

### Option C: Both

Provide Code-OSS in the package tree AND a download helper for users who want the Microsoft version. Let the user choose. This is the most Prime Directive-aligned approach.

---

## 10. InterGenOS Desktop Tier Coverage Analysis

Out of VS Code's ~30 runtime library dependencies, the InterGenOS desktop tier (345 packages) already provides **every single one** except:

| Missing | Status |
|---------|--------|
| socat | Not in any tier (recommended, not required) |
| Node.js | Not in any tier yet (needed only for building Code-OSS, not for running VS Code tarball) |

**The InterGenOS GNOME desktop environment already satisfies all VS Code runtime dependencies.** No new libraries need to be added to run the Microsoft tarball or a pre-built Code-OSS binary.

---

## Summary

| Question | Answer |
|----------|--------|
| What is VS Code built on? | Electron 39 (Chromium 142 + Node.js 22.20 + V8 14.2) |
| Can it run on InterGenOS? | YES -- all runtime deps already in desktop tier |
| Does it need X11? | Needs X11 libs, runs on XWayland by default under Wayland |
| Can it run native Wayland? | Yes, with `--ozone-platform-hint=auto` flag |
| Does it bundle Node.js? | YES -- does not use system Node.js at runtime |
| Can we redistribute it? | NO -- proprietary license prohibits standalone distribution |
| Can we build Code-OSS? | YES -- MIT license, needs Node.js 22.x for build only |
| Does it need D-Bus? | YES -- hard dependency |
| Does it need gnome-keyring? | Recommended for credential storage, works without (degraded) |
| Does it need polkit? | No |
| Does it need systemd services? | No (gnome-keyring needs PAM or systemd user session) |
| Extension marketplace? | Microsoft's is proprietary; Open VSX is the open alternative |
