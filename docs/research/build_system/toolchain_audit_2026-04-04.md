# Toolchain Audit — 28 Packages vs LFS 13.0 (Chapters 5-7)
**Date:** 2026-04-04

## Key Architecture Finding

The toolchain has **two build paths**:
1. **Shell scripts** (`toolchain-build.sh` for Ch. 5, `temp-tools-build.sh` for Ch. 6) — contain inline build commands, do NOT read package.yml templates
2. **Package templates** (`packages/toolchain/<name>/package.yml` + optional build.sh) — intended for the Python builder but NOT currently used for toolchain builds

The shell scripts are **correct** — they match the LFS book. The package templates have discrepancies that would matter if/when the Python builder is used for toolchain builds.

Chapter 7 packages (bison-tmp, gettext-tmp, perl-tmp, python-tmp, texinfo-tmp, util-linux-tmp) are built by `chroot-build.sh` which also uses inline commands, not templates.

**All 28 packages match LFS 13.0 versions exactly. No version discrepancies.**

---

## Bugs Requiring Fixes

### Template Bugs (affect Python builder use)

| # | Package | Issue | Severity |
|---|---------|-------|----------|
| 1 | **diffutils-tmp** | Missing `gl_cv_func_strcasecmp_works=y` in package.yml — configure will error during cross-compilation | CRITICAL |
| 2 | **coreutils-tmp** | Missing `--enable-no-install-program=kill,uptime` — installs programs that shouldn't be there | HIGH |
| 3 | **coreutils-tmp** | Extra `gl_cv_macro_MB_CUR_MAX_good=y` not in LFS book | MEDIUM |
| 4 | **coreutils-tmp** | Missing post-install steps (chroot move, man page adjustments) — needs build.sh | HIGH |
| 5 | **gawk-tmp** | Missing `sed -i 's/extras//' Makefile.in` pre-configure — needs build.sh | MEDIUM |
| 6 | **gettext-tmp** | Wrong install method — LFS copies only 3 binaries, autotools installs everything — needs build.sh | HIGH |
| 7 | **bash-tmp** | Missing `ln -sv bash $LFS/bin/sh` post-install — needs build.sh | HIGH |
| 8 | **bash-tmp** | Extra `bash_cv_strtold_broken=no` not in LFS book | LOW |
| 9 | **python-tmp** | Missing `--without-static-libpython` flag in build.sh | MEDIUM |
| 10 | **xz-tmp** | Missing `rm -v $LFS/usr/lib/liblzma.la` post-install — harmful for cross-compilation per LFS | HIGH |
| 11 | **util-linux-tmp** | `mkdir -pv /var/lib/hwclock` creates on host, not in `$IGOS` — needs `$IGOS` prefix | MEDIUM |
| 12 | **glibc** | Extra `--with-headers=$IGOS/usr/include` not in LFS 13.0 (was in older versions) | LOW |
| 13 | **make-tmp** | Extra `--without-guile` not in LFS book (harmless) | LOW |

### Packages That Need build.sh Created

These 16 autotools packages have no build.sh. Most work fine with the autotools style, but 5 need custom steps the autotools style can't handle:

| Package | Why It Needs build.sh |
|---------|----------------------|
| **bash-tmp** | Post-install symlink `ln -sv bash $LFS/bin/sh` |
| **coreutils-tmp** | Missing flag + post-install file moves + man page fixes |
| **diffutils-tmp** | Cross-compilation cache variable `gl_cv_func_strcasecmp_works=y` |
| **gawk-tmp** | Pre-configure `sed -i 's/extras//' Makefile.in` |
| **gettext-tmp** | Custom install (cp 3 binaries instead of make install) |
| **xz-tmp** | Post-install `.la` file removal |

The remaining 10 autotools packages without build.sh are fine as-is:
bison-tmp, findutils-tmp, grep-tmp, gzip-tmp, m4, make-tmp, patch-tmp, sed-tmp, tar-tmp, texinfo-tmp

---

## Packages That Pass Fully (No Issues)

| Package | Chapter | Notes |
|---------|---------|-------|
| binutils-pass1 | 5.2 | build.sh matches LFS exactly |
| binutils-pass2 | 6.17 | build.sh matches LFS exactly |
| file-tmp | 6.7 | Two-step build correct |
| findutils-tmp | 6.8 | Autotools flags correct |
| gcc-pass1 | 5.3 | build.sh matches LFS exactly |
| gcc-pass2 | 6.18 | build.sh matches LFS exactly |
| grep-tmp | 6.10 | Autotools flags correct |
| gzip-tmp | 6.11 | Autotools flags correct |
| libstdcpp | 5.6 | build.sh matches LFS exactly |
| linux-headers | 5.4 | build.sh matches LFS exactly |
| m4 | 6.2 | Autotools flags correct |
| ncurses | 6.3 | build.sh correct (minor TIC_PATH deviation) |
| patch-tmp | 6.13 | Autotools flags correct |
| perl-tmp | 7.9 | build.sh matches LFS exactly |
| sed-tmp | 6.14 | Autotools flags correct |
| tar-tmp | 6.15 | Autotools flags correct |
| texinfo-tmp | 7.11 | Autotools flags correct |

**17 of 28 packages pass with no issues.**

---

## Decision Required

The toolchain shell scripts (`toolchain-build.sh`, `temp-tools-build.sh`, `chroot-build.sh`) work correctly today. The template discrepancies only matter if the Python builder is used for toolchain builds.

**Question:** Do we want the Python builder to eventually handle toolchain, or keep the shell scripts for Ch. 5-7?

If keeping shell scripts: fix the templates anyway for documentation accuracy.
If switching to Python builder: fix templates AND create the 6 missing build.sh files.
