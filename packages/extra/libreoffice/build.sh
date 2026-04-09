#!/bin/bash
# libreoffice 26.2.1.2 — Full-featured office productivity suite
# BLFS 13.0
#
# WARNING: This is the LARGEST build (~21 SBU with parallelism=8, 11 GB disk).
# An Internet connection is required — ~80 small tarballs are downloaded during build.
# Built WITHOUT Java.

configure() {
    # Fix build failures with poppler-26.02.0
    patch -Np1 -i "${IGOS_SOURCES}/libreoffice-26.2.1.2-poppler_26.02-1.patch"

    # Fix zlib linking bug, install failure, and prevent man page compression
    sed -i '/icuuc \\/a zlib\\'           writerperfect/Library_wpftdraw.mk
    sed -i "/distro-install-file-lists/d" Makefile.in
    sed -e "/gzip -f/d"   \
        -e "s|.1.gz|.1|g" \
        -i bin/distro-install-desktop-integration

    # Link dictionaries and help tarballs if downloaded separately
    install -dm755 external/tarballs
    for f in libreoffice-dictionaries-26.2.1.2.tar.xz \
             libreoffice-help-26.2.1.2.tar.xz         \
             libreoffice-translations-26.2.1.2.tar.xz; do
        if [ -f "${IGOS_SOURCES}/$f" ]; then
            ln -svf "${IGOS_SOURCES}/$f" external/tarballs/
        fi
    done

    # Create symlinks for unpacked content
    for d in helpcontent2:libreoffice-help-26.2.1.2 \
             dictionaries:libreoffice-dictionaries-26.2.1.2 \
             translations:libreoffice-translations-26.2.1.2; do
        target="${d%%:*}"
        srcdir="${d##*:}"
        [ -d "src/$srcdir/$target" ] && ln -svf "src/$srcdir/$target/" . || true
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
    make build
}

do_install() {
    make DESTDIR="$DESTDIR" distro-pack-install
}

post_install() {
    update-desktop-database -q 2>/dev/null || true
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
}
