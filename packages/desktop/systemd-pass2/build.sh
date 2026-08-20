#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Systemd 259.1 — Pass 2 rebuild with PAM support
# BLFS 13.0
#
# LFS builds systemd without PAM (Chapter 8). After linux-pam is
# installed, systemd must be rebuilt with PAM support so that:
#   - pam_systemd.so is built and installed
#   - systemd-logind can create proper user sessions
#   - GDM and GNOME can register display sessions
#
# Without this rebuild, GNOME desktop login fails because
# systemd --user cannot start (no XDG_RUNTIME_DIR created).
#
# `sysusers=true` overrides the LFS recipe default of false (see
# packages/core/systemd/build.sh header for the same divergence). LFS
# disables sysusers because LFS users are created manually in Chapter
# 7; InterGenOS ships /usr/lib/sysusers.d/<pkg>.conf files per the
# Arch+Fedora declarative pattern, so the systemd-sysusers binary +
# systemd-sysusers.service boot-time unit are required for the
# mechanism to function on the installed system.

# `elfutils=enabled` (added 2026-08-19). meson.build resolves libdw and
# libelf with `required : get_option('elfutils')` and sets HAVE_ELFUTILS only
# when both are found; at the `auto` default the lookup fails quietly and
# systemd-coredump is built with no ELF symbolization. That is what the first
# release shipped — this package supersedes core/systemd, so its build is the
# one on installed systems, and the post-install evaluation found
# systemd-coredump reporting "elfutils disabled", which also meant the single
# coredump that install produced could not be parsed. Explicit `enabled`
# turns the silent downgrade into a configure-time failure. elfutils builds
# in Chapter 8, well ahead of this tier, and is declared in both dependency
# lists to match.

configure() {
    set -e
    # Same sed fix as pass 1
    sed -e 's/GROUP="render"/GROUP="video"/' \
        -e 's/GROUP="sgx", //'               \
        -i rules.d/50-udev-default.rules.in

    mkdir -p build
    cd       build

    meson setup ..                          \
        --prefix=/usr                       \
        --libdir=/usr/lib                   \
        --buildtype=release                 \
        -D default-dnssec=allow-downgrade   \
        -D firstboot=false                  \
        -D install-tests=false              \
        -D ldconfig=false                   \
        -D sysusers=true                    \
        -D rpmmacrosdir=/usr/lib/rpm/macros.d \
        -D homed=enabled                    \
        -D remote=disabled                  \
        -D microhttpd=disabled              \
        -D man=enabled                      \
        -D mode=release                     \
        -D pam=enabled                      \
        -D pamconfdir=/etc/pam.d            \
        -D dev-kvm-mode=0660                \
        -D nobody-group=nogroup             \
        -D sysupdate=enabled                \
        -D ukify=enabled                    \
        -D apparmor=enabled                 \
        -D tpm2=enabled                     \
        -D libfido2=enabled                 \
        -D xkbcommon=enabled                \
        -D seccomp=enabled                  \
        -D libcryptsetup=enabled            \
        -D idn=true                         \
        -D qrencode=enabled                 \
        -D gcrypt=enabled                   \
        -D gnutls=enabled                   \
        -D libarchive=enabled               \
        -D libcurl=enabled                  \
        -D bashcompletiondir=/usr/share/bash-completion/completions \
        -D bootloader=enabled               \
        -D elfutils=enabled                 \
        -D sbat-distro=intergenos           \
        -D sbat-distro-summary="InterGenOS" \
        -D sbat-distro-pkgname=systemd      \
        -D sbat-distro-version=259.1-1      \
        -D sbat-distro-generation=1         \
        -D sbat-distro-url=https://github.com/InterGenJLU/intergenos \
        -D docdir=/usr/share/doc/systemd-259.1
}

build() {
    set -e
    cd build
    ninja -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build
    # Direct install — overwrites pass 1 systemd with PAM-enabled version
    ninja install

    # TPM-absent boot-stall strip — MUST be repeated here. pass2 ninja-installs
    # the full vendor unit set and OVERWRITES the core-systemd tpm2.target that
    # packages/core/systemd/build.sh strips. Without this the pass2 (unstripped)
    # unit wins on the installed system and the ~15s dev-tpm device wait returns
    # (confirmed on the GBC002.5 A12 install: tpm2.target @15.6s, "Timed out
    # waiting for device /dev/tpm0/tpmrm0"). Same sed + fail-loud assert as
    # core systemd; here the install is direct (to /) so no DESTDIR prefix.
    sed -i \
        -e '/^After=dev-tpmrm0\.device dev-tpm0\.device$/d' \
        -e '/^Wants=dev-tpmrm0\.device dev-tpm0\.device$/d' \
        -e '/^# Make this a synchronization point on the first TPM device found$/d' \
        /usr/lib/systemd/system/tpm2.target
    if grep -qE '^(After|Wants)=dev-tpm' /usr/lib/systemd/system/tpm2.target; then
        echo "ERROR: tpm2.target still declares a TPM device dep after strip (pass2) — upstream reworded; update the sed." >&2
        exit 1
    fi

    # Same pcrproduct remount-fs ordering drop-in as core systemd —
    # keep both recipes in lockstep (the pass2 install re-ships the
    # vendor unit set; full rationale in packages/core/systemd/build.sh;
    # DFB-09, 2026-07-19). Direct install (no DESTDIR prefix here).
    install -dm755 /usr/lib/systemd/system/systemd-pcrproduct.service.d
    cat > /usr/lib/systemd/system/systemd-pcrproduct.service.d/10-intergenos-after-remount-fs.conf << "EOF"
# InterGenOS: the NvPCR anchor-secret write under /var/lib/systemd/nvpcr
# needs the root remount to rw first; upstream orders only after
# tpm2.target and loses that race on single-root layouts.
[Unit]
After=systemd-remount-fs.service
EOF
}

post_install() {
    set -e
    # Verify pam_systemd.so was installed
    if [ -f /usr/lib/security/pam_systemd.so ]; then
        echo "  pam_systemd.so installed successfully"
    else
        echo "  ERROR: pam_systemd.so not found after rebuild!"
        return 1
    fi

    # Re-execute the running system manager so it picks up the freshly
    # installed binaries.
    #
    # Narrowed, not masked. daemon-reexec acts on a RUNNING manager, and no
    # manager owns the build chroot's root or the installer's target-chroot
    # root: both mount /run as a fresh tmpfs, so /run/systemd/system is absent
    # there (measured 2026-08-19 inside a chroot built from this systemd:
    # absent). On such a root the operation is impossible rather than failed,
    # so it is skipped. On a live root it is possible, so a failure is a real
    # failure and must reach the caller. pkm ships a canonical owner for
    # daemon-reload but none for daemon-reexec, so this recipe is the only
    # caller and a mask here would leave nobody reporting.
    if [ -d /run/systemd/system ]; then
        systemctl daemon-reexec
    fi
}
