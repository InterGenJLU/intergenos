#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Linux Kernel 6.18.10
# LFS 13.0 Section 10.3
#
# DESTDIR exception: Kernel uses INSTALL_MOD_PATH and INSTALL_PATH,
# not DESTDIR.
#
# IMPORTANT: Kernel configuration requires a .config file.
# Use 'make menuconfig' or copy a known-good config to .config
# before building. The build system does NOT provide a default
# kernel config — this is deliberate; the kernel config is a
# user-owned decision.

# --- Kernel release identity (single source of truth) ---------------------
# KERNELRELEASE = <version>-igos-<release>, where <release> is read from
# linux-kernel's OWN package.yml. linux-kernel-pass2 + chroot-build-bootloader.sh
# read the same field, so every surface (uname -r, hostnamectl, /lib/modules,
# the UKI name, the GRUB menu) self-reports the release the user sees from pkm /
# the mirror. Reproducibility-safe — deterministic from the release; the clean
# build counter (#1) + the pinned timestamp are deliberately untouched.
# The version is an identity value — fail loud on an empty PKG_VERSION rather
# than defaulting to a stale literal (symmetry review). Every caller that
# sources this recipe exports it first, in build context and on an installed
# target alike; an empty value is a harness bug, not a silent-default case.
# Checked before the release resolution below, which uses it.
if [ -z "${PKG_VERSION:-}" ]; then
    echo "FATAL: PKG_VERSION is empty — refusing to mis-stamp the kernel version" >&2
    exit 1
fi

# This file is sourced in TWO contexts and the release must resolve in both.
# The chroot build drivers source it out of the recipe tree; the installer's
# hooks phase copies that same tree onto the target and sources it from there
# (installer/backend/hooks.py run_post_install_hooks). The read used to name
# an absolute build-tree path, which exists in neither the target's chroot nor
# any machine but the builder — measured on a fresh install as rc=1, "FATAL:
# cannot parse 'release:'", killing the hook at source time before post_install
# was ever called. The refusal was correct; the data source was not.
#
# Primary source is this recipe's OWN package.yml, located relative to the file
# being sourced, so the same read serves both contexts and no absolute build
# path is assumed.
_KERNEL_RECIPE_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
_KREL=$(grep '^release:' "${_KERNEL_RECIPE_DIR}/package.yml" 2>/dev/null | awk '{print $2}' | tr -d '"')

# Fallback for a root that carries the kernel but not the recipe tree: the
# module directory the package staged is named <version>-igos-<release>, so
# the release is recoverable from installed state. EXACTLY ONE match is
# accepted — a second staged tree means an orphaned prior release is present
# (the class preflight-single-kernel.sh exists to catch) and guessing between
# them would mis-stamp the identity just as surely as defaulting to 1.
if [ -z "$_KREL" ]; then
    _kmod_count=0
    _kmod_found=""
    for _kmod_dir in "/usr/lib/modules/${PKG_VERSION}-igos-"*; do
        [ -d "$_kmod_dir" ] || continue
        _kmod_count=$((_kmod_count + 1))
        _kmod_found="$_kmod_dir"
    done
    if [ "$_kmod_count" -eq 1 ]; then
        _KREL="${_kmod_found##*-igos-}"
    elif [ "$_kmod_count" -gt 1 ]; then
        echo "FATAL: ${_kmod_count} staged module trees match /usr/lib/modules/${PKG_VERSION}-igos-* — refusing to guess which release this is" >&2
        exit 1
    fi
    unset _kmod_count _kmod_found _kmod_dir
fi

# Fail LOUD on an unresolvable release — defaulting to 1 would silently mis-stamp
# the kernel IDENTITY (uname -r / UKI name / /lib/modules dir / GRUB entry) and
# reintroduce the exact transparency gap the release-stamp closes. A release is an
# identity value; doctrine is fail-closed when in doubt. (review of 17875898.)
if [ -z "$_KREL" ]; then
    echo "FATAL: cannot resolve the kernel release — no 'release:' in ${_KERNEL_RECIPE_DIR}/package.yml and no staged /usr/lib/modules/${PKG_VERSION}-igos-* tree; refusing to mis-stamp the kernel release" >&2
    exit 1
fi
KVER="${PKG_VERSION}-igos-${_KREL}"

configure() {
    set -e
    make mrproper

    # ── Apply the DECLARED kernel patches (CVE mitigations) ─────────────────
    #
    # Decided 2026-08-11. These used to be applied by globbing a directory inside
    # this recipe, which meant they existed only here — and linux-kernel-pass2,
    # which supersedes this package and whose payload lands LAST on an installed
    # system, applied none of them. The kernel a user booted was unpatched.
    #
    # The patch set is now declared in package.yml, identically in both recipes,
    # and the files live in the canonical build/patches directory the declared
    # mechanism reads. Pass 2 gets them automatically: its bash driver calls
    # apply_package_patches before sourcing its build.sh. THIS pass's driver
    # (chroot-build-ch10.sh) has no declared-patch support at all, so the same
    # declaration is applied here, by the same parser, against the same files.
    # tests/igos_build/test_kernel_patch_lockstep.py fails the suite if the two
    # declarations ever differ.
    #
    # Fail-closed at every step: an unreadable declaration, a missing file, a
    # sha256 that does not match, or a patch that will not apply all refuse.
    local _patch_src="${IGOS_PATCHES:-/sources}"
    local _decl
    _decl=$(python3 /mnt/intergenos/scripts/parse-package-yml-patches.py \
                "${_KERNEL_RECIPE_DIR}/package.yml")
    if [ $? -ne 0 ]; then
        echo "  FATAL: could not read the declared patch set from ${_KERNEL_RECIPE_DIR}/package.yml" >&2
        return 1
    fi
    if [ -n "$_decl" ]; then
        echo "  Applying declared kernel patches from ${_patch_src}..."
        local _pfile _psha _ppath _pactual
        while IFS='|' read -r _pfile _psha; do
            [ -z "$_pfile" ] && continue
            _ppath="${_patch_src}/${_pfile}"
            if [ ! -f "$_ppath" ]; then
                echo "  FATAL: declared patch not found: ${_ppath}" >&2
                echo "  The patch is declared in package.yml but absent where the build" >&2
                echo "  reads patches from. Refusing to build an unpatched kernel." >&2
                return 1
            fi
            _pactual=$(sha256sum "$_ppath" | cut -d' ' -f1)
            if [ -n "$_psha" ] && [ "$_pactual" != "$_psha" ]; then
                echo "  FATAL: declared patch ${_pfile} does not match its recorded sha256" >&2
                echo "         declared ${_psha}" >&2
                echo "         actual   ${_pactual}" >&2
                return 1
            fi
            echo "    ${_pfile}"
            patch -Np1 < "$_ppath" || {
                echo "  FATAL: failed to apply ${_pfile}" >&2
                return 1
            }
        done <<< "$_decl"
    fi

    # Merge kernel config fragments (baseline + overrides)
    # Overrides are concatenated AFTER baseline so they win in olddefconfig
    local config_dir="/mnt/intergenos/config/kernel"
    local frag_dir="$config_dir/fragments"
    if [ -d "$frag_dir" ] && ls "$frag_dir"/*.config >/dev/null 2>&1; then
        echo "  Merging kernel config fragments..."
        cat "$frag_dir"/*.config > .config
        make olddefconfig

        # Stamp the package release into CONFIG_LOCALVERSION so KERNELRELEASE
        # becomes <ver>-igos-<release> (e.g. 6.18.10-igos-2) — the release then
        # shows up in uname -r / hostnamectl / /lib/modules/<rel>/ / the UKI name
        # / the GRUB menu, matching what pkm + the mirror report. Deterministic
        # from the release (reproducibility-safe); $KVER (top of file) is the
        # single source of truth that do_install + depmod also use.
        sed -i "s|^CONFIG_LOCALVERSION=.*|CONFIG_LOCALVERSION=\"-igos-${_KREL}\"|" .config
        make olddefconfig

        # ── Post-merge assertion on the produced config ──────────────────────
        #
        # `cat fragments/*.config | olddefconfig` has NO conflict detection, so a
        # symbol can be requested and silently not appear: a dependency downgrade
        # demotes it (DM_VERITY=y becomes =m because BLK_DEV_DM was =m), or a
        # parent symbol was never requested and olddefconfig discards the children
        # without a word. The measured case in this tree: the baseline fragment
        # asked for THIRTEEN CONFIG_MMC_* host-controller drivers and never asked
        # for the parent `menuconfig MMC`, so the produced kernel had no MMC
        # subsystem at all and nothing failed.
        #
        # The requirements moved out of this recipe and into two data files on
        # 2026-08-11, read by one gate that BOTH kernel passes now run. Pass 2
        # supersedes this pass and its payload is what lands last on an installed
        # system, so every property asserted here has to be asserted there too —
        # and two copies of a 28-symbol and a 44-symbol list in two shell scripts
        # is a drift class this repository has already had to police once with a
        # test. One list, one reader, both passes.
        #
        # This runs in configure(), not build(): the produced config exists here,
        # which is BEFORE the multi-hour compile. A dropped hardware class should
        # cost seconds, not a kernel build.
        if ! python3 /mnt/intergenos/scripts/check-kernel-required-symbols.py \
                --repo-root /mnt/intergenos \
                --config "${PWD}/.config"; then
            echo "" >&2
            echo "==========================================" >&2
            echo "  FATAL: the produced kernel config does not meet this" >&2
            echo "  distribution's required-symbol set. The unmet requirements" >&2
            echo "  are printed above." >&2
            echo "" >&2
            echo "  Refusing to ship a kernel that silently lost a security" >&2
            echo "  guarantee or a hardware class. A virtual machine cannot" >&2
            echo "  exhibit the hardware half of this, which is why it must fail" >&2
            echo "  HERE and not on a user's laptop." >&2
            echo "" >&2
            echo "  A gate that could not MEASURE also refuses, deliberately: an" >&2
            echo "  instrument that saw nothing must never report nothing wrong." >&2
            echo "==========================================" >&2
            echo "" >&2
            return 1
        fi

        # ── Assert the fake-hardware class is ABSENT from the produced config ──
        #
        # Decided 2026-08-07. Omitting a symbol from a fragment does NOT disable
        # it: olddefconfig resolves it from its Kconfig default and from any
        # `imply` pointing at it. SND_SOC_SDW_MOCKUP was stripped from the
        # baseline and STILL reached the produced config at =m and shipped as a
        # built module, because two Kconfigs carry `imply SND_SOC_SDW_MOCKUP`.
        #
        # The generator now writes explicit "is not set" lines, and this gate
        # proves they survived the merge. A fabricated device is a masking
        # primitive: it reads as working hardware to anything inspecting the
        # system, including this repository's own hardware smoke checks.
        #
        # CONFIG_SND_DUMMY is the sharpest case and was decided on its own
        # (2026-08-07): it registers a fake ALSA sound card, and the hardware
        # smoke check tests audio by counting registered cards — so leaving it
        # enabled would make a machine with a dead codec report working audio.
        # CONFIG_SND_SEQ_DUMMY is a different symbol and stays: it provides ALSA
        # sequencer loopback ports, not a card.
        #
        # THIS LIST IS THE COMPLETE CLASS, not a sample. It mirrors
        # TEST_CLASS_EXPLICIT in docs/research/kernel_configs/analyze_convergence.py,
        # which is what the generator writes "is not set" lines for. A gate that
        # covered only some members would let the rest come back through an
        # `imply` with nothing failing — which is the defect this gate exists to
        # stop, so a partial list would be the same mistake in a smaller size.
        # tests/igos_build/test_kernel_fake_hardware_gate.py fails the suite if
        # the two lists ever drift apart.
        local _fake
        for _fake in \
            CONFIG_ATM_DUMMY \
            CONFIG_BLK_DEV_NULL_BLK \
            CONFIG_COMEDI_TEST \
            CONFIG_COMEDI_TESTS \
            CONFIG_COMEDI_TESTS_EXAMPLE \
            CONFIG_COMEDI_TESTS_NI_ROUTES \
            CONFIG_DUMMY_IRQ \
            CONFIG_DVB_DUMMY_FE \
            CONFIG_EFI_TEST \
            CONFIG_GPIO_MOCKUP \
            CONFIG_I2C_SLAVE_TESTUNIT \
            CONFIG_I2C_STUB \
            CONFIG_IEEE802154_FAKELB \
            CONFIG_IIO_SIMPLE_DUMMY \
            CONFIG_MTD_TESTS \
            CONFIG_NFC_VIRTUAL_NCI \
            CONFIG_PCI_ENDPOINT_TEST \
            CONFIG_PCI_EPF_TEST \
            CONFIG_PPS_GENERATOR_DUMMY \
            CONFIG_PTP_1588_CLOCK_MOCK \
            CONFIG_RC_LOOPBACK \
            CONFIG_REGULATOR_VIRTUAL_CONSUMER \
            CONFIG_SCSI_DEBUG \
            CONFIG_SCSI_PROTO_TEST \
            CONFIG_SND_DUMMY \
            CONFIG_SND_PCMTEST \
            CONFIG_SND_SOC_CS_AMP_LIB_TEST \
            CONFIG_SND_SOC_INTEL_AVS_MACH_I2S_TEST \
            CONFIG_SND_SOC_SDW_MOCKUP \
            CONFIG_SPEAKUP_SYNTH_DUMMY \
            CONFIG_SPI_LOOPBACK_TEST \
            CONFIG_STM_DUMMY \
            CONFIG_TEST_POWER \
            CONFIG_THERMAL_CORE_TESTING \
            CONFIG_USB_DUMMY_HCD \
            CONFIG_USB_LINK_LAYER_TEST \
            CONFIG_USB_TEST \
            CONFIG_VDPA_SIM \
            CONFIG_VDPA_SIM_BLOCK \
            CONFIG_VDPA_SIM_NET \
            CONFIG_VME_FAKE; do
            if grep -qE "^${_fake}=(y|m)$" .config; then
                echo "" >&2
                echo "==========================================" >&2
                echo "  FATAL: a fake-hardware driver is ENABLED in the" >&2
                echo "  produced kernel config: ${_fake}" >&2
                echo "" >&2
                echo "  These fabricate devices the machine does not have, which" >&2
                echo "  makes a broken system look healthy to every check that" >&2
                echo "  inspects it. Being absent from the fragment is NOT enough:" >&2
                echo "  an 'imply' or a Kconfig default re-acquires the symbol, so" >&2
                echo "  it must be explicitly 'is not set' in a fragment." >&2
                echo "==========================================" >&2
                echo "" >&2
                return 1
            fi
        done
        echo "  Fake-hardware drivers asserted ABSENT from the produced config (fabricated devices cannot mask a dead one)"
    else
        echo ""
        echo "=========================================="
        echo "  WARNING: No kernel config found."
        echo "  Run 'make menuconfig' to configure."
        echo "=========================================="
        echo ""
        return 1
    fi
}

build() {
    set -e
    make -j${IGOS_JOBS}

    # ── The fabricated-device gate, fired at the ARTIFACT ────────────────────
    #
    # Decided 2026-08-11. configure() already asserts that no member of the
    # enumerated fake-hardware class is enabled in the produced .config. That
    # check is necessary and it is NOT sufficient, and the shortfall was
    # measured rather than predicted: sweeping the BUILT MODULE LIST of a real
    # kernel build on 2026-08-07 found ten further fabricated-device drivers
    # that no config-level check could have seen. A config check can only look
    # for symbol names somebody already wrote down; it cannot see a module whose
    # symbol name differs from its filename (vdpa_sim_blk.ko is built by
    # CONFIG_VDPA_SIM_BLOCK), and it cannot see a module with no Kconfig symbol
    # at all (ddbridge-dummy-fe.ko is built unconditionally as a component of a
    # real DVB card driver).
    #
    # So the class is swept against what was actually COMPILED, here, where the
    # modules exist and before anything is staged or shipped. The sweep was the
    # manual instrument of record until now; a check that depends on someone
    # remembering to run it is not a gate, which is why it refuses the build.
    if ! python3 /mnt/intergenos/scripts/check-fabricated-devices.py \
            --repo-root /mnt/intergenos \
            --config "${PWD}/.config" \
            --kernel-source "${PWD}"; then
        echo "" >&2
        echo "==========================================" >&2
        echo "  FATAL: the fabricated-device gate refused this kernel." >&2
        echo "" >&2
        echo "  Its findings are printed above. A driver that fabricates a" >&2
        echo "  device the machine does not have makes a broken system look" >&2
        echo "  healthy to everything that inspects it, including this" >&2
        echo "  repository's own hardware smoke checks — a fake sound card" >&2
        echo "  reports working audio on a machine whose codec is dead." >&2
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
    # Kernel uses INSTALL_MOD_PATH, not DESTDIR
    make INSTALL_MOD_PATH="$DESTDIR" modules_install

    # Install kernel image, System.map, and config
    install -vm755 -d "${DESTDIR}/boot"
    cp -iv arch/x86/boot/bzImage "${DESTDIR}/boot/vmlinuz-${KVER}"
    cp -iv System.map "${DESTDIR}/boot/System.map-6.18.10"
    cp -iv .config "${DESTDIR}/boot/config-6.18.10"

    # Install kernel documentation
    install -v -dm755 "${DESTDIR}/usr/share/doc/linux-6.18.10"
    cp -r Documentation/* "${DESTDIR}/usr/share/doc/linux-6.18.10"

    # Sanitized userspace (uapi) headers. glibc's SHIPPED headers
    # hard-include <linux/*.h> (bits/errno.h -> linux/errno.h), so an
    # install without /usr/include/linux cannot compile ANY C program —
    # found 2026-07-17 bootstrapping the first InterGenOS build host.
    # Decided 2026-07-17: the kernel package owns the uapi header set on
    # installed systems (the chroot's copy came from the toolchain
    # phases and was never packaged).
    make INSTALL_HDR_PATH="${DESTDIR}/usr" headers_install

    # Stage kernel source + .config + Module.symvers for reproducibility
    # and to keep /lib/modules/<ver>/build as a valid symlink target for
    # manual out-of-tree module builds. Without this staged source tree,
    # /lib/modules/<ver>/build is a dangling symlink to the ephemeral
    # build work-dir and out-of-tree builds cannot resolve kernel headers.
    #
    # Automated rebuild-on-kernel-upgrade (DKMS-style triggers for
    # NVIDIA / VirtualBox / ZFS / other proprietary modules) is NOT
    # wired in this release. Users running manually-built kernel modules
    # must rebuild them against the new kernel after each `pkm upgrade
    # linux-kernel` until automated rebuild support is added. Per Rule 21
    # (no stub claims), this file used to claim "supports DKMS" — the
    # claim was aspirational; the comment now describes what is actually
    # shipped. Closes O-034.
    #
    # Aligns with PRIME DIRECTIVE: users control their machine — they get
    # the source.
    local pkg_ver="${PKG_VERSION}"   # guaranteed non-empty by the top-level guard
    local src_stage="${DESTDIR}/usr/src/linux-${pkg_ver}"
    install -v -dm755 "${DESTDIR}/usr/src"

    # Extract fresh source from canonical tarball (byte-identical to upstream)
    tar -xf "${IGOS_SOURCES}/linux-${pkg_ver}.tar.xz" \
        -C "${DESTDIR}/usr/src/"

    # Copy our build's .config + Module.symvers (so users get the EXACT
    # config + symbol versions matching the running kernel)
    cp .config "${src_stage}/.config"
    [ -f Module.symvers ] && cp Module.symvers "${src_stage}/Module.symvers"

    # Generate auto-config headers + host scripts so source is DKMS-ready
    make -C "${src_stage}" olddefconfig
    make -C "${src_stage}" modules_prepare

    # Replace build/source symlinks (auto-emitted by modules_install
    # pointing at ephemeral $PWD) with stable /usr/src/ targets.
    # NOTE: -n is critical — without it, ln -sf with a LINK_NAME that
    # already exists as a symlink-to-directory creates the new link
    # INSIDE that directory rather than replacing it.
    ln -sfnv "/usr/src/linux-${pkg_ver}" \
             "${DESTDIR}/lib/modules/${KVER}/build"
    ln -sfnv "/usr/src/linux-${pkg_ver}" \
             "${DESTDIR}/lib/modules/${KVER}/source"

    # Ship the canonical source tarball for byte-identity verification
    # against upstream + clean-rebuild scenarios
    install -vm644 "${IGOS_SOURCES}/linux-${pkg_ver}.tar.xz" \
        "${DESTDIR}/usr/src/linux-${pkg_ver}.tar.xz"

    # Ship the D-005 UKI rebuild hook. pkm/installer.py fires
    # /var/lib/pkm/hooks/<pkgname>/post-install after deploy on the
    # target system (Forge install + pkm upgrade alike). The hook
    # rebuilds the UKI from the new kernel + signs it with the user's
    # local MOK per D-005 Option A.
    install -v -dm755 "${DESTDIR}/var/lib/pkm/hooks/linux-kernel"
    install -vm755 "/mnt/intergenos/packages/core/linux-kernel/hooks/post-install.sh" \
        "${DESTDIR}/var/lib/pkm/hooks/linux-kernel/post-install"

    # Ship the four kernel-lifecycle helpers the hook above consumes
    # (keep-N retention prune, microcode-cpio regen, FDE initramfs builder
    # + its runtime entry point). These were previously staged into the
    # chroot by scripts/chroot-build-bootloader.sh only — a chroot lineage
    # that skips phase_bootloader ships without them (ge9b-01 did: no
    # kernel pruning, microcode never early-loaded, FDE installs
    # unbootable). Package ownership + the verify_paths entries in
    # package.yml turn their presence into a checked gate on every image.
    install -v -dm755 "${DESTDIR}/usr/lib/intergen"
    install -vm755 "/mnt/intergenos/scripts/prune-old-kernels.sh" \
        "${DESTDIR}/usr/lib/intergen/prune-old-kernels.sh"
    install -vm755 "/mnt/intergenos/scripts/update-boot-menu.sh" \
        "${DESTDIR}/usr/lib/intergen/update-boot-menu.sh"
    install -vm755 "/mnt/intergenos/scripts/build-microcode-cpio.sh" \
        "${DESTDIR}/usr/lib/intergen/build-microcode-cpio.sh"
    install -vm755 "/mnt/intergenos/installer/init/fde-init.sh" \
        "${DESTDIR}/usr/lib/intergen/fde-init.sh"
    install -vm755 "/mnt/intergenos/installer/init/build-fde-initramfs.sh" \
        "${DESTDIR}/usr/lib/intergen/build-fde-initramfs.sh"
}

# Post-install: runs on the live system AFTER deploy
# NOTE: scripts/chroot-build-ch10.sh invokes this at PACKAGE BUILD time inside
# the build chroot, and the INSTALLER re-runs it on the target at its hooks
# phase (installer/backend/hooks.py run_post_install_hooks sources this recipe
# in a chroot of the target). pkm does not replay it — pkm fires the separate
# user-install-time hook shipped by do_install above to
# /var/lib/pkm/hooks/linux-kernel/post-install. The depmod below is correct in
# both contexts: KVER names the module tree the package staged, and rebuilding
# its dependency table on the target is idempotent.
post_install() {
    set -e
    # Regenerate module dependency files for the package's KERNELRELEASE
    # (matches the modules-dir created by `make modules_install`, i.e.
    # <ver>-igos-<release> per the release-stamped CONFIG_LOCALVERSION).
    #
    # THE RELEASE IS RESOLVED HERE, NOT INHERITED FROM THE RECIPE PREAMBLE.
    # This body runs in three contexts and only two of them have the preamble.
    # igos-build/hookseal.py seals this FUNCTION BODY into the archive as
    # .scripts/post_install.sh, which pkm runs as a standalone script — no
    # preamble, so ${KVER} is empty there and `depmod ""` fails with
    # "depmod: ERROR: Bad version passed", which is what marked the package
    # degraded (measured 2026-08-06).
    #
    # KVER is still honoured when it IS set, so the sourced contexts (the
    # chroot build driver, and the installer's hook phase) keep using the value
    # the recipe already computed. When it is not set the release is read off
    # the module tree the package staged, which is the same recoverable
    # identity the preamble's own fallback uses.
    local root="${PKM_PACKAGE_ROOT:-/}"
    local kver="${KVER:-}"
    if [ -z "$kver" ]; then
        # EXACTLY ONE match is accepted. A second staged tree means an orphaned
        # prior release is present, and guessing between them would rebuild the
        # dependency table for a kernel that is not the one just installed.
        # Same rule, same reason, as the release resolution at the top of this
        # recipe.
        local _found="" _count=0 _d
        for _d in "${root%/}"/usr/lib/modules/*-igos-*; do
            [ -d "$_d" ] || continue
            _count=$((_count + 1))
            _found="${_d##*/}"
        done
        if [ "$_count" -gt 1 ]; then
            echo "FATAL: ${_count} staged module trees under ${root%/}/usr/lib/modules — refusing to guess which release to depmod" >&2
            return 1
        fi
        kver="$_found"
    fi
    if [ -z "$kver" ]; then
        echo "FATAL: cannot resolve the kernel release to depmod — KVER is unset and no ${root%/}/usr/lib/modules/*-igos-* tree is staged. Refusing to run depmod with an empty version." >&2
        return 1
    fi
    # Scope to the package root when there is one; an unscoped depmod on a
    # staged root would rebuild the BUILD HOST's module table instead.
    if [ "${root%/}" = "" ]; then
        depmod "$kver"
    else
        depmod -b "${root%/}" "$kver"
    fi
}
