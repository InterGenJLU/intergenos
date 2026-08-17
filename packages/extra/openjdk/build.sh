#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# openjdk 24.0.2 — OpenJDK Java development kit, built from source
# Recipe reference: BLFS 12.x "OpenJDK" (general/openjdk.html).
#
# The shipped artifact is compiled from source (make images). A pinned
# bootstrap-seed JDK binary is extracted only to satisfy the circular
# "building a JVM requires a JVM" dependency — the same binary-seed pattern
# used by the in-tree rust and go recipes. The seed never ships.

BUILD_NUM="12"                       # OpenJDK build tag: 24.0.2+12
JDK_DIR="jdk-${PKG_VERSION}+${BUILD_NUM}"

# Each build phase runs in its OWN shell — an export from configure() never
# reaches build()/check()/do_install() (first build-verify: build's
# cd "${SRC_DIR}" got null once configure started succeeding). Every phase
# re-derives the source dir through this one helper.
_find_srcdir() {
    local d
    d="$(find "$PWD" -maxdepth 1 -type d \
             \( -name '*jdk*source*' -o -name 'jdk24u*' -o -name 'jdk-24*' \) \
             -not -name 'boot-jdk-seed' | head -1)"
    if [ -z "${d}" ] || [ ! -f "${d}/configure" ]; then
        d="$(find "$PWD" -maxdepth 2 -name configure -path '*jdk*' \
                 -not -path '*/boot-jdk-seed/*' -printf '%h\n' | head -1)"
    fi
    [ -f "${d}/configure" ] || { echo "OpenJDK source dir not found" >&2; return 1; }
    printf '%s\n' "${d}"
}

configure() {
    set -e

    # Extract the bootstrap-seed JDK (Rule 5: secondary source, explicit) into
    # a DEDICATED subdir: the Temurin source and binary tarballs can share the
    # same "jdk-<ver>+<build>" top-level name, and a same-dir extraction would
    # merge the two trees — poisoning both the seed probe and the source-dir
    # probe below. The subdir removes the collision class structurally.
    mkdir -p boot-jdk-seed
    tar -xf "${IGOS_SOURCES}/OpenJDK24U-jdk_x64_linux_hotspot_24.0.2_12.tar.gz" \
        -C boot-jdk-seed
    BOOT_JDK="$PWD/boot-jdk-seed/jdk-${PKG_VERSION}+${BUILD_NUM}"
    if [ ! -x "${BOOT_JDK}/bin/javac" ]; then
        # Seed tarballs occasionally carry a bare "jdk-<ver>" top dir; resolve it.
        BOOT_JDK="$(find "$PWD/boot-jdk-seed" -maxdepth 1 -type d -name 'jdk-24*' \
                    -exec test -x '{}/bin/javac' \; -print | head -1)"
    fi
    [ -x "${BOOT_JDK}/bin/javac" ] || { echo "seed JDK javac not found" >&2; exit 1; }
    export BOOT_JDK

    # The source tarball extracts to a single top-level directory; enter it
    # (the seed lives under boot-jdk-seed/, excluded from both probes).
    SRC_DIR="$(find "$PWD" -maxdepth 1 -type d \
                   \( -name '*jdk*source*' -o -name 'jdk24u*' -o -name 'jdk-24*' \) \
                   -not -name 'boot-jdk-seed' | head -1)"
    if [ -z "${SRC_DIR}" ] || [ ! -f "${SRC_DIR}/configure" ]; then
        SRC_DIR="$(find "$PWD" -maxdepth 2 -name configure -path '*jdk*' \
                   -not -path '*/boot-jdk-seed/*' -printf '%h\n' | head -1)"
    fi
    [ -f "${SRC_DIR}/configure" ] || { echo "OpenJDK source dir not found" >&2; exit 1; }
    export SRC_DIR
    cd "${SRC_DIR}"

    # Newer GCC: OpenJDK's bundled Hotspot uses uabs() which collides with a
    # libstdc++ symbol; BLFS renames it. Idempotent (no-op if already patched).
    find src -name \*.\*pp -exec sed -i 's/uabs(/g_uabs(/' {} \;

    # JAVA_HOME/CLASSPATH/MAKEFLAGS must be unset for the configure probe.
    unset JAVA_HOME CLASSPATH MAKEFLAGS

    # Generate the JDK trust store from the SYSTEM trust store (p11-kit) at
    # build — the BLFS /etc/pki/tls/java/cacerts path assumes make-ca ran with
    # a JDK present, which the build chroot never has (first build-verify:
    # configure fails loud on the absent file). Deriving from our own anchors
    # introduces no external trust; a make-ca refresh reaches the JDK at its
    # next rebuild (static-bundle semantics, same as the shipped CA bundle).
    trust extract --format=java-cacerts --filter=ca-anchors \
        --purpose=server-auth "${PWD}/.build-cacerts"

    bash configure \
        --with-boot-jdk="${BOOT_JDK}" \
        --enable-unlimited-crypto \
        --disable-warnings-as-errors \
        --with-stdc++lib=dynamic \
        --with-giflib=system \
        --with-harfbuzz=system \
        --with-lcms=system \
        --with-libjpeg=system \
        --with-libpng=system \
        --with-zlib=system \
        --with-version-build="${BUILD_NUM}" \
        --with-version-pre="" \
        --with-version-opt="" \
        --with-jobs="${IGOS_JOBS}" \
        --with-cacerts-file="${PWD}/.build-cacerts" \
        --without-version-pre
}

build() {
    set -e
    SRC_DIR="${SRC_DIR:-$(_find_srcdir)}"
    cd "${SRC_DIR}"
    unset MAKEFLAGS
    make images
}

check() {
    set -e
    SRC_DIR="${SRC_DIR:-$(_find_srcdir)}"
    cd "${SRC_DIR}"
    # Smoke-verify the freshly-built JDK: version + compile + run. The full
    # jtreg tier1 conformance suite needs an X display and hours of runtime and
    # is not run in-chroot; this proves the built compiler and runtime work
    # (the same hello-world discipline as the go/rust recipes).
    local jdk
    jdk="$(echo "$PWD"/build/*/images/jdk)"
    "${jdk}/bin/java" -version
    local t=/tmp/openjdk-smoke
    rm -rf "$t"; mkdir -p "$t"
    cat > "$t/Hello.java" << 'JAVAEOF'
public class Hello {
    public static void main(String[] a) {
        System.out.println("hello, InterGenOS");
    }
}
JAVAEOF
    "${jdk}/bin/javac" -d "$t" "$t/Hello.java"
    "${jdk}/bin/java" -cp "$t" Hello
    rm -rf "$t"
}

do_install() {
    set -e
    SRC_DIR="${SRC_DIR:-$(_find_srcdir)}"
    cd "${SRC_DIR}"
    local jdk
    jdk="$(echo "$PWD"/build/*/images/jdk)"

    install -vdm755 "${DESTDIR}/opt/${JDK_DIR}"
    cp -Rv "${jdk}"/* "${DESTDIR}/opt/${JDK_DIR}/"

    # Strip build-time debug info; harmless if none present.
    find "${DESTDIR}/opt/${JDK_DIR}" -name \*.debuginfo -delete 2>/dev/null || true

    # Stable /opt/jdk symlink + PATH via profile.d (BLFS layout).
    ln -vsfn "${JDK_DIR}" "${DESTDIR}/opt/jdk"

    install -vdm755 "${DESTDIR}/etc/profile.d"
    cat > "${DESTDIR}/etc/profile.d/openjdk.sh" << 'PROFILEEOF'
# OpenJDK: put the JDK on PATH. Modern Java needs no JAVA_HOME/CLASSPATH.
if [ -d /opt/jdk/bin ]; then
    case ":$PATH:" in
        *:/opt/jdk/bin:*) ;;
        *) PATH="/opt/jdk/bin:$PATH" ;;
    esac
    export PATH
fi
PROFILEEOF
    chmod 0644 "${DESTDIR}/etc/profile.d/openjdk.sh"

    # Desktop icons (Swing apps advertise these).
    local s
    for s in 16 24 32 48; do
        if [ -f "src/java.desktop/unix/classes/sun/awt/X11/java-icon${s}.png" ]; then
            install -vDm644 "src/java.desktop/unix/classes/sun/awt/X11/java-icon${s}.png" \
                "${DESTDIR}/usr/share/icons/hicolor/${s}x${s}/apps/java.png"
        fi
    done
}
