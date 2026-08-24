#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Linux Kernel 6.18.10 — Pass 2 rebuild with merged config fragments
#
# Pass 1 (core) builds with whatever intergenos.config existed at the time.
# This pass rebuilds with all fragments merged, ensuring USB_STORAGE=y,
# RTW88 bus parents, RTW89, and all overrides are applied.
#
# Note on the previously-removed SKIP-LOGIC: an earlier optimization
# checked a checksum of the config fragments and short-circuited the
# build if unchanged. That collided with the framework's filesystem-
# snapshot-diff manifest tracking — a no-op build means zero new files,
# which the tracker correctly rejects ("No new files detected"). The
# skip-logic also produced installs without manifests, breaking the
# audit trail required by the project's security-only alignment. Removed in favor of always-rebuild
# semantics (16-min cost per build cycle, predictable manifest output).

FRAG_DIR="/mnt/intergenos/config/kernel/fragments"

# Kernel release identity — read from linux-kernel's package.yml (the single
# source of truth; see packages/core/linux-kernel/build.sh). pass2 ships the
# FINAL kernel (supersedes linux-kernel), so it MUST produce the same
# KERNELRELEASE the user sees from pkm — i.e. linux-kernel's release, NOT pass2's
# own package-revision (which is unrelated to the kernel's identity).
_KREL=$(grep '^release:' /mnt/intergenos/packages/core/linux-kernel/package.yml 2>/dev/null | awk '{print $2}' | tr -d '"')
# Fail LOUD on an unparseable release (NOT default-to-1) — a silent mis-stamp of
# the kernel IDENTITY reintroduces the transparency gap the release-stamp closes.
# pass2 ships the FINAL kernel, so a wrong stamp here is what the user actually
# boots. Fail-closed. (WC review of 17875898.)
if [ -z "$_KREL" ]; then
    echo "FATAL: cannot parse 'release:' from packages/core/linux-kernel/package.yml — refusing to mis-stamp the kernel release" >&2
    exit 1
fi
# The version is an identity value too — fail loud on an empty PKG_VERSION rather
# than defaulting to a stale literal (WC symmetry review). pass2 ships the FINAL
# kernel, so a wrong version stamp is what the user boots.
if [ -z "${PKG_VERSION:-}" ]; then
    echo "FATAL: PKG_VERSION is empty — refusing to mis-stamp the kernel version" >&2
    exit 1
fi
KVER="${PKG_VERSION}-igos-${_KREL}"

configure() {
    set -e
    make mrproper

    # Merge ALL config fragments (baseline + overrides)
    cat "$FRAG_DIR"/*.config > .config
    make olddefconfig

    # Release-stamp CONFIG_LOCALVERSION so this (shipped) kernel's KERNELRELEASE
    # is <ver>-igos-<release>, matching linux-kernel + the bootloader phase.
    sed -i "s|^CONFIG_LOCALVERSION=.*|CONFIG_LOCALVERSION=\"-igos-${_KREL}\"|" .config
    make olddefconfig

    # Verify critical options
    echo "  Verify: USB_STORAGE=$(grep CONFIG_USB_STORAGE= .config | head -1)"
    echo "  Verify: RTW88_PCI=$(grep CONFIG_RTW88_PCI .config | head -1)"
    echo "  Verify: RTW88_8821CE=$(grep CONFIG_RTW88_8821CE .config | head -1)"

    # ── Post-merge assertion on the produced config ──────────────────────────
    #
    # Decided 2026-08-11, and THIS pass is where it matters most. Pass 2
    # supersedes pass 1 and, on an installed system, its payload is written
    # LAST: both passes stage the identical /boot/vmlinuz-<KVER> and
    # /usr/lib/modules/<KVER>, and the installer enforces that a package
    # declaring `supersedes:` installs AFTER its predecessor. So the kernel a
    # user actually boots is the one this recipe produced — while every
    # post-merge assertion lived on the pass whose output this one replaces.
    #
    # The three "Verify:" lines above are echoes: they print a value and let the
    # build continue whatever it says. These requirements REFUSE.
    #
    # The requirement lists live in config/kernel/required-security-symbols.txt
    # and config/kernel/required-hardware-symbols.txt and are read by the same
    # gate pass 1 runs, so the two passes cannot drift apart.
    if ! python3 /mnt/intergenos/scripts/check-kernel-required-symbols.py \
            --repo-root /mnt/intergenos \
            --config "${PWD}/.config"; then
        echo "" >&2
        echo "==========================================" >&2
        echo "  FATAL: the produced kernel config does not meet this" >&2
        echo "  distribution's required-symbol set. The unmet requirements are" >&2
        echo "  printed above." >&2
        echo "" >&2
        echo "  This pass ships the kernel the user boots, so a guarantee lost" >&2
        echo "  here is lost on their machine. Refusing to build it." >&2
        echo "" >&2
        echo "  A gate that could not MEASURE also refuses, deliberately: an" >&2
        echo "  instrument that saw nothing must never report nothing wrong." >&2
        echo "==========================================" >&2
        echo "" >&2
        return 1
    fi
}

build() {
    set -e
    make -j${IGOS_JOBS}

    # ── The fabricated-device gate, fired at the ARTIFACT ────────────────────
    #
    # Decided 2026-08-11, and THIS recipe is the load-bearing placement: pass 2
    # supersedes pass 1 and ships the kernel the user actually boots. Pass 1's
    # configure() carries the post-merge assertion loops; this recipe carries
    # none of them, so until now the kernel that ships was never checked for
    # fabricated devices at all — the checks lived on the pass whose output pass
    # 2 replaces.
    #
    # The gate reads the artifact rather than the request: it sweeps the
    # enumerated class against the produced .config AND the built module list,
    # and it sweeps every built module's name for the fabricated-device
    # vocabulary so members nobody enumerated are refused rather than shipped.
    if ! python3 /mnt/intergenos/scripts/check-fabricated-devices.py \
            --repo-root /mnt/intergenos \
            --config "${PWD}/.config" \
            --kernel-source "${PWD}"; then
        echo "" >&2
        echo "==========================================" >&2
        echo "  FATAL: the fabricated-device gate refused this kernel." >&2
        echo "" >&2
        echo "  Its findings are printed above. This pass ships the kernel the" >&2
        echo "  user boots, so a fabricated device reaching here reaches them:" >&2
        echo "  it makes a broken system look healthy to everything that" >&2
        echo "  inspects it, including this repository's own hardware smoke" >&2
        echo "  checks — a fake sound card reports working audio on a machine" >&2
        echo "  whose codec is dead." >&2
        echo "" >&2
        echo "  A gate that could not MEASURE also refuses, deliberately: an" >&2
        echo "  instrument that saw nothing must never report nothing wrong." >&2
        echo "==========================================" >&2
        echo "" >&2
        return 1
    fi
}

do_install() {
    set -e
    # Stage into DESTDIR, exactly as pass 1 does (../linux-kernel/build.sh).
    #
    # This wrote to absolute paths and relied on "the framework's FS-snapshot
    # diff" to derive the manifest from observed new files. No such diff ever
    # ran: that mechanism belongs to igos-build, and this package is built by
    # the bash builder (scripts/chroot-build-core-extra.sh), whose archive is a
    # tar of the DESTDIR staging tree. The kernel therefore landed in the build
    # chroot — and so on the live image via squashfs — while the archive every
    # install is built from carried five entries: the bundled COPYING and its
    # parent directories. Measured on an installed system 2026-07-29: the
    # linux-kernel-pass2 manifest is 496 bytes and the running kernel's image,
    # modules and uapi headers are all owned by pass 1.
    #
    # Every path below is the pass-1 form, which is proven — that staging is
    # what actually delivers the kernel on installed systems today. The two
    # deliberate divergences from pass 1 are kept: the LFS §5.4 header idiom
    # (see below) and the idempotent source-tree extract.
    make INSTALL_MOD_PATH="$DESTDIR" modules_install

    install -vm755 -d "${DESTDIR}/boot"
    cp -v arch/x86/boot/bzImage "${DESTDIR}/boot/vmlinuz-${KVER}"
    # Named from ${KVER}, matching pass 1 and the "both passes stage the
    # identical /boot/vmlinuz-<KVER>" contract stated above — these two lines
    # were the exception to it, carrying a literal version with no release.
    cp -v System.map "${DESTDIR}/boot/System.map-${KVER}"
    cp -v .config "${DESTDIR}/boot/config-${KVER}"

    # Release-stamped LOCALVERSION (-igos-<release>) makes modules install to
    # /lib/modules/<ver>-igos-<release>/ — depmod must match that KERNELRELEASE,
    # and -b scopes it to the staging tree so it indexes the modules being
    # packaged rather than whatever the build chroot happens to have live.
    depmod -b "$DESTDIR" "${KVER}"

    # Sanitized userspace (uapi) headers — same rationale as pass 1
    # (glibc's shipped headers hard-include <linux/*.h>; without these an
    # installed system cannot compile any C).
    # LFS §5.4 idiom (make headers + prune + cp), NOT `make headers_install`:
    # headers_install shells out to rsync, and rsync is tier base — absent in
    # the chroot when this package builds at core-extra on a from-scratch.
    make headers
    find usr/include -type f ! -name '*.h' -delete
    install -v -dm755 "${DESTDIR}/usr"
    cp -r usr/include "${DESTDIR}/usr"

    # Stage kernel source + .config + Module.symvers for reproducibility
    # and out-of-tree module builds (DKMS, NVIDIA, VirtualBox, ZFS).
    # Aligns with PRIME DIRECTIVE: users control their machine — they get
    # the source.
    local pkg_ver="${PKG_VERSION}"   # guaranteed non-empty by the top-level guard
    local src_dst="${DESTDIR}/usr/src/linux-${pkg_ver}"
    install -v -dm755 "${DESTDIR}/usr/src"

    # Idempotent extract: a re-run against an already-populated staging tree
    # skips the untar rather than unpacking over itself.
    if [ ! -d "${src_dst}" ]; then
        tar -xf "${IGOS_SOURCES}/linux-${pkg_ver}.tar.xz" -C "${DESTDIR}/usr/src/"
    fi

    # Always refresh .config + Module.symvers (pass2 may have rebuilt with
    # different fragments than pass1)
    cp .config "${src_dst}/.config"
    [ -f Module.symvers ] && cp Module.symvers "${src_dst}/Module.symvers"
    make -C "${src_dst}" olddefconfig
    make -C "${src_dst}" modules_prepare

    # Replace build/source symlinks with stable /usr/src/ targets. The symlink
    # TARGET is the on-target absolute path, not the staged one — DESTDIR is a
    # packaging prefix and must never appear inside a shipped symlink.
    # NOTE: -n is critical — without it, ln -sf with a LINK_NAME that
    # already exists as a symlink-to-directory (the build symlink
    # auto-emitted by make modules_install pointing at $PWD) creates
    # the new link INSIDE that directory rather than replacing it.
    ln -sfnv "/usr/src/linux-${pkg_ver}" \
             "${DESTDIR}/lib/modules/${KVER}/build"
    ln -sfnv "/usr/src/linux-${pkg_ver}" \
             "${DESTDIR}/lib/modules/${KVER}/source"

    # ── Ship the D-005 UKI rebuild hook (scope item 4, decided 2026-08-11) ──
    #
    # pass 1 ships this hook; this pass did not, and this pass is the one whose
    # kernel bytes pkm deploys LAST on an installed system (supersedes-enforced
    # ordering). Without a hook here, that final deploy is followed by no UKI
    # rebuild at all: the UKI keeps embedding the kernel pass 1 deployed while
    # the modules on disk are this pass's.
    #
    # The hook file is the SAME one pass 1 ships — one script, two registrations
    # — so the two passes cannot drift apart in what they rebuild.
    #
    # Firing both on a dual upgrade is safe and is the intended behaviour: the
    # hook rebuilds the UKI from whatever kernel is staged when it runs, so on a
    # dual upgrade the LAST rebuild wins, and the last one is this pass's,
    # which is also the pass whose bytes are on disk. A rebuild that runs twice
    # costs time, not correctness.
    install -v -dm755 "${DESTDIR}/var/lib/pkm/hooks/linux-kernel-pass2"
    install -vm755 "/mnt/intergenos/packages/core/linux-kernel/hooks/post-install.sh" \
        "${DESTDIR}/var/lib/pkm/hooks/linux-kernel-pass2/post-install"

    # Ship the canonical source tarball for byte-identity verification
    # against upstream + clean-rebuild scenarios
    install -vm644 "${IGOS_SOURCES}/linux-${pkg_ver}.tar.xz" \
        "${DESTDIR}/usr/src/linux-${pkg_ver}.tar.xz"
}
