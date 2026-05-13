# Desktop Experience on InterGenOS

InterGenOS ships GNOME 49 on Wayland by default — a modern, fast, and privacy-respecting desktop. Hardware acceleration is handled through the Mesa stack for AMD and Intel GPUs. AppArmor and systemd isolation directives confine every system service from first boot. There is no telemetry, no app-store analytics, and no auto-updater running in the background. Switchable desktop environments (KDE, Xfce, Sway) are planned for future releases.

## 1. The Desktop Environment

InterGenOS runs **GNOME 49** on the **Wayland** display protocol. The default visual experience is tuned with the Cybernetic - Blue icon theme, the Bibata-Modern-Classic cursor, and a system-wide prefer-dark color scheme. These choices reflect the InterGenOS visual language — clean, modern, and distinctly ours.

The Adwaita widget theme ships as the GTK4 baseline and is customized through a GSettings override file that applies at user-session start. This means the theme is consistent whether you are using core GNOME apps or third-party GTK4 applications installed through pkm.

### Key Desktop Features

- **Activities Overview**: Press the Super key (Windows key) to open the overview. Your open windows, workspace thumbnails, and the application dash are all visible at once.
- **Multi-monitor support**: GNOME 49 handles mixed-DPI and mixed-refresh-rate setups without configuration. Hot-plug a monitor and it immediately works.
- **Touch and touchpad gestures**: Three-finger swipe to switch workspaces. Pinch-to-zoom in compatible applications. Touch scrolling works out of the box on touchscreen hardware.
- **Accessibility**: Screen reader (Orca), on-screen keyboard, high-contrast theme, and large-text mode are built in. These are enabled from the Accessibility panel in GNOME Settings.

### Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Super` | Open Activities overview |
| `Super + Tab` | Switch between open applications |
| `Super + \`` | Switch between windows of the same application |
| `Ctrl + Alt + T` | Open GNOME Terminal |
| `Super + L` | Lock screen |
| `Super + Arrow keys` | Snap window to half-screen or quadrant |
| `Super + Shift + Arrow` | Move window to adjacent monitor |
| `Ctrl + Alt + Del` | Power off / restart dialog |
| `Alt + F2`, then `r`, then `Enter` | Restart GNOME Shell (without logging out) |

## 2. What's Installed by Default

The desktop-tier installation (~383 packages) provides a fully functional workstation out of the box. Here are the headline applications:

| Application | Purpose |
|---|---|
| **Firefox 140 ESR** | Web browser (Extended Support Release, security-patched by Mozilla through the ESR window) |
| **Files (Nautilus)** | File manager with Samba, SFTP, and WebDAV remote mount support |
| **GNOME Text Editor** | Modern GTK4 text editor with syntax highlighting |
| **GNOME Console** | Terminal emulator with Wayland-native rendering |
| **Image Viewer (Loupe)** | Wayland-native image viewer with touch and gesture support |
| **GNOME Software** | **Substituted by pkm.** The app-browser UI slot is served by pkm's CLI, not a GUI app store. Software updates happen through `sudo pkm sync` and `sudo pkm upgrade`. |
| **Settings** | Full GNOME Settings panel: Wi-Fi, Bluetooth, Displays, Power, Privacy, Accessibility, Sharing, and more |
| **GNOME Calendar** | Local and online calendar with Nextcloud and Google integration |
| **GNOME Contacts** | Address book with CardDAV sync |
| **Evince (Document Viewer)** | PDF, PostScript, DjVu, and comic-book viewer |
| **Totem (Videos)** | Video player with hardware decoding through VA-API |

System utilities are also included: disk usage analyzer, system monitor, screenshot tool, font viewer, and a GNOME-optimized archive manager.

## 3. What You'd Install from the Binary Mirror

Beyond the defaults, the binary repository at [repo.intergenos.org](https://repo.intergenos.org) carries a curated selection of user-facing applications:

### Audio and Video
- **Audacity** — Multi-track audio editor
- **Rhythmbox** — Music player with podcast support
- **Transmission** — BitTorrent client
- **Celluloid** — GTK4 frontend for mpv

### Development Tools
- **Code-OSS** — Open-source build of Visual Studio Code (no proprietary Microsoft branding or telemetry)
- **Vim** and **Neovim** — Terminal-based text editors
- **Git** — Version control (installed by default in the core tier)
- **Node.js 22** — JavaScript runtime
- **Go** — Systems programming language
- **Rust** — Systems programming language with cargo

### Utilities
- **htop** — Interactive process viewer
- **rsync** — File synchronization
- **bat** — cat clone with syntax highlighting
- **ripgrep (rg)** — Recursive grep replacement
- **fd** — find replacement
- **zoxide** — Smart cd command
- **hyperfine** — Benchmarking tool

### Download-Helper Packages

Some proprietary or distribution-restricted applications are available through download-helper packages. These do **not** bundle the actual binary — they fetch it from the vendor on first install after you accept the license:

- `brave-helper` — Brave Browser
- `chrome-helper` — Google Chrome
- `claude-code-helper` — Claude Code CLI
- `nvidia-helper` — NVIDIA proprietary driver
- `vscode-helper` — Microsoft VS Code (proprietary build; use Code-OSS for the open-source build)

## 4. The Wayland Posture

Wayland is the default display protocol. Every GNOME application ships with native Wayland support. X11 compatibility is provided through **Xwayland** for applications that have not yet been ported — this is a seamless translation layer that runs automatically when needed.

**Why Wayland:**

- **Per-window isolation**: Each application sees only its own input and pixel buffer. One application cannot key-log another or scrape another window's pixels.
- **Modern input handling**: HiDPI, variable refresh rate, mixed-DPI multi-monitor, and touch/gesture input work correctly because the protocol was designed for them.
- **No screen tearing**: Wayland composites every frame through the display server, eliminating tearing artifacts present in legacy X11 setups.
- **Future-proof**: The GNOME ecosystem, Firefox, and the broader Linux desktop world are standardizing on Wayland. X11 maintenance is winding down.

For the broader security story, see [Security Defaults](security-defaults.md).

## 5. Hardware Acceleration

InterGenOS ships the **Mesa** graphics stack for AMD (Radeon) and Intel (Arc, Iris, UHD) GPUs. This covers:

- OpenGL and OpenGL ES through `radeonsi` (AMD) and `iris` / `crocus` (Intel)
- Vulkan through `radv` (AMD) and `anv` (Intel)
- VA-API hardware video decoding through `radeonsi` and `intel-media-driver`
- Compute (OpenCL) through `rusticl`

All Mesa drivers are installed and enabled by default. Firefox uses VA-API for hardware-accelerated video playback. GNOME Shell renderers use OpenGL or Vulkan automatically.

### NVIDIA GPUs

NVIDIA's proprietary driver is available through the `nvidia-helper` download pattern — an explicit, user-initiated opt-in. The core distribution does not ship proprietary firmware. If you need CUDA or hardware-accelerated rendering on NVIDIA hardware, install `nvidia-helper` and follow the post-install instructions for enrolling the NVIDIA kernel module with your Machine Owner Key.

## 6. Switchable Desktop Environments (Post-v1.0)

The v1.0 release ships GNOME only. Support for switching desktop environments is planned for a future release:

- **KDE Plasma** — Qt-based desktop with extensive customization
- **Xfce** — Lightweight GTK-based desktop for resource-constrained hardware or users who prefer a classic desktop metaphor
- **Sway** — Wayland-native tiling compositor built on wlroots, for users who prefer keyboard-driven window management

When this feature lands, you will be able to install an additional desktop environment through pkm and select it from the login screen. No reinstallation required.

## 7. What We Don't Ship

InterGenOS makes deliberate omissions in the interest of security and simplicity:

- **No Snap**: The Snap daemon (`snapd`) is not installed and not in the repository. Snap's auto-update model conflicts with the user-control posture, and its confinement model is redundant with AppArmor + systemd isolation already applied at the package level.
- **No Flatpak by default**: Flatpak is available as an optional install (`sudo pkm install flatpak`) if you want sandboxed third-party applications. It is not pre-installed because the binary mirror's signed-index trust chain already provides equivalent integrity guarantees for packages we build and sign ourselves.
- **No telemetry**: No component of the desktop phones home — not GNOME, not Firefox (telemetry is disabled at build time), not the shell, not the package manager.
- **No app-store analytics**: There is no usage tracking of any kind. `pkm` counts the packages you have installed for dependency resolution, but that data never leaves your machine.
- **No auto-update**: No background service downloads or applies updates without your explicit action. Run `sudo pkm sync && sudo pkm upgrade` when you choose to update.

## 8. Cross-References

- [Getting Started](../getting-started.md) — First-boot walkthrough and initial system setup
- [Security Defaults](security-defaults.md) — Full breakdown of AppArmor, Secure Boot, systemd hardening, and kernel lockdown posture
- [Package Management](package-management.md) — pkm command reference, mirror trust chain, and archive verification
- [Databases](databases.md) — Which databases ship in InterGenOS and how to choose one for your project
- [Repository Trust Model](../repository-trust.md) — How the signed-index trust chain protects package downloads
