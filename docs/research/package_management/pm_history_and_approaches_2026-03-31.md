# Package Management — History, Failures, and Successes

**Date:** March 31, 2026
**Context:** Designing InterGenOS package manager

---

## LFS's Seven Documented Approaches

The official LFS book (Chapter 8.2) describes seven package management schemes:

1. **Manual Tracking** ("It's All in My Head") — No tools. Simple but unscalable.
2. **Separate Directories** — Each package in /opt/foo-1.1. Becomes unmanageable at scale.
3. **Symlink Style** — Files symlinked into /usr from separate trees (Stow, Epkg, Graft, Depot). Must trick installation scripts with DESTDIR.
4. **Timestamp-Based** — Record timestamps to track installations. Unreliable with simultaneous installs.
5. **Tracing Installation Scripts** — Monitor system calls via LD_PRELOAD/strace. Comprehensive but fragile.
6. **Package Archives** — Simulate install to separate tree, archive result (RPM, Debian, Portage model). Handles dependencies but requires metadata.
7. **User-Based Management** — Each package installed as separate user. Unique to LFS, complex.

LFS explicitly avoids recommending any particular approach.

### Critical LFS Warnings

- Shared library name changes demand immediate recompilation of dependents
- Mixed old/new library versions cause malfunctions
- File overwrites during installation risk crashing running processes
- Glibc upgrades require special procedures

---

## Successful Minimal Package Managers

### CRUX pkgutils — Simplicity by Design
- **Format:** Plain tar.gz, no metadata beyond .footprint
- **Tools:** pkgadd, pkgrm, pkginfo, pkgmk, rejmerge
- **Key feature:** NO automatic dependency resolution
- **Why it succeeds:** Users understand dependencies manually; transparency over automation

### Slackware pkgtools — Philosophy of Simplicity Over Convenience
- **Format:** Tarball with slack-desc + doinst.sh
- **Naming:** name-version-architecture-revision.tgz
- **Philosophy:** "Automated dependency resolution can lead to dependency hell; sysadmin should be in full control"
- **Community extensions:** Optional slapt-get, slackpkg for those who want auto-deps

### Alpine apk — Minimal for Containers
- **Design motivation:** Distro that fits in memory, fast initfs/tmpfs
- **Package pinning, signature verification, minimal deps**
- **Subpackaging:** Split into small packages with install_if directives
- **Result:** 5 MB base image

### Void XBPS — Built from Scratch in C
- **Atomic operations:** Every install/removal is transactional; breaks roll back
- **Test coverage:** ~200 test cases
- **Build system:** xbps-src builds in containers using Linux namespaces
- **Design goal:** Fast, simple, bug-free, featureful, portable

### Arch pacman — Simple + Rolling Release Synergy
- **Speed advantage:** Doesn't check for all breaking changes
- **Single version per lib in repos** — avoids NP-complete resolution by design
- **AUR** provides community source packages alongside official binary repos

### Nix — Purely Functional
- **Packages as pure functions; immutable after building**
- **Hash-based directories** allow multiple versions to coexist
- **Atomic upgrades/rollbacks**
- **Used by:** Mozilla, CERN, Replit, Firebase Studio

---

## Failed Approaches and Cautionary Tales

### RPM → YUM → DNF Trajectory
- RPM started simple (1995-1997), no dependency resolution
- YUM bolted on dependency resolution → 56,000+ lines, excessive memory, slow metadata
- Forced rewrite as DNF with libsolv solver in Fedora 22
- **Lesson:** Bolting dependency resolution onto a simple PM creates unfixable tech debt

### Corel Linux / Xandros
- Attempted Windows-like simplicity (1999-2001)
- Failed due to financial problems, limited package library, poor localization
- Microsoft partnership killed all Linux projects within weeks
- **Lesson:** Custom packaging doesn't help if business decisions undermine the project

### Common Failure Patterns
1. **Over-automation:** Promises auto-deps but creates maintenance burden
2. **Unfounded assumptions:** Assumes upstream publishes accurate dependency metadata
3. **Scope creep:** Start simple, then add breaking complexity
4. **Breaking changes:** Shared lib upgrades require recompiling everything; format changes require migration
5. **No reproducibility:** Different build environments produce different binaries

### Common Success Patterns
1. **Transparency:** Users understand what's happening (Slackware, CRUX, Alpine)
2. **Minimalism:** Solve one problem well
3. **Philosophy alignment:** PM design matches distro philosophy
4. **Avoid over-promising:** Don't promise what you can't deliver
5. **Community extensions:** Allow optional features without mandating them

---

## The Bootstrap Problem

How do you package-manage a system that doesn't have a package manager yet?

### Standard Solutions
- **Stage 0 Bootstrap:** Pre-built bootstrap binaries of essential tools
- **Multi-stage compiler bootstrap:** 3-4 stages, each building the next, final comparison for authenticity
- **GNU Guix approach:** Reduced bootstrap seed from 100+ MB to 357 bytes (hex0 program)

### Practical Implication for InterGenOS
The build system builds the system first (LFS-style). The package manager is built as part of the system. Once the system is bootable, the package manager manages it going forward. The build system and package manager are separate tools with complementary roles.

---

## Design Decisions for InterGenOS

Based on this research:
- **Start minimal and honest** — like CRUX/Slackware, not RPM
- **Package format:** .igos.tar.zst with metadata.json
- **Dependency tracking:** Explicit in templates, topological sort for resolution
- **Binary caching:** From day one — never rebuild GCC unnecessarily
- **Atomic operations:** Learn from XBPS — no partial installs
- **No false promises:** If auto-resolution would be unreliable, say so
- **The build system handles build-order; the package manager handles installed-system state**
