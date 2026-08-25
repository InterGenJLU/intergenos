# Desktop Experience on InterGenOS

InterGenOS ships GNOME 49 on Wayland by default — a modern, fast, and privacy-respecting desktop. Hardware acceleration is handled through the Mesa stack for AMD and Intel GPUs. InterGenOS-authored and packaged services carry service-appropriate AppArmor and systemd restrictions; the exact sandbox differs where a service such as SSH must create user sessions. There is no InterGenOS telemetry or app-store analytics, and nothing downloads or installs package updates in the background. The enabled package-update timer, `pkm-check-updates.timer`, reads the package index already cached on your machine, makes no network connection, and installs nothing (see [Package Management](package-management.md)); unrelated maintenance timers such as log rotation also ship. Switchable desktop environments (KDE, Xfce, Sway) are planned for future releases.

## 1. The Desktop Environment

InterGenOS runs **GNOME 49** on the **Wayland** display protocol. The default visual experience is tuned with the first-party **InterGenOS** icon theme (default since 1.4; inherits Adwaita/hicolor for full application coverage), the **Bibata-Modern-Classic** cursor, and a system-wide prefer-dark color scheme. The **Papirus-Dark** and **Cybernetic Blue** icon themes ship as featured alternates — selectable in the first-boot Welcomer or later with GNOME Tweaks. System typography is **Inter** (clean geometric sans, used for UI + documents + titlebars) paired with **JetBrains Mono** (programming-ligature monospace, used for terminal + text editor + code surfaces). Inter ships as its variable release, so the family name the desktop defaults ask for is `Inter Variable`; `fc-match 'Inter Variable'` on an installed system resolves it to `InterVariable.ttf`. These choices reflect the InterGenOS visual language — clean, modern, and distinctly ours.

The Adwaita widget theme ships as the GTK4 baseline and is customized through a GSettings override file that applies at user-session start. This means the theme is consistent whether you are using core GNOME apps or third-party GTK4 applications installed through pkm.

### Key Desktop Features

- **Activities Overview**: Press the Super key (Windows key) to open the overview. Your open windows, workspace thumbnails, and the application dash are all visible at once.
- **Multi-monitor support**: During installation, Forge attempts to read the live display state and seeds the new user's first-login layout with every connected output that reports a current mode. If that state cannot be read, Forge records a skipped seed and leaves GNOME to configure the displays normally. GNOME supports later hot-plug, mixed-DPI, and mixed-refresh-rate layouts through Settings → Displays.
- **Touch and touchpad gestures**: Three-finger swipe to switch workspaces. Pinch-to-zoom in compatible applications. Touch scrolling works out of the box on touchscreen hardware.
- **Accessibility**: On-screen keyboard, high-contrast theme, and large-text mode are built in and enabled from the Accessibility panel in GNOME Settings. The AT-SPI accessibility bus (the framework screen readers build on) ships as well.

### Keyboard Shortcuts

Every row below is configured by the current source tree. The two
InterGenOS-specific additions, `Ctrl + Alt + T` and `Super + D`, enter the
installed defaults with R001.2. The schema and key are given so you can inspect
the active value with `gsettings get`.

| Shortcut | Action | Where the binding lives |
|---|---|---|
| `Super` | Open Activities overview | `org.gnome.mutter overlay-key` |
| `Super + A` | Show all applications | `org.gnome.shell.keybindings toggle-application-view` |
| `Super + Tab` / `Alt + Tab` | Switch between open **applications** | `org.gnome.desktop.wm.keybindings switch-applications` |
| `Super + backtick` / `Alt + backtick` | Switch between windows of the same application | `org.gnome.desktop.wm.keybindings switch-group` |
| `Ctrl + Alt + T` | Open a terminal | `org.gnome.settings-daemon.plugins.media-keys custom-keybindings` (custom0) |
| `Super + L` | Lock screen | `org.gnome.settings-daemon.plugins.media-keys screensaver` |
| `Super + Left` / `Super + Right` | Tile window to the left or right half | `org.gnome.mutter.keybindings toggle-tiled-left` / `toggle-tiled-right` |
| `Super + Up` | Maximize window | `org.gnome.desktop.wm.keybindings maximize` |
| `Super + Down` | Unmaximize window | `org.gnome.desktop.wm.keybindings unmaximize` |
| `Super + H` | Minimize window | `org.gnome.desktop.wm.keybindings minimize` |
| `Super + D` | Show the desktop | `org.gnome.desktop.wm.keybindings show-desktop` |
| `Super + Shift + Left/Right` | Move window to adjacent monitor | `org.gnome.desktop.wm.keybindings move-to-monitor-left` / `move-to-monitor-right` |
| `Super + Page Up` / `Page Down` | Switch workspace | `org.gnome.desktop.wm.keybindings switch-to-workspace-left` / `switch-to-workspace-right` |
| `Ctrl + Alt + Del` | Log out | `org.gnome.settings-daemon.plugins.media-keys logout` |
| `Alt + F2` | Open the run-a-command prompt | `org.gnome.desktop.wm.keybindings panel-run-dialog` |

**R001.1 note.** R001.1 did not bind `Ctrl + Alt + T` or `Super + D` even
though its documentation listed them. R001.2 adds both: the terminal key
through the system dconf defaults (a custom keybinding's schema is relocatable
and a gschema override cannot carry one), and show-desktop through the desktop
gschema override.

- There are no corner or quadrant tiling bindings; `Super + Arrow` tiles to
  halves and maximizes or unmaximizes, as listed above.
- `Ctrl + Alt + Del` logs out. It does not open a power-off or restart dialog;
  use the system menu at the top right for those.
- `Alt + F2`, then `r` restarts GNOME Shell on X11 only. R001.1 runs Wayland,
  where the shell cannot restart in place and the command is unavailable.

## 2. What's Installed by Default

The desktop installation provides a fully functional workstation out of the box — the 400+-package GNOME desktop tier plus ISO-bundled applications (such as Firefox, which ships on the ISO from the extra tier). Here are the headline applications:

| Application | Purpose |
|---|---|
| **Firefox 140 ESR** | Web browser (Extended Support Release, security-patched by Mozilla through the ESR window) |
| **Files (Nautilus)** | File manager with Samba, SFTP, and WebDAV remote mount support |
| **GNOME Text Editor** | Modern GTK4 text editor with syntax highlighting |
| **GNOME Terminal** | Terminal emulator with Wayland-native rendering |
| **Image Viewer (Loupe)** | Wayland-native image viewer with touch and gesture support |
| **GNOME Software** | **Substituted by pkm.** The app-browser UI slot is served by pkm's CLI, not a GUI app store. Refresh the index with `sudo pkm sync`, then install all available upgrades with `sudo pkm upgrade --all` (or name individual packages). |
| **Settings** | Full GNOME Settings panel: Wi-Fi, Bluetooth, Displays, Power, Privacy, Accessibility, Sharing, and more |
| **GNOME Calendar** | Local and online calendar with Nextcloud and Google integration |
| **GNOME Contacts** | Address book with CardDAV sync |
| **Evince (Document Viewer)** | PDF, PostScript, DjVu, and comic-book viewer |
| **Celluloid (Videos)** | GTK4 video player (mpv frontend) with hardware-accelerated decoding |

System utilities are also included: disk usage analyzer, system monitor, screenshot tool, font viewer, and a GNOME-optimized archive manager.

## 3. Beyond the Defaults

The binary repository at [repo.intergenos.org](https://repo.intergenos.org) carries a curated selection of user-facing applications. Some of these are part of the default install; others are optional packages you add when you need them. The notes below mark which is which.

Whether an application ships in the image is not an editorial choice made in
this document: it is the `iso_include` field of the package's own recipe, which
the build reads when it decides what goes into the squashfs. Each entry below
names its package, and the preflight gate
`tests/preflight/test_documented_application_labels_match_the_image.py`
fails the build when a label here disagrees with that field.

### Audio and Video

These ship on the ISO and are installed by default:

- **Audacity** (`audacity`) — Multi-track audio editor
- **Rhythmbox** (`rhythmbox`) — Music player with podcast support
- **Transmission** (`transmission`) — BitTorrent client
- **Celluloid** (`celluloid`) — GTK4 frontend for mpv, also listed among the default applications above

### Development Tools

A full developer toolchain ships by default in the core tier:

- **Git** — Version control
- **Vim** — Terminal-based text editor
- **Node.js 22** — JavaScript runtime
- **Go** — Systems programming language
- **Rust** — Systems programming language with cargo

### Utilities

A set of modern command-line utilities ships by default in the base tier:

- **htop** — Interactive process viewer
- **rsync** — File synchronization
- **bat** — `cat` clone with syntax highlighting
- **ripgrep (rg)** — Recursive grep replacement
- **fd** — `find` replacement

These ship on the ISO and are installed by default:

- **zoxide** (`zoxide`) — Smart `cd` command

The following are optional, installed on demand:

- **hyperfine** (`hyperfine`) — Command-line benchmarking tool

### Download-Helper Packages

Some proprietary or distribution-restricted applications are available through download-helper packages. These do **not** bundle the vendor binary in the mirror archive — the helper fetches it from the vendor after you accept the license:

- `brave` — Brave Browser
- `chrome` — Google Chrome
- `claude-code` — Claude Code CLI
- `vscode` — Microsoft VS Code (proprietary build)

The NVIDIA proprietary driver follows the same opt-in pattern under the package name `nvidia`. `sudo pkm install nvidia` downloads and verifies the signed helper archive, then presents the NVIDIA license before any vendor payload is fetched or installed.

## 4. The Wayland Posture

Wayland is the default display protocol. Applications with native Wayland support use it directly. X11-only applications run through **Xwayland**, which starts automatically when needed.

**Why Wayland:**

- **Per-window isolation**: Each Wayland-native application sees only its own input and pixel buffer, so one such application cannot key-log another or scrape another window's pixels. The exception is X11 applications: they run under the single Xwayland server described above, and the X protocol lets any client of that server read the input events and window contents of the others. Isolation holds between Wayland-native clients, and between Xwayland as a whole and the rest of the session — not among the X11 applications sharing it. Steam and most games are X11 clients.
- **Modern input handling**: The image enables Mutter fractional scaling and native Xwayland scaling. HiDPI, variable-refresh, multi-monitor, touch, and gesture behavior still depends on the hardware and application.
- **No screen tearing**: Wayland composites every frame through the display server, eliminating tearing artifacts present in legacy X11 setups.
- **Future-proof**: The GNOME ecosystem, Firefox, and the broader Linux desktop world are standardizing on Wayland. X11 maintenance is winding down.

For the broader security story, see [Security Defaults](security-defaults.md).

## 5. Hardware Acceleration

InterGenOS ships the **Mesa** graphics stack for AMD (Radeon) and Intel (Arc, Iris, UHD) GPUs. This covers:

- OpenGL and OpenGL ES through `radeonsi` (AMD) and `iris` / `crocus` (Intel)
- Vulkan through `radv` (AMD) and `anv` (Intel)
- VA-API hardware video decoding through `radeonsi` (AMD)
- Compute (OpenCL) through packaged `rusticl`; enable it for a supported driver with `RUSTICL_ENABLE=radeonsi` or `RUSTICL_ENABLE=iris`

The OpenGL, Vulkan, and video drivers are installed and available by default.
Rusticl is built and its ICD is installed, but it has zero runtime cost and no
device enabled until `RUSTICL_ENABLE` names one. Firefox uses VA-API for
hardware-accelerated video playback when the hardware and browser configuration
support it. GNOME Shell renderers select OpenGL or Vulkan according to their
runtime configuration.

### NVIDIA GPUs

NVIDIA's proprietary driver is available as the `nvidia` package — an explicit, user-initiated opt-in. The base image does ship the redistributable vendor firmware blobs in `linux-firmware` and related firmware packages that common Wi-Fi, graphics, audio, and other devices require; it does not include NVIDIA's proprietary driver. The driver is offered only on hardware with an NVIDIA GPU present. If you need CUDA or hardware-accelerated rendering on NVIDIA hardware, run `sudo pkm install nvidia`, accept the NVIDIA license when prompted, and follow the post-install instructions for enrolling the NVIDIA kernel module with your Machine Owner Key.

## 6. Switchable Desktop Environments (Post-v1.0)

The v1.0 release ships GNOME only. Support for switching desktop environments is planned for a future release:

- **KDE Plasma** — Qt-based desktop with extensive customization
- **Xfce** — Lightweight GTK-based desktop for resource-constrained hardware or users who prefer a classic desktop metaphor
- **Sway** — Wayland-native tiling compositor built on wlroots, for users who prefer keyboard-driven window management

When this feature lands, you will be able to install an additional desktop environment through pkm and select it from the login screen. No reinstallation required.

## 7. What We Don't Ship

InterGenOS makes deliberate omissions in the interest of security and simplicity:

- **No Snap**: The Snap daemon (`snapd`) is not installed and not in the repository. Snap's auto-update model conflicts with the user-control posture, and its confinement model is redundant with AppArmor + systemd isolation already applied at the package level.
- **No Flatpak by default**: Flatpak is not packaged in InterGenOS. The binary mirror's signed-index trust chain already provides equivalent integrity guarantees for the packages we build and sign ourselves, so the default system ships without a sandboxed third-party-app store.
- **No InterGenOS telemetry**: InterGenOS components do not send project usage or analytics. Network-capable applications still connect when you ask them to — for example, browsing, synchronization, package downloads, or web search. Firefox telemetry is locked OFF via the Mozilla `policies.json` and Preferences locks shipped at `/usr/lib/firefox/distribution/policies.json`; an administrator with root can still edit that file.
- **No app-store analytics**: There is no usage tracking of any kind. `pkm` counts the packages you have installed for dependency resolution, but that data never leaves your machine.
- **No auto-update**: No background service downloads or applies updates without your explicit action. Run `sudo pkm sync && sudo pkm upgrade --all` when you choose to update every package with a newer version in the cached index.
- **No Plymouth boot splash**: InterGenOS shows your boot. We don't paint a logo over the 5–10 seconds between GRUB and GDM. You see the kernel hand off to systemd, you see every service start with [OK] or [FAILED] markers, you see your network come up, you see AppArmor load. If anything fails — a broken mount, a weird module load, a hardware quirk — you see it immediately. This is a security signal, not a polish gap. Spotting odd output during boot is an actual practice for catching compromise or hardware change; we'd rather give you that surface than hide it behind a corporate logo.

## 8. Cross-References

- [Getting Started](../getting-started.md) — First-boot walkthrough and initial system setup
- [Security Defaults](security-defaults.md) — Full breakdown of AppArmor, Secure Boot, systemd hardening, and kernel lockdown posture
- [Package Management](package-management.md) — pkm command reference, mirror trust chain, and archive verification
- [Databases](databases.md) — Which databases ship in InterGenOS and how to choose one for your project
- [Repository Trust Model](../repository-trust.md) — How the signed-index trust chain protects package downloads
