# LibreOffice Offline Build — Research & Implementation

**Date:** April 10, 2026  
**Status:** IMPLEMENTED — fully offline build, no network in chroot

## Problem

LibreOffice downloads ~80-100 external dependency tarballs during `make build` from `dev-www.libreoffice.org`. Our chroot has zero network access — this is a security requirement, not a limitation.

## Solution

Three configure flags enable fully offline builds:

1. **`--with-external-tar=<path>`** — tells LO where pre-downloaded tarballs are
2. **`--disable-fetch-external`** — fails instead of downloading (fail-fast)
3. **`--disable-online-update`** — disables update checker

### Pre-Download Process

External tarballs are downloaded on the build host (outside chroot) and staged at `/sources/libreoffice-externals/`. The build.sh symlinks them into `external/tarballs/` during configure.

The `download.lst` file in the LO source root lists all 149 external entries with SHA256 checksums. With our `--with-system-*` flags, ~80-85 tarballs are actually needed.

### What Distros Do

- **Arch**: Pre-downloads via `makepkg` source array
- **Void**: Uses `XBPS_FETCH` with cached sources
- **Gentoo**: Uses portage's `SRC_URI` fetching before sandbox

All download before building, none allow network during compilation.

## Files Changed

- `packages/extra/libreoffice/build.sh` — added offline flags + external tarball symlinks
- `build/sources/libreoffice-externals/` — 85 pre-downloaded tarballs (398MB)

## Companion Tarballs (separate from externals)

- `libreoffice-dictionaries-26.2.1.2.tar.xz` (60MB)
- `libreoffice-help-26.2.1.2.tar.xz` (56MB)  
- `libreoffice-translations-26.2.1.2.tar.xz` (224MB)

These go into `/sources/` alongside the main tarball and are symlinked into `external/tarballs/` by the build script.
