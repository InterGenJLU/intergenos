#!/bin/bash
# rocksdb 11.1.1 — Facebook/Meta embedded persistent KV store (LSM-tree)
#
# Why this version (11.1.1, current latest stable):
#   * Released 2026-04-29 per https://github.com/facebook/rocksdb/releases
#   * v11.1.1 is the most recent tag on the project's releases page;
#     matches the database-landing-plan section 6 pin. Active upstream;
#     monthly releases is typical.
#   * Single SHA-256 anchor pinned in package.yml after local download +
#     sha256sum of the upstream GitHub archive tarball.
#
# License: the SPDX expression "(Apache-2.0 OR GPL-2.0) AND BSD-3-Clause"
# captures the package's licensing structure as actually shipped in the
# 11.1.1 tarball — three license files are present at the root:
#
#   * LICENSE.Apache  — Apache 2.0 (11.4 KB)
#   * COPYING         — GPL-2.0 full text (18.1 KB)
#   * LICENSE.leveldb — BSD-3-Clause covering the leveldb-derived source
#                       files (1.6 KB; "Copyright 2011 The LevelDB Authors")
#
# The original Facebook/Meta code is dual-licensed Apache-2.0 OR GPL-2.0
# (per source-file headers). The leveldb-derived portions remain under
# their original BSD-3-Clause license — they are NOT relicensed under
# the Apache OR GPL choice. The AND conjunction with the BSD term is
# therefore load-bearing: a downstream packager must comply with BOTH
# (Apache-2.0 OR GPL-2.0) for the rocksdb-original code AND BSD-3-Clause
# for the leveldb-derived bits. This is a license-correction vs the
# dispatch-stated "GPL-2 OR Apache-2 dual" — the table omits the third
# license component covering the leveldb-derivation. Surfacing here per
# the verify-and-pin / license-correction discipline applied earlier this
# arc by other packages.
#
# Build profile: CMake out-of-source, shared library, Ninja-driven for
# heavyweight C++ template-heavy compile parallelism.
#
# ============================================================
# WAVE 1b DEP-ARROW DISCOVERY AUDIT (the validation deliverable)
# ============================================================
# This package consumes ALL FOUR wave-1b deps that landed earlier in
# this arc (snappy at master a749ed, gflags at 29851a, jemalloc at
# 80c73b6, liburing at 90043445). Per the dispatch, the per-dep
# discovery mechanism is documented here for the next maintainer +
# for any future Wave 2 consumer that hits the same surface.
#
# Source of truth: rocksdb 11.1.1's cmake/modules/Find*.cmake files
# and the top-level CMakeLists.txt find_package() flow at lines 138-196.
#
# (1) SNAPPY ------------------------------------------------------------
#   Discovery flow (CMakeLists.txt:162-166):
#     find_package(Snappy CONFIG)           # first try
#     if NOT Snappy_FOUND:
#         find_package(Snappy REQUIRED)     # fall back to FindSnappy.cmake
#   CONFIG path: looks for SnappyConfig.cmake. The in-tree snappy 1.2.2
#   package installs this at /usr/lib/cmake/Snappy/SnappyConfig.cmake
#   via snappy's own CMakeLists.txt install rules (snappy uses CMAKE_
#   INSTALL_LIBDIR=lib). CONFIG path resolves first-try.
#   Module fallback (rocksdb cmake/modules/FindSnappy.cmake): plain
#   find_path(snappy.h) + find_library(snappy). Both resolve against
#   /usr/include/snappy.h and /usr/lib/libsnappy.so via cmake default
#   search paths. Module fallback also resolves first-try.
#   IMPORTED target: Snappy::snappy (created either by CONFIG file or
#   by FindSnappy.cmake module post-find).
#   ASSESSMENT: clean first-try via either path. No workaround needed.
#
# (2) GFLAGS ------------------------------------------------------------
#   Discovery flow (CMakeLists.txt:138-156):
#     find_package(gflags CONFIG)           # first try
#     if NOT gflags_FOUND:
#         find_package(gflags REQUIRED)     # fall back to Findgflags.cmake
#   CONFIG path: gflags 2.3.0 installs gflagsConfig.cmake at
#   /usr/lib/cmake/gflags/gflagsConfig.cmake via its CMakeLists.txt
#   install rules. CONFIG path resolves first-try.
#   Module fallback (cmake/modules/Findgflags.cmake): plain find_path(
#   gflags/gflags.h) + find_library(gflags). Both resolve against
#   /usr/include/gflags/gflags.h and /usr/lib/libgflags.so via cmake
#   default search paths. Module fallback also resolves first-try.
#   IMPORTED target: gflags::gflags.
#   ASSESSMENT: clean first-try via either path. No workaround needed.
#
# (3) JEMALLOC ----------------------------------------------------------
#   Discovery flow (CMakeLists.txt:121-124):
#     find_package(JeMalloc REQUIRED)       # only Module path, no CONFIG
#   jemalloc 5.3.1 ships via autotools and does NOT generate a
#   cmake-config file, so the CONFIG path would always fail. rocksdb
#   skips the CONFIG-first try and uses the Module path directly.
#   Module (cmake/modules/FindJeMalloc.cmake): plain find_path(jemalloc/
#   jemalloc.h) + find_library(jemalloc). The in-tree jemalloc 5.3.1
#   package installs:
#     /usr/include/jemalloc/jemalloc.h
#     /usr/lib/libjemalloc.so
#   (via configure --prefix=/usr --libdir=/usr/lib). Both resolve via
#   cmake default search paths.
#   IMPORTED target: JeMalloc::JeMalloc.
#   ASSESSMENT: clean first-try via the Module path. No workaround
#   needed. (Note: if a future maintainer wants a cmake-config-first
#   discovery flow, jemalloc would need a separate CMake-build helper
#   to generate JeMallocConfig.cmake — out of scope here.)
#
# (4) LIBURING ----------------------------------------------------------
#   Discovery flow (CMakeLists.txt:349-350):
#     find_package(uring)                   # NOT REQUIRED — soft optional
#   WITH_LIBURING is the gate (set explicitly ON below per dispatch).
#   liburing autotools does NOT generate a cmake-config file. rocksdb
#   uses the Module path directly via cmake/modules/Finduring.cmake.
#   Module: find_path(liburing.h) at the top level (NOT inside a
#   subdirectory) + find_library(liburing.a liburing) — note the static
#   prefer behaviour: cmake tries `liburing.a` first, then `liburing`
#   (resolves to libliburing.so via standard prefix convention). The
#   in-tree liburing 2.14 package installs:
#     /usr/include/liburing.h
#     /usr/lib/libliburing.so       (shared only; static NOT shipped)
#   The find_library(liburing.a liburing) call resolves to
#   /usr/lib/libliburing.so on the second name attempt. find_path
#   resolves to /usr/include/liburing.h directly.
#   IMPORTED target: uring::uring.
#   ASSESSMENT: clean first-try via the Module path. No workaround
#   needed for the shared-only liburing build. (Note: if a future
#   maintainer wants to enable static rocksdb builds via liburing-
#   static linkage, the liburing package would need to add an
#   `--enable-static` configure flag — out of scope here.)
#
# OVERALL DEP-ARROW VERDICT: all four wave-1b dep arrows resolve
# first-try with the in-tree package install layouts as landed at the
# c7d45760 / 28440cb6 master tip. No silent fallbacks, no halt-and-
# propose required, no upstream-of-rocksdb package fixes needed.
#
# Critically, rocksdb does NOT use pkg-config or pkg_check_modules for
# any of the four wave-1b deps. The pkg-config / lib64-vs-lib discovery
# gap therefore does NOT apply at this layer. Future Wave 2 packages
# that use pkg_check_modules(SNAPPY snappy) or pkg_check_modules(GFLAGS
# gflags) directly will be the actual test cases for pkg-config
# discovery — this one isn't. (MongoDB is the next likely candidate
# for that test surface.)
#
# Narrow caveat: the find_package(uring) call is NOT marked REQUIRED.
# If uring discovery were to fail silently in a future chroot config
# (e.g., liburing moved to a non-default path), rocksdb would compile
# WITHOUT io_uring support — no error raised, just degraded async-I/O
# performance. The same silent-fallback caveat that applied to leveldb's
# snappy probe applies here for the uring optional. Documented for the
# next maintainer who notices "async I/O is slow on this machine."
#
# ============================================================
# CONFIGURE FLAGS APPLIED
# ============================================================
#
# Compression deps (the four wave-1b validation arrows):
#   -DWITH_SNAPPY=ON     snappy linkage (compression)
#   -DWITH_GFLAGS=ON     gflags linkage (CLI arg parser; required by tools
#                        even with WITH_TOOLS=OFF for some library-internal
#                        flag-handling code paths)
#   -DWITH_JEMALLOC=ON   jemalloc linkage (allocator)
#   -DWITH_LIBURING=ON   liburing linkage (async I/O)
#
# Additional compression deps (already in tree pre-this-arc):
#   -DWITH_ZLIB=ON       zlib (compression)
#   -DWITH_LZ4=ON        lz4  (compression)
#   -DWITH_ZSTD=ON       zstd (compression)
#   -DWITH_BZ2=ON        bzip2 (compression)
#
# Install + build shape:
#   -DCMAKE_INSTALL_PREFIX=/usr        standard distro layout
#   -DCMAKE_INSTALL_LIBDIR=lib         avoids lib64-vs-lib drift
#   -DCMAKE_BUILD_TYPE=Release         optimized, no debug symbols
#   -DBUILD_SHARED_LIBS=ON             global cmake shared-libs flag
#   -DROCKSDB_BUILD_SHARED=ON          rocksdb-specific shared-libs flag
#                                      (default ON; explicit for symmetry)
#
# Portability + RTTI (per dispatch + landing-plan section 5b):
#   -DPORTABLE=1                       CRITICAL: disables -march=native
#                                      bake-in. CMakeLists.txt:292 default
#                                      PORTABLE=0 enables -march=native at
#                                      line 314, which would silently leak
#                                      build-VM microarch assumptions to
#                                      user hosts. We distribute pre-built
#                                      binaries; PORTABLE=1 is mandatory.
#   -DUSE_RTTI=1                       Enable RTTI in all builds (not just
#                                      Debug). Some downstream C++
#                                      consumers (future MongoDB code
#                                      paths) need dynamic_cast support in
#                                      release builds against librocksdb.
#                                      CMakeLists.txt:444 default is AUTO
#                                      which means Release-no-RTTI; the
#                                      dispatch-specified =1 makes it
#                                      explicit.
#
# Disabled paths (skip-test / skip-tool / skip-bench cascades):
#   -DWITH_TESTS=OFF                   no test artifacts (variable used at
#                                      CMakeLists.txt:1240 even though not
#                                      declared via option())
#   -DWITH_ALL_TESTS=OFF               actual option() at line 1337,
#                                      default ON; turned OFF here
#   -DWITH_BENCHMARK_TOOLS=OFF         no benchmark tools
#   -DWITH_BENCHMARK=OFF               no benchmark tests
#   -DWITH_TOOLS=OFF                   no CLI tools (ldb, sst_dump, etc.)
#   -DWITH_CORE_TOOLS=OFF              no core tools either
#   -DWITH_TRACE_TOOLS=OFF             no trace tools
#   -DWITH_JNI=OFF                     no Java JNI bindings (java/ tree)
#   -DWITH_NUMA=OFF                    no NUMA-aware allocator (defaults
#                                      OFF; explicit)
#   -DWITH_TBB=OFF                     no TBB-based threading (defaults
#                                      OFF; explicit)
#   -DWITH_DYNAMIC_EXTENSION=OFF       no runtime-loadable extension
#                                      modules (defaults OFF; explicit)
#
# Syscall-surface declarations (no implicit defaults per dispatch):
#   -DWITH_FALLOCATE=ON                fallocate(2) syscall for pre-
#                                      allocating SST file space. Explicit
#                                      because the dispatch flagged this
#                                      as a syscall-surface item to
#                                      declare rather than implicitly
#                                      inherit. Default ON anyway, but
#                                      no longer hidden.
#
# Security-only-alignment posture: rocksdb is an embedded library — no
# daemons, no network sockets, no setuid/setgid binaries, no privileged
# entry points. The library exposes a C++ API for in-process LSM-tree
# storage against a local filesystem path chosen by the consumer.
# Privileged surface is determined entirely by the consuming program;
# future in-tree consumers (MongoDB primarily; potentially others) will
# ship their own AppArmor + systemd hardening when they land.
#
# fallocate(2) is the primary syscall surface added by linking rocksdb;
# it is gated by the running kernel's CAP_SYS_ADMIN/CAP_DAC_OVERRIDE
# requirements (depends on filesystem and pre-allocation mode). io_uring
# via liburing is the secondary syscall surface — gated by the kernel's
# io_uring_disabled sysctl (which we ship at default 0 = on but
# operator-configurable to 1 or 2).
#
# Downstream consumer at landing: none. This package lands ahead of its
# consumers (MongoDB primarily, expected to be the heaviest user) as the
# canonical 4-dep-arrow validation case for the Wave 1b dep chain.

configure() {
    set -e
    cmake -B build -G Ninja                                                 \
          -DCMAKE_BUILD_TYPE=Release                                        \
          -DCMAKE_INSTALL_PREFIX=/usr                                       \
          -DCMAKE_INSTALL_LIBDIR=lib                                        \
          -DBUILD_SHARED_LIBS=ON                                            \
          -DROCKSDB_BUILD_SHARED=ON                                         \
          -DWITH_SNAPPY=ON                                                  \
          -DWITH_GFLAGS=ON                                                  \
          -DWITH_JEMALLOC=ON                                                \
          -DWITH_LIBURING=ON                                                \
          -DWITH_ZLIB=ON                                                    \
          -DWITH_LZ4=ON                                                     \
          -DWITH_ZSTD=ON                                                    \
          -DWITH_BZ2=ON                                                     \
          -DWITH_TESTS=OFF                                                  \
          -DWITH_ALL_TESTS=OFF                                              \
          -DWITH_BENCHMARK_TOOLS=OFF                                        \
          -DWITH_BENCHMARK=OFF                                              \
          -DWITH_TOOLS=OFF                                                  \
          -DWITH_CORE_TOOLS=OFF                                             \
          -DWITH_TRACE_TOOLS=OFF                                            \
          -DWITH_JNI=OFF                                                    \
          -DWITH_NUMA=OFF                                                   \
          -DWITH_TBB=OFF                                                    \
          -DWITH_DYNAMIC_EXTENSION=OFF                                      \
          -DWITH_FALLOCATE=ON                                               \
          -DPORTABLE=1                                                      \
          -DUSE_RTTI=1
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}

check() {
    set -e
    # Tests disabled via -DWITH_TESTS=OFF + -DWITH_ALL_TESTS=OFF. The
    # upstream test suite pulls third-party/gtest-1.8.1/ (bundled in the
    # tarball under third-party/) and runs >300 test executables — a
    # large compile cost for verification that doesn't add value at the
    # library-landing layer. Run-time verification of the API will come
    # from downstream consumers in v1.x (primarily MongoDB).
    return 0
}
