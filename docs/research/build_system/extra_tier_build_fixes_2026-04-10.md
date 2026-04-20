# Extra Tier Build Fixes — Comprehensive Log

**Date:** April 9-10, 2026  
**Packages built:** 59 (56 original + 3 GTK3 C++ bindings + perl-archive-zip + poppler-data + libdvdread)

## Issues Encountered and Resolutions

### Template/Dependency Issues (pre-build)

| Issue | Fix |
|-------|-----|
| `libheif` duplicate in extra + desktop | Removed `extra/libheif` |
| `pangomm` duplicate in extra + desktop | Renamed to `pangomm1` (API version convention) |
| `libadwaita` not found | Corrected to `libadwaita1` in celluloid dep |
| `openjpeg` not found | Corrected to `openjpeg2` in gimp dep |
| `libunwind` not found | Removed — optional, not in tree |
| `aalib` not found | Removed — optional, not in tree |
| Missing GTK3 C++ stack | Created `libsigcpp2`, `glibmm2`, `cairomm1` |
| Missing `perl-archive-zip` | Created package for LibreOffice |
| Missing `poppler-data` | Created package for GIMP |
| `libdvdread` never built | Built manually — was in desktop tier template but never compiled |

### Build Script Fixes

| Package | Issue | Fix |
|---------|-------|-----|
| zip | Wrong make target | `generic_gcc` → `generic` per BLFS |
| pciutils | DESTDIR double-nesting | `PREFIX="$DESTDIR/usr"` → `DESTDIR= PREFIX=/usr` |
| potrace | clang picked up | Added `CC=gcc` |
| libproxy | gi-docgen missing | Added `-D docs=false` |
| GLM | Headers at wrong path | Fixed `cp -r` to create `glm/` subdirectory |
| LibreOffice | Root check fails | `sed` to bypass check (chroot is root) |
| LibreOffice | DESTDIR in build phase | `unset DESTDIR` before `make build` (matches BLFS) |
| LibreOffice | Missing aux tarballs | Downloaded dictionaries, help, translations (340MB) |
| LibreOffice | Network downloads during build | Pre-downloaded 85 externals, added `--disable-fetch-external` + `--with-external-tar` |

### System-Wide Fixes

| Issue | Fix |
|-------|-----|
| Clang can't find GCC (crtbeginS.o, cstddef) | `/etc/clang/clang.cfg` with `--gcc-triple=x86_64-igos-linux-gnu` |
| libpng missing APNG support | Rebuilt with APNG patch from BLFS |
| 14 packages with double-patch application | Removed `patch` from build.sh, kept `patches:` in package.yml for SHA256 validation |
| 11 meson packages missing `--libdir=/usr/lib` | Added `--libdir=/usr/lib` to all (meson defaults to lib64 on x86_64) |
| Package naming inconsistency | Standardized: `<name><major-version>` for coexisting API versions |

### Builder Enhancements

| Enhancement | Details |
|-------------|---------|
| Compressed patch support | `.gz`, `.bz2`, `.xz` patches auto-decompressed via `zcat`/`bzcat`/`xzcat` |

## Packages That Need Rebuilding

All extra tier packages should be rebuilt from scratch with corrected templates. Key packages that installed to wrong paths:
- gegl, appstream-glib, gspell, gtksourceview5, libproxy (installed to lib64 instead of lib)
- GLM (headers at /usr/include/ instead of /usr/include/glm/)

## Outstanding Items

- LibreOffice externals should be treated as vendored content in the build sources
- The lib64 typelib symlinks in the current chroot are temporary — proper rebuild with `--libdir=/usr/lib` eliminates them
