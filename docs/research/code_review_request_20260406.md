# InterGenOS Code Review Request

## Project Context

InterGenOS is a Linux distribution built entirely from source, based on LFS 13.0 (Linux From Scratch) and BLFS 13.0 (Beyond LFS). The build system uses YAML package templates + bash build scripts, orchestrated by a Python builder that handles dependency resolution, DESTDIR staging, manifest tracking, and archive creation.

The build runs inside a chroot on an Ubuntu 24.04 build VM. The chroot has no internet access — all sources must be pre-downloaded.

## What We Need Reviewed

Three categories of changes made in the last session. Please review for correctness, potential build failures, missing dependencies, and adherence to BLFS 13.0 conventions.

---

## Category 1: Build Infrastructure Fixes

### 1a. CWD Reset Between Build Phases (3 files)

**Problem:** In the bash build scripts (`chroot-build-ch8.sh`, `chroot-build-core-extra.sh`, `chroot-build-base.sh`), each package's `configure()`, `build()`, `check()`, and `do_install()` functions run in the same shell. If `build()` does `cd nss` (for example), the CWD persists into `do_install()`, which then does `cd dist` from the wrong directory. This caused NSS's DESTDIR staging to produce an empty manifest.

**Fix:** Added `cd "$workdir"` before each phase call (configure, build, check, install, post_install).

**Question for reviewer:** Is there any case where a package INTENTIONALLY relies on CWD from a previous phase? Should the reset be unconditional?

### 1b. Timezone Consistency (2 files)

**Problem:** Timestamps in build logs alternated between CDT (host) and UTC (chroot). Root cause: `glibc-core/build.sh` post_install hardcodes `ln -sfv /usr/share/zoneinfo/UTC /etc/localtime`, overwriting the host timezone copied by `chroot-setup.sh`.

**Fix:** 
- `glibc-core/build.sh`: Read `$TZ` env var or `/etc/timezone` instead of hardcoding UTC
- `chroot-setup.sh`: Copy both `/etc/localtime` (as regular file, not symlink) and `/etc/timezone` into the chroot

**Question for reviewer:** Is reading `$TZ` in glibc's post_install safe? Could `$TZ` contain values that don't map to a zoneinfo file?

### 1c. WebKitGTK OOM Prevention (2 files)

**Problem:** WebKitGTK with 16 parallel ninja jobs on 32GB RAM triggers the OOM killer (WebCore unified sources each use ~2GB).

**Fix:** Cap ninja parallelism at 8 jobs for both `webkitgtk-gtk3` and `webkitgtk`:
```bash
local jobs=${IGOS_JOBS}
[ "$jobs" -gt 8 ] && jobs=8
ninja -j${jobs}
```

**Question for reviewer:** Is 8 the right threshold for 32GB? Should this be calculated dynamically (e.g., `$(( $(free -g | awk '/Mem/{print $2}') / 3 ))`)?

---

## Category 2: 15 New Package Templates

We added 15 packages to enable features that were previously disabled due to missing dependencies. Each package has a `package.yml` (metadata + deps) and `build.sh` (configure/build/install functions).

### BLFS packages (have BLFS build instructions):
1. **libcdio 2.1.0** — CD I/O library (autotools, no deps)
2. **libcdio-paranoia 10.2+2.0.2** — CD paranoia from libcdio (autotools, dep: libcdio)
3. **libdaemon 0.14** — Daemon helper library (autotools, no deps)
4. **avahi 0.8** — mDNS/DNS-SD service discovery (autotools, deps: glib2, libdaemon, gtk3). Includes BLFS IPv6 race condition patch and security sed fix. Creates system user/group in post_install.
5. **graphviz 14.1.2** — Graph visualization (cmake, dep: cmake). Includes BLFS rpath fix.
6. **gtksourceview5 5.18.0** — Source code widget (meson, dep: gtk4)

### Non-BLFS packages (upstream autotools/cmake/meson):
7. **libmtp 1.1.23** — MTP device access (autotools, dep: libusb)
8. **libnfs 6.0.2** — NFS client library (cmake, no deps)
9. **libbluray 1.4.1** — Blu-ray library (autotools, runtime deps: libxml2, fontconfig)
10. **libgphoto2 2.5.33** — Camera access (autotools, runtime deps: libusb, libexif, curl)
11. **libplist 2.7.0** — Apple property list (autotools, no deps)
12. **libimobiledevice-glue 1.3.2** — Common code (autotools, dep: libplist)
13. **libusbmuxd 2.1.1** — USB mux client (autotools, deps: libplist, libimobiledevice-glue)
14. **libimobiledevice 1.4.0** — Apple device access (autotools, deps: libplist, libimobiledevice-glue, libusbmuxd, libusb, openssl)
15. **libmsgraph 0.3.4** — Microsoft Graph API (meson, deps: glib2, json-glib, libsoup3, gnome-online-accounts)

**Questions for reviewer:**
- Do any of these packages have additional required dependencies I've missed?
- Are the configure flags correct for each package?
- For the libimobiledevice chain (packages 11-14), is the dependency ordering correct?
- Should any of these packages have `post_install()` hooks (e.g., ldconfig, udev rules)?

---

## Category 3: Feature Re-enablement

We removed disabled flags from 5 existing packages:

1. **gvfs** — Removed 10 `-Dfeature=false` flags. Only `-Dgoogle=false` remains (libgdata deprecated upstream, removed from BLFS). Added all new packages as build deps. Created `gvfs-pass2` for GOA/OneDrive support (builds after gnome-online-accounts).

2. **vala** — Removed `--disable-valadoc`, added graphviz as build dep.

3. **libsecret** — Removed `-Dmanpage=false`, added libxslt as build dep.

4. **librest** — Removed `-Dexamples=false` and `-Dtests=false`, added gtksourceview5 and libadwaita1 as build deps.

5. **libpwquality** — Removed `--disable-python-bindings`.

**Questions for reviewer:**
- For gvfs, is `gvfs-pass2` the right pattern for the GOA circular dependency, or is there a better approach?
- Does librest's examples feature actually require gtksourceview5 at build time, or only at runtime?
- Will libpwquality's Python bindings build correctly with Python 3.14?

---

## Full Diff

The complete diff is included below for reference. Total: 45 files changed, 678 insertions, 33 deletions.

[PASTE THE DIFF OUTPUT FROM `git diff HEAD~3..HEAD` HERE]

---

## How to Review

1. Check each `package.yml` for correct dependency declarations
2. Check each `build.sh` for correct configure flags per upstream/BLFS
3. Verify the dependency chain ordering won't cause circular dependencies
4. Flag any missing `post_install()` hooks (ldconfig, user creation, etc.)
5. Flag any potential build failures in an offline chroot environment
6. Check if any `--disable-static` should NOT be used for particular packages
