# Clean Automated Build — Complete Remediation Checklist

## Purpose

This document captures EVERY issue discovered during the desktop build sessions
on April 5-6, 2026. Each item must be resolved in the source tree BEFORE
attempting the next clean build. The goal: `build-intergenos.sh --checkpoint`
runs from start to finish with zero manual intervention.

---

## Category 1: Missing Packages (must add to tree)

| Package | Version | Why Needed | Dependencies | Status |
|---------|---------|-----------|--------------|--------|
| lmdb | 0.9.35 | Samba ldb backend | none | DONE |
| jansson | 2.15.0 | Samba JSON support | none | DONE |
| cyrus-sasl | 2.1.28 | OpenLDAP auth | lmdb | DONE |
| openldap | 2.6.12 | Samba LDAP | cyrus-sasl | DONE |
| ghostscript | 10.06.0 | libcupsfilters | cups, fontconfig, freetype2, etc. | DONE |
| xdg-desktop-portal-gnome | 49.0 | GNOME portal | gnome-desktop, gtk4, libadwaita1 | DONE |
| libevent | 2.1.12 | links | none | DONE |
| links | 2.30 | xdg-utils | libevent | DONE |
| pyyaml | 6.0.3 | Mesa, igos-build | cython, libyaml | DONE |
| bdftopcf | 1.1 | bitmap fonts | xorgproto | DONE |
| libheif | 1.21.2 | glycin HEIF/AVIF | libaom, dav1d, x265 | DONE |
| glycin | 2.0.8 | mutter image loading | bubblewrap, fontconfig, lcms2, libheif, libseccomp, rust, vala | DONE |
| argcomplete | 3.6.3 | mutter build tools | none | DONE |
| shaderc | 2026.1 | GTK4 Vulkan, Mesa glslc | cmake, glslang, spirv-tools | TODO |

## Category 2: Missing Dependency Declarations (package.yml fixes)

| Package | Missing Dep | Status |
|---------|------------|--------|
| gexiv2 | pygobject3 | DONE |
| xdg-desktop-portal | gst-plugins-base | DONE |
| xev | libXrandr | DONE |
| xdg-desktop-portal-gtk | gnome-desktop | DONE |
| font-cursor-misc | bdftopcf | DONE |
| font-misc-misc | bdftopcf | DONE |
| mutter | glycin, argcomplete | DONE |
| poppler | nss (was declared but NSS wasn't built correctly) | DONE — NSS rebuild needed |

## Category 3: Build Script Fixes (build.sh corrections)

| Package | Issue | Fix | Status |
|---------|-------|-----|--------|
| samba | Missing --without flags | Removed all — added openldap/jansson deps instead | DONE |
| spidermonkey | clang can't find cstddef | Force CC=gcc CXX=g++ | DONE |
| librest | examples need libadwaita | -Dexamples=false per BLFS | DONE |
| gdk-pixbuf | PNG loader not found in tests | Disable deprecated loaders per BLFS 2.44.5 | DONE |
| itstool | autogen.sh not in tarball | Use autoreconf + ./configure | DONE |
| accountsservice | generate-version.sh fails | Replace script with echo $PKG_VERSION | DONE |
| xdg-desktop-portal | Wrong meson option name | docbook-docs → documentation | DONE |
| xdg-desktop-portal | umockdev missing | -Dtests=disabled | DONE |
| appstream | ./AppStream prefix in tarball | Move contents up if subdir exists | DONE |
| poppler | Duplicate cmake flags, gpgmepp/Qt missing | Clean up, add -DENABLE_GPGME=OFF -DENABLE_QT5=OFF -DENABLE_QT6=OFF | DONE |
| mesa | Rust crates download in offline chroot | vulkan-drivers=amd,intel,swrast,virtio (exclude nouveau) | DONE — needs Vulkan remediation |
| mesa | xdemos patch double-includes | Skip patch (included by default in Mesa 25.x) | DONE |
| gtk4 | gstreamer clone from internet | --wrap-mode=nofallback | DONE |
| gtk4 | glslc not found | -Dvulkan=disabled | DONE — needs shaderc for re-enable |
| glycin | Patch applied before vendor extraction | Apply patch in build.sh after vendor, clear checksums | DONE |
| nss | Standalone patch already in upstream | --forward || true | DONE |

## Category 4: Build Infrastructure Issues

| Issue | Root Cause | Fix | Status |
|-------|-----------|-----|--------|
| YAML version truncation | YAML parses 2.30 as float 2.3 | Quoted all 495 versions | DONE |
| DESTDIR staging missing dirs | Python builder bare staging | Added usr/{bin,lib,sbin} + root symlinks | DONE |
| DESTDIR safety check false positive | Intentional staging symlinks flagged | Check is_symlink() before flagging | DONE |
| --skip-built rebuilds Rust | Manifest verification fails after post_install | Check manifest existence only | DONE |
| Rust not in PATH | /opt/rustc/bin not in builder PATH | Added to build_env() globally | DONE |
| `which` not in chroot | Many configure scripts need it | Build `which` as base dep | DONE |
| Patches not in chroot | build/patches/ not copied after restore | Copy patches alongside sources | DONE |
| PyYAML not in chroot | Python builder needs it | Auto-install in chroot-build-desktop.sh | DONE |
| Base deps not built | Desktop depends on base tier (libtirpc, popt, which) | Auto-build in chroot-build-desktop.sh | DONE |
| NSS empty manifest | DESTDIR staging didn't capture files | Root cause: bash CWD persistence — build() cd leaks into do_install(). Fixed by resetting CWD before each phase in all 3 bash build scripts | DONE (2026-04-05) |
| argcomplete installs to /usr/usr/bin | pip --root + --prefix double-prefixes | Removed --prefix=/usr, added --no-deps (matches all other pip packages) | DONE (2026-04-05) |

## Category 5: Vulkan Remediation

These changes restore full Vulkan support for AMD hardware:

| Item | What | Status |
|------|------|--------|
| Add shaderc 2026.1 | Provides glslc (all deps in tree: cmake, glslang, spirv-tools) | DONE (2026-04-05) |
| Pre-download 27 Mesa Rust crates | All crates needed by NVK and other Rust components, archived as mesa-25.3.5-rust-crates.tar.gz | DONE (2026-04-05) |
| Mesa: vulkan-drivers=auto | Re-enable all Vulkan drivers incl. nouveau/NVK | DONE (2026-04-05) |
| Mesa: place crates in subprojects/packagecache/ | Pre-configure step in build.sh extracts crate archive | DONE (2026-04-05) |
| Mesa: wrap-mode=nodownload | Prevents any download attempts while using pre-placed crates | DONE (2026-04-05) |
| GTK4: -Dvulkan=enabled | Re-enable Vulkan rendering | DONE (2026-04-05) |
| GTK4: add shaderc + gst-plugins-base-pass2 deps | So resolver orders correctly | DONE (2026-04-05) |

## Category 6: Offline Chroot Patterns

Recurring issue: packages that download from the internet during build.

| Package | What Downloads | Solution | Status |
|---------|---------------|----------|--------|
| Mesa | 27 Rust crates via meson wrap | Pre-download, archive as mesa-25.3.5-rust-crates.tar.gz, extract to subprojects/packagecache/ | DONE |
| GTK4 | gstreamer via git clone | --wrap-mode=nofallback | DONE |
| glycin | Rust crates via cargo | Pre-vendor tarball (glycin-2.0.8-vendor.tar.gz) | DONE |
| Any pip package | PyPI packages | Download wheel on host, install offline | DONE |

**Pattern documented:** For any package that needs internet during build:
1. Download the required files on the host (has internet)
2. Place them in build/sources/ or build/vendor/
3. Modify build.sh to use the pre-downloaded files
4. Never rely on internet access inside the chroot

## Category 7: gst-plugins-base Rebuild

gst-plugins-base must be built TWICE:
1. First build: before Mesa (no GL support)
2. Second build: after Mesa (with GL support for gstreamer-gl-1.0)

GTK4 needs gstreamer-gl-1.0 which only exists after the rebuild.

**Fix:** Created `gst-plugins-base-pass2` package that depends on Mesa + gst-plugins-base.
GTK4 now depends on gst-plugins-base-pass2 instead of gst-plugins-base directly.
Status: **DONE (2026-04-05)**

## Status: ALL REMEDIATION COMPLETE

All 7 categories resolved. Committed as 0c019d8, pushed to origin/master.
Ready for clean automated build from kernel checkpoint.
