# How Linux Distributions Organize Packages Beyond the Base System

**Created:** April 5, 2026
**Purpose:** Research how major distributions categorize user-facing applications vs system infrastructure, to inform InterGenOS tier naming decisions.

---

## 1. Major Distribution Tier/Category Systems

### Arch Linux (Post-2023 Merge)

**Repository-level tiers (3 official + 1 community):**

| Repository | Purpose | Enabled by Default |
|------------|---------|-------------------|
| **core** | Minimal boot + package management (kernel, glibc, pacman, systemd, bash, filesystem) | Yes |
| **extra** | Everything else official (merged with former `community` in May 2023) | Yes |
| **multilib** | 32-bit compatibility libraries (Wine, Steam) | No |
| **AUR** | User-submitted PKGBUILDs (not pre-built, not official) | N/A (separate system) |

Before May 2023, Arch had a separate `community` repo maintained by Trusted Users. The distinction became meaningless, so they merged it into `extra`. There are no further sub-categories within `extra` -- vim, Firefox, GNOME, and LibreOffice all sit side by side.

**Testing repos:** `core-testing`, `extra-testing` (staging areas, not user-facing categories).

**Where editors live:** Vim and Emacs are both in `extra`. Not in `core`.

**Key insight:** Arch uses a flat model. There are no "application categories" at the repository level -- search and metadata handle discovery.

---

### Fedora

**Two-axis system: Repositories x Groups**

**Repositories (licensing axis):**
- `fedora` -- free software meeting Fedora Legal guidelines
- `updates` -- updates for the above
- No official non-free repo (delegated to RPM Fusion, see Section 5)

**Environment Groups (installation profiles):**
Defined in `comps.xml`. These are what the Anaconda installer shows:

| Environment Group | What It Provides |
|-------------------|------------------|
| Minimal Install | Just `@core` |
| Fedora Workstation | GNOME desktop |
| KDE Plasma Workspaces | KDE desktop |
| Xfce/LXDE/LXQt/Cinnamon/MATE Desktop | Alternative DEs |
| Basic Desktop | Minimal graphical environment |
| Development and Creative Workstation | Dev tools + desktop |
| Web Server / Infrastructure Server | Server roles |
| Fedora Custom Operating System | Bare minimum |

**Package Groups (functional bundles):**
Each environment group pulls in package groups. Groups include:

- `@core` -- essential system packages (always installed)
- `@standard` -- common CLI tools
- `@base-x` -- minimal X11/Wayland
- `@hardware-support` -- firmware, drivers
- `@fonts` -- default fonts
- `@editors` -- text editors
- `@office` / `@libreoffice` -- office suites
- `@multimedia` / `@sound-and-video` -- media tools
- `@system-tools` -- admin utilities
- `@development-tools` -- compilers, debuggers
- `@games-and-entertainment` -- games
- `@text-based-internet` -- CLI web/mail/IRC
- `@network-servers` -- httpd, etc.
- `@security-lab` -- security tools
- `@design-suite` -- graphics tools

Within each group, packages have three priority levels: **mandatory** (always installed with group), **default** (installed unless deselected), **optional** (only if explicitly chosen).

**Where editors live:** In the `@editors` group. Vim, nano, emacs are all there.

**Key insight:** Fedora's model is profile-based. The installer picks an environment, which pulls groups, which pull packages. Groups use human-readable names, not filesystem categories.

---

### Debian

**Two independent classification axes:**

**Archive Areas (licensing):**

| Area | What It Means |
|------|---------------|
| **main** | DFSG-free software (the only "real" Debian) |
| **contrib** | Free software that depends on non-free |
| **non-free** | Redistributable but not DFSG-free |
| **non-free-firmware** | Hardware firmware (added in Debian 12) |

**Package Sections (functional categories within each area):**

There are 30+ sections used in `debian/control`. The complete list:

`admin`, `cli-mono`, `comm`, `database`, `debug`, `devel`, `doc`, `editors`, `education`, `electronics`, `embedded`, `fonts`, `games`, `gnome`, `gnu-r`, `golang`, `graphics`, `hamradio`, `haskell`, `httpd`, `interpreters`, `introspection`, `java`, `javascript`, `kde`, `kernel`, `libs`, `libdevel`, `lisp`, `localization`, `mail`, `math`, `metapackages`, `misc`, `net`, `news`, `ocaml`, `oldlibs`, `otherosfs`, `perl`, `php`, `python`, `ruby`, `rust`, `science`, `shells`, `sound`, `tasks`, `tex`, `text`, `utils`, `vcs`, `video`, `web`, `x11`, `xfce`, `zope`

**Package Priorities (importance):**

| Priority | Meaning |
|----------|---------|
| **required** | System won't function without it |
| **important** | Expected on any Unix-like system |
| **standard** | Default install includes these |
| **optional** | User-chosen software (the default for most packages) |
| **extra** | Specialized or conflicting (deprecated; now treated as optional) |

**Where editors live:** Section `editors`. Both vim and emacs are in the `editors` section, priority `optional`, area `main`.

**Key insight:** Debian separates licensing (areas) from function (sections) from importance (priorities). Three independent axes. Sections are fine-grained and specific.

---

### Ubuntu

**Archive Components (Debian-derived but different):**

| Component | Maintained By | License |
|-----------|---------------|---------|
| **main** | Canonical | Free |
| **restricted** | Canonical | Proprietary (drivers) |
| **universe** | Community | Free (not Canonical-supported) |
| **multiverse** | Community | Non-free |

Ubuntu inherits Debian's section names (`editors`, `utils`, etc.) but uses its own component model for the support/licensing axis. The key difference from Debian: Ubuntu distinguishes "who supports it" (Canonical vs community) rather than just licensing.

**Key insight:** Ubuntu's model emphasizes the support boundary more than Debian's does.

---

### openSUSE

**Pattern-based system (functional role bundles):**

Patterns are the primary organizational unit, similar to Fedora's groups but more granular. Known patterns include:

| Pattern Category | Example Names |
|-----------------|---------------|
| Base system | `base`, `enhanced_base`, `console`, `minimal_base` |
| Desktop environments | `gnome`, `gnome_basis`, `kde`, `kde_plasma`, `xfce`, `lxqt` |
| Desktop function | `multimedia`, `office`, `imaging`, `games`, `fonts` |
| Development | `devel_basis`, `devel_C_C++`, `devel_gnome`, `devel_kde`, `devel_java`, `devel_kernel`, `devel_perl`, `devel_python` |
| Server | `dhcp_dns_server`, `file_server`, `lamp_server`, `mail_server` |
| Management | `sw_management` (software management tools) |

Patterns are installed with `zypper in -t pattern <name>`.

**Key insight:** openSUSE's patterns are task-oriented, not package-type-oriented. "multimedia" includes players AND libraries AND codecs. This is the most user-intent-focused model.

---

### Gentoo

**Fine-grained category/package naming:**

Gentoo uses ~170 categories in a `prefix-suffix` naming convention. Every package lives at `category/package-name`. Major category prefixes:

| Prefix | Meaning | Examples |
|--------|---------|----------|
| `app-` | Applications | `app-editors`, `app-office`, `app-misc`, `app-shells`, `app-admin`, `app-crypt`, `app-text`, `app-arch`, `app-backup` |
| `dev-` | Development | `dev-libs`, `dev-lang`, `dev-python`, `dev-java`, `dev-util`, `dev-vcs`, `dev-db`, `dev-build` |
| `sys-` | System | `sys-apps`, `sys-libs`, `sys-fs`, `sys-kernel`, `sys-process`, `sys-boot`, `sys-auth`, `sys-devel` |
| `net-` | Networking | `net-misc`, `net-libs`, `net-dns`, `net-firewall`, `net-fs`, `net-im`, `net-vpn`, `net-wireless` |
| `x11-` | X11 specific | `x11-apps`, `x11-libs`, `x11-misc`, `x11-terms`, `x11-themes`, `x11-wm` |
| `media-` | Multimedia | `media-libs`, `media-sound`, `media-video`, `media-gfx`, `media-fonts` |
| `www-` | Web | `www-client`, `www-apps`, `www-servers` |
| `gnome-` | GNOME | `gnome-base`, `gnome-extra` |
| `kde-` | KDE | `kde-apps`, `kde-frameworks`, `kde-plasma` |
| `gui-` | GUI (Wayland-era) | `gui-apps`, `gui-libs`, `gui-wm` |
| `games-` | Games | `games-action`, `games-board`, `games-rpg`, etc. |
| `sci-` | Science | `sci-libs`, `sci-mathematics`, `sci-physics` |
| `mail-` | Mail | `mail-client`, `mail-mta`, `mail-filter` |
| `sec-` | Security | `sec-keys`, `sec-policy` |

**Where editors live:** `app-editors/vim`, `app-editors/emacs`, `app-editors/nano`.

**Key insight:** Gentoo's model is the most taxonomic -- every package has a precise category based on what it IS, not what role it serves. The `gui-apps` category (added relatively recently) is notable as a Wayland-era addition separate from `x11-apps`.

---

### Void Linux

**Flat repository with subrepo overlays:**

| Repository | Purpose |
|------------|---------|
| **main** | All official free packages (one flat pool, no sub-categories) |
| **nonfree** | Packages with non-free licenses |
| **multilib** | 32-bit compatibility (glibc only) |
| **multilib/nonfree** | Non-free 32-bit packages |
| **debug** | Debug symbols |

Source templates live in a flat `srcpkgs/` directory with no categorization. There are no sections, groups, or patterns -- just a single namespace of ~15,000+ templates.

**Key insight:** Void proves you can run a successful distribution with zero package categorization. Search is the discovery mechanism.

---

### Alpine Linux

**Three-tier repository model:**

| Repository | Maintainer | Support | Available On |
|------------|-----------|---------|-------------|
| **main** | Core team | Full support, timely updates | All branches |
| **community** | Contributors + core review | 6 months after release | All branches |
| **testing** | Anyone | None (staging only) | `edge` branch only |

Packages flow: `testing` -> `community` -> (rarely) `main`.

No sub-categories within repositories. The model is purely about trust/support level.

**Key insight:** Alpine's model is a maturity pipeline. A package graduates from testing to community to (sometimes) main based on quality and importance, not function.

---

### NixOS

**Flat attribute namespace:**

Nixpkgs (~120,000+ packages) uses a single attribute set. Packages are referenced by attribute path: `pkgs.vim`, `pkgs.firefox`, `pkgs.gnome.nautilus`. The source tree uses directory categories under `pkgs/`:

```
pkgs/
  applications/    # GUI applications
  build-support/   # Build infrastructure
  data/            # Data packages (fonts, etc.)
  desktops/        # Desktop environments
  development/     # Compilers, libraries, tools
  games/           # Games
  misc/            # Miscellaneous
  os-specific/     # OS-specific packages
  servers/         # Server software
  shells/          # Shell programs
  tools/           # CLI tools and utilities
  top-level/       # Package set aggregation
```

But these directories are just for source organization. Users never see them -- they interact with the flat `pkgs.*` namespace.

**Key insight:** NixOS proves that source organization and user-facing categorization can be completely decoupled.

---

## 2. How LFS-Based Projects Handle This

### BLFS (Beyond Linux From Scratch)

BLFS organizes into 13 Parts (with chapters beneath):

| Part | Name | Content |
|------|------|---------|
| I | Introduction | Welcome, important info |
| II | Post LFS Configuration and Extra Software | After LFS, security, filesystems, text editors, shells, virtualization |
| III | General Libraries and Utilities | Graphics/font libs, general libs, system utilities, programming |
| IV | Networking | Connecting, programs, utilities, libraries, text web browsers |
| V | Servers | Major servers, mail, databases, other servers |
| VI | Graphical Components | Display managers, graphical environments, graphical env libs, window managers, icons |
| VII | KDE | KDE introduction, frameworks, applications |
| VIII | GNOME | GNOME libraries and desktop |
| IX | Xfce | Xfce desktop and applications |
| X | LXQt | LXQt desktop and applications |
| XI | X Software | Office programs, graphical web browsers, other X-based programs |
| XII | Multimedia | Libraries, audio, video, CD/DVD |
| XIII | Printing, Scanning and Typesetting | Printing, scanning, SGML, XML, PostScript, typesetting |

**Where editors live:** Part II, Chapter 6 "Text Editors" -- alongside security, filesystems, and shells. BLFS treats editors as post-LFS system configuration, not as "applications."

**Key insight:** BLFS uses a pedagogical ordering (what you build next), not a functional categorization. It's a build sequence, not a package manager taxonomy.

### ALFS (Automated Linux From Scratch) / jhalfs

ALFS/jhalfs simply automates the LFS and BLFS book instructions. It has no independent tier structure -- it follows the book chapter ordering. The BLFS extension in jhalfs can resolve dependencies and build packages in order, but doesn't add categorization beyond what BLFS provides.

### LFS Derivatives

- **Cucumber Linux** -- Uses Slackware-style categories (a, ap, d, l, n, x, xap, etc.)
- **GLFS (Gaming Linux From Scratch)** -- Extends BLFS with gaming packages (Steam, Wine), no new tier structure
- **CLFS (Cross-Compiled LFS)** -- Architecture variants, same build sequence approach

No LFS derivative I found introduces a novel package categorization system. Most either follow BLFS structure or adopt an existing distribution's approach.

---

## 3. Where Editors/IDEs Live Across Distributions

| Distribution | Vim Location | Emacs Location | VS Code Location |
|-------------|-------------|----------------|-----------------|
| Arch | `extra` repo | `extra` repo | AUR (or Microsoft repo) |
| Fedora | `@editors` group | `@editors` group | Not in repos (Microsoft repo or Flatpak) |
| Debian/Ubuntu | Section `editors` | Section `editors` | Not in repos (Microsoft repo or Snap) |
| openSUSE | No specific pattern | No specific pattern | Not in repos (Microsoft repo or Flatpak) |
| Gentoo | `app-editors/vim` | `app-editors/emacs` | Not in tree (overlay or Flatpak) |
| Void | `srcpkgs/vim` (main) | `srcpkgs/emacs` (main) | Not in repos |
| Alpine | `main` repo | `community` repo | Not in repos |
| BLFS | Ch. 6 "Text Editors" | Ch. 6 "Text Editors" | Not covered |

**The universal pattern:** Traditional editors (vim, emacs, nano) are always in the "editors" category wherever one exists. VS Code is almost never in official repos because it's either proprietary (the Microsoft build) or requires Electron (which is complex to package). Most distributions handle VS Code through:
- Microsoft's own apt/yum repository
- Flatpak/Flathub
- Snap
- AUR PKGBUILDs
- The open-source `code-oss` / `vscodium` builds

---

## 4. The "Apps" Naming Convention

### Do Any Distributions Use "apps" As a Tier/Category Name?

**Yes, several:**

| Distribution | Usage |
|-------------|-------|
| Gentoo | `x11-apps`, `gui-apps`, `www-apps`, `gnustep-apps`, `kde-apps` -- as category suffixes |
| BLFS | "Xfce Applications", "LXQt Applications", "Other X-based Programs" -- in chapter titles |
| Fedora | "MATE Applications" -- as a group name |
| NixOS | `pkgs/applications/` -- as a source directory |

However, **no major distribution uses bare "apps" as a top-level tier name**.

### Most Common Names for Optional User-Facing Applications

Ranked by frequency across distributions:

1. **"optional"** -- Debian/Ubuntu priority level (the default for most packages)
2. **"extra"** -- Arch's catch-all (everything not in core), Debian's deprecated priority
3. **"universe"** -- Ubuntu's community-maintained pool
4. **"community"** -- Alpine's contributed packages
5. **Pattern/group names by function** -- Fedora, openSUSE (`@editors`, `@multimedia`, `@office`, `multimedia`, `office`)
6. **Category prefixes** -- Gentoo (`app-*`, `gui-*`, `www-*`)

**There is no industry-standard single word for "optional user-facing applications."** The closest conventions:
- Debian calls them priority `optional` (but that includes libraries too)
- Arch just puts them in `extra` (alongside everything non-core)
- Fedora groups them by function (`@editors`, `@office`, etc.)
- Gentoo categorizes by type (`app-editors`, `app-office`)

---

## 5. How Distributions Handle Proprietary Software

### Approach 1: Separate Repositories (Most Common)

| Distribution | Free Repo | Non-Free Repo | Who Maintains Non-Free |
|-------------|----------|---------------|----------------------|
| Debian | `main` | `contrib` + `non-free` + `non-free-firmware` | Debian project (but "not part of Debian") |
| Ubuntu | `main` + `universe` | `restricted` + `multiverse` | Canonical / community |
| Fedora | `fedora` | None official | RPM Fusion (third-party, `free` + `nonfree`) |
| openSUSE | Official repos | Packman (third-party) | Community |
| Void | `main` | `nonfree` | Void team |
| Alpine | `main` + `community` | No non-free repo | N/A |

### Approach 2: Download Helpers / Fetch Scripts

- **Arch AUR:** PKGBUILDs that download proprietary binaries and repackage them (e.g., `spotify`, `google-chrome`, `zoom`). The PKGBUILD is open source; the downloaded binary is not.
- **Chaotic-AUR:** Pre-builds AUR packages (including proprietary fetch scripts) so users don't have to compile.
- **Gentoo `fetch` restriction:** Ebuilds can require manual download of source/binary before building (e.g., Oracle JDK). The ebuild is open; the content requires user agreement.

### Approach 3: Universal Package Formats (Increasingly Common)

- **Flatpak/Flathub:** Sandboxed apps. Fedora now ships Flathub enabled (filtered mode). Contains both free and proprietary.
- **Snap/Snapcraft:** Ubuntu's approach. Canonical-controlled store. Mixed licensing.
- **AppImage:** Self-contained apps. No repo -- distributed by app developers.

### Approach 4: Licensing Metadata

- **Debian:** `non-free` area flag in `debian/control`
- **Fedora:** License field in spec file; automated license auditing
- **Gentoo:** `LICENSE` variable in ebuild + `ACCEPT_LICENSE` user setting. Fine-grained: users can accept specific licenses (`ACCEPT_LICENSE="google-chrome"`)
- **Void:** `restricted` repository flag; `license` field in template

**Key insight:** The trend is toward separating proprietary software into distinct repositories or delegating it to universal formats (Flatpak). No major distribution mixes free and proprietary in the same tier without flagging it.

---

## 6. Summary: Organizational Models

There are really only a few distinct organizational philosophies:

| Model | Used By | Description |
|-------|---------|-------------|
| **Flat pool** | Arch, Void | Everything in one bucket (core/extra or just main). Search handles discovery. |
| **Functional groups/patterns** | Fedora, openSUSE | Packages grouped by what they DO (office, multimedia, development) |
| **Taxonomic categories** | Gentoo, Debian sections | Packages classified by what they ARE (app-editors, libs, net-misc) |
| **Support tiers** | Alpine, Ubuntu | Packages classified by who maintains/supports them |
| **Build sequence** | BLFS, LFS derivatives | Packages ordered by when you build them in the chain |

Most distributions combine 2-3 of these. Debian uses taxonomic sections + support areas + priority levels. Fedora uses functional groups + licensing repos. Ubuntu uses support tiers + inherited Debian sections.

---

## 7. Implications for InterGenOS

InterGenOS currently has: `toolchain`, `core`, `base`, `desktop`.

The question is what to call a tier for user-facing applications (editors, IDEs, browsers, etc.) that don't fit the current tiers.

**What other distributions do with this class of software:**
- Arch: dumps it all in `extra`
- Fedora: puts it in functional groups (`@editors`, `@office`)
- Debian: uses fine-grained sections (`editors`, `web`, `utils`)
- Gentoo: uses precise categories (`app-editors`, `www-client`)
- openSUSE: bundles into patterns (`office`, `multimedia`)
- BLFS: spreads across Parts by function

**No distribution uses "apps" as a standalone top-level tier.** The closest are:
- Gentoo's `app-*` prefix (but always with a suffix)
- NixOS's `pkgs/applications/` directory (internal, not user-facing)

**Common alternatives for this tier concept:**
- `extra` (Arch model -- "everything beyond core")
- `optional` (Debian model -- priority-based)
- `applications` (descriptive but verbose)
- Function-specific tiers: `editors`, `office`, `multimedia` (but this fragments)
