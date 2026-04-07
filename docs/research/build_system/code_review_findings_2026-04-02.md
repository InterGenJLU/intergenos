# Code Review Findings — 2026-04-02

## Context
Deep review of all build scripts and base package build.sh files after a bricked VM
incident. Previous session introduced numerous errors that need fixing before rebuild.

---

## CHAPTER 8 SCRIPT (chroot-build-ch8.sh) — CRITICAL

| Severity | Issue | Location |
|----------|-------|----------|
| CRITICAL | Missing `set -e` — build failures silently ignored | line 25 |
| CRITICAL | Unsafe string handling in debug stripping — unquoted vars | lines 594-636 |
| HIGH | Wrong triplet in cleanup — `x86_64-lfs-*` should be `x86_64-igos-*` | line 668 |
| HIGH | No error check on tarball extraction | line 86 |
| MAJOR | Fragile function type checking (`type -t | grep -q`) | lines 98-140 |

## CHAPTER 9 SCRIPT (chroot-config-ch9.sh)

| Severity | Issue |
|----------|-------|
| MAJOR | Hardcoded Google DNS (8.8.8.8) despite using DHCP |
| MODERATE | No verification systemd-networkd is available |

## SUPPORTING SCRIPTS

| Severity | Issue | File |
|----------|-------|------|
| HIGH | Archive format mismatch — comments say .tar.zst, code creates .tar.gz | pkg-functions.sh |
| HIGH | Missing error checks in kernel config merge | merge-kernel-config.sh |
| MAJOR | Hardcoded IP 192.168.122.69 in multiple scripts | temp-tools-build.sh, toolchain-build.sh, host-check.py |
| MODERATE | Shell injection vulnerability in SSH command construction | host-check.py |

## BASE PACKAGE build.sh SCRIPTS — CRITICAL

### Version Mismatches (build.sh hardcoded versions don't match package.yml)
| Package | build.sh says | package.yml says |
|---------|--------------|-----------------|
| libunistring | 1.4.1 | 1.4.2 |
| libuv | 1.52.0 | 1.52.1 |
| libarchive | 3.8.5 | 3.8.6 |
| atop | 2.11.0 | 2.12.1 |
| btop | 1.4.4 | 1.4.6 |
| lsof | 4.99.5 | 4.99.6 |

### Other Critical Issues
| Severity | Issue | Package |
|----------|-------|---------|
| CRITICAL | lsof calls ./configure but lsof has no configure script | lsof |
| CRITICAL | exim chmod /var/mail without checking it exists | exim |

### Confirmed GOOD
- All base packages (except glib2) properly use DESTDIR="$DESTDIR"
- System user/group creation correct (at, exim, fcron)
- Linux-PAM, NSS, cmake, git builds follow BLFS correctly
- glib2 three-pass build logic is sound (architecture was the problem)

---

## ROOT CAUSE OF SYSTEM BRICK

**glib2** with `direct_install: true` ran three `ninja install` passes directly to `/` on
the live target system. This replaced shared libraries that running processes (systemd, sshd,
bash) depended on. When the library symlink chain broke mid-install, every dynamically-linked
binary on the system became non-executable ("required file not found").

**Fix**: Build glib2 (and all packages) in the chroot on the build VM, never on a live target.
The target VM should only receive finished, tested packages.
