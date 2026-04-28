# IGR — InterGenRepo Specification

**Date:** April 10, 2026  
**Status:** APPROVED architecture — ready for implementation planning  
**Version:** 1.0

---

## 1. What IGR Is

IGR (InterGenRepo) is the package repository infrastructure for InterGenOS. It provides two tiers of package availability through a single interface:

- **igr/core** — Pre-built, signed binary packages maintained by the InterGenOS team. Security-updated. Ships on the ISO. The "it Just Works" tier.
- **igr/community** — User-submitted package templates (package.yml + build.sh). Reviewed for correctness and security. Built locally by the user or by InterGen on their behalf. The AUR equivalent.

### Design Philosophy

> Don't compete on package count. Provide the vehicles that unlock ecosystems, then let InterGen + community templates handle the rest.

The language toolchain strategy means `igr/core` at ~600-650 packages provides *access* to millions more through native package managers (cargo, pip, npm, go install, gem). InterGen bridges the gap between "package not in IGR" and "user has it installed."

---

## 2. Repository Tiers

### igr/core — Official Repository

**What belongs here:** Anything a user would reasonably expect to Just Work out of the box.

**Content:**

| Category | Packages | Count | Status |
|----------|----------|-------|--------|
| System foundation | LFS 13.0 + BLFS core | ~280 | Done |
| Base CLI tools | htop, rsync, screen, strace, etc. | ~30 | Done |
| Desktop | GNOME on Wayland, GStreamer, PipeWire | ~200 | Done |
| Applications | GIMP, Inkscape, Thunderbird, LibreOffice, mpv, Celluloid | ~60 | Done |
| Language toolchains | Rust (cargo), Python (pip), Node.js (npm) | ~3 | Done |
| Language toolchains | Go, Ruby | ~2 | Needed |
| GPU — AMD | Mesa, libva, Vulkan loader, drivers | ~10 | Done |
| GPU — NVIDIA | nvidia-driver, nvidia-utils, nvidia-settings | ~3 | Needed |
| Shells | bash, zsh, fish | ~3 | Need zsh, fish |
| Networking | WireGuard, OpenVPN | ~2 | Needed |
| Containers | Docker or Podman + dependencies | ~5 | Needed |
| IDEs/Editors | Neovim, Code-OSS helper | ~2 | Need Neovim |
| Virtualization | QEMU, libvirt, virt-manager | ~5 | Needed |
| Fonts | Noto, DejaVu, Liberation (complete set) | ~5 | Partial |
| AI | llama-cpp, InterGen | ~2 | Phase 2 |
| LSB compat | libxcrypt-compat, ncurses-compat | ~2 | Done |
| **Total** | | **~615-650** | |

**Quality guarantees:**
- Every package built from source by our build system
- SHA256-verified source tarballs
- Security review on all patches
- Pre-built binary archives (.igos.tar.gz), signed with InterGenOS GPG key
- Security updates tracked and pushed via CI/CD pipeline
- pkm manifests included — full package tracking from install

**The decision filter:** "Would a user reasonably expect this to Just Work out of the box?" If yes → core. If it's specialized → community.

### igr/community — Community Templates

**What belongs here:** Everything else. Specialty tools, niche libraries, alternative applications, user-requested packages.

**Content:** Package templates (package.yml + build.sh) — the same format as our build system. Two files per package, submitted via pull request to the `igr-community` GitHub repository.

**Quality guarantees:**
- Templates reviewed by maintainers before merge (correctness + security)
- SHA256 checksums on source URLs required
- Build tested on at least one InterGenOS system before merge (CI)
- **No guarantee of runtime behavior** — "builds successfully" is the bar, not "works perfectly for every use case"
- No pre-built binaries — user builds locally

**Contribution flow:**
```
1. User creates package.yml + build.sh (or InterGen generates them)
2. User submits PR to igr-community repo
3. CI runs: template validation + test build on InterGenOS
4. Maintainer reviews: correctness, security, licensing
5. Merge → template available via `pkm search` and InterGen
```

---

## 3. Package Format

### Binary Packages (igr/core)

Format: `.igos.tar.gz` — what pkm already produces during tracked builds.

Contents:
```
package-name-version.igos.tar.gz
├── .PKGINFO                    # name, version, description, deps, build date
├── .MANIFEST                   # complete file list with checksums
├── usr/
│   ├── bin/
│   ├── lib/
│   ├── share/
│   └── ...
└── etc/                        # config files (if any)
```

Signature: `.igos.tar.gz.sig` — GPG detached signature, verified by pkm before install.

### Template Packages (igr/community)

Format: Directory in the igr-community Git repo.

```
igr-community/
├── docker/
│   ├── package.yml             # metadata, source URL, SHA256, dependencies
│   └── build.sh                # build instructions
├── neofetch/
│   ├── package.yml
│   └── build.sh
└── ...
```

Same format as `/mnt/intergenos/packages/`. No new tooling needed — `igos-build.py --package <name>` builds them directly.

---

## 4. Repository Infrastructure

### Server Side (VPS at origin.intergenstudios.com)

```
igr.intergenstudios.com/
├── core/
│   ├── x86_64/
│   │   ├── packages.db          # Package database (JSON or SQLite)
│   │   ├── packages.db.sig      # Signed database
│   │   ├── gimp-3.0.6-1.igos.tar.gz
│   │   ├── gimp-3.0.6-1.igos.tar.gz.sig
│   │   ├── thunderbird-140.8.0esr-1.igos.tar.gz
│   │   ├── thunderbird-140.8.0esr-1.igos.tar.gz.sig
│   │   └── ...
│   └── sources/                 # Source tarballs mirror (optional, for rebuild)
├── community/
│   └── templates.db             # Index of available community templates
└── keys/
    └── intergenos-signing.gpg   # Public key for verification
```

### Client Side (pkm)

pkm configuration at `/etc/pkm/repos.conf`:
```yaml
repos:
  core:
    url: https://igr.intergenstudios.com/core/x86_64
    type: binary
    signed: true
    keyring: /etc/pkm/trusted.d/intergenos-signing.gpg
  community:
    url: https://github.com/InterGenOS/igr-community
    type: templates
    signed: false  # templates are Git-tracked, not GPG-signed
```

### pkm Commands

```bash
# Core repo (pre-built binaries)
pkm sync                        # refresh package database from igr/core
pkm install <package>           # download + verify sig + install binary
pkm update                      # check for updates, download + install
pkm update --security           # security updates only
pkm search <query>              # search both core and community

# Community repo (build from template)
pkm build <package>             # fetch template, resolve deps, build, install
pkm build <package> --dry-run   # show what would be built

# Common
pkm remove <package>            # uninstall (already implemented)
pkm list                        # list installed packages (already implemented)
pkm info <package>              # show package details
pkm verify                      # verify installed package integrity
```

---

## 5. Security Model

### Security Compliance

| Layer | Protection |
|-------|-----------|
| Source tarballs | SHA256 in package.yml, verified before extraction |
| Binary packages | GPG-signed .igos.tar.gz.sig, verified by pkm before install |
| Package database | GPG-signed packages.db.sig, verified on sync |
| Community templates | Reviewed by maintainers, CI-tested, Git-auditable |
| Transport | HTTPS only (TLS certificate validation) |
| InterGen builds | InterGen asks user permission before building community packages |

### Key Management

- **Signing key:** RSA 4096-bit, stored offline on owner's hardware
- **CI signing:** Subkey with expiry, used by automated build pipeline
- **User trust:** Public key ships on the ISO at `/etc/pkm/trusted.d/`
- **Key rotation:** Subkeys rotated annually, master key kept offline

### Community Template Review (security filter)

Before any community template is merged:
1. **Source URL must be HTTPS** from a recognized upstream (no random mirrors)
2. **SHA256 must be verified** against upstream's published checksum
3. **build.sh audited** for command injection, network access during build, unsafe operations
4. **No pre-built binaries** in templates — everything builds from source
5. **License must be specified** and compatible with redistribution
6. **CI must build successfully** on a clean InterGenOS system

---

## 6. CI/CD Pipeline

### Security Update Automation

```
Cron (daily)
  → Monitor upstream security advisories (CVE feeds, distro-watch)
  → Compare against igr/core package versions
  → If update available:
    1. Download new source tarball
    2. Verify SHA256 against upstream
    3. Update package.yml with new version + hash
    4. Build in clean chroot
    5. Run test suite (if package has one)
    6. Sign with CI subkey
    7. Push to igr/core repository
    8. Notify maintainer (email/webhook)
```

### New Package Build Pipeline

```
Maintainer pushes template to intergenos repo
  → CI triggers
  → Clean chroot build (same as our current build VM)
  → Package tracking (manifest + archive)
  → Sign with CI subkey
  → Push .igos.tar.gz + .sig to igr/core
  → Update packages.db + sign
```

### Community Template CI

```
PR submitted to igr-community
  → CI triggers
  → Validate package.yml (schema, SHA256 present, deps resolvable)
  → Attempt build on InterGenOS
  → Report build success/failure on PR
  → Maintainer reviews + merges (or requests changes)
```

---

## 7. InterGen Integration

InterGen is the bridge between "package not available" and "package installed." This is the key differentiator.

### Scenario 1: Package in igr/core
```
User: "Install Docker"
InterGen: "Docker 27.5.1 is available. Install it?"
User: "Yes"
InterGen: runs `pkm install docker`
InterGen: "Docker installed. Want me to enable the service?"
```

### Scenario 2: Package in igr/community
```
User: "I need Neofetch"
InterGen: "Neofetch isn't in the official repo, but there's a 
           community template. I can build it for you — takes
           about 30 seconds. Shall I?"
User: "Yes"
InterGen: runs `pkm build neofetch`, monitors output
InterGen: "Neofetch installed. Try running `neofetch`."
```

### Scenario 3: Package not in IGR at all
```
User: "I need some-obscure-tool"
InterGen: "That's not in IGR yet. I can try to create a template
           for it if you'd like — I'll need to research the build
           process. Or you can check if it's available via pip/cargo/npm."
User: "Try creating a template"
InterGen: researches upstream, generates package.yml + build.sh,
          attempts build, reports result
InterGen: "Built successfully. Want me to submit it to the 
           community repo so others can use it too?"
```

### Scenario 4: Security update available
```
InterGen: "A security update is available for libpng (CVE-2026-XXXX).
           Update now?"
User: "Yes"
InterGen: runs `pkm update libpng`
InterGen: "Updated. The vulnerability was a buffer overflow in 
           APNG handling — now patched."
```

---

## 8. The Ecosystem Multiplier Strategy

The language toolchains in igr/core don't just add 5 packages — they unlock entire ecosystems:

| Toolchain | Registry | Available Packages | Install Command |
|-----------|----------|-------------------|-----------------|
| Rust | crates.io | ~170,000 | `cargo install <name>` |
| Python | PyPI | ~600,000 | `pip install <name>` |
| Node.js | npm | ~2,500,000 | `npm install -g <name>` |
| Go | proxy.golang.org | ~1,000,000+ | `go install <name>@latest` |
| Ruby | RubyGems | ~180,000 | `gem install <name>` |

InterGen knows about these. When a user asks for a tool that's available via a language package manager, InterGen can suggest the appropriate method:

```
User: "I need a JSON formatter"
InterGen: "jq is available in igr/core (system package).
           There's also fx (Go), jless (Rust), and python-json-tool.
           jq is the standard choice — install it?"
```

---

## 9. Migration Path

### Phase 1: Foundation (current)
- Build all core packages in the chroot as we do now
- Package archives (.igos.tar.gz) already created by tracked builds
- pkm already has install, remove, query, verify

### Phase 2: Local Repository
- Set up a local directory structure matching the repo format
- Generate packages.db from our built archives
- Implement `pkm sync` and `pkm install` from local repo
- Test the full flow: sync → search → install → verify

### Phase 3: Remote Repository
- Stand up igr.intergenstudios.com on the VPS
- Upload packages + signatures
- Implement GPG verification in pkm
- Configure pkm on the ISO to point to the remote repo

### Phase 4: Community Templates
- Create igr-community GitHub repo
- Implement `pkm build` (calls igos-build.py --package)
- Set up CI for template validation + test builds
- Accept first community contributions

### Phase 5: InterGen Integration
- InterGen queries pkm for package availability
- InterGen can run pkm install/build with user confirmation
- InterGen can generate templates for unknown packages
- InterGen can submit templates to community repo

---

## 10. Naming and Branding

| Item | Value |
|------|-------|
| Repository name | IGR (InterGenRepo) |
| Domain | igr.intergenstudios.com |
| CLI command | `pkm` (IGR is the repo, pkm is the tool) |
| Official repo | igr/core |
| Community repo | igr/community |
| GitHub org | github.com/InterGenOS/igr-community |
| Package extension | .igos.tar.gz |
| Signature extension | .igos.tar.gz.sig |
| Config file | /etc/pkm/repos.conf |

---

## 11. What Needs Building (prioritized)

| Component | Effort | Phase | Notes |
|-----------|--------|-------|-------|
| `pkm sync` — refresh DB from repo | Medium | 2 | Download + parse packages.db |
| `pkm install` from repo | Medium | 2 | Download + verify sig + extract |
| `pkm update` — check + apply updates | Medium | 2 | Compare installed vs available |
| packages.db generation script | Small | 2 | Walk archives, emit JSON/SQLite |
| GPG signing integration | Medium | 3 | Sign archives + db during build |
| GPG verification in pkm | Medium | 3 | Verify before install |
| VPS repo hosting (Apache/nginx) | Small | 3 | Static file serving |
| `pkm build` from template | Medium | 4 | Wraps igos-build.py --package |
| igr-community GitHub repo | Small | 4 | Repo + CI + contribution guide |
| CI template validation | Medium | 4 | Schema check + test build |
| Security update cron watcher | Large | 3 | CVE monitoring + auto-rebuild |
| InterGen pkm integration | Medium | 5 | Query, install, build via InterGen |
| InterGen template generation | Large | 5 | AI-generated package.yml + build.sh |
