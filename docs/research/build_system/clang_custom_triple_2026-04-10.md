# Clang + Custom GCC Target Triple — Research & Solution

**Date:** April 10, 2026  
**Status:** IMPLEMENTED — system-wide `/etc/clang/clang.cfg`

## Problem

Clang's `GCCInstallationDetector` maintains a hardcoded list of known x86_64 target triples. Our `x86_64-igos-linux-gnu` is not in that list, so clang cannot find GCC's headers (`cstddef`), runtime files (`crtbeginS.o`), or libraries (`libgcc_s`).

## Why No Other Distro Has This Problem

| Distro | GCC Triple | In Clang's List? |
|--------|-----------|-----------------|
| Debian/Ubuntu | `x86_64-linux-gnu` | Yes |
| Fedora/RHEL | `x86_64-redhat-linux` | Yes |
| SUSE | `x86_64-suse-linux` | Yes |
| Gentoo/Arch | `x86_64-pc-linux-gnu` | Yes |
| Void | `x86_64-unknown-linux-gnu` | Yes |
| LFS/BLFS | `x86_64-pc-linux-gnu` | Yes |

## Solution: `--gcc-triple` via clang config files

LLVM 18+ supports `--gcc-triple=<triple>` which tells clang's `GCCInstallationDetector` exactly which triple to search for. Combined with clang's config file system:

**`/etc/clang/clang.cfg`:**
```
--gcc-triple=x86_64-igos-linux-gnu
```

**`/etc/clang/clang++.cfg`:**
```
--gcc-triple=x86_64-igos-linux-gnu
```

Clang auto-detects the GCC version by scanning `/usr/lib/gcc/x86_64-igos-linux-gnu/` — no version hardcoding needed.

## Why This Over Alternatives

- `--gcc-install-dir=/usr/lib/gcc/.../15.2.0` — hardcodes version, breaks on GCC upgrade
- `--gcc-toolchain=/usr` — still needs a recognized triple, so doesn't solve the problem alone
- `-B` and `CPLUS_INCLUDE_PATH` hacks — fragile, per-package, bypasses normal detection

## Verification

```bash
clang++ -v -c -xc++ /dev/null 2>&1 | grep "Selected GCC"
# Output: Selected GCC installation: /usr/bin/../lib/gcc/x86_64-igos-linux-gnu/15.2.0
```

## Files Changed

- `/etc/clang/clang.cfg` — created in chroot
- `/etc/clang/clang++.cfg` — created in chroot
- `scripts/create-image.sh` — creates both files during image creation
- `packages/extra/thunderbird/build.sh` — removed all per-package clang workarounds

## Sources

- LLVM Discourse: gcc-install-dir deprecation thread
- Clang Command Line Reference: --gcc-triple documentation
- Clang 18.1.6 Release Notes (--gcc-triple introduction)
- GCCInstallationDetector source: clang/lib/Driver/ToolChains/Gnu.cpp
- Gentoo clang config file approach: blogs.gentoo.org
