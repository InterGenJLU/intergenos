#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocm-core 7.2.4 — ROCm platform base metadata
#
# Ships the platform-contract artifacts every AMD-packaged ROCm install
# carries and that consumers read directly: /opt/rocm/.info/version
# (rccl configure auto-detect class), include/rocm-core/rocm_version.h
# (rccl hip_rocm_version_info.h:42), and librocm-core (rocm_getpath).
# See package.yml for the class rationale.

configure() {
    set -e
    mkdir -p build
    # ROCM_VERSION drives the generated version header + .info/version —
    # keep in lockstep with version: in package.yml.
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DROCM_VERSION=7.2.4 \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build

    # Fail loudly if the platform-contract artifacts did not land where
    # declared (upstream installs `version` into `.info/` relative to
    # the prefix and the headers into include/rocm-core/).
    test -e "${DESTDIR}/opt/rocm/.info/version"
    test -e "${DESTDIR}/opt/rocm/include/rocm-core/rocm_version.h"

    # PATH for the toolkit's own programs. /opt/rocm/bin holds ~450 programs
    # and is on no default PATH, so a person who installed the toolkit got
    # "command not found" from rocm-smi, amd-smi, rocgdb, rocprofv3 and
    # hipify-perl while every one of them sat installed on the disk (measured
    # on an installed machine 2026-09-20). Four of them — hipcc, hipconfig,
    # rocminfo, rocm_agent_enumerator — have /usr/bin symlinks from their own
    # recipes and keep them: a systemd service or any other non-login process
    # has no profile drop-in and must still find those four.
    #
    # APPENDED, not prepended, and the reason is measured: three names in
    # /opt/rocm/bin also exist in /usr/bin — llama-bench, llama-cli and
    # llama-server, the HIP-built copies beside the CPU-built ones the
    # llama-cpp package ships. Prepending would silently change which engine
    # a person gets by typing llama-cli. Appending leaves every existing name
    # resolving exactly as it does today and adds the ~445 that resolved to
    # nothing.
    #
    # Written here as owned payload rather than by a post-install hook: a hook
    # that writes into /etc leaves a file no package owns (the same class the
    # rust recipe's comment records).
    install -dm755 "${DESTDIR}/etc/profile.d"
    cat > "${DESTDIR}/etc/profile.d/rocm.sh" << "PROFILE"
# Begin /etc/profile.d/rocm.sh

case ":${PATH}:" in
    *:/opt/rocm/bin:*) ;;
    *) export PATH=${PATH}:/opt/rocm/bin ;;
esac

# End /etc/profile.d/rocm.sh
PROFILE
    chmod 644 "${DESTDIR}/etc/profile.d/rocm.sh"
}
