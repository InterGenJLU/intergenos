# Core Audit — 105 Packages vs LFS 13.0 Ch. 8 + BLFS 13.0
**Date:** 2026-04-04

---

## CRITICAL ISSUES (will break builds or produce incorrect system)

| # | Package | Issue |
|---|---------|-------|
| 1 | **dejagnu** | Missing package.yml entirely — build will fail |
| 2 | **expect** | Missing package.yml entirely — build will fail |
| 3 | **glibc-core** | Missing package.yml entirely — build will fail |
| 4 | **tcl** | Missing package.yml entirely — build will fail |
| 5 | **glibc-core** | Extra `--build` and `--host` flags in build.sh — Ch. 8 final system should NOT have cross-compile flags |
| 6 | **xml-parser** | build_style `autotools` is wrong — uses `perl Makefile.PL`, not `./configure` |
| 7 | **tcl** | `$SRCDIR` variable set in configure() but used in build() — may be empty if functions run in subshells |
| 8 | **git** | perllibdir missing version: `/usr/lib/perl5/site_perl` should be `/usr/lib/perl5/5.42/site_perl` |

## lib64 ISSUES (meson packages missing --libdir=/usr/lib)

| Package | File |
|---------|------|
| **dbus** | packages/core/dbus/build.sh |
| **glib2** | packages/core/glib2/build.sh |
| **glib2-bootstrap** | packages/core/glib2-bootstrap/build.sh |
| **gobject-introspection** | packages/core/gobject-introspection/build.sh |
| **kmod** | packages/core/kmod/build.sh |
| **libpsl** | packages/core/libpsl/build.sh |
| **linux-pam** | packages/core/linux-pam/build.sh |
| **p11-kit** | packages/core/p11-kit/build.sh |
| **systemd** | packages/core/systemd/build.sh |

**9 core meson packages need `--libdir=/usr/lib` added.**

## MODERATE ISSUES

| # | Package | Issue |
|---|---------|-------|
| 1 | **binutils-core** | Extra `--enable-gold` and `--build/--host` flags not in LFS; missing removal of `/usr/share/doc/gprofng/` |
| 2 | **bc** | Missing `-std=c99` in CC variable (may be fixed in 7.1.0 but should verify) |
| 3 | **glib2** | `-D man-pages=disabled` should be `-D man-pages=enabled` per BLFS |
| 4 | **glibc-core** | Only 3 locales installed vs ~20 in LFS — may cause test failures downstream |
| 5 | **shadow-pam** | chpasswd/newusers PAM configs wrong — copied from chage template |
| 6 | **vim** | Missing test sed (`sed '/test_plugin_glvs/d'`); extra `--with-tlib=ncursesw` not in LFS |
| 7 | **util-linux-core** | Missing `touch /etc/fstab` in check() for tests |
| 8 | **libpipeline** | check() will fail — Check library not in LFS; needs `|| true` |
| 9 | **python** | pip.conf content differs from LFS — should merge LFS entries into custom config |
| 10 | **curl** | Missing `openssl` as explicit dependency |
| 11 | **libssh2** | Missing `openssl` as explicit dependency |
| 12 | **wget** | dep categorization wrong (libpsl→host, make-ca→runtime, missing openssl) |

## MINOR ISSUES

| # | Package | Issue |
|---|---------|-------|
| 1 | **bash** | Stale configure_flags in package.yml don't match build.sh |
| 2 | **dbus** | build_style `autotools` in package.yml is wrong — uses meson |
| 3 | **e2fsprogs** | Comment says Section 8.81, should be 8.83 |
| 4 | **grub** | Comment says Section 8.64, should be 8.66 |
| 5 | **util-linux-core** | Comment says Section 8.80, should be 8.82 |
| 6 | **ninja** | Missing `--verbose` flag (cosmetic) |
| 7 | **ncurses-core** | Missing optional doc install |
| 8 | **p11-kit** | Duplicate libnssckbi.so symlink (also in nss) |
| 9 | **zstd** | lz4 listed as dep but not required by LFS |

## PACKAGES THAT PASS CLEAN (no issues)

acl, attr, autoconf, automake, bison-core, bzip2, cmake, coreutils-core, diffutils-core, elfutils, expat, file, findutils-core, flex, flit-core, gawk-core, gcc-core, gdbm, gettext, gmp, gperf, grep-core, groff, gzip-core, iana-etc, inetutils, intltool, iproute2, jinja2, kbd, less, libcap, libffi, libidn2, libpipeline (except check), libtasn1, libtool, libunistring, libuv, libxcrypt, linux-kernel, lz4, m4-core, make-ca, make-core, man-db, man-pages, markupsafe, meson, mpc, mpfr, nano, ncurses-core, nghttp2, nspr, nss, openssl, packaging, patch-core, pcre2, perl-core, pkgconf, procps-ng, psmisc, readline, sed-core, setuptools, shadow, sqlite, sudo, tar-core, texinfo-core, wheel, xz, zlib, zstd

**~75 of 105 packages pass clean.**

## MISSING package.yml (4 packages — need creation)

| Package | Version | Source | Deps |
|---------|---------|--------|------|
| dejagnu | 1.6.3 | https://ftpmirror.gnu.org/dejagnu/dejagnu-1.6.3.tar.gz | expect |
| expect | 5.45.4 | https://prdownloads.sourceforge.net/expect/expect5.45.4.tar.gz | tcl |
| glibc-core | 2.43 | https://ftp.gnu.org/gnu/glibc/glibc-2.43.tar.xz | linux-headers (toolchain) |
| tcl | 8.6.17 | https://downloads.sourceforge.net/tcl/tcl8.6.17-src.tar.gz | zlib |

## VERSION NOTES (newer than book — intentional per project policy)

| Package | Ours | BLFS | Notes |
|---------|------|------|-------|
| bc | 7.1.0 | 7.0.3 | GCC 15 compat |
| cmake | 4.3.1 | 4.2.3 | Latest stable |
| curl | 8.19.0 | 8.18.0 | Latest stable |
| libunistring | 1.4.2 | 1.4.1 | Latest stable |
| libuv | 1.52.1 | 1.52.0 | Latest stable |
| libarchive | 3.8.6 | 3.8.5 | Latest stable |
| nghttp2 | 1.68.1 | 1.68.0 | Latest stable |
| nss | 3.121 | 3.120.1 | Latest stable |
