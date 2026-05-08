#!/bin/bash
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
    set -e
    # Halt #30 (2026-05-08): firefox compile fails with
    #   intl/lwbrk/LineBreaker.cpp:458 static_assert
    #   U_LB_COUNT == std::size(sUnicodeLineBreakToClass)
    #   evaluates to '49 == 48'
    # Our system ICU is one Unicode line-break-class ahead of firefox
    # 140.9.0esr's bundled sUnicodeLineBreakToClass[] table. With
    # --with-system-icu the static_assert fires.
    #
    # Resolution requires owner architectural decision: switch to bundled
    # ICU (--without-system-icu, mozilla-tested config) vs. ICU pin vs.
    # upstream patch. Holy Grail tradeoff (system-shared crypto/text
    # surface vs. mozilla-validated configuration) deserves explicit
    # owner input. Skip-and-continue tonight; queue for v1.0+1.
    #
    # Chrome download-helper covers browser need on the v1 ISO.
    return 0

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
    # Halt #30 skip — see configure().
    :
}

do_install() {
    set -e
    # Halt #30 skip — see configure().
    :
}

post_install() {
    set -e
    # Halt #30 skip — no firefox installed, nothing to wire up.
    return 0

    # Create desktop file for menu integration
    mkdir -pv /usr/share/{applications,pixmaps}

    cat > /usr/share/applications/firefox.desktop << "DESKTOP_EOF"
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

    # Install hicolor icons from the upstream chrome bundle (matches BLFS).
    for s in 16 22 24 32 48 64 128 256; do
        if [ -f /usr/lib/firefox/browser/chrome/icons/default/default${s}.png ]; then
            install -Dm644 /usr/lib/firefox/browser/chrome/icons/default/default${s}.png \
                /usr/share/icons/hicolor/${s}x${s}/apps/firefox.png
        fi
    done

    # /usr/share/pixmaps/firefox.png — used by some menus that don't read hicolor
    if [ -f /usr/lib/firefox/browser/chrome/icons/default/default48.png ]; then
        install -Dm644 /usr/lib/firefox/browser/chrome/icons/default/default48.png \
            /usr/share/pixmaps/firefox.png
    fi

    # Refresh icon cache + desktop database (best-effort; non-fatal if missing)
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
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
