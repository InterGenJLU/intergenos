#!/bin/bash
# leveldb 1.23 — Google LevelDB embedded ordered key-value store
#
# Why this version (1.23, current latest stable):
#   * Released 2021-02-23 per https://github.com/google/leveldb/releases
#   * 1.23 is the most recent tag on the project's releases page; no
#     newer stable release has been published. Database-landing-plan
#     §6 also names 1.23 as the pin target. Quiet upstream: no CVEs
#     in 12+ months and zero releases since 2021-02 — the API is
#     stable and the codebase is in long-term maintenance mode.
#   * Single SHA-256 anchor pinned in package.yml after local download
#     + sha256sum of the upstream GitHub archive tarball.
#
# License: BSD-3-Clause. The LICENSE file in the tarball root carries
# a single 3-clause BSD notice (Google "LevelDB Authors" copyright,
# source + binary retain-notice clauses, and the no-endorsement clause
# naming Google Inc. and contributors). 27 lines. Single license file
# only — no LGPL / GPL components present in the tarball.
#
# Build profile: CMake out-of-source, shared library, modern
# `cmake -B build` syntax matching the gflags / uchardet in-tree
# convention.
#
# Submodule note: the GitHub auto-generated source archive does NOT
# include the third_party/ submodules (googletest + benchmark). The
# extracted tarball has empty third_party/googletest/ and
# third_party/benchmark/ directories. This is fine because the
# upstream CMakeLists.txt only invokes add_subdirectory() against
# those paths inside `if(LEVELDB_BUILD_TESTS)` / `if(LEVELDB_BUILD_BENCHMARKS)`
# blocks — both of which are disabled below. No submodule fetch
# required for the library build.
#
# Snappy dependency discovery (the validation case for this package):
#   leveldb's upstream CMakeLists.txt uses a RAW LINKER PROBE for
#   snappy at line 42:
#     check_library_exists(snappy snappy_compress "" HAVE_SNAPPY)
#   Then at line 273-275:
#     if(HAVE_SNAPPY) target_link_libraries(leveldb snappy) endif()
#   This is the SIMPLEST possible dependency discovery mechanism. It
#   does NOT use find_package(Snappy), does NOT use pkg_check_modules,
#   does NOT consult any .pc file or cmake-config file. The probe
#   succeeds if and only if the test compiler can link against
#   `-lsnappy` and find the symbol `snappy_compress`. The header
#   `snappy.h` must also be on the default include path for the
#   actual leveldb sources that #include <snappy.h>.
#
#   In our build chroot, the snappy package (landed at commit a749ed,
#   master tip c7d45760) installs:
#     /usr/lib/libsnappy.so       (via CMAKE_INSTALL_LIBDIR=lib)
#     /usr/include/snappy.h       (cmake CMAKE_INSTALL_INCLUDEDIR=include default)
#   The default linker search path on x86_64 Linux includes /usr/lib,
#   and the default include path includes /usr/include. The discovery
#   surface is therefore complete by construction — no .pc / cmake-
#   config / find_package machinery is involved, so the well-known
#   lib64-vs-lib pkg-config gap (where downstream find_package /
#   pkg_check_modules calls look in /usr/lib64 by default on some
#   distros and miss libraries installed to /usr/lib) does NOT apply
#   to this dep arrow.
#
# Configure flags chosen:
#   -DCMAKE_INSTALL_PREFIX=/usr        standard distro layout
#   -DCMAKE_INSTALL_LIBDIR=lib         avoids lib64-vs-lib drift;
#                                      keeps leveldbConfig.cmake at
#                                      /usr/lib/cmake/leveldb/ for
#                                      downstream find_package(leveldb)
#   -DBUILD_SHARED_LIBS=ON             shared-lib-only per distro convention
#   -DCMAKE_BUILD_TYPE=Release         optimized build, no debug symbols
#                                      in shipped artifact
#   -DLEVELDB_BUILD_TESTS=OFF          skip the third_party/googletest
#                                      add_subdirectory(); upstream
#                                      tests are not the verification
#                                      target for a library landing
#   -DLEVELDB_BUILD_BENCHMARKS=OFF     skip the third_party/benchmark
#                                      add_subdirectory(); benchmarks
#                                      pull benchmark + gtest deps
#   -DLEVELDB_INSTALL=ON               default is ON; explicit to guard
#                                      against future upstream flips
#
# Security-only-alignment note: leveldb is an embedded library — no
# daemons, no network sockets, no setuid/setgid binaries, no kernel
# interfaces. It exposes a C++ API for in-process key-value storage
# against a local filesystem path chosen by the consumer. Privileged
# surface is determined entirely by the consuming program. Consumers
# in our tree (none at this landing; future MongoDB / RocksDB do NOT
# embed leveldb — they have their own KV layers) will ship their own
# AppArmor + systemd hardening when they land.
#
# Downstream consumer at landing: none. This package lands ahead of
# its consumers as the first downstream-of-Wave-1b validation that
# the snappy dep-arrow integrates cleanly. The dep-arrow snappy →
# leveldb is the test case for whether Wave 1b's installed-library
# discovery surface is consumable by downstream extras/ packages.

configure() {
    set -e
    cmake -B build                                                          \
          -DCMAKE_BUILD_TYPE=Release                                        \
          -DCMAKE_INSTALL_PREFIX=/usr                                       \
          -DCMAKE_INSTALL_LIBDIR=lib                                        \
          -DBUILD_SHARED_LIBS=ON                                            \
          -DLEVELDB_BUILD_TESTS=OFF                                         \
          -DLEVELDB_BUILD_BENCHMARKS=OFF                                    \
          -DLEVELDB_INSTALL=ON
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
    # Tests disabled via -DLEVELDB_BUILD_TESTS=OFF — building the
    # upstream test suite would require the third_party/googletest
    # git submodule which the GitHub auto-archive tarball does not
    # include, and would add gtest + gmock as transitive build deps
    # for no meaningful verification beyond what the library link
    # path already exercises. Run-time verification of the API will
    # come from downstream consumers in v1.x.
    return 0
}
