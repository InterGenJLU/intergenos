# Desktop Build Fixes — April 5, 2026

Log of all package fixes required during desktop tier builds.

## Build Infrastructure Fixes

| Fix | Root Cause | Solution |
|-----|-----------|----------|
| YAML version truncation | YAML parses `2.30` as float `2.3` | Quoted ALL 495 version strings |
| DESTDIR staging missing dirs | Python builder created bare staging | Added `usr/{bin,lib,sbin}` + root symlinks |
| DESTDIR safety check false positive | Intentional staging symlinks triggered collision check | Check `is_symlink()` before flagging |
| --skip-built rebuilding Rust | Manifest verification fails after post_install moves files | Check manifest existence only, skip verification |
| Rust not in PATH | `/opt/rustc/bin` not in builder's PATH | Added to `build_env()` globally |
| `which` not in chroot | Many configure scripts use `which` (not POSIX) | Built `which` package from base tier |
| Missing patches in chroot | `build/patches/` not copied to `/sources/` after restore | Copy patches alongside sources |
| PyYAML not in chroot | Python builder needs PyYAML but it's not a tracked package | Auto-install in chroot-build-desktop.sh + added pyyaml package |
| Base deps missing | Desktop packages depend on base tier packages (libtirpc, popt, which) | Auto-build base deps before desktop tier |

## Per-Package Fixes

| Package | Error | Fix | BLFS Match |
|---------|-------|-----|------------|
| cdparanoia | Patch file missing | Copy patches to /sources/ | N/A |
| libvpx | "Neither yasm nor nasm found" | Build nasm first (dep ordering) | Yes |
| newt | popt.h missing | Build popt (base dep) | Yes |
| samba (1) | LDAP not found | --without-ldap | No — fixed by adding openldap |
| samba (2) | lmdb not found | --without-ldb-lmdb | No — fixed by adding lmdb |
| samba (3) | jansson not found | --without-json | No — fixed by adding jansson |
| samba (4) | ADS not found | --without-ads | No — needs LDAP, now have it |
| samba (final) | All deps added | Removed all --without flags except --without-ad-dc | Yes |
| gexiv2 | Python gi module not found | Added pygobject3 as dependency (not disable!) | Per Prime Directive |
| cargo-c (1) | cargo not found | Added /opt/rustc/bin to builder PATH | BLFS assumes profile.d sourced |
| cargo-c (2) | DESTDIR/usr/bin/ missing | Added mkdir -p | N/A |
| rust-bindgen | DESTDIR/usr/bin/ missing | Added mkdir -p | N/A |
| freetype2 pass2 | No new files (fs diff) | Removed `direct_install: true` | N/A |
| spidermonkey | clang can't find cstddef | Force CC=gcc CXX=g++ | BLFS uses GCC on x86_64 |
| bluez | /usr/sbin/ missing in staging | DESTDIR staging scaffold fix | N/A |
| librest | libadwaita not found (examples) | Added -Dexamples=false per BLFS | Yes |
| links | Version 2.30 parsed as 2.3 | Quoted version in YAML | N/A |

## Packages Added (from BLFS gap analysis)

| Package | Version | Reason |
|---------|---------|--------|
| lmdb | 0.9.35 | Samba ldb backend |
| jansson | 2.15.0 | Samba JSON support |
| cyrus-sasl | 2.1.28 | OpenLDAP authentication |
| openldap | 2.6.12 | Samba LDAP support |
| ghostscript | 10.06.0 | Required by libcupsfilters |
| xdg-desktop-portal-gnome | 49.0 | Native GNOME portal backend |
| libevent | 2.1.12 | Required by links |
| links | 2.30 | Required by xdg-utils |
| pyyaml | 6.0.3 | Required by Mesa |
