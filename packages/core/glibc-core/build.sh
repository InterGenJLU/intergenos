#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Glibc 2.43
# LFS 13.0 Section 8.5
#
# DESTDIR exception: Glibc uses install_root instead of DESTDIR.
# Post-install: nsswitch.conf, ld.so.conf, timezone, locales.

configure() {
    set -e
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    mkdir -v build
    cd       build

    echo "rootsbindir=/usr/sbin" > configparms

    ../configure --prefix=/usr                   \
        --build=x86_64-igos-linux-gnu            \
        --host=x86_64-igos-linux-gnu             \
        --disable-werror                         \
        --disable-nscd                           \
        libc_cv_slibdir=/usr/lib                 \
        --enable-stack-protector=strong           \
        --enable-kernel=5.4
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

check() {
    set -e
    cd build
    # CRITICAL: Do not skip the glibc test suite
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check

    # Check for timeouts (common in chroot)
    echo ""
    echo "=== Glibc Timeout Check ==="
    grep "Timed out" $(find -name \*.out) 2>/dev/null || echo "No timeouts"
}

do_install() {
    set -e
    cd build

    # Prevent warnings during install
    touch "${DESTDIR}/etc/ld.so.conf" 2>/dev/null || true

    # Skip test-installation rule (it would fail in DESTDIR)
    sed '/test-installation/s@$(PERL)@echo not running@' -i ../Makefile

    # Glibc uses install_root, not DESTDIR
    make install_root="$DESTDIR" install

    # Fix ldd path
    sed '/RTLDLIST=/s@/usr@@g' -i "${DESTDIR}/usr/bin/ldd"

    # Install minimal set of locales needed for tests and basic operation
    mkdir -pv "${DESTDIR}/usr/lib/locale"
    # These localedef commands must run against the staged glibc
    # They will be re-run in post_install against the live system

    # /lib64 dynamic-linker symlinks. Bash and every other ELF binary
    # built on this system carries `/lib64/ld-linux-x86-64.so.2` as its
    # hardcoded interpreter path (set by gcc at compile time, baked into
    # the ELF PT_INTERP header). glibc installs the actual loader at
    # /usr/lib/ld-linux-x86-64.so.2, and the build chroot historically
    # had these symlinks created by toolchain/glibc/build.sh directly
    # into $IGOS/lib64 — but they were NEVER packaged, so a fresh pkm
    # install onto a clean target had no /lib64 entries and `chroot
    # /mnt/target /bin/bash` failed with "No such file or directory"
    # (kernel can't resolve PT_INTERP). Surfaced 2026-05-26 during
    # install attempts #5/#6/#7 — localedef hook is the first user of
    # chroot-of-bash and was always where the missing-interpreter
    # symptom landed. ld-lsb-x86-64.so.3 mirrors the same target for
    # LSB-compatible binaries.
    install -dm755 "${DESTDIR}/lib64"
    ln -sfv ../lib/ld-linux-x86-64.so.2 "${DESTDIR}/lib64/ld-linux-x86-64.so.2"
    ln -sfv ../lib/ld-linux-x86-64.so.2 "${DESTDIR}/lib64/ld-lsb-x86-64.so.3"

    # Ship the static /etc/ld.so.conf and /etc/nsswitch.conf templates
    # in DESTDIR so they're present on every fresh pkm install.
    # Historical layout: these were created only by post_install (below)
    # which runs against the live system. The build chroot has them
    # because post_install fires during build, and the live ISO is
    # the build chroot's filesystem — but pkm doesn't replay package
    # post_install when installing onto a fresh target (only archive-
    # shipped .scripts/post_install.sh + per-archive system hooks run).
    # The recipe's bash post_install() IS re-run on the target, but by
    # the installer rather than by pkm, and only at the hooks phase:
    # installer/backend/hooks.py run_post_install_hooks copies the
    # recipe tree to the target and sources each build.sh in a chroot
    # of it. That re-run cannot be relied on to place package content
    # — it happens after the package phase, with no build sources — so
    # anything a fresh install must have still belongs in DESTDIR. So /etc/
    # ld.so.conf and /etc/nsswitch.conf were ghost-only state on every
    # fresh install — ldconfig hooks ran with no config file (using
    # the compiled-in default cache locations), and getent looked at
    # a missing nsswitch.conf (falling back to a minimal compiled-in
    # policy that omits systemd integration). Surfaced via the 2026-05-26
    # Class 11 mechanical chroot-gap scan. Content here is identical
    # to what the retired post_install writes produced (hook-contract
    # wave 2026-07-30: the hook writers are gone — these shipped copies
    # are the single source; the hosts line now carries the mdns entry
    # formerly sed-appended by nss-mdns's retired cross-package hook).
    # Config-protect governs live-system overrides on upgrade.
    install -dm755 "${DESTDIR}/etc"
    cat > "${DESTDIR}/etc/ld.so.conf" << "EOF"
# Begin /etc/ld.so.conf
/usr/local/lib
/usr/lib64
/opt/lib

# Add an include directory
include /etc/ld.so.conf.d/*.conf

EOF
    install -dm755 "${DESTDIR}/etc/ld.so.conf.d"

    cat > "${DESTDIR}/etc/nsswitch.conf" << "EOF"
# Begin /etc/nsswitch.conf

passwd: files systemd
group: files systemd
shadow: files systemd

hosts: mymachines resolve [!UNAVAIL=return] files myhostname dns mdns4_minimal [NOTFOUND=return]
networks: files

protocols: files
services: files
ethers: files
rpc: files

# End /etc/nsswitch.conf
EOF

    # Note: d9911088's /etc/{passwd,group,shadow,gshadow} + /root content
    # was reverted in plan v2 (2026-05-27, bilateral review APPROVE
    # at 06:41Z). Class 11 chroot-state-not-packaged baseline content
    # moved to packages/core/intergenos-base-files/ (cross-distro
    # convention: Fedora setup, Debian base-files+base-passwd, Arch
    # filesystem, Void base-files). Research dossier at private repo
    # 06c930e. glibc-core retains /etc/{nsswitch,ld.so}.conf above —
    # those are glibc-consumer-config that semantically belong here.

    # GBC002.4 (2026-06-08): stage the IANA zoneinfo DB into the PACKAGE
    # (DESTDIR) so it reaches Forge package-installs. Previously zic ran ONLY in
    # post_install() against the absolute /usr/share/zoneinfo (the build chroot /
    # live ISO), so the glibc-core archive shipped NO zoneinfo — every Forge
    # install got an EMPTY /usr/share/zoneinfo -> gnome-control-center Date&Time
    # crashed (missing zone.tab, cc-tz-dialog.c:139 assertion) and TZ was stuck
    # at UTC. ld.so.conf + nsswitch.conf above were already staged into DESTDIR
    # for the same reason; zoneinfo was the one that got missed. Mirror the
    # post_install zic invocation here, DESTDIR-prefixed. (Verified on the GBC002
    # A12 install: /usr/share/zoneinfo had 0 entries while the squashfs had 67.)
    tar -xf "${IGOS_SOURCES}/tzdata2025c.tar.gz" -C /tmp
    _ZI="${DESTDIR}/usr/share/zoneinfo"
    mkdir -pv "${_ZI}"/{posix,right}
    for tz in etcetera southamerica northamerica europe africa antarctica \
              asia australasia backward; do
        zic -L /dev/null        -d "${_ZI}"       "/tmp/${tz}"
        zic -L /dev/null        -d "${_ZI}/posix" "/tmp/${tz}"
        zic -L /tmp/leapseconds -d "${_ZI}/right" "/tmp/${tz}"
    done
    cp -v /tmp/zone.tab /tmp/zone1970.tab /tmp/iso3166.tab "${_ZI}"
    zic -d "${_ZI}" -p America/New_York
    unset _ZI

}

# Post-install: runs on the live system AFTER deploy
post_install() {
    set -e
    # Create essential locales
    localedef -i C -f UTF-8 C.UTF-8
    localedef -i en_US -f ISO-8859-1 en_US
    localedef -i en_US -f UTF-8 en_US.UTF-8

    # nsswitch.conf + ld.so.conf ship as owned payload from do_install
    # (hook-contract wave); the mdns hosts entry formerly sed-appended by
    # nss-mdns's retired hook is baked into the shipped file.

    # Timezone data — BUILD CONTEXT ONLY.
    #
    # ${IGOS_SOURCES} names the build chroot's source tree; the chroot build
    # drivers export it and nothing on an installed target does. This function
    # runs in both places (see the do_install note above), and on a target the
    # unguarded extraction read /tzdata2025c.tar.gz — measured on a fresh
    # install as rc=2, "tar: /tzdata2025c.tar.gz: Cannot open", after which
    # `set -e` abandoned every remaining step in this function.
    #
    # There is nothing for this block to do on a target: do_install stages the
    # whole zoneinfo tree into the package payload (1,795 files measured on an
    # installed system), so the target already has what these zic runs would
    # produce. A build without the tarball is a different matter and stays
    # FATAL — that is the GBC002.4 empty-/usr/share/zoneinfo regression, and a
    # silent skip there would ship it again.
    #
    # Context is read off the sources DIRECTORY, not just the variable: an
    # IGOS_SOURCES that survives into a target environment still resolves to
    # no directory there, so the target still takes the skip.
    if [ -n "${IGOS_SOURCES:-}" ] && [ -d "${IGOS_SOURCES}" ]; then
        if [ ! -f "${IGOS_SOURCES}/tzdata2025c.tar.gz" ]; then
            echo "FATAL: ${IGOS_SOURCES}/tzdata2025c.tar.gz is absent — refusing to build glibc-core without the timezone database" >&2
            return 1
        fi
        tar -xf "${IGOS_SOURCES}/tzdata2025c.tar.gz" -C /tmp

        ZONEINFO=/usr/share/zoneinfo
        mkdir -pv $ZONEINFO/{posix,right}

        for tz in etcetera southamerica northamerica europe africa antarctica \
                  asia australasia backward; do
            zic -L /dev/null   -d $ZONEINFO       /tmp/${tz}
            zic -L /dev/null   -d $ZONEINFO/posix /tmp/${tz}
            zic -L /tmp/leapseconds -d $ZONEINFO/right /tmp/${tz}
        done

        cp -v /tmp/zone.tab /tmp/zone1970.tab /tmp/iso3166.tab $ZONEINFO
        zic -d $ZONEINFO -p America/New_York
        unset ZONEINFO
    else
        echo "glibc-core post_install: no build source tree present — the zoneinfo database ships in the package payload; skipping timezone-database generation and continuing" >&2
    fi

    # /etc/localtime is DELIBERATELY not written here (r4). This hook re-runs
    # on every fresh install at Forge's hooks phase — AFTER the config phase
    # has applied the user's chosen timezone — and no user signal reaches this
    # chroot (no TZ, no /etc/timezone), so the old TZ-probe here resolved UTC
    # and silently clobbered every install's selection (proven on a live
    # install trace, 2026-07-30). The timezone symlink has exactly one owner:
    # the installer's config phase (installer/backend/config.py set_timezone).
    # The build chroot gets its (UTC) link from phase config, not from here.

    # Rebuild ld cache
    ldconfig
}
