# Package Repository Infrastructure Research — April 9, 2026

## Source
Research into how Linux distros host package repositories.
Concrete cost/storage/bandwidth numbers for planning InterGenOS's pkm ecosystem.

## Real-World Distro Repo Sizes

| Distro | Packages | Binary Size (amd64) |
|--------|----------|-------------------|
| Arch Linux (core+extra) | ~13,000 | ~55 GB |
| Debian (main, amd64) | ~60,000 | ~100 GB |
| Alpine Linux (amd64) | ~14,000 | ~7 GB |
| Void Linux (glibc, amd64) | ~15,000 | ~35 GB |

## InterGenOS Estimates

| Package Count | Binaries Only | + Source | + Debug |
|---------------|--------------|---------|---------|
| 500 | 1.5-3 GB | 5-10 GB | 10-20 GB |
| 1,000 | 3-5 GB | 10-20 GB | 20-40 GB |
| 2,000 | 5-10 GB | 20-40 GB | 40-80 GB |

## Bandwidth Per User

| User Type | Monthly |
|-----------|---------|
| Rolling release (daily updates) | 500 MB - 2 GB |
| Weekly updates | 300 MB - 1 GB |
| Stable release (security only) | 50-200 MB |

## Hosting Cost Comparison (40 GB storage + 2 TB egress/month)

| Provider | Monthly Cost |
|----------|-------------|
| **Cloudflare R2** | **$1-2** (free egress!) |
| Backblaze B2 + Cloudflare | ~$1 |
| Hetzner VPS (CPX21) | ~$9 (includes compute) |
| BuyVM | ~$3.50 (unmetered) |
| AWS S3 + CloudFront | ~$170 |

## Recommended Architecture

**Cloudflare R2 as package host from day one.**

Repo structure:
```
repo.intergenos.org/x86_64/
├── InterGenOS.db        (gzipped JSON index, GPG signed)
├── InterGenOS.db.sig    (GPG detached signature)
└── *.igos.tar.gz        (package archives)
```

Security chain: GPG key → signed index → SHA256 per package → verified download

## Phased Growth Plan

| Phase | Users | Storage | Bandwidth | Cost/month |
|-------|-------|---------|-----------|-----------|
| Dev | 1-5 | 5-15 GB | <10 GB | $0-5 |
| Early adopters | 10-100 | 15-40 GB | 100 GB-1 TB | $5-20 |
| Public release | 1,000+ | 40-100 GB | 1-10 TB | $25-75 |
| Growth | 10,000+ | 100-300 GB | 10-50 TB | $75-200 + mirrors |

## Model Distros

- **Void Linux (xbps-src)** — best model for from-source distro. Template-based, monorepo, chroot builds.
- **Alpine (aports)** — simplest package format, extremely efficient.
- Both use flat-file repos over HTTPS with signed indexes.

## pkm Repository Layer

Implemented in `pkm/repo.py` (512 lines):
- RepoManager: sync, search, download with SHA256 verification
- GPG signature verification on every index sync
- Dependency resolver with topological ordering
- Package cache at /var/cache/pkm/packages/
- generate_index(): build-side index generator
- sign_index(): GPG detached signature

Package format: `.igos.tar.gz` (confirmed standard across entire codebase)
