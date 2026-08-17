#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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
    # Fix ncurses header include path in gptcurses.cc for our LFS-layout
    # chroot. Upstream gptcurses.cc assumes Debian/Ubuntu layout where
    # ncursesw headers sit at /usr/include/ncursesw/ (Linux `#else` branch
    # includes <ncursesw/ncurses.h>). Our chroot is LFS-default: headers
    # land at /usr/include/ncurses.h directly with NO ncursesw/ subdir.
    # Strip the ncursesw/ prefix so the include resolves on LFS layout.
    # Idempotent (no-op if already stripped). Diagnosed 2026-05-23 20:09 CDT
    # after prior sed `s@ncurses.h@ncursesw/&@` had wrong direction (it
    # ADDED prefix, producing <ncursesw/ncursesw/ncurses.h> on re-extract
    # and breaking the build).
    sed -i 's|<ncursesw/ncurses.h>|<ncurses.h>|g' gptcurses.cc
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
