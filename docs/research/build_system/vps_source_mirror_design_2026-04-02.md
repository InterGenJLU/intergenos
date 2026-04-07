# VPS Source Mirror & Auto-Update Design — 2026-04-02

## Overview

Three components working together to make InterGenOS builds fully self-sufficient
and keep packages current without manual intervention.

---

## Component 1: Source Mirror

**URL:** `https://intergenstudios.com/intergenos/sources/`

**Structure:**
```
/intergenos/sources/
├── current/                    # Tarballs matching current package.yml versions
│   ├── binutils-2.46.0.tar.xz
│   ├── gcc-15.2.0.tar.xz
│   ├── ...
│   └── SHA256SUMS
├── latest/                     # Newer versions detected by auto-updater
│   ├── binutils-2.47.0.tar.xz
│   └── SHA256SUMS
└── updates.json                # Flags for the build system
```

**`updates.json` format:**
```json
{
  "last_check": "2026-04-02T18:00:00Z",
  "updates_available": [
    {
      "name": "binutils",
      "current": "2.46.0",
      "latest": "2.47.0",
      "url": "https://ftp.gnu.org/gnu/binutils/binutils-2.47.0.tar.xz",
      "sha256": "abc123...",
      "detected": "2026-04-02T18:00:00Z",
      "severity": "minor"
    }
  ]
}
```

**Build system integration:**
- `igos-build` can check `updates.json` and warn: "3 package updates available"
- `--check-updates` flag to show what's new
- `--use-latest` flag to build with the latest versions (for testing)
- Updates are INFORMATIONAL — the owner decides when to adopt them

---

## Component 2: Download Script (`scripts/download-sources.py`)

**Runs on the dev machine or build VM.**

```bash
# Download sources for specific tiers
python3 scripts/download-sources.py --tier core base

# Download all sources + compute/update SHA256 hashes
python3 scripts/download-sources.py --all --update-checksums

# Upload to VPS mirror
python3 scripts/download-sources.py --all --mirror-upload

# Verify local cache matches templates
python3 scripts/download-sources.py --verify

# Check for upstream updates (doesn't download, just reports)
python3 scripts/download-sources.py --check-updates
```

**Features:**
- Reads package.yml source URLs across all tiers
- Downloads to `/mnt/intergenos/build/sources/`
- Parallel downloads (configurable concurrency)
- SHA256 computation and template update
- SCP/rsync upload to VPS
- Resume support (skip existing files matching SHA256)
- Upstream version checking (see Component 3)

---

## Component 3: Auto-Update Poller (runs on VPS)

**A cron job on the VPS that periodically checks upstream sources for new versions.**

**How it works:**
1. Reads all `package.yml` templates (synced to VPS via git pull)
2. For each package, checks the upstream source URL pattern for newer versions
3. If a newer version exists:
   - Downloads it to `/intergenos/sources/latest/`
   - Computes SHA256
   - Adds entry to `updates.json`
   - Optionally sends a notification (email, webhook, etc.)
4. Does NOT modify any package.yml — updates are advisory only

**Version detection strategies:**
- **GNU mirrors:** Parse FTP directory listings (most GNU packages)
- **GitHub releases:** Use GitHub API (`/repos/owner/repo/releases/latest`)
- **PyPI:** Use PyPI JSON API
- **GNOME/freedesktop:** Parse download directories
- **Custom:** Per-package URL patterns defined in package.yml

**New package.yml field (optional):**
```yaml
upstream_check:
  type: github          # github, gnu-ftp, pypi, gnome, custom
  repo: torvalds/linux  # for github type
  pattern: "v{version}" # version tag pattern
```

**Cron schedule:** Weekly or daily, configurable. Not real-time — this is advisory.

**Safety:**
- NEVER auto-updates the build
- NEVER modifies package.yml
- Only downloads, checksums, and flags
- The owner reviews updates and decides when to adopt them
- Matches the Prime Directive: the USER is in control

---

## Component 4: Update Adoption Workflow

When the owner decides to adopt updates:

```bash
# See what's available
python3 scripts/download-sources.py --check-updates

# Output:
#   binutils: 2.46.0 → 2.47.0 (minor, detected 2026-04-02)
#   gcc: 15.2.0 → 15.3.0 (minor, detected 2026-04-05)
#   openssl: 3.6.1 → 3.6.2 (patch/security, detected 2026-04-03)

# Adopt a specific update
python3 scripts/download-sources.py --adopt binutils

# This:
# 1. Downloads the new tarball to build/sources/
# 2. Updates package.yml with new version + SHA256
# 3. Moves the old tarball to an archive
# 4. Updates the VPS mirror

# Adopt all updates
python3 scripts/download-sources.py --adopt-all

# Test build with new version before committing
python3 -m igos-build --only binutils --build --tracked
```

---

## Implementation Priority

1. **download-sources.py** — needed NOW (build self-sufficiency)
2. **VPS mirror (current/)** — needed SOON (reproducibility, upstream independence)
3. **Auto-update poller** — needed LATER (convenience, but manual checking works)
4. **Adoption workflow** — needed LATER (can be done manually until then)

---

## VPS Specs (as of 2026-04-02)

- **Host:** KnownHost, AlmaLinux 8.10 (Parallels/Virtuozzo container)
- **CPU:** 4 cores, Xeon E5-2697 v4 @ 2.30GHz
- **RAM:** 6GB
- **Disk:** 150GB (121GB free)
- **Role:** Package repository and auto-update poller ONLY
- **NOT for builds** — too slow for GCC/LLVM/WebKitGTK, not enough RAM

When InterGenOS reaches a publishable state (working desktop + AI), upgrade
the VPS and consider adding it as a CI build server. Owner knows the
KnownHost CEO personally — upgrade path is straightforward.

## VPS Requirements (current role)

- **Disk:** ~10GB for current sources, ~5GB for latest buffer = ~15GB
- **HTTP:** Already serving (intergenstudios.com)
- **Cron:** Standard cron for the poller script
- **Git:** Pull the repo periodically to stay in sync with template changes
- **Python 3:** For the poller script
