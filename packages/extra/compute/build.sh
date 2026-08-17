#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# compute meta-package — no source, no build (gaming-meta precedent).
#
# Installs:
#   /usr/share/doc/compute/README — user documentation for the ROCm
#   platform this meta pulls in.
#
# Security-only-alignment filter notes:
#   - No SUID binaries, no daemons, no kernel modules, no udev rules,
#     no config drops. A README is the only payload.
#   - Every member of the set builds from sha256-pinned upstream
#     sources inside the InterGenOS build chroot; this meta only names
#     the set.

configure() {
    set -e
    : # no-op (meta-package, no source code)
}

build() {
    set -e
    : # no-op (meta-package, no source code)
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/doc/compute"
    cat > "${DESTDIR}/usr/share/doc/compute/README" <<'EOF'
compute meta-package — InterGenOS
==================================

This package is a convenience meta-package that installs the complete
InterGenOS ROCm GPU compute platform in one step:

  Runtime and HIP layer   — rocr-runtime, rocm-hip, rocm-comgr,
                            rocm-device-libs, rocm-core, roctracer,
                            rocprofiler-register

  Compiler toolchain      — rocm-llvm (the AMD LLVM/Clang toolchain),
                            hipify (CUDA-to-HIP translation)

  Math libraries          — rocblas, hipblas, hipblaslt, rocfft,
                            hipfft, rocrand, hiprand, rocsolver,
                            hipsolver, rocsparse, hipsparse, rocprim,
                            hipcub, rocthrust, rocwmma,
                            composable-kernel

  Machine learning        — miopen (deep-learning primitives),
                            migraphx (graph optimization), rccl
                            (multi-GPU collectives), aotriton
                            (ahead-of-time Triton kernels),
                            llama-cpp-hip (GPU-accelerated local
                            LLM inference)

  Tools and debugging     — rocminfo, rocm-smi-lib, amdsmi,
                            rocgdb, rocdbgapi, rocm-debug-agent,
                            rocprofiler-sdk, aqlprofile

  Support libraries       — eigen, fmt, glog, half, nlohmann-json,
                            pybind11, frugally-deep, functionalplus,
                            elfutils-libdw, rocm-cmake

Everything in this set is mirror-hosted: it installs post-install over
the network (pkm install compute) and is not on the install ISO, which
keeps the ISO lean — the ROCm trees are multi-gigabyte.

Every package in the set builds from sha256-pinned upstream sources
inside the InterGenOS build chroot; nothing here is a prebuilt binary
download.

For the model-training stack that runs on top of this platform
(pytorch, unsloth, transformers, and their closure), see the
companion meta-package: pkm install training.
EOF
}
