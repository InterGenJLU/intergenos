#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# pytorch 2.10.0 — from-source ROCm/HIP build against the in-tree ROCm 7.2.4
# platform. training-apparatus cornerstone (mirror-only). Version = latest stable in
# the unsloth consumer band (torch<2.11) — see the package.yml pin comment.
#
# EXECUTION-VERIFY ITEMS (characterize at the first/pair-proof build; each has
# its decided fallback recorded here so a halt is a decision, not a scramble):
#
#  1. clang-17 visibility class (pytorch/pytorch#173707): ROCm 7.2's clang
#     defaults -fvisibility=hidden and can hide auto-instantiated template
#     symbols (const_data_ptr/mutable_data_ptr) -> ImportError at torch import.
#     Reproduced upstream on a 2.11 nightly; 2.10.0 PREDATES that report —
#     status here unknown either way. IF hit: the fix is a visibility
#     flag/patch saved to the tree — NEVER a hand-edit of generated build
#     output (the upstream workaround's shape is not acceptable here).
#  2. Dep declarations (build+runtime ROCm sets in package.yml) reconcile
#     against the build's actual find_package trace + libtorch_hip DT_NEEDED
#     set at the pair-proof build — declare-against-reality discipline.

configure() {
    set -e
    # HIPify: translate the CUDA sources to HIP in-tree. Upstream's documented
    # from-source ROCm step; runs before any build-system invocation.
    python3 tools/amd_build/build_amd.py
}

build() {
    set -e
    export USE_ROCM=1
    export ROCM_PATH=/opt/rocm
    # Offload targets: declared in package.yml (gpu_targets), delivered
    # fail-closed by the builder. No auto-detect — the chroot has no GPU.
    export PYTORCH_ROCM_ARCH="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # Vendor-lane exclusions: this is the ROCm build; CUDA/XPU/MPS off.
    export USE_CUDA=0 USE_XPU=0 USE_MPS=0

    # Distributed training on the dual-GPU target: RCCL (the in-tree NCCL
    # analog) + bundled gloo/tensorpipe. RCCL lives in the ROCm prefix.
    export USE_DISTRIBUTED=1
    export USE_SYSTEM_NCCL=1
    export NCCL_ROOT="${ROCM_PATH}"

    # Profiler: bundled kineto against the in-tree roctracer.
    export USE_KINETO=1

    # MAGMA is not in the tree; GPU linear algebra goes through the in-tree
    # hipSOLVER/rocSOLVER path. Revisit only on measured need (own row, not a
    # silent scope change).
    export USE_MAGMA=0

    # SDPA (flash/mem-efficient attention) on ROCm: consume the FROM-SOURCE
    # compute/aotriton package. The upstream default downloads a prebuilt
    # binary from GitHub releases — rejected (offline build, no vendor
    # prebuilts). aotriton.cmake honors AOTRITON_INSTALLED_PREFIX and skips
    # the download when it is set.
    export AOTRITON_INSTALLED_PREFIX=/opt/aotriton
    export USE_FLASH_ATTENTION=1
    export USE_MEM_EFF_ATTENTION=1

    export BUILD_TEST=0
    export MAX_JOBS="${IGOS_JOBS:-$(nproc)}"

    # env -u DESTDIR (DESTDIR-redirect class, fourth live strike): setup.py's
    # build_deps runs `cmake --install` into the in-tree torch/ dir mid-wheel;
    # with the builder's DESTDIR exported the entire lib set (libtorch_python
    # included) landed under the staging dir and the final stub link found an
    # empty torch/lib (burn-proven 2026-07-22, 31-min green build lost to the
    # redirect). do_install passes DESTDIR inline, unaffected.
    env -u DESTDIR pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" torch
}
