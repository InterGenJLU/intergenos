#!/bin/bash
# AppArmor v3.1.7 — libapparmor + parser + profile install
#
# Three upstream components compiled and installed:
#   1. libraries/libapparmor — autotools library, produces libapparmor.so
#      (consumed by systemd, polkit, dbus and others via -lapparmor and the
#      libapparmor.pc pkg-config file). systemd-pass2's audit-fix declared
#      apparmor as a build-dep expecting libapparmor.so to be available;
#      Build #6 Halt #8 surfaced that the previous stub build.sh never
#      compiled it.
#   2. parser/ — Makefile-driven, produces apparmor_parser binary plus
#      parser.conf, profile-load helper, rc.apparmor.functions, systemd unit.
#   3. profiles/ — Makefile-driven, installs upstream profile substrate to
#      /etc/apparmor.d/ (abi/, abstractions/, tunables/ + top-level profiles).
#
# Plus three InterGenOS-specific install steps:
#   4. apparmor-profiles-extra_1.35 (Debian-derived: irssi, totem, pidgin,
#      etc.) extracted from the secondary tarball declared in package.yml.
#      The orchestrator only auto-extracts source[0]; we extract source[1]
#      ourselves in build().
#   5. Custom IGOS profiles (intergen-mcp, pkm, forge, first-boot-greeter)
#      shipped in $PKG_DIR/profiles/ alongside this script.
#   6. Complain-mode marker file used by the first-boot orchestrator to
#      mass-apply complain mode after the kernel boots with apparmor LSM.
#      Per Prime Directive: graceful rollout, log-only by default until
#      profiles graduate to enforce per-profile in future releases.

PKG_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # Generate libapparmor's autotools machinery, then configure with
    # InterGenOS layout. perl/python bindings default to off (configure.ac
    # uses --with-perl/--with-python opt-in); leaving them off keeps the
    # chroot build slim and avoids pulling Perl/Python build deps into
    # libapparmor's runtime closure.
    cd libraries/libapparmor
    sh autogen.sh
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --localstatedir=/var \
                --with-gnu-ld
    cd "${OLDPWD}"
}

build() {
    set -e
    # 1. libapparmor — autotools, parallel-safe.
    make -C libraries/libapparmor

    # 2. parser — Makefile, depends on libapparmor's in-tree build artifacts.
    #    The parser Makefile picks up libraries/libapparmor/include and
    #    libraries/libapparmor/src/.libs automatically via relative paths.
    make -C parser

    # 4. Extract apparmor-profiles-extra_1.35 secondary tarball into a known
    #    in-tree directory for do_install() to consume. Tarball layout has
    #    a 'work/' top-level (Debian source-package convention) — no version
    #    dir to strip.
    rm -rf profiles-extra
    mkdir -p profiles-extra
    tar -xJf "${IGOS_SOURCES}/apparmor-profiles-extra_1.35.tar.xz" \
        -C profiles-extra
}

do_install() {
    set -e

    # 1. libapparmor — autotools install with DESTDIR.
    make -C libraries/libapparmor install DESTDIR="${DESTDIR}"

    # 2. parser — explicit path overrides for InterGenOS /usr-merged layout.
    #    Defaults in parser/Makefile place binaries in ${DESTDIR}/sbin and
    #    helper scripts in ${DESTDIR}/lib/apparmor; we override to /usr/sbin
    #    and /usr/lib/apparmor respectively.
    make -C parser install \
        DESTDIR="${DESTDIR}" \
        SBINDIR="${DESTDIR}/usr/sbin" \
        USR_SBINDIR="${DESTDIR}/usr/sbin" \
        APPARMOR_BIN_PREFIX="${DESTDIR}/usr/lib/apparmor" \
        SYSTEMD_UNIT_DIR="${DESTDIR}/usr/lib/systemd/system" \
        CONFDIR=/etc/apparmor \
        INSTALL_CONFDIR="${DESTDIR}/etc/apparmor"

    # 2b. systemd unit + aa-teardown — parser/Makefile's plain `install`
    #     target only runs install-systemd via DISTRO=redhat/suse, neither
    #     of which we set. Run install-systemd explicitly so the apparmor
    #     systemd unit lands in /usr/lib/systemd/system/.
    make -C parser install-systemd \
        DESTDIR="${DESTDIR}" \
        APPARMOR_BIN_PREFIX="${DESTDIR}/usr/lib/apparmor" \
        SYSTEMD_UNIT_DIR="${DESTDIR}/usr/lib/systemd/system" \
        USR_SBINDIR="${DESTDIR}/usr/sbin"

    # 3. profiles — installs upstream substrate to /etc/apparmor.d/ + abi/,
    #    abstractions/, tunables/, plus extra-profiles to
    #    /usr/share/apparmor/extra-profiles/. The 'local' target generates
    #    local/ override stubs from each top-level profile and runs as a
    #    dependency of 'install'.
    make -C profiles install DESTDIR="${DESTDIR}"

    # 4. apparmor-profiles-extra (Debian-derived). Layout:
    #    profiles-extra/work/profiles/<name>     — top-level profile files
    #    profiles-extra/work/profiles/abstractions/* — extra abstractions
    if [ -d profiles-extra/work/profiles ]; then
        # Top-level profile files: install each that doesn't already exist
        # (don't overwrite upstream apparmor 3.1.7 substrate).
        find profiles-extra/work/profiles -maxdepth 1 -type f -print0 \
        | while IFS= read -r -d '' f; do
            target="${DESTDIR}/etc/apparmor.d/$(basename "$f")"
            if [ ! -e "$target" ]; then
                install -m 644 "$f" "$target"
            fi
        done
        # Extra abstractions: same merge policy — never shadow upstream.
        if [ -d profiles-extra/work/profiles/abstractions ]; then
            install -dm 755 "${DESTDIR}/etc/apparmor.d/abstractions/"
            for f in profiles-extra/work/profiles/abstractions/*; do
                [ -f "$f" ] || continue
                target="${DESTDIR}/etc/apparmor.d/abstractions/$(basename "$f")"
                if [ ! -e "$target" ]; then
                    install -m 644 "$f" "$target"
                fi
            done
        fi
    fi

    # 5. InterGenOS-specific custom profiles.
    install -vdm 755 "${DESTDIR}/etc/apparmor.d/disable/"
    install -vdm 755 "${DESTDIR}/etc/apparmor.d/local/"
    install -vm 644 "${PKG_DIR}/profiles/usr.bin.intergen-mcp" \
        "${DESTDIR}/etc/apparmor.d/"
    install -vm 644 "${PKG_DIR}/profiles/usr.bin.pkm" \
        "${DESTDIR}/etc/apparmor.d/"
    install -vm 644 "${PKG_DIR}/profiles/usr.bin.forge" \
        "${DESTDIR}/etc/apparmor.d/"
    install -vm 644 "${PKG_DIR}/profiles/usr.libexec.intergenos.first-boot-greeter" \
        "${DESTDIR}/etc/apparmor.d/"

    # 6. Complain-mode default marker. First-boot orchestrator reads this
    #    after kernel boots with apparmor LSM and runs aa-complain on every
    #    profile in /etc/apparmor.d/. Per Prime Directive: graceful rollout,
    #    profiles graduate to enforce per-profile in future releases.
    install -vdm 755 "${DESTDIR}/usr/share/intergenos-apparmor/"
    echo "complain" > "${DESTDIR}/usr/share/intergenos-apparmor/default_mode"
}
