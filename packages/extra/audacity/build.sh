#!/bin/bash
# audacity 3.7.7 — Multi-track audio editor and recorder
#
# Why this version (3.7.7, latest stable in the 3.7.x maintenance line):
#   * Released 2025-09-25; current latest stable per
#     https://github.com/audacity/audacity/releases/latest as of 2026-05-03.
#   * 3.7.x is the active maintenance series. 3.6.x is EOL.
#   * SHA-256 of audacity-sources-3.7.7.tar.gz cross-verified against upstream
#     CHECKSUMS.txt published alongside the release artifacts.
#
# ============================================================================
# DESIGN: source-built, no Conan, no telemetry
# ============================================================================
#
# Audacity's upstream build system ships with Conan 2 enabled by default and
# expects to download pre-built dependency binaries from `center.conan.io`.
# This conflicts with the security-only alignment (no opaque network downloads of binaries
# we cannot audit) and with the Prime Directive (the user must understand and
# trust every component on their machine).
#
# We therefore build with:
#
#   -Daudacity_conan_enabled=Off
#       Disables the entire Conan pipeline. No HTTP calls during configure.
#       Forces find_package() to use the system (our chroot's /usr) for every
#       dependency rather than Conan's package cache.
#
#   -Daudacity_lib_preference=system
#       For any `addlib(...)` library that supports system/local dual-mode
#       (vamp, lv2, sbsms, soundtouch, twolame, soxr, sqlite), prefer the
#       system copy first. With conan_enabled=Off, "local" means "build from
#       Audacity's bundled lib-src/ tree" — i.e., still source-built, just
#       in-tree rather than from our package store.
#
#   -Daudacity_obey_system_dependencies=Off
#       Allows graceful fallback to lib-src/ for libraries we have not yet
#       packaged (libsoxr, libsbsms). With this On the configure aborts the
#       moment a system pkg-config probe fails.
#
# ============================================================================
# TELEMETRY: explicitly disabled per Prime Directive
# ============================================================================
#
# Audacity 3.x can ship with three network-touching subsystems. We disable all
# three at the ROOT (`audacity_has_networking=Off`) which transitively gates
# crash reporting, update checks, Sentry telemetry, and audio.com upload (see
# CMakeLists.txt lines 230-318: cmake_dependent_option chains all of these to
# `${_OPT}has_networking`). We also pass each child option explicitly so the
# build log makes the policy unambiguous and so a future flag rename upstream
# does not silently re-enable a subsystem.
#
#   -Daudacity_has_networking=Off       (master switch — gates all below)
#   -Daudacity_has_crashreports=Off     (no Sentry/breakpad/crashpad)
#   -Daudacity_has_sentry_reporting=Off (no Sentry DSN reporting)
#   -Daudacity_has_updates_check=Off    (no automatic update checks)
#   -Daudacity_has_audiocom_upload=Off  (no audio.com integration)
#   -Daudacity_has_url_schemes_support=Off (no custom URL handlers)
#   -Daudacity_has_whats_new=Off        (no startup nag dialog hitting network)
#
# Side-effect of has_networking=Off: ThreadPool and CURL are no longer required
# (see DependenciesList.cmake lines 65-68), which is intentional.
#
# ============================================================================
# PLUGIN HOST OPTIONS: LADSPA on, LV2 off, VST3 off, Vamp off
# ============================================================================
#
# LADSPA (audacity_use_ladspa=on)
#   We ship desktop/ladspa-sdk (1.17). LADSPA is the original Linux plugin
#   format and is required for compatibility with the existing plugin
#   ecosystem. Configure-default is on; we set it explicitly.
#
# LV2 (audacity_use_lv2=on)
#   LV2 plugin host enabled. Audacity 3.7.7 BUNDLES its own LV2 host stack
#   in lib-src/lv2/ (with its own configure script + waf-based build that
#   builds zix/serd/sord/sratom/lilv/suil from drobilla source). The flag
#   is a simple boolean — there is no `=system` mode in Audacity for LV2,
#   so the system lv2/lilv/suil packages we have in /desktop/ are NOT
#   consumed by this build. They remain useful for any future LV2 host
#   package (Ardour, Carla, Qtractor, etc.) that does use system find_package.
#
# VST3 (audacity_has_vst3=Off)
#   VST3 SDK requires Steinberg's VST3 GPLv3-compatible SDK. Our tree does
#   not yet ship the SDK and Audacity's bundled vst3sdk fetch path runs
#   through Conan, which we have disabled. Same use-if-have rationale.
#
# VST2 (audacity_use_vst=Off)
#   Steinberg withdrew the VST2 SDK in 2018 and prohibits new VST2-host
#   distribution. Configure-default is On (presumes the SDK is available).
#   We force Off to avoid both the legal exposure and a missing-header build
#   failure.
#
# Vamp (audacity_use_vamp=on)
#   Vamp plugin host enabled. Like LV2, Audacity bundles its own libvamp at
#   lib-src/libvamp/ — no system dep needed. Earlier "deferred" framing was
#   wrong; Vamp support is free with the bundled source.
#
# Audio Units, ASIO: macOS/Windows only, not relevant on Linux.
#
# ============================================================================
# IN-TREE / BUNDLED LIBRARIES (still source-built, just from lib-src/)
# ============================================================================
#
# Audacity's source release ships a `lib-src/` tree containing libraries that
# either (a) are not in major distros (libnyquist, portmixer), (b) require
# patched versions Audacity carries internally (sqlite, libsoxr fallback), or
# (c) are not yet in our package tree (libsbsms).
#
# With conan_enabled=Off + obey_system_dependencies=Off, the addlib mechanism
# (see cmake-proxies/cmake-modules/AudacityFunctions.cmake lines 693-782) will
# auto-fallback to building the lib-src/ copy when the system probe fails.
# Concretely:
#   * libsoxr   — REQUIRED, no system pkg → built from lib-src/libsoxr/
#   * libsbsms  — optional, no system pkg → built from lib-src/libsbsms/ (since lib_preference=system but missing → local)
#   * libnyquist — always local (lib-src/libnyquist/, no system option)
#   * portmixer  — always local (lib-src/portmixer/, no system option)
#   * pffft      — always local (cmake-proxies/pffft/, no system option)
#
# All other libraries come from /usr (i.e., from our package tree).
#
# ============================================================================
# KNOWN GAP: RapidJSON
# ============================================================================
#
# Audacity 3.7.x calls `audacity_find_package(RapidJSON REQUIRED)` (see
# cmake-proxies/cmake-modules/DependenciesList.cmake line 70). RapidJSON is a
# header-only C++ JSON library, NOT bundled in lib-src/, and currently NOT in
# our package tree. With conan_enabled=Off this find_package() WILL fail and
# the configure step will abort.
#
# Resolution: package RapidJSON in the desktop tier (header-only, trivial
# build) before merging this audacity package. This file is staged in
# anticipation of that addition; the current change-set does not include
# RapidJSON because the task brief explicitly forbids this package adding
# dependency packages of its own. Surfacing the gap explicitly here so a
# future builder does not silently assume the build will work.
#
# ============================================================================
# Components built (subject to enabled features above):
#   * audacity                 — main GUI binary (src/, modules/)
#   * lib-* shared libraries   — Audacity's modular runtime (libraries/lib-*)
#   * mod-* loadable modules   — effect/import/export plugins
#   * plug-ins/                — Nyquist effect bundles
#   * locale/                  — translation .mo files
#   * audacity.desktop         — XDG desktop entry
#   * audacity.appdata.xml     — AppStream metadata
#   * man pages                — installed under /usr/share/man/man1/
#   * icons                    — installed under /usr/share/icons/hicolor/
# ============================================================================

configure() {
    # Out-of-tree CMake build. Audacity's BUILDING.md uses Unix Makefiles in
    # examples; we use Ninja for parallelism + compatibility with our other
    # CMake-using packages (lv2, transmission, etc.).
    cmake -S . -B build -G Ninja                                              \
        -DCMAKE_BUILD_TYPE=Release                                            \
        -DCMAKE_INSTALL_PREFIX=/usr                                           \
        -DCMAKE_INSTALL_LIBDIR=lib                                            \
        -DCMAKE_INSTALL_SYSCONFDIR=/etc                                       \
        -DCMAKE_INSTALL_LOCALSTATEDIR=/var                                    \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5                                    \
                                                                              \
        -Daudacity_conan_enabled=Off                                          \
        -Daudacity_lib_preference=system                                      \
        -Daudacity_obey_system_dependencies=Off                               \
                                                                              \
        -Daudacity_has_networking=Off                                         \
        -Daudacity_has_crashreports=Off                                       \
        -Daudacity_has_sentry_reporting=Off                                   \
        -Daudacity_has_updates_check=Off                                      \
        -Daudacity_has_audiocom_upload=Off                                    \
        -Daudacity_has_url_schemes_support=Off                                \
        -Daudacity_has_whats_new=Off                                          \
        -Daudacity_has_tests=Off                                              \
        -Daudacity_has_vst3=Off                                               \
                                                                              \
        -Daudacity_use_wxwidgets=system                                       \
        -Daudacity_use_portaudio=system                                       \
        -Daudacity_use_libsndfile=system                                      \
        -Daudacity_use_libmp3lame=system                                      \
        -Daudacity_use_libid3tag=system                                       \
        -Daudacity_use_libmpg123=system                                       \
        -Daudacity_use_libogg=system                                          \
        -Daudacity_use_libvorbis=system                                       \
        -Daudacity_use_libflac=system                                         \
        -Daudacity_use_libopus=system                                         \
        -Daudacity_use_opusfile=system                                        \
        -Daudacity_use_expat=system                                           \
        -Daudacity_use_zlib=system                                            \
        -Daudacity_use_png=system                                             \
        -Daudacity_use_jpeg=system                                            \
                                                                              \
        -Daudacity_use_ffmpeg=loaded                                          \
                                                                              \
        -Daudacity_use_ladspa=on                                              \
        -Daudacity_use_lv2=on                                                 \
        -Daudacity_use_vamp=on                                                \
        -Daudacity_use_vst=Off                                                \
        -Daudacity_use_wavpack=system                                         \
        -Daudacity_use_midi=system                                            \
                                                                              \
        -Daudacity_use_soundtouch=system                                      \
        -Daudacity_use_twolame=system                                         \
                                                                              \
        -Daudacity_use_portmixer=on                                           \
        -Daudacity_use_nyquist=on                                             \
                                                                              \
        -Daudacity_bundle_gplv3=On
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}

post_install() {
    # CMake installs (verified against linux/packages/ubuntu-20.04/debian/audacity.install):
    #   binary           -> /usr/bin/audacity
    #   modules + libs   -> /usr/lib/audacity/{modules,libaudacity-*.so,...}
    #   data + nyquist   -> /usr/share/audacity/{nyquist,plug-ins,EQDefaultCurves.xml,...}
    #   desktop file     -> /usr/share/applications/audacity.desktop
    #   appdata          -> /usr/share/metainfo/audacity.appdata.xml
    #   icons (hicolor)  -> /usr/share/icons/hicolor/{16,22,24,32,48,...}x*/apps/
    #   man page         -> /usr/share/man/man1/audacity.1
    # Refresh icon cache + desktop database (best-effort; non-fatal if missing).
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
    update-mime-database -n /usr/share/mime 2>/dev/null || true
}

# do_test:
#   Audacity ships a Catch2-based test suite under tests/ which is enabled
#   only when `audacity_has_tests=On`. We disable tests at configure time
#   because:
#     1. Catch2 is required (REQUIRED via audacity_find_package) and is not
#        in our package tree. With conan_enabled=Off there is no fallback.
#     2. Even with Catch2 present, large portions of the suite require a
#        functioning audio device, a wxWidgets GUI event loop, and on-disk
#        scratch space patterns that don't translate to the chroot. Audacity's
#        own debian/rules at linux/packages/ubuntu-20.04/debian/rules
#        explicitly skips dh_auto_test (line 30: "tests fails with system
#        portaudio") — i.e., upstream itself does not run the suite when
#        building with system PortAudio, which is exactly our configuration.
#     3. The build itself is the verification: a successful link of audacity,
#        the lib-* shared objects, and the mod-* modules covers virtually all
#        of the project's compilable surface.
check() {
    return 0
}
