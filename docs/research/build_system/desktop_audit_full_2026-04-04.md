# Full Desktop Package Audit — 163 Built Packages vs BLFS 13.0
**Date:** 2026-04-04
**Audited by:** 7 parallel agents, consolidated manually

---

## CRITICAL ISSUES (will break builds, produce wrong binaries, or cause runtime failures)

### Build-Breaking / Wrong Output

| # | Package | Issue | File |
|---|---------|-------|------|
| 1 | **icu** | Source URL points to 76.1 but version says 78.2 — wrong tarball downloaded | package.yml |
| 2 | **x265** | Missing `-D GIT_ARCHETYPE=1` — without it, no shared lib or pkg-config generated unless git present | build.sh |
| 3 | **vala** | Uses `make` instead of `make bootstrap` per BLFS — compiler not rebuilt properly | build.sh |
| 4 | **speex** | Missing speexdsp tarball — BLFS builds both speex AND speexdsp. `libspeexdsp.so` needed by GStreamer | package.yml |
| 5 | **slang** | Does NOT support parallel build per BLFS, but autotools builder runs parallel make. Also missing `RPATH=` | package.yml |
| 6 | **npth** | build.sh has sed commands from NSPR (wrong package) — will fail or silently do nothing | build.sh |
| 7 | **newt** | Missing BLFS sed to disable static lib — cannot apply since build_style is autotools | package.yml/build.sh |
| 8 | **shared-mime-info** | Missing `-D update-mimedb=true` meson flag — MIME database won't be populated | package.yml |
| 9 | **trove-classifiers** | Missing required sed to hardcode version string — wheel gets wrong version | build.sh |
| 10 | **lynx** | Wrong configure flag: `--enable-locale-strstrcase` should be `--enable-locale-charset` | package.yml + build.sh |
| 11 | **lame** | Missing BLFS sed for hardcoded library path — autotools can't apply it (dead build.sh) | build.sh |

### lib64 Issue (meson packages installing to /usr/lib64 instead of /usr/lib)

These packages used meson without `--libdir=/usr/lib`. On LFS (which patches GCC to use /usr/lib), meson may still default to lib64, causing libraries to install to the wrong path. Plugin/module discovery will fail for packages that look in compiled-in paths.

**Library packages (HIGH impact — wrong install path for .so files):**

| Package | build_style | Has Libraries? |
|---------|------------|---------------|
| libdrm | custom | YES — libdrm.so, libdrm_amdgpu.so, etc. |
| libei | custom | YES — libei.so, libeis.so |
| libcloudproviders | meson | YES — libcloudproviders.so |
| libdisplay-info | meson | YES — libdisplay-info.so |
| libevdev | meson | YES — libevdev.so |
| libsecret | custom | YES — libsecret-1.so |
| libwacom | custom | YES — libwacom.so |
| libxmlb | custom | YES — libxmlb.so |
| libxcvt | meson | YES — libxcvt.so |
| gdk-pixbuf | custom | YES — libgdk_pixbuf-2.0.so |
| graphene | custom | YES — libgraphene-1.0.so |
| gstreamer | custom | YES — libgstreamer-1.0.so |
| json-glib | custom | YES — libjson-glib-1.0.so |
| pixman | meson | YES — libpixman-1.so |
| pipewire | custom | YES — libpipewire-0.3.so |
| polkit | custom | YES — libpolkit-*.so |
| pulseaudio | custom | YES — libpulse.so |
| wayland | meson | YES — libwayland-*.so |
| wireplumber | custom | YES (if meson in build.sh) |

**Non-library / data packages (LOW impact):**

| Package | Notes |
|---------|-------|
| bubblewrap | Binary only, no libs |
| dav1d | Has libs but also has custom install |
| desktop-file-utils | Binary only |
| fribidi | Has libs |
| gnome-backgrounds | Data only |
| hicolor-icon-theme | Data only |
| inih | Has libs |
| iso-codes | Data only |
| shared-mime-info | Data only |
| sound-theme-freedesktop | Data only |
| totem-pl-parser | Has libs |
| xdg-dbus-proxy | Binary only |

---

## IMPORTANT ISSUES (missing deps, wrong flags, functional divergence)

### Missing Required/Recommended Dependencies

| Package | Missing Dep | BLFS Classification |
|---------|------------|-------------------|
| gnupg2 | OpenLDAP | Required |
| libavif | SVT-AV1 | Required |
| gnutls | make-ca, libunistring, libtasn1, p11-kit | Recommended (critical for TLS) |
| cups | colord, dbus, libusb, Linux-PAM, xdg-utils | Recommended |
| enchant | glib2, vala | Required |
| hatch-vcs | setuptools_scm | Required |
| libsndfile | libvorbis | Recommended |
| libpwquality | linux-pam | Recommended |
| mpg123 | alsa-lib | Recommended |
| alsa-lib | alsa-ucm-conf | Recommended |
| docbook-xml | libarchive | Required |
| libgtop | glib2, Xorg Libraries | Required |
| libcloudproviders | glib2 | Required |
| libmbim | glib2 | Required |
| libqmi | glib2 | Required |
| gstreamer | glib2 | Required |
| gdk-pixbuf | glib2 | Required |
| libsecret | glib2 | Required |
| pluggy | setuptools_scm | Recommended |
| totem-pl-parser | libarchive, libgcrypt | Recommended |
| wireplumber | glib2 | Required |
| libpng | APNG patch (for Firefox) | Recommended |

### Missing/Wrong Configure Flags

| Package | Issue |
|---------|-------|
| alsa-plugins | Missing `--sysconfdir=/etc` |
| alsa-utils | Missing `--disable-bat`, `--disable-xmlto`, `--with-curses=ncursesw` |
| abseil-cpp | Missing `-DCMAKE_SKIP_INSTALL_RPATH=ON` |
| cdparanoia | Missing `--mandir=/usr/share/man`, parallel build not supported |
| enchant | Missing `--sysconfdir=/etc` |
| libgpg-error | Missing `--sysconfdir=/etc` |
| libjpeg-turbo | Missing `-DENABLE_STATIC=FALSE`, `-DCMAKE_SKIP_INSTALL_RPATH=ON` |
| libmbim | Wrong flag: `-Dgtk_doc=false` should be `-Dbash_completion=false` per BLFS |
| libwebp | Missing `--enable-libwebpextras`, `--enable-swap-16bit-csp` |
| libavif | Missing cmake flags for AOM and SVT codecs |
| libdrm | Missing `-Dvalgrind=disabled` |
| nettle | Missing post-install `chmod 755` on shared libs |
| opus | Build style should be meson per BLFS, not autotools |
| qpdf | Missing `-D BUILD_STATIC_LIBS=OFF` |
| svt-av1 | Missing `-DCMAKE_SKIP_INSTALL_RPATH=ON`, `-DBUILD_SHARED_LIBS=ON` |
| gstreamer | Missing `-Dgst_debug=false` |
| fuse3 | Missing post-install `chmod u+s /usr/bin/fusermount3` |
| gsettings-desktop-schemas | Missing post_install: `glib-compile-schemas` |
| soundtouch | BLFS uses autotools, we use cmake |

### Missing Xorg $XORG_CONFIG Flags (--sysconfdir=/etc --localstatedir=/var)

These Xorg packages use `build_style: autotools` and are missing the standard `--sysconfdir=/etc --localstatedir=/var` from `$XORG_CONFIG`. Impact varies — libraries generally don't install config files, but it's a systemic inconsistency.

Affected: sessreg, libSM, libXau, libxcb, libXdmcp, libxshmfence, libfontenc, libFS, font-alias, font-util, iceauth, util-macros, xbitmaps, xcb-proto, xcb-util, xcb-util-wm, xtrans

---

## DEAD CODE (build.sh functions never executed due to build_style)

### build_style: autotools (build.sh configure/build/do_install all dead)
alsa-lib, alsa-plugins, alsa-utils, enchant, fdk-aac, font-alias, font-util, gnome-menus, gnupg2, gnutls, gpgme, hwdata, iceauth, iptables, lame, lcms2, libassuan, libksba, libmnl, libnl, libogg, libpcap, libseccomp, libSM, libusb, libvorbis, libwebp, libXau, libxcb, libXdmcp, libxshmfence, libyaml, lynx, mpg123, mtdev, nasm, nettle, newt, rpcsvc-proto, sbc, sessreg, slang, sound-theme-freedesktop, speex, util-macros, x264, xbitmaps, xcb-proto, xcb-util, xcb-util-wm, xtrans
**Total: 51 packages**

### build_style: meson (build.sh configure/build dead, do_install may be live via install_func)
bubblewrap, c-ares (cmake), brotli (cmake), dav1d, desktop-file-utils, fribidi, gnome-backgrounds, hicolor-icon-theme, inih, iso-codes, libcloudproviders, libdisplay-info, libevdev, libpciaccess, libxcvt, pixman, qpdf (cmake), shared-mime-info, soundtouch (cmake), totem-pl-parser, wayland, xdg-dbus-proxy
**Total: 22 packages**

### build_style: custom but configure_flags in package.yml are dead (custom ignores them)
cdparanoia, cracklib, libsndfile, libxslt, npth, pipewire, polkit, ruby, pinentry

**Grand total dead code: 73 packages with some form of dead code (out of 163)**

---

## STALE VERSION COMMENTS IN build.sh

52 packages have build.sh header comments with version numbers that don't match package.yml. This is cosmetic but indicates templates weren't fully updated during the version audit.

---

## VERSION MISMATCHES WITH BLFS 13.0

| Package | Our Version | BLFS Version | Notes |
|---------|------------|-------------|-------|
| **icu** | 78.2 (yml) / 76.1 (URL) | 78.2 | Source URL is WRONG |

All other 162 packages match BLFS 13.0 versions.

---

## PACKAGES WITH NO ISSUES FOUND

These packages passed all checks — correct version, matching build instructions, no dead code, no lib64 exposure:

attrs, aspell, bash-completion (not in BLFS), boost, cython, docbook-xsl-nons, docutils, duktape, editables, font-dejavu, font-noto, help2man (not in BLFS), keyutils, libaio, libaom, libatasmart, libbytesize, libevent, libfyaml, libyaml, lvm2, lxml, Mako, markdown, mitkrb, pathspec, perl-parse-yapp, pygments, sassc, setuptools-scm, sgml-common, unifdef, wayland-protocols, wpa_supplicant, xmlto

**Total clean: ~35 packages**
