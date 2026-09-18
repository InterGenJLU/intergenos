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

# Assert that a staged binary carries COMPILED DEVICE CODE for every
# architecture gpu_targets declares. $1 = binary, $2 = the declared list.
#
# Why this is a gate and not a comment. The declaration is a promise to a person
# with that card, and the only thing that keeps it is a code object for that
# architecture inside the binary. Nothing verified it: the build wrote the
# declaration into a record beside the binary and never asked the binary what it
# actually contained. A target dropped by a cmake flag change, a toolchain that
# silently skipped one, or a widened declaration that the compile did not follow
# would all have shipped green.
#
# HOW IT READS, and why not the obvious tools. Three were measured on an
# installed engine 2026-09-17:
#
#   roc-obj-ls            SHIPPED BUT NON-FUNCTIONAL on this distribution: it is
#                         a perl script that requires File::Which, which the
#                         project's perl does not carry, so it aborts at line 25
#                         before reading anything.
#   llvm-objdump --offloading
#                         works, and reported exactly what that build declared — but
#                         EXTRACTS every bundle entry as a file beside the input.
#                         Run against a staged binary that is about to be sealed,
#                         it would write gigabytes of extracted code objects into
#                         DESTDIR and ship them.
#   llvm-objcopy --dump-section
#                         reads only, writes nothing beside the binary, and scopes
#                         the scan to the section that holds the bundles. This is
#                         the one used. Measured 2026-09-17 on the installed engine
#                         built from the THREE-target declaration of the time, it
#                         reported gfx1100, gfx1102 and gfx1201 — 125 bundle
#                         entries each — matching what llvm-objdump extracted.
#
# The bundle entry ids inside .hip_fatbin have the form
# hipv4-amdgcn-amd-amdhsa--gfx1100. Scoping the scan to that section is what
# keeps this from being a substring match on a binary: an unrelated string
# constant elsewhere in the executable cannot be mistaken for a code object.
#
# It lives in do_install() rather than check() for the same governed reason as
# llama_assert_build_number: an assertion in check() does not run for a variant
# whose recipe declares tests.enabled=false, and the engine recipes are held to
# one shape.
llama_assert_device_code() {
    _bin="$1"; _targets="$2"
    _objcopy="${ROCM_PATH:-/opt/rocm}/lib/llvm/bin/llvm-objcopy"
    if [ ! -x "$_objcopy" ]; then
        echo "ERROR: no llvm-objcopy at ${_objcopy}; cannot verify that" >&2
        echo "       ${_bin} carries the device code it declares. Refusing" >&2
        echo "       to seal the archive on an unverified promise." >&2
        return 1
    fi
    # /dev/null is the output object llvm-objcopy insists on; the section goes
    # to stdout. A binary with no .hip_fatbin section makes it exit non-zero,
    # which is treated as "nothing found" below rather than as a pass.
    # grep -a rather than strings(1): the same answer, measured, with one
    # fewer tool the build environment has to carry.
    _have="$("$_objcopy" --dump-section=.hip_fatbin=- "$_bin" /dev/null 2>/dev/null \
             | grep -a -oE 'amdhsa--gfx[0-9a-z]+' \
             | sed 's/.*--//' | sort -u | tr '\n' ' ')"
    echo "[device-code] ${_bin} carries: ${_have:-(none)}"
    if [ -z "$_have" ]; then
        echo "ERROR: ${_bin} carries no AMD device code at all. Either the HIP" >&2
        echo "       backend did not build into it or the section this reads" >&2
        echo "       (.hip_fatbin) is not where the toolchain put it. Refusing" >&2
        echo "       to seal the archive: a verification that finds nothing is" >&2
        echo "       not a verification that found everything." >&2
        return 1
    fi
    _missing=""
    _old_ifs="$IFS"; IFS=';'
    for _t in $_targets; do
        IFS="$_old_ifs"
        case " $_have " in
            *" $_t "*) ;;
            *) _missing="${_missing} ${_t}" ;;
        esac
        IFS=';'
    done
    IFS="$_old_ifs"
    if [ -n "$_missing" ]; then
        echo "ERROR: ${_bin} declares GPU targets it does not carry code for." >&2
        echo "       declared : ${_targets}" >&2
        echo "       carries  : ${_have}" >&2
        echo "       missing  :${_missing}" >&2
        echo "       A declared architecture with no code object is a promise to" >&2
        echo "       a card that cannot be kept: this engine segfaults at model" >&2
        echo "       load on an architecture it has no kernels for, rather than" >&2
        echo "       refusing cleanly. Refusing to seal the archive." >&2
        return 1
    fi
}

# Assert that every architecture gpu_targets declares also has KERNELS in the
# math libraries this engine links against. $1 = the declared list.
# $2 = the space-separated names of those libraries, stated by the caller.
#
# Why a second check. The first one asks the engine's own binary what it
# carries, and an engine can carry code for a card while the layers underneath
# it cannot serve one. ggml's HIP backend calls into hipBLAS, which resolves to
# rocBLAS, and both ship per-architecture kernel files generated at THEIR build
# time from THEIR own declared target list. Declaring a card here that rocBLAS
# was not built for would pass the code-object check and still fail on the
# machine — the same defect one level down, introduced by the fix rather than
# removed by it.
#
# WHICH LIBRARIES ARE ASKED, and why it is no longer a fixed pair. This check
# was written to loop over "rocblas hipblaslt". The engine does not link
# hipBLASLt: measured 2026-09-18 with ldd on the built engine, its dynamic
# dependencies name libhipblas.so.3 and librocblas.so.5 and no hipblaslt at
# all. So the loop demanded kernels from a library whose work never reaches
# this binary, and on the six-target build it refused an engine that was
# correct — "[math-kernels] hipblaslt carries: gfx1100 gfx1102 gfx1201 /
# missing: gfx1030 gfx1101 gfx1200" — because hipBLASLt's own upstream target
# list excludes RDNA2 by construction
# (cmake/tensilelite_supported_architectures.cmake in ROCm/rocm-libraries: its
# kernels are built on MFMA and WMMA, which gfx1030 does not have). A gate that
# refuses a true build is not stricter than one that does not; it is wrong in
# the other direction, and the people it stops are the ones who then learn to
# route around it. The caller states the libraries, do_install() takes them
# from this recipe's own declared runtime dependencies, and a library that
# ships no per-architecture kernels of its own (hipBLAS is the dispatch layer
# over rocBLAS) is simply not named.
#
# Measured 2026-09-17 on a machine running ROCm 7.2.4, built from the
# THREE-target declaration in force at the time: the compiler accepts 76 AMDGPU
# targets and the device libraries carry bitcode for 54, but lib/rocblas/library
# and lib/hipblaslt/library carried kernel files for exactly gfx1100, gfx1102
# and gfx1201 — the same three every ROCm recipe then declared. That agreement
# is the property this check exists to hold, and it is why the declaration was
# widened to six (Decided 2026-09-18) by rebuilding the math libraries first and
# this engine last. The upper bound on this engine's declaration is the math
# libraries, not the compiler, and widening it is a decision about rebuilding
# that stack.
#
# A missing or empty library directory REFUSES. A check with nothing to compare
# against cannot certify anything, and reading it as "no objection" is how a
# gate comes to pass on absence.
llama_assert_math_kernels() {
    _targets="$1"
    _libs="$2"
    _rocm="${ROCM_PATH:-/opt/rocm}"
    # Naming no library would run the loop zero times and return success: a
    # check that asked nothing, reporting that it found nothing wrong. Same
    # rule as the missing and the empty directory below.
    if [ -z "$(printf '%s' "$_libs" | tr -d '[:space:]')" ]; then
        echo "ERROR: no math library was named for this check, so nothing was" >&2
        echo "       verified. The engine's declared architectures have to be" >&2
        echo "       checked against the kernels of the libraries it links." >&2
        echo "       Refusing to seal the archive." >&2
        return 1
    fi
    for _lib in $_libs; do
        _dir="${_rocm}/lib/${_lib}/library"
        if [ ! -d "$_dir" ]; then
            echo "ERROR: no kernel library directory at ${_dir}; cannot verify" >&2
            echo "       that ${_lib} carries kernels for the architectures this" >&2
            echo "       engine declares. Refusing to seal the archive on an" >&2
            echo "       unverified promise." >&2
            return 1
        fi
        _have="$(ls -1 "$_dir" 2>/dev/null \
                 | grep -oE 'gfx[0-9a-z]+' | sort -u | tr '\n' ' ')"
        echo "[math-kernels] ${_lib} carries: ${_have:-(none)}"
        if [ -z "$_have" ]; then
            echo "ERROR: ${_dir} names no GPU architecture at all. Either this" >&2
            echo "       library was built without per-architecture kernels or" >&2
            echo "       its file naming changed. Refusing to seal the archive:" >&2
            echo "       a check that finds nothing has verified nothing." >&2
            return 1
        fi
        _missing=""
        _old_ifs="$IFS"; IFS=';'
        for _t in $_targets; do
            IFS="$_old_ifs"
            case " $_have " in
                *" $_t "*) ;;
                *) _missing="${_missing} ${_t}" ;;
            esac
            IFS=';'
        done
        IFS="$_old_ifs"
        if [ -n "$_missing" ]; then
            echo "ERROR: this engine declares GPU targets that ${_lib} has no" >&2
            echo "       kernels for." >&2
            echo "       declared      : ${_targets}" >&2
            echo "       ${_lib} carries: ${_have}" >&2
            echo "       missing       :${_missing}" >&2
            echo "       The engine would carry its own code objects for those" >&2
            echo "       cards and still fail on the machine, because its matrix" >&2
            echo "       multiplies resolve into this library. Widening this" >&2
            echo "       engine's declaration means rebuilding that library for" >&2
            echo "       the same architectures first. Refusing to seal the" >&2
            echo "       archive." >&2
            return 1
        fi
    done
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

    # Every staged binary this package declares must carry device code for every
    # architecture the recipe declares. Asked of the binaries that ship, after
    # they are staged and before the archive is sealed. verify_paths already
    # requires the architecture RECORD to be present; presence is not content,
    # and the record was shipping empty until this release — so the binary is
    # asked directly rather than the record believed.
    for _b in llama-server llama-cli; do
        llama_assert_device_code "$DESTDIR/opt/rocm/bin/${_b}" \
            "${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}" \
            || return 1
    done

    # And the layers underneath must be able to serve those same cards. See
    # llama_assert_math_kernels for why carrying the code object is not enough,
    # and for why the libraries are named here rather than fixed in the check.
    #
    # THE LIST IS rocBLAS, and it is this recipe's own runtime dependencies read
    # back: dependencies.runtime declares rocm-hip, rocblas and hipblas, which
    # is exactly what ldd reports for the built engine (libhipblas.so.3 and
    # librocblas.so.5, measured 2026-09-18). Of those, rocBLAS is the one that
    # ships per-architecture kernel files — hipBLAS is the dispatch layer whose
    # work resolves into rocBLAS, and the HIP runtime carries no Tensile
    # kernels at all — so rocBLAS is what there is to check. hipBLASLt is NOT
    # here because this engine does not link it. The day hipBLAS ships kernels
    # of its own, or this engine links another library that does, its name is
    # added to this line and the check asks it too.
    llama_assert_math_kernels "${IGOS_GPU_TARGETS}" "rocblas" || return 1

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
    # Read from IGOS_GPU_TARGETS, which the builder exports into EVERY phase's
    # environment from package.yml's gpu_targets (igos-build/builder.py), and
    # with the :? form so an absent declaration fails the build instead of
    # writing a record that says nothing.
    #
    # It was written from $GPU_TARGETS, which configure() sets. Each phase runs
    # in its own shell, so that variable is unset here and the record shipped
    # EMPTY. Measured 2026-09-17 on an installed machine:
    # /opt/rocm/share/llama-cpp-hip/gpu-targets was one byte, a newline
    # (sha256 01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b).
    # intergen.serving_device.hip_build_gpu_targets then read an empty set, and
    # hip_is_supported_here returns None — "I could not tell" — for an empty
    # set, so the check that exists to keep this engine off a card it has no
    # code for could not return False on any installed machine. The engine it
    # guards segfaults at model load on such a card. The sibling CUDA recipe
    # already consumes ${IGOS_GPU_TARGETS} in its own do_install; this one now
    # does the same.
    printf '%s\n' "${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}" > gpu-targets.txt
    install -Dm644 gpu-targets.txt \
        "${DESTDIR}/opt/rocm/share/llama-cpp-hip/gpu-targets"
}
