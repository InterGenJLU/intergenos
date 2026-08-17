#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# intergenos-keyring 0.1.0 — InterGenOS GPG release keyring
# https://github.com/InterGenJLU/intergenos
#
# Installs: /etc/pkm/trusted.gpg containing the InterGenOS master public
# release-signing key. pkm verifies InterGenOS.db signatures against this
# keyring on every `pkm sync` / `pkm update` / `pkm refresh`. Without this
# keyring on disk every sync fails closed (the canonical broken state
# pre this package).
#
# Build approach: the binary keyring is PRE-GENERATED on the host from
# docs/signing-key.asc and committed to the repository at
# packages/core/intergenos-keyring/trusted.gpg. Build-time work is just
# install-the-file + sha256-assert. This eliminates two chroot-install
# dependencies that previously caused the build to fail at this package:
#   1. gnupg2 in the chroot at intergenos-keyring's build position
#      (intergenos-keyring is built early in core-extra at line 521 of
#      chroot-build-core-extra.sh; gnupg2 is built much later at line
#      876 — the package.yml `host: gnupg2` dep is informational only,
#      not honored by the bash inner script's hardcoded order. K12
#      modernization to dep-graph-driven ordering remains pending).
#   2. /mnt/intergenos/docs/signing-key.asc reachable in the chroot
#      (sync_chroot_scripts at scripts/build-intergenos.sh ~line 972
#      rsyncs scripts/ + packages/ + config/ + installer/ but NOT docs/).
# Pre-generating sidesteps both. Mirrors Arch's archlinux-keyring pattern
# (ships pre-built archlinux.gpg, no runtime gpg dep at install time).
#
# Key-rotation flow: when the operator rotates the master release-signing
# key (or stages a subkey rotation per docs/signing-key.md §Rollover):
#   1. Update docs/signing-key.asc with the new public key bundle
#   2. Re-generate packages/core/intergenos-keyring/trusted.gpg via:
#        TMPHOME=$(mktemp -d)
#        GNUPGHOME="$TMPHOME" gpg --no-default-keyring \
#            --keyring packages/core/intergenos-keyring/trusted.gpg \
#            --import docs/signing-key.asc
#        rm -rf "$TMPHOME"
#        rm -f packages/core/intergenos-keyring/trusted.gpg~
#   3. Update EXPECTED_SHA256 below to the new sha256sum
#   4. Commit both the .asc + the .gpg + the updated sha256 in one commit
#      for atomic provenance; reviewers verify the .gpg matches the .asc.
#
# Q10 subkey-rotation: handled the same way — re-run step 2 with both
# outgoing + incoming subkey ASCII bundles imported into the same keyring
# (during the 30-day overlap window), regenerate the binary, update the
# sha256. The pkm-side pin set in pkm/release-keys.json must be extended
# with the incoming subkey's fingerprint BEFORE shipping the regenerated
# keyring so the L-025 pinned-fingerprint check at verify-time succeeds.

PKG_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

build() {
    set -e
    # Pre-built binary keyring is shipped with the package; no build step.
    return 0
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/etc/pkm"

    local src_keyring="${PKG_DIR}/trusted.gpg"
    if [ ! -f "${src_keyring}" ]; then
        echo "FATAL: pre-built keyring missing at ${src_keyring}" >&2
        echo "Regenerate per the header comment of this build.sh." >&2
        return 1
    fi

    # Defensive sha256 assert: the binary keyring is a security-critical
    # artifact. Pin its sha256 here and refuse to install if the on-disk
    # file drifts from expectation (silent corruption, accidental commit,
    # supply-chain tampering between repo-clone and build). Composes with
    # the artifact-integrity ≠ behavioral-integrity discipline — verify
    # the bytes that are about to be deployed, not just that the file
    # exists.
    #
    # When rotating the keyring per the header-comment flow, update this
    # EXPECTED_SHA256 in the SAME commit that regenerates the binary.
    local EXPECTED_SHA256="437f712d6e4585dc54b6bfee866a9820ab488076ecbcdb9403ca0f1cf086b1b2"
    local actual_sha256
    actual_sha256="$(sha256sum "${src_keyring}" | awk '{print $1}')"
    if [ "${actual_sha256}" != "${EXPECTED_SHA256}" ]; then
        echo "FATAL: intergenos-keyring trusted.gpg sha256 mismatch." >&2
        echo "  Expected: ${EXPECTED_SHA256}" >&2
        echo "  Actual:   ${actual_sha256}" >&2
        echo "  Path:     ${src_keyring}" >&2
        echo "If you rotated the keyring, update EXPECTED_SHA256 in" >&2
        echo "${BASH_SOURCE[0]} in the SAME commit as the binary change." >&2
        return 1
    fi

    install -m644 "${src_keyring}" "${DESTDIR}/etc/pkm/trusted.gpg"
}
