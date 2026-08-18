#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

LIB32 GOVERNANCE (RT-14 + RT-9, GE gate-tooling 2026-07-02): `lib32-*`
packages are NOT classified by the three layers above. Their natural
tier derives from an explicit `lib32_source:` mapping field in their
package.yml naming the 64-bit sibling SOURCE package (lib32-libpulse →
pulseaudio, lib32-glibc → glibc-core — name-prefix stripping mis-keys
both, which is why the field is mandatory, never inferred). The same
field carries the RT-9 version lock: a lib32 package's `version:` must
equal its sibling's, bumped only together (mesa(64) ahead of lib32-mesa
= a title renders in 64-bit and misrenders in 32-bit with no gate naming
the skew). Every lib32 package must also declare `elf_class: "32"` (the
archive-time width audit keys on it). Violations report as
LIB32-GOVERNANCE and fail the run.

ELF-CLASS MIXED GOVERNANCE (carried by RT-14; closes the WC F-3 escape
hatch): a package may declare `elf_class: mixed` ONLY if it has an
Authorized entry in ELF_CLASS_MIXED_ALLOWED below. A mixed
declaration outside that set reports MIXED-UNGOVERNED and fails the run
— the width audit's waiver is a governed exception, not a recipe-local
opt-out.

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

# Tier ordering (earliest → latest). Index used for comparison. Mirrors the
# run_phase build order in build-intergenos.sh (reordered 2026-07-21 for the
# training-stack wave: extra and compute now build BEFORE ai, so the ai-tier
# GPU-native builds can consume the compute SDKs and the extra-tier python/C++
# libs at build time. The former ai<->extra peer exclusion dissolved with the
# reorder — the order is a plain chain again).
TIER_ORDER = ["toolchain", "core", "base", "desktop", "extra", "compute", "ai"]
TIER_INDEX = {t: i for i, t in enumerate(TIER_ORDER)}


def tier_allowed(consumer_tier: str, dep_tier: str) -> bool:
    """Is it OK for `consumer_tier` to depend (build/host) on `dep_tier`?"""
    if dep_tier == consumer_tier:
        return True
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
    # 2026-06-23 NK1 smartcard / PIV signing stack — the hardware token path
    # for the signing ceremonies (sign-bootloader / sign-shim / sign-kernel-uki).
    # pcsc-lite (daemon+lib) → ccid (USB reader driver) → opensc (PKCS#11,
    # PATCHED for NK1 RSA-4096 PIV) → libp11 (OpenSSL PKCS#11 engine/provider).
    "pcsc-lite", "ccid", "opensc", "libp11",
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
    # Binary serialization (header-only C++; rocblas/Tensile msgpack reader)
    "msgpack-cxx",
    # Storage / filesystems substrate
    "sqlite", "lmdb", "libuv", "libarchive",
    # Other foundational
    "popt", "pcre2", "libcap", "libseccomp", "libnl",
    "kmod", "eudev",
    # libcap-ng: retiered extra->core 2026-07-17 — util-linux-pass2 needs
    # it at core-extra build time (setpriv); sibling of libcap above.
    # qemu/libvirt/swtpm still consume it downstream.
    "libcap-ng",
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
    # Boot-time systemd generator providing RAM-backed compressed swap.
    # Runs in early boot (systemd generator) → core per the "required at
    # boot or by systemd to bring the system up" rule in package-tiers.md.
    "zram-generator",
    # udisks2 moved to desktop 2026-05-11 — it's a D-Bus disk management
    # service whose primary consumers are file managers and the file-
    # mounting UX. Per Rule 1: desktop integration service.
    # Kernel headers / module mgmt
    "kmod",
    # Time / locale
    "tzdata", "iso-codes",
}

# Language runtimes — decided tier:core, feature-complete.
LANGUAGE_RUNTIMES = {
    "perl", "python", "python3", "ruby", "rust", "nodejs", "node",
    "llvm", "lua", "luajit", "go", "cargo-c",
}

# Python build infrastructure (PEP 517 backends + version helpers).
PYTHON_BUILD_INFRA = {
    "hatchling", "hatch-vcs", "hatch-fancy-pypi-readme",
    "setuptools", "setuptools-scm", "setuptools_rust", "setuptools-rust",
    "wheel", "pip", "pypa-build", "build",
    "pyproject_hooks", "pyproject-hooks", "pyproject-metadata",
    "pyproject_metadata", "meson_python", "meson-python",
    "flit", "flit-core", "flit_core",
    "pdm-backend", "poetry-core",
    "maturin", "uv_build", "uv-build",
    # Training-stack wave backends retiered ai->core (decided 2026-07-21 —
    # every python build backend lives here with its siblings above):
    "scikit-build-core", "scikit_build_core", "versioneer",
    # F12 wave 2026-07-21: setuptools-rust's runtime dep (imported by its
    # extension/rustc_info modules) — build-infra sibling.
    "semantic-version", "semantic_version",
    "editables", "pathspec", "pluggy", "trove-classifiers",
    "packaging", "tomli", "tomllib",
    "iniconfig", "pytest",  # pytest test stack — runtime/leaf, consumer-inference can't reach them (added 2026-06-23)
    # Python C-FFI + crypto runtime (required for systemd-pass2's ukify
    # tool — see packages/desktop/systemd-pass2/build.sh -D ukify=enabled).
    "cffi", "pycparser",
    "python-pefile", "python-cryptography",
    # MessagePack serializer — build-time dep of the compute tier's rocblas
    # (Tensile's fast logic-file path); same core python-lib shape as pyyaml.
    "python-msgpack",
    # Pipelining/parallelism lib — Tensile requirements.txt build dep of the
    # compute tier's rocblas; same core python-lib shape as python-msgpack.
    "python-joblib",
    # C++ header parser — hipamd configure hard-requires it (rocm-hip build
    # dep); same core python-lib shape as python-joblib.
    "python-cppheaderparser",
    # Python Lex-Yacc — CppHeaderParser's lexer backend (its only runtime
    # dep); same core python-lib shape.
    "python-ply",
    # HTML/XML parser + its required CSS selector engine. Consumer-inference
    # cannot reach these for the same reason it cannot reach pytest: the thing
    # that imports them is not a package. scripts/parse-blfs-book.py runs on
    # the build host and produces build/blfs-packages.db, which
    # preflight-audit-coverage.py and preflight-silent-loss.py read — so they
    # are libraries the distribution is BUILT and GATED with, which is what
    # this set holds. Both are iso_include: false; core is what they ARE,
    # mirror-only is how they are delivered (the lib32-* pairing).
    "beautifulsoup4", "soupsieve",
}

# Build-system tools — required to build a substantial fraction of the
# rest of the distribution.
BUILD_TOOLS = {
    "cmake", "meson", "ninja", "pkg-config", "pkgconf",
    # gyp (gyp-next): meta-build system, cmake/meson sibling — drives
    # lib32-nss's upstream build.sh path (decided 2026-07-02).
    "gyp",
    "autoconf", "automake", "libtool", "m4", "bison", "flex",
    "gettext", "texinfo", "help2man", "makedepend",
    "git", "gperf", "intltool", "itstool", "asciidoc", "asciidoctor",
    # python-pip: the interpreter's package installer — build-time consumer
    # count ~68 recipes; owned-package onboarding decided 2026-07-28 (A-3).
    "python-pip",
    "docbook-xml", "docbook-xsl", "docbook-xsl-nons",
    "xmlto", "doxygen", "sphinx", "docutils",
    "util-macros", "xorgproto",
    "nasm", "yasm",
    # wayland-protocols removed 2026-05-12: it's GUI substrate (desktop
    # tier) per its in-tree consumers (all 8 are desktop). Its prior
    # presence in this BUILD_TOOLS set was the root cause of the bulk
    # core-retier (commit 8dc10cc) that broke its build order vs wayland.
    "cython", "rpcsvc-proto", "unifdef", "highway",
    "rust-bindgen", "cbindgen",
}

# GUI substrate — naturally tier:desktop per decision tree.
GUI_SUBSTRATE_DESKTOP = {
    # Display servers / protocols
    "wayland", "wayland-protocols", "xwayland", "libxkbcommon",
    "xkbcomp", "xkeyboard-config",
    # Wayland client-side decorations + seat/session management substrate
    # (libdecor = CSD library; seatd = seat mgmt daemon + libseat). Wayland
    # compositor substrate libs — siblings of wayland/libinput — pulled in by
    # the gamescope stack. Added 2026-07-09 (gamescope floor).
    "libdecor", "seatd",
    # X11 libraries
    "libX11", "libXau", "libXdmcp", "libxcb", "xcb-proto",
    "libXcomposite", "libXcursor", "libXdamage", "libXext",
    "libXfixes", "libXfont2", "libXft", "libXi", "libXinerama",
    "libXmu", "libXrandr", "libXrender", "libXtst", "libXt",
    "libXpm", "libXScrnSaver", "libXv", "libXxf86vm",
    # libXres — X-Resource extension client lib (maps X windows to client PIDs);
    # X.Org lib, sibling of the libX* family. Added 2026-07-09 (gamescope floor).
    "libXres",
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
    # cups-pk-helper: PolicyKit mechanism exposing CUPS configuration to the
    # GNOME Printers panel (gnome-control-center). A desktop print-INTEGRATION
    # service (like polkit/colord), not a user-installed application — belongs
    # with cups in desktop per docs/package-tiers.md (desktop integration
    # service + LSB Print). Without this explicit entry the generic
    # *-helper→extra fallback (hard_category_tier) misfiles it as extra.
    "cups-pk-helper",
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
    "gparted",  # GNOME GUI partition editor (GTK app; runtime-consumes exfatprogs)
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
    "brave", "chrome", "claude-code",
    "discord", "edge", "spotify",
    "vscode", "ffmpeg-nonfree",
    # 2026-06-23 host-migration comms helpers (download-helper pattern,
    # mirror-only): signal (AGPL payload), zoom (proprietary payload).
    "signal", "zoom",
    # 2026-07-02 GE extra-tier wave: the GE-Proton compatibility-tool
    # download-helper (D3 decided ADD; chrome-exemplar,
    # pin-exact posture).
    "ge-proton",
    # 2026-07-02 operator decision 2: the Steam client download-helper
    # (chrome-exemplar against Valve's SIGNED apt repo, scoped
    # weak-digest posture, fail-closed 32-bit-closure launch wrapper).
    # Sibling of the *-helper wrappers + ge-proton above.
    "steam",
    # 2026-07-02 GE arc: the gaming meta-package (RT-5 experimental-NVK
    # wording; RT-11 flat lib32 set as direct deps; amdgpu-meta
    # precedent, mirror-only).
    "gaming",
    # 2026-07-21 training-stack wave: the two grouping meta-packages on
    # the gaming pattern (mirror-only, flat derived runtime sets) —
    # `training` = the model-training stack closure, `compute` = the
    # full ROCm platform.
    "training",
    "compute",
    # 2026-07-09 gamescope floor: Valve's SteamOS session micro-compositor,
    # a user-facing gaming utility built from source (sibling of the gaming
    # stack above). tier:extra per the GE-arc precedent.
    "gamescope",
    # 2026-07-09 GE-tooling wave: Feral Interactive's game-mode daemon — a
    # user-facing gaming utility (`gamemoderun <game>`) built from source,
    # sibling of gamescope above. tier:extra per the GE-arc precedent.
    "gamemode",
    # 2026-08-05 desktop-application wave: three user-launchable GTK
    # applications layered on the desktop, none of them part of GNOME.
    # timeshift = system restore utility (rsync/btrfs snapshots),
    # remmina = remote desktop client, handbrake = video transcoder.
    # tier:extra per the package-tiers decision tree step 11; same class
    # as gimp/inkscape/audacity already listed above.
    "timeshift", "remmina", "handbrake",
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
    "strace", "time", "tree", "neofetch",
    "jq", "ripgrep", "fd", "bat", "ncdu", "dialog", "mc",
    "file", "less", "vim", "nano",
    "perl-file-fcntllock",
    # 2026-06-23 host-migration build-artifact toolchain + dev tooling.
    # squashfs-tools/xorriso/mtools = ISO/image construction (an InterGenOS
    # box building InterGenOS ISOs); sshpass + neovim = dev/admin tooling.
    # (tmux moved OUT of BASE_CLI → MODERN_CLI_TOOLS_EXTRA: it links libevent,
    # a desktop-tier lib, so it must live in extra to satisfy build order.)
    "squashfs-tools", "xorriso", "mtools", "sshpass", "neovim",
    # 2026-06-30 network client utilities (BLFS) — CLI tools an experienced user
    # expects, not required to boot (docs/package-tiers.md base criteria): dig/host/
    # nslookup (bind-utils), whois, traceroute.
    "bind-utils", "whois", "traceroute",
    # 2026-08-18 system-maintenance + hardware-inspection utilities, same base
    # criteria: a CLI tool, the system boots without it, and every
    # general-purpose distribution carries it.
    #   logrotate — rotates the system logs, including the
    #     /etc/logrotate.d/intergen-tool-dispatch snippet the tree already
    #     shipped with no consumer.
    #   usbutils  — lsusb and friends; names devices from the udev hwdb.
    #   nvme-cli  — the CLI consumer of the core libnvme library.
    #   ethtool   — reports and changes network-interface state; needed before
    #     a machine can reach a mirror, which is why it is on the ISO and the
    #     mirror-only network diagnostics are not.
    "logrotate", "usbutils", "nvme-cli", "ethtool",
}

# AI assistant stack.
AI_STACK = {
    "intergen", "llama.cpp", "llama-cpp",
    # Training-stack wave (decided 2026-07-21, set-complete pass): the
    # from-source fine-tuning apparatus — mirror-only (iso_include: false
    # per-recipe), tier: ai. Torch family + HF stack + their pure-python
    # closure. Build backends are NOT here (core, PYTHON_BUILD_INFRA).
    "pytorch", "torchvision", "torchao", "xformers", "triton", "bitsandbytes",
    "unsloth", "unsloth-zoo", "cut-cross-entropy",
    "transformers", "trl", "peft", "datasets", "accelerate",
    # timm (2026-08-05): PyTorch Image Models, the image-model half of the same
    # HF stack as transformers and diffusers, and a direct consumer of
    # torchvision. Named here for the reason the whole set exists — consumer
    # inference builds its reverse graph from build/host edges only, and timm
    # is a leaf that nothing in this tree imports yet, so it has no consumer
    # edge and would classify UNCLEAR.
    "timm",
    "huggingface-hub", "diffusers", "tokenizers", "safetensors",
    "hf-xet", "hf-transfer", "sentencepiece",
    "arrow-cpp", "pyarrow", "libcst", "pillow",
    "pydantic", "pydantic-core", "annotated-types", "annotated-doc",
    "msgspec", "typer", "tyro", "typeguard", "typing-inspection",
    "docstring-parser", "shellingham", "click",
    "httpx", "httpcore", "h11", "anyio", "sniffio",
    "fsspec", "sympy", "mpmath", "networkx",
    "regex", "tqdm", "einops", "psutil", "dill", "multiprocess",
    "nest-asyncio", "pytz",
    # python-xxhash (2026-08-04): the Python binding for xxHash, imported at
    # module load by datasets/fingerprint.py. Same closure class as dill and
    # multiprocess above — a general-purpose library whose only consumer in
    # this tree is the ai-tier datasets package. It is listed here for the
    # same reason they are: consumer inference builds its reverse graph from
    # build/host deps only, so a library that is exclusively a RUNTIME
    # dependency has no consumer edge and classifies UNCLEAR. The tier: core
    # xxhash package is a different thing — the C library and xxhsum CLI,
    # which this binding links but no Python import can substitute for.
    "python-xxhash",
    # (python-dateutil retiered to AI_SUPPORT_LIBS_EXTRA 2026-07-21 —
    # runtime dep of pandas/extra, consumed by compute-tier aotriton.)
}

# Compute tier: opt-in GPU compute SDKs + engine variants (mirror-only;
# iso_include defaults false for tier: compute). The ROCm 7.2.4 subset the
# HIP engine links (hip + hipBLAS + rocBLAS and their build chain), the
# CUDA toolkit install-helper (helper only — nvcc is not redistributable,
# so the toolkit itself is fetched from NVIDIA at install time and never
# enters an archive), and the llama.cpp GPU engine variants.
COMPUTE_GPU_SDKS = {
    "rocm-llvm", "rocm-device-libs", "rocm-comgr", "rocr-runtime",
    "rocm-hip", "rocminfo", "rocblas", "hipblas", "rocwmma", "rocsolver",
    "rocprofiler-register",
    "cuda-toolkit", "llama-cpp-hip", "llama-cpp-cuda",
    # ROCm build-chain additions (2026-07-15 wave): CMake build-tools modules
    # (rocwmma REQUIREs; preempts rocblas/hipblas network FetchContent) + the
    # 7.x monorepo's split-out hipBLAS common headers.
    "rocm-cmake", "hipblas-common",
    # Compute-stack support libraries (decided 2026-07-16):
    # rocprofiler-register's system-lib deps — its bundled path is an
    # unpinned git-submodule clone, fatal on a tarball build. General C++
    # libs whose only consumers today are compute-tier; retier when a
    # broader consumer appears.
    "fmt", "glog",
    # ROCm platform-completion wave (decided 2026-07-17: v1 = the full
    # user-facing platform, classes A-I + HIPIFY; rdc excluded).
    # Math-library set (rocm-libraries monorepo):
    "rocprim", "hipcub", "rocthrust",          # class E primitives
    "rocrand", "hiprand",                      # class C random
    "rocfft", "hipfft",                        # class B FFT
    "rocsparse", "hipsparse",                  # class D sparse
    "hipsolver",                               # class A (rocsolver pre-listed)
    "composable-kernel", "hipblaslt", "miopen", "migraphx",  # class F ML
    # Ops/monitoring + comm (rocm-systems monorepo + standalone rccl):
    "rocm-smi-lib", "amdsmi", "rccl",
    # Profiling/debug (class I) + the CUDA-porting tool:
    "roctracer", "aqlprofile", "rocprofiler-sdk",
    # (elfutils-libdw retiered to USER_FACING_LIBS_EXTRA 2026-07-21 —
    # the retier-on-broader-consumer contract fired: libvirt/qemu (extra)
    # declare it runtime, and extra now precedes compute.)
    "rocm-core",
    "rocdbgapi", "rocgdb", "rocm-debug-agent", "hipify",
    # Header-only third-party deps of the ML stack (fmt/glog precedent —
    # only consumers today are compute-tier; retier on a broader consumer):
    "nlohmann-json", "eigen", "functionalplus", "frugally-deep",
    "pybind11", "half",
    # Training-stack compute additions (decided 2026-07-21): the pinned LLVM
    # builds triton and aotriton's bundled triton fork require (private
    # prefixes /opt/triton-llvm and /opt/aotriton-llvm, consumed only via
    # explicit LLVM_SYSPATH), and the from-source ROCm SDPA backend.
    "triton-llvm", "aotriton", "aotriton-triton-llvm",
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

# elf_class: mixed governance (RT-14-carried; closes the WC F-3 escape hatch).
# A `mixed` declaration waives the archive-time ELF word-size audit for the
# whole package, so WHO may declare it is a governed, authorized set —
# same discipline as the tier precedent sets (build-rules §3.11). Every entry
# carries the reason it is legitimate. A mixed declaration NOT in this set
# fails the run as MIXED-UNGOVERNED.
ELF_CLASS_MIXED_ALLOWED = {
    # grub ships 32-bit i386-pc BIOS boot modules inside the 64-bit package —
    # firmware-facing boot code, never host-loaded through ld.so. Declared
    # elf_class: mixed in packages/core/grub/package.yml (RT-1 gate landing).
    "grub": "i386-pc BIOS boot modules ride the 64-bit grub package",
    # gcc-core (multilib) bundles its 32-bit target runtime (libgcc_s,
    # libstdc++ under /usr/lib32) inside the 64-bit package — the GE design's
    # ONE allowlisted G1 exception (Debian-style; igos-build has no
    # subpackage-split machinery and a synthetic lib32-gcc-libs would serve
    # the build, not the user). Ratified with the GE design review; the
    # 32-bit halves stay NEEDED-closure-audited by Step 4.75.
    "gcc-core": "multilib gcc bundles its own 32-bit target runtime in /usr/lib32",
    # llvm (multilib toolchain era) bundles clang's 32-bit compiler-rt
    # runtimes (ASan/UBSan/Scudo + crt objects under
    # /usr/lib/clang/*/lib/i386-unknown-linux-gnu/) inside the 64-bit
    # package — real 32-bit code clang links into -m32 builds, unlocked by
    # the GE multilib toolchain (compiler-rt i386 autodetection). Same shape
    # as gcc-core. Authorized 2026-07-03 (GE-01 launch-gate arc).
    "llvm": "clang bundles its 32-bit compiler-rt runtimes for -m32 targets",
    # rust (multilib toolchain era) bundles the i686-unknown-linux-gnu std
    # library under /opt/rustc/lib/rustlib/i686-unknown-linux-gnu/ inside
    # the 64-bit package — real 32-bit code rustc links into --target
    # i686 builds (NVK/lib32-mesa is the consumer that forced it, GE-01
    # L15). The llvm/gcc-core shape verbatim: a multilib compiler
    # carrying its own 32-bit target runtime. 4th member of the mapped
    # mixed family (GE-01 launch-gate arc, 2026-07-03).
    "rust": "rustc bundles its i686 std for --target i686 builds (NVK)",
}


def load_all_packages(packages_dir: Path = PACKAGES_DIR,
                      malformed: list | None = None) -> Dict[str, dict]:
    """Returns {name: {tier, version, version_is_str, deps_build, deps_host,
    yml_path, pending_acquisition, lib32_source, elf_class}}.

    FAIL-CLOSED on malformed manifests (re-certification finding G1-a): a package.yml that
    cannot be parsed or has the wrong structure is appended to `malformed`
    as (path, reason) and reported by main() as a halting MALFORMED-MANIFEST
    verdict — NEVER silently skipped. (The old `continue` made an
    unparseable recipe vanish from validation — a Rule-2 silent-skip shape —
    and a structure error like a list-valued `dependencies:` crashed the
    validator with no summary output, which the orchestrator's old deny-list
    acceptance then waved through.) Library callers that pass no collector
    get the exception raised — loud, never dropped."""
    out = {}
    for yml in packages_dir.rglob("package.yml"):
        try:
            data = yaml.safe_load(yml.read_text())
            if not isinstance(data, dict):
                raise ValueError(
                    f"top level is {type(data).__name__}, expected a mapping")
            name = data.get("name")
            tier = data.get("tier")
            if not name or not tier:
                raise ValueError("missing required name:/tier: fields")
            deps = data.get("dependencies") or {}
            if not isinstance(deps, dict):
                raise ValueError(
                    f"dependencies: is {type(deps).__name__}, expected a mapping")
            for dep_kind in ("build", "host", "runtime"):
                v = deps.get(dep_kind)
                if v is not None and (not isinstance(v, list)
                                      or not all(isinstance(x, str) for x in v)):
                    raise ValueError(
                        f"dependencies.{dep_kind}: must be a list of strings "
                        f"(got {type(v).__name__} — a scalar here list()-"
                        f"coerces into a character list)")
            # Scalar-type validation (re-cert residual 3): a list/mapping
            # authored where a scalar belongs must become a NAMED row here,
            # not a traceback in whichever downstream consumer first
            # dereferences it (the build still halted either way — this is
            # the diagnostic upgrade).
            for field in ("name", "tier", "version", "lib32_source",
                          "elf_class", "pending_acquisition"):
                val = data.get(field)
                if val is not None and not isinstance(val, (str, int, float, bool)):
                    raise ValueError(
                        f"{field}: is {type(val).__name__}, expected a scalar")
            raw_version = data.get("version")
            out[name] = {
                "tier": tier,
                # str-normalized for display; version_is_str preserves the
                # AUTHORED type — YAML coerces an unquoted 1.10 to the float
                # 1.1 BEFORE str() runs, so a float version can mask a
                # genuine RT-9 trailing-zero skew (re-certification finding G1-b).
                # lib32_audit refuses non-string versions on a lib32 pair.
                "version": str(raw_version) if raw_version is not None else None,
                "version_is_str": isinstance(raw_version, str),
                "deps_build": list(deps.get("build") or []),
                "deps_host": list(deps.get("host") or []),
                "yml_path": yml,
                "pending_acquisition": data.get("pending_acquisition"),
                "lib32_source": data.get("lib32_source"),
                "elf_class": str(data.get("elf_class")) if data.get("elf_class") is not None else None,
                # W2-a: source/patch sha pins, for the lib32 twin↔sibling
                # source-identity gate (a twin builds the sibling's EXACT
                # artifact — divergence at the same release string was
                # invisible to governance until this).
                "source_shas": [s.get("sha256")
                                for s in (data.get("source") or [])
                                if isinstance(s, dict)],
                "patch_shas": [pt.get("sha256")
                               for pt in (data.get("patches") or [])
                               if isinstance(pt, dict)],
            }
        except Exception as e:
            if malformed is None:
                raise
            malformed.append((yml, f"{e.__class__.__name__}: {e}"))
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
    # Capture stack (capture wave): UCM profiles beside alsa-lib; camera
    # pipeline library + V4L2 tooling + the AEC engine, all consumed by
    # the pipewire/wireplumber desktop audio-video substrate.
    "alsa-ucm-conf", "libcamera", "v4l-utils", "webrtc-audio-processing",
    "wireless-regdb",
    "gvfs-pass2", "systemd-pass2", "dbus-pass2", "libxml2-pass2",
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
    "sof-firmware",            # Intel audio-DSP firmware — same boot-time
                               # driver-probe class as linux-firmware (which
                               # upstream ships without Intel SOF)
    "linux-kernel-pass2",      # kernel pass2 bootstrap variant; lives w/ kernel
    "pyyaml-pass2",            # PyYAML pass2 = follows pyyaml which is build infra
    "nftables",                # system-level firewall
    "libmnl", "libndp", "libnftnl",  # low-level netlink/nftables libs
    "liburing",                # low-level io_uring kernel I/O lib (Jens Axboe);
                               # libdex(desktop)+fuse3(core)+rocksdb(extra) consume
                               # it across tiers, so the cross-tier rule forces it
                               # to core (re-tiered from extra 2026-06-25)
    "sgml-common",             # DocBook/SGML data — build-time docs infra
    "pciutils",                # PCI enumeration — system-level
    "cpio",                    # initramfs construction
    "openldap",                # LDAP directory service (used by mitkrb, gnupg2, exim, samba)
    "cyrus-sasl",              # SASL auth (used by exim, openldap, samba)
    "icu",                     # Unicode (used by libxml2-full, node.js)
    "oniguruma",               # multi-charset regex lib — jq's regex engine (added 2026-06-23)
    "newt",                    # text-mode UI for installer dialogs
    "go",                      # foundational language runtime (per Node/Ruby/Rust convention)
    "meson_python",            # PEP 517 build backend (Python build infra)
    "pyproject-metadata",      # ditto
    "cbindgen", "rust-bindgen",# Rust→C ABI generators (build-time tools)
    "patchelf",                # ELF RPATH rewriter; build-time tool used by
                               # dbus-python and other Python bindings
    "editables", "pathspec", "pluggy", "trove-classifiers",  # hatchling deps (already in PYTHON_BUILD_INFRA above; defense in depth)
    # InterGen aiohttp web-UI stack Python deps — explicitly core (they live in
    # packages/core/ and serve core consumers) but consumer-inference can't
    # reach them: attrs is a RUNTIME dep of core/aiohttp so the build/host-dep-
    # only inference misses that consumer and mis-suggests desktop; pkgconfig
    # collides with the pkg-config tool name; expandvars has no declared
    # consumer. Resume builds skipped the validate phase, so this surfaced only
    # on the clean GBC001 build. (2026-06-03)
    "attrs", "pkgconfig", "expandvars",
    # -pass1 bootstrap variants — the pass1 lives in tier:core to break
    # cross-tier cycles; the full build lives in tier:desktop.
    "libpcap-pass1", "slang-pass1", "networkmanager-pass1", "pinentry-pass1",
    "vala-pass1",  # added 2026-05-11: --disable-valadoc bootstrap that
                   # satisfies libgudev's vala dep without pulling graphviz
                   # (tier:desktop) into tier:core. Full vala stays in desktop.
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
    # 2026-08-18 desktop integration services. All three are loaded or driven
    # by something in the desktop rather than launched by a user, and all three
    # are invisible to consumer inference because nothing build-depends on them
    # (the desktop consumes them at run time, over D-Bus or as a plugin).
    #   switcheroo-control — the D-Bus service GNOME Shell asks which GPU an
    #     application should run on; it is what puts "Launch using Discrete
    #     Graphics Card" in the menu on hybrid-graphics machines.
    #   NetworkManager-openvpn / -openconnect — VPN plugins loaded by
    #     NetworkManager, each shipping a libnma/GTK authentication dialog.
    #     They are mirror-only (iso_include: false in their recipes) because
    #     each is useless without the mirror-only client binary it drives, but
    #     mirror-only is a delivery axis and does not change what they ARE.
    "switcheroo-control",
    "NetworkManager-openvpn",
    # X11 server-side utilities
    "libdmx", "sessreg", "setxkbmap",
    # iOS / USB device integration (consumed by gvfs)
    "libimobiledevice-glue", "libusbmuxd",
    # Multimedia decoders
    "libmad", "libmpeg2",
    # GNOME aux apps
    "loupe", "seahorse",
    # zenity: GNOME GTK dialog utility (GUI substrate — dialog boxes for
    # scripts). Its sole consumer, intergen (ai), declares it as a RUNTIME
    # dep, which is invisible to the build/host reverse-dep graph — so
    # consumer-inference cannot place it and it classifies UNCLEAR without
    # this entry. A core GNOME GUI utility → desktop per docs/package-tiers.md.
    "zenity",
    # libdbusmenu: GTK-menus-over-D-Bus library providing the Dbusmenu
    # GObject-Introspection typelibs for GNOME Shell tray/quicklist support.
    # Consumed by gnome-shell at RUNTIME only (typelib pull), invisible to
    # the build/host reverse-dep graph — so consumer-inference cannot place
    # it and it classifies UNCLEAR without this entry. A GNOME Shell support
    # library → desktop per docs/package-tiers.md (declared tier:desktop).
    "libdbusmenu",
    # NSS plugin (mDNS service discovery — desktop primary)
    "nss-mdns",
    # poppler-data follows poppler
    "poppler-data",
    # Realtime kit for PulseAudio/PipeWire
    "rtkit",
    # SDL2 stack
    "sdl2", "sdl2-ttf",
    # SASS CSS preprocessor — build-time tool for GTK themes (consumed by
    # adw-gtk3-theme, catppuccin-gtk-theme, etc.; tier:desktop per its
    # earliest consumer).
    "dart-sass",
    # Vendor-neutral GL dispatch (libGL/libEGL/libGLES front-end) — the desktop
    # GL substrate that the GPU drivers register behind. tier:desktop.
    "libglvnd",
    # open-vm-tools: VMware guest userspace daemon (vmtoolsd) shipping the
    # timeSync + resolutionKMS (DRM/KMS dynamic-resize) plugins for VMware VM
    # guests. tier:desktop — it links libdrm (a desktop package) for the resize
    # plugin and integrates with the desktop session. Classifies UNCLEAR because
    # NOTHING in-tree reverse-depends on it (it is a leaf guest-integration
    # daemon, not a build/runtime dep of anything), so consumer-inference cannot
    # place it — same shape as zenity/libdbusmenu above. Hand-anchored desktop.
    "open-vm-tools",
}

# Stragglers base CLI utilities
BASE_CLI_EXTRA = {
    "parallel",  # GNU parallel
    "rdfind",    # duplicate-file finder
    "zip", "unzip",  # archive CLI utilities (alongside tar/gzip in core)
}

# ============================================================================
# 2026-05-22 evening CST: classifier extension for first-ever fresh-revert
# golden-builder run. The named sets below capture the maintainer's intent
# for packages that the prior 3-layer classifier could not auto-resolve
# from upstream-naming-convention or BLFS lineage. Each set encodes a
# canonical CATEGORY the operator has hand-classified per PRIME DIRECTIVE +
# Rule 1 (tier reflects what the package IS).
# ============================================================================

# Secure Boot adjacent — static-binary variants used in early-init by the
# initramfs FDE/MOK/TPM chain. tier:core.
SECURE_BOOT_STATIC = {
    "cryptsetup-static", "fido2-tools-static", "tpm2-tools-static",
}

# System utilities (LFS/BLFS canonical) — disk/filesystem/init management
# foundations. tier:core. inih + liburcu are xfsprogs's two unconditional
# build deps; the classifier needs xfsprogs anchored to core for inih's
# consumer-inference to resolve correctly.
SYSTEM_UTILITIES_CORE = {
    "util-linux-pass2",  # util-linux rebuilt with libcap-ng-backed setpriv
                         # (pass 1 = LFS-sacrosanct util-linux-core, which the
                         # book builds --disable-setpriv); supersedes pass 1
    "gptfdisk",     # GPT partition tools (sgdisk, cgdisk, gdisk)
    "mdadm",        # Linux MD software RAID admin
    "ntfs-3g",      # NTFS driver + ntfsprogs utilities
    "exfatprogs",   # exFAT mkfs/fsck tools — kernel-native fs, same shape as dosfstools; external-drive support
    "os-prober",    # Detect other OSes for grub dual-boot
    "xfsprogs",     # XFS filesystem utilities
    "liburcu",      # Userspace RCU (xfsprogs xfs_io threading dep)
    "inih",         # INI file parser (xfsprogs config-file dep)
    "numactl",      # libnuma + NUMA policy tools — rocr-runtime runtime dep; multi-node placement control
}

# InterGenOS first-party core packages — policy/data shippers per D-006
# (GNOME defaults SSoT), D-011 (firewall defaults SSoT), and the keyring /
# legal / helper-lib triplet that's been a core fixture since 2026-05.
# No upstream; the fleet IS upstream. tier:core.
INTERGENOS_FIRSTPARTY_CORE = {
    "ca-certificates",                # Mozilla CA bundle for OS-level TLS
    "intergenos-base-files",          # /etc baseline + FHS skeleton (Debian base-files analog)
    "intergenos-default-settings",    # D-006 SSoT GNOME defaults
    "intergenos-firewall-defaults",   # D-011 SSoT firewall policy
    "intergenos-helper-lib",          # H-007 helper-lib for pkm install helpers
    "intergenos-keyring",             # GPG release-signing trust keyring
    "intergenos-legal",               # LICENSE + SOURCES.md to /usr/share/doc/
}

# InterGen assistant Python runtime libraries. tier:core per the shared-Python-
# lib convention (cf. python-cryptography, pyyaml, setuptools — all tier:core),
# built in the core-extra phase so they are present before the ai tier builds
# intergen. These are RUNTIME deps of ai/intergen, so classify()'s consumer-
# inference (which follows build/host deps only) cannot reach them — hence this
# explicit rule. Added 2026-05-31 with the InterGen AI-stack package set.
INTERGEN_PYTHON_RUNTIME_CORE = {
    "aiohttp", "aiohappyeyeballs", "aiosignal", "frozenlist", "multidict",
    "propcache", "yarl",                # aiohttp async-HTTP stack + transitive deps
    "idna",                             # IDNA/URL encoding (aiohttp dep)
    "typing-extensions",                # typing backports (broad transitive dep)
    "rich", "markdown-it-py", "mdurl",  # rich terminal rendering + its markdown deps
    "prompt-toolkit", "wcwidth",        # interactive console UI for intergen + width calc
}

# GPU drivers + userspace tools — optional hardware support. tier:extra.
# (libglvnd, the vendor-neutral GL dispatch lib, is tier:desktop and lives in
# GUI_SUBSTRATE_DESKTOP_EXTRA, not here.) Added 2026-05-31.
GPU_DRIVERS_EXTRA = {
    "amdgpu", "amdgpu_top", "radeontop",          # AMD GPU driver + monitors
    "nvidia",                                     # NVIDIA proprietary driver
    "libva-utils", "libvdpau", "libvdpau-va-gl",  # VA-API / VDPAU acceleration
    "vulkan-tools",                               # Vulkan diagnostics
}

# Container runtime tooling — optional. tier:extra. Added 2026-05-31.
CONTAINER_RUNTIME_EXTRA = {
    "docker", "containerd", "runc",
    # 2026-08-18 container tooling wave. docker-buildx is a docker CLI plugin
    # and lands in the cli-plugins directory the docker package already creates
    # and documents as empty. buildah and skopeo are daemonless peers that
    # build and move OCI images; they belong with the engine because they are
    # the same capability, not user applications layered on the desktop.
    "docker-buildx", "buildah", "skopeo",
}

# The mingw-w64 PE cross-toolchain (GE extra-tier wave, RT-15 staged
# bootstrap) — tier:extra per the ratified GE multilib build plan §4
# (private operations doc; the gaming stack is mirror-only). The
# -bootstrap intermediate is install-set-required per the standing
# -pass/-bootstrap rule. Added 2026-07-02.
WINDOWS_CROSS_TOOLCHAIN_EXTRA = {
    "mingw-w64-binutils", "mingw-w64-headers", "mingw-w64-gcc-bootstrap",
    "mingw-w64-crt", "mingw-w64-winpthreads", "mingw-w64-gcc",
    "mingw-w64-tools",
}

# The Windows-compatibility runtime stack (GE extra-tier wave) —
# tier:extra per the ratified GE multilib build plan §4; mirror-only,
# reaches installs via the gaming meta. Grows with each landing (dxvk,
# vkd3d-proton, the gaming meta) under the same-commit discipline.
# Added 2026-07-02.
WINDOWS_COMPAT_RUNTIME_EXTRA = {
    "wine", "dxvk", "vkd3d-proton",
    # The wine addon MSI data packages (operator decision 1, 2026-07-02):
    # pure-data shippers of wine's own addons.c-pinned MSIs, killing the
    # silent plain-HTTP prefix-creation fetch. Runtime deps of wine.
    "wine-gecko", "wine-mono",
    # cabextract — Microsoft Cabinet (.cab) extractor; winetricks runtime dep
    # for unpacking the Windows .cab components (fonts/runtimes) winetricks
    # installs into a Wine prefix. A standalone CLI tool (bundled libmspack),
    # tier:extra as part of this Windows-compat lane. Consumer-inference cannot
    # place it (winetricks runtime-depends, not build-depends), so anchor here.
    "cabextract",
    # winetricks — the Wine-prefix component/tweak helper itself (a single
    # POSIX-sh script; runtime-deps wine/cabextract/unzip/wget). Mirror-only
    # tier:extra, the consumer that anchors cabextract above. 2026-07-09
    # GE-tooling wave.
    "winetricks",
}

# Themes / icons / cursors — pure-data shippers, tier:desktop.
THEMES_DESKTOP = {
    # GTK themes
    "adw-gtk3-theme", "catppuccin-gtk-theme", "dracula-gtk-theme",
    "fluent-gtk-theme", "graphite-gtk-theme", "nordic-theme",
    "orchis-theme", "whitesur-gtk-theme",
    # Icon themes
    "cybernetic-icon-theme", "fluent-icon-theme", "intergenos-icon-theme",
    "papirus-icon-theme", "tela-icon-theme", "whitesur-icon-theme",
    # Cursor themes
    "bibata-cursor-theme", "macos-cursor-theme", "phinger-cursors",
    # InterGenOS-branded theming
    "intergenos-grub-theme", "intergenos-theme",
}

# InterGenOS / InterGen first-party desktop packages — GNOME-tier
# extensions, installer (forge), first-boot helpers, branded assets.
INTERGENOS_FIRSTPARTY_DESKTOP = {
    # InterGen* — AI/first-boot/GNOME-extensions
    "intergen-firstboot", "intergen-mark", "intergen-no-overview",
    "intergen-pkm-notifier", "intergen-toggle", "intergen-welcome",
    # InterGenOS GNOME extension bundles
    "intergenos-extensions-appearance", "intergenos-extensions-layout",
    "intergenos-extensions-productivity", "intergenos-extensions-utilities",
    "intergenos-settings-arrow", "intergenos-launch-monitor",
    # InterGenOS visual assets
    "intergenos-wallpapers",
    # InterGenOS offline wiki (signed docs content for the assistant citation
    # surface - first-party desktop data, wallpapers-class)
    "intergenos-wiki",
    # InterGenOS installer
    "forge",
    # InterGenOS backup utility (Chronicle) — GTK4/libadwaita app + engine,
    # sibling of intergen-welcome/forge (first-party desktop, tier:desktop).
    "intergenos-backup",
    # GNOME Shell user-theme extension (lets users switch shell themes)
    "user-theme",
}

# User-facing server daemons — tier:extra. User installs these to run
# them as services; not part of base-system or desktop substrate.
SERVER_APPS_EXTRA = {
    "apache-httpd", "caddy", "haproxy", "lighttpd", "nginx",
    # 2026-06-23: network service daemon (user-installs-it, ships tailscaled.service,
    # disabled-by-default). Host-migration comms tooling.
    "tailscale",
}

# Databases / kv stores — tier:extra. User-installed services.
DATABASE_APPS_EXTRA = {
    "etcd", "influxdb", "mariadb", "memcached", "postgresql", "valkey",
}

# Language ecosystems / dev runtimes — tier:extra (mirror-installable). These
# are full development/server language stacks a user installs to build and run
# software, NOT foundational build substrate. They are DISTINCT from the core
# LANGUAGE_RUNTIMES set (go/rust/nodejs/ruby/python): those are tier:core because
# the InterGenOS tree itself builds through them; nothing in core/base/desktop
# build-depends on these, so they belong in extra (user-facing applications
# layered on the desktop) and default to iso_include:false — the RC001
# unlock-lane "package universe available via the mirror" model. Composer is the
# PHP dependency manager (its ecosystem tool), grouped with PHP.
LANGUAGE_ECOSYSTEMS_EXTRA = {
    "openjdk",    # OpenJDK JDK/JRE, built from source (binary-seed bootstrap)
    "erlang",     # Erlang/OTP language + runtime
    "php",        # PHP scripting language + FPM
    "composer",   # PHP dependency manager (vendor-exception pinned phar)
    "zig",        # Zig compiler/toolchain (builds against in-tree LLVM)
    "ghc",        # Glasgow Haskell Compiler, from source via pinned binary seed
    "julia",      # Julia (bundled-dep -full tarball, offline from-source build)
    "R",          # R statistical environment (bundled reference BLAS/LAPACK)
}

# Modern CLI tools (Rust/Go single-binary QoL utilities) that operator
# scoped to tier:extra rather than tier:base. Distinction from BASE_CLI
# is curated: BASE_CLI = expected-by-experienced-user; tier:extra = nice
# but optional.
MODERN_CLI_TOOLS_EXTRA = {
    "bottom",      # btop-like system monitor (Rust)
    "dust",        # du replacement (Rust)
    "eza",         # ls replacement (Rust)
    "grex",        # regex-generator from examples (Rust)
    "hugo",        # static site generator (Go)
    "hyperfine",   # benchmarking tool (Rust)
    "just",        # task runner (Rust)
    "lazygit",     # git TUI (Go)
    "lego",        # ACME / Let's Encrypt client (Go)
    "sd",          # sed replacement (Rust)
    "starship",    # cross-shell prompt (Rust)
    "tealdeer",    # tldr client (Rust)
    "tokei",       # code statistics (Rust)
    "xh",          # httpie-like HTTP client (Rust)
    "zoxide",      # smarter cd (Rust)
    "gh",          # GitHub CLI (Go) — 2026-06-23 host-migration dev tooling
    "tmux",        # terminal multiplexer — in extra (not base) because it
                   # links libevent (desktop tier) + base builds before desktop
}

# Hardware / storage diagnostic CLI utilities — classic C tools that ship in the
# image (iso_include:true) for post-install diagnostic use. tier:extra per the
# 2026-05-03 D4 directive (PI-Z24). Distinct from MODERN_CLI_TOOLS_EXTRA (Rust/Go
# CLI replacements): these are firmware/SMART/DMI inspectors an admin runs to check
# drive health and read the BIOS hardware inventory.
SYSTEM_DIAGNOSTICS_EXTRA = {
    "smartmontools",  # S.M.A.R.T. disk health (smartctl, smartd)
    "dmidecode",      # BIOS SMBIOS/DMI hardware inspector (dmidecode, biosdecode)
    "lm-sensors",     # hwmon thermal/fan visibility (sensors, sensors-detect,
                      # fancontrol) — ships per the 2026-07-21 thermal ruling
}

# Network diagnostics (2026-08-18). Mirror-only command-line tools for finding
# where a network problem is, plus the meta-package that installs the set.
# tier:extra is FORCED for two of them rather than chosen: tcpdump and nmap
# both link libpcap, which is tier:desktop here, and a base-tier package may
# not build-depend on a desktop-tier one. The other three sit with them so the
# meta-package's whole closure lives in one tier. ethtool is deliberately NOT
# here — it is in BASE_CLI and ships on the ISO, because it reports on the
# machine's own interface rather than on the network beyond it.
NETWORK_DIAGNOSTICS_EXTRA = {
    "mtr",                   # per-hop loss and latency along a path
    "tcpdump",               # packet capture and decode (links libpcap)
    "iperf3",                # achievable throughput between two hosts
    "nmap",                  # host and service discovery (links libpcap)
    "socat",                 # arbitrary connection construction
    "network-diagnostics",   # dependency-only meta-package over the five
}

# VPN clients and their helper (2026-08-18). Mirror-only: a VPN client is an
# opt-in capability a user installs, not a utility every general-purpose
# system carries, and none of these is needed to reach the mirror. The
# NetworkManager plugins that front two of them are desktop-tier integration
# and are classified with the desktop sets, not here.
VPN_CLIENTS_EXTRA = {
    "openvpn",        # TLS-based VPN daemon and client
    "openconnect",    # Cisco AnyConnect / Juniper / GlobalProtect / Fortinet
    "wireguard-tools",# wg + wg-quick, the user-space half of in-kernel WireGuard
    "vpnc-scripts",   # the routing/DNS helper openconnect refuses to build without
    # The OpenConnect NetworkManager plugin sits here rather than with the
    # desktop integration services because it LINKS libopenconnect, making
    # openconnect a build dependency and forcing the plugin into the later
    # tier. Its OpenVPN sibling only executes a binary at run time and stays
    # in the desktop set.
    "NetworkManager-openconnect",
}

# Support libraries whose only consumers today are the training stack
# (arrow-cpp's C/C++ codec/compute deps + the python libs aotriton's build
# venv consumes). tier:extra (mirror-only) — extra precedes compute and ai
# in the build order, so those consumers may depend on them. Same
# retier-on-broader-consumer contract as the fmt/glog compute precedent.
AI_SUPPORT_LIBS_EXTRA = {
    "thrift", "re2", "utf8proc", "xsimd",
    "pandas", "filelock",
    # pandas' runtime dep — moved with it (2026-07-21; compute-tier
    # aotriton imports pandas at configure time, so the whole closure
    # must precede the compute phase).
    "python-dateutil",
}

# Libraries / build helpers consumed only by tier:extra apps.
# Mirrors the pattern already used inside USER_FACING_APPS for LibreOffice
# helper-lib deps -- separating these out keeps the named-set semantics
# tight (libraries vs apps).
USER_FACING_LIBS_EXTRA = {
    # Apache deps
    "apr", "apr-util",
    # Database-stack libraries
    # (liburing removed 2026-06-25: it is a low-level kernel io_uring I/O lib,
    # not a database lib — it was mis-grouped here because rocksdb consumes it.
    # It is now FOUNDATIONAL_LIBS/core: libdex(desktop)+fuse3(core) also consume
    # it, and the cross-tier rule forces it <= core. See package-tiers.md.)
    "jemalloc", "leveldb", "rocksdb", "snappy",
    # Build helpers used by tier:extra apps
    "scons", "gflags", "gopls",
    # DWARF introspection lib — libvirt/qemu (extra) runtime dep; moved
    # from COMPUTE_GPU_SDKS 2026-07-21 under its recorded
    # retier-on-broader-consumer contract (compute profilers still
    # consume it; extra precedes compute).
    "elfutils-libdw",
    # Python WebSocket lib for the intergen-web-ui (packages/extra/intergen-web-ui/)
    "websockets",
    # NOTE: dart-sass is in GUI_SUBSTRATE_DESKTOP_EXTRA below (consumed by
    # tier:desktop GTK themes, not tier:extra apps).
    #
    # 2026-08-05 desktop-application wave: the libraries the three new
    # applications need and nothing else in the tree provides. Each takes
    # the tier of its only consumer (package-tiers.md, library-follows-
    # consumer rule):
    #   libxapp       -> timeshift (taskbar progress reporting)
    #   libssh        -> remmina (SSH/SFTP tunnelling; distinct from the
    #                    core-tier libssh2, a different project)
    #   libvncserver  -> remmina (the VNC plugin links libvncclient)
    #   libsodium     -> remmina (a hard build requirement upstream)
    #   libtheora     -> handbrake (the Theora encoder/decoder it links
    #                    from the system rather than from its own contribs)
    "libxapp", "libssh", "libvncserver", "libsodium", "libtheora",
}

# Icon-generation toolchain (authored 2026-07-22 under the mirror-first
# package directive — these were previously pip-pulled by the generator's
# own run instructions). Pure-python SVG-rendering stack: cairosvg atop
# cairocffi (cffi bindings to the desktop tier's libcairo) + its CSS/XML
# support libs. Mirror-only (iso_include:false, the extra-tier default);
# consumed by design tooling, not by any shipped app.
ICON_TOOLCHAIN_EXTRA = {
    "cairosvg", "cairocffi", "cssselect2", "tinycss2",
    "webencodings", "defusedxml",
}

# Virtualization / self-hosting stack (Ubuntu-replacement wave, 2026-07-16).
# The host-side hypervisor + management + client + guest-firmware set, and
# the support libraries whose ONLY consumers are these extra-tier packages
# (per package-tiers.md: a library used only by tier:extra packages is
# tier:extra). Kernel-side KVM support is core (linux-kernel); everything
# here is the optional userspace layered on the desktop.
VIRTUALIZATION_STACK_EXTRA = {
    # hypervisor + firmware + device backends
    "qemu", "seabios", "edk2-ovmf", "virtiofsd",
    "swtpm", "libtpms", "acpica",
    # management stack
    "libvirt", "libvirt-glib", "libvirt-python",
    "virt-manager", "virt-viewer",
    # SPICE remoting + peripherals
    "spice-protocol", "spice", "spice-gtk", "usbredir", "phodav",
    # OS metadata for guest provisioning
    "libosinfo", "osinfo-db", "osinfo-db-tools",
    # network support (consumers: qemu/libvirt). libcap-ng moved to the
    # foundational core set 2026-07-17 (util-linux-pass2 build dep).
    "dnsmasq",
    # Python support libraries (consumers: virt-manager runtime,
    # spice-gtk codegen)
    "python-six", "python-pyparsing", "python-requests",
    "python-urllib3", "python-charset-normalizer",
    "python-certifi",
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
    if name in SECURE_BOOT_STATIC:
        return "core"
    if name in SYSTEM_UTILITIES_CORE:
        return "core"
    if name in INTERGENOS_FIRSTPARTY_CORE:
        return "core"
    if name in INTERGEN_PYTHON_RUNTIME_CORE:
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
    if name in THEMES_DESKTOP:
        return "desktop"
    if name in INTERGENOS_FIRSTPARTY_DESKTOP:
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
    # Step 9b: compute tier (opt-in GPU SDKs + engine variants)
    if name in COMPUTE_GPU_SDKS:
        return "compute"
    # Step 10: user-facing apps
    if name in USER_FACING_APPS:
        return "extra"
    if name in SERVER_APPS_EXTRA:
        return "extra"
    if name in DATABASE_APPS_EXTRA:
        return "extra"
    if name in LANGUAGE_ECOSYSTEMS_EXTRA:
        return "extra"
    if name in MODERN_CLI_TOOLS_EXTRA:
        return "extra"
    if name in SYSTEM_DIAGNOSTICS_EXTRA:
        return "extra"
    if name in USER_FACING_LIBS_EXTRA:
        return "extra"
    if name in AI_SUPPORT_LIBS_EXTRA:
        return "extra"
    if name in GPU_DRIVERS_EXTRA:
        return "extra"
    if name in CONTAINER_RUNTIME_EXTRA:
        return "extra"
    if name in NETWORK_DIAGNOSTICS_EXTRA:
        return "extra"
    if name in VPN_CLIENTS_EXTRA:
        return "extra"
    if name in WINDOWS_CROSS_TOOLCHAIN_EXTRA:
        return "extra"
    if name in WINDOWS_COMPAT_RUNTIME_EXTRA:
        return "extra"
    if name in VIRTUALIZATION_STACK_EXTRA:
        return "extra"
    if name in ICON_TOOLCHAIN_EXTRA:
        return "extra"
    # Pattern: any *-helper at user-facing-app boundary → extra
    if name.endswith("-helper") and not name.startswith("gnome-"):
        return "extra"
    return ""


def classify(packages: Dict[str, dict], lfs_ch8: Set[str]) -> Dict[str, str]:
    """Returns {name: natural_tier_or_UNCLEAR} via hard rules + consumer inference.

    lib32-* packages bypass all three layers: their tier DERIVES from the
    declared `lib32_source:` sibling (RT-14) in pass 3, after everything else
    has classified. Neither name-pattern nor consumer-inference may place a
    lib32 package — an unmapped one is UNCLEAR (and lib32_audit names the
    missing field precisely)."""
    natural = {}
    # Pass 0 (RT-14): reserve lib32-* names for sibling derivation.
    lib32 = {n for n in packages if n.startswith("lib32-")}

    # Pass 1: hard rules
    for name, p in packages.items():
        if name in lib32:
            continue
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
            if name in natural or name in lib32:
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

    # Pass 3 (RT-14): lib32-* derive tier from the declared 64-bit sibling.
    # The mapping field is authoritative — evaluated INSTEAD of patterns and
    # inference, never after them. Missing/bad mappings resolve UNCLEAR here;
    # lib32_audit reports the precise defect.
    for name in lib32:
        src = packages[name].get("lib32_source")
        if src and src in packages and src not in lib32:
            natural[name] = natural.get(src, "UNCLEAR")
        else:
            natural[name] = "UNCLEAR"

    # Anything still uncategorized = UNCLEAR
    for name in packages:
        if name not in natural:
            natural[name] = "UNCLEAR"

    return natural


# ============================================================================
# lib32 + elf_class governance audit (RT-14 mapping field, RT-9 version lock,
# mixed-declaration governance)
# ============================================================================

# LIB32-PATCH-OMISSION governed exemptions (W2-a latent-edge close): a twin
# may omit a sibling patch ONLY through a named (twin, patch-sha) entry here,
# with the reason it cannot affect the twin's shipped libs — same governed-set
# discipline as ELF_CLASS_MIXED_ALLOWED (build-rules §3.11). Any other
# omission fails the run.
LIB32_PATCH_OMISSION_ALLOWED = {
    # mesa's only patch adds the xdemos DEMO PROGRAMS (glxgears/glxinfo) —
    # 64-bit tools the libs-only twin never builds; the shipped libraries
    # are untouched by it. Documented in-recipe (lib32-mesa package.yml,
    # the operator-reviewed mesa-pair landing 2026-07-02).
    ("lib32-mesa",
     "9677943764bfadc2800714e34933507365dfc24b33ec9d5a4720db03b6168f3d"):  # xdemos patch sha256 pin
        "xdemos patch builds 64-bit demo programs the libs-only twin omits",
    # nss's only patch (nss-standalone-1.patch) reworks the raw-make
    # coreconf plumbing for the sibling's standalone make build; the twin
    # builds via NSS's first-class build.sh gyp path (--target ia32,
    # Decided 2026-07-02), which never invokes that plumbing —
    # the shipped libraries compile from the same sources. Documented
    # in-recipe (lib32-nss package.yml).
    ("lib32-nss",
     "87bb1af0b11fd41311b9899187f6e4b3fca9940651123c7bc836ec7497d2da84"):  # nss-standalone-1.patch sha256 pin
        "standalone patch serves the raw-make lane the gyp-path twin never invokes",
}


def lib32_audit(packages: Dict[str, dict]) -> Dict[str, List[str]]:
    """Returns {name: [violation, ...]} for every lib32/elf_class governance
    defect. Empty dict == clean. Every check fails CLOSED: a lib32 package
    the validator cannot key deterministically is a violation, never a pass."""
    out: Dict[str, List[str]] = {}

    def flag(name: str, msg: str) -> None:
        out.setdefault(name, []).append(msg)

    for name, p in packages.items():
        src = p.get("lib32_source")
        is_lib32 = name.startswith("lib32-")

        if not is_lib32:
            if src:
                flag(name, f"lib32_source declared on a non-lib32 package "
                           f"(field is reserved for lib32-* recipes)")
        else:
            if not src:
                flag(name, "missing lib32_source: — every lib32-* package "
                           "MUST name its 64-bit sibling source package "
                           "(RT-14; name-prefix stripping mis-keys, so the "
                           "field is mandatory, never inferred)")
            elif src not in packages:
                flag(name, f"lib32_source '{src}' does not resolve to any "
                           f"package in the tree")
            elif src.startswith("lib32-") or src == name:
                flag(name, f"lib32_source '{src}' must name the 64-bit "
                           f"SOURCE package, not another lib32 package")
            else:
                # RT-9: siblings version-lock, bumped only together.
                # Both versions MUST be authored as quoted strings: YAML
                # coerces an unquoted 1.10 to the float 1.1 before any
                # comparison, silently masking a real trailing-zero skew
                # (re-certification finding G1-b) — refuse the coercion itself.
                for vp, vn in ((p, name), (packages[src], src)):
                    if vp.get("version") is not None and not vp.get("version_is_str"):
                        flag(name, f"{vn} version is an unquoted YAML number "
                                   f"— coercion (1.10 -> 1.1) can mask a "
                                   f"trailing-zero skew; quote the version "
                                   f"string (G1-b)")
                v, sv = p.get("version"), packages[src].get("version")
                if v != sv:
                    flag(name, f"version {v} != 64-bit sibling {src} "
                               f"version {sv} — lib32 siblings version-lock, "
                               f"bumped only together (RT-9)")
                # W2-a (Wave-2 verify finding): the twin↔sibling SOURCE
                # IDENTITY is a checked gate, not an assumption. A twin
                # builds its sibling's exact artifact, so every sha the twin
                # pins must appear in the sibling's pin set (SUBSET rule: a
                # twin may omit sibling co-sources — lib32-glibc omits the
                # tzdata co-source — but may never diverge a shared artifact
                # or introduce a foreign one; a same-release-string sha
                # divergence was previously invisible to governance).
                # Same rule for patch pins. source_shas is None only for
                # loader-less test fixtures; the real loader always
                # provides lists.
                twin_shas = p.get("source_shas")
                if twin_shas is not None:
                    sib_shas = set(filter(None, packages[src].get("source_shas") or []))
                    pinned = [s for s in twin_shas if s]
                    if not pinned:
                        flag(name, "twin declares no sha-pinned source — a "
                                   "lib32 twin reuses its sibling's pinned "
                                   "tarball; an unpinned twin source cannot "
                                   "be identity-checked (LIB32-SOURCE-DRIFT)")
                    else:
                        for sha in pinned:
                            if sha not in sib_shas:
                                flag(name, f"source sha {sha[:16]}… is not in "
                                           f"64-bit sibling {src}'s pin set — "
                                           f"a twin builds the sibling's exact "
                                           f"artifact (LIB32-SOURCE-DRIFT)")
                twin_patches = p.get("patch_shas")
                if twin_patches is not None:
                    sib_patches = set(filter(None, packages[src].get("patch_shas") or []))
                    for sha in filter(None, twin_patches):
                        if sha not in sib_patches:
                            flag(name, f"patch sha {sha[:16]}… is not in "
                                       f"64-bit sibling {src}'s patch set — "
                                       f"a twin carries no foreign patches "
                                       f"(LIB32-SOURCE-DRIFT)")
                    # W2-a latent edge (the Wave-2 adversarial verify's
                    # observation, closed same day): the SUBSET rule permits
                    # omission, which is right for co-SOURCES (lib32-glibc
                    # legitimately omits the tzdata co-source) but wrong for
                    # PATCHES — a sibling patch that alters the built
                    # artifact, dropped by a twin, builds divergent libs at
                    # the same release string and nothing else would see it.
                    # Every sibling patch sha must appear in the twin's
                    # patch pins unless the (twin, sha) pair is in the
                    # governed exemption set below — a named, sha-scoped,
                    # reasoned entry per build-rules §3.11 (same discipline
                    # as ELF_CLASS_MIXED_ALLOWED), never a silent drop.
                    twin_patch_set = set(filter(None, twin_patches))
                    for sha in sib_patches:
                        if sha in twin_patch_set:
                            continue
                        if (name, sha) in LIB32_PATCH_OMISSION_ALLOWED:
                            continue
                        flag(name, f"64-bit sibling {src}'s patch sha "
                                   f"{sha[:16]}… is MISSING from the "
                                   f"twin's patch set — a dropped sibling "
                                   f"patch builds divergent libs "
                                   f"(LIB32-PATCH-OMISSION)")
            if p.get("elf_class") != "32":
                flag(name, "lib32-* must declare elf_class: \"32\" — the "
                           "archive-time ELF word-size audit keys on it")

        # elf_class: mixed is a governed waiver, never a recipe-local opt-out.
        if p.get("elf_class") == "mixed" and name not in ELF_CLASS_MIXED_ALLOWED:
            flag(name, "elf_class: mixed is not governed for this package — "
                       "a mixed declaration waives the width audit and "
                       "requires an authorized "
                       "ELF_CLASS_MIXED_ALLOWED entry in this validator")

    return out


# ============================================================================
# Cross-tier-dep audit
# ============================================================================

def cross_tier_deps(packages: Dict[str, dict],
                    natural: Dict[str, str]) -> Tuple[
                        Dict[str, List[Tuple[str, str, str]]],
                        Dict[str, List[Tuple[str, str]]]]:
    """Returns ({consumer: [(dep_name, dep_kind, dep_tier), ...]},
    {consumer: [(dep_name, dep_kind), ...]}) — backward edges based on
    natural (post-correction) tier, plus DANGLING dep names: a declared
    dep that matches no package in the tree. The old `natural.get(dep)`
    falsy-skip made a typo'd or retired dep name vanish from validation."""
    out: Dict[str, List[Tuple[str, str, str]]] = {}
    dangling: Dict[str, List[Tuple[str, str]]] = {}
    for name, p in packages.items():
        if natural.get(name) in ("UNCLEAR", None):
            continue
        consumer_tier = natural[name]
        for kind in ("build", "host"):
            for dep in p[f"deps_{kind}"]:
                if dep not in packages:
                    dangling.setdefault(name, []).append((dep, kind))
                    continue
                dep_tier = natural.get(dep)
                if dep_tier and dep_tier != "UNCLEAR" and not tier_allowed(consumer_tier, dep_tier):
                    out.setdefault(name, []).append((dep, kind, dep_tier))
    return out, dangling


# ============================================================================
# Main
# ============================================================================

def main(argv: list[str]) -> int:
    # Optional --packages-dir <path> (fixture trees for the wedge tests /
    # planted-violation runs); remaining positional = single-package filter.
    args = list(argv[1:])
    packages_dir = PACKAGES_DIR
    if "--packages-dir" in args:
        i = args.index("--packages-dir")
        try:
            packages_dir = Path(args[i + 1])
        except IndexError:
            print("error: --packages-dir requires a path", file=sys.stderr)
            return 2
        del args[i:i + 2]
    filter_name = args[0] if args else None

    lfs_ch8 = load_lfs_ch8()
    malformed: list = []
    packages = load_all_packages(packages_dir, malformed)
    # Completeness contract: zero packages inventoried = nothing was
    # validated; exit 0 there would certify an empty scan (wrong
    # --packages-dir, empty tree). Malformed manifests already fail via the
    # MALFORMED count in the exit predicate.
    if not packages and not malformed:
        print(f"error: zero packages found under {packages_dir} — an empty "
              f"scan validates nothing (wrong --packages-dir?)", file=sys.stderr)
        return 2
    # Same completeness contract for the single-package filter: a filter
    # name matching NO package would skip every row and exit 0 — a vacuous
    # pass certifying nothing (typo'd name, retired package).
    if filter_name and filter_name not in packages:
        print(f"error: package filter '{filter_name}' matches no package "
              f"under {packages_dir} — a filtered scan that validates zero "
              f"rows certifies nothing (typo?)", file=sys.stderr)
        return 2
    natural = classify(packages, lfs_ch8)
    xtd, dangling = cross_tier_deps(packages, natural)
    l32 = lib32_audit(packages)

    print(f"# validate-package-tiers.py — Rule 1 + cross-tier-dep audit")
    print(f"# scanned {len(packages)} packages; LFS Ch 8 has {len(lfs_ch8)} entries")
    print(f"# tier ordering: {' → '.join(TIER_ORDER)}")
    print()
    print("package\tcurrent_tier\tverdict\tnotes")

    n_ok = n_move = n_unclear = n_xtd = n_pending = n_lib32 = n_dangling = 0
    n_scanned = 0
    rows = []
    for name in sorted(packages):
        if filter_name and name != filter_name:
            continue
        n_scanned += 1
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

        if name in dangling:
            verdict = ("DANGLING-DEP" if verdict == "OK"
                       else verdict + "+DANGLING-DEP")
            n_dangling += 1
            for dep, kind in dangling[name]:
                notes.append(f"{kind}-dep '{dep}' matches NO package in the tree")

        if name in l32:
            # Governance failures override UNCLEAR (the UNCLEAR is a symptom
            # of the named defect) and annotate everything else.
            is_mixed_only = all("mixed" in v for v in l32[name])
            new_verdict = "MIXED-UNGOVERNED" if is_mixed_only else "LIB32-GOVERNANCE"
            if verdict in ("OK", "UNCLEAR"):
                if verdict == "UNCLEAR":
                    n_unclear -= 1
                elif verdict == "OK" and not p.get("pending_acquisition"):
                    n_ok -= 1
                elif verdict == "OK":
                    n_pending -= 1
                verdict = new_verdict
            else:
                verdict = verdict + "+" + new_verdict
            n_lib32 += 1
            notes.extend(l32[name])

        rows.append((name, current, verdict, "; ".join(notes) if notes else ""))

    # Malformed manifests are halting violations, never silent skips
    # (re-certification finding G1-a): each is a named row + counts toward the exit code.
    for yml, reason in malformed:
        rows.append((str(yml), "?", "MALFORMED-MANIFEST", reason))

    for r in rows:
        if r[2] != "OK":  # only print non-OK rows for readability
            print("\t".join(r))

    print()
    print(f"# summary: OK={n_ok}  MOVE={n_move}  UNCLEAR={n_unclear}  "
          f"CROSS-TIER-DEP={n_xtd}  DANGLING-DEP={n_dangling}  "
          f"LIB32/MIXED={n_lib32}  "
          f"MALFORMED={len(malformed)}  PENDING={n_pending}")
    print(f"# total non-OK rows: {n_scanned - n_ok - n_pending}")

    return 0 if (n_move == 0 and n_unclear == 0 and n_xtd == 0
                 and n_dangling == 0 and n_lib32 == 0 and not malformed) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
