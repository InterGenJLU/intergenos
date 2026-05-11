#!/usr/bin/env python3
"""
validate-package-tiers.py — Rule 1 enforcement (canonical tier validation).

Encodes the decision tree from docs/package-tiers.md. For every
packages/*/*/package.yml in the repo, reports:

  - Whether the declared `tier:` matches the canonical natural tier
  - Whether every entry in `dependencies.build` and `dependencies.host`
    resolves to a package in the same-or-earlier tier

Output format: TSV to stdout.
  package <TAB> current_tier <TAB> verdict <TAB> notes

verdict ∈ {OK, MOVE→<tier>, UNCLEAR, CROSS-TIER-DEP}.

Exit codes:
  0  — every package is OK (or pending_acquisition)
  1  — one or more packages have MOVE/UNCLEAR/CROSS-TIER-DEP verdicts

The classifier uses three layers (in priority order):

  1. HARD CATEGORY MATCH — explicit name in one of the named-set lists
     below (LFS_CH8, SECURE_BOOT, PKG_MANAGER, FOUNDATIONAL_LIBS,
     LANGUAGE_RUNTIMES, PYTHON_BUILD_INFRA, BUILD_TOOLS,
     GUI_SUBSTRATE_DESKTOP, GNOME_CORE_DESKTOP, USER_FACING_APPS,
     AI_STACK). Deterministic.

  2. PATTERN MATCH — name patterns (e.g., `lib*` prefixed names with
     known GUI roots in their deps). Used to catch packages whose
     names aren't in the explicit lists but whose nature is unambiguous
     from naming convention.

  3. CONSUMER-TIER INFERENCE — for any package not categorized above,
     compute the set of tiers that consume it (via reverse-dep graph),
     take the earliest tier. This is the doc's library-takes-tier-of-
     consumer rule.

If none of the three layers produces a definitive answer, the verdict
is UNCLEAR and the package surfaces for maintainer review.

Reviewable; not auto-fix. Maintainer signs off on every non-OK row
before any tier move is applied.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

import yaml


REPO = Path(__file__).resolve().parent.parent
PACKAGES_DIR = REPO / "packages"
SCRIPTS_DIR = REPO / "scripts"

# Tier ordering (earliest → latest). Index used for comparison.
TIER_ORDER = ["toolchain", "core", "base", "desktop", "extra", "ai"]
TIER_INDEX = {t: i for i, t in enumerate(TIER_ORDER)}

# `extra` and `ai` are peers; both depend on `desktop`.
# Cross-tier dep allowed if dep's tier index <= consumer's tier index,
# with the special-case that `ai` is allowed to depend on `desktop` AND
# `extra` is allowed to depend on `desktop` (both have higher index than
# desktop), but `ai` and `extra` may not depend on each other. We handle
# this via the explicit allowed-relation table.

def tier_allowed(consumer_tier: str, dep_tier: str) -> bool:
    """Is it OK for `consumer_tier` to depend (build/host) on `dep_tier`?"""
    if dep_tier == consumer_tier:
        return True
    # The peer relation: extra and ai both build on desktop but not each other
    if consumer_tier == "extra" and dep_tier == "ai":
        return False
    if consumer_tier == "ai" and dep_tier == "extra":
        return False
    return TIER_INDEX[dep_tier] < TIER_INDEX[consumer_tier]


# ============================================================================
# Hard category lists — derived from docs/package-tiers.md decision tree
# ============================================================================

# LFS Ch 8 is derived from scripts/chroot-build-ch8.sh at runtime (single
# source of truth for which packages are in the LFS book's Ch 8 sequence).

# Secure Boot / initramfs / bootloader chain — explicit tier:core.
SECURE_BOOT_CHAIN = {
    "efivar", "efitools", "sbsigntool", "mokutil", "shim-signed",
    "busybox-static", "gnu-efi", "linux-kernel",
}

# Package manager + its hard deps.
PKG_MANAGER = {"pkm", "popt", "rpm"}

# Foundational system libraries — used by 30%+ of the rest of the
# distribution, including non-desktop packages. tier:core.
FOUNDATIONAL_LIBS = {
    # Core C/C++ runtime support
    "zlib", "ncurses", "readline", "gmp", "mpfr", "mpc",
    # System service substrate
    "dbus", "glib2", "glib2-bootstrap", "libffi",
    # TLS / crypto chain (LSB-adjacent)
    "openssl", "gnutls", "nettle", "libgpg-error", "libgcrypt",
    "libassuan", "libksba", "npth", "pinentry",
    "libtasn1", "libunistring", "libidn2", "p11-kit", "nss", "nspr",
    "make-ca", "libpsl",
    # Networking primitives
    "curl", "wget", "libssh2", "libpsl", "nghttp2", "c-ares",
    # Compression
    "brotli", "lz4", "xz", "zstd", "lzo", "xxhash",
    # XML / text data
    "libxml2", "libxslt", "libyaml", "libfyaml", "jansson", "json-c",
    "json-glib",
    # Storage / filesystems substrate
    "sqlite", "lmdb", "libuv", "libarchive",
    # Other foundational
    "popt", "pcre2", "libcap", "libseccomp", "libnl",
    "kmod", "eudev",
    # libpcap is in PASS1_FULL_BUILDS_DESKTOP (the pass1 lives in core)
    # libgudev moved to desktop 2026-05-11 — it's a GObject wrapper around
    # libudev; primary consumers are tier:desktop services. Per Rule 1.
    # Auth / session
    "linux-pam", "shadow", "shadow-pam", "libpwquality", "cracklib",
    "mitkrb", "keyutils", "sudo", "openssh",
    # Crypto-using daemons that are system services
    "polkit", "networkmanager",
    # GnuPG core
    "gnupg2", "gpgme", "gpgmepp",
    # System-level disk/volume management
    "lvm2", "cryptsetup", "btrfs-progs", "dosfstools", "fuse3",
    "libaio", "libatasmart", "libnvme", "libbytesize", "libblockdev",
    # Block-device / disk utilities used by core daemons
    "parted",
    # udisks2 moved to desktop 2026-05-11 — it's a D-Bus disk management
    # service whose primary consumers are file managers and the file-
    # mounting UX. Per Rule 1: desktop integration service.
    # Kernel headers / module mgmt
    "kmod",
    # Time / locale
    "tzdata", "iso-codes",
}

# Language runtimes — owner-direct tier:core, feature-complete.
LANGUAGE_RUNTIMES = {
    "perl", "python", "python3", "ruby", "rust", "nodejs", "node",
    "llvm", "lua", "luajit", "go", "cargo-c",
}

# Python build infrastructure (PEP 517 backends + version helpers).
PYTHON_BUILD_INFRA = {
    "hatchling", "hatch-vcs", "hatch-fancy-pypi-readme",
    "setuptools", "setuptools-scm", "setuptools_rust",
    "wheel", "pip", "pypa-build", "build",
    "pyproject_hooks", "pyproject-hooks", "pyproject-metadata",
    "pyproject_metadata", "meson_python", "meson-python",
    "flit", "flit-core", "flit_core",
    "pdm-backend", "poetry-core",
    "maturin", "uv_build", "uv-build",
    "editables", "pathspec", "pluggy", "trove-classifiers",
    "packaging", "tomli", "tomllib",
}

# Build-system tools — required to build a substantial fraction of the
# rest of the distribution.
BUILD_TOOLS = {
    "cmake", "meson", "ninja", "pkg-config", "pkgconf",
    "autoconf", "automake", "libtool", "m4", "bison", "flex",
    "gettext", "texinfo", "help2man", "makedepend",
    "git", "gperf", "intltool", "itstool", "asciidoc", "asciidoctor",
    "docbook-xml", "docbook-xsl", "docbook-xsl-nons",
    "xmlto", "doxygen", "sphinx", "docutils",
    "util-macros", "xorgproto", "wayland-protocols",
    "nasm", "yasm",
    "cython", "rpcsvc-proto", "unifdef", "highway",
    "rust-bindgen", "cbindgen",
}

# GUI substrate — naturally tier:desktop per decision tree.
GUI_SUBSTRATE_DESKTOP = {
    # Display servers / protocols
    "wayland", "wayland-protocols", "xwayland", "libxkbcommon",
    "xkbcomp", "xkeyboard-config",
    # X11 libraries
    "libX11", "libXau", "libXdmcp", "libxcb", "xcb-proto",
    "libXcomposite", "libXcursor", "libXdamage", "libXext",
    "libXfixes", "libXfont2", "libXft", "libXi", "libXinerama",
    "libXmu", "libXrandr", "libXrender", "libXtst", "libXt",
    "libXpm", "libXScrnSaver", "libXv", "libXxf86vm",
    "font-util",
    # Graphics stack
    "mesa", "libdrm", "libpciaccess", "libclc",
    "vulkan-headers", "vulkan-loader", "libepoxy",
    "libxshmfence", "libxcvt", "libdisplay-info",
    "spirv-headers", "spirv-tools", "glslang", "shaderc",
    "glu", "glm", "glad",
    # Font / text rendering
    "fontconfig", "freetype2", "freetype2-pass1", "harfbuzz",
    "pango", "cairo", "fribidi", "graphite2", "pixman",
    # Toolkits
    "gtk3", "gtk4", "gdk-pixbuf", "gdk-pixbuf-pass2",
    "librsvg", "libadwaita1", "libhandy1", "glycin",
    "gobject-introspection",
    # Image format libraries (primarily desktop-rendered)
    "libpng", "libjpeg-turbo", "libtiff", "libtiff-pass2",
    "libwebp", "libheif", "libde265", "libavif", "libjxl",
    "openjpeg2", "giflib", "lcms2",
    "exiv2", "gexiv2", "libexif",
    # Audio stack
    "alsa-lib", "gstreamer", "gst-plugins-base",
    "gst-plugins-base-pass2", "gst-plugins-good", "gst-plugins-bad",
    "pulseaudio", "pipewire", "wireplumber",
    "libogg", "libvorbis", "libsndfile", "libsamplerate",
    "flac", "opus", "speex", "lame", "lame-pass2", "mpg123",
    "fdk-aac", "sbc", "soundtouch", "taglib",
    # Video stack
    "ffmpeg", "libass", "libplacebo",
    "dav1d", "libaom", "libvpx", "libde265", "svt-av1",
    "x264", "x265", "libva",
    "libdvdread", "libdvdnav", "cdparanoia", "libcdio", "libcdio-paranoia",
    "totem-pl-parser",
    "libheif",
    # Print stack — LSB Print is a desktop-runtime capability
    "cups", "ghostscript",
    # Color management
    "colord", "colord-gtk", "babl", "gegl",
    # Desktop services that aren't core (polkit/NM are in core)
    "geoclue2", "modemmanager", "upower", "udisks2",
    "bluez", "avahi", "gvfs", "gnome-keyring", "gnome-online-accounts",
    # GNOME core
    "gnome-shell", "mutter", "gnome-desktop", "gnome-control-center",
    "gnome-session", "gjs", "gcr", "gcr4", "gspell",
    "tinysparql", "localsearch", "libgweather", "libsoup3", "librest",
    "libcanberra", "geocode-glib",
    "evolution-data-server", "evince",
    "libnotify", "libsecret", "libcloudproviders", "libical",
    "libportal", "libinput", "libei", "libevdev", "mtdev",
    "ibus",
    # Web rendering (consumed by Evolution, GNOME apps)
    "webkitgtk", "webkitgtk-gtk3",
    # GTK-source-view, adwaita, etc.
    "gtksourceview5", "adwaita-icon-theme", "hicolor-icon-theme",
    # XDG integrations
    "xdg-desktop-portal", "xdg-desktop-portal-gnome",
    "xdg-desktop-portal-gtk", "xdg-utils", "xdg-dbus-proxy",
    "desktop-file-utils", "shared-mime-info",
    "dconf", "gsettings-desktop-schemas", "at-spi2-core",
    # Python GUI bindings (consumed only by tier:desktop+)
    "pygobject3", "pycairo", "dbus-python",
    # Aux libraries used by desktop stack only
    "double-conversion", "icu", "fftw", "graphene",
    "graphviz",
    # Modem/network stack consumed by NetworkManager
    "libmbim", "libqmi", "libnvme",
    # X11 fonts
    "libfontenc", "libICE", "libSM",
    # Misc desktop-only
    "samba", "openldap", "cyrus-sasl", "libldap", "libsasl",
    "libplacebo", "gphoto2", "libgphoto2", "libmtp", "libimobiledevice",
    "libnfs", "libbluray", "libgusb",
    "mtdev", "libdaemon",
    "appstream", "libxmlb",
    "links", "lynx", "w3m",
    "luajit",  # often desktop-tier in BLFS; here flagged in language_runtimes already
    "spidermonkey",  # JS engine for polkit/gnome — could be core; see notes
    "imagemagick",
    "iptables",
    # Document conversion stack
    "poppler", "libgxps",
    # External integrations
    "libsoup3", "glib-networking",
    # XML stack used primarily by desktop
    "rasqal", "redland",
    # Bash completion / desktop-only shell helpers
    "bash-completion",
    # Misc desktop-tier helpers
    "pinentry-pass1",
    # Bluetooth audio etc.
    "libnewt",
    # Sound theme / freedesktop
    "sound-theme-freedesktop",
}

# User-facing applications layered on the desktop.
USER_FACING_APPS = {
    "libreoffice", "gimp", "inkscape", "krita", "blender",
    "code-oss", "firefox", "chromium", "google-chrome",
    "thunderbird",
    "audacity", "obs-studio", "vlc", "mpv",
    "telegram-desktop", "signal-desktop", "discord", "slack",
    "spotify",
    "discord-launcher",
    # GNOME-adjacent user-facing media apps (not GNOME-core)
    "celluloid", "rhythmbox", "transmission",
    # Container ecosystem (Podman + helpers) — user-installs-it-to-run-containers
    "podman", "crun", "conmon", "netavark", "aardvark-dns",
    "containers-common", "catatonit", "fuse-overlayfs",
    "passt", "go-md2man",
    # User-app launchers / helpers (the *-helper proprietary-binary wrappers)
    "brave-helper", "chrome-helper", "claude-code-helper",
    "discord-helper", "edge-helper", "spotify-helper",
    "vscode-helper",
    # Libraries used only by tier:extra apps (per Rule 1: library takes
    # tier of consumer). LibreOffice's file-format helpers + container
    # stack libs + transmission deps.
    "libcdr", "librevenge", "libvisio", "libwpd", "libwpg",
    "libdeflate", "libnatpmp", "libslirp", "libxcrypt-compat",
    "miniupnpc", "ncurses-compat", "yajl",
    "cppunit",
}

# Base-tier CLI utilities — QoL tools an experienced user expects.
BASE_CLI = {
    "at", "atop", "btop", "ed", "exim", "fcron",
    "htop", "iotop", "lsof", "pax", "rsync", "screen",
    "strace", "time", "tmux", "tree", "neofetch",
    "jq", "ripgrep", "fd", "bat", "ncdu", "dialog", "mc",
    "file", "less", "vim", "nano",
    "perl-file-fcntllock",
}

# AI assistant stack.
AI_STACK = {
    "intergen", "llama.cpp", "llama-cpp",
}


# ============================================================================
# LFS Ch 8 list — derived at runtime from chroot-build-ch8.sh
# ============================================================================

def collect_run_package_names(script_path: Path) -> Set[str]:
    if not script_path.exists():
        return set()
    text = script_path.read_text(errors="replace")
    return set(re.findall(r'^\s*run_package\s+"([^"]+)"', text, re.MULTILINE))


def load_lfs_ch8() -> Set[str]:
    return collect_run_package_names(SCRIPTS_DIR / "chroot-build-ch8.sh")


# ============================================================================
# Package model
# ============================================================================

def load_all_packages() -> Dict[str, dict]:
    """Returns {name: {tier, deps_build, deps_host, yml_path, pending_acquisition}}."""
    out = {}
    for yml in PACKAGES_DIR.rglob("package.yml"):
        try:
            data = yaml.safe_load(yml.read_text())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        tier = data.get("tier")
        if not name or not tier:
            continue
        deps = data.get("dependencies") or {}
        out[name] = {
            "tier": tier,
            "deps_build": list(deps.get("build") or []),
            "deps_host": list(deps.get("host") or []),
            "yml_path": yml,
            "pending_acquisition": data.get("pending_acquisition"),
        }
    return out


# ============================================================================
# Classifier — decision tree
# ============================================================================

# Pattern matchers — applied AFTER explicit lists. Patterns are conservative
# and tier-specific; they catch families of packages that share an obvious
# nature by name.
PATTERN_DESKTOP_PREFIXES = (
    "gnome-", "font-", "gst-", "xcb-util-",
)
PATTERN_DESKTOP_EXACT_X11_APPS = {
    # X11 client utilities — display/keyboard/mouse session tools
    "xauth", "xbitmaps", "xcursor-themes", "xcursorgen",
    "xdg-user-dirs", "xdpyinfo", "xdriinfo", "xev", "xhost",
    "xinput", "xmodmap", "xprop", "xrandr", "xrdb", "xset",
    "xwininfo", "bdftopcf", "encodings", "iceauth", "smproxy",
    "snapshot", "xauth", "xev",
    # X11 server-side fonts and config
    "font-alias", "font-cursor-misc", "font-dejavu",
    "font-misc-misc", "font-noto",
    # X11 libraries not in main GUI_SUBSTRATE list
    "libFS", "libICE", "libSM", "libXaw", "libXpresent",
    "libXvMC", "libXxf86dga", "libfontenc",
}
PATTERN_DESKTOP_AUDIO_VIDEO = {
    "a52dec", "alsa-plugins", "alsa-utils", "vorbis-tools",
    "wireless-regdb",
    "gvfs-pass2", "systemd-pass2",
    "freerdp", "gtk-vnc",
    "cups-filters",
    # Audio plugin runtime (LV2 stack)
    "sord", "sratom", "suil", "swh-plugins",
    "twolame", "wavpack",  # often desktop audio, sometimes extra
    # Document/RDF stack used by desktop apps
    "tdb",
    "tpm2-tss",  # TPM stack used by gnome-keyring + secure boot UX
    # Container/desktop integration boundary cases
    "yelp-xsl",
    # Audio aux
    "intel-ucode", "iucode-tool",  # firmware loaders — could be core; see below
}
PATTERN_DESKTOP_CXX_BINDINGS = {
    # C++ bindings for GUI toolkits — desktop-tier
    "cairomm", "glibmm", "gtkmm4", "atkmm", "pangomm", "sigc++",
}
PATTERN_DESKTOP_GNOME_AUX = {
    # GNOME ecosystem packages not in the GNOME-core hard list
    "baobab", "file-roller", "folks", "freerdp", "gdm",
    "grilo", "grilo-plugins", "gst-libav", "gst-plugins-ugly",
    "editorconfig-core-c", "vte",
    "sysprof", "yelp-xsl",
}

# Packages that look like they could be core but per docs/package-tiers.md
# are EXPLICITLY core — covers UNCLEAR cases.
FOUNDATIONAL_LIBS_EXTENDED = {
    "abseil-cpp", "protobuf",  # foundational C++ libs (used by many things)
    "apparmor",                # MAC framework, security service
    "wpa_supplicant",          # network auth (often paired w/ NetworkManager)
    "efibootmgr",              # EFI variable mgmt — boot chain
    "intel-ucode", "iucode-tool",  # CPU microcode — boot-time
    "linux-firmware",          # firmware blobs needed at boot for drivers
    "linux-kernel-pass2",      # kernel pass2 bootstrap variant; lives w/ kernel
    "pyyaml-pass2",            # PyYAML pass2 = follows pyyaml which is build infra
    "nftables",                # system-level firewall
    "libmnl", "libndp", "libnftnl",  # low-level netlink/nftables libs
    "sgml-common",             # DocBook/SGML data — build-time docs infra
    "pciutils",                # PCI enumeration — system-level
    "cpio",                    # initramfs construction
    "openldap",                # LDAP directory service (used by mitkrb, gnupg2, exim, samba)
    "cyrus-sasl",              # SASL auth (used by exim, openldap, samba)
    "icu",                     # Unicode (used by libxml2-full, node.js)
    "newt",                    # text-mode UI for installer dialogs
    "go",                      # foundational language runtime (per Node/Ruby/Rust convention)
    "meson_python",            # PEP 517 build backend (Python build infra)
    "pyproject-metadata",      # ditto
    "cbindgen", "rust-bindgen",# Rust→C ABI generators (build-time tools)
    "patchelf",                # ELF RPATH rewriter; build-time tool used by
                               # dbus-python and other Python bindings
    "editables", "pathspec", "pluggy", "trove-classifiers",  # hatchling deps (already in PYTHON_BUILD_INFRA above; defense in depth)
    # -pass1 bootstrap variants — the pass1 lives in tier:core to break
    # cross-tier cycles; the full build lives in tier:desktop.
    "libpcap-pass1", "slang-pass1", "networkmanager-pass1", "pinentry-pass1",
}

# Full-build variants of -pass1 packages — these LIVE in tier:desktop
# (with all their deep desktop deps available) and are superseded over
# their tier:core -pass1 counterparts at install time via
# migrate-pkm-supersedes.sh. Validator: treat as natural=desktop, not
# natural=core (which the foundational-lib heuristic would otherwise pick).
PASS1_FULL_BUILDS_DESKTOP = {
    "libpcap", "slang", "networkmanager", "pinentry",
    # Two more that have the same shape after the 2026-05-11 tier
    # correction batch (doxygen has ghostscript dep in desktop tier;
    # vala's consumers all moved to desktop, so it follows).
    "doxygen", "vala",
}

# Final-stragglers desktop additions (catches the remaining UNCLEAR rows).
GUI_SUBSTRATE_DESKTOP_EXTRA = {
    # X11 server-side utilities
    "libdmx", "sessreg", "setxkbmap",
    # iOS / USB device integration (consumed by gvfs)
    "libimobiledevice-glue", "libusbmuxd",
    # Multimedia decoders
    "libmad", "libmpeg2",
    # GNOME aux apps
    "loupe", "seahorse",
    # NSS plugin (mDNS service discovery — desktop primary)
    "nss-mdns",
    # poppler-data follows poppler
    "poppler-data",
    # Realtime kit for PulseAudio/PipeWire
    "rtkit",
    # SDL2 stack
    "sdl2", "sdl2-ttf",
}

# Stragglers base CLI utilities
BASE_CLI_EXTRA = {
    "parallel",  # GNU parallel
    "rdfind",    # duplicate-file finder
    "zip", "unzip",  # archive CLI utilities (alongside tar/gzip in core)
}


def hard_category_tier(name: str, lfs_ch8: Set[str]) -> str:
    """Returns the hard-rule tier for `name`, or '' if not categorized."""
    # PRIORITY 0: -pass1 / full-build variants override other rules.
    # A name like "libpcap" might match FOUNDATIONAL_LIBS, but the full
    # build lives in tier:desktop while libpcap-pass1 lives in tier:core.
    # The PASS1 sets explicitly say which tier each variant lives in.
    if name in PASS1_FULL_BUILDS_DESKTOP:
        return "desktop"
    # Step 1: toolchain is handled by directory location only.
    # Step 2-6 all map to tier:core:
    if name in lfs_ch8:
        return "core"
    if name in SECURE_BOOT_CHAIN:
        return "core"
    if name in PKG_MANAGER:
        return "core"
    if name in FOUNDATIONAL_LIBS:
        return "core"
    if name in FOUNDATIONAL_LIBS_EXTENDED:
        return "core"
    if name in LANGUAGE_RUNTIMES:
        return "core"
    if name in PYTHON_BUILD_INFRA:
        return "core"
    if name in BUILD_TOOLS:
        return "core"
    # Step 7-ish: base CLI utilities
    if name in BASE_CLI:
        return "base"
    if name in BASE_CLI_EXTRA:
        return "base"
    # Step 8: GUI substrate (explicit + patterns)
    if name in GUI_SUBSTRATE_DESKTOP:
        return "desktop"
    if name in GUI_SUBSTRATE_DESKTOP_EXTRA:
        return "desktop"
    if name in PATTERN_DESKTOP_EXACT_X11_APPS:
        return "desktop"
    if name in PATTERN_DESKTOP_AUDIO_VIDEO:
        return "desktop"
    if name in PATTERN_DESKTOP_CXX_BINDINGS:
        return "desktop"
    if name in PATTERN_DESKTOP_GNOME_AUX:
        return "desktop"
    for prefix in PATTERN_DESKTOP_PREFIXES:
        if name.startswith(prefix):
            return "desktop"
    # Step 9: AI stack
    if name in AI_STACK:
        return "ai"
    # Step 10: user-facing apps
    if name in USER_FACING_APPS:
        return "extra"
    # Pattern: any *-helper at user-facing-app boundary → extra
    if name.endswith("-helper") and not name.startswith("gnome-"):
        return "extra"
    return ""


def classify(packages: Dict[str, dict], lfs_ch8: Set[str]) -> Dict[str, str]:
    """Returns {name: natural_tier_or_UNCLEAR} via hard rules + consumer inference."""
    natural = {}
    # Pass 1: hard rules
    for name, p in packages.items():
        # Directory-based check for toolchain
        if "/toolchain/" in str(p["yml_path"]):
            natural[name] = "toolchain"
            continue
        t = hard_category_tier(name, lfs_ch8)
        if t:
            natural[name] = t

    # Pass 2: consumer-tier inference for the rest.
    # Build reverse dep graph: for each package, who declares it as a build/host dep?
    consumers: Dict[str, Set[str]] = {n: set() for n in packages}
    for name, p in packages.items():
        for dep in p["deps_build"] + p["deps_host"]:
            if dep in consumers:
                consumers[dep].add(name)

    # Iterate inference until stable.
    changed = True
    while changed:
        changed = False
        for name, p in packages.items():
            if name in natural:
                continue
            # Look at consumer tiers
            consumer_tiers = set()
            for c in consumers.get(name, ()):
                if c in natural:
                    consumer_tiers.add(natural[c])
            if consumer_tiers:
                # Earliest tier wins (library-takes-tier-of-consumer rule)
                earliest = min(consumer_tiers, key=lambda t: TIER_INDEX[t])
                natural[name] = earliest
                changed = True

    # Anything still uncategorized = UNCLEAR
    for name in packages:
        if name not in natural:
            natural[name] = "UNCLEAR"

    return natural


# ============================================================================
# Cross-tier-dep audit
# ============================================================================

def cross_tier_deps(packages: Dict[str, dict],
                    natural: Dict[str, str]) -> Dict[str, List[Tuple[str, str, str]]]:
    """Returns {consumer: [(dep_name, dep_kind, dep_tier), ...]} for backward edges
    based on natural (post-correction) tier."""
    out: Dict[str, List[Tuple[str, str, str]]] = {}
    for name, p in packages.items():
        if natural.get(name) in ("UNCLEAR", None):
            continue
        consumer_tier = natural[name]
        for dep in p["deps_build"]:
            dep_tier = natural.get(dep)
            if dep_tier and dep_tier != "UNCLEAR" and not tier_allowed(consumer_tier, dep_tier):
                out.setdefault(name, []).append((dep, "build", dep_tier))
        for dep in p["deps_host"]:
            dep_tier = natural.get(dep)
            if dep_tier and dep_tier != "UNCLEAR" and not tier_allowed(consumer_tier, dep_tier):
                out.setdefault(name, []).append((dep, "host", dep_tier))
    return out


# ============================================================================
# Main
# ============================================================================

def main(argv: list[str]) -> int:
    filter_name = argv[1] if len(argv) > 1 else None

    lfs_ch8 = load_lfs_ch8()
    packages = load_all_packages()
    natural = classify(packages, lfs_ch8)
    xtd = cross_tier_deps(packages, natural)

    print(f"# validate-package-tiers.py — Rule 1 + cross-tier-dep audit")
    print(f"# scanned {len(packages)} packages; LFS Ch 8 has {len(lfs_ch8)} entries")
    print(f"# tier ordering: {' → '.join(TIER_ORDER)}")
    print()
    print("package\tcurrent_tier\tverdict\tnotes")

    n_ok = n_move = n_unclear = n_xtd = n_pending = 0
    rows = []
    for name in sorted(packages):
        if filter_name and name != filter_name:
            continue
        p = packages[name]
        current = p["tier"]
        nat = natural[name]
        notes = []

        if p.get("pending_acquisition"):
            verdict = "OK"
            notes.append(f"pending_acquisition")
            n_pending += 1
        elif nat == "UNCLEAR":
            verdict = "UNCLEAR"
            n_unclear += 1
        elif nat != current:
            verdict = f"MOVE→{nat}"
            n_move += 1
        else:
            verdict = "OK"
            n_ok += 1

        if name in xtd:
            verdict = "CROSS-TIER-DEP" if verdict == "OK" else verdict + "+XTD"
            n_xtd += 1
            for dep, kind, dt in xtd[name]:
                notes.append(f"{kind}-dep on {dep}(tier:{dt})")

        rows.append((name, current, verdict, "; ".join(notes) if notes else ""))

    for r in rows:
        if r[2] != "OK":  # only print non-OK rows for readability
            print("\t".join(r))

    print()
    print(f"# summary: OK={n_ok}  MOVE={n_move}  UNCLEAR={n_unclear}  "
          f"CROSS-TIER-DEP={n_xtd}  PENDING={n_pending}")
    print(f"# total non-OK rows: {len(packages) - n_ok - n_pending}")

    return 0 if (n_move == 0 and n_unclear == 0 and n_xtd == 0) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
