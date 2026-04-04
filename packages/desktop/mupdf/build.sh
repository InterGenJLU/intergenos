#!/bin/bash
# mupdf 1.26.12 — Lightweight PDF and XPS viewer
# BLFS 13.0

configure() {
    cat > user.make << EOF
USE_SYSTEM_FREETYPE := yes
USE_SYSTEM_HARFBUZZ := yes
USE_SYSTEM_JBIG2DEC := no
USE_SYSTEM_JPEGXR := no
USE_SYSTEM_LCMS2 := no
USE_SYSTEM_LIBJPEG := yes
USE_SYSTEM_MUJS := no
USE_SYSTEM_OPENJPEG := yes
USE_SYSTEM_ZLIB := yes
USE_SYSTEM_GLUT := yes
USE_SYSTEM_CURL := yes
USE_SYSTEM_GUMBO := no
EOF
}

build() {
    export XCFLAGS=-fPIC
    make -j${IGOS_JOBS} build=release shared=yes verbose=yes
    unset XCFLAGS
}

do_install() {
    make DESTDIR="$DESTDIR" \
         prefix=/usr \
         shared=yes \
         docdir=/usr/share/doc/mupdf-${version} \
         install

    # For version 1.26.12: major=1, minor=26, patch=12
    # BLFS creates libmupdf.so.26.12 and libmupdf.so.26
    local IFS='.'
    set -- ${version}
    local minor="$2"
    local patch="$3"

    ln -sfv "libmupdf.so.${minor}.${patch}" "${DESTDIR}/usr/lib/libmupdf.so.${minor}"
    ln -sfv "libmupdf.so.${minor}" "${DESTDIR}/usr/lib/libmupdf.so"
    chmod 755 "${DESTDIR}/usr/lib/libmupdf.so."* 2>/dev/null || true

    ln -sfv mupdf-x11 "${DESTDIR}/usr/bin/mupdf"
}
