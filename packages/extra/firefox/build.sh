#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# firefox 140.9.0esr — Mozilla Firefox web browser
# BLFS 13.0
#
# WARNING: This is a HUGE build (~15 SBU on 8-core, ~9 GB disk, needs 8 GB RAM)
# Ensure /dev/shm is mounted if building in chroot.
#
# Version selection: 140.9.0esr (Extended Support Release)
#   - BLFS canonical Firefox recipe pins this exact version
#   - The 4 toolchain patches we apply (Python 3.14, glibc 2.43, ffmpeg 8,
#     llvm 22) are published by BLFS only against 140.9.0esr
#   - Pairs with thunderbird 140.8.0esr already in this tier (matched
#     Mozilla ESR cycle)
#   - Rapid-release Firefox (150.x) does not have these patches and is
#     not reviewed by BLFS — would diverge from upstream distro practice

configure() {
    set -e -o pipefail
    # pipefail: NEWSHA=$(sha256sum | awk) below — without pipefail, a
    # missing file would silently produce an empty hash; pipefail makes
    # the failure halt the build instead.
    # ICU policy (2026-05-08, decided Option A): system ICU + patch.
    # firefox-140.9.0esr-icu78.patch teaches firefox's
    # intl/lwbrk/LineBreaker.cpp about the new ICU 78 line-break class
    # UNAMBIGUOUS_HYPHEN (LB 48 → CLASS_BREAKABLE), restoring parity between
    # firefox's static sUnicodeLineBreakToClass[] and our system ICU's
    # U_LB_COUNT. Patch is upstream-Gentoo's, applies clean to 140.9.0esr.
    # System ICU = one shared library on the system (a CVE rebuild benefits
    # everything, not firefox alone) + ICU is independently user-patchable
    # without a firefox rebuild.

    # Patches applied by builder PATCH phase (package.yml) with SHA256 validation.
    # Post-patch fixups only below.

    # Remove checksums from cargo crates for files that don't exist after patching
    # (matches the thunderbird treatment for the same toolchain patches).
    for crate in {minimal-lexical,lmdb-rkv,cubeb-sys,wasi,glslopt,sfv}; do
        if [ -f third_party/rust/$crate/.cargo-checksum.json ]; then
            sed -e 's|,"[^"]*.gitmodules[^,]*[^,]||' \
                -e '$a\' \
                -i third_party/rust/$crate/.cargo-checksum.json
        fi
    done

    # Update cargo checksum for glibc-2.43 patched file (glslopt threads_posix.h)
    GLSL_PTHREAD="third_party/rust/glslopt/glsl-optimizer/include/c11/threads_posix.h"
    if [ -f "$GLSL_PTHREAD" ]; then
        NEWSHA=$(sha256sum "$GLSL_PTHREAD" | awk '{ print $1 }')
        sed -i "s|threads_posix.h\":\"[a-f0-9]*\"|threads_posix.h\":\"$NEWSHA\"|" \
            third_party/rust/glslopt/.cargo-checksum.json
    fi

    # Create mozconfig
    cat > mozconfig << "MOZEOF"
# If you have installed wireless-tools comment out this line:
ac_add_options --disable-necko-wifi

# Use system libraries for recommended dependencies
ac_add_options --with-system-av1
ac_add_options --with-system-icu
ac_add_options --with-system-libevent
ac_add_options --with-system-libvpx
ac_add_options --with-system-nspr
ac_add_options --with-system-nss
ac_add_options --with-system-webp

# Core build configuration
ac_add_options --prefix=/usr
ac_add_options --enable-application=browser

ac_add_options --disable-crashreporter
ac_add_options --disable-updater
ac_add_options --disable-debug
ac_add_options --disable-debug-symbols
ac_add_options --disable-tests

# Default-browser-agent is a Windows-mostly service that probes/maintains
# Mozilla's default-browser usage telemetry. Off-switch confirmed in
# Firefox 75+ build options.
ac_add_options --disable-default-browser-agent

# NOTE: --enable-rust-simd intentionally OMITTED. encoding_rs's simd-accel
# feature requires nightly Rust (uses feature(core_intrinsics, portable_simd)).
# We ship stable Rust 1.95.0; build fails with E0599 in encoding_rs.
# Standard distro practice; encoding correct via scalar fallback paths.

ac_add_options --enable-strip
ac_add_options --enable-install-strip

# Official branding (cannot distribute the binary if you do this)
ac_add_options --enable-official-branding

ac_add_options --enable-system-ffi
ac_add_options --enable-system-pixman

ac_add_options --with-system-jpeg
ac_add_options --with-system-png
ac_add_options --with-system-zlib

# Disable sandboxed wasm libraries (seriously slows the build)
ac_add_options --without-wasm-sandboxed-libraries
MOZEOF
}

build() {
    set -e
    export MACH_BUILD_PYTHON_NATIVE_PACKAGE_SOURCE=none
    export MOZBUILD_STATE_PATH=$(pwd)/mozbuild

    # GCC detection handled system-wide by /etc/clang/clang.cfg
    # (--gcc-triple=x86_64-igos-linux-gnu)
    ./mach build
}

do_install() {
    set -e
    MACH_BUILD_PYTHON_NATIVE_PACKAGE_SOURCE=none \
        DESTDIR="$DESTDIR" ./mach install

    # Mozilla distribution policies (system-wide enforcement). Ships at
    # /usr/lib/firefox/distribution/policies.json -- the canonical Mozilla
    # mechanism for enterprise/system-wide policy lockdown. Per
    # docs/users/desktop-experience.md section 7: "Firefox telemetry is
    # disabled at build time" -- this is the actual enforcement mechanism.
    # Mozilla policies.json overrides about:config + cannot be re-enabled
    # by the user (Locked semantics).
    #
    # Canonical keys verified against mozilla.github.io/policy-templates/.
    # Preferences sub-keys verified against arkenfox/user.js canonical set
    # (the gold-standard "telemetry off" preference list maintained by the
    # privacy community).
    install -dm755 "${DESTDIR}/usr/lib/firefox/distribution"
    cat > "${DESTDIR}/usr/lib/firefox/distribution/policies.json" << 'POLICIES_EOF'
{
    "policies": {
        "DisableTelemetry": true,
        "DisableFirefoxStudies": true,
        "DisablePocket": true,
        "DisableFeedbackCommands": true,
        "DontCheckDefaultBrowser": true,
        "DisableSystemAddonUpdate": true,
        "FirefoxHome": {
            "Pocket": false,
            "Snippets": false,
            "Highlights": false
        },
        "UserMessaging": {
            "ExtensionRecommendations": false,
            "FeatureRecommendations": false,
            "MoreFromMozilla": false,
            "SkipOnboarding": true,
            "UrlbarInterventions": false,
            "WhatsNew": false
        },
        "OverrideFirstRunPage": "",
        "OverridePostUpdatePage": "",
        "Preferences": {
            "toolkit.telemetry.unified": { "Value": false, "Status": "locked" },
            "toolkit.telemetry.enabled": { "Value": false, "Status": "locked" },
            "toolkit.telemetry.server": { "Value": "data:,", "Status": "locked" },
            "toolkit.telemetry.archive.enabled": { "Value": false, "Status": "locked" },
            "toolkit.telemetry.newProfilePing.enabled": { "Value": false, "Status": "locked" },
            "toolkit.telemetry.shutdownPingSender.enabled": { "Value": false, "Status": "locked" },
            "toolkit.telemetry.updatePing.enabled": { "Value": false, "Status": "locked" },
            "toolkit.telemetry.bhrPing.enabled": { "Value": false, "Status": "locked" },
            "toolkit.telemetry.firstShutdownPing.enabled": { "Value": false, "Status": "locked" },
            "toolkit.telemetry.coverage.opt-out": { "Value": true, "Status": "locked" },
            "datareporting.policy.dataSubmissionEnabled": { "Value": false, "Status": "locked" },
            "datareporting.healthreport.uploadEnabled": { "Value": false, "Status": "locked" },
            "app.normandy.enabled": { "Value": false, "Status": "locked" },
            "app.normandy.api_url": { "Value": "", "Status": "locked" },
            "app.shield.optoutstudies.enabled": { "Value": false, "Status": "locked" },
            "browser.discovery.enabled": { "Value": false, "Status": "locked" },
            "browser.crashReports.unsubmittedCheck.autoSubmit2": { "Value": false, "Status": "locked" },
            "browser.newtabpage.activity-stream.showSponsored": { "Value": false, "Status": "locked" },
            "browser.newtabpage.activity-stream.showSponsoredTopSites": { "Value": false, "Status": "locked" }
        }
    }
}
POLICIES_EOF

    # Menu integration ships IN THE ARCHIVE (owned payload) rather than
    # being written by a target-side hook: hook output lands after pkm
    # records the archive, so the desktop entry, 8 hicolor icons and the
    # pixmap arrived owned by nobody on every install. Everything here
    # copies material from the package's own staged payload; the icon-
    # cache/desktop-database refresh the old hook ended with is pkm's own
    # canonical per-package hook on the target.
    mkdir -pv "${DESTDIR}/usr/share/applications" "${DESTDIR}/usr/share/pixmaps"

    cat > "${DESTDIR}/usr/share/applications/firefox.desktop" << "DESKTOP_EOF"
[Desktop Entry]
Name=Firefox
Comment=Browse the World Wide Web
GenericName=Web Browser
Exec=firefox %u
Terminal=false
Type=Application
Icon=firefox
Categories=Network;WebBrowser;
MimeType=text/html;text/xml;application/xhtml+xml;application/xml;application/rss+xml;application/rdf+xml;image/gif;image/jpeg;image/png;x-scheme-handler/http;x-scheme-handler/https;x-scheme-handler/ftp;x-scheme-handler/chrome;video/webm;application/x-xpinstall;
StartupNotify=true
StartupWMClass=firefox
DESKTOP_EOF

    # hicolor icons from the upstream chrome bundle (matches BLFS)
    for s in 16 22 24 32 48 64 128 256; do
        if [ -f "${DESTDIR}/usr/lib/firefox/browser/chrome/icons/default/default${s}.png" ]; then
            install -Dm644 "${DESTDIR}/usr/lib/firefox/browser/chrome/icons/default/default${s}.png" \
                "${DESTDIR}/usr/share/icons/hicolor/${s}x${s}/apps/firefox.png"
        fi
    done

    # /usr/share/pixmaps/firefox.png — used by some menus that don't read hicolor
    if [ -f "${DESTDIR}/usr/lib/firefox/browser/chrome/icons/default/default48.png" ]; then
        install -Dm644 "${DESTDIR}/usr/lib/firefox/browser/chrome/icons/default/default48.png" \
            "${DESTDIR}/usr/share/pixmaps/firefox.png"
    fi
}

# do_test:
#   BLFS explicitly recommends NOT running Mozilla's test suite for distro
#   builds: it requires hours of additional time and many GB of disk for
#   marginal benefit. The configure step sets --disable-tests which removes
#   the relevant test binaries from the build graph entirely. Smoke testing
#   is performed at first-boot via the launcher (icon + desktop file).
#   See: BLFS xsoft/firefox.html "Testing Firefox" section.
check() {
    set -e
    return 0
}
