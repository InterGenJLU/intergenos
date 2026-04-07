# GNOME Desktop on Wayland — Complete Dependency Chain from LFS 13.0

**Source:** BLFS 13.0 Systemd Book  
**Date:** 2026-04-01  
**Goal:** Complete build order from base InterGenOS (LFS 13.0 + 19 core + 20 base packages) to working GNOME 49 on Wayland

---

## Already Installed (LFS 13.0 Core + InterGenOS Base)

These packages are **already built** and will NOT appear in the build order:

**LFS 13.0 (82 packages):** glibc, gcc, binutils, systemd, dbus, python, perl, openssl, meson, ninja, pkg-config, util-linux, pcre2, zlib, xz, zstd, bzip2, gzip, tar, findutils, gawk, grep, sed, coreutils, bash, readline, ncurses, libffi, expat, gdbm, gettext, texinfo, groff, man-db, iproute2, iana-etc, shadow (basic), procps-ng, e2fsprogs, kmod, libelf, linux, man-pages, autoconf, automake, libtool, flex, bison, m4, bc, file, gmp, mpc, mpfr, diffutils, patch, make, less, vim/nano, wheel, flit-core, setuptools, pip, jinja2, markupsafe, meson, ninja, etc.

**InterGenOS Core (19 packages):** libtasn1, libunistring, libuv, libarchive, nghttp2, nspr, linux-pam, glib2 (with gobject-introspection), libidn2, p11-kit, sudo, libssh2, nss, make-ca, libpsl, curl, wget, cmake, git

**InterGenOS Base (20 packages):** cpio, ed, fcron, htop, iotop, libtirpc, pax, perl-file-fcntllock, popt, screen, strace, time, which, libnsl, lsof, rsync, atop, exim, at, btop

---

## Complete Build Order — 6 Tiers

### TIER 1: Foundation Libraries (no desktop deps)

These have zero or minimal dependencies. Many are leaf nodes used by everything above.

| # | Package | Version (BLFS 13.0) | Required Deps | Notes |
|---|---------|---------------------|---------------|-------|
| 1 | libgpg-error | 1.59 | none | GPG error codes |
| 2 | libgcrypt | 1.12.0 | libgpg-error | Crypto library |
| 3 | nettle | 3.10.2 | none | Crypto library |
| 4 | libogg | 1.3.6 | none | Audio container |
| 5 | libvorbis | 1.3.7 | libogg | Audio codec |
| 6 | flac | 1.5.0 | none | Audio codec |
| 7 | opus | 1.6.1 | none | Audio codec |
| 8 | libsndfile | 1.2.2 | none | REC: flac, opus |
| 9 | libsamplerate | 0.2.2 | none | Audio resampling |
| 10 | speex | 1.2.1 | libogg | Audio codec |
| 11 | lame | 3.100 | none | MP3 encoder |
| 12 | mpg123 | 1.33.4 | none | REC: alsa-lib (build later) |
| 13 | nasm | 3.01 | none | Assembler |
| 14 | libpng | 1.6.55 | none | Image library |
| 15 | libjpeg-turbo | 3.1.3 | cmake | REC: nasm |
| 16 | libtiff | 4.7.1 | none | REC: cmake |
| 17 | giflib | 5.2.2 | none | Image library |
| 18 | libwebp | 1.6.0 | none | REC: libjpeg, libpng |
| 19 | pixman | 0.46.4 | none | Pixel manipulation |
| 20 | lcms2 | 2.18 | none | Color management |
| 21 | fribidi | 1.0.16 | none | Bidi text |
| 22 | graphite2 | 1.3.14 | cmake | Font rendering |
| 23 | icu | 78.2 | none | Unicode/i18n |
| 24 | libxml2 | 2.15.1 | none | REC: icu |
| 25 | libxslt | 1.1.45 | libxml2 | XSLT processor |
| 26 | json-c | 0.18 | cmake | JSON library |
| 27 | inih | 62 | none | INI file parser |
| 28 | brotli | 1.2.0 | cmake | Compression |
| 29 | highway | 1.3.0 | cmake | SIMD library |
| 30 | fmt | 12.1.0 | cmake | Formatting library |
| 31 | fast_float | 8.2.3 | cmake | Float parsing |
| 32 | utfcpp | 4.0.9 | cmake | UTF-8 handling |
| 33 | simdutf | 8.0.0 | cmake | SIMD UTF |
| 34 | libyaml | 0.2.5 | none | YAML parser |
| 35 | libfyaml | 0.9.4 | none | REC: libyaml |
| 36 | iso-codes | 4.20.1 | none | Country/language codes |
| 37 | libndp | 1.9 | none | Neighbor Discovery Protocol |
| 38 | libmnl | 1.0.5 | none | Netlink library |
| 39 | libnl | 3.12.0 | none | Netlink library |
| 40 | keyutils | 1.6.3 | none | Kernel key management |
| 41 | libusb | 1.0.29 | none | USB library |
| 42 | libseccomp | 2.6.0 | none | Seccomp library |
| 43 | libunwind | 1.8.3 | none | Stack unwinding |
| 44 | mtdev | 1.1.7 | none | Multitouch device |
| 45 | libevdev | 1.13.6 | none | Input device handling |
| 46 | hwdata | 0.404 | none | Hardware identification |
| 47 | lzo | 2.10 | none | Compression |
| 48 | boost | 1.90.0 | none | C++ libraries |
| 49 | libaio | 0.3.113 | none | Async I/O |
| 50 | duktape | 2.7.0 | none | JS engine |
| 51 | cracklib | 2.10.3 | none | Password checking |
| 52 | libexif | 0.6.25 | none | EXIF metadata |
| 53 | soundtouch | 2.4.0 | none | Audio processing |
| 54 | fdk-aac | 2.0.3 | none | AAC codec |
| 55 | sbc | 2.2 | none | Bluetooth audio codec |
| 56 | dav1d | 1.5.3 | none | REC: nasm |
| 57 | libaom | 3.13.1 | none | REC: nasm |
| 58 | libvpx | 1.16.0 | none | REC: nasm |
| 59 | x264 | 20250815 | none | REC: nasm |
| 60 | x265 | 4.1 | cmake | REC: nasm |
| 61 | svt-av1 | 4.0.1 | cmake | REC: nasm |
| 62 | libassuan | 3.0.2 | libgpg-error | GPG IPC |
| 63 | libksba | 1.6.7 | libgpg-error | X.509/CMS |
| 64 | npth | 1.8 | none | Threading |
| 65 | gnutls | 3.8.12 | nettle | REC: make-ca, libtasn1, p11-kit |
| 66 | slang | 2.3.3 | none | Screen library |
| 67 | gpm | 1.20.7 | none | Mouse support |
| 68 | rpcsvc-proto | 1.4.4 | none | RPC protocol |
| 69 | libevent | 2.1.12 | none | Event library |
| 70 | c-ares | 1.34.6 | cmake | DNS library |
| 71 | lua | 5.4.8 | none | Scripting language |
| 72 | libpcap | 1.10.6 | none | Packet capture |
| 73 | libatasmart | 0.19 | none | ATA S.M.A.R.T. |
| 74 | fuse3 | 3.18.1 | none | Filesystem in userspace |
| 75 | hicolor-icon-theme | 0.18 | none | Base icon theme |
| 76 | sound-theme-freedesktop | 0.8 | none | Default sounds |
| 77 | cdparanoia | 10.2 | none | CD ripper |
| 78 | openjpeg2 | 2.5.4 | cmake | JPEG 2000 |

### Python Dependencies (Tier 1b - many are pure Python, build fast)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 79 | editables | 0.5 | none | Python module |
| 80 | pluggy | 1.6.0 | none | Python module |
| 81 | hatchling | 1.28.0 | editables, pluggy | Python build backend |
| 82 | setuptools_scm | 9.2.2 | none | Python module |
| 83 | hatch-fancy-pypi-readme | 25.1.0 | hatchling | Python module |
| 84 | hatch-vcs | 0.5.0 | hatchling, setuptools_scm | Python module |
| 85 | attrs | 25.4.0 | hatch-fancy-pypi-readme, hatch-vcs | Python module |
| 86 | pygments | 2.19.2 | hatchling | Python syntax highlighter |
| 87 | smartypants | 2.0.2 | none | Python typography |
| 88 | typogrify | 2.1.0 | hatchling, smartypants | Python typography |
| 89 | markdown | 3.10.2 | none | Python Markdown |
| 90 | docutils | 0.22.4 | none | Python doc utilities |
| 91 | Mako | 1.3.10 | none | Python templates (for Mesa) |
| 92 | PyYAML | 6.0.3 | none | Python YAML |
| 93 | cython | 3.2.4 | none | Python/C compiler |
| 94 | lxml | 6.0.2 | libxslt | Python XML |

### TIER 1c: DocBook/XML Infrastructure

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 95 | sgml-common | 0.6.3 | none | SGML infrastructure |
| 96 | docbook-xml-4.5 (DocBook) | 4.5 | libarchive, libxml2 | XML DTD |
| 97 | docbook-xsl-nons | 1.79.2 | none | REC: libxml2 |
| 98 | itstool | 2.0.7 | DocBook, lxml | Translation tool |
| 99 | xmlto | 0.0.29 | DocBook, libxslt | XML conversion |

### TIER 1d: Rust Toolchain

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 100 | rust | 1.93.1 | none | Rust compiler (large) |
| 101 | cbindgen | 0.29.2 | rust | C bindings generator |
| 102 | cargo-c | 0.10.20 | rust | REC: libssh2 |
| 103 | rust-bindgen | 0.72.1 | rust | Rust bindings generator |

### TIER 1e: LLVM (needed for Mesa, SpiderMonkey)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 104 | llvm | 21.1.8 | none | Compiler infrastructure |

### TIER 1f: Security/Auth Stack

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 105 | pinentry | 1.3.2 | libassuan, libgpg-error | GPG PIN entry |
| 106 | openldap | 2.6.12 | none | REC: cyrus-sasl |
| 107 | gnupg2 | 2.5.17 | libassuan, libksba, openldap | REC: gnutls, pinentry |
| 108 | gpgme | 2.0.1 | libassuan | REC: gnupg2 |
| 109 | mitkrb (MIT Kerberos) | 1.22.2 | none | Authentication |
| 110 | libpwquality | 1.4.5 | cracklib | REC: linux-pam |
| 111 | shadow (rebuild) | 4.19.3 | linux-pam | Rebuild with PAM |

---

### TIER 2: Graphics Stack (X11, Wayland, Mesa)

This is the core graphics infrastructure.

#### X11 Foundation

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 112 | util-macros | 1.20.2 | (xorg-env) | Xorg build macros |
| 113 | xorgproto | 2025.1 | util-macros | X11 protocol headers |
| 114 | libXau | 1.0.12 | xorgproto | X authority |
| 115 | libXdmcp | 1.1.5 | xorgproto | X display manager |
| 116 | xcb-proto | 1.17.0 | none | XCB protocol |
| 117 | libxcb | 1.17.0 | libXau | REC: libXdmcp |
| 118 | xorg7-lib (Xorg Libraries) | (multiple) | fontconfig, libxcb | Includes: libICE, libSM, libX11, libXext, libXfixes, libXrender, libXrandr, libXi, libXcomposite, libXdamage, libXft, libXinerama, libxkbfile, libXmu, libXpm, libXt, libXtst, libXv, libXxf86vm |
| 119 | xkeyboard-config (XKeyboardConfig) | 2.46 | xorg7-lib | Keyboard layouts |

#### Wayland

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 120 | wayland | 1.24.0 | libxml2 | Wayland protocol |
| 121 | wayland-protocols | 1.47 | wayland | Wayland protocol extensions |

#### Input Stack

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 122 | libinput | 1.31.0 | libevdev, mtdev | Input device handling |
| 123 | libwacom | 2.18.0 | libevdev, libgudev | REC: libxml2; Wacom tablets |

#### Vulkan/SPIR-V

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 124 | spirv-headers | 1.4.341.0 | cmake | SPIR-V headers |
| 125 | spirv-tools | 1.4.341.0 | cmake | SPIR-V tools |
| 126 | spirv-llvm-translator | 21.1.4 | libxml2, spirv-tools | SPIR-V/LLVM bridge |
| 127 | libclc | 21.1.8 | spirv-llvm-translator | OpenCL C library |
| 128 | vulkan-headers | 1.4.341.0 | cmake | Vulkan API headers |
| 129 | vulkan-loader | 1.4.341.0 | cmake, xorg7-lib | REC: wayland |
| 130 | glslang | 16.2.0 | cmake | GLSL compiler |
| 131 | glslc (from shaderc) | 2026.1 | cmake, spirv-tools | Shader compiler |

#### Mesa

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 132 | libdrm | 2.4.131 | none | REC: xorg7-lib |
| 133 | mesa | 25.3.5 | xorg7-lib, libdrm, Mako, PyYAML | REC: glslang, libva, llvm, wayland-protocols, libclc, vulkan-loader, cbindgen, rust-bindgen |

#### Video Acceleration

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 134 | libva | 2.23.0 | libdrm | REC: mesa (circular — build without EGL/GLX first, rebuild after mesa) |

#### Display Helpers

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 135 | libdisplay-info | 0.3.0 | hwdata | Display EDID info |
| 136 | libxcvt | 0.1.3 | (xorg-env) | Video mode timings |
| 137 | libxkbcommon | 1.13.1 | xkeyboard-config | REC: libxcb, wayland-protocols |
| 138 | libepoxy | 1.5.10 | mesa | OpenGL dispatch |

#### XCB Utilities

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 139 | xcb-util | 0.4.1 | libxcb | XCB utility library |
| 140 | xcb-utilities (xcb-util-image, etc.) | (multiple) | libxcb | xcb-util-image, keysyms, render-util, wm, cursor |
| 141 | startup-notification | 0.12 | xorg7-lib, xcb-util | Startup feedback |

#### Xorg Apps, Fonts, Xwayland

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 142 | xbitmaps | 1.1.3 | util-macros | X bitmap files |
| 143 | xorg7-app (Xorg Applications) | (multiple) | libpng, xbitmaps, xcb-util | iceauth, luit, mkfontscale, sessreg, setxkbmap, smproxy, x11perf, xauth, xbacklight, xcmsdb, xdpyinfo, xdriinfo, xev, xgamma, xhost, xinput, xkbcomp, xkbevd, xlsatoms, xlsclients, xmessage, xmodmap, xpr, xprop, xrandr, xrdb, xrefresh, xset, xsetroot, xvinfo, xwd, xwininfo, xwud |
| 144 | xorg7-font (Xorg Fonts) | (multiple) | xcursor-themes | encodings, font-alias, font-util, misc fonts |
| 145 | xcursor-themes | 1.0.7 | xorg7-app | Cursor themes |
| 146 | xwayland | 24.1.9 | libxcvt, wayland-protocols, xorg7-app, xorg7-font | REC: libepoxy, mesa |

---

### TIER 3: UI Toolkit Prerequisites (fonts, images, Cairo, Pango)

#### Font Rendering (circular: freetype <-> harfbuzz)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 147 | freetype2 (pass 1) | 2.14.1 | none | Build first WITHOUT harfbuzz |
| 148 | harfbuzz | 12.3.2 | none | REC: glib2, graphite2, icu, freetype2 |
| 149 | freetype2 (pass 2) | 2.14.1 | none | Rebuild WITH harfbuzz, libpng |
| 150 | fontconfig | 2.17.1 | freetype2 | Font configuration |

#### GLib Ecosystem (glib2 already installed, but GObject Introspection helpers needed)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 151 | pycairo | 1.29.0 | cairo | Python Cairo bindings (needed by PyGObject) |
| 152 | json-glib | 1.10.8 | glib2 | JSON for GLib |
| 153 | shared-mime-info | 2.4 | glib2 | MIME type database |
| 154 | desktop-file-utils | 0.28 | glib2 | .desktop file tools |
| 155 | libgudev | 238 | glib2 | GObject udev wrapper |
| 156 | libxmlb | 0.3.25 | glib2 | XML binary format |

#### Cairo and Pango

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 157 | cairo | 1.18.4 | libpng | REC: fontconfig, glib2, xorg7-lib |
| 158 | pango | 1.57.0 | fontconfig, freetype2, harfbuzz, glib2 | REC: cairo, xorg7-lib |

#### PyGObject

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 159 | pygobject3 | 3.54.5 | glib2 | REC: pycairo |

#### Image Libraries (higher level)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 160 | libjxl | 0.11.2 | brotli, giflib, highway, libjpeg, libpng | JPEG XL |
| 161 | libavif | 1.3.0 | dav1d | REC: libaom |
| 162 | libheif | 1.21.2 | none | REC: libaom, x265 |
| 163 | exiv2 | 0.28.7 | cmake | REC: brotli, curl, inih |
| 164 | woff2 | 1.0.2 | brotli | Web fonts |

---

### TIER 4: GTK and Desktop Prerequisites

#### GDK-Pixbuf and Image Loading

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 165 | glycin | 2.0.8 | bubblewrap, fontconfig, glib2, lcms2, libseccomp, rust | Image decoding framework |
| 166 | bubblewrap | 0.11.0 | none | Sandboxing |
| 167 | gdk-pixbuf | 2.44.5 | glib2, shared-mime-info | REC: docutils, glycin |
| 168 | librsvg | 2.61.4 | cairo, pango | REC: gdk-pixbuf, glib2, vala |

#### Vala Compiler

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 169 | vala | 0.56.18 | glib2 | Vala language compiler |

#### DConf and GSettings

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 170 | gsettings-desktop-schemas | 49.1 | glib2 | Desktop settings schemas |
| 171 | libhandy1 | 1.8.3 | gtk3 | REC: vala; adaptive widgets |
| 172 | dconf | 0.49.0 | dbus, glib2, libhandy1, libxml2 | REC: libxslt |

#### AT-SPI2 (Accessibility)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 173 | at-spi2-core | 2.58.3 | dbus, glib2, xorg7-lib | Accessibility toolkit |

#### Graphene

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 174 | graphene | 1.10.8 | glib2 | Graphics math |

#### GTK3

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 175 | gtk3 | 3.24.51 | at-spi2-core, gdk-pixbuf, libepoxy, pango | REC: adwaita-icon-theme, docbook-xsl, iso-codes, libxkbcommon, libxslt, wayland, wayland-protocols, glib2 |

#### Adwaita Icons and GNOME Themes (after GTK3)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 176 | adwaita-icon-theme | 49.0 | gtk3, gtk4 | Build after gtk4 too (see below) |

#### GTK4

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 177 | gtk4 | 4.20.3 | gdk-pixbuf, graphene, iso-codes, libepoxy, librsvg, pango, pygobject3, wayland-protocols | REC: adwaita-icon-theme, glslc, gst-plugins-bad, gst-plugins-good, libvpx |

**Note:** gtk4 and adwaita-icon-theme have a circular dep. Build gtk4 first without the icon theme, install adwaita-icon-theme, then optionally rebuild gtk4. Or build the icon theme from git without gtk4 first.

#### Notification Infrastructure

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 178 | notification-daemon | 3.20.0 | gtk3 | Notification display |
| 179 | libnotify | 0.8.8 | gdk-pixbuf | REC: gtk4 |

#### libadwaita

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 180 | appstream | 1.1.2 | curl, libfyaml, libxmlb, libxslt | REC: docbook-xsl |
| 181 | sassc | 3.6.2 | none | SASS compiler |
| 182 | libadwaita1 | 1.8.4 | appstream, sassc | REC: vala |

#### Portal Infrastructure

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 183 | libportal | 0.9.1 | glib2 | REC: gtk3 |
| 184 | xdg-dbus-proxy | 0.1.6 | glib2 | D-Bus proxy |
| 185 | xdg-user-dirs | 0.19 | none | XDG user directories |

---

### TIER 5: Desktop Services

#### Audio Stack

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 186 | alsa-lib | 1.2.15.3 | none | ALSA library |
| 187 | alsa-plugins | 1.2.12 | alsa-lib | ALSA plugins |
| 188 | alsa-utils | 1.2.15.2 | alsa-lib | ALSA utilities |
| 189 | pulseaudio | 17.0 | libsndfile | REC: alsa-lib, dbus, glib2, speex |
| 190 | pipewire | 1.6.0 | none | REC: bluez, gst-plugins-base, pulseaudio, wireplumber |
| 191 | wireplumber | 0.5.13 | glib2, systemd, linux-pam | REC: lua |

#### GStreamer

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 192 | gstreamer | 1.28.1 | glib2 | Multimedia framework |
| 193 | gst-plugins-base | 1.28.1 | gstreamer | REC: alsa-lib, cdparanoia, glib2, iso-codes, libgudev, libjpeg, libogg, libpng, mesa, pango, xorg7-lib |
| 194 | gst-plugins-good | 1.28.1 | gst-plugins-base | REC: cairo, flac, lame, libsoup3, mpg123, pulseaudio |
| 195 | gst-plugins-bad | 1.28.1 | gst-plugins-base | REC: libaom, libdvdread, libdvdnav, svt-av1, soundtouch |

#### FFmpeg

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 196 | ffmpeg | 8.0.1 | none | REC: dav1d, libaom, fdk-aac, lame, libvpx, svt-av1, x264, nasm, alsa-lib, libva |

#### Polkit

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 197 | polkit | 127 | duktape, systemd | REC: libxslt, linux-pam |

#### Sound Theme and libcanberra

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 198 | libcanberra | 0.30 | libvorbis | REC: alsa-lib, gstreamer, gtk3, sound-theme-freedesktop |
| 199 | gsound | 1.0.3 | libcanberra | REC: glib2, vala |

#### Bluetooth

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 200 | libical | 3.0.20 | cmake | REC: glib2, libxml2, vala |
| 201 | bluez | 5.86 | dbus, glib2, libical | Bluetooth stack |

#### GLib Networking and libsoup3

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 202 | glib-networking | 2.80.1 | glib2 | REC: gsettings-desktop-schemas, make-ca |
| 203 | libsoup3 | 3.6.6 | glib-networking, libpsl, nghttp2 | REC: glib2, vala |

#### System Services

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 204 | libgusb | 0.4.9 | json-glib, libusb | REC: glib2, hwdata, vala |
| 205 | colord | 1.4.8 | dbus, glib2, lcms2, libgusb | REC: systemd, vala |
| 206 | colord-gtk | 0.3.1 | colord | REC: glib2, gtk4, vala |
| 207 | libmbim | 1.34.0 | none | REC: glib2 |
| 208 | libqmi | 1.38.0 | glib2, libgudev | REC: libmbim |
| 209 | modemmanager | 1.24.2 | libgudev | REC: glib2, libmbim, polkit, vala |
| 210 | accountsservice | 23.13.9 | polkit | REC: glib2, systemd, vala |
| 211 | upower | 1.91.1 | libgudev | Power management |
| 212 | power-profiles-daemon | 0.30 | polkit, upower | Power profiles |

#### Networking

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 213 | iptables | 1.8.12 | none | Firewall rules |
| 214 | newt | 0.52.25 | popt, slang | REC: gpm |
| 215 | wpa_supplicant | 2.11 | none | REC: libnl |
| 216 | networkmanager | 1.56.0 | libndp | REC: curl, glib2, iptables, newt, nss, polkit, pygobject3, systemd, vala, wpa_supplicant |
| 217 | geoclue2 | 2.8.0 | json-glib, libsoup3 | REC: libnotify, vala |

#### UDisks2

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 218 | libbytesize | 2.12 | pygments | Byte size library |
| 219 | lvm2 | 2.03.38 | libaio | Logical volume manager |
| 220 | libblockdev | 3.4.0 | glib2 | REC: cryptsetup, keyutils, libatasmart, libbytesize, libnvme, lvm2 |
| 221 | cryptsetup | 2.8.4 | json-c, lvm2, popt | Disk encryption |
| 222 | udisks2 | 2.11.1 | libatasmart, libblockdev, libgudev, polkit | REC: systemd |

#### IBus (Input Method)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 223 | ibus | 1.5.33 | iso-codes, libarchive, vala | REC: dconf, glib2, gtk3, gtk4 |

#### Printing (Cups — circular dep requires staging)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 224 | cups (pass 1) | 2.4.16 | gnutls | Build first without cups-filters |
| 225 | poppler | 26.02.0 | cmake, glib2 | REC: boost, libjpeg, libpng, nss, openjpeg2 |
| 226 | qpdf | 12.3.2 | libjpeg | PDF tools |
| 227 | mupdf | 1.26.12 | xorg7-lib | REC: harfbuzz, libjpeg, openjpeg2, curl. Lighter alternative to ghostscript for libcupsfilters. |
| 228 | libcupsfilters | 2.1.1 | cups, glib2, mupdf, lcms2, poppler, qpdf | Needs ghostscript OR mupdf — use mupdf |
| 229 | libppd | 2.1.1 | libcupsfilters | PPD handling |
| 230 | cups-filters | 2.0.1 | libcupsfilters, libppd | CUPS filters |
| 231 | cups (pass 2, rebuild) | 2.4.16 | gnutls | Rebuild with cups-filters |

**Note:** The cups/cups-filters circular dep requires a two-pass build. Build cups first (without filters), then build the filter chain, then rebuild cups.

#### Samba (needed by gnome-control-center)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 232 | lynx | 2.9.2 | none | REC: brotli. Text browser (needed by xdg-utils) |
| 233 | xdg-utils | 1.2.1 | xmlto, lynx, xorg7-app | Desktop integration utils |
| 234 | perl-parse-yapp | (perl module) | none | Perl parser |
| 235 | samba | 4.23.5 | gnutls, perl-parse-yapp, rpcsvc-proto | REC: fuse3, gpgme, libtasn1, linux-pam, mitkrb, openldap |

#### SpiderMonkey and GJS

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 233 | spidermonkey | from firefox-140.8.0 | cbindgen | REC: llvm |
| 234 | gjs | 1.86.0 | cairo, dbus, spidermonkey | REC: gtk3, gtk4 |

#### XDG Desktop Portals

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 235 | xdg-desktop-portal-gtk | 1.15.3 | gtk3 | REC: gnome-desktop |
| 236 | xdg-desktop-portal | 1.20.3 | fuse3, json-glib, pipewire, dbus | Needs portal backend |

---

### TIER 6: GNOME Core

#### GNOME Libraries

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 237 | libsecret | 0.21.7 | glib2 | REC: libgcrypt, gnutls, vala |
| 238 | gcr (3.x) | 3.41.2 | glib2, libgcrypt, p11-kit | REC: gnupg2, gtk3, libxslt, vala |
| 239 | gcr4 | 4.4.0.1 | glib2, libgcrypt, p11-kit | REC: gnupg2, gtk4, libxslt, vala |
| 240 | librest | 0.10.2 | json-glib, libsoup3, make-ca | REC: glib2 |
| 241 | totem-pl-parser | 3.26.6 | none | REC: glib2, libarchive, libgcrypt |
| 242 | yelp-xsl | 49.0 | libxslt | Help stylesheets |
| 243 | geocode-glib | 3.26.4 | json-glib, libsoup3 | REC: glib2 |
| 244 | gnome-online-accounts | 3.56.4 | gcr4, libadwaita1, librest, vala | REC: glib2 |
| 245 | gnome-desktop | 44.5 | gsettings-desktop-schemas, iso-codes, libseccomp, xkeyboard-config | REC: gtk3, gtk4, glib2 |
| 246 | gnome-menus | 3.38.1 | glib2 | Menu specification |
| 247 | gnome-autoar | 0.4.5 | libarchive, gtk3 | REC: vala |
| 248 | libgee | 0.20.8 | glib2, vala | GObject collection |
| 249 | libgtop | 2.41.3 | glib2, xorg7-lib | System monitoring |
| 250 | libgweather | 4.4.4 | geocode-glib, libsoup3 | REC: glib2, libxml2, vala |
| 251 | libpeas | 1.36.0 | glib2, gtk3 | REC: libxml2 |
| 252 | libshumate | 1.5.3 | gtk4, libsoup3, protobuf-c | REC: glib2 |
| 253 | protobuf | 33.5 | abseil-cpp, cmake | Protocol buffers |
| 254 | abseil-cpp | 20260107.1 | cmake | C++ common libraries |
| 255 | protobuf-c | 1.5.2 | protobuf | C protocol buffers |
| 256 | vte | 0.82.3 | libxml2 | REC: fast_float, fmt, icu, gnutls, gtk3, gtk4, simdutf |
| 257 | evolution-data-server | 3.58.3 | libical, nss | REC: gnome-online-accounts, glib2, gtk3, gtk4, libgweather, vala, webkitgtk |
| 258 | tinysparql | 3.10.1 | json-glib, vala | REC: glib2, icu, libsoup3, pygobject3 |
| 259 | localsearch | 3.10.2 | gexiv2, tinysparql | REC: exempi, ffmpeg, icu, libexif, libseccomp, libwebp, poppler, upower |
| 260 | gexiv2 | 0.14.6 | exiv2 | REC: vala |
| 261 | exempi | 2.6.6 | boost | XMP metadata |

#### WebKitGTK (large build)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 262 | unifdef | 2.12 | none | Preprocessor tool |
| 263 | enchant | 2.8.15 | aspell, vala | Spell checking |
| 264 | aspell | 0.60.8.2 | which | Spell checking |
| 265 | ruby | 4.0.1 | none | Ruby language |
| 266 | webkitgtk | 2.50.5 | cairo, gst-plugins-base, gst-plugins-bad, gtk3, gtk4, libgudev, libsoup3, libwebp, openjpeg2, ruby, unifdef, which | REC: bubblewrap, enchant, geoclue2, glib2, libavif, libseccomp, xdg-dbus-proxy |

#### GNOME Desktop Components

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 267 | xdg-desktop-portal-gnome | 49.0 | gnome-desktop, gtk4, libadwaita1, xdg-desktop-portal, xdg-desktop-portal-gtk | GNOME portal backend |
| 268 | gnome-backgrounds | 49.0 | libjxl | Desktop wallpapers |
| 269 | gvfs | 1.58.2 | dbus, glib2, libusb, libsecret | REC: gtk3, libgudev, systemd, udisks2 |
| 270 | nautilus | 49.3 | gexiv2, gnome-desktop, libadwaita1, libportal, tinysparql | REC: desktop-file-utils, glib2, gst-plugins-base, libcloudproviders, localsearch |
| 271 | libcloudproviders | 0.3.6 | glib2, vala | Cloud storage integration |
| 272 | gnome-bluetooth | 47.1 | gsound, gtk4, upower | REC: glib2, libadwaita1 |
| 273 | gnome-keyring | 48.0 | dbus, gcr | REC: linux-pam, openssh |
| 274 | gnome-settings-daemon | 49.1 | alsa-lib, fontconfig, gcr4, geoclue2, gnome-desktop, libcanberra, libgweather, libnotify, pulseaudio, upower | REC: colord, cups, modemmanager, nss, wayland |
| 275 | tecla | 49.0 | libadwaita1, libxkbcommon | On-screen keyboard |
| 276 | libnma | 1.10.6 | gcr, gtk3, networkmanager | REC: gtk4, vala |
| 277 | gnome-control-center | 49.4 | accountsservice, blueprint-compiler, colord-gtk, cups, gnome-bluetooth, gnome-online-accounts, gnome-settings-daemon, gsound, libadwaita1, libgtop, libnma, libpwquality, mitkrb, modemmanager, samba, shared-mime-info, tecla, udisks2 | REC: ibus |
| 278 | gnome-themes-extra | 3.28 | gtk3 | Legacy themes |

#### libei (Emulated Input)

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 279 | libei | 1.5.0 | attrs | Emulated input protocol |

#### Mutter and GNOME Shell

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 280 | mutter | 49.4 | at-spi2-core, docutils, graphene, libei, libxcvt, pipewire | REC: desktop-file-utils, glib2, libdisplay-info, startup-notification |
| 281 | gnome-shell | 49.4 | evolution-data-server, gcr4, gjs, gnome-desktop, ibus, mutter, polkit, startup-notification | BUILD-TIME required only. Runtime deps (adwaita-icon-theme, dconf, gdm, gnome-control-center, libgweather) must be installed before running. REC: desktop-file-utils, gnome-autoar, gnome-bluetooth, gst-plugins-base, networkmanager, power-profiles-daemon, gnome-menus |

#### GDM and Session

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 282 | gnome-session | 49.2 | gnome-desktop, json-glib, mesa, systemd, upower | Session manager |
| 283 | gdm | 49.2 | accountsservice, dconf, libcanberra, gtk3, linux-pam | Display manager. gnome-session and gnome-shell are runtime deps. |

**Build order for GNOME core:** mutter -> gnome-shell -> gnome-session -> gdm. The BLFS book lists gnome-control-center and gdm as runtime required for gnome-shell, not build-time required. Build gnome-shell first, then the rest can follow.

#### GNOME Shell Extensions and Final Bits

| # | Package | Version | Required Deps | Notes |
|---|---------|---------|---------------|-------|
| 284 | gnome-shell-extensions | 49.0 | libgtop | Shell extensions |
| 285 | gi-docgen | 2026.1 | markdown, pygments, typogrify | API documentation |
| 286 | blueprint-compiler | 0.18.0 | pygobject3 | UI compiler |
| 287 | gnome-tweaks | 49.0 | gtk4, libadwaita1, libgudev, pygobject3, sound-theme-freedesktop | GNOME Tweaks |
| 288 | yelp | 49.0 | gsettings-desktop-schemas, libadwaita1, webkitgtk, yelp-xsl | Help viewer |
| 289 | gnome-user-docs | 49.4 | itstool | User documentation |
| 290 | gnome-backgrounds | 49.0 | libjxl | Wallpapers |

---

## Summary Statistics

- **Total new packages to build: ~290** (including Python modules and rebuilds)
- **Tier 1 (Foundation):** ~104 packages
- **Tier 2 (Graphics):** ~35 packages
- **Tier 3 (UI prerequisites):** ~18 packages
- **Tier 4 (GTK/Desktop):** ~23 packages
- **Tier 5 (Services):** ~50 packages
- **Tier 6 (GNOME Core):** ~55 packages

## Circular Dependencies (requires staged builds)

1. **FreeType <-> HarfBuzz:** Build FreeType first (no harfbuzz), then HarfBuzz, then rebuild FreeType
2. **CUPS <-> cups-filters:** Build CUPS first (no filters), then filter chain, then rebuild CUPS
3. **Mesa <-> libva:** Build libva first (no EGL/GLX), then Mesa, then rebuild libva
4. **gtk4 <-> adwaita-icon-theme:** Build gtk4 first, then icons (or break the cycle)
5. **gnome-shell <-> gdm:** Build gnome-shell first, then gdm (runtime dep only)

## Notable Large Builds

- **LLVM:** ~1.5 hours (parallelism=4)
- **Rust:** ~25 SBU
- **Mesa:** ~3 SBU
- **WebKitGTK:** ~40-80 SBU (very large)
- **SpiderMonkey:** ~10 SBU
- **GCC (if rebuilding):** ~14 SBU

## Critical Path

The longest dependency chain to gnome-shell is roughly:
```
libgpg-error -> libgcrypt -> gnutls -> glib-networking -> libsoup3 ->
geocode-glib -> libgweather -> gnome-settings-daemon -> gnome-control-center ->
gnome-shell
```

And in parallel:
```
rust -> cbindgen -> spidermonkey -> gjs -> gnome-shell
llvm -> mesa -> libepoxy -> gtk3/gtk4 -> mutter -> gnome-shell
```

## Packages NOT Needed (commonly confused)

- **Xorg-Server:** NOT needed for pure Wayland GNOME. Xwayland provides X11 compat.
- **PulseAudio:** Needed as build dep even with PipeWire (PipeWire provides PulseAudio compat layer)
- **Qt:** NOT needed for GNOME
- **Node.js:** NOT needed for GNOME
