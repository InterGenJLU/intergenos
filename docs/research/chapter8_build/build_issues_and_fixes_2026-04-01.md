# Chapter 8 Build Issues and Fixes

**Date:** April 1, 2026
**Context:** Issues encountered during Chapter 8 build execution
**Result:** All 82 packages built successfully after fixes

---

## Issue 1: zstd unavailable for archive compression
**Fix:** Switched to gzip (.igos.tar.gz). Commit: 04cdca3

## Issue 2: Readline shlib-install hang (pipe buffer)
**Root cause (strace confirmed):** Child processes blocked on write() to stdout pipe under nohup.
**Fix:** Redirect do_install() output to file in pkg_stage(). Commit: 22e71c1

## Issue 3: install() function name collision
**Root cause:** Bash function install() shadowed /usr/bin/install, causing infinite recursion.
**Fix:** Renamed to do_install() across 82 files. Commit: c3aebce

## Issue 4: Readline wrong sed commands
**Root cause:** Pre-existing build.sh had incorrect seds (not from LFS book).
**Fix:** Rewrote to match LFS 13.0 Section 8.12 verbatim. Commit: e786c99

## Issue 5: GMP missing gcc-15 sed
**Fix:** Added `sed -i '/long long t1;/,+1s/()/(...)/' configure`. Commit: b905ab6

## Issue 6: Bc 7.0.3 + GCC 15 C23 token pasting
**Root cause:** BC_PARSE_EXPR_ENTRY macro token-pastes true/false with UL suffix. C23 keywords can't be concatenated. Upstream fixed in 7.1.0.
**Fix:** Bumped to bc 7.1.0. Commit: fca3e9c

## Issue 7: Triplet mismatch (x86_64-pc-linux-gnu vs x86_64-igos-linux-gnu)
**Root cause:** Final system binutils/glibc/gcc configured without --build/--host/--target flags, auto-detecting pc-linux-gnu instead of our custom triplet.
**Fix:** Added explicit triplet flags. Required full Chapter 5-8 rebuild. Commit: 8b4eb02

## Issue 8: Bash exec kills runner
**Root cause:** `exec /usr/bin/bash --login` in post_install() replaced the runner's shell process.
**Fix:** Removed — new bash is deployed by pkg_deploy automatically. Commit: 3b0b5fa

## Issue 9: Inetutils missing /usr/sbin in staging
**Fix:** mkdir -pv before mv. Commit: 2d6125d

## Issue 10: Elfutils pkgconfig installed as file not directory
**Root cause:** `install -vm644 file "${DESTDIR}/usr/lib/pkgconfig"` creates a file named pkgconfig when the directory doesn't exist.
**Fix:** Use `install -vDm644 file "${DESTDIR}/usr/lib/pkgconfig/libelf.pc"`. Commit: 1ff38b5

## Issue 11: Coreutils/nano/e2fsprogs staging directory issues
**Fix:** Preventive mkdir fixes for directories that DESTDIR install doesn't create. Commit: c2b5ed2

## Issue 12: Python PGO flaky test_generators
**Root cause:** test_raise_and_yield_from fails under PGO instrumentation in KVM due to signal delivery timing. 1/9742 tests.
**Fix:** Exclude test_generators from PROFILE_TASK. Also added --without-static-libpython per LFS. Commit: c4467c0

## Issue 13: GRUB missing sed + stale bash-completion mv
**Root cause:** Missing grub-2.14 linker bug fix sed. Old bash-completion mv was for older GRUB versions.
**Fix:** Added sed, removed mv. Commit: afb0027

## Issue 14: Systemd missing man directory
**Root cause:** -D man=disabled means ninja install doesn't create /usr/share/man. Man pages tarball needs the directory.
**Fix:** mkdir before tar extract. Commit: 539060a

## Issue 15: GCC sanity check a.out path
**Root cause:** `cc dummy.c` writes a.out to CWD, not /tmp.
**Fix:** Use `cc -o /tmp/a.out`. Commit: ac27ea0

## Issue 16: GCC single-threaded test suite
**Fix:** Added -j${IGOS_JOBS} to make check. Commit: 3ebf1ff

---

## Lessons Learned

1. **DESTDIR staging creates a different directory tree than the live system** — always mkdir target dirs explicitly
2. **Never name a bash function the same as a system command** — install() shadows /usr/bin/install
3. **Pipe buffers under nohup are finite** — redirect chatty processes to files, not pipes
4. **Verify build commands against the LFS book** — stale scripts cause hard-to-diagnose failures
5. **Custom triplets must be explicit in all three toolchain packages** — binutils, glibc, gcc
6. **Check upstream for version-specific compiler fixes** — the fix usually already exists
7. **PGO test failures are common in VMs** — exclude flaky tests, not the whole optimization
8. **strace from the host traces into chroot** — invaluable for diagnosing chroot-specific hangs
