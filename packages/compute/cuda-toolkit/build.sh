#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# cuda-toolkit 13.3.1 — download-and-install helper for the NVIDIA CUDA toolkit
# Upstream: https://developer.nvidia.com/cuda-toolkit
#
# WHAT THIS PACKAGE SHIPS: one shell script and one document. Nothing else.
# The toolkit itself is fetched from NVIDIA, on the user's machine, at install
# time, by the shipped script — because `nvcc` is not redistributable and a
# mirror package carrying it would be redistribution. See package.yml for the
# full reasoning and the decision record.
#
# WHAT THE HELPER DOES, in order:
#   1. refuses to run as anything but root, and outside pkm
#   2. states what will be downloaded, how large it is, and under whose terms
#   3. takes an explicit "I ACCEPT" BEFORE spending the download
#   4. fetches the sha256-pinned .run and verifies it — fail-closed
#   5. EXTRACTS it (never executes it: the installer would try to install
#      NVIDIA's own bundled driver over our signed one, and its --silent mode
#      accepts the EULA on the user's behalf, which we will not do)
#   6. writes the payload's own verbatim EULA text beside the acceptance
#      record, so what was agreed to is auditable afterwards
#   7. merges the component trees into /opt/cuda, minus the bundled driver
#      and minus the uninstallers
#   8. puts /opt/cuda/lib64 on the loader path and records every deposited
#      file so pkm files/verify/remove see the real install
#
# EXTRACT-DON'T-EXECUTE, CURRENT FLAG: the 2026-06-11 design record specified
# makeself's `--target <dir> --noexec`. The CUDA 13.x runfile is NOT a plain
# makeself archive and rejects that form; its extract-only flag is
# `--extract=<absolute-path>`. Verified against the pinned runfile 2026-08-04.
#
# --nox11 IS LOAD-BEARING, NOT COSMETIC: the runfile's wrapper checks for a
# tty, and when there is none but DISPLAY is set it tries to re-launch itself
# inside a terminal emulator. On a machine with a graphical session and no
# terminal on stdin — a pkm install driven from a GUI, or any unattended run —
# that path fires, finds none of the terminals it guesses at, and dies with
# `exec: -t: invalid option`. Reproduced on a live desktop session 2026-08-04.
# --nox11 suppresses the whole block, so the extraction can never depend on a
# graphical environment or on a person being in front of the machine.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

CUDA_VERSION="13.3.1"

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e

    # Version lockstep. The helper's CUDA_VERSION constant, the pinned URL and
    # this recipe's version must move together — a bump that changes one and
    # not the others would fetch a different toolkit than the package claims.
    # Fail-closed at build time (same class as the nvidia eula-helper check).
    if ! grep -q "^CUDA_VERSION=\"${CUDA_VERSION}\"$" "$BUILD_DIR/helper/igos-install-cuda-toolkit"; then
        echo "ERROR: helper CUDA_VERSION does not match build.sh CUDA_VERSION=${CUDA_VERSION}" \
             "— the version, the pinned URL and the sha256 bump together." >&2
        exit 1
    fi
    if ! grep -q "/compute/cuda/${CUDA_VERSION}/local_installers/" \
            "$BUILD_DIR/helper/igos-install-cuda-toolkit"; then
        echo "ERROR: helper download URL does not name CUDA ${CUDA_VERSION}" \
             "— version and URL are out of lockstep." >&2
        exit 1
    fi

    install -d -m 755 "${DESTDIR}/usr/bin"
    install -m 755 "$BUILD_DIR/helper/igos-install-cuda-toolkit" \
        "${DESTDIR}/usr/bin/igos-install-cuda-toolkit"

    install -d -m 755 "${DESTDIR}/usr/share/doc/cuda-toolkit"
    install -m 644 "$BUILD_DIR/docs/CUDA-TOOLKIT.md" \
        "${DESTDIR}/usr/share/doc/cuda-toolkit/CUDA-TOOLKIT.md"

    # The directory the helper writes its acceptance record into. Created here
    # so the package owns it and pkm verify sees it; the helper also creates it
    # defensively at run time.
    install -d -m 755 "${DESTDIR}/var/lib/intergen/legal"
}
