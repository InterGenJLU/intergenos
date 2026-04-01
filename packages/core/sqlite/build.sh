#!/bin/bash
# Sqlite 3510200
# LFS 13.0 Section 8.52

configure() {
    ./configure --prefix=/usr     \
        --disable-static          \
        --enable-fts4             \
        --enable-fts5             \
        CPPFLAGS="-DSQLITE_ENABLE_COLUMN_METADATA=1 \
                  -DSQLITE_ENABLE_UNLOCK_NOTIFY=1   \
                  -DSQLITE_ENABLE_DBSTAT_VTAB=1     \
                  -DSQLITE_SECURE_DELETE=1"
}

build() {
    make LDFLAGS.rpath="" -j${IGOS_JOBS}
}

check() {
    : # No test suite
}

install() {
    make DESTDIR="$DESTDIR" install
}
