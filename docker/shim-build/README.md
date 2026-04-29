# InterGenOS shim-review Dockerfile — reproducibility skeleton

**B2 deliverable** | Author: DeepSeek V4 (PRO) | Date: 2026-04-28

## Purpose

Byte-for-byte reproducible shim build with embedded vendor certificate. The
Dockerfile produces a tarball whose sha256sum MUST match across all build
hosts — this is the shim-review acceptance gate (non-reproducible = PR stalls).

Reference: `docs/research/installer/ms_shim_sponsorship_2026-04-18.md` §9 Step 2.

## Architecture

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Base image | `debian:bookworm-slim@sha256:5a2a...` | Narrow package set, multi-year ABI stability, Debian-native reproducibility tooling |
| Shim source | `rhboot/shim` v16.1, tag `dad4f207` | Exact tag SHA pinned; deterministic checkout |
| Vendor cert | Placeholder self-signed | Real cert from Thursday ceremony (C6) plugs in via build arg |
| Tar | `--sort=name --owner=0 --group=0 --mtime=@$SOURCE_DATE_EPOCH` | Deterministic archive format |

## Reproducibility anchors

1. **Pinned base image digest** — `FROM debian:bookworm-slim@sha256:...` not `FROM debian:bookworm-slim`
2. **`SOURCE_DATE_EPOCH`** — fixed Unix timestamp; all file timestamps derive from it
3. **`LANG=C.UTF-8`, `TZ=UTC`** — no locale-dependent behavior in build tools
4. **Fixed shim source SHA** — git clone at exact tag, commit SHA recorded in output
5. **Deterministic tar** — sort order + ownership + mtime all fixed
6. **Fixed vendor cert** — placeholder with fixed serial/subject; swap post-ceremony

## Usage

```bash
cd docker/shim-build

# Build with placeholder cert
docker build \
  --build-arg SOURCE_DATE_EPOCH=$(date -d '2026-04-28 00:00:00 UTC' +%s) \
  -t intergenos-shim-builder .

# Extract output
mkdir -p out
docker run --rm -v $(pwd)/out:/out intergenos-shim-builder

# Verify reproducibility
sha256sum out/intergenos-shim-*.tar
```

## Post-ceremony cert swap (Thursday 2026-04-30)

After the Nitrokey ceremony produces the real vendor certificate (C6 output):

```bash
docker build \
  --build-arg SOURCE_DATE_EPOCH=$(date -d '2026-04-28 00:00:00 UTC' +%s) \
  --build-arg VENDOR_CERT_SUBJECT="/C=US/O=InterGenOS/CN=InterGenOS Secure Boot CA" \
  -f Dockerfile.cert \
  -t intergenos-shim-builder .
```

Where `Dockerfile.cert` replaces the `openssl req` step with a `COPY` of the
ceremony-generated certificate files.

## Multi-host verification

Build on all three hosts, compare sha256sum:

```
/mnt/intergenos-deepseek (Ubuntu 24.04 desktop, x86_64)
Ubuntu 24.04 server (separate bare metal)
Windows-claude clone (Zephyrus M16, Docker Desktop)
```

All MUST produce identical `intergenos-shim-16.1.tar.sha256`.

## Known limitations (skeleton phase)

1. **Makefile variables need validation** — `VENDOR_CERT_FILE` and `ENABLE_SBSIGN` are
   based on shim 16.1 Makefile conventions; confirm with actual build test.
2. **gnu-efi version not pinned** — Debian stable's gnu-efi is fixed, but not
   digest-pinned in apt. For stricter reproducibility, consider `snapshot.debian.org`.
3. **Placeholder cert subject** — the real cert subject may differ from the
   placeholder; this is fine since the cert swap is a one-time step post-ceremony.
4. **No shim SBAT generation yet** — SBAT data is embedded at build time; will need
   InterGenOS-specific SBAT entries before final shim-review submission.
