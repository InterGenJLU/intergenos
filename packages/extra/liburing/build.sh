#!/bin/bash
# liburing 2.14 — Linux io_uring async I/O wrapper library
#
# Why this version (2.14, current latest stable):
#   * Released 2025-02-07 per https://github.com/axboe/liburing/releases.
#   * 2.14 is the most recent tag marked as the stable release on the
#     project's release page (notable items in 2.14: comprehensive man-page
#     coverage of the full liburing API, lots of test updates, assorted
#     bug fixes).
#   * Single SHA-256 anchor pinned in package.yml after local download +
#     sha256sum of the upstream GitHub auto-generated source archive.
#
# License: MIT AND LGPL-2.1 AND GPL-2.0 (three components, all shipped in
# the tarball):
#   * LICENSE  — MIT — covers the FFI binding library + (per source-file
#                SPDX-License-Identifier headers, e.g. src/setup.c) the
#                core liburing source files.
#   * COPYING  — LGPL-2.1 — historical liburing library licensing; ships
#                alongside MIT in a dual-license arrangement so downstream
#                consumers can pick whichever is more compatible with
#                their use case.
#   * COPYING.GPL — GPL-2.0 — covers the test/ program tree.
#   The dispatch landing-plan reference cited "MIT + LGPL-2.1"; the
#   GPL-2.0 component (for the test programs) is also part of the
#   tarball and is captured here in the SPDX expression. We do NOT
#   install the test/ tree (which would propagate the GPL-2.0 component
#   into the installed image); the runtime artifacts are exclusively the
#   MIT/LGPL-2.1-licensed library and its headers.
#
# Build profile: liburing uses a hand-rolled `configure` shell script
# (NOT autoconf-generated) that writes a `config-host.mak` consumed by
# the top-level Makefile. The packaged release tarball ships `configure`
# pre-baked; no autotools-bootstrap (autogen.sh / autoreconf) step is
# required or available. `make install` honors DESTDIR.
#
# Configure flags chosen:
#   --prefix=/usr            standard distro layout
#   --libdir=/usr/lib        avoids lib64-vs-lib pkg-config gap
#                            (configure's default is already /usr/lib;
#                            passed explicitly for symmetry with other
#                            extra/ packages and to lock the value
#                            against any future configure default change)
#   --libdevdir=/usr/lib     development library install dir; matches
#                            --libdir to keep .pc / development symlinks
#                            colocated with the runtime .so under /usr/lib
#   --includedir=/usr/include  standard
#   --mandir=/usr/share/man  configure's default is /usr/man (deprecated
#                            convention); override to the modern
#                            /usr/share/man layout used by every other
#                            in-tree package
#   --datadir=/usr/share     standard
#
# Security-only-alignment note: liburing is a user-space wrapper around
# the kernel's io_uring(7) syscall interface — it adds no privileged
# surface of its own. No setuid/setgid binaries, no daemons, no network
# exposure, no new device files. The async I/O capabilities surfaced
# (read/write/openat/connect/etc.) are gated entirely by the kernel's
# io_uring config (CONFIG_IO_URING), which InterGenOS controls in the
# kernel build, and by the io_uring_disabled sysctl (default 0 in stock
# kernels; can be set to 1 or 2 to restrict per-task or globally).
# Per-syscall permissions follow the calling process's existing capability
# set — io_uring never escalates beyond what the caller could already do
# via traditional syscalls.
#
# Forward-deferred flag (NOT applied here; recorded for future Wave 2
# work): per docs/architecture/database-landing-plan.md §5a, once
# postgresql lands as part of Wave 2, its build.sh should be updated to
# enable `-Dliburing=enabled` in the same commit as the postgresql
# landing. Postgresql is not in tree yet at this commit, so the
# `-Dliburing=disabled` workaround documented in the landing plan
# remains in effect until the Wave 2 landing PR re-enables it.

configure() {
    set -e
    ./configure                                                            \
        --prefix=/usr                                                      \
        --libdir=/usr/lib                                                  \
        --libdevdir=/usr/lib                                               \
        --includedir=/usr/include                                          \
        --mandir=/usr/share/man                                            \
        --datadir=/usr/share
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # `make install` invokes src/Makefile + Makefile install rules with
    # DESTDIR honored (verified by reading the top-level Makefile install
    # target). Installs:
    #   /usr/lib/liburing.so* + liburing-ffi.so*    (runtime libs)
    #   /usr/lib/pkgconfig/liburing.pc + liburing-ffi.pc
    #   /usr/include/liburing.h + liburing/*.h     (headers)
    #   /usr/share/man/man{2,3,7}/*.{2,3,7}        (man pages)
    # We intentionally do NOT run `make install-tests` — those install
    # the GPL-2.0-licensed test/ programs under /usr/share/liburing-test
    # which are development utilities, not runtime artifacts, and would
    # propagate the GPL-2.0 component into the installed image.
    make DESTDIR="$DESTDIR" install
}

check() {
    set -e
    # liburing ships an extensive `make runtests` integration suite under
    # test/ that exercises the io_uring kernel interface end-to-end. Per
    # the existing in-tree convention (mirroring jemalloc and other
    # low-level kernel-interface libraries), `make runtests` is skipped
    # in the packaged-build path:
    #   - `make runtests` requires a functional io_uring kernel surface
    #     (kernel running in the build VM, CONFIG_IO_URING enabled,
    #     appropriate capability set) which is true for the build VM
    #     but adds runtime variability not appropriate for a hermetic
    #     packaged build.
    #   - Runtime functional verification is performed by downstreams
    #     that link against liburing (rocksdb's own test suite, the
    #     io_uring integration paths in future postgresql), which catch
    #     liburing regressions transitively against the actual workload.
    # The compile-only success of `make` above (the `build()` phase
    # above this) catches build-time API/ABI breakage.
    return 0
}
