# Desktop Experience on InterGenOS

InterGenOS ships GNOME 49 on Wayland by default — a modern, fast, and privacy-respecting desktop. Hardware acceleration is handled through the Mesa stack for AMD and Intel GPUs. AppArmor and systemd isolation directives confine every system service from first boot. There is no telemetry, no app-store analytics, and nothing that downloads or installs updates in the background; the one background timer that ships enabled, `pkm-check-updates.timer`, reads the package index already cached on your machine, makes no network connection, and installs nothing (see [Package Management](package-management.md)). Switchable desktop environments (KDE, Xfce, Sway) are planned for future releases.

## 1. The Desktop Environment

InterGenOS runs **GNOME 49** on the **Wayland** display protocol. The default visual experience is tuned with the first-party **InterGenOS** icon theme (default since 1.4; inherits Adwaita/hicolor for full application coverage), the **Bibata-Modern-Classic** cursor, and a system-wide prefer-dark color scheme. The **Papirus-Dark** and **Cybernetic Blue** icon themes ship as featured alternates — selectable via Settings → Appearance or the first-boot welcomer. System typography is **Inter** (clean geometric sans, used for UI + documents + titlebars) paired with **JetBrains Mono** (programming-ligature monospace, used for terminal + text editor + code surfaces). Inter ships as its variable release, so the family name the desktop defaults ask for is `Inter Variable`; `fc-match 'Inter Variable'` on an installed system resolves it to `InterVariable.ttf`. These choices reflect the InterGenOS visual language — clean, modern, and distinctly ours.

The Adwaita widget theme ships as the GTK4 baseline and is customized through a GSettings override file that applies at user-session start. This means the theme is consistent whether you are using core GNOME apps or third-party GTK4 applications installed through pkm.

### Key Desktop Features

- **Activities Overview**: Press the Super key (Windows key) to open the overview. Your open windows, workspace thumbnails, and the application dash are all visible at once.
- **Multi-monitor support**: GNOME 49 handles mixed-DPI and mixed-refresh-rate setups without configuration. Hot-plug a monitor and it immediately works.
- **Touch and touchpad gestures**: Three-finger swipe to switch workspaces. Pinch-to-zoom in compatible applications. Touch scrolling works out of the box on touchscreen hardware.
- **Accessibility**: On-screen keyboard, high-contrast theme, and large-text mode are built in and enabled from the Accessibility panel in GNOME Settings. The AT-SPI accessibility bus (the framework screen readers build on) ships as well.

### Keyboard Shortcuts

Every row below is a binding that is active on an installed R001.1 system.
Each was read back with `gsettings get` rather than taken from the GNOME
defaults, and you can check any of them the same way — the schema and key are
given so the claim is verifiable rather than asserted.

| Shortcut | Action | Where the binding lives |
|---|---|---|
| `Super` | Open Activities overview | `org.gnome.mutter overlay-key` |
| `Super + A` | Show all applications | `org.gnome.shell.keybindings toggle-application-view` |
| `Super + Tab` / `Alt + Tab` | Switch between open **applications** | `org.gnome.desktop.wm.keybindings switch-applications` |
| `Super + backtick` / `Alt + backtick` | Switch between windows of the same application | `org.gnome.desktop.wm.keybindings switch-group` |
| `Super + L` | Lock screen | `org.gnome.settings-daemon.plugins.media-keys screensaver` |
| `Super + Left` / `Super + Right` | Tile window to the left or right half | `org.gnome.mutter.keybindings toggle-tiled-left` / `toggle-tiled-right` |
| `Super + Up` | Maximize window | `org.gnome.desktop.wm.keybindings maximize` |
| `Super + Down` | Unmaximize window | `org.gnome.desktop.wm.keybindings unmaximize` |
| `Super + H` | Minimize window | `org.gnome.desktop.wm.keybindings minimize` |
| `Super + Shift + Left/Right` | Move window to adjacent monitor | `org.gnome.desktop.wm.keybindings move-to-monitor-left` / `move-to-monitor-right` |
| `Super + Page Up` / `Page Down` | Switch workspace | `org.gnome.desktop.wm.keybindings switch-to-workspace-left` / `switch-to-workspace-right` |
| `Ctrl + Alt + Del` | Log out | `org.gnome.settings-daemon.plugins.media-keys logout` |
| `Alt + F2` | Open the run-a-command prompt | `org.gnome.desktop.wm.keybindings panel-run-dialog` |

**Not bound in R001.1.** These are worth knowing because other distributions
bind them and their absence is otherwise silent:

- `Ctrl + Alt + T` does not open a terminal — `custom-keybindings` is empty, and
  GNOME has no built-in terminal key to inherit. Launch the terminal from the
  overview or the dock.
- `Super + D` does not show the desktop — `show-desktop` has no binding.
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
| **GNOME Software** | **Substituted by pkm.** The app-browser UI slot is served by pkm's CLI, not a GUI app store. Software updates happen through `sudo pkm sync` and `sudo pkm upgrade`. |
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

Some proprietary or distribution-restricted applications are available through download-helper packages. These do **not** bundle the actual binary — they fetch it from the vendor on first install after you accept the license:

- `brave` — Brave Browser
- `chrome` — Google Chrome
- `claude-code` — Claude Code CLI
- `vscode` — Microsoft VS Code (proprietary build)

The NVIDIA proprietary driver follows the same opt-in pattern under the package name `nvidia`. Installing it (`pkm install nvidia`) presents the NVIDIA license for acceptance before the driver is fetched and installed.

## 4. The Wayland Posture

Wayland is the default display protocol. Every GNOME application ships with native Wayland support. X11 compatibility is provided through **Xwayland** for applications that have not yet been ported — this translation layer runs automatically when needed.

**Why Wayland:**

- **Per-window isolation**: Each Wayland-native application sees only its own input and pixel buffer, so one such application cannot key-log another or scrape another window's pixels. The exception is X11 applications: they run under the single Xwayland server described above, and the X protocol lets any client of that server read the input events and window contents of the others. Isolation holds between Wayland-native clients, and between Xwayland as a whole and the rest of the session — not among the X11 applications sharing it. Steam and most games are X11 clients.
- **Modern input handling**: HiDPI, variable refresh rate, mixed-DPI multi-monitor, and touch/gesture input work correctly because the protocol was designed for them.
- **No screen tearing**: Wayland composites every frame through the display server, eliminating tearing artifacts present in legacy X11 setups.
- **Future-proof**: The GNOME ecosystem, Firefox, and the broader Linux desktop world are standardizing on Wayland. X11 maintenance is winding down.

For the broader security story, see [Security Defaults](security-defaults.md).

## 5. Hardware Acceleration

InterGenOS ships the **Mesa** graphics stack for AMD (Radeon) and Intel (Arc, Iris, UHD) GPUs. This covers:

- OpenGL and OpenGL ES through `radeonsi` (AMD) and `iris` / `crocus` (Intel)
- Vulkan through `radv` (AMD) and `anv` (Intel)
- VA-API hardware video decoding through `radeonsi` (AMD)
- Compute (OpenCL) through `rusticl`

All Mesa drivers are installed and enabled by default. Firefox uses VA-API for hardware-accelerated video playback. GNOME Shell renderers use OpenGL or Vulkan automatically.

### NVIDIA GPUs

NVIDIA's proprietary driver is available as the `nvidia` package — an explicit, user-initiated opt-in. The base distribution does not ship proprietary firmware by default, and the driver is offered only on hardware with an NVIDIA GPU present. If you need CUDA or hardware-accelerated rendering on NVIDIA hardware, run `pkm install nvidia`, accept the NVIDIA license when prompted, and follow the post-install instructions for enrolling the NVIDIA kernel module with your Machine Owner Key.

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
- **No telemetry**: No component of the desktop phones home — not GNOME, not Firefox (telemetry locked OFF via canonical Mozilla `policies.json` + 19 about:config Preferences locks shipped at `/usr/lib/firefox/distribution/policies.json`; the `Locked` semantics mean the browser's own settings interface cannot re-enable them — an administrator with root can still edit that file, which is your machine and your call), not the shell, not the package manager.
- **No app-store analytics**: There is no usage tracking of any kind. `pkm` counts the packages you have installed for dependency resolution, but that data never leaves your machine.
- **No auto-update**: No background service downloads or applies updates without your explicit action. Run `sudo pkm sync && sudo pkm upgrade` when you choose to update.
- **No Plymouth boot splash**: InterGenOS shows your boot. We don't paint a logo over the 5–10 seconds between GRUB and GDM. You see the kernel hand off to systemd, you see every service start with [OK] or [FAILED] markers, you see your network come up, you see AppArmor load. If anything fails — a broken mount, a weird module load, a hardware quirk — you see it immediately. This is a security signal, not a polish gap. Spotting odd output during boot is an actual practice for catching compromise or hardware change; we'd rather give you that surface than hide it behind a corporate logo.

## 8. Cross-References

- [Getting Started](../getting-started.md) — First-boot walkthrough and initial system setup
- [Security Defaults](security-defaults.md) — Full breakdown of AppArmor, Secure Boot, systemd hardening, and kernel lockdown posture
- [Package Management](package-management.md) — pkm command reference, mirror trust chain, and archive verification
- [Databases](databases.md) — Which databases ship in InterGenOS and how to choose one for your project
- [Repository Trust Model](../repository-trust.md) — How the signed-index trust chain protects package downloads
