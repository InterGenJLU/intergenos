#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# lib32-llvm 21.1.8 — LLVM 32-bit runtime + build interface (GE arc, Wave 2)
# Sibling: packages/core/llvm (same tarballs, same version — RT-9 +
# LIB32-SOURCE-DRIFT locks). Authoring spec + research grounding:
# docs/sessions/2026-07-02-ge-lib32-llvm-authoring-spec.md
#
# LLVM-only (no clang / no compiler-rt — the twin serves lib32-mesa's
# radeonsi codegen + the libLLVM runtime mesa dlopens). Built through THE
# cmake toolchain-file twin (config/lib32/lib32-cmake-toolchain.cmake,
# landed with this recipe — the builder styles fail-close cmake for
# elf_class-32 recipes that do not pass it explicitly).

source /mnt/intergenos/scripts/lib32-env.sh

pre_configure() {
    set -e -o pipefail
    tar -xf "${IGOS_SOURCES}/llvm-cmake-${PKG_VERSION}.src.tar.xz"
    tar -xf "${IGOS_SOURCES}/llvm-third-party-${PKG_VERSION}.src.tar.xz"
    sed "/LLVM_COMMON_CMAKE_UTILS/s@../cmake@cmake-${PKG_VERSION}.src@" \
        -i CMakeLists.txt
    # third-party kept: HandleLLVMOptions references it even without
    # clang/compiler-rt in-tree (verified against the sibling's sed target).
    sed "/LLVM_THIRD_PARTY_DIR/s@../third-party@third-party-${PKG_VERSION}.src@" \
        -i cmake/modules/HandleLLVMOptions.cmake
    grep -rl '#!.*python' | xargs sed -i '1s/python$/python3/'
    # NO clang, NO compiler-rt extraction — LLVM-only twin.
}

configure() {
    set -e
    pre_configure

    mkdir -v build
    cd       build

    # -m32/-U_TIME_BITS + the find_library lib32 pinning live in the
    # toolchain file, NOT here (one definition). X86 is named literally:
    # "host" would resolve x86_64 under -m32. AMDGPU = radeonsi codegen.
    # LLVM_ENABLE_LIBXML2=OFF explicitly: llvm's libxml2 use is optional
    # tooling outside this twin's scope, and lib32-libxml2 is not in the
    # adopted set — an explicit OFF beats a silent auto-disable.
    cmake -G Ninja -W no-dev                                                  \
          -D CMAKE_TOOLCHAIN_FILE=/mnt/intergenos/config/lib32/lib32-cmake-toolchain.cmake \
          -D CMAKE_INSTALL_PREFIX=/usr                                        \
          -D CMAKE_BUILD_TYPE=Release                                         \
          -D CMAKE_SKIP_INSTALL_RPATH=ON                                      \
          -D LLVM_LIBDIR_SUFFIX=32                                            \
          -D LLVM_TARGETS_TO_BUILD="X86;AMDGPU"                               \
          -D LLVM_TARGET_ARCH=i686                                            \
          -D LLVM_HOST_TRIPLE=i686-pc-linux-gnu                               \
          -D LLVM_DEFAULT_TARGET_TRIPLE=i686-pc-linux-gnu                     \
          -D LLVM_BUILD_LLVM_DYLIB=ON                                         \
          -D LLVM_LINK_LLVM_DYLIB=ON                                          \
          -D LLVM_ENABLE_RTTI=ON                                              \
          -D LLVM_ENABLE_FFI=ON                                               \
          -D LLVM_ENABLE_LIBXML2=OFF                                          \
          -D LLVM_ENABLE_BINDINGS=OFF                                         \
          -D LLVM_INCLUDE_BENCHMARKS=OFF                                      \
          -D LLVM_BUILD_DOCS=OFF                                              \
          -D LLVM_ENABLE_SPHINX=OFF                                           \
          -D LLVM_BINUTILS_INCDIR=/usr/include                                \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5                                 \
          ..
}

build() {
    set -e
    cd build
    # -v MANDATORY: the archive-time time64 log assertion refuses a log
    # with no visible compile evidence (RT-8/F2-a).
    ninja -v
}

do_install() {
    set -e
    cd build
    DESTDIR="$PWD/m32root" ninja install

    # --- collapse the reference split to our single-package shape ---
    # Static libs stay (cmake export set references them), non-executable.
    chmod -x m32root/usr/lib32/*.a 2>/dev/null || true

    # The ONE 32-bit-specific header (the 64-bit dispatcher includes it
    # under -m32 — the stubs-32.h pattern) + the genuine 32-bit-built
    # llvm-config, renamed. Everything else under bin/include/share is
    # owned by the 64-bit sibling and never staged.
    install -dm755 "${DESTDIR}/usr/include/llvm/Config"
    install -m644 m32root/usr/include/llvm/Config/llvm-config.h \
                  "${DESTDIR}/usr/include/llvm/Config/llvm-config-32.h"
    install -dm755 "${DESTDIR}/usr/bin"
    install -m755 m32root/usr/bin/llvm-config "${DESTDIR}/usr/bin/llvm-config32"

    # 32-bit gold plugin symlink, mirroring the reference layout.
    if [ -f m32root/usr/lib32/LLVMgold.so ]; then
        install -dm755 m32root/usr/lib32/bfd-plugins
        ln -sf ../LLVMgold.so m32root/usr/lib32/bfd-plugins/LLVMgold.so
    fi

    # Allowlist-stage the lib32 tree; the two extras above were installed
    # directly (they are transformed/renamed, not verbatim root copies).
    # Then the fail-loud assertions — files AND directories outside the
    # allowlist halt; the extras are DECLARED to the assert.
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32 \
        usr/bin/llvm-config32 \
        usr/include/llvm/Config/llvm-config-32.h

    # --- RT-7 acceptance assertion (the whole point of the respec) ---
    # The staged llvm-config32 must answer from the 32-bit world. METHOD
    # FIX (launch-7 halt 2026-07-03): llvm-config does NOT bake its paths —
    # it computes the prefix RELATIVE to its own location (verified: zero
    # staging strings in the binary; run from /tmp it answers //lib32), so
    # the STAGED copy correctly answers <staging>/usr/lib32 and the old
    # "== /usr/lib32" expectation false-FATAL'd on working payload. The
    # honest invariants, each catching a real defect mode:
    #   1. staged --libdir == <staging>/usr/lib32 exactly — proves the
    #      relative-prefix math lands on the lib32 suffix (a lib-vs-lib32
    #      misconfigure still refuses); deployed to /usr/bin the same math
    #      yields /usr/lib32.
    #   2. NO staging-root path baked in the binary — the defect the old
    #      check thought it was catching (an install-prefix leak embeds
    #      it; DESTDIR-driven installs never do).
    #   3. --host-target answers an i686 triple — the baked 32-bit
    #      identity. The original "-m32 in --cflags" expectation was NEVER
    #      satisfiable: llvm-config emits only includes + definitions in
    #      --cflags under our configuration (the 64-bit sibling likewise
    #      shows no -march baseline flags); the width flag flows via the
    #      toolchain file at BUILD time and is not reported. host-target
    #      is configure-baked and answers the width question honestly.
    local cfgbin="${DESTDIR}/usr/bin/llvm-config32"
    local libdir host_target
    libdir=$("$cfgbin" --libdir)
    host_target=$("$cfgbin" --host-target)
    if [ "$libdir" != "${DESTDIR}/usr/lib32" ]; then
        echo "FATAL: staged llvm-config32 --libdir answered '$libdir', expected '${DESTDIR}/usr/lib32' (relative-prefix math broken or wrong libdir)" >&2
        return 1
    fi
    if grep -q "$DESTDIR" "$cfgbin"; then
        echo "FATAL: llvm-config32 has the staging root '$DESTDIR' BAKED into the binary (install-prefix leak) — it would answer staging paths after deploy" >&2
        return 1
    fi
    case "$host_target" in
        i686-*|i586-*|i386-*) : ;;
        *) echo "FATAL: llvm-config32 --host-target answered '$host_target', not an i686-class triple — the twin was not configured 32-bit" >&2
           return 1 ;;
    esac
    echo "lib32-llvm: llvm-config32 acceptance PASSED (staged libdir suffix correct, no baked staging path, host-target=$host_target)"

    lib32_env_end
}
