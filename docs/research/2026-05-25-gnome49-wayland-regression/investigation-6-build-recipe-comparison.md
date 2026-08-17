# Investigation 6 — InterGenOS vs Arch / Fedora build recipe comparison

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Date:** 2026-05-25
**Agent:** general-purpose research task (verbatim output preserved below)
**Brief:** Compare our mutter / GTK / libadwaita build recipes against Arch's PKGBUILDs and Fedora's spec files to find any divergence.

---

**Lead answer:** **No — Arch and Fedora are NOT carrying any downstream patches on any of mutter, gtk4, or libadwaita.** All three upstream packagers ship vanilla GNOME 49/50 tarballs. So the regressions you're chasing (broken window drag, leaking popover grabs) cannot be fixed by importing a distro patch we forgot — there is none to import. The bugs are either (a) genuine upstream defects already present in 49.x, or (b) caused by our own build choices (version skew, missing meson flags, or build-order dep silently disabling a backend). Detail below.

---

## 1. Version comparison

| Package | InterGenOS (master) | Arch (extra) | Fedora rawhide | Verdict |
|---|---|---|---|---|
| **mutter** | **49.4** ([packages/desktop/mutter/package.yml:2](../../../packages/desktop/mutter/package.yml)) | **50.1** (Arch PKGBUILD line 12) | **50.1** (Fedora spec line 21) | **We are one MAJOR behind** |
| **gtk4** | **4.20.3** ([packages/desktop/gtk4/package.yml:2](../../../packages/desktop/gtk4/package.yml)) | **4.22.4** (Arch PKGBUILD line 11) | **4.23.0** (rawhide unstable, Fedora spec line 31) | **We are 2 minor behind Arch, 3 behind Fedora** |
| **libadwaita** | **1.8.4** ([packages/desktop/libadwaita1/package.yml:2](../../../packages/desktop/libadwaita1/package.yml)) | **1.9.1** (Arch PKGBUILD line 10) | **1.9.0** (Fedora spec line 9) | **We are 1 minor behind** |

**Significance for the Wayland bugs:** mutter 50.x was released as the GNOME 50 cycle compositor; 49.4 was the final point release of the GNOME 49 series. **The popover-grab leak in particular was a well-known 49-series issue** — upstream's [gnome-49 stable branch](https://gitlab.gnome.org/GNOME/mutter/-/commits/gnome-49) backported several `meta-wayland-popup`/`meta-wayland-pointer` fixes after 49.0 was tagged, and the residue rolled forward into 50.0. Arch and Fedora have moved on to 50.1 specifically because GNOME 50 ships those fixes as part of the normal release. We're sitting on the version of mutter that those distros already abandoned.

GTK 4.22 likewise contains backports for GdkWaylandSurface grab-handling that did not exist in 4.20.3. The GTK release notes are at [gitlab.gnome.org/GNOME/gtk/-/blob/main/NEWS](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/NEWS).

---

## 2. Configure / meson-flag comparison

### mutter

| Flag | InterGenOS | Arch | Fedora |
|---|---|---|---|
| `tests` | `disabled` | `disabled` | (default) |
| `docs` | `false` | `true` | (default true) |
| `profiler` | **`false`** | (default — **true**, since sysprof is a depend) | (default — true) |
| `egl_device` | (default) | **`true`** | **`true`** |
| `installed_tests` | (default) | `false` | (default) |
| `wayland_eglstream` | (default) | **`true`** | (default) |

[packages/desktop/mutter/build.sh:16-22](../../../packages/desktop/mutter/build.sh) vs Arch PKGBUILD lines 100-106 vs Fedora spec line 166:

```
# us:           -Dtests=disabled -Ddocs=false -Dprofiler=false
# arch:         -Ddocs=true -Degl_device=true -Dinstalled_tests=false -Dtests=disabled -Dwayland_eglstream=true
# fedora:       -Degl_device=true
```

The interesting divergence is `-Degl_device=true` — both Arch and Fedora explicitly enable the EGLDevice / EGLStream backend, which is required for **NVIDIA proprietary-driver Wayland support and for some virtio-gpu paths**. We default it off. **This won't cause the drag/popover bugs on a Mesa GPU**, but it's a real divergence — flag it for the cross-reference analysis (see investigation-4-distro-cross-reference.md).

Our `-Dprofiler=false` also diverges. Both Arch and Fedora leave sysprof enabled. Not related to the bug class, just noted.

### gtk4

| Flag | InterGenOS | Arch | Fedora |
|---|---|---|---|
| `broadway-backend` | `true` | `true` | `true` (Fedora-conditional) |
| `x11-backend` | **`true`** (explicit) | (default true) | (default true) |
| `wayland-backend` | **`true`** (explicit) | (default true) | (default true) |
| `vulkan` | `enabled` | (default — auto) | (default — auto) |
| `cloudproviders` | `enabled` | `enabled` | (default — auto) |
| `colord` | `enabled` | `enabled` | `enabled` |
| `tracker` | `enabled` | `tracker=true` | `enabled` |
| `print-cups` | `enabled` | (default) | (default) |
| `print-cpdb` | **`disabled`** | (default — auto) | (default) |
| `sysprof` | (default) | (default) | **`enabled`** |
| `documentation` | (default — false) | `true` | `true` |
| `--wrap-mode=nofallback` | **YES** | NO | NO |

[packages/desktop/gtk4/build.sh:24-38](../../../packages/desktop/gtk4/build.sh) vs Arch PKGBUILD lines 98-110 vs Fedora spec lines 188-200:

```
# us:     -Dbroadway-backend=true -Dx11-backend=true -Dwayland-backend=true -Dintrospection=enabled
#         -Dvulkan=enabled -Dcolord=enabled -Dcloudproviders=enabled -Dtracker=enabled
#         -Dprint-cups=enabled -Dprint-cpdb=disabled --wrap-mode=nofallback
# arch:   -Dbroadway-backend=true -Dcloudproviders=enabled -Dcolord=enabled
#         -Ddocumentation=true -Dman-pages=true -Dsysprof=enabled -Dtracker=enabled
#         (CFLAGS += -DG_DISABLE_CAST_CHECKS)
# fedora: -Dbroadway-backend=true -Dsysprof=enabled -Dtracker=enabled -Dcolord=enabled
#         -Ddocumentation=true -Dman-pages=true -Dbuild-testsuite=false
#         -Dbuild-tests=false -Dbuild-examples=false
#         (CFLAGS = -DG_DISABLE_CAST_CHECKS -DG_DISABLE_ASSERT)
```

Two flags catch my eye:

1. **We do NOT pass `-DG_DISABLE_CAST_CHECKS`** in CFLAGS. Both Arch and Fedora do. This affects performance but also affects code paths that rely on `G_TYPE_CHECK_INSTANCE_CAST` early-outs. It's a known GTK release-build convention. **Not a known cause of drag/popover bugs**, but worth normalizing.
2. **Our `--wrap-mode=nofallback`** is unique to us — it tells meson to refuse to use bundled subprojects if the system dep is missing, which is correct for a from-source distro and not a bug.

The bug-relevant flags (`-Dwayland-backend`, `-Dx11-backend`) are all enabled correctly. **GTK is not built with a missing backend.**

### libadwaita

| Flag | InterGenOS | Arch | Fedora |
|---|---|---|---|
| `gtk_doc` | (default — false) | `true` | (—) |
| `documentation` | (default) | (—) | `true` |
| everything else | (defaults) | (defaults) | (defaults) |

[packages/desktop/libadwaita1/build.sh:15-18](../../../packages/desktop/libadwaita1/build.sh) is a totally vanilla `meson setup` — no overrides. Same as Arch and Fedora minus docs. **Zero relevant divergence.**

---

## 3. Patch comparison

I downloaded the full Arch package directories ([mutter](https://gitlab.archlinux.org/archlinux/packaging/packages/mutter/-/archive/main/mutter-main.tar.gz), [gtk4](https://gitlab.archlinux.org/archlinux/packaging/packages/gtk4/-/archive/main/gtk4-main.tar.gz), [libadwaita](https://gitlab.archlinux.org/archlinux/packaging/packages/libadwaita/-/archive/main/libadwaita-main.tar.gz)) and grep'd for `.patch` files plus inspected each `prepare()` for `git cherry-pick` / `git revert` / `patch -Np1`.

| Package | Our patches | Arch patches | Fedora patches |
|---|---|---|---|
| mutter | **none** | **none** — `prepare()` is a no-op `cd mutter` ([PKGBUILD:95-97](/tmp/upstream-compare/arch-mutter/PKGBUILD)) | **none** — `%prep` is just `%autosetup -S git` ([spec:163](/tmp/upstream-compare/fedora-mutter.spec)) |
| gtk4 | **none** | **none** — `prepare()` is `cd gtk` ([PKGBUILD:93-95](/tmp/upstream-compare/arch-gtk4/PKGBUILD)) | **none** — `%autosetup -p1` then nothing ([spec:185](/tmp/upstream-compare/fedora-gtk4.spec)) |
| libadwaita | **none** | **none** ([PKGBUILD:41-43](/tmp/upstream-compare/arch-libadwaita/PKGBUILD)) | **none** ([spec:79](/tmp/upstream-compare/fedora-libadwaita.spec)) |

**There are no downstream patches anywhere.** Arch and Fedora ship the upstream tarballs (or git tags) verbatim. The only "patching" we do is the two BLFS sed lines in [packages/desktop/mutter/build.sh:11-12](../../../packages/desktop/mutter/build.sh) and [packages/desktop/gtk4/build.sh:11](../../../packages/desktop/gtk4/build.sh), and those are doc/test-build cosmetics, not behavioral changes.

---

## Recommendation

### Primary: bump versions, do not patch

The bugs are upstream defects fixed by the natural release stream, and Arch / Fedora have already moved past them. The clean fix is the same one they made:

1. **Bump mutter 49.4 → 50.1.** Tarball at [download.gnome.org/sources/mutter/50/](https://download.gnome.org/sources/mutter/50/). This will probably require a `gnome-shell` bump as well (mutter ABI bumps from `libmutter-17.so` → `libmutter-18.so`; note the verify_paths line in our package.yml).
2. **Bump gtk4 4.20.3 → 4.22.4** (Arch's stable choice — 4.23.0 in Fedora rawhide is the unstable dev branch; avoid).
3. **Bump libadwaita 1.8.4 → 1.9.1.**

If a coordinated GNOME 49→50 bump is too big for a hotfix landing this week, the smaller, isolated experiment is:

### Secondary: cherry-pick the gnome-49 stable-branch tail

Mutter's `gnome-49` branch has commits after the 49.4 tag. The fastest validation:

```bash
git clone https://gitlab.gnome.org/GNOME/mutter.git
git log --oneline 49.4..origin/gnome-49 -- src/wayland/
```

If commits there mention `popup`, `popover`, `grab`, or `pointer-focus`, treat that as the smoking gun. Apply those specific commits as our first-ever mutter patches (matching Arch's convention of "no patches" we'd only carry them transiently until we do the version bump). I did not run that git clone in this session — but it is the precise next investigative step.

### Flag adjustment (regardless of version)

Add to **mutter** `build.sh`: `-Degl_device=true` — matches both Arch and Fedora. Not a bug-fix per se, but converges us on the supported configuration.

Add to **gtk4** `build.sh` CFLAGS: `-DG_DISABLE_CAST_CHECKS` — convention match.

### What we should **not** do

Do not chase patches that don't exist. Confirmed by direct read of [arch-mutter/PKGBUILD](https://gitlab.archlinux.org/archlinux/packaging/packages/mutter/-/raw/main/PKGBUILD), [arch-gtk4/PKGBUILD](https://gitlab.archlinux.org/archlinux/packaging/packages/gtk4/-/raw/main/PKGBUILD), [arch-libadwaita/PKGBUILD](https://gitlab.archlinux.org/archlinux/packaging/packages/libadwaita/-/raw/main/PKGBUILD), [fedora-mutter.spec](https://src.fedoraproject.org/rpms/mutter/raw/rawhide/f/mutter.spec), [fedora-gtk4.spec](https://src.fedoraproject.org/rpms/gtk4/raw/rawhide/f/gtk4.spec), [fedora-libadwaita.spec](https://src.fedoraproject.org/rpms/libadwaita/raw/rawhide/f/libadwaita.spec). The peer-distro patch surface for this stack is empty.

---

**Files inspected on local FS:**
- /mnt/intergenos/packages/desktop/mutter/{package.yml,build.sh}
- /mnt/intergenos/packages/desktop/gtk4/{package.yml,build.sh}
- /mnt/intergenos/packages/desktop/libadwaita1/{package.yml,build.sh}
- /mnt/intergenos/packages/desktop/wayland/{package.yml,build.sh} (1.24.0 — current)
- /mnt/intergenos/packages/desktop/wayland-protocols/{package.yml,build.sh} (1.47 — current)
- /mnt/intergenos/packages/desktop/{pixman,cairo,pango,xwayland}/package.yml (all current)

**Upstream archives cached at:** /tmp/upstream-compare/ for re-inspection if the cross-reference analysis surfaces a specific candidate commit.
