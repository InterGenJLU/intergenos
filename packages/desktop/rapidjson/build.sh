#!/bin/bash
# rapidjson 0.0.0+master.20250205 — Fast JSON parser/generator for C++ (header-only)
#
# Why we ship rapidjson:
#   Audacity 3.7.7 requires rapidjson for project file metadata. The library is
#   also a common dep in the C++ ecosystem (CGAL, ROS bindings, various media
#   tools) so we tier it under desktop rather than co-locating with audacity.
#
# Why master snapshot instead of the v1.1.0 release tag (security-first / "latest stable"):
#   The official tagged release v1.1.0 dates from 2016-08-25 — over nine years
#   of accumulated bug and security fixes sit on master without ever being cut
#   into a tagged release. Both Debian (1.1.0+dfsg2-7.6) and Arch (1.1.0-6)
#   work around this by patching v1.1.0 with a stack of upstream cherry-picks,
#   including CVE-2024-38517.
#
#   Per the security-only alignment and the project's "ALWAYS latest
#   stable versions" rule, we instead pin the master tip at upstream commit
#   24b5e7a8b27f42fa16b96fc70aade9106cf7102f (committed 2025-02-05, "Fix out of
#   bounds read with kParseValidateEncodingFlag"). That commit:
#     * incorporates CVE-2024-38517 + the additional out-of-bounds-read fix
#       (the most recent security fix on master)
#     * matches the "ship master snapshot" pattern Debian/Arch effectively
#       arrive at via patching, but without a maintained patch stack
#     * keeps modern-toolchain compatibility (GCC 14, C++20) that v1.1.0 lacks
#
#   Version string `0.0.0+master.YYYYMMDD` — chosen so that whenever upstream
#   cuts a real v1.2.x or v2.0 release, our pkm version compare cleanly orders
#   the tagged release as newer. The +master.<date> suffix is the snapshot
#   marker; the leading 0.0.0 sorts below any future real release.
#
# Build system:
#   CMake. RapidJSON is header-only — the build target installs headers,
#   pkg-config (.pc), and CMake config files. We disable docs, examples, and
#   tests because (a) they need doxygen + gtest at build time which we don't
#   want to pull in for a header library, and (b) consumers don't need them.
#
# Flags:
#   -DRAPIDJSON_BUILD_DOC=OFF              — skip Doxygen pass (no doxygen dep)
#   -DRAPIDJSON_BUILD_EXAMPLES=OFF         — skip example binaries (not used)
#   -DRAPIDJSON_BUILD_TESTS=OFF            — skip unittests/perftests (no gtest dep)
#   -DRAPIDJSON_BUILD_THIRDPARTY_GTEST=OFF — don't try to use bundled gtest
#   -DCMAKE_BUILD_TYPE=Release             — match other desktop CMake packages

configure() {
    set -e
    cmake -B build                                  \
          -DCMAKE_INSTALL_PREFIX=/usr               \
          -DCMAKE_BUILD_TYPE=Release                \
          -DRAPIDJSON_BUILD_DOC=OFF                 \
          -DRAPIDJSON_BUILD_EXAMPLES=OFF            \
          -DRAPIDJSON_BUILD_TESTS=OFF               \
          -DRAPIDJSON_BUILD_THIRDPARTY_GTEST=OFF
}

build() {
    set -e
    # Header-only — `cmake --build` is essentially a no-op that prepares the
    # install tree (RapidJSONConfig.cmake.in -> RapidJSONConfig.cmake, .pc.in
    # -> .pc). Run it for hygiene; don't skip.
    cmake --build build -j${IGOS_JOBS}
}

# Header-only library: there is no runtime to test outside of consuming code.
# Upstream's tests are unit/perf tests against gtest, which we deliberately
# don't pull in (see RAPIDJSON_BUILD_TESTS=OFF rationale above).
check() {
    set -e
    return 0
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
