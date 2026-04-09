#!/bin/bash
# thunderbird 140.8.0esr — Mozilla Thunderbird email and news client
# BLFS 13.0
#
# WARNING: This is a HUGE build (~14 SBU on 8-core, 8.9 GB disk, needs 8 GB RAM)
# Ensure /dev/shm is mounted if building in chroot.

configure() {
    # Fix building with Python 3.14
    patch -Np1 -i "${IGOS_SOURCES}/thunderbird-140.8.0esr-python_3.14_fixes-1.patch"

    # Remove checksums from cargo crates for files that don't exist
    for crate in {minimal-lexical,lmdb-rkv,cubeb-sys,wasi,glslopt,sfv}; do
        sed -e 's|,"[^"]*.gitmodules[^,]*[^,]||' \
            -e '$a\' \
            -i comm/third_party/rust/$crate/.cargo-checksum.json
    done

    # Fix building with glibc-2.43 and adapt checksums
    GLSL_PTHREAD="comm/third_party/rust/glslopt/glsl-optimizer/include/c11/threads_posix.h"
    OLDSHA=$(sha256sum $GLSL_PTHREAD | awk '{ print $1 }')
    patch -Np1 -i "${IGOS_SOURCES}/thunderbird-140.8.0esr-glibc-2.43.patch"
    NEWSHA=$(sha256sum $GLSL_PTHREAD | awk '{ print $1 }')
    sed "s/$OLDSHA/$NEWSHA/" \
        -i comm/third_party/rust/glslopt/.cargo-checksum.json

    # Create mozconfig
    cat > mozconfig << "MOZEOF"
# If you have installed wireless-tools comment out this line:
ac_add_options --disable-necko-wifi

# Use system libraries for recommended dependencies
ac_add_options --with-system-av1
ac_add_options --with-system-libevent
ac_add_options --with-system-libvpx
ac_add_options --with-system-nspr
ac_add_options --with-system-nss
ac_add_options --with-system-webp

# Core build configuration
ac_add_options --prefix=/usr
ac_add_options --enable-application=comm/mail

ac_add_options --disable-crashreporter
ac_add_options --disable-updater
ac_add_options --disable-debug
ac_add_options --disable-debug-symbols
ac_add_options --disable-tests

# SIMD optimization in the shipped encoding_rs crate
ac_add_options --enable-rust-simd

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
    export MACH_BUILD_PYTHON_NATIVE_PACKAGE_SOURCE=none
    export MOZBUILD_STATE_PATH=$(pwd)/mozbuild
    ./mach build
}

do_install() {
    MACH_BUILD_PYTHON_NATIVE_PACKAGE_SOURCE=none \
        DESTDIR="$DESTDIR" ./mach install
}

post_install() {
    # Create desktop file for menu integration
    mkdir -pv /usr/share/{applications,pixmaps}

    cat > /usr/share/applications/thunderbird.desktop << "DESKTOP_EOF"
[Desktop Entry]
Name=Thunderbird Mail
Comment=Send and receive mail with Thunderbird
GenericName=Mail Client
Exec=thunderbird %u
Terminal=false
Type=Application
Icon=thunderbird
Categories=Network;Email;
MimeType=application/xhtml+xml;text/xml;application/xhtml+xml;application/xml;application/rss+xml;x-scheme-handler/mailto;
StartupNotify=true
DESKTOP_EOF

    # Link icon
    for s in 16 22 24 32 48 64 128 256; do
        if [ -f /usr/lib/thunderbird/chrome/icons/default/default${s}.png ]; then
            install -Dm644 /usr/lib/thunderbird/chrome/icons/default/default${s}.png \
                /usr/share/icons/hicolor/${s}x${s}/apps/thunderbird.png
        fi
    done

    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
}
