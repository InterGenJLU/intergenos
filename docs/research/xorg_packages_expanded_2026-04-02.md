# BLFS 13.0 Xorg Meta-Package Expansion

**Date:** 2026-04-02
**Source:** BLFS 13.0 systemd book (`docs/lfs-13.0/BLFS-BOOK-13.0-systemd.html`)
**Purpose:** Expand every BLFS Xorg meta-package into individual components for InterGenOS package definitions

---

## Table of Contents

1. [Foundation Packages (util-macros, xorgproto)](#1-foundation-packages)
2. [XCB Layer (xcb-proto, libxcb)](#2-xcb-layer)
3. [Pre-XCB Auth Libraries (libXau, libXdmcp)](#3-pre-xcb-auth-libraries)
4. [Xorg Libraries (xorg7-lib) -- 32 packages](#4-xorg-libraries-xorg7-lib)
5. [XCB Utilities (xcb-util + xcb-utilities) -- 6 packages](#5-xcb-utilities)
6. [Support Packages (xbitmaps, xcursor-themes)](#6-support-packages)
7. [Xorg Applications (xorg7-app) -- 33 packages](#7-xorg-applications-xorg7-app)
8. [Xorg Fonts (xorg7-font) -- 9 packages](#8-xorg-fonts-xorg7-font)
9. [Complete Build Order](#9-complete-build-order)
10. [Wayland/GNOME Relevance Notes](#10-waylandgnome-relevance-notes)

---

## 1. Foundation Packages

### util-macros-1.20.2

| Field | Value |
|---|---|
| Version | 1.20.2 |
| Source | https://www.x.org/pub/individual/util/util-macros-1.20.2.tar.xz |
| MD5 | 5f683a1966834b0a6ae07b3680bcb863 |
| Required deps | Xorg build environment (XORG_PREFIX set) |
| Build style | autotools |
| Build commands | `./configure $XORG_CONFIG && make install` |
| Special notes | No test suite. Data-only package (m4 macros). No libraries or programs installed. |

### xorgproto-2025.1

| Field | Value |
|---|---|
| Version | 2025.1 |
| Source | https://xorg.freedesktop.org/archive/individual/proto/xorgproto-2025.1.tar.xz |
| MD5 | 15534fa6fb13a6a70afe7561c1424f3c |
| Required deps | util-macros-1.20.2 |
| Build style | **meson** (not autotools) |
| Build commands | `mkdir build && cd build && meson setup --prefix=$XORG_PREFIX .. && ninja && ninja install && mv -v $XORG_PREFIX/share/doc/xorgproto{,-2025.1}` |
| Special notes | No test suite. Headers-only package. Optional: `-D legacy=true` for old programs like LessTif. |

---

## 2. XCB Layer

### xcb-proto-1.17.0

| Field | Value |
|---|---|
| Version | 1.17.0 |
| Source | https://xorg.freedesktop.org/archive/individual/proto/xcb-proto-1.17.0.tar.xz |
| MD5 | (not listed separately in extraction; use md5sum from download) |
| Required deps | Xorg build environment |
| Recommended deps | (none beyond env) |
| Optional deps | libxml2-2.15.1 (for tests) |
| Build style | autotools |
| Build commands | `PYTHON=python3 ./configure $XORG_CONFIG && make install && rm -f $XORG_PREFIX/lib/pkgconfig/xcb-proto.pc` |
| Special notes | The `rm` of the old pkgconfig file is needed if upgrading from xcb-proto <= 1.15.1. Data-only (XML protocol descriptions + Python codegen). |

### libxcb-1.17.0

| Field | Value |
|---|---|
| Version | 1.17.0 |
| Source | https://xorg.freedesktop.org/archive/individual/lib/libxcb-1.17.0.tar.xz |
| MD5 | (from download) |
| Required deps | libXau-1.0.12, xcb-proto-1.17.0 |
| Recommended deps | libXdmcp-1.1.5 (required for Mesa) |
| Optional deps | doxygen-1.16.1, libxslt-1.1.45 |
| Build style | autotools |
| Build commands | `./configure $XORG_CONFIG --without-doxygen --docdir='${datadir}'/doc/libxcb-1.17.0 && LC_ALL=en_US.UTF-8 make && make install` |
| Special notes | `LC_ALL=en_US.UTF-8` required during make. Produces 25 shared libraries: libxcb.so, libxcb-composite.so, libxcb-damage.so, libxcb-dbe.so, libxcb-dpms.so, libxcb-dri2.so, libxcb-dri3.so, libxcb-glx.so, libxcb-present.so, libxcb-randr.so, libxcb-record.so, libxcb-render.so, libxcb-res.so, libxcb-screensaver.so, libxcb-shape.so, libxcb-shm.so, libxcb-sync.so, libxcb-xf86dri.so, libxcb-xfixes.so, libxcb-xinerama.so, libxcb-xinput.so, libxcb-xkb.so, libxcb-xtest.so, libxcb-xvmc.so, libxcb-xv.so |

---

## 3. Pre-XCB Auth Libraries

These are standalone packages in BLFS (not part of any meta-package), but they are required dependencies of libxcb and must be built between xorgproto and libxcb.

### libXau-1.0.12

| Field | Value |
|---|---|
| Version | 1.0.12 |
| Source | https://www.x.org/pub/individual/lib/libXau-1.0.12.tar.xz |
| MD5 | 4c9f81acf00b62e5de56a912691bd737 |
| Required deps | xorgproto-2025.1 |
| Build style | autotools |
| Build commands | `./configure $XORG_CONFIG && make && make install` |
| Special notes | Has test suite (`make check`). X11 Authorization Protocol library. |

### libXdmcp-1.1.5

| Field | Value |
|---|---|
| Version | 1.1.5 |
| Source | https://www.x.org/pub/individual/lib/libXdmcp-1.1.5.tar.xz |
| MD5 | ce0af51de211e4c99a111e64ae1df290 |
| Required deps | xorgproto-2025.1 |
| Optional deps | xmlto-0.0.29, fop-2.11, libxslt-1.1.45 (for docs) |
| Build style | autotools |
| Build commands | `./configure $XORG_CONFIG --docdir='${datadir}'/doc/libXdmcp-1.1.5 && make && make install` |
| Special notes | Has test suite. X Display Manager Control Protocol library. Recommended for libxcb (required for Mesa). |

---

## 4. Xorg Libraries (xorg7-lib)

**Meta-package deps (shared):** Fontconfig-2.17.1, libxcb-1.17.0
**Optional (shared):** asciidoc-10.2.1, xmlto-0.0.29, fop-2.11, Links-2.30, Lynx-2.9.2
**Runtime recommended:** dbus-1.16.2

All 32 individual packages listed in BLFS build order. The BLFS book builds them in exactly the order shown in the md5 list. Source base URL: `https://www.x.org/pub/individual/lib/`

### Build Order Position 1: xtrans-1.6.0

| Field | Value |
|---|---|
| Version | 1.6.0 |
| Source | https://www.x.org/pub/individual/lib/xtrans-1.6.0.tar.xz |
| MD5 | 6ad67d4858814ac24e618b8072900664 |
| Build deps | xorgproto, libxcb (meta-package level deps) |
| Build style | autotools |
| Build commands | `./configure $XORG_CONFIG --docdir=$XORG_PREFIX/share/doc/xtrans-1.6.0 && make && make install` |
| Description | X Transport library (headers-only, network transport abstraction) |

### Build Order Position 2: libX11-1.8.13

| Field | Value |
|---|---|
| Version | 1.8.13 |
| Source | https://www.x.org/pub/individual/lib/libX11-1.8.13.tar.xz |
| MD5 | b617a053d2003cc81309f4e13d01379c |
| Build deps | xtrans, libxcb, xorgproto, fontconfig |
| Build style | autotools |
| Build commands | `./configure $XORG_CONFIG --docdir=$XORG_PREFIX/share/doc/libX11-1.8.13 && make && make install` |
| Description | Core X11 client library. Has working test suite. |

### Build Order Position 3: libXext-1.3.7

| Field | Value |
|---|---|
| Version | 1.3.7 |
| Source | https://www.x.org/pub/individual/lib/libXext-1.3.7.tar.xz |
| MD5 | ea8149187a26e9df6dbd94a60b3d8da0 |
| Build deps | libX11 |
| Build style | autotools |
| Build commands | standard `./configure $XORG_CONFIG` |
| Description | X11 miscellaneous extensions library (DPMS, MIT-SHM, Xv, etc.) |

### Build Order Position 4: libFS-1.0.10

| Field | Value |
|---|---|
| Version | 1.0.10 |
| Source | https://www.x.org/pub/individual/lib/libFS-1.0.10.tar.xz |
| MD5 | c5cc0942ed39c49b8fcd47a427bd4305 |
| Build deps | xtrans, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Font Service client library |

### Build Order Position 5: libICE-1.1.2

| Field | Value |
|---|---|
| Version | 1.1.2 |
| Source | https://www.x.org/pub/individual/lib/libICE-1.1.2.tar.xz |
| MD5 | d1ffde0a07709654b20bada3f9abdd16 |
| Build deps | xtrans, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | Inter-Client Exchange library (session management transport) |

### Build Order Position 6: libSM-1.2.6

| Field | Value |
|---|---|
| Version | 1.2.6 |
| Source | https://www.x.org/pub/individual/lib/libSM-1.2.6.tar.xz |
| MD5 | 3aeeea05091db1c69e6f768e0950a431 |
| Build deps | libICE |
| Build style | autotools |
| Build commands | standard |
| Description | X Session Management library |

### Build Order Position 7: libXScrnSaver-1.2.5

| Field | Value |
|---|---|
| Version | 1.2.5 |
| Source | https://www.x.org/pub/individual/lib/libXScrnSaver-1.2.5.tar.xz |
| MD5 | ec09c90a1cfd2c0630321d366a5e7203 |
| Build deps | libX11, libXext, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X11 Screen Saver extension library |

### Build Order Position 8: libXt-1.3.1

| Field | Value |
|---|---|
| Version | 1.3.1 |
| Source | https://www.x.org/pub/individual/lib/libXt-1.3.1.tar.xz |
| MD5 | 9acd189c68750b5028cf120e53c68009 |
| Build deps | libX11, libSM, libICE |
| Build style | autotools |
| Build commands | `./configure $XORG_CONFIG --docdir=$XORG_PREFIX/share/doc/libXt-1.3.1 --with-appdefaultdir=/etc/X11/app-defaults` |
| Description | X Toolkit Intrinsics library. Has working test suite. **Special configure flag.** |

### Build Order Position 9: libXmu-1.3.1

| Field | Value |
|---|---|
| Version | 1.3.1 |
| Source | https://www.x.org/pub/individual/lib/libXmu-1.3.1.tar.xz |
| MD5 | 1ef8065f0284e76c2238770365012ab2 |
| Build deps | libXt, libXext |
| Build style | autotools |
| Build commands | standard |
| Description | X Miscellaneous Utility library. Has working test suite. |

### Build Order Position 10: libXpm-3.5.18

| Field | Value |
|---|---|
| Version | 3.5.18 |
| Source | https://www.x.org/pub/individual/lib/libXpm-3.5.18.tar.xz |
| MD5 | d22b838e42ac0229ddf5a3afaf23910c |
| Build deps | libXt, libXext |
| Build style | autotools |
| Build commands | `./configure $XORG_CONFIG --docdir=$XORG_PREFIX/share/doc/libXpm-3.5.18 --disable-open-zfile` |
| Description | X Pixmap library. Has working test suite. **Special configure flag** (`--disable-open-zfile` allows building without `compress` command). |

### Build Order Position 11: libXaw-1.0.16

| Field | Value |
|---|---|
| Version | 1.0.16 |
| Source | https://www.x.org/pub/individual/lib/libXaw-1.0.16.tar.xz |
| MD5 | 2a9793533224f92ddad256492265dd82 |
| Build deps | libXmu, libXpm, libXt, libXext, libX11 |
| Build style | autotools |
| Build commands | standard |
| Description | X Athena Widgets library |

### Build Order Position 12: libXfixes-6.0.2

| Field | Value |
|---|---|
| Version | 6.0.2 |
| Source | https://www.x.org/pub/individual/lib/libXfixes-6.0.2.tar.xz |
| MD5 | baa39ada682dd524491a165bb0dfc708 |
| Build deps | libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Fixes extension library |

### Build Order Position 13: libXcomposite-0.4.7

| Field | Value |
|---|---|
| Version | 0.4.7 |
| Source | https://www.x.org/pub/individual/lib/libXcomposite-0.4.7.tar.xz |
| MD5 | 132816d5efccb883bbc2bf45eb905770 |
| Build deps | libX11, libXfixes, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Composite extension library |

### Build Order Position 14: libXrender-0.9.12

| Field | Value |
|---|---|
| Version | 0.9.12 |
| Source | https://www.x.org/pub/individual/lib/libXrender-0.9.12.tar.xz |
| MD5 | 4c54dce455d96e3bdee90823b0869f89 |
| Build deps | libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Render extension library |

### Build Order Position 15: libXcursor-1.2.3

| Field | Value |
|---|---|
| Version | 1.2.3 |
| Source | https://www.x.org/pub/individual/lib/libXcursor-1.2.3.tar.xz |
| MD5 | 5ce55e952ec2d84d9817169d5fdb7865 |
| Build deps | libXrender, libXfixes, libX11 |
| Build style | autotools |
| Build commands | standard |
| Description | X Cursor management library |

### Build Order Position 16: libXdamage-1.1.7

| Field | Value |
|---|---|
| Version | 1.1.7 |
| Source | https://www.x.org/pub/individual/lib/libXdamage-1.1.7.tar.xz |
| MD5 | 72bb73f2a07f81784ad69a39d7df1da2 |
| Build deps | libXfixes, libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Damage extension library |

### Build Order Position 17: libfontenc-1.1.9

| Field | Value |
|---|---|
| Version | 1.1.9 |
| Source | https://www.x.org/pub/individual/lib/libfontenc-1.1.9.tar.xz |
| MD5 | 3cba344d6b351cf308114865afa0d91e |
| Build deps | xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X font encoding library |

### Build Order Position 18: libXfont2-2.0.7

| Field | Value |
|---|---|
| Version | 2.0.7 |
| Source | https://www.x.org/pub/individual/lib/libXfont2-2.0.7.tar.xz |
| MD5 | 66e03e3405d923dfaf319d6f2b47e3da |
| Build deps | libfontenc, xorgproto, xtrans, freetype, fontconfig |
| Build style | autotools |
| Build commands | `./configure $XORG_CONFIG --docdir=$XORG_PREFIX/share/doc/libXfont2-2.0.7 --disable-devel-docs` |
| Description | X font library (version 2). **Special configure flag** (`--disable-devel-docs` avoids needing xmlto with a text browser). |

### Build Order Position 19: libXft-2.3.9

| Field | Value |
|---|---|
| Version | 2.3.9 |
| Source | https://www.x.org/pub/individual/lib/libXft-2.3.9.tar.xz |
| MD5 | d378be0fcbd1f689f9a132e0d642bc4b |
| Build deps | libXrender, libX11, fontconfig, freetype |
| Build style | autotools |
| Build commands | standard |
| Description | X FreeType library (client-side font rendering with Xrender) |

### Build Order Position 20: libXi-1.8.2

| Field | Value |
|---|---|
| Version | 1.8.2 |
| Source | https://www.x.org/pub/individual/lib/libXi-1.8.2.tar.xz |
| MD5 | 95a960c1692a83cc551979f7ffe28cf4 |
| Build deps | libXext, libXfixes, libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Input extension library |

### Build Order Position 21: libXinerama-1.1.6

| Field | Value |
|---|---|
| Version | 1.1.6 |
| Source | https://www.x.org/pub/individual/lib/libXinerama-1.1.6.tar.xz |
| MD5 | 5f3f5754a40730d1518233a60ba5c48e |
| Build deps | libXext, libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | Xinerama (multi-monitor) extension library |

### Build Order Position 22: libXrandr-1.5.5

| Field | Value |
|---|---|
| Version | 1.5.5 |
| Source | https://www.x.org/pub/individual/lib/libXrandr-1.5.5.tar.xz |
| MD5 | b550dfa388292a821aecdd52acecc94c |
| Build deps | libXext, libXrender, libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Resize and Rotate extension library |

### Build Order Position 23: libXres-1.2.3

| Field | Value |
|---|---|
| Version | 1.2.3 |
| Source | https://www.x.org/pub/individual/lib/libXres-1.2.3.tar.xz |
| MD5 | 5014282a08b54ec0edfa73c5cf9ae2c1 |
| Build deps | libXext, libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Resource usage extension library |

### Build Order Position 24: libXtst-1.2.5

| Field | Value |
|---|---|
| Version | 1.2.5 |
| Source | https://www.x.org/pub/individual/lib/libXtst-1.2.5.tar.xz |
| MD5 | b62dc44d8e63a67bb10230d54c44dcb7 |
| Build deps | libXext, libXi, libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Test extension library (synthetic input events) |

### Build Order Position 25: libXv-1.0.13

| Field | Value |
|---|---|
| Version | 1.0.13 |
| Source | https://www.x.org/pub/individual/lib/libXv-1.0.13.tar.xz |
| MD5 | 8a26503185afcb1bbd2c65e43f775a67 |
| Build deps | libXext, libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Video extension library |

### Build Order Position 26: libXvMC-1.0.15

| Field | Value |
|---|---|
| Version | 1.0.15 |
| Source | https://www.x.org/pub/individual/lib/libXvMC-1.0.15.tar.xz |
| MD5 | de4227c5722a8f5ca5748f3ef524aeee |
| Build deps | libXv, libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Video Motion Compensation library |

### Build Order Position 27: libXxf86dga-1.1.7

| Field | Value |
|---|---|
| Version | 1.1.7 |
| Source | https://www.x.org/pub/individual/lib/libXxf86dga-1.1.7.tar.xz |
| MD5 | 543164f1239fbe92cc0a9128d8da88e9 |
| Build deps | libXext, libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | XFree86 Direct Graphics Access extension library |

### Build Order Position 28: libXxf86vm-1.1.7

| Field | Value |
|---|---|
| Version | 1.1.7 |
| Source | https://www.x.org/pub/individual/lib/libXxf86vm-1.1.7.tar.xz |
| MD5 | bea9e3707fae6c3275769e771006fa0f |
| Build deps | libXext, libX11, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | XFree86 Video Mode extension library |

### Build Order Position 29: libpciaccess-0.18.1

| Field | Value |
|---|---|
| Version | 0.18.1 |
| Source | https://www.x.org/pub/individual/lib/libpciaccess-0.18.1.tar.xz |
| MD5 | 57c7efbeceedefde006123a77a7bc825 |
| Build deps | (minimal -- xorg env) |
| Build style | **meson** (not autotools) |
| Build commands | `meson setup --prefix=$XORG_PREFIX --buildtype=release build && ninja -C build && ninja -C build install` |
| Description | PCI access library. **Uses meson, not autotools.** |

### Build Order Position 30: libxkbfile-1.2.0

| Field | Value |
|---|---|
| Version | 1.2.0 |
| Source | https://www.x.org/pub/individual/lib/libxkbfile-1.2.0.tar.xz |
| MD5 | fa0faa5b6a8e726186c535d73712ccea |
| Build deps | libX11, xorgproto |
| Build style | **meson** (not autotools) |
| Build commands | `meson setup --prefix=$XORG_PREFIX --buildtype=release build && ninja -C build && ninja -C build install` |
| Description | XKB file handling library. **Uses meson, not autotools.** |

### Build Order Position 31: libxshmfence-1.3.3

| Field | Value |
|---|---|
| Version | 1.3.3 |
| Source | https://www.x.org/pub/individual/lib/libxshmfence-1.3.3.tar.xz |
| MD5 | 9805be7e18f858bed9938542ed2905dc |
| Build deps | xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | Shared memory fence library (used by DRI3). Has working test suite. |

### Build Order Position 32: libXpresent-1.0.2

| Field | Value |
|---|---|
| Version | 1.0.2 |
| Source | https://www.x.org/pub/individual/lib/libXpresent-1.0.2.tar.xz |
| MD5 | 53b72ce969745f8d3e41175d6549ce0b |
| Build deps | libX11, libXext, libXfixes, libXrandr, xorgproto |
| Build style | autotools |
| Build commands | standard |
| Description | X Present extension library |

---

## 5. XCB Utilities

### xcb-util-0.4.1 (standalone package, NOT part of XCB Utilities meta)

| Field | Value |
|---|---|
| Version | 0.4.1 |
| Source | https://xcb.freedesktop.org/dist/xcb-util-0.4.1.tar.xz |
| MD5 | 34d749eab0fd0ffd519ac64798d79847 |
| Required deps | libxcb-1.17.0 |
| Optional deps | doxygen-1.16.1 (for docs) |
| Build style | autotools |
| Build commands | `./configure $XORG_CONFIG && make && make install` |
| Description | Base XCB utility library |

### XCB Utilities meta-package (xcb-utilities) -- 5 packages

**Shared required deps:** libxcb-1.17.0, xcb-util-0.4.1
**Source base URL:** `https://xorg.freedesktop.org/archive/individual/lib/`

All use standard autotools: `./configure $XORG_CONFIG && make && make install`

#### xcb-util-image-0.4.1

| Field | Value |
|---|---|
| Version | 0.4.1 |
| Source | https://xorg.freedesktop.org/archive/individual/lib/xcb-util-image-0.4.1.tar.xz |
| MD5 | a67bfac2eff696170259ef1f5ce1b611 |
| Required deps | libxcb, xcb-util |
| Build style | autotools |
| Description | Port of Xlib's XImage and XShmImage functions to XCB |

#### xcb-util-keysyms-0.4.1

| Field | Value |
|---|---|
| Version | 0.4.1 |
| Source | https://xorg.freedesktop.org/archive/individual/lib/xcb-util-keysyms-0.4.1.tar.xz |
| MD5 | fbdc05f86f72f287ed71b162f1a9725a |
| Required deps | libxcb |
| Build style | autotools |
| Description | Standard X key constants and API for keycode conversion |

#### xcb-util-renderutil-0.3.10

| Field | Value |
|---|---|
| Version | 0.3.10 |
| Source | https://xorg.freedesktop.org/archive/individual/lib/xcb-util-renderutil-0.3.10.tar.xz |
| MD5 | 193b890e2a89a53c31e2ece3afcbd55f |
| Required deps | libxcb |
| Build style | autotools |
| Description | XCB Render extension convenience functions |

#### xcb-util-wm-0.4.2

| Field | Value |
|---|---|
| Version | 0.4.2 |
| Source | https://xorg.freedesktop.org/archive/individual/lib/xcb-util-wm-0.4.2.tar.xz |
| MD5 | 581b3a092e3c0c1b4de6416d90b969c3 |
| Required deps | libxcb |
| Build style | autotools |
| Description | EWMH and ICCCM helper libraries for window managers |

#### xcb-util-cursor-0.1.6

| Field | Value |
|---|---|
| Version | 0.1.6 |
| Source | https://xorg.freedesktop.org/archive/individual/lib/xcb-util-cursor-0.1.6.tar.xz |
| MD5 | e85bccd1993992be07232f8b80a814c8 |
| Required deps | libxcb, xcb-util-renderutil, xcb-util-image |
| Build style | autotools |
| Description | XCB cursor library (port of libXcursor) |

---

## 6. Support Packages

### xbitmaps-1.1.3

| Field | Value |
|---|---|
| Version | 1.1.3 |
| Source | https://www.x.org/pub/individual/data/xbitmaps-1.1.3.tar.xz |
| MD5 | 2b03f89d78fb91671370e77d7ad46907 |
| Required deps | util-macros-1.20.2 |
| Build style | autotools |
| Build commands | `./configure $XORG_CONFIG && make install` |
| Description | Bitmap images used by Xorg applications. Data-only. |

### xcursor-themes-1.0.7

| Field | Value |
|---|---|
| Version | 1.0.7 |
| Source | https://www.x.org/pub/individual/data/xcursor-themes-1.0.7.tar.xz |
| MD5 | 070993be1f010b09447ea24bab2c9846 |
| Required deps | Xorg Applications (needs xcursorgen from xorg7-app) |
| Build style | autotools |
| Build commands | `./configure --prefix=/usr && make && make install` |
| Description | Redglass and whiteglass animated cursor themes. **Note: uses --prefix=/usr explicitly** (not $XORG_PREFIX) so non-Xorg desktop environments can find them. |

---

## 7. Xorg Applications (xorg7-app)

**Meta-package required deps:** libpng-1.6.55, Mesa-25.3.5, xbitmaps-1.1.3, xcb-util-0.4.1
**Optional deps:** Linux-PAM-1.7.2

**Source base URL:** `https://www.x.org/pub/individual/app/`

All use standard autotools: `./configure $XORG_CONFIG && make && make install`

**Post-install note:** Remove broken `xkeystone` script: `rm -f $XORG_PREFIX/bin/xkeystone`

| # | Package | Version | MD5 | Description | Wayland/GNOME Relevance |
|---|---------|---------|-----|-------------|------------------------|
| 1 | iceauth | 1.0.10 | 30f898d71a7d8e817302970f1976198c | ICE authority file utility | NEEDED -- session management |
| 2 | mkfontscale | 1.2.3 | 7dcf5f702781bdd4aaff02e963a56270 | Create index of scalable font files | NEEDED -- font infrastructure |
| 3 | sessreg | 1.1.4 | b9efe1d21615c474b22439d41981beef | Manage utmp/wtmp entries for X sessions | X11-only -- display manager helper |
| 4 | setxkbmap | 1.3.4 | 1d61c9f4a3d1486eff575bf233e5776c | Set XKB keyboard map | USEFUL -- keyboard config (XWayland uses it) |
| 5 | smproxy | 1.0.8 | 6484cd8ee30354aaaf8f490988f5f6ef | Session Manager proxy | X11-only |
| 6 | xauth | 1.1.5 | 9cfdec89ad7bd86bcdfda150ae995955 | X authority file utility | NEEDED -- XWayland auth |
| 7 | xcmsdb | 1.0.7 | 37063ccf902fe3d55a90f387ed62fe1f | Device Color Characterization utility | X11-only |
| 8 | xcursorgen | 1.0.9 | f97e81b2c063f6ae9b18d4b4be7543f6 | Create X cursor files | NEEDED -- cursor theme building |
| 9 | xdpyinfo | 1.4.0 | 700556957773d378fa16a65a4406be0a | Display information utility | X11-only (diagnostic) |
| 10 | xdriinfo | 1.0.8 | 830a54ef3ba338013e06a1b5b012b4bd | Query DRI config | USEFUL -- GPU diagnostics |
| 11 | xev | 1.2.6 | f29d1544f8dd126a1b85e2f7f728672d | Print X events | X11-only (diagnostic) |
| 12 | xgamma | 1.0.8 | 687e42aa5afaec37f14da3072651c635 | Alter gamma correction | X11-only |
| 13 | xhost | 1.0.10 | 45c7e956941194e5f06a9c7307f5f971 | Access control for X server | X11-only |
| 14 | xinput | 1.6.4 | 8e4d14823b7cbefe1581c398c6ab0035 | Configure X input devices | USEFUL -- input device testing |
| 15 | xkbcomp | 1.5.0 | b8128ff6816897bd385ca437cd2886ee | Compile XKB keyboard description | NEEDED -- keyboard layout compilation |
| 16 | xkbevd | 1.1.6 | 543c0535367ca30e0b0dbcfa90fefdf9 | XKB event daemon | X11-only |
| 17 | xkbutils | 1.0.6 | 07483ddfe1d83c197df792650583ff20 | XKB utility programs | X11-only (diagnostic) |
| 18 | xkill | 1.0.7 | 294db9393a9d8e6613e1e3dd4fe0273f | Kill X client by window | X11-only |
| 19 | xlsatoms | 1.1.4 | da5b7a39702841281e1d86b7349a03ba | List X interned atoms | X11-only (diagnostic) |
| 20 | xlsclients | 1.1.5 | ab4b3c47e848ba8c3e47c021230ab23a | List X client applications | X11-only (diagnostic) |
| 21 | xmessage | 1.0.7 | ba2dd3db3361e374fefe2b1c797c46eb | Display message dialog | X11-only |
| 22 | xmodmap | 1.0.11 | 0d66e07595ea083871048c4b805d8b13 | Modify X keymaps | X11-only (use xkb instead) |
| 23 | xpr | 1.2.0 | ab6c9d17eb1940afcfb80a72319270ae | Print X window dump | X11-only |
| 24 | xprop | 1.2.8 | 5ef4784b406d11bed0fdf07cc6fba16c | Property displayer for X | USEFUL -- debugging XWayland apps |
| 25 | xrandr | 1.5.3 | dc7680201afe6de0966c76d304159bda | RandR command-line tool | USEFUL -- display config (XWayland) |
| 26 | xrdb | 1.2.2 | c8629d5a0bc878d10ac49e1b290bf453 | X resource database utility | NEEDED -- X resource management |
| 27 | xrefresh | 1.1.0 | 55003733ef417db8fafce588ca74d584 | Refresh X screen | X11-only |
| 28 | xset | 1.2.5 | 18ff5cdff59015722431d568a5c0bad2 | User preference utility for X | USEFUL -- font paths, screensaver |
| 29 | xsetroot | 1.1.3 | fa9a24fe5b1725c52a4566a62dd0a50d | Set root window params | X11-only |
| 30 | xvinfo | 1.1.5 | d698862e9cad153c5fefca6eee964685 | Print X video extension info | X11-only (diagnostic) |
| 31 | xwd | 1.0.9 | b0081fb92ae56510958024242ed1bc23 | X window dump | X11-only |
| 32 | xwininfo | 1.1.6 | c91201bc1eb5e7b38933be8d0f7f16a8 | Window information utility | USEFUL -- debugging |
| 33 | xwud | 1.0.7 | 3e741db39b58be4fef705e251947993d | X window undump (display xwd files) | X11-only |

---

## 8. Xorg Fonts (xorg7-font)

**Meta-package required deps:** xcursor-themes-1.0.7 (which requires Xorg Applications)
**Source base URL:** `https://www.x.org/pub/individual/font/`

All use standard autotools: `./configure $XORG_CONFIG && make && make install`

**Post-install note:** If XORG_PREFIX is not /usr, create symlinks:
```
install -v -d -m755 /usr/share/fonts
ln -svfn $XORG_PREFIX/share/fonts/X11/OTF /usr/share/fonts/X11-OTF
ln -svfn $XORG_PREFIX/share/fonts/X11/TTF /usr/share/fonts/X11-TTF
```

| # | Package | Version | MD5 | Description | Required? |
|---|---------|---------|-----|-------------|-----------|
| 1 | font-util | 1.4.1 | a6541d12ceba004c0c1e3df900324642 | Font metadata utilities (bdftruncate, ucs2any) | **Required** -- infrastructure for other font packages |
| 2 | encodings | 1.1.0 | a56b1a7f2c14173f71f010225fa131f1 | Font encoding files | **Required** -- needed by font packages |
| 3 | font-alias | 1.0.6 | dd1a744b97eb6d388d4e78b17011193e | Font aliases (fixed, cursor, etc.) | **Required** -- standard font name aliases |
| 4 | font-adobe-utopia-type1 | 1.0.5 | 546d17feab30d4e3abcf332b454f58ed | Adobe Utopia Type1 fonts | Optional -- legacy fonts |
| 5 | font-bh-ttf | 1.0.4 | 063bfa1456c8a68208bf96a33f472bb1 | Bigelow & Holmes TrueType fonts (Luxi family) | Recommended -- useful TrueType fonts |
| 6 | font-bh-type1 | 1.0.4 | 51a17c981275439b85e15430a3d711ee | Bigelow & Holmes Type1 fonts | Optional -- legacy Type1 |
| 7 | font-ibm-type1 | 1.0.4 | 00f64a84b6c9886040241e081347a853 | IBM Courier Type1 fonts | Optional -- legacy |
| 8 | font-misc-ethiopic | 1.0.5 | fe972eaf13176fa9aa7e74a12ecc801a | Ethiopic TrueType & OTF fonts | Optional -- internationalization |
| 9 | font-xfree86-type1 | 1.0.5 | 3b47fed2c032af3a32aad9acc1d25150 | XFree86 cursor and other Type1 fonts | **Required** -- cursor font (used by X server) |

---

## 9. Complete Build Order

This is the full dependency-respecting build order for all Xorg packages expanded from meta-packages. Each package must be built after all its dependencies.

```
Phase 1: Foundation
  1.  util-macros-1.20.2          (no X deps)
  2.  xorgproto-2025.1            (requires: util-macros)

Phase 2: XCB Infrastructure
  3.  libXau-1.0.12               (requires: xorgproto)
  4.  libXdmcp-1.1.5              (requires: xorgproto)
  5.  xcb-proto-1.17.0            (requires: xorg build env)
  6.  libxcb-1.17.0               (requires: libXau, xcb-proto; recommended: libXdmcp)

Phase 3: Xorg Libraries (xorg7-lib) -- in order
  7.  xtrans-1.6.0                (transport headers)
  8.  libX11-1.8.13               (requires: xtrans, libxcb, xorgproto, fontconfig)
  9.  libXext-1.3.7               (requires: libX11)
  10. libFS-1.0.10                (requires: xtrans, xorgproto)
  11. libICE-1.1.2                (requires: xtrans, xorgproto)
  12. libSM-1.2.6                 (requires: libICE)
  13. libXScrnSaver-1.2.5         (requires: libX11, libXext, xorgproto)
  14. libXt-1.3.1                 (requires: libX11, libSM, libICE)
  15. libXmu-1.3.1                (requires: libXt, libXext)
  16. libXpm-3.5.18               (requires: libXt, libXext)
  17. libXaw-1.0.16               (requires: libXmu, libXpm, libXt, libXext, libX11)
  18. libXfixes-6.0.2             (requires: libX11, xorgproto)
  19. libXcomposite-0.4.7         (requires: libX11, libXfixes, xorgproto)
  20. libXrender-0.9.12           (requires: libX11, xorgproto)
  21. libXcursor-1.2.3            (requires: libXrender, libXfixes, libX11)
  22. libXdamage-1.1.7            (requires: libXfixes, libX11, xorgproto)
  23. libfontenc-1.1.9            (requires: xorgproto)
  24. libXfont2-2.0.7             (requires: libfontenc, xorgproto, xtrans, fontconfig)
  25. libXft-2.3.9                (requires: libXrender, libX11, fontconfig)
  26. libXi-1.8.2                 (requires: libXext, libXfixes, libX11, xorgproto)
  27. libXinerama-1.1.6           (requires: libXext, libX11, xorgproto)
  28. libXrandr-1.5.5             (requires: libXext, libXrender, libX11, xorgproto)
  29. libXres-1.2.3               (requires: libXext, libX11, xorgproto)
  30. libXtst-1.2.5               (requires: libXext, libXi, libX11, xorgproto)
  31. libXv-1.0.13                (requires: libXext, libX11, xorgproto)
  32. libXvMC-1.0.15              (requires: libXv, libX11, xorgproto)
  33. libXxf86dga-1.1.7           (requires: libXext, libX11, xorgproto)
  34. libXxf86vm-1.1.7            (requires: libXext, libX11, xorgproto)
  35. libpciaccess-0.18.1         (minimal deps -- MESON build)
  36. libxkbfile-1.2.0            (requires: libX11, xorgproto -- MESON build)
  37. libxshmfence-1.3.3          (requires: xorgproto)
  38. libXpresent-1.0.2           (requires: libX11, libXext, libXfixes, libXrandr, xorgproto)

Phase 4: XCB Utilities
  39. xcb-util-0.4.1              (requires: libxcb)
  40. xcb-util-image-0.4.1        (requires: libxcb, xcb-util)
  41. xcb-util-keysyms-0.4.1      (requires: libxcb)
  42. xcb-util-renderutil-0.3.10  (requires: libxcb)
  43. xcb-util-wm-0.4.2           (requires: libxcb)
  44. xcb-util-cursor-0.1.6       (requires: libxcb, xcb-util-renderutil, xcb-util-image)

Phase 5: Support Data
  45. xbitmaps-1.1.3              (requires: util-macros)

Phase 6: Xorg Applications (xorg7-app)
  [requires: libpng, Mesa, xbitmaps, xcb-util, plus all xorg libs]
  46. iceauth-1.0.10
  47. mkfontscale-1.2.3
  48. sessreg-1.1.4
  49. setxkbmap-1.3.4
  50. smproxy-1.0.8
  51. xauth-1.1.5
  52. xcmsdb-1.0.7
  53. xcursorgen-1.0.9
  54. xdpyinfo-1.4.0
  55. xdriinfo-1.0.8
  56. xev-1.2.6
  57. xgamma-1.0.8
  58. xhost-1.0.10
  59. xinput-1.6.4
  60. xkbcomp-1.5.0
  61. xkbevd-1.1.6
  62. xkbutils-1.0.6
  63. xkill-1.0.7
  64. xlsatoms-1.1.4
  65. xlsclients-1.1.5
  66. xmessage-1.0.7
  67. xmodmap-1.0.11
  68. xpr-1.2.0
  69. xprop-1.2.8
  70. xrandr-1.5.3
  71. xrdb-1.2.2
  72. xrefresh-1.1.0
  73. xset-1.2.5
  74. xsetroot-1.1.3
  75. xvinfo-1.1.5
  76. xwd-1.0.9
  77. xwininfo-1.1.6
  78. xwud-1.0.7

Phase 7: Cursor Themes & Fonts
  79. xcursor-themes-1.0.7        (requires: Xorg Applications -- needs xcursorgen)
  80. font-util-1.4.1
  81. encodings-1.1.0
  82. font-alias-1.0.6
  83. font-adobe-utopia-type1-1.0.5
  84. font-bh-ttf-1.0.4
  85. font-bh-type1-1.0.4
  86. font-ibm-type1-1.0.4
  87. font-misc-ethiopic-1.0.5
  88. font-xfree86-type1-1.0.5
```

**Total: 88 individual packages** (expanded from 6 meta-package groups + standalone packages)

---

## 10. Wayland/GNOME Relevance Notes

For a **Wayland-primary GNOME desktop**, many of these packages are still required because:

1. **XWayland** depends on most Xorg libraries (libX11, libXext, libXfixes, libXcomposite, libXrender, libXcursor, libXdamage, libXi, libXrandr, libXtst, libxshmfence, libxkbfile, etc.)
2. **GTK** and **GDK** link against multiple X11 libraries even when running under Wayland
3. **Mesa** requires libxcb, libXau, libXdmcp, libxshmfence, libpciaccess, and many xorg libs
4. **Many GNOME components** still need X11 libraries for XWayland compatibility

### Packages safe to SKIP for Wayland-only (if we ever wanted to):

From xorg7-lib:
- libFS (X Font Service -- unused by modern apps)
- libXScrnSaver (X11 screensaver extension)
- libXaw (Athena Widgets -- legacy toolkit)
- libXvMC (X Video Motion Compensation -- legacy)
- libXxf86dga (XFree86 DGA -- legacy)
- libXxf86vm (XFree86 Video Mode -- legacy)

From xorg7-app:
- sessreg, smproxy, xcmsdb, xdpyinfo, xev, xgamma, xhost, xkbevd, xkbutils, xkill, xlsatoms, xlsclients, xmessage, xmodmap, xpr, xrefresh, xsetroot, xvinfo, xwd, xwud

### Packages REQUIRED even on Wayland/GNOME:

From xorg7-lib: **All core libs** (libX11, libXext, libXfixes, libXcomposite, libXrender, libXcursor, libXdamage, libXi, libXinerama, libXrandr, libXres, libXtst, libXv, libpciaccess, libxkbfile, libxshmfence, libXpresent, libXft, libXmu, libXpm, libXt, libICE, libSM, xtrans, libfontenc, libXfont2)

From xorg7-app: iceauth, mkfontscale, xauth, xcursorgen, xkbcomp, xrdb, setxkbmap

From xorg7-font: font-util, encodings, font-alias, font-xfree86-type1 (minimum)

**Recommendation:** Build ALL of them. The X11-only tools are small, and skipping them creates fragile conditional logic in the build system for minimal disk savings. Many are useful for debugging XWayland issues.

---

## Special Build Notes Summary

1. **Meson builds (not autotools):** xorgproto, libpciaccess, libxkbfile
2. **Special configure flags:**
   - libXt: `--with-appdefaultdir=/etc/X11/app-defaults`
   - libXpm: `--disable-open-zfile`
   - libXfont2: `--disable-devel-docs`
   - libxcb: `--without-doxygen` and `LC_ALL=en_US.UTF-8` during make
   - xcb-proto: `PYTHON=python3` prefix to configure
   - xcursor-themes: `--prefix=/usr` (NOT $XORG_PREFIX)
3. **Working test suites:** libX11, libXt, libXmu, libXpm, libxshmfence, libXau, libXdmcp, libxcb
4. **Post-install cleanup:** Remove `$XORG_PREFIX/bin/xkeystone` after xorg7-app
5. **Post-install config:** Font symlinks if XORG_PREFIX != /usr
