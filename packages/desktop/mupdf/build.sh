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
USE_SYSTEM_GLUT := no
HAVE_GLUT := no
USE_SYSTEM_CURL := yes
USE_SYSTEM_GUMBO := no
EOF
}

build() {
    export XCFLAGS=-fPIC
    make -j${IGOS_JOBS} build=release verbose=yes
    unset XCFLAGS
}

do_install() {
    make DESTDIR="$DESTDIR" \
         prefix=/usr \
         build=release \
         docdir=/usr/share/doc/mupdf-${version} \
         install

    ln -sfv mupdf-x11 "${DESTDIR}/usr/bin/mupdf"
}
