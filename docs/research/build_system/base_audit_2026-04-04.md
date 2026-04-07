# Base Audit — 20 Packages vs BLFS 13.0
**Date:** 2026-04-04

---

## Summary

20 packages audited. Very clean tier.

- **1 bug fixed:** libnsl missing `--prefix=/usr` (would install to /usr/local)
- **1 cosmetic fix:** iotop build.sh header version (0.6 → 1.31)
- **0 lib64 issues** (no meson packages in base)
- **0 missing package.yml**
- **7 packages with build_style: autotools + live build.sh** — all verified correct
- **5 packages not in BLFS** (atop, btop, htop, iotop, strace) — reviewed against upstream

## Packages That Pass Clean

at, atop, btop, cpio, ed, exim, fcron, htop, libtirpc, lsof, pax,
perl-file-fcntllock, popt, rsync, screen, strace, time, which

## Intentional Deviations From BLFS (documented, correct)

- **screen:** Uses `--enable-pam` (BLFS uses `--disable-pam`). Intentional — linux-pam is a build dep.
- **at:** Uses `/usr/lib/systemd/system` instead of BLFS `/lib/systemd/system`. More correct on LFS where /lib → /usr/lib.
- **lsof:** Version 4.99.6 vs BLFS 4.99.5. Per project policy: always latest stable.

## Version Matches

All 15 BLFS packages match exactly (except lsof — latest stable policy).
5 packages not in BLFS — sourced from upstream.
