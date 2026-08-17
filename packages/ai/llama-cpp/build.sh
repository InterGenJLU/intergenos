#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# llama-cpp b8796 — LLM inference engine
# https://github.com/ggml-org/llama.cpp
#
# Installs: llama-server (HTTP API), llama-cli, llama-bench, the engine's shared
# libraries, and the headers/cmake/pkg-config files programs build against.
#
# Vulkan + CPU build (the GPU-engine plan's ruled DEFAULT, r2/PI-Z26): one
# binary serves every modern GPU through the loader+ICD model — NVIDIA via the
# proprietary driver's ICD, AMD via RADV, Intel via ANV — and falls back to the
# retained CPU backend cleanly when no usable Vulkan device is present. The
# loader (vulkan-loader) is the ONLY hard runtime dep; per-vendor ICDs live
# with the GPU stack and their absence is a clean CPU no-op, never a failure.
# glslc (shaderc) compiles the backend's shaders at BUILD time only. Built
# against the tree's CURRENT shaderc (P7-A amendment 5: one-version policy —
# the 2025.2-era bfloat16-probe bug fails LOUD here at build if it ever
# resurfaces; patch-don't-pin in that case).
#
# ISA floor is set explicitly for portability: a distro binary must NOT be
# compiled for the build VM's CPU. GGML_NATIVE defaults ON (ggml/CMakeLists.txt),
# which bakes in -march=native = the builder's full ISA (the Ryzen 9 5900X has
# AVX2/FMA/BMI2) -> SIGILL on any target lacking those. We instead pin
# GGML_NATIVE=OFF + an explicit AVX2 floor (x86-64-v3 class), the exact set the
# .192 `build-a12safe` config validated on the target A12 (AMD A12-9720P,
# Excavator: avx2/fma/f16c/bmi2/abm all present). FMA/F16C ride along with AVX2
# in ggml; AVX512 stays OFF. Floor = Haswell-2013+/Excavator.

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
    # --list-devices gains a bracketed PCI-id suffix per device ([PCI
    # domain:bus:device.function]) so the serving-device selector can map a
    # ggml device to its kernel DRM card — required to tell two identical
    # GPUs apart and avoid the one driving displays. The id comes from
    # ggml_backend_dev_props.device_id, which the Vulkan and HIP/CUDA
    # backends already populate at this pin; only the printer omitted it.
    # Same patch rides every engine variant (one-pin policy). Fails LOUD if
    # a future pin bump moves the printer — re-derive the patch then.
    BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    patch -p1 < "$BUILD_DIR/list-devices-pci-id.patch"

    mkdir -p build
    cd    build

    # No curl/model-download support in llama-server BY DESIGN: upstream
    # deprecated LLAMA_CURL with no successor (ignored at this pin) and the
    # shipped binary links no libcurl — verified 2026-07-11. InterGen owns
    # model acquisition via the signed models manifest; the inference
    # server never fetches from the network itself (decided).
    # (r4: this block previously sat INSIDE the cmake arg list, where bash
    # terminates the command at the comment — cmake configured with only the
    # first 4 args and the next flag line died as a command, exit 127.
    # Comments never go inside a line-continued invocation.)
    # WHAT THIS PACKAGE INSTALLS, AND WHY THE NEXT FOUR FLAGS EXIST.
    # Decided 2026-08-04. Upstream defaults LLAMA_BUILD_TESTS, LLAMA_BUILD_TOOLS
    # and LLAMA_BUILD_EXAMPLES ON when llama.cpp is the top-level project, and
    # `make install` then installs a binary for every one of them into
    # /usr/bin. Measured on this pin with the flags left at their defaults, the
    # staged install set was 115 files and 276.4 MiB, of which /usr/bin alone
    # held 81 files and 209.9 MiB — 37 of them named test-*, taking 39.1 MiB.
    # Names like test-log, test-opt, test-rope, test-sampling and
    # export-graph-ops are generic enough to collide with another package's
    # tool in the global namespace, and none of them is declared, documented or
    # needed on an installed machine.
    #
    #   LLAMA_BUILD_TESTS=OFF     the 37 test-* binaries plus export-graph-ops
    #                             are not compiled, so they cannot be installed.
    #   LLAMA_BUILD_EXAMPLES=OFF  the same for the example and proof-of-concept
    #                             binaries (llama-simple, llama-lookup*,
    #                             llama-passkey and the rest of that set).
    #   LLAMA_TOOLS_INSTALL=OFF   the tools ARE still built — llama-cli and
    #                             llama-server live under tools/ — but upstream
    #                             stops installing all of them. do_install then
    #                             stages the three this package declares, by
    #                             name, and refuses anything else.
    #   CMAKE_SKIP_RPATH=ON       required BECAUSE do_install stages two of
    #                             those binaries out of the build directory
    #                             rather than through cmake's install step.
    #                             cmake rewrites the run-time library path only
    #                             on ITS install, so a binary copied straight
    #                             out of build/bin keeps the BUILD one —
    #                             measured here, where build/bin/llama-cli
    #                             carried a DT_RUNPATH naming the build
    #                             machine's own build directory with a trailing
    #                             empty element, which makes the loader search
    #                             the current working directory first. This
    #                             engine needs no run-time library path at all:
    #                             its libraries install into /usr/lib, which the
    #                             loader searches by default, and the binaries
    #                             that cmake installs today already carry none.
    #                             do_install asserts the result rather than
    #                             trusting the flag.
    #
    # The shared libraries, headers, cmake package files and llama.pc stay on
    # upstream's own install rules. They are this package's product — it is the
    # engine other programs build against, which is exactly what the two static
    # variants under packages/compute are not — and staging them by name would
    # silently drop a library whose soname changes on a pin bump.
    build_number="$(llama_pin_build_number)" || return 1

    cmake .. \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_BUILD_TYPE=Release \
        -DLLAMA_BUILD_NUMBER="${build_number}" \
        -DLLAMA_BUILD_SERVER=ON \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
        -DLLAMA_TOOLS_INSTALL=OFF \
        -DBUILD_SHARED_LIBS=ON \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -DCMAKE_SKIP_RPATH=ON \
        -DGGML_NATIVE=OFF \
        -DGGML_AVX=ON \
        -DGGML_AVX2=ON \
        -DGGML_VULKAN=ON \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

# The binaries this package installs into /usr/bin. Every one is declared in
# package.yml's verify_paths. llama-bench is here for a second reason as well:
# the CUDA variant's shipped documentation and its post-install message both
# tell the user to run /usr/bin/llama-bench to compare that engine against this
# one, so dropping it would turn a shipped instruction into a dead path.
DECLARED_BINARIES="llama-server llama-cli llama-bench"

do_install() {
    set -e
    cd build

    # Upstream's own install rules: the shared libraries, the public headers,
    # the cmake package files and llama.pc — plus llama-server, whose install
    # rule is the one under tools/ that LLAMA_TOOLS_INSTALL does not guard.
    make DESTDIR="$DESTDIR" install

    # convert_hf_to_gguf.py is installed unconditionally by the top-level
    # CMakeLists and upstream offers no switch for it. It is a
    # HuggingFace-to-GGUF conversion script that imports transformers, torch
    # and the gguf python package on its first lines; an installed system has
    # none of the three, so the shipped copy exits with ModuleNotFoundError
    # before doing anything — measured on an installed machine, 2026-08-04. A
    # command in /usr/bin that cannot run is a claim the system does not
    # honour. It is removed here because it cannot be prevented at configure
    # time, and the check below is what stops that removal from silently
    # ceasing to happen.
    rm -f "$DESTDIR/usr/bin/convert_hf_to_gguf.py"

    # The two declared binaries upstream no longer installs, staged by name.
    install -d -m 755 "$DESTDIR/usr/bin"
    for b in llama-cli llama-bench; do
        if [ ! -f "bin/$b" ]; then
            echo "ERROR: bin/${b} was not produced. This package declares it in" \
                 "verify_paths; refusing to stage an install without it." >&2
            return 1
        fi
        install -m 755 "bin/$b" "$DESTDIR/usr/bin/$b"
    done

    # ---- What may leave in /usr/bin, checked rather than assumed ------------
    # The flags above decide what gets built and what upstream installs, but a
    # pin bump can add a tool, move a test out of tests/, or install something
    # from a rule that no option guards — which is how 37 binaries named test-*
    # came to sit in /usr/bin on every installed machine. This refuses the
    # archive instead of shipping the surprise.
    # find, not ls: `ls` honours an alias when one is set, and an aliased ls
    # (-F appends a type marker, -a adds . and ..) makes this comparison fail
    # against a perfectly correct staged tree. Measured here 2026-08-04 —
    # re-running this function from a shell that carries the fleet's usual
    # `ls` alias produced "./ ../ llama-bench* llama-cli* llama-server*" and a
    # false refusal. The package builder runs non-interactively, where aliases
    # do not expand, so the shipped path was never wrong; a gate whose verdict
    # depends on the caller's shell is still the wrong mechanism.
    staged=$(find "$DESTDIR/usr/bin" -mindepth 1 -maxdepth 1 -printf '%f\n' \
             | sort | tr '\n' ' ')
    expected=$(echo $DECLARED_BINARIES | tr ' ' '\n' | sort | tr '\n' ' ')
    if [ "$staged" != "$expected" ]; then
        echo "ERROR: /usr/bin holds something this package does not declare." >&2
        echo "       expected: ${expected}" >&2
        echo "       staged:   ${staged}" >&2
        echo "       Either declare the new file in verify_paths and add it to" \
             "DECLARED_BINARIES, or stop installing it. Refusing to seal the" \
             "archive." >&2
        return 1
    fi

    # Nothing named test-* anywhere in the staged tree, not just in /usr/bin.
    # This is the defect class itself, so it is checked directly rather than
    # inferred from the flags that are supposed to prevent it.
    if find "$DESTDIR" -name 'test-*' | grep -q .; then
        echo "ERROR: test binaries reached DESTDIR despite LLAMA_BUILD_TESTS=OFF:" >&2
        find "$DESTDIR" -name 'test-*' >&2
        return 1
    fi

    # No run-time library path may survive into a published binary or library.
    # Two of the binaries above are copied out of the build directory, where
    # cmake writes a DT_RUNPATH pointing at the build machine's own directories
    # unless CMAKE_SKIP_RPATH is honoured. See the configure() note for what was
    # measured without the flag. Checked, because the flag is upstream's to
    # honour and a binary naming a build machine's filesystem is not one we
    # publish.
    # The check runs through find -exec rather than a pipeline into a while
    # loop: a `read` loop on the right of a pipe runs in a subshell, so its
    # failure exit would end the subshell and let this function return success.
    rpath_hits=$(find "$DESTDIR" -type f \
                     \( -name '*.so' -o -name '*.so.*' -o -path '*/bin/*' \) \
                     -exec sh -c '
                         for f do
                             if readelf -d "$f" 2>/dev/null \
                                | grep -qiE "\((RPATH|RUNPATH)\)"; then
                                 printf "%s\n" "$f"
                             fi
                         done' sh {} +)
    if [ -n "$rpath_hits" ]; then
        echo "ERROR: staged files carry a run-time library path:" >&2
        printf '%s\n' "$rpath_hits" >&2
        echo "       This engine resolves its libraries from /usr/lib, which the" \
             "loader searches by default; a published binary must not name a" \
             "build machine's directories. Refusing to seal the archive." >&2
        return 1
    fi

    # Every SONAME symlink the binaries load must resolve to a real file.
    # package.yml declares the SONAMEs, and the squashfs verify_paths audit
    # checks them with lexists — which a DANGLING symlink already satisfies —
    # so the real-file guarantee is made here, where it can be derived from
    # whatever the build produced instead of being written as a filename that
    # carries the build number a third time.
    for soname in libllama.so.0 libggml.so.0 libggml-base.so.0 \
                  libggml-cpu.so.0 libggml-vulkan.so.0 libmtmd.so.0; do
        link="$DESTDIR/usr/lib/$soname"
        if [ ! -L "$link" ]; then
            echo "ERROR: /usr/lib/${soname} is not a symlink in the staged tree." \
                 "The engine's binaries load it by that name; refusing to seal" \
                 "the archive." >&2
            return 1
        fi
        if [ ! -f "$(readlink -f "$link")" ]; then
            echo "ERROR: /usr/lib/${soname} does not resolve to a real file" \
                 "(points at '$(readlink "$link")'). A dangling SONAME ships an" \
                 "engine that cannot load; refusing to seal the archive." >&2
            return 1
        fi
    done

    # The staged binary must REPORT the pinned build number. Asked of the
    # binary that ships, not of the one in the build tree, and pointed at the
    # libraries staged beside it because nothing is installed on the system
    # yet and these carry no run-time library path.
    llama_assert_build_number "$DESTDIR/usr/bin/llama-cli" "$DESTDIR/usr/lib" || return 1
}
