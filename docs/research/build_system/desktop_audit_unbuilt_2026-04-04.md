# Unbuilt Desktop Package Audit — 166 Packages vs BLFS 13.0
**Date:** 2026-04-04

---

## CRITICAL ISSUES (will cause build failures)

| # | Package | Issue |
|---|---------|-------|
| 1 | **gnome-keyring** | Wrong build system — uses autotools but BLFS 13.0 uses meson; also wrong dep (gcr4 vs gcr3) |
| 2 | **gjs** | Missing required dep: spidermonkey — will not build |
| 3 | **librsvg** | Broken line continuation in build.sh — meson command is syntactically invalid |
| 4 | **yelp-xsl** | build.sh uses autotools but package is meson in BLFS 13.0 |
| 5 | **mupdf** | Broken symlink version parsing — creates wrong .so names |
| 6 | **protobuf** | Wrong cmake variable: `BUILD_SHARED_LIBS` vs correct `protobuf_BUILD_SHARED_LIBS` |
| 7 | **libjxl** | Typo `-DDJPEGXL_ENABLE_BENCHMARK=OFF` (double D); missing `-DBUILD_SHARED_LIBS=ON`; missing required deps highway, lcms2 |
| 8 | **libnma** | Wrong dep (gcr4 vs gcr3); wrong meson option (`-Dgtk4=true` vs `-Dlibnma_gtk4=true`) |
| 9 | **spirv-tools** | Missing `-DBUILD_SHARED_LIBS=ON` — will build static-only |
| 10 | **spirv-llvm-translator** | Missing `-DBUILD_SHARED_LIBS=ON` — will build static-only; missing libxml2 dep |
| 11 | **nautilus** | Broken sed path — `../meson.build` wrong, should be `meson.build` |
| 12 | **tinysparql** | Broken sed path — `../docs/reference/meson.build` wrong |
| 13 | **vte** | Broken sed path — `../doc/meson.build` wrong |
| 14 | **pango** | Broken sed path — same pattern as nautilus/tinysparql/vte |
| 15 | **samba** | Missing mitkrb dep (required by `--with-system-mitkrb5` flag); missing PYTHON env var |
| 16 | **freetype2-pass1** | Version mismatch — 2.13.3 should be 2.14.1 to match pass2 |

## HIGH PRIORITY (missing required deps)

| Package | Missing Required Deps |
|---------|----------------------|
| adwaita-icon-theme | librsvg |
| at-spi2-core | missing `-Dgtk2_atk_adaptor=false` flag |
| bluez | missing post-install for /etc/bluetooth/main.conf |
| colord | libgudev |
| colord-gtk | gtk3; missing `-Dgtk4=true -Dvapi=true` flags |
| evolution-data-server | libsecret |
| gnome-control-center | blueprint-compiler, cups, libgtop, libnma, mitkrb, ModemManager, samba, shared-mime-info (8 missing!) |
| gnome-desktop | wrong flag (`-Dgtk_doc=false` should be `-Ddesktop_docs=false`) |
| gnome-online-accounts | json-glib, librest, vala; wrong flag name |
| gnome-session | mesa; missing post_install (rm gnome.desktop) |
| gnome-settings-daemon | geocode-glib, libwacom |
| gnome-shell | evolution-data-server, ibus |
| gnome-shell-extensions | wrong dep (gnome-shell should be libgtop) |
| gnome-terminal | gnome-shell, itstool, libhandy1 (also: libhandy1 template doesn't exist!) |
| gnome-tweaks | gsettings-desktop-schemas, libgudev, sound-theme-freedesktop |
| gnome-user-docs | libxml2 |
| gvfs | gcr4; missing meson disables for unavailable deps |
| libsoup3 | libpsl, libxml2, nghttp2 |
| libshumate | protobuf-c |
| mutter | docutils, gnome-settings-daemon, gtk4 |
| gcr4 | p11-kit |
| nautilus | gexiv2, gnome-autoar, libseccomp, tinysparql |
| power-profiles-daemon | pygobject3 |
| xwayland | pixman |

## MISSING CONFIGURE FLAGS

| Package | Missing Flags |
|---------|--------------|
| exiv2 | `-DEXIV2_ENABLE_VIDEO=yes -DEXIV2_ENABLE_WEBREADY=yes -DEXIV2_ENABLE_CURL=yes -DEXIV2_BUILD_SAMPLES=no -DCMAKE_SKIP_INSTALL_RPATH=ON` |
| gst-plugins-bad | `-Dgpl=enabled` |
| gst-plugins-base | `--wrap-mode=nodownload` |
| geoclue2 | `-Dnmea-source=false` |
| geocode-glib | `-Dsoup2=false` |
| graphite2 | Missing CMake 4.0 compatibility seds |
| json-c | `-DBUILD_STATIC_LIBS=OFF` |
| libical | Wrong `-DBUILD_SHARED_LIBS=ON` should be `-DSHARED_ONLY=yes`; missing `-j1` |
| libppd | `--with-cups-rundir=/run/cups --enable-ppdc-utils` |
| libportal | `-Dvapi=false` |
| librest | `-Dexamples=false` |
| libshumate | `--wrap-mode=nodownload` |
| localsearch | `-Dman=false -Dfunctional_tests=false` |
| modemmanager | `-Dbash_completion=false -Dqrtr=false` |
| mutter | `-Dbash_completion=false` |
| openjpeg2 | Wrong `-DBUILD_SHARED_LIBS=ON` should be `-DBUILD_STATIC_LIBS=OFF` |
| pango | `--wrap-mode=nofallback -Dintrospection=enabled` |
| poppler | `-G Ninja -DENABLE_QT5=OFF` |
| protobuf-c | `make -j1` (no parallel build) |
| vulkan-loader | `-DCMAKE_SKIP_INSTALL_RPATH=ON` |
| webkitgtk | `-DCMAKE_SKIP_INSTALL_RPATH=ON` |
| webkitgtk-gtk3 | `-DCMAKE_SKIP_INSTALL_RPATH=ON` |
| woff2 | `-DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_SKIP_INSTALL_RPATH=ON` |

## XORG $XORG_CONFIG FLAGS NEEDED

All unbuilt Xorg autotools libraries and apps missing `--sysconfdir=/etc --localstatedir=/var`:

libX11, libXaw, libXcomposite, libXcursor, libXdamage, libXext, libXfixes, libXfont2, libXft, libXi, libXinerama, libXmu, libXpm, libXpresent, xcursorgen, xdpyinfo, xdriinfo, xev, xhost

## DUPLICATE CMAKE FLAGS (cosmetic but sloppy)

exiv2, glslang, libjxl, libical, openjpeg2, poppler, protobuf — all have doubled PREFIX/BUILD_TYPE

## MISSING RECOMMENDED DEPS (lower priority but important for functionality)

| Package | Missing Recommended Deps |
|---------|-------------------------|
| evolution-data-server | icu, libcanberra |
| exiv2 | curl |
| ffmpeg | openssl (used by build flag but not in deps) |
| gcr4 | gnupg2, libxslt |
| gst-plugins-bad | libaom, libva, svt-av1 |
| gst-plugins-base | cdparanoia, iso-codes, libgudev, libjpeg-turbo, libpng, mesa, wayland-protocols |
| gst-plugins-good | gdk-pixbuf, libsoup3, libvpx, mpg123, nasm |
| gvfs | libgudev, libsoup3 |
| libcupsfilters | libexif, libjpeg-turbo, libpng, libtiff |
| libgweather | libxml2, vala |
| libpeas | libxml2 |
| libxkbcommon | wayland (essential for GNOME Wayland!) |
| networkmanager | nss, curl |
| xdg-desktop-portal | bubblewrap (BLFS warns: "large security issue" without it) |
| xwayland | libtirpc |

## MISSING PACKAGE TEMPLATES

| Package | Needed By | Notes |
|---------|-----------|-------|
| **libhandy1** | gnome-terminal | GTK3 adaptive widget library — no template exists |
| **gcr** (3.x) | gnome-keyring, libnma | GTK3 version of gcr — only gcr4 exists |
| **highway** | libjxl | SIMD library — no template exists |

## PACKAGES THAT PASS CLEAN (no issues)

cairo, cargo-c, cbindgen, cryptsetup, dconf, doxygen, encodings, fontconfig, font-cursor-misc, font-misc-misc, gexiv2, gsound, gtk3, harfbuzz, libadwaita1, libass, libblockdev, libepoxy, libinput, libnotify, libnvme, libva, libxkbfile, libXrandr, libXrender, libXScrnSaver, libXt, libXtst, libXv, libXvMC, libXxf86dga, libXxf86vm, llvm, pycairo, pygobject3, rust, rust-bindgen, setxkbmap, smproxy, spirv-headers, startup-notification, tecla, udisks2, vulkan-headers, xcb-util-cursor, xcb-util-image, xcb-util-keysyms, xcb-util-renderutil

**~49 of 166 packages pass clean.**

## VERSION MATCHES

All 166 packages match BLFS 13.0 versions except:
- **freetype2-pass1**: 2.13.3 (should be 2.14.1)
