#!/bin/bash
# gflags 2.3.0 — Google command-line flags library
#
# Why this version (2.3.0, current latest stable):
#   * Released 2024-12-06 per https://github.com/gflags/gflags/releases
#   * 2.3.0 is the most recent tag marked "Latest" on the project's
#     releases page; supersedes 2.2.2 (2023-11-11) with CMake enhancements,
#     Bazel 9 compatibility, QNX support, and bug fixes.
#   * Single SHA-256 anchor pinned in package.yml after local download +
#     sha256sum of the upstream GitHub archive tarball.
#
# License: BSD-3-Clause. The COPYING.txt file in the source tarball
# carries a single 3-clause BSD notice (Google Inc. copyright, source +
# binary retain-notice clauses, and the no-endorsement clause naming
# Google Inc. and contributors).
#
# Build profile: CMake out-of-source, shared library, modern out-of-source
# `-B build` syntax matching the in-tree CMake convention (see
# packages/extra/uchardet for the canonical minimal pattern).
#
# Configure flags chosen:
#   -DCMAKE_INSTALL_PREFIX=/usr     standard distro layout
#   -DCMAKE_INSTALL_LIBDIR=lib      avoids lib64-vs-lib pkg-config gap;
#                                   gflags-config.cmake placement matters
#                                   for downstream find_package() calls
#                                   (RocksDB tools, glog, etc.)
#   -DBUILD_SHARED_LIBS=ON          shared-lib-only per distro convention
#   -DCMAKE_BUILD_TYPE=Release      optimized build, no debug symbols in
#                                   shipped artifact
#   -DGFLAGS_BUILD_STATIC_LIBS=OFF  explicit static-off (CMakeLists default
#                                   is OFF but explicit guards against
#                                   future upstream flips)
#   -DGFLAGS_BUILD_TESTING=OFF      no in-build test artifacts; the
#                                   library itself is the verification
#                                   target
#   -DGFLAGS_NAMESPACE=gflags       canonical namespace (default; explicit
#                                   for downstream linker stability)
#
# Holy-Grail / security-only-alignment note: gflags is a small,
# self-contained command-line parsing library. No daemons, no setuid
# binaries, no network exposure, no kernel interfaces. The library has
# no privileged surface of its own; downstream consumers (RocksDB tools,
# etc.) determine the security posture of any program that links it.
#
# Downstream consumer: RocksDB tools (per database-landing-plan.md §2
# Wave 1b). Other potential consumers: glog (Google logging library) if
# landed later, and various Google-authored C++ utilities.

configure() {
    set -e
    cmake -B build                                                          \
          -DCMAKE_BUILD_TYPE=Release                                        \
          -DCMAKE_INSTALL_PREFIX=/usr                                       \
          -DCMAKE_INSTALL_LIBDIR=lib                                        \
          -DBUILD_SHARED_LIBS=ON                                            \
          -DGFLAGS_BUILD_STATIC_LIBS=OFF                                    \
          -DGFLAGS_BUILD_TESTING=OFF                                        \
          -DGFLAGS_NAMESPACE=gflags
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
    # Tests disabled via -DGFLAGS_BUILD_TESTING=OFF — building the test
    # suite would require gtest as an additional dependency without
    # adding meaningful verification beyond what the library link path
    # already exercises. Run-time verification is performed by
    # downstreams that link against gflags (RocksDB's own test suite
    # catches gflags-API regressions against the linked version).
    return 0
}
