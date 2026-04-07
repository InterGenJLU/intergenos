# InterGenOS Base Package Version Audit

**Date:** April 2, 2026
**Purpose:** Verify all 37 base packages are at latest stable versions, compatible with GCC 15

---

## Policy

InterGenOS should ALWAYS attempt to use the LATEST STABLE VERSION OF EVERY PACKAGE, unless there are KNOWN BUILD ISSUES THAT REQUIRE AN EARLIER VERSION.

---

## Results

### Packages at latest stable (no action needed) — 26 packages

| Package | Version | Notes |
|---------|---------|-------|
| which | 2.23 | Dormant upstream |
| time | 1.9 | Last release 2018, needs GCC 15 sed fix |
| pax | 20240817 | No newer snapshot |
| cpio | 2.15 | Last release 2023, needs GCC 15 sed fixes |
| libtasn1 | 4.21.0 | Released Jan 2026 |
| libtirpc | 1.3.7 | Latest stable |
| nspr | 4.38.2 | Latest stable |
| popt | 1.19 | Latest stable |
| libidn2 | 2.3.8 | Released Mar 2025 |
| p11-kit | 0.26.2 | Released Feb 2026 |
| libnsl | 2.0.1 | Latest on GitHub |
| ed | 1.22.5 | Released Feb 2026 |
| linux-pam | 1.7.2 | Latest stable |
| fcron | 3.4.0 | Latest stable |
| libpsl | 0.21.5 | Latest stable |
| make-ca | 1.16.1 | BLFS script, latest |
| File::FcntlLock | 0.22 | Latest on CPAN |
| wget | 1.25.0 | Released Nov 2024 |
| sudo | 1.9.17p2 | Latest stable |
| exim | 4.99.1 | Security release Dec 2025 |
| git | 2.53.0 | Released Feb 2026 |
| at | 3.2.5 | Latest stable |
| rsync | 3.4.1 | Released Jan 2025 |
| screen | 5.0.1 | Released May 2025 |
| htop | 3.4.1 | Latest stable |
| iotop | 1.31 | Tomas-M C rewrite, latest |

### Packages upgraded — 11 packages

| Package | Old | New | Reason |
|---------|-----|-----|--------|
| atop | 2.11.0 | 2.12.1 | GCC 15 build failure (incompatible-pointer-types) |
| libunistring | 1.4.1 | 1.4.2 | Unicode 17.0, grapheme bugfix |
| libuv | 1.52.0 | 1.52.1 | Patch release Mar 2026 |
| libarchive | 3.8.5 | 3.8.6 | Security/bugfix release Mar 2026 |
| nss | 3.120.1 | 3.121 | Security fixes Feb 2026 |
| nghttp2 | 1.68.0 | 1.68.1 | CVE-2026-27135 |
| curl | 8.18.0 | 8.19.0 | New release Mar 2026 |
| cmake | 4.2.3 | 4.3.1 | Major feature update Mar 2026 |
| lsof | 4.99.5 | 4.99.6 | New release Mar 2026 |
| btop | 1.4.4 | 1.4.6 | New release Jan 2026 |
| strace | 6.14 | 6.19 | 5 versions behind, Feb 2026 |

### GCC 15 compatibility notes

- **time 1.9** — requires `sed -i 's/sighandler interrupt_signal/__sighandler_t interrupt_signal/' src/time.c`
- **cpio 2.15** — requires two sed fixes for xstat function pointer declarations
- **atop 2.11.0** — BROKEN, upgraded to 2.12.1 which includes the fix
- **btop 1.4.6** — potential issue with bundled `intel_gpu_top.c` if GPU_SUPPORT=true for Intel

### Known issue: GitHub archive URL filenames

GitHub archive URLs like `https://github.com/org/repo/archive/refs/tags/v1.2.3.tar.gz` produce filename `v1.2.3.tar.gz` instead of `repo-1.2.3.tar.gz`. This breaks the igos-build filename derivation. Affected packages: atop, btop. Need to either use release download URLs or add a filename field to the template schema.
