# InterGenOS shim-review Dockerfile — reproducible build

**B2 deliverable** | Initial draft 2026-04-28; rewritten 2026-05-05 for ceremony-v2 cert + architectural shift to external signing.

## Purpose

Byte-for-byte reproducible build of an UNSIGNED shim EFI binary with the
InterGenOS Secure Boot CA vendor certificate embedded. Final signing happens
outside the container, on the workstation, against Nitrokey #1 PIV slot 9c
(see `scripts/sign-shim.sh`) — the private key is hardware-bound and cannot
be copied into a container.

The reproducibility property: the output tarball's sha256sum MUST match
across all build hosts. This is the shim-review acceptance gate.

Reference: `docs/research/installer/ms_shim_sponsorship_2026-04-18.md` §9 Step 2.

## Architecture

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Base image | `debian:bookworm-slim@sha256:5a2a...` | Narrow package set, multi-year ABI stability, Debian-native reproducibility tooling |
| Shim source | `rhboot/shim` v16.1, commit `afc49558` (pinned) | Tag + exact commit SHA pinned; build asserts SHA match and exits non-zero on mismatch (L3 fix) |
| Vendor cert | Real ceremony-v2 cert (committed at `vendor-cert/`) | DER fingerprint `7B:8F:21:50:...:C8:76`; private half on NK#1 PIV slot 9c |
| SBAT vendor entry | `sbat/sbat.intergenos.csv` (committed) | Concatenated to embedded SBAT section via shim's `VENDOR_SBATS` Makefile glob |
| Build parallelism | `make -j1` | L1 fix — eliminates thread-race ordering as a reproducibility leak |
| Tar | `--sort=name --owner=0 --group=0 --mtime=@$SOURCE_DATE_EPOCH` | Deterministic archive format |
| In-container signing | DROPPED | Private key is hardware-bound to NK#1 PIV; signing happens externally |

## Reproducibility anchors

1. **Pinned base image digest** — `FROM debian:bookworm-slim@sha256:...`, not `FROM debian:bookworm-slim`
2. **`SOURCE_DATE_EPOCH`** — fixed Unix timestamp; all file timestamps derive from it
3. **`LANG=C.UTF-8`, `TZ=UTC`** — no locale-dependent behavior in build tools
4. **Fixed shim commit SHA** — clone at tag, then assert HEAD matches the commit pin (L3)
5. **Serial build** — `make -j1` eliminates thread-race ordering (L1)
6. **Deterministic tar** — sort order + ownership + mtime all fixed
7. **Real vendor cert + SBAT entry committed in repo** — no out-of-band setup; deterministic input

**Outstanding leaks** (tracked separately):
- **L2 (apt versions)**: package versions in `apt-get install` are not pinned. Different `apt-get update` timestamps will pull different gcc-minor / libc / binutils. Fix candidate: `snapshot.debian.org` timestamp in `sources.list`. Required for full multi-host reproducibility.
- **Harness graduation**: `scripts/verify-b2-reproducibility.sh` lives in DS's research doc (`b2_reproducibility_harness_2026-05-01.md`); not yet graduated to repo.

## Usage

```bash
# From the repo root
docker build \
  --build-arg SOURCE_DATE_EPOCH=1746489600 \
  -t intergenos-shim-builder \
  -f docker/shim-build/Dockerfile .

# Extract output
mkdir -p build-output
docker run --rm -v $(pwd)/build-output:/out intergenos-shim-builder

# Verify reproducibility (hash should be deterministic across hosts)
sha256sum build-output/intergenos-shim-16.1.tar
```

## Workstation-side signing (NK#1)

After the container produces the unsigned `shimx64.efi`, sign it on the
workstation with NK#1 plugged in:

```bash
scripts/sign-shim.sh build-output/shimx64.efi build-output/shimx64.efi.signed
```

The script uses OpenSC's PKCS#11 module + sbsign's pkcs11 engine. NK#1's
PIV slot 9c is the signing key; you'll touch the token to authorize each
sign operation (UIF policy enforced on-card).

## Multi-host verification

After L2 fix lands, build on multiple independent hosts and compare:

```bash
# Host A
docker build --build-arg SOURCE_DATE_EPOCH=1746489600 ...
sha256sum build-output/intergenos-shim-16.1.tar
# Host B (different hardware, same Dockerfile + repo state)
sha256sum build-output/intergenos-shim-16.1.tar
# Hashes MUST match — that's the byte-for-byte reproducibility property
```

The `verify-b2-reproducibility.sh` harness (script in
`docs/research/shim_review/b2_reproducibility_harness_2026-05-01.md` §2;
graduation pending) automates the comparison: tarball sha256, shim binary
sha256, vendor cert sha256, commit SHA text, SBAT section sha256, PE
metadata. 6/6 PASS = full reproducibility.

## What this build does NOT produce

- A Microsoft-signed shim. That comes from the shim-review PR-merge process.
  Submit the unsigned binary + this Dockerfile + the vendor cert + the
  39-question README to `rhboot/shim-review`; MS signs after review acceptance.
- A self-signed shim. Run `scripts/sign-shim.sh` for that, separately.

## Known limitations (current phase)

1. **L2 not yet applied** — apt-package versions floating; multi-host
   reproducibility is not yet provable until snapshot.debian.org pin lands.
   Tracked separately.
2. **Harness not in repo yet** — `scripts/verify-b2-reproducibility.sh`
   lives only in research doc. Tracked separately.
3. **PKCS#11 URI in `sign-shim.sh` is a default guess** — `id=%02` is the
   typical OpenSC mapping for PIV slot 9c, but should be confirmed via
   `pkcs11-tool --list-objects --type privkey` on first run with NK#1 plugged
   in. Override via `PKCS11_KEY_URI` env var if the default doesn't match.
