#!/bin/bash
# libreoffice 26.2.1.2 — Full-featured office productivity suite
# BLFS 13.0
#
# WARNING: This is the LARGEST build (~21 SBU with parallelism=8, 11 GB disk).
# Built WITHOUT Java.
#
# External dependencies (~80 tarballs) are pre-downloaded to
# /sources/libreoffice-externals/ and loaded via --with-external-tar.
# Network access is NOT required at build time.

configure() {
    set -e
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    # Fix zlib linking bug, install failure, and prevent man page compression
    sed -i '/icuuc \\/a zlib\\'           writerperfect/Library_wpftdraw.mk
    sed -i "/distro-install-file-lists/d" Makefile.in
    sed -e "/gzip -f/d"   \
        -e "s|.1.gz|.1|g" \
        -i bin/distro-install-desktop-integration

    # Link companion tarballs and pre-downloaded externals
    install -dm755 external/tarballs
    for f in libreoffice-dictionaries-26.2.1.2.tar.xz \
             libreoffice-help-26.2.1.2.tar.xz         \
             libreoffice-translations-26.2.1.2.tar.xz; do
        if [ -f "${IGOS_SOURCES}/$f" ]; then
            ln -svf "${IGOS_SOURCES}/$f" external/tarballs/
        fi
    done

    # Link all pre-downloaded external tarballs so LO finds them
    if [ -d "${IGOS_SOURCES}/libreoffice-externals" ]; then
        for f in "${IGOS_SOURCES}"/libreoffice-externals/*; do
            [ -f "$f" ] && ln -svf "$f" external/tarballs/ 2>/dev/null
        done
    fi

    # Pre-extract companion tarballs.
    # With --disable-fetch-external, the build system won't extract these
    # automatically. The companion tarballs share the same top-level directory
    # name (libreoffice-26.2.1.2/) as the main source — they're designed to
    # be extracted alongside it and merge into the same tree. Since the builder
    # already stripped that level with --strip-components=1, we do the same
    # here so dictionaries/, helpcontent2/, and translations/ land directly
    # in the CWD (the source root).
    for f in libreoffice-dictionaries-26.2.1.2.tar.xz \
             libreoffice-help-26.2.1.2.tar.xz         \
             libreoffice-translations-26.2.1.2.tar.xz; do
        if [ -f "external/tarballs/$f" ]; then
            tar -xf "external/tarballs/$f" --strip-components=1
        fi
    done

    export LO_PREFIX=/usr

    ./autogen.sh --prefix=$LO_PREFIX         \
                 --sysconfdir=/etc            \
                 --with-vendor="InterGenOS"   \
                 --with-lang='en-US'          \
                 --with-help=html             \
                 --with-myspell-dicts         \
                 --without-java               \
                 --without-junit              \
                 --without-system-dicts       \
                 --disable-dconf              \
                 --disable-odk               \
                 --disable-mariadb-sdbc       \
                 --disable-online-update      \
                 --disable-fetch-external     \
                 --with-external-tar="$(pwd)/external/tarballs" \
                 --enable-release-build=yes   \
                 --enable-python=system       \
                 --with-system-boost          \
                 --with-system-clucene        \
                 --with-system-curl           \
                 --with-system-epoxy          \
                 --with-system-expat          \
                 --with-system-glm            \
                 --with-system-gpgmepp        \
                 --with-system-graphite       \
                 --with-system-harfbuzz       \
                 --with-system-icu            \
                 --with-system-jpeg           \
                 --with-system-lcms2          \
                 --with-system-libatomic_ops  \
                 --with-system-libtiff        \
                 --with-system-libpng         \
                 --with-system-libxml         \
                 --with-system-libwebp        \
                 --with-system-nss            \
                 --with-system-odbc           \
                 --with-system-openssl        \
                 --with-system-poppler        \
                 --with-system-redland        \
                 --with-system-zlib           \
                 --with-system-zstd
}

build() {
    set -e
    # LibreOffice refuses to build as root — bypass the check since
    # we're building in a controlled chroot environment.
    sed -i "s/test ! \`uname\` = 'Haiku' -a \`id -u\` = 0/false/" Makefile

    # Unset DESTDIR during build — LibreOffice's build target runs
    # install-gdb-printers which uses DESTDIR, causing double-nested paths.
    # DESTDIR is only needed for the install phase (do_install).
    unset DESTDIR
    make build
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" distro-pack-install
}

post_install() {
    set -e
    update-desktop-database -q 2>/dev/null || true
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
}
