#!/bin/bash
# spidermonkey 128.6.0 — Mozilla SpiderMonkey JavaScript engine
# BLFS 13.0
# Note: source is from firefox ESR tarball

configure() {
    # Apply Python 3.14 compatibility patch
    patch -Np1 -i ../spidermonkey-*-python_3.14_fixes-1.patch

    mkdir obj &&
    cd    obj &&

    ../js/src/configure --prefix=/usr            \
                        --disable-debug-symbols  \
                        --disable-jemalloc       \
                        --enable-readline        \
                        --enable-rust-simd       \
                        --with-intl-api          \
                        --with-system-icu        \
                        --with-system-zlib
}

build() {
    cd obj &&
    make -j${IGOS_JOBS}
}

check() {
    cd obj &&
    make -C js/src check-jstests \
         JSTESTS_EXTRA_ARGS="--timeout 300 --wpt=disabled" || true
}

do_install() {
    cd obj &&

    # Remove old shared lib to avoid crash on reinstall
    rm -fv "${DESTDIR}/usr/lib/libmozjs-"*.so 2>/dev/null || true

    make DESTDIR="$DESTDIR" install

    # Remove static lib
    rm -v "${DESTDIR}/usr/lib/libjs_static.ajs" 2>/dev/null || true

    # Fix js config
    sed -i '/@NSPR_CFLAGS@/d' "${DESTDIR}/usr/bin/js"*-config 2>/dev/null || true
}

post_install() {
    # Fix header for XP_UNIX define
    local jsver="${version%%.*}"
    sed "\$i#define XP_UNIX" -i "/usr/include/mozjs-${jsver}/js-config.h" 2>/dev/null || true
}
