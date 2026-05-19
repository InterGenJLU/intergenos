#!/bin/bash
# gptfdisk 1.0.10 — GPT partition tools (BLFS 13.0 / postlfs/gptfdisk).
# T0-3 sub-cluster 1 — installer runtime dep (sgdisk for scripted GPT ops).
#
# Upstream Makefile ships no install target — the BLFS book recommends a
# convenience patch (gptfdisk-1.0.10-convenience-1.patch) that adds one.
# We inline that patch logic via sed below (avoids carrying a patches/ tree).
# Patch content verified against the BLFS-canonical patch at:
#   https://www.linuxfromscratch.org/patches/blfs/svn/gptfdisk-1.0.10-convenience-1.patch

configure() {
    set -e
    # Add install target to Makefile (BLFS convenience patch, inline).
    # Targets land under /usr/sbin (not /sbin per the BLFS patch as-published)
    # so verify_paths is unambiguous on UsrMerge systems.
    if ! grep -q '^install:' Makefile; then
        cat >> Makefile <<'EOF'

install: gdisk cgdisk sgdisk fixparts
	install -dm755 $(DESTDIR)/usr/sbin $(DESTDIR)/usr/share/man/man8
	install -m755 gdisk cgdisk sgdisk fixparts $(DESTDIR)/usr/sbin
	install -m644 *.8 $(DESTDIR)/usr/share/man/man8
EOF
    fi
    # Fix ncurses header include path in gptcurses.cc (BLFS-noted issue
    # arising when ncursesw headers land in /usr/include/ncursesw/ rather
    # than /usr/include/).
    sed -i 's@ncurses.h@ncursesw/&@' gptcurses.cc 2>/dev/null || true
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
