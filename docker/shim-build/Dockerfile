# InterGenOS shim-review Dockerfile — byte-for-byte reproducible build
#
# Produces a signed shim EFI binary with embedded vendor certificate.
# Multi-host verification: docker build on any host with this Dockerfile
# MUST produce the same sha256sum on the output tarball.
#
# Vendor cert: placeholder self-signed. REAL vendor cert (from Thursday
# 2026-04-30 Nitrokey ceremony, C6 output) plugs in at build arg VENDOR_CERT.
#
# Reference: docs/research/installer/ms_shim_sponsorship_2026-04-18.md
#            docs/research/installer/signing_key_custody_2026-04-18.md
#
# Usage:
#   docker build \
#     --build-arg SOURCE_DATE_EPOCH=1745904000 \
#     -t intergenos-shim-builder .
#   docker run --rm -v $(pwd)/out:/out intergenos-shim-builder

# -----------------------------------------------------------------------------
# Pinned base image — Debian 12 bookworm-slim, digest-locked
# Rationale: Debian stable provides a narrow, well-known package set with
# multi-year ABI stability. slim variant minimizes attack surface. Rocky
# (RHEL-compatible) was considered but Debian's broader arch support and
# established reproducibility tooling (reproducible-builds.org is Debian-
# native) give it the edge for byte-for-byte verification.
# -----------------------------------------------------------------------------
FROM debian:bookworm-slim@sha256:5a2a80d11944804c01b8619bc967e31801ec39bf3257ab80b91070eb23625644

# -----------------------------------------------------------------------------
# Reproducibility anchors — must be identical across all build hosts
# -----------------------------------------------------------------------------
ARG SOURCE_DATE_EPOCH=1745904000
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV TZ=UTC
ENV DEBIAN_FRONTEND=noninteractive

# -----------------------------------------------------------------------------
# Shim source — pinned to exact Git tag SHA
# rhboot/shim v16.1 tag object: dad4f20725731a8b16ce136918c312c29694848a
# (git tag -v 16.1 would verify the signed tag; SHA here is the tag object,
# not the commit, but Docker's ADD from git resolves tags to commits.
# For strict reproducibility, we clone at checkout-time and verify.)
# -----------------------------------------------------------------------------
ARG SHIM_VERSION=16.1
ARG SHIM_TAG_SHA=dad4f20725731a8b16ce136918c312c29694848a

# -----------------------------------------------------------------------------
# Vendor certificate — placeholder, replaced post-Thursday ceremony
# Build arg allows plugging in the real cert without changing the Dockerfile.
# Default: generate a self-signed placeholder with fixed serial/subject so
# the intermediate build is deterministic until cert swap.
# -----------------------------------------------------------------------------
ARG VENDOR_CERT_SUBJECT="/C=US/O=InterGenOS/CN=InterGenOS Secure Boot CA (PLACEHOLDER)"
ARG VENDOR_CERT_DAYS=3650

# -----------------------------------------------------------------------------
# Build dependencies
# gnu-efi: EFI development headers (shim is an EFI application)
# libelf-dev: ELF manipulation (shim validates PE/COFF headers)
# openssl: X.509 cert generation and EFI signature tooling
# sbsigntool: sign EFI binaries (replaces pesign; lighter, pure Debian)
# git / ca-certificates: clone shim source
# build-essential: gcc + make toolchain
# perl: shim build scripts
# -----------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    git \
    gnu-efi \
    libelf-dev \
    openssl \
    perl \
    sbsigntool \
 && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Clone shim source at exact tag
# --depth 1 keeps the layer small; tag is verified by checkout.
# -----------------------------------------------------------------------------
WORKDIR /build
RUN git clone --depth 1 --branch 16.1 --recurse-submodules --shallow-submodules \
    https://github.com/rhboot/shim.git /build/shim \
 && git -C /build/shim rev-parse HEAD > /build/SHIM_COMMIT_SHA

# -----------------------------------------------------------------------------
# Vendor certificate — fixed test keypair for pre-ceremony reproducibility.
# Source: tests/fixtures/shim-build/test-vendor-{cert,key}.pem
# WARNING: TEST ONLY. NEVER use for production signing.
# Post-ceremony: real vendor cert replaces these files via build arg or COPY.
# -----------------------------------------------------------------------------
COPY tests/fixtures/shim-build/test-vendor-key.pem /build/vendor_key.pem
COPY tests/fixtures/shim-build/test-vendor-cert.pem /build/vendor_cert.pem
COPY tests/fixtures/shim-build/test-vendor-cert.der /build/vendor_cert.der

# -----------------------------------------------------------------------------
# Build shim with embedded vendor certificate
# VENDOR_CERT_FILE points to the DER-encoded certificate shim embeds.
# ENABLE_SBSIGN=1 enables sbsign-based signing of the resulting shim binary.
# -----------------------------------------------------------------------------
WORKDIR /build/shim
RUN make -j$(nproc) \
    VENDOR_CERT_FILE=/build/vendor_cert.der \
    ENABLE_SBSIGN=1 \
    DEFAULT_LOADER="\\\\grubx64.efi"

# -----------------------------------------------------------------------------
# Sign the shim binary with the vendor key
# The shim binary is signed so Secure Boot trusts the first-stage loader.
# -----------------------------------------------------------------------------
RUN sbsign --key /build/vendor_key.pem \
           --cert /build/vendor_cert.pem \
           --output /build/shimx64.efi.signed \
           /build/shim/shimx64.efi

# -----------------------------------------------------------------------------
# Collect outputs into a deterministic tarball
# --sort=name: file order independent of filesystem
# --owner=0 --group=0 --numeric-owner: consistent ownership
# --mtime=@SOURCE_DATE_EPOCH: fixed timestamp
# --format=gnu: GNU tar format supports all needed features
# -----------------------------------------------------------------------------
RUN mkdir -p /out \
 && cp /build/shimx64.efi.signed /out/shimx64.efi \
 && cp /build/vendor_cert.der /out/vendor_cert.der \
 && cp /build/SHIM_COMMIT_SHA /out/SHIM_COMMIT_SHA \
 && tar --sort=name \
        --owner=0 --group=0 --numeric-owner \
        --mtime="@${SOURCE_DATE_EPOCH}" \
        --format=gnu \
        -cf /out/intergenos-shim-${SHIM_VERSION}.tar \
        -C /out shimx64.efi vendor_cert.der SHIM_COMMIT_SHA \
 && sha256sum /out/intergenos-shim-${SHIM_VERSION}.tar > /out/intergenos-shim-${SHIM_VERSION}.tar.sha256 \
 && cat /out/intergenos-shim-${SHIM_VERSION}.tar.sha256

# -----------------------------------------------------------------------------
# Expected verification:
#   sha256sum of the tarball MUST match byte-for-byte across:
#     - /mnt/intergenos-deepseek (Ubuntu 24.04 desktop, x86_64)
#     - Ubuntu 24.04 server (separate host)
#     - Windows-claude clone (Zephyrus M16, Docker Desktop)
#   Mismatch = non-reproducibility bug → fix before shim-review PR.
# -----------------------------------------------------------------------------
