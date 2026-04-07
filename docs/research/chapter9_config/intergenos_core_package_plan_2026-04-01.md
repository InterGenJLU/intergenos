# InterGenOS Core Package Plan

**Date:** April 1, 2026
**Purpose:** Define all packages needed for InterGenOS Core (LSB Core compliance + essential tools) before DE work begins.

---

## Summary

37 packages + 2 compat shims across 6 build phases.

**Already have from LFS:** Bash, Bc, Binutils, Coreutils, Diffutils, File, Findutils, Gawk, GCC, Gettext, Glibc, Grep, Gzip, M4, Man-DB, Procps, Psmisc, Sed, Shadow, Systemd, Tar, Util-linux, Zlib, Perl, Python 3.14, OpenSSL 3.6.1, meson, ninja, make, pkg-config, autoconf, automake, libxcrypt (with libcrypt.so.2)

**Already built as BLFS:** OpenSSH 10.2p1

---

## Build Phases

### Phase 0: Zero dependencies
| # | Package | Version | Why | Source |
|---|---------|---------|-----|--------|
| 1 | Which | 2.23 | LSB Core + basic utility | BLFS |
| 2 | Time | 1.9 | LSB Core | BLFS |
| 3 | Pax | 20240817 | LSB Core | BLFS |
| 4 | cpio | 2.15 | LSB Core | BLFS |
| 5 | libtasn1 | 4.21.0 | Needed by p11-kit | BLFS |
| 6 | libunistring | 1.4.1 | Needed by libidn2 -> libpsl | BLFS |
| 7 | libuv | 1.52.0 | Needed by CMake | BLFS |
| 8 | libarchive | 3.8.5 | Needed by CMake, Ed | BLFS |
| 9 | libtirpc | 1.3.7 | Needed by libnsl -> Exim, lsof | BLFS |
| 10 | NSPR | 4.38.2 | LSB Core + needed by NSS | BLFS |
| 11 | popt | 1.19 | Needed by rsync | BLFS |

### Phase 1: Depends on Phase 0
| # | Package | Version | Why | Deps |
|---|---------|---------|-----|------|
| 12 | libidn2 | 2.3.8 | Needed by libpsl | libunistring |
| 13 | p11-kit | 0.26.2 | Needed by make-ca | libtasn1 |
| 14 | libnsl | 2.0.1 | Needed by Exim | libtirpc |
| 15 | Ed | 1.22.5 | LSB Core | libarchive (for .tar.lz) |
| 16 | NSS | 3.120.1 | LSB Core | NSPR |
| 17 | Linux-PAM | 1.7.2 | LSB Core; improves sudo/sshd/fcron | (none required) |
| 18 | Fcron | 3.4.0 | Cron daemon | (optional PAM) |
| 19 | nghttp2 | 1.68.0 | HTTP/2 for curl/CMake | (none required) |

### Phase 2: Depends on Phase 1
| # | Package | Version | Why | Deps |
|---|---------|---------|-----|------|
| 20 | libpsl | 0.21.5 | Recommended by wget/curl | libidn2, libunistring |
| 21 | make-ca | 1.16.1 | CA certificates for HTTPS | p11-kit |
| 22 | File::FcntlLock | 0.22 | Perl module for Exim | (Perl in LFS) |

### Phase 3: Depends on Phase 2
| # | Package | Version | Why | Deps |
|---|---------|---------|-----|------|
| 23 | Wget | 1.25.0 | Download tool | libpsl, make-ca |
| 24 | cURL | 8.18.0 | Download tool/library | libpsl, make-ca |
| 25 | Sudo | 1.9.17p2 | Privilege escalation | (optional PAM) |
| 26 | Exim | 4.99.1 | MTA for LSB Core | libnsl, File::FcntlLock |

### Phase 4: Depends on Phase 3
| # | Package | Version | Why | Deps |
|---|---------|---------|-----|------|
| 27 | CMake | 4.2.3 | Build system | curl, libarchive, libuv, nghttp2 |
| 28 | Git | 2.53.0 | Version control | curl |
| 29 | at | 3.2.5 | LSB Core job scheduling | Exim (MTA) |

### Phase 5: System utilities
| # | Package | Version | Why | Deps |
|---|---------|---------|-----|------|
| 30 | rsync | 3.4.1 | File sync | popt |
| 31 | screen | 5.0.1 | Terminal multiplexer | (optional PAM) |
| 32 | lsof | 4.99.5 | List open files | libtirpc |
| 33 | strace | (latest) | System call tracer | (upstream, minimal deps) |
| 34 | htop | (latest) | Interactive process viewer | (upstream, ncurses) |
| 35 | atop | (latest) | System/process monitor | (upstream, ncurses, zlib) |
| 36 | btop | (latest) | Resource monitor | cmake |
| 37 | iotop | (latest) | I/O monitor | (upstream, Python) |

### Compat shims
| # | Task | Why |
|---|------|-----|
| 38 | libcrypt.so.1 symlink | LSB compat (libxcrypt already built) |
| 39 | libncurses.so.5 / libncursesw.so.5 compat | LSB compat (ncurses compat build) |

### Post-build reconfiguration
After Linux-PAM is built:
- Reconfigure Shadow for PAM
- Reconfigure OpenSSH for PAM
- Reconfigure Sudo for PAM
- Reconfigure Fcron for PAM

---

## Packages NOT in BLFS 13.0 (build from upstream)
- htop, atop, btop, iotop, strace
- All are straightforward builds with minimal deps (ncurses, zlib, cmake, Python — all available)

## Decision log
- **MTA:** Exim (owner preference)
- **Cron:** Fcron (only option in BLFS 13.0; cronie not available)
- **PAM:** Yes — reconfigure shadow/sshd/sudo/fcron after build
- **LSB Languages:** Deferred (Python2 is dead, libxml2/libxslt come with DE)
- **LSB Desktop/Imaging/Gtk3:** Deferred to DE phase
