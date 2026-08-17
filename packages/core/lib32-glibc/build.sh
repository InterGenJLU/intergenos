#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# Glibc 2.43 — 32-bit multilib runtime (GE arc)
# Multilib-LFS 13.0 Section 8.5.3
#
# Builds the m32 glibc into /usr/lib32. Runs immediately after glibc-core
# and BEFORE the multilib gcc-core build (which needs the 32-bit startfiles,
# the gnu/*-32.h headers, and the /lib/ld-linux.so.2 loader present).
#
# Header-clobber protection (D-W0-6, trap T3): a full m32 "make install"
# over the live root would overwrite the 64-bit gnu/stubs.h, gnu/lib-names.h
# and hundreds of other headers. This recipe installs into a private
# install_root and stages an ALLOWLIST into DESTDIR: the /usr/lib32 tree,
# exactly the two gnu/{lib-names,stubs}-32.h headers (the 64-bit dispatcher
# headers already #include the -32.h variants under -m32), the loader
# compatibility symlink, and the ld.so.conf.d drop-in. Nothing else leaves
# the staging root, and do_install asserts that mechanically.

configure() {
    set -e
    # glibc-fhs-1.patch applied by the builder PATCH phase (package.yml).

    mkdir -v build
    cd       build

    echo "rootsbindir=/usr/sbin" > configparms

    # i686-pc-linux-gnu is the Multilib-LFS native-in-chroot host triplet
    # for the Ch 8 m32 pass (the i686-igos- cross triplet is Ch 5 only).
    CC="gcc -m32" CXX="g++ -m32"                 \
    ../configure --prefix=/usr                   \
        --host=i686-pc-linux-gnu                 \
        --build=x86_64-igos-linux-gnu            \
        --libdir=/usr/lib32                      \
        --libexecdir=/usr/lib32                  \
        --disable-werror                         \
        --disable-nscd                           \
        libc_cv_slibdir=/usr/lib32               \
        --enable-stack-protector=strong          \
        --enable-kernel=5.4
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build

    # Skip test-installation rule (fails under install_root; same as glibc-core)
    sed '/test-installation/s@$(PERL)@echo not running@' -i ../Makefile

    # Glibc uses install_root, not DESTDIR. Install the FULL m32 tree into a
    # private root, then stage only the allowlisted pieces below.
    make install_root="$PWD/m32root" install

    # 1) The 32-bit runtime tree
    install -dm755 "${DESTDIR}/usr/lib32"
    cp -a m32root/usr/lib32/* "${DESTDIR}/usr/lib32/"

    # 2) EXACTLY the two 32-bit dispatcher headers (allowlist — never the
    #    full include tree; see the header comment)
    install -dm755 "${DESTDIR}/usr/include/gnu"
    install -vm644 m32root/usr/include/gnu/lib-names-32.h \
                   m32root/usr/include/gnu/stubs-32.h     \
                   "${DESTDIR}/usr/include/gnu/"

    # 3) 32-bit dynamic loader compatibility symlink. Every m32 ELF carries
    #    PT_INTERP=/lib/ld-linux.so.2 (baked by gcc); the real loader lands
    #    at /usr/lib32/ld-linux.so.2. Same problem/fix as glibc-core's
    #    /lib64/ld-linux-x86-64.so.2 staging — 32-bit flavor.
    install -dm755 "${DESTDIR}/usr/lib"
    ln -sfv ../lib32/ld-linux.so.2 "${DESTDIR}/usr/lib/ld-linux.so.2"

    # 4) Loader search path as a package-owned drop-in (D-W0-3; glibc-core's
    #    /etc/ld.so.conf already ends with "include /etc/ld.so.conf.d/*.conf")
    install -dm755 "${DESTDIR}/etc/ld.so.conf.d"
    echo "/usr/lib32" > "${DESTDIR}/etc/ld.so.conf.d/lib32-glibc.conf"

    # --- T3 staged-payload allowlist assertion (fail loudly, never ship) ---
    # FULL-payload sweep: every NON-DIRECTORY entry in the staged tree must
    # be on the explicit allowlist — the lib32 tree, exactly the two -32.h
    # dispatcher headers, the loader compat symlink, and the ld.so.conf.d
    # drop-in. Matching non-directories (not just regular files) catches
    # stray symlinks/FIFOs/device nodes too, and sweeping the WHOLE tree
    # (not just include + lib) closes the class, not the instance.
    # (Hardened per the Wave-1 adversarial-verify finding W1-b on the shared lib32 guard.)
    #
    # The staging FRAMEWORK pre-creates the merged-usr skeleton symlinks in
    # every staging root (pkg_stage: bin/lib/sbin -> usr/<name>) — GE-01
    # launch-6 halt. VERIFY each is exactly that skeleton symlink, then
    # exclude it from the sweep; a real file or a wrong target at those
    # paths still FATALs (verify-then-exclude, never a blanket exclusion).
    local link
    for link in bin lib sbin; do
        if [ -e "${DESTDIR}/${link}" ] || [ -L "${DESTDIR}/${link}" ]; then
            if [ ! -L "${DESTDIR}/${link}" ] || \
               [ "$(readlink "${DESTDIR}/${link}")" != "usr/${link}" ]; then
                echo "FATAL: lib32-glibc staging root has a non-skeleton /${link} entry" >&2
                return 1
            fi
        fi
    done
    local stray
    stray=$(find "${DESTDIR}" ! -type d \
                ! -path "${DESTDIR}/bin" \
                ! -path "${DESTDIR}/lib" \
                ! -path "${DESTDIR}/sbin" \
                ! -path "${DESTDIR}/usr/lib32/*" \
                ! -path "${DESTDIR}/usr/include/gnu/lib-names-32.h" \
                ! -path "${DESTDIR}/usr/include/gnu/stubs-32.h" \
                ! -path "${DESTDIR}/usr/lib/ld-linux.so.2" \
                ! -path "${DESTDIR}/etc/ld.so.conf.d/lib32-glibc.conf" | head -5)
    if [ -n "$stray" ]; then
        echo "FATAL: lib32-glibc staged non-allowlisted content:" >&2
        echo "$stray" >&2
        return 1
    fi
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    set -e
    # Pick up the new /usr/lib32 search path
    ldconfig

    # --- T4 canary: a 32-bit binary must EXECUTE, not just exist ---
    # Proves loader path + interpreter symlink + m32 runtime end-to-end.
    echo 'int main(){return 0;}' > /tmp/lib32-canary.c
    gcc -m32 /tmp/lib32-canary.c -o /tmp/lib32-canary
    /tmp/lib32-canary
    echo "lib32-glibc: 32-bit exec canary PASSED"
    rm -v /tmp/lib32-canary.c /tmp/lib32-canary
}
