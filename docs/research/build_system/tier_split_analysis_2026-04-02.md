# Base-to-Core Tier Split Analysis — 2026-04-02

## Context
After a base build bricked the target VM (glib2 direct_install replaced live system libraries),
we analyzed which "base" packages should actually be "core" (built in the Chapter 8 chroot).

## Decision Criteria
1. Is it a build dependency for other base packages?
2. Is it needed by the build system (igos-build) to function?
3. Is it a foundational library that many future packages will need?
4. Is it a runtime dependency of existing core packages?
5. Would building it in the chroot avoid live-system dangers?

---

## MOVE TO CORE (19 packages)

### Group A: TLS/Certificate Chain
| Package | Why |
|---------|-----|
| libtasn1 | Foundation of TLS chain, required by p11-kit |
| libunistring | Required by libidn2 → libpsl → curl/wget |
| libidn2 | Required by libpsl, internationalized domain names |
| p11-kit | PKCS#11 trust module, required by make-ca and nss |
| make-ca | Installs CA certificate bundle, without it openssl has no trusted roots |
| libpsl | Public Suffix List, required by curl and wget |

### Group B: curl/wget/git (network tools the build system needs)
| Package | Why |
|---------|-----|
| nghttp2 | HTTP/2 library, curl links against it |
| curl | libcurl is linked by cmake, git, and nearly every network program |
| wget | igos-build literally calls wget to download tarballs |
| git | Version control is build infrastructure |

### Group C: PAM and sudo
| Package | Why |
|---------|-----|
| linux-pam | Every login mechanism needs PAM, shadow should be rebuilt with PAM |
| sudo | Essential for user privilege management (Prime Directive) |
| libssh2 | NEW — Required by curl for SCP/SFTP, by git for SSH transport |

### Group D: Foundational libraries
| Package | Why |
|---------|-----|
| glib2 | Most depended-upon library after glibc, needed by desktop everything |
| libarchive | Provides bsdtar, cmake depends on it, builder uses it for .lz |
| libuv | Async I/O, cmake depends on it |
| nspr | Required by nss |

### Group E: NSS and cmake
| Package | Why |
|---------|-----|
| nss | Network Security Services, required by many BLFS packages |
| cmake | Build system for huge fraction of BLFS packages |

### Proposed core build order (after existing Chapter 8):
```
 1. libtasn1
 2. libunistring
 3. libuv
 4. libarchive
 5. nghttp2
 6. nspr
 7. linux-pam  (then rebuild shadow with PAM support)
 8. glib2      (two-pass with gobject-introspection)
 9. libidn2
10. p11-kit
11. sudo
12. libssh2
13. nss
14. make-ca
15. libpsl
16. curl       (with libssh2 support)
17. wget
18. cmake
19. git
```

---

## STAY AS BASE (20 packages)

| Package | Why |
|---------|-----|
| cpio | Archive utility, not a build dep |
| fcron | Cron daemon, system service |
| screen | Terminal multiplexer, useful but not a build dep |
| htop | End-user monitoring tool |
| iotop | End-user I/O monitoring tool |
| libtirpc | Sun RPC, only needed by libnsl/lsof/NFS |
| pax | POSIX archiver, niche |
| perl-file-fcntllock | Perl module, only needed by exim |
| popt | Option parsing, only needed by rsync |
| strace | Debugging tool |
| time | Command timer utility |
| which | Command locator (bash builtins do this) |
| atop | System monitor, end-user tool |
| ed | Line editor |
| libnsl | NIS/NIS+ library, legacy |
| lsof | Diagnostic tool |
| rsync | File sync utility |
| exim | MTA system service |
| at | Job scheduler |
| btop | Terminal monitor, end-user tool |

---

## MISSING PACKAGES TO CONSIDER

| Package | Why | Tier |
|---------|-----|------|
| libssh2 | Required by curl for SCP/SFTP, by git for SSH transport | Core (before curl) |
| c-ares | Async DNS, recommended by BLFS for curl | Base |
| which | Could be dropped entirely (bash builtins do this) | Drop? |
