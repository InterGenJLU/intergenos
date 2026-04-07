# LFS Alternatives Assessment

**Date:** March 31, 2026
**Decision:** Stick with LFS as the base — no alternative provides better alignment with the PRIME DIRECTIVE

---

## Alternatives Evaluated

### CLFS (Cross Linux From Scratch)
- **Status:** No longer maintained (last updated August 2017)
- **Cross-compilation support integrated back into main LFS**
- **Verdict:** Not relevant unless targeting non-x86 architectures

### Gentoo Stage1/Stage2
- **Pros:** Faster bootstrap (hours vs weeks), Portage included, 19,000+ packages
- **Cons:** Inherits Gentoo opinions (USE flags, eclasses, Portage philosophy)
- **Verdict:** Too much inherited baggage when building our own package manager

### Buildroot
- **Designed for:** Embedded/IoT (not desktop)
- **Produces:** Monolithic root filesystem images (must regenerate for any change)
- **No dynamic package management**
- **Verdict:** Wrong tool for desktop Linux

### Yocto/OpenEmbedded
- **Designed for:** Industrial embedded (automotive, medical, IoT)
- **Overkill complexity** for a one-person desktop project
- **Verdict:** Wrong scale and audience

### Alpine / Void / CRUX (start from minimal distro)
- **Alpine:** Uses musl (glibc compatibility issues), apk, OpenRC
- **Void:** Custom XBPS, runit init, good but opinionated
- **CRUX:** Minimal ports system, closest to LFS spirit
- **Verdict:** Why inherit their package manager when building our own?

### T2 SDE
- **Returning to desktop focus in 2026** (KDE Plasma, systemd, Wayland)
- **Meta-distribution build kit** — more abstraction than LFS
- **Verdict:** Worth watching, but more opinionated than LFS

### NixOS
- **Declarative, functional approach** — radical reproducibility
- **Teaches "how to declare a system" not "how Linux works"**
- **Verdict:** Fundamentally misaligned with educational philosophy

---

## Why LFS Remains the Right Choice

1. **Building our own build system and package manager** — inheriting someone else's is counterproductive
2. **PRIME DIRECTIVE demands full understanding** — LFS is the only path where you built everything
3. **Educational value is a feature** — InterGenOS teaches users; must understand the system fully
4. **Zero inherited baggage** — every decision is ours
5. **Proven path:** Milis Linux (Turkey) took exact same path: LFS → custom PM → daily-driver
6. **Actively maintained:** LFS 13.0 released March 5, 2026

## Proof of Concept: Milis Linux
- LFS-based distro from Turkey
- Started as LFS exercise, evolved to daily-driver
- Built custom package manager on top of LFS
- Proves the InterGenOS approach is viable
