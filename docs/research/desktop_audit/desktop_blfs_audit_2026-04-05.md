# InterGenOS Desktop Tier BLFS 13.0 Audit — Complete Results

**Date:** April 5, 2026
**Packages audited:** 337
**Reference:** BLFS 13.0 systemd edition

---

## Summary

| Severity | Count | % |
|----------|-------|---|
| CLEAN | 251 | 74.5% |
| INFO | 29 | 8.6% |
| WARNING | 43 | 12.8% |
| ERROR | 14 | 4.2% |

---

## ERRORS (14) — Immediate Action Required

| Package | Issue |
|---------|-------|
| **pinentry** | Missing first sed: `sed -i "/FLTK 1/s/3/4/" configure` |
| **libsamplerate** | Contains incorrect seds copied from libsndfile (will fail on build) |
| **exiv2** | Missing cmake flags: -DEXIV2_ENABLE_VIDEO=yes, -DEXIV2_ENABLE_WEBREADY=yes, -DEXIV2_ENABLE_CURL=yes, -DEXIV2_BUILD_SAMPLES=no; missing curl dep |
| **libva** | Uses --prefix=/usr instead of $XORG_PREFIX; BLFS requires Xorg env + libdrm as required |
| **soundtouch** | Uses CMake but BLFS uses autotools (bootstrap + configure) |
| **lynx** | Missing --datadir flag and install-full post-install step |
| **iceauth** | Syntax error in build.sh (trailing backslash without continuation) |
| **sessreg** | Syntax error in build.sh (trailing backslash without continuation) |
| **font-util** | Syntax error in build.sh (trailing backslash without continuation) |
| **font-alias** | Syntax error in build.sh (trailing backslash without continuation) |
| **font-dejavu** | NOT in BLFS 13.0 (supplementary font — needs manual review) |
| **xwayland** | Undocumented -Dxkb_dir parameter not in BLFS |
| **libdmx** | NOT in BLFS 13.0 — cannot verify |
| **gnome-shell-extensions** | Missing libgtop dependency that BLFS requires |

---

## WARNINGS (43) — Should Address

### Missing $XORG_CONFIG flags (--sysconfdir=/etc --localstatedir=/var)
These Xorg packages are missing the full $XORG_CONFIG expansion in build.sh:
- libXrender, libXrandr, libXtst, libXv, libXvMC, libXxf86dga, libXxf86vm, libXScrnSaver
- xcb-util, xcb-util-image, xcb-util-keysyms, xcb-util-renderutil, xcb-util-wm, xcb-util-cursor, xbitmaps
- startup-notification
- xev, xhost

**Fix:** Add `--sysconfdir=/etc --localstatedir=/var` to configure flags (18 packages)

### Build flag mismatches
| Package | Issue |
|---------|-------|
| **libgpg-error** | Missing --sysconfdir=/etc |
| **libpwquality** | Python bindings not built (--disable-python-bindings) |
| **opus** | Uses autotools but BLFS uses meson/ninja |
| **sbc** | Missing --disable-tester flag |
| **libwebp** | Missing --enable-libwebpextras and --enable-swap-16bit-csp |
| **x265** | Missing -D CMAKE_POLICY_VERSION_MINIMUM=3.5 |
| **svt-av1** | Missing -D CMAKE_SKIP_INSTALL_RPATH=ON -D BUILD_SHARED_LIBS=ON |
| **libmnl** | Added --disable-static not in BLFS |
| **libevdev** | Meson flags may not be applied from package.yml |
| **libinput** | Missing -D udev-dir=/usr/lib/udev |
| **hicolor-icon-theme** | Uses autotools but BLFS uses meson |
| **itstool** | Uses ./configure instead of ./autogen.sh |
| **doxygen** | Disables build_wizard when BLFS expects it enabled |
| **vala** | Disables valadoc documentation |
| **font-cursor-misc** | NOT in BLFS 13.0 |
| **glslang** | Duplicate cmake parameters (copy-paste error) |
| **mesa** | Uses /usr instead of $XORG_PREFIX |
| **gdk-pixbuf** | Image loaders disabled (BLFS recommends enabled) |
| **gtk3** | -D man=false instead of BLFS man=true |
| **gtk4** | Unnecessary build-testsuite/build-tests flags |
| **libadwaita1** | Unnecessary -Dexamples=false and -Dtests=false |
| **polkit** | man and test flags disabled vs BLFS enabled |
| **gnome-terminal** | Uses libadwaita1 instead of BLFS libhandy1 |
| **gvfs** | Fewer backends disabled than BLFS suggests |
| **npth** | package.yml has --disable-static but BLFS doesn't |

---

## NOT IN BLFS (4 packages — manual review)

| Package | Notes |
|---------|-------|
| **font-dejavu** | Supplementary font, standard for desktop |
| **font-noto** | Google Noto fonts, standard for i18n |
| **font-cursor-misc** | Legacy Xorg cursor font |
| **libdmx** | DMX extension library |

---

## Circular Dependencies — CORRECTLY HANDLED

- **freetype2-pass1 → harfbuzz → freetype2** — Synthetic package correctly breaks the cycle

---

## Version Mismatches

**None.** All 333 BLFS-mapped packages match BLFS 13.0 versions exactly.

---

## Recommended Fix Priority

### Priority 1: Build-breaking errors
1. Fix 4 syntax errors (iceauth, sessreg, font-util, font-alias)
2. Fix libsamplerate wrong seds
3. Fix soundtouch build system (autotools, not cmake)
4. Fix exiv2 cmake flags
5. Add libgtop dep to gnome-shell-extensions

### Priority 2: Missing Xorg flags (batch fix)
6. Add --sysconfdir=/etc --localstatedir=/var to 18 Xorg packages

### Priority 3: Functionality gaps
7. Fix pinentry missing sed
8. Fix lynx install-full
9. Fix xwayland extra parameter
10. Fix gdk-pixbuf image loaders
11. Fix opus build system (meson)

### Priority 4: Polish
12. Fix remaining WARNING items (glslang duplicates, man page flags, etc.)
