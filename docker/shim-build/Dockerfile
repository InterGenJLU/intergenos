# InterGenOS shim-review Dockerfile — byte-for-byte reproducible build
#
# Produces an UNSIGNED shim EFI binary with the InterGenOS Secure Boot CA
# vendor certificate embedded. Final signing happens OUTSIDE the container,
# on the workstation, via scripts/sign-shim.sh against Nitrokey #1's PIV
# slot 9c (private key is hardware-bound; cannot be copied into a container).
#
# Multi-host verification: docker build on any host with this Dockerfile +
# this repo's pinned cert MUST produce the same sha256sum on the output
# tarball.
#
# Usage:
#   docker build \
#     --build-arg SOURCE_DATE_EPOCH=1746489600 \
#     -t intergenos-shim-builder \
#     -f docker/shim-build/Dockerfile .
#   docker run --rm -v $(pwd)/build-output:/out intergenos-shim-builder

# -----------------------------------------------------------------------------
# Pinned base image — Debian 12 bookworm-slim, digest-locked
# Rationale: Debian stable provides a narrow, well-known package set with
# multi-year ABI stability. slim variant minimizes attack surface.
# Reproducible-builds.org tooling is Debian-native.
# -----------------------------------------------------------------------------
FROM debian:bookworm-slim@sha256:5a2a80d11944804c01b8619bc967e31801ec39bf3257ab80b91070eb23625644

# -----------------------------------------------------------------------------
# Reproducibility anchors — must be identical across all build hosts
# SOURCE_DATE_EPOCH = 2026-05-06 00:00:00 UTC (post-ceremony cert mint at
# 2026-05-05 17:23:27 UTC; epoch must be >= cert NotBefore for self-consistency)
# -----------------------------------------------------------------------------
ARG SOURCE_DATE_EPOCH=1746489600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV TZ=UTC
ENV DEBIAN_FRONTEND=noninteractive

# -----------------------------------------------------------------------------
# Shim source — pinned to exact tag AND exact commit SHA
# Tag 16.1 → commit afc49558b34548644c1cd0ad1b6526a9470182ed
# Commit pin (L3 fix from B2 leak register) ensures that even if the upstream
# tag is moved (malicious or accidental), the build asserts the expected SHA
# and exits non-zero on mismatch.
# -----------------------------------------------------------------------------
ARG SHIM_VERSION=16.1
ARG SHIM_COMMIT=afc49558b34548644c1cd0ad1b6526a9470182ed

# -----------------------------------------------------------------------------
# Build dependencies
# gnu-efi: EFI development headers
# libelf-dev: ELF manipulation for shim's PE/COFF assembly
# openssl: cert + EFI signature tooling (used by shim's Makefile during build)
# git / ca-certificates: clone shim source
# build-essential: gcc + make toolchain
# perl: shim build scripts
# (sbsigntool intentionally excluded — signing happens outside the container.)
#
# L2 fix (apt version pinning): switch from floating Debian bookworm to a
# snapshot.debian.org timestamp. This pins every apt package to the exact
# version available at that timestamp, eliminating the reproducibility leak
# where two builds weeks apart pull different gcc/libc/binutils versions.
# Timestamp 20260501T000000Z chosen as: post-bookworm-12.9 release,
# pre-ceremony, stable package set.
# -----------------------------------------------------------------------------
RUN echo "deb [trusted=yes check-valid-until=no] http://snapshot.debian.org/archive/debian/20260501T000000Z bookworm main" \
        > /etc/apt/sources.list.d/snapshot.list \
 && rm -f /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    dos2unix \
    git \
    gnu-efi \
    libelf-dev \
    openssl \
    perl \
 && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Clone shim source at exact tag, then assert the commit SHA matches the pin.
# --depth 1 keeps the layer small. The assert below is the L3 reproducibility
# fix: a moved tag now fails the build instead of silently producing a
# different binary.
# -----------------------------------------------------------------------------
WORKDIR /build
RUN git clone --depth 1 --branch ${SHIM_VERSION} --recurse-submodules --shallow-submodules \
    https://github.com/rhboot/shim.git /build/shim \
 && ACTUAL_SHA=$(git -C /build/shim rev-parse HEAD) \
 && if [ "${ACTUAL_SHA}" != "${SHIM_COMMIT}" ]; then \
        echo "ERROR: shim HEAD ${ACTUAL_SHA} != expected ${SHIM_COMMIT}"; \
        exit 1; \
    fi \
 && echo "${ACTUAL_SHA}" > /build/SHIM_COMMIT_SHA

# -----------------------------------------------------------------------------
# InterGenOS Secure Boot CA vendor certificate (DER + PEM)
# Cert minted on Nitrokey #1 PIV slot 9c during the 2026-05-05 air-gapped
# Tails ceremony (see scripts/ceremony/ceremony.py + docs/signing-key.md).
# Canonical DER fingerprint and PEM-file integrity hash are published in
# docs/signing-key.md. Private half is hardware-bound to NK#1's PIV applet
# and physically cannot be extracted; only the public cert lives in repo.
# -----------------------------------------------------------------------------
COPY docker/shim-build/vendor-cert/intergenos-secure-boot-ca.der /build/vendor_cert.der
COPY docker/shim-build/vendor-cert/intergenos-secure-boot-ca.pem /build/vendor_cert.pem

# -----------------------------------------------------------------------------
# InterGenOS SBAT vendor entry
# shim's Makefile globs data/sbat.*.csv (VENDOR_SBATS in the Makefile) and
# concatenates each match onto the embedded .sbat section. Our distro line
# below is generation 1; bump only when an InterGenOS shim version needs to
# be revoked via SBAT enforcement.
# -----------------------------------------------------------------------------
COPY docker/shim-build/sbat/sbat.intergenos.csv /build/shim/data/sbat.intergenos.csv

# -----------------------------------------------------------------------------
# Build the shim binary with embedded vendor cert + SBAT entries.
# -j1 (L1 fix): serial build to eliminate thread-race ordering as a
# reproducibility leak. Cost is seconds — shim is small (~30 source files).
# DEFAULT_LOADER points shim at the GRUB binary it should load next in the
# Secure Boot chain. ENABLE_SBSIGN intentionally NOT set: we do not sign
# inside the container.
# -----------------------------------------------------------------------------
WORKDIR /build/shim
RUN make -j1 \
    VENDOR_CERT_FILE=/build/vendor_cert.der \
    DEFAULT_LOADER="\\\\grubx64.efi"

# -----------------------------------------------------------------------------
# Collect outputs into a deterministic tarball
# All four reproducibility anchors present: --sort, --owner=0, --group=0,
# --mtime=@SOURCE_DATE_EPOCH. Output binary is UNSIGNED — see
# scripts/sign-shim.sh for the workstation-side NK#1 signing step.
# -----------------------------------------------------------------------------
RUN mkdir -p /out \
 && cp /build/shim/shimx64.efi /out/shimx64.efi \
 && cp /build/vendor_cert.der /out/vendor_cert.der \
 && cp /build/vendor_cert.pem /out/vendor_cert.pem \
 && cp /build/SHIM_COMMIT_SHA /out/SHIM_COMMIT_SHA \
 && cp /build/shim/data/sbat.intergenos.csv /out/sbat.intergenos.csv \
 && tar --sort=name \
        --owner=0 --group=0 --numeric-owner \
        --mtime="@${SOURCE_DATE_EPOCH}" \
        --format=gnu \
        -cf /out/intergenos-shim-${SHIM_VERSION}.tar \
        -C /out shimx64.efi vendor_cert.der vendor_cert.pem SHIM_COMMIT_SHA sbat.intergenos.csv \
 && sha256sum /out/intergenos-shim-${SHIM_VERSION}.tar > /out/intergenos-shim-${SHIM_VERSION}.tar.sha256 \
 && cat /out/intergenos-shim-${SHIM_VERSION}.tar.sha256

# -----------------------------------------------------------------------------
# Expected verification (post-build, host-side):
#   1. sha256sum /out/intergenos-shim-${SHIM_VERSION}.tar (Q25 fill)
#   2. objcopy --dump-section .sbat=/dev/stdout /out/shimx64.efi (Q14, Q29 fill)
#   3. readelf -lW /out/shimx64.efi | grep STACK   (Q11 NX-bit assertion)
#   4. Multi-host: same Dockerfile + repo on different hosts MUST produce
#      identical tarball sha256. Run scripts/verify-b2-reproducibility.sh
#      (graduation tracked separately) against two hosts' tarballs.
#   5. Sign with NK#1: scripts/sign-shim.sh /out/shimx64.efi shimx64.efi.signed
# -----------------------------------------------------------------------------
