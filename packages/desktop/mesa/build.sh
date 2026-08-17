#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# mesa 25.3.5 — OpenGL, Vulkan, and OpenCL implementation
# BLFS 13.0

configure() {
    set -e
    # Pre-place Rust crate tarballs for offline build.
    # NVK (Nouveau Vulkan) and other Rust-based components require 27 crates
    # from crates.io. Since the chroot has no internet, we pre-download them
    # on the host and archive them as mesa-25.3.5-rust-crates.tar.gz.
    # Meson checks subprojects/packagecache/ before attempting downloads.
    if [ -f "${IGOS_SOURCES}/mesa-25.3.5-rust-crates.tar.gz" ]; then
        mkdir -p subprojects/packagecache
        tar -xf "${IGOS_SOURCES}/mesa-25.3.5-rust-crates.tar.gz" \
            -C subprojects/packagecache
    fi

    # DirectX-Headers subproject (L17): d3d12 makes dep_dxheaders REQUIRED
    # and upstream's DirectX-Headers.wrap is WRAP-GIT — packagecache cannot
    # serve it; meson needs the vendored directory itself. Extract the
    # pinned tag tarball to the wrap's declared directory (Rule 5 explicit
    # secondary-source extraction; the crates block above is the sibling
    # precedent). Fail loudly if absent — a missing vendored subproject
    # under wrap-mode=nodownload would otherwise die mid-configure.
    tar -xf "${IGOS_SOURCES}/DirectX-Headers-1.618.1.tar.gz"
    rm -rf subprojects/DirectX-Headers-1.0
    mv DirectX-Headers-1.618.1 subprojects/DirectX-Headers-1.0

    # Post-patch fix for the 2020 BLFS xdemos patch (mesa-add_xdemos-4.patch,
    # diffed against mesa-20.2.1): it links glxgears/glxinfo via
    # `link_with: [libgl, glx_gallium_link]` — Mesa's INTERNAL gl target, which
    # under GLVND (enabled here; libglvnd is in-tree) is the vendor lib
    # (libGLX_mesa) and does NOT export the public gl*/glX* entry points, so the
    # demos fail to link with undefined references (glTranslated, glXSwapBuffers,
    # glMatrixMode, ...). Those symbols live in the GLVND public libGL. Re-link
    # the demos against it (gl.pc -> -lGL) instead of Mesa's internal target.
    # 2026-06-02. (Durable alternative, if this patch keeps drifting per Mesa
    # bump: move the demos to a standalone mesa-demos package, their upstream home.)
    local xdemos=src/glx/xdemos/meson.build
    if [ -f "$xdemos" ]; then
        sed -i \
            -e "/link_with : \[libgl, glx_gallium_link\],/d" \
            -e "s/dependencies : \[x11_dep\]/dependencies : [x11_dep, dependency('gl')]/" \
            "$xdemos"
    fi

    mkdir -p build
    cd    build

    # Build flags rationale:
    #   -D platforms=x11,wayland     X11 (XWayland) + native Wayland WSI
    #   -D gallium-drivers=auto      enables radeonsi (AMD GCN1+) +
    #                                r600/r300 (pre-GCN AMD) + nouveau
    #                                + iris (Intel) + zink + others on x86_64
    #   -D vulkan-drivers=auto       enables radv (AMD Vulkan) + anv
    #                                (Intel Vulkan) + lavapipe (software)
    #   -D valgrind=disabled         no Valgrind suppressions; runtime cost
    #   -D video-codecs=all          enable every VAAPI codec the radeonsi
    #                                back-end supports (H264/H265/AV1/etc)
    #   -D libunwind=disabled        no libunwind dep; uses glibc backtrace
    #   -D gallium-rusticl=true      enable Rusticl, Mesa's open-source
    #                                OpenCL implementation. Targets radeonsi
    #                                (AMD) and iris (Intel) gallium drivers.
    #                                Cross-distro alignment: Arch enables;
    #                                Fedora enables; Debian enables. Builds
    #                                /usr/lib/libRusticlOpenCL.so +
    #                                /etc/OpenCL/vendors/rusticl.icd manifest.
    #                                Activated at runtime by setting
    #                                RUSTICL_ENABLE=radeonsi (or iris). Zero
    #                                cost when not activated.
    #   (SPIR-V kernel ingestion for rusticl is now IMPLICIT — the separate
    #    `opencl-spirv` meson option was REMOVED upstream in Mesa 25.x because
    #    SPIR-V is mandatory for rusticl, not optional. `gallium-rusticl=true`
    #    alone enables it; the libclc + spirv-tools + spirv-llvm-translator deps
    #    already in tree provide the libraries. 25.3.5 configure errors on the
    #    old option as "Unknown option". 2026-06-02.)
    # RT-3 (GE arc, 2026-07-02): the full feature surface is EXPLICIT — the
    # former gallium/vulkan `auto` lists are FROZEN to what auto resolved to
    # on this tree (the pin-time check), and the feature options that used
    # to ride meson's auto (egl/gbm/glvnd/gles*/llvm/...) are pinned so a
    # missing dep HARD-ERRORS at configure instead of silently disabling a
    # surface (the RT-3 disease). ONE deliberate change from the frozen
    # state: gles1=disabled (decided 2026-07-02 — no known
    # consumer; matches the lib32 stack). The matrix is duplicated in
    # feature-matrix.json and asserted by the checker call below — the two
    # cannot drift without the build refusing.
    meson setup ..                        \
          --prefix=/usr                   \
          --libdir=/usr/lib               \
          --buildtype=release             \
          --wrap-mode=nodownload          \
          -D platforms=x11,wayland        \
          -D gallium-drivers=r300,r600,radeonsi,nouveau,i915,iris,crocus,virgl,svga,zink,llvmpipe,softpipe,d3d12 \
          -D vulkan-drivers=amd,intel,intel_hasvk,nouveau,swrast \
          -D egl=enabled                  \
          -D glx=dri                      \
          -D gbm=enabled                  \
          -D glvnd=enabled                \
          -D gles1=disabled               \
          -D gles2=enabled                \
          -D llvm=enabled                 \
          -D xmlconfig=enabled            \
          -D zstd=enabled                 \
          -D expat=enabled                \
          -D display-info=enabled         \
          -D xlib-lease=enabled           \
          -D gallium-va=enabled           \
          -D lmsensors=disabled           \
          -D valgrind=disabled            \
          -D video-codecs=all             \
          -D libunwind=disabled           \
          -D gallium-rusticl=true         \
          -D mesa-clc=enabled             \
          -D install-mesa-clc=true        \
          -D precomp-compiler=enabled     \
          -D install-precomp-compiler=true

    # mesa-clc/precomp install (GE-01 L16): the CLC kernel precompiler and
    # the drivers' internal shader compilers were already BUILT here (NVK
    # demands with_clc), but never INSTALLED — and lib32-mesa consumes them
    # as SYSTEM tools (mesa-clc=system) so the 32-bit build does not need a
    # 32-bit LLVMSPIRVLib. Upstream's own cross-build design ("if needed
    # for cross builds" — meson.options); the reference multilib distro's
    # lib32-mesa uses exactly this split.
    # RT-3 post-configure assertion: the resolved configure state must
    # match the declared matrix exactly — fail-closed on deviations,
    # upstream option renames, or unreadable introspection.
    python3 /mnt/intergenos/igos-build/mesa_feature_matrix.py \
        --build . \
        --matrix /mnt/intergenos/packages/desktop/mesa/feature-matrix.json \
        --label mesa
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    # --skip-subprojects: a plain ninja install also installs every meson
    # subproject's install targets — the vendored DirectX-Headers subproject
    # deposited its full footprint (36 headers under /usr/include/directx,
    # /usr/include/{composition,dxguids,wsl}, two static libs and a
    # pkgconfig file) into the chroot on the first from-scratch build, and
    # every later rebuild then resolved dep_dxheaders from that system copy
    # and sealed an archive WITHOUT the files — leaving them permanently
    # unowned on the cumulative chroot (the 4.85 ownership-gate 52-file
    # finding on both the ge9b-11 and ge9b-12 mints). The headers are a
    # build-time dependency only; nothing shipped consumes them at runtime.
    DESTDIR="$DESTDIR" meson install --skip-subprojects
}
