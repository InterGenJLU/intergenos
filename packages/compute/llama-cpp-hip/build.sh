#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# llama-cpp-hip b8796 — LLM inference engine, HIP/ROCm variant
# https://github.com/ggml-org/llama.cpp
#
# The opt-in AMD-GPU engine (compute tier, mirror-only). The shipped
# default engine (packages/ai/llama-cpp, Vulkan+CPU) remains untouched —
# this variant exists for ROCm boxes where HIP outperforms Vulkan and
# multi-GPU steering (HIP_VISIBLE_DEVICES per service) is needed.
#
# COEXISTENCE DESIGN — static, /opt/rocm prefix: the default engine owns
# /usr/bin/llama-* and /usr/lib/libllama+libggml*. A shared-lib HIP
# variant with identically-named libraries anywhere near the linker path
# is a runtime-shadowing hazard for the DEFAULT engine (which library a
# binary resolves would depend on ld.so.conf ordering — a silent
# wrong-backend class). BUILD_SHARED_LIBS=OFF removes the class: ggml/
# llama link statically into the variant's binaries, which live wholly
# under /opt/rocm/bin. The ROCm stack itself (hipblas/rocblas/hsa)
# stays dynamic — those sonames are unique to /opt/rocm.
#
# Invocation validated at authoring against the pin's docs/build.md HIP
# section, current flag-for-flag (GPU_TARGETS is the live flag name;
# GGML_HIP_ROCWMMA_FATTN=ON per freshness finding 4 — compile the
# flash-attention capability in, the runtime A/B decides the default).
# HIPCXX/HIP_PATH via hipconfig per the upstream-documented invocation.
#
# ISA floor matches the default engine exactly (GGML_NATIVE=OFF +
# explicit AVX2/FMA floor — a distro binary is never compiled for the
# build VM's CPU; see packages/ai/llama-cpp for the full rationale).

# ---- The build-number stamp, shared by all three engine variants ------------
# Upstream derives the build number and commit from git metadata. This recipe
# builds from a release tarball, which carries no .git, so cmake's build-info
# step fell back to zero and every engine binary shipped so far reported
# "version: 0 (unknown)" — it could not say which build it was, which is the
# one question a user comparing two engines has to be able to answer.
#
# The number is DERIVED from this recipe's own version pin and never written a
# second time: a hardcoded copy could disagree with the pin after a bump, and a
# binary that misreports itself is worse than one that reports nothing. The pin
# shape is checked, so a future pin that stops being b<number> fails the build
# loudly instead of stamping something wrong. The same two functions are
# carried byte for byte by all three engine recipes (packages/ai/llama-cpp,
# packages/compute/llama-cpp-cuda, packages/compute/llama-cpp-hip) — one shape
# across the engines, the same discipline the --list-devices patch follows.
llama_pin_build_number() {
    _pin="${PKG_VERSION:?FATAL: the builder did not export PKG_VERSION}"
    _num="${_pin#b}"
    case "$_num" in
        ''|*[!0-9]*)
            echo "ERROR: the version pin '${_pin}' is not the b<number> shape this" >&2
            echo "       recipe derives the engine build number from. Update the" >&2
            echo "       derivation deliberately; never ship an engine that cannot" >&2
            echo "       say which build it is." >&2
            return 1 ;;
    esac
    printf '%s\n' "$_num"
}

# Assert that a built binary REPORTS the pinned number, and refuse the archive
# if it does not. $1 = binary to ask, $2 = value for LD_LIBRARY_PATH (the
# engine's libraries are not installed yet at this point).
#
# This lives in do_install() rather than check() deliberately: the CUDA variant
# declares tests.enabled=false for a governed reason, and the builder SKIPS the
# whole check phase for such a package — an assertion placed there would simply
# not run on one of the three engines, which is the silent-failure shape this
# stamp exists to remove. do_install() runs unconditionally and is the last
# point before the archive is sealed.
llama_assert_build_number() {
    _want="$(llama_pin_build_number)" || return 1
    _out="$(LD_LIBRARY_PATH="$2" "$1" --version 2>&1 || true)"
    # The version line is not always the FIRST line, and assuming it was cost a
    # refusal of a correctly stamped binary here on 2026-08-04: the CUDA build
    # enumerates its devices before printing it whenever a device is present,
    # so its output opens with ggml_cuda_init lines. Match the line wherever it
    # appears. A leading newline is prepended to both sides so a version line
    # that IS first still matches.
    case "
$_out" in
        *"
version: ${_want} "*) ;;
        *)
            echo "ERROR: ${1} does not report the pinned build number." >&2
            echo "       expected a line: version: ${_want} (<commit>)" >&2
            echo "       what it printed, in full:" >&2
            printf '%s\n' "$_out" | sed 's/^/         | /' >&2
            echo "       Refusing to seal the archive: an engine that misreports" >&2
            echo "       its build cannot be told apart from another one." >&2
            return 1 ;;
    esac
    echo "[build-number] ${1} reports: $(printf '%s\n' "$_out" | grep -m1 "^version: ${_want} ")"
}

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # --list-devices PCI-id suffix — the SAME patch as packages/ai/llama-cpp
    # (byte-identical file, one-pin policy): the serving-device selector maps
    # a ggml device to its kernel DRM card by this id. See the ai recipe's
    # configure() comment for the full rationale.
    BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    patch -p1 < "$BUILD_DIR/list-devices-pci-id.patch"

    # ROCM_PATH: same HIP-root detection pin as rocblas/rocwmma/hipblas (see
    # the rocblas recipe for the mechanism). The HIP_PATH prefix-env below
    # only reaches the CONFIGURE process; the build-phase device compiles
    # invoke clang directly and mis-detect the HIP root without the env —
    # live-proven here as `hip/hip_fp16.h not found` (the INCLUDE half of
    # the same class; the header is present at /opt/rocm/include/hip/).
    export ROCM_PATH=/opt/rocm

    build_number="$(llama_pin_build_number)" || return 1

    mkdir -p build

    HIPCXX="$(/opt/rocm/bin/hipconfig -l)/clang" \
    HIP_PATH="$(/opt/rocm/bin/hipconfig -R)" \
    cmake -S . -B build \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DLLAMA_BUILD_NUMBER="${build_number}" \
        -DLLAMA_BUILD_SERVER=ON \
        -DBUILD_SHARED_LIBS=OFF \
        -DGGML_NATIVE=OFF \
        -DGGML_AVX=ON \
        -DGGML_AVX2=ON \
        -DGGML_HIP=ON \
        -DGPU_TARGETS="${GPU_TARGETS}" \
        -DGGML_HIP_ROCWMMA_FATTN=ON \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" install

    # The staged binary must REPORT the pinned build number. Asked of the
    # binary that ships. These binaries link llama and ggml statically
    # (BUILD_SHARED_LIBS=OFF) but resolve the HIP runtime dynamically from
    # /opt/rocm/lib, which is present at build time because this package
    # build-depends on the ROCm stack that owns that prefix.
    #
    # The check matches the version line wherever it appears in the output,
    # which the sibling CUDA variant turned out to need: its backend
    # enumerates devices before printing that line. Whether the HIP backend
    # prints anything ahead of it is not measured here — this recipe is not
    # built on a machine without ROCm — and it does not have to be, because
    # the matcher does not depend on the line's position.
    llama_assert_build_number "$DESTDIR/opt/rocm/bin/llama-cli" \
                              "/opt/rocm/lib" || return 1

    # Install the architecture list this build was compiled for, so the runtime
    # can tell whether the build has device code for the GPU in front of it.
    #
    # It matters because the failure is not graceful: on an AMD GPU outside this
    # list — a gfx90c APU, say — llama-server SEGFAULTS at model load rather
    # than refusing cleanly, so selecting the HIP engine by vendor alone turns a
    # working Vulkan setup into a crash. intergen.serving_device reads this file
    # and declines HIP when the machine's architecture is measurably absent from
    # it.
    #
    # Written from the same GPU_TARGETS the cmake configure above consumed, so
    # there is one source of truth (package.yml gpu_targets) rather than a
    # second list that can drift away from what was actually compiled.
    printf '%s\n' "${GPU_TARGETS}" > gpu-targets.txt
    install -Dm644 gpu-targets.txt \
        "${DESTDIR}/opt/rocm/share/llama-cpp-hip/gpu-targets"
}
