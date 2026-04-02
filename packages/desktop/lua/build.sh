#!/bin/bash
# lua 5.4.7 — Lightweight scripting language
# BLFS 13.0

configure() {
    # Create pkg-config file
    cat > lua.pc << "EOF"
V=5.4
R=${version}

prefix=/usr
INSTALL_BIN=${prefix}/bin
INSTALL_INC=${prefix}/include
INSTALL_LIB=${prefix}/lib
INSTALL_MAN=${prefix}/share/man/man1
INSTALL_LMOD=${prefix}/share/lua/${V}
INSTALL_CMOD=${prefix}/lib/lua/${V}
exec_prefix=${prefix}
libdir=${exec_prefix}/lib
includedir=${prefix}/include

Name: Lua
Description: An Extensible Extension Language
Version: ${R}
Requires:
Libs: -L${libdir} -llua -lm -ldl
Cflags: -I${includedir}
EOF

    # Apply shared library patch
    patch -Np1 -i ../lua-*-shared_library-1.patch
}

build() {
    make -j${IGOS_JOBS} linux
}

check() {
    make test || true
}

do_install() {
    make INSTALL_TOP="${DESTDIR}/usr"                \
         INSTALL_DATA="cp -d"            \
         INSTALL_MAN="${DESTDIR}/usr/share/man/man1" \
         TO_LIB="liblua.so liblua.so.5.4 liblua.so.${version}" \
         install

    mkdir -pv                      "${DESTDIR}/usr/share/doc/lua-${version}"
    cp -v doc/*.{html,css,gif,png} "${DESTDIR}/usr/share/doc/lua-${version}"

    install -v -m644 -D lua.pc "${DESTDIR}/usr/lib/pkgconfig/lua.pc"
}
