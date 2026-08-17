#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# llama-cpp-cuda b8796 — LLM inference engine, CUDA variant
# https://github.com/ggml-org/llama.cpp
#
# The opt-in NVIDIA-GPU engine (compute tier, mirror-only). The shipped default
# engine (packages/ai/llama-cpp, Vulkan+CPU) remains untouched — this variant
# exists so an NVIDIA machine can be MEASURED on both engines and run whichever
# is faster on that card and model. It is not assumed to be the faster one: on
# the first card measured (GeForce RTX 3070 Ti Laptop, compute capability 8.6,
# driver 580.159.04, a 9B model at Q4_K_M) the shipped Vulkan engine was faster
# than this one on every metric taken, by 3.1% to 6.5%. The numbers, the method
# and the reason are in docs/CUDA-ENGINE.md, which this recipe must not
# contradict.
#
# COEXISTENCE DESIGN — static, own prefix. Same reasoning as llama-cpp-hip, and
# the shipped engine's own linkage is the proof of the hazard: /usr/bin/llama-server
# resolves libllama.so.0, libggml.so.0, libggml-cpu.so.0, libggml-base.so.0 and
# libggml-vulkan.so.0 out of /usr/lib. A CUDA variant with those same sonames
# anywhere on the loader path would make "which backend am I running" a question
# about ld.so.conf ordering — a silently-wrong-backend class. BUILD_SHARED_LIBS=OFF
# removes it: ggml and llama link statically into this variant's binaries, which
# live wholly under /opt/llama-cpp-cuda. Exactly one package tree-wide claims
# /usr/bin/llama-server, and it is not this one.
#
# WHY /opt/llama-cpp-cuda AND NOT /opt/cuda. The HIP variant installs into
# /opt/rocm because InterGenOS builds and owns that whole prefix. /opt/cuda is
# NOT ours: it is laid down by the cuda-toolkit download helper straight from
# NVIDIA's payload, and every file in it is recorded in that helper's footprint
# manifest. Installing our binaries into it would give two owners to one tree
# and make `pkm remove` ambiguous. This engine takes its own prefix.
#
# LICENSING OF WHAT THIS PACKAGE SHIPS. Every byte in the archive is compiled
# from the llama.cpp source pinned above (MIT). No NVIDIA object code is linked
# in: GGML_STATIC stays OFF, so the CUDA runtime and cuBLAS are resolved at run
# time from /opt/cuda (the download helper's install) and the CUDA driver API
# from the nvidia driver package. The toolkit runfile is a BUILD input only,
# declared redistributable: false so it can never enter a source archive.
#
# ISA floor matches both sibling engines exactly (GGML_NATIVE=OFF + explicit
# AVX2/FMA floor — a distro binary is never compiled for the build VM's CPU;
# see packages/ai/llama-cpp for the full rationale).

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

# Lockstep with package.yml's source[1] and with the cuda-toolkit helper. All
# three carry the same version and the same hash; a bump moves all three.
CUDA_VERSION="13.3.1"
CUDA_RUN="cuda_13.3.1_610.43.02_linux.run"
CUDA_RUN_SHA256="9f98ec1f6c950401041d3f1308e221f0d5db8771a8e10569001b64caaee31a92"

# Where configure() assembles the CUDA toolkit, derived the same way in every
# phase. Each phase function runs in a separate shell but with the same working
# directory (the extracted source root), so re-deriving the path is exact.
#
# The earlier form wrote the path into a fixed name under /tmp in configure()
# and read it back in build() and do_install(). That is a predictable path in a
# world-writable directory whose contents a later phase then trusts, and two
# builds of this package in one work area would overwrite each other's copy.
# Deriving it removes both, and removes the failure where a phase run without
# its predecessor reads a stale path from an earlier build.
cuda_toolkit_root() {
    echo "$(dirname "$PWD")/cuda-toolkit-root"
}

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
    CUDA_ARCHS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    SRC_PARENT="$(dirname "$PWD")"
    CUDA_ROOT="$(cuda_toolkit_root)"

    # ---- The CUDA toolkit: verify, extract, assemble -----------------------
    # source[1] is declared `extract: false`, so the builder staged and
    # sha-verified the runfile and left it alone. Re-verify here anyway: this
    # recipe hands the file to a shell interpreter, and the §2.5 pre-built-
    # artifact pattern is that the consumer checks the hash itself rather than
    # trusting that something upstream did.
    RUN_PATH="${IGOS_SOURCES}/${CUDA_RUN}"
    if [ ! -f "$RUN_PATH" ]; then
        echo "ERROR: ${CUDA_RUN} is not staged at ${RUN_PATH}." >&2
        echo "       It is source[1] of this package; the source-staging sweep" >&2
        echo "       fetches it. If this is a targeted resume, stage it by hand" >&2
        echo "       before rebuilding (build-rules 3.10)." >&2
        return 1
    fi
    got=$(sha256sum "$RUN_PATH" | awk '{print $1}')
    if [ "$got" != "$CUDA_RUN_SHA256" ]; then
        echo "ERROR: ${CUDA_RUN} sha256 mismatch — refusing to unpack." >&2
        echo "       expected ${CUDA_RUN_SHA256}" >&2
        echo "       got      ${got}" >&2
        return 1
    fi
    echo "[llama-cpp-cuda] CUDA ${CUDA_VERSION} runfile verified: ${CUDA_RUN_SHA256}"

    # EXTRACT, NEVER EXECUTE (ruled 2026-06-11, decision 5). Two flags carry
    # the whole behaviour and both are load-bearing:
    #
    #   --extract=<abs>  the CUDA 13.x runfile's extract-only flag. The ruling
    #                    was written against makeself's `--target … --noexec`;
    #                    this runfile is not a plain makeself archive and
    #                    rejects that form. Verified against the pinned file.
    #   --nox11          without it the wrapper, finding no tty but a DISPLAY,
    #                    tries to re-launch itself inside a terminal emulator,
    #                    finds none of the ones it guesses at, and dies with
    #                    `exec: -t: invalid option`. A build must never depend
    #                    on a graphical session or on someone being present.
    rm -rf "$CUDA_ROOT" "$SRC_PARENT/cuda-extract"
    mkdir -p "$SRC_PARENT/cuda-extract" "$CUDA_ROOT"
    sh "$RUN_PATH" --nox11 --extract="$SRC_PARENT/cuda-extract"

    # The payload is one directory per toolkit component, each already carrying
    # the standard targets/x86_64-linux layout plus include/ and lib64/ symlinks
    # into it, so merging the component directories reproduces the tree NVIDIA's
    # own --toolkit install produces. rsync rather than cp: the per-component
    # include/lib64 symlinks repeat and must be replaced, not followed.
    # `bin/` holds only NVIDIA's uninstallers and is skipped.
    for comp in "$SRC_PARENT/cuda-extract"/*/; do
        case "$(basename "$comp")" in bin) continue ;; esac
        rsync -a "$comp" "$CUDA_ROOT/"
    done
    for required in bin/nvcc include/cuda_runtime.h lib64/libcudart.so \
                    lib64/libcublas.so lib64/stubs/libcuda.so; do
        if [ ! -e "$CUDA_ROOT/$required" ]; then
            echo "ERROR: assembled toolkit lacks ${required} — NVIDIA's payload" \
                 "layout changed; the recipe needs updating, not a workaround." >&2
            return 1
        fi
    done

    # ---- --list-devices PCI-id suffix --------------------------------------
    # The SAME patch as packages/ai/llama-cpp and packages/compute/llama-cpp-hip
    # (byte-identical file, one-pin policy): the serving-device selector maps a
    # ggml device to its kernel DRM card by the bracketed PCI id this adds to
    # each --list-devices line. See the ai recipe's configure() comment for the
    # full rationale. Applied to the llama.cpp source tree (the working
    # directory here), never to the NVIDIA payload. Fails LOUD if a future pin
    # bump moves the printer — re-derive the patch then.
    patch -p1 < "$BUILD_DIR/list-devices-pci-id.patch"

    # ---- Configure ---------------------------------------------------------
    # GGML_CUDA_NCCL is ON by default upstream, and NCCL is NOT part of the
    # toolkit runfile — leaving the default would emit a "NCCL not found,
    # performance for multiple CUDA GPUs will be suboptimal" warning and build
    # a different engine than the flags say. Turned OFF explicitly: this is a
    # single-GPU engine, and adding NVIDIA's collective-communications library
    # would be another proprietary payload to answer for.
    #
    # GGML_STATIC is left OFF (upstream default) deliberately: ON would link
    # cudart/cuBLAS statically, putting NVIDIA object code inside the archive
    # we publish. OFF keeps them dynamic — resolved from /opt/cuda at run time.
    #
    # CMAKE_SKIP_RPATH is ON because do_install stages the binaries out of the
    # build directory by name rather than running `cmake --install`. cmake
    # rewrites the run-time library path only on ITS install step, so a binary
    # copied straight out of build/bin keeps the BUILD rpath — measured here on
    # 2026-08-04, where the staged llama-server carried
    # DT_RUNPATH "<the build machine's toolkit directory>:". That is wrong three
    # times over: it names a directory that does not exist on a user's machine,
    # it puts a build-host filesystem path inside a binary we publish, and its
    # trailing empty element makes the loader search the current working
    # directory before the system path. This engine needs no rpath at all — the
    # CUDA runtime is found through /etc/ld.so.conf.d/cuda.conf, which the
    # cuda-toolkit helper owns, and that single owner is the design. do_install
    # asserts the result rather than trusting this flag.
    #
    # LLAMA_BUILD_TESTS and LLAMA_BUILD_EXAMPLES are turned OFF, which is a
    # divergence from both sibling engine recipes and the reason is measured.
    # Upstream defaults both ON when llama.cpp is the top-level project, and
    # `cmake --install` then installs every binary they produce. Combined with
    # BUILD_SHARED_LIBS=OFF, which this recipe needs so the engines cannot
    # shadow each other's libraries, EVERY produced binary carries its own copy
    # of the CUDA kernels for all six declared architectures — about 150 MiB
    # each. Measured on this pin with the flags left at their defaults, the
    # staged install came to 10,983 MiB across 115 files, of which the three
    # binaries this package declares are 459 MiB and the remaining 10,524 MiB
    # are 37 test executables and 41 example and tool binaries the package does
    # not declare, does not document and does not need. Turning both off means
    # they are never compiled, so the build is also substantially shorter.
    build_number="$(llama_pin_build_number)" || return 1

    mkdir -p build
    PATH="$CUDA_ROOT/bin:$PATH" \
    CUDAToolkit_ROOT="$CUDA_ROOT" \
    cmake -S . -B build \
        -DCMAKE_INSTALL_PREFIX=/opt/llama-cpp-cuda \
        -DCMAKE_BUILD_TYPE=Release \
        -DLLAMA_BUILD_NUMBER="${build_number}" \
        -DLLAMA_BUILD_SERVER=ON \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
        -DBUILD_SHARED_LIBS=OFF \
        -DGGML_NATIVE=OFF \
        -DGGML_AVX=ON \
        -DGGML_AVX2=ON \
        -DGGML_CUDA=ON \
        -DGGML_CUDA_NCCL=OFF \
        -DCMAKE_SKIP_RPATH=ON \
        -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHS}" \
        -DCMAKE_CUDA_COMPILER="$CUDA_ROOT/bin/nvcc" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    CUDA_ROOT="$(cuda_toolkit_root)"
    if [ ! -x "$CUDA_ROOT/bin/nvcc" ]; then
        echo "ERROR: no assembled CUDA toolkit at ${CUDA_ROOT} — configure()" \
             "did not run in this work area. Refusing to build without the" \
             "compiler the recipe pins." >&2
        return 1
    fi
    PATH="$CUDA_ROOT/bin:$PATH" cmake --build build -j "${IGOS_JOBS}"
}

check() {
    set -e
    # Device-side tests need a CUDA device; the chroot has none. The compile
    # across every declared architecture IS the build-time proof, and the
    # recipe's tests: block records why (package.yml). Device behaviour is
    # proven on real NVIDIA hardware.
    pkg_run_tests "$BUILD_DIR/package.yml" true
}

do_install() {
    set -e

    # Install exactly what this package declares, by name — not `cmake
    # --install`, which stages upstream's whole install set. Two reasons, both
    # measured on this pin:
    #
    #   Size. Every binary here is statically linked and carries the CUDA
    #   kernels for six architectures, so upstream's install set staged at
    #   10,983 MiB. Compiling tests and examples out (above) removes most of
    #   that, but `cmake --install` would still stage the remaining tool
    #   binaries, the static libraries and the headers. The three binaries
    #   below are 459 MiB, and are the whole package.
    #
    #   Honesty. verify_paths names three binaries and the documentation
    #   describes three binaries. Installing more than that ships files the
    #   package never claims, which is the same shape of unstated content the
    #   verify_paths rule exists to prevent — and it is how the default engine
    #   came to put 37 files named test-* straight into /usr/bin on every
    #   installed machine.
    #
    # The static libraries and headers upstream also installs are for building
    # other programs against llama.cpp. That is the default engine's job — it
    # installs shared libraries into /usr/lib for exactly that. This variant is
    # a self-contained alternative engine, not a development target.
    install -d -m 755 "$DESTDIR/opt/llama-cpp-cuda/bin"
    for b in llama-server llama-cli llama-bench; do
        if [ ! -f "build/bin/$b" ]; then
            echo "ERROR: build/bin/${b} was not produced. This package declares" \
                 "it in verify_paths; refusing to stage an install without it." >&2
            return 1
        fi
        install -m 755 "build/bin/$b" "$DESTDIR/opt/llama-cpp-cuda/bin/$b"
    done

    # No run-time library path may survive into a published binary. See the
    # CMAKE_SKIP_RPATH note in configure() for what was measured without this.
    # Checked rather than assumed: the flag is upstream's to honour, and a
    # binary that names a build machine's directories is not one we publish.
    for b in llama-server llama-cli llama-bench; do
        rp=$(readelf -d "$DESTDIR/opt/llama-cpp-cuda/bin/$b" 2>/dev/null \
             | grep -iE '\((RPATH|RUNPATH)\)' || true)
        if [ -n "$rp" ]; then
            echo "ERROR: ${b} carries a run-time library path:" >&2
            echo "  ${rp}" >&2
            echo "       A published binary must resolve the CUDA runtime through" \
                 "/etc/ld.so.conf.d/cuda.conf, not through a path baked in at" \
                 "build time. Refusing to seal the archive." >&2
            return 1
        fi
    done

    # Nothing NVIDIA-owned may leave with this package. The toolkit was a build
    # input; if any of its files ever reached DESTDIR — through an upstream
    # cmake install rule, or a future flag that bundles the runtime — this
    # package would be redistributing them. Assert it rather than assume it.
    if find "$DESTDIR" -name 'libcudart*' -o -name 'libcublas*' -o -name 'nvcc' \
            | grep -q .; then
        echo "ERROR: CUDA toolkit files found in DESTDIR. This package must ship" \
             "only its own compiled output; the toolkit reaches machines through" \
             "the cuda-toolkit download helper. Refusing to seal the archive." >&2
        find "$DESTDIR" -name 'libcudart*' -o -name 'libcublas*' -o -name 'nvcc' >&2
        exit 1
    fi

    # A post-install hook, not a silent dependency. pkm never runs a download
    # helper on a user's behalf (it will not accept a vendor licence for them),
    # so pulling cuda-toolkit in as a runtime dependency puts the HELPER on the
    # machine but not the toolkit. Without the toolkit these binaries fail to
    # start with a missing-soname error, which is loud but unhelpful. Say the
    # one command that fixes it, at install time, before anyone hits that.
    install -d -m 755 "$DESTDIR/var/lib/pkm/hooks/llama-cpp-cuda"
    install -m 755 "$BUILD_DIR/hooks/post-install.sh" \
        "$DESTDIR/var/lib/pkm/hooks/llama-cpp-cuda/post-install"

    install -d -m 755 "$DESTDIR/usr/share/doc/llama-cpp-cuda"
    install -m 644 "$BUILD_DIR/docs/CUDA-ENGINE.md" \
        "$DESTDIR/usr/share/doc/llama-cpp-cuda/CUDA-ENGINE.md"

    # The staged binary must REPORT the pinned build number. Asked of the
    # binary that ships. The loader path is the assembled toolkit, because
    # these binaries resolve the CUDA runtime dynamically (GGML_STATIC stays
    # OFF by design) and on an installed machine that comes from /opt/cuda via
    # the cuda-toolkit helper's ld.so.conf entry, which does not exist in a
    # build chroot.
    #
    # The backend DOES initialise before the version line is printed —
    # measured here 2026-08-04, where the output opened with two ggml_cuda_init
    # lines — which is why the check matches that line wherever it appears
    # instead of expecting it first. A chroot without a GPU is not a problem
    # for it: with no device visible the binary prints "failed to initialize
    # CUDA" and still reports its version, exit 0 (measured the same day by
    # hiding the devices; a machine with no driver at all is what the
    # build-substrate run settles).
    llama_assert_build_number "$DESTDIR/opt/llama-cpp-cuda/bin/llama-cli" \
                              "$(cuda_toolkit_root)/lib64" || return 1
}
