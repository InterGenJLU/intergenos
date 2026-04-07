# DESTDIR Package Tracking — Research and Design Decision

**Date:** April 1, 2026
**Context:** Designing the package tracking layer for Chapter 8 builds
**Decision:** DESTDIR + Archive, Slackware-style one-file-per-package manifests

---

## Decision

All Chapter 8 packages will be installed via DESTDIR staging, creating:
1. A file manifest per package at `/var/lib/igos/packages/<name>-<version>`
2. A binary archive at `/var/lib/igos/archives/<name>-<version>.igos.tar.gz`
   (gzip used during initial build; zstd not available until package #8)

This enables clean removal, upgrade, and querying from day one.

## DESTDIR Exceptions (10 of 83 packages)

| Package | Mechanism | Notes |
|---------|-----------|-------|
| Glibc | `install_root=$DEST` | Glibc's native variable; `DESTDIR` may also work |
| Bzip2 | `PREFIX=$DEST/usr` + manual steps | No autotools, custom Makefile |
| Lz4 | `PREFIX=/usr DESTDIR=$DEST` | Both required |
| Zstd | `PREFIX=/usr DESTDIR=$DEST` | Both required |
| Libcap | `prefix=/usr lib=lib DESTDIR=$DEST RAISE_SETFCAP=no` | RAISE_SETFCAP=no critical for staging |
| Man-pages | `DESTDIR=$DEST prefix=/usr` | Simple Makefile |
| Iana-Etc | Manual `install` commands | No build system |
| Ninja | Manual `install` commands | No install target |
| 7 pip packages | `pip3 install --root=$DEST` | flit-core, packaging, wheel, setuptools, markupsafe, jinja2, meson |

## Post-Install Hooks (run on live system after file install)

| Package | Commands |
|---------|----------|
| Glibc | ldconfig, localedef, timezone symlink, nsswitch.conf, ld.so.conf |
| Shadow | pwconv, grpconv, useradd -D, passwd root |
| Systemd | systemd-machine-id-setup, systemctl preset-all |
| GCC | Sanity checks (readelf, grep for correct paths) |
| Bash | exec /usr/bin/bash --login (replace running shell) |

## Manifest Format (Slackware-style)

File: `/var/lib/igos/packages/<name>-<version>`
```
PACKAGE NAME: <name>-<version>
PACKAGE VERSION: <version>
COMPRESSED SIZE: <size>
UNCOMPRESSED SIZE: <size>
BUILD DATE: <ISO 8601>
DESCRIPTION:
<name>: <short description>

FILE LIST:
usr/
usr/bin/
usr/bin/foo
usr/lib/
usr/lib/libfoo.so.1
...
```

## Bootstrap Strategy

1. Chapter 8 runner uses a shell function `pkg_install()` to stage/capture/install
2. No package manager binary needed during the build — just shell functions
3. Actual `igos-pkg` tools built post-Chapter 8
4. Manifests from step 1 are the authoritative package database immediately

## Sources

- LFS 13.0 Section 8.2 (Package Management Techniques)
- CRUX pkgutils: single flat-file DB, name+version+filelist
- Slackware pkgtools: one text file per package in /var/lib/pkgtools/packages/
- Arch pacman: directory per package with desc+files+mtree
- Void Linux templates: confirmed DESTDIR patterns for all edge cases
- Prior InterGenOS research: pm_history_and_approaches_2026-03-31.md
