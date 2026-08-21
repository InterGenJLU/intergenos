#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Systemd 259.1
# LFS 13.0 Section 8.78
#
# Complex meson build. DESTDIR supported.
# Post-install: machine-id-setup, preset-all.

configure() {
    set -e
    # Fix udev rules: render -> video group, remove sgx
    sed -e 's/GROUP="render"/GROUP="video"/' \
        -e 's/GROUP="sgx", //'               \
        -i rules.d/50-udev-default.rules.in

    mkdir -p build
    cd       build

    # `bootloader=enabled` is explicit on purpose. The default `auto` resolves
    # via meson's feature.require() against pyelftools-found + EFI-enabled +
    # x86_64-EFI-arch (per src/systemd/meson.build:1925-1928). When pyelftools
    # is missing, `auto` SILENTLY disables the bootloader and linuxx64.efi.stub
    # never gets built — surfacing later as opaque "STUB not found" from
    # scripts/build-uki.sh. Forcing `enabled` flips silent-disable to loud-
    # error per the security-only alignment (explicit > implicit) + the
    # user-control principle (don't hide things). pyelftools is now in our
    # host-deps to satisfy the require
    # condition under all build environments.
    #
    # `elfutils=enabled` is explicit for the same reason as `bootloader`.
    # meson.build resolves libdw and libelf with
    # `required : get_option('elfutils')` and sets HAVE_ELFUTILS only when
    # both are found; at the `auto` default a chroot without elfutils built
    # yet produces a systemd-coredump with no ELF symbolization and says
    # nothing at build time. That is what shipped in the first release —
    # the installed system reports "elfutils disabled" and its one coredump
    # could not be parsed. `enabled` turns that silent downgrade into a
    # configure-time failure; elfutils is declared in dependencies.build to
    # match, and builds ahead of systemd in the Chapter 8 order.
    #
    # `sysusers=true` overrides the LFS 13.0 recipe default of false. LFS
    # disables sysusers because LFS users are created manually in Chapter 7;
    # InterGenOS ships /usr/lib/sysusers.d/<pkg>.conf files per the
    # Arch+Fedora declarative pattern, so the systemd-sysusers binary +
    # systemd-sysusers.service boot-time unit are required for the
    # mechanism to function on the installed system. Security-aligned.
    meson setup ..                \
        --prefix=/usr             \
        --libdir=/usr/lib         \
        --buildtype=release       \
        -D default-dnssec=no      \
        -D firstboot=false        \
        -D install-tests=false    \
        -D ldconfig=false         \
        -D sysusers=true          \
        -D rpmmacrosdir=no        \
        -D homed=disabled         \
        -D remote=disabled        \
        -D microhttpd=disabled    \
        -D man=disabled           \
        -D mode=release           \
        -D pamconfdir=no          \
        -D dev-kvm-mode=0660      \
        -D nobody-group=nogroup   \
        -D sysupdate=disabled     \
        -D ukify=disabled         \
        -D bootloader=enabled     \
        -D elfutils=enabled       \
        -D sbat-distro=intergenos \
        -D sbat-distro-summary="InterGenOS" \
        -D sbat-distro-pkgname=systemd \
        -D sbat-distro-version=259.1-1 \
        -D sbat-distro-generation=1 \
        -D sbat-distro-url=https://github.com/InterGenJLU/intergenos \
        -D docdir=/usr/share/doc/systemd-259.1
}

build() {
    set -e
    cd build
    ninja -j${IGOS_JOBS}
}

check() {
    set -e
    cd build
    # The tests read /etc/os-release, so one has to exist. Corrected
    # 2026-08-21: this used to write the file unconditionally, which
    # overwrote the identity stub the Chapter 8 driver stages before this
    # package builds (chroot-build-ch8.sh, "Minimal OS identity stub") and
    # dropped the ID= line that stub exists to provide. systemd's own configure
    # reads $ID from that file, and every Chapter 8 package built after this
    # one saw a file with nothing but a NAME. The stub is also guarded by a
    # file-exists test, so it would not restore what this phase removed; only
    # Chapter 9 rewrote it, much later. Write a fallback only when the file is
    # genuinely absent, and otherwise leave whatever the driver staged alone.
    if [ ! -f /etc/os-release ]; then
        printf 'NAME="InterGenOS"\nID=intergenos\n' > /etc/os-release
    fi
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        unshare -m ninja test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Install man pages from separate tarball
    mkdir -pv "${DESTDIR}/usr/share/man"
    tar -xf ${IGOS_SOURCES}/systemd-man-pages-259.1.tar.xz \
        --no-same-owner --strip-components=1                \
        -C "${DESTDIR}/usr/share/man"

    # GBC001.4-rebuild — TPM-absent boot stall fix (14s → instant).
    # Upstream tpm2.target carries `After=dev-tpmrm0.device dev-tpm0.device`
    # AND `Wants=dev-tpmrm0.device dev-tpm0.device`, making it a hard boot
    # synchronization point on the first TPM device. On any machine with no
    # TPM (every VM) or a failing TPM driver (e.g. HP `tpm_crb ... -EBUSY`
    # on the reference laptop), those device units never appear, so systemd
    # waits the full DefaultDeviceTimeoutSec before tpm2.target activates —
    # and it gates sysinit.target, stalling the entire boot. Abandonment-
    # class delay.
    #
    # IMPORTANT — why we edit the unit instead of shipping a drop-in:
    # GBC001.4 shipped tpm2.target.d/10-…-no-device-block.conf with empty
    # `After=`/`Wants=` to RESET the upstream lists. systemd documents
    # empty-string as a list reset, BUT on the GBC001.3 bare-metal boot
    # (systemd 259.1) the drop-in loaded (visible in `systemctl cat`) yet
    # `systemctl show tpm2.target -p Wants` STILL listed the devices, and
    # the boot still ate the 14s device-wait. Verified live three ways:
    # (a) /usr/lib drop-in empty-reset → ignored; (b) /etc drop-in empty-
    # reset → ignored; (c) full unit override with the device deps simply
    # ABSENT → Wants/After correctly empty. So the empty-reset is silently
    # a no-op here; the dependency must not be DECLARED in the first place.
    # We build systemd from source, so we strip the two device lines from
    # the installed vendor unit directly — keeps /etc clean (PRIME
    # DIRECTIVE) and reaches Forge-INSTALLED targets via DESTDIR. A present
    # TPM is unaffected: udev still creates /dev/tpm0 and activates
    # dev-tpm0.device on its own, and systemd-pcrphase units (After=
    # tpm2.target) still run — tpm2.target simply no longer BLOCKS on the
    # device that never arrives.
    sed -i \
        -e '/^After=dev-tpmrm0\.device dev-tpm0\.device$/d' \
        -e '/^Wants=dev-tpmrm0\.device dev-tpm0\.device$/d' \
        -e '/^# Make this a synchronization point on the first TPM device found$/d' \
        "${DESTDIR}/usr/lib/systemd/system/tpm2.target"
    # Assert the strip landed (fail loud if upstream reworded the lines).
    if grep -qE '^(After|Wants)=dev-tpm' "${DESTDIR}/usr/lib/systemd/system/tpm2.target"; then
        echo "ERROR: tpm2.target still declares a TPM device dep after strip — upstream reworded; update the sed." >&2
        exit 1
    fi

    # systemd-pcrproduct orders only After=tpm2.target, but its
    # ExecStart (systemd-pcrextend --product-id) REWRITES the NvPCR
    # anchor secret at /var/lib/systemd/nvpcr/ whenever it differs —
    # every boot on a swtpm guest — racing systemd-remount-fs on a
    # single-root layout and failing "Read-only file system" (proven
    # on the ge9b-05 BuildVM guest: pcrextend write at monotonic 2.573
    # vs remount finish 2.659; DFB-09, 2026-07-19). The pcrlock unit
    # family carries After=systemd-remount-fs.service upstream; the
    # nvpcr-writing pcrextend units do not. Ordering-only ADD via
    # drop-in (append semantics are well-defined — the earlier
    # tpm2.target drop-in failure was empty-list RESET semantics, a
    # different mechanism); no cycle (remount-fs is likewise
    # DefaultDependencies=no + Before=sysinit.target). Siblings
    # pcrmachine/pcrnvdone/pcrfs-root share the anchor mechanism but
    # have not failed in evidence — extend only on their own evidence.
    install -dm755 "${DESTDIR}/usr/lib/systemd/system/systemd-pcrproduct.service.d"
    cat > "${DESTDIR}/usr/lib/systemd/system/systemd-pcrproduct.service.d/10-intergenos-after-remount-fs.conf" << "EOF"
# InterGenOS: the NvPCR anchor-secret write under /var/lib/systemd/nvpcr
# needs the root remount to rw first; upstream orders only after
# tpm2.target and loses that race on single-root layouts.
[Unit]
After=systemd-remount-fs.service
EOF

    # Drop-in 2: cap the now-backgrounded device wait at 15s instead of
    # the 90s default, so the orphaned dev-tpm*.device jobs settle quickly.
    install -dm755 "${DESTDIR}/usr/lib/systemd/system.conf.d"
    cat > "${DESTDIR}/usr/lib/systemd/system.conf.d/10-intergenos-device-timeout.conf" << "EOF"
# InterGenOS GBC001.2: shorten the per-device activation timeout from the
# 90s default to 15s. With tpm2.target no longer gating boot (see
# tpm2.target.d/10-intergenos-no-device-block.conf) this only affects how
# long backgrounded device jobs linger before timing out.
[Manager]
DefaultDeviceTimeoutSec=15s
EOF
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    set -e
    # Machine-id is generated at first boot by systemd's first-boot
    # path (systemd-tmpfiles + systemd-machine-id-commit.service).
    # Running `systemd-machine-id-setup` here would bake a single
    # machine-id into the squashfs — every install would share the
    # same identity-bearing value (used by dbus, journal,
    # systemd-resolved, network leases). The historical comment on
    # this line read "unique per machine, never bake into a package"
    # — correct intent but the code ran at build-chroot time and
    # contradicted the intent. Removed during the 2026-05-18
    # cross-pattern post_install sweep alongside the D-007
    # SSH/credentials cluster.

    # Apply the preset policy — ON A FIRST INSTALL ONLY.
    #
    # This function runs in four places. Three of them have no user choices to
    # lose: the build chroot, the image chroot, and the target the installer is
    # populating. The fourth does. pkm fires a package's sealed post_install on
    # every install AND every upgrade — it has one archive-lifecycle call site
    # and it names post_install — so an unconditional preset-all here re-applied
    # the whole default policy over a running machine whenever this package was
    # upgraded, turning off every service its owner had turned on, remote login
    # among them, and writing nothing anywhere to say it had happened.
    #
    # The installer still applies the policy authoritatively to a freshly
    # installed target at the end of its own run. This hook is not that pass and
    # must not behave as if it were.
    #
    # THE PREDICATE IS SYSTEMD'S OWN, not a heuristic invented here.
    # /etc/machine-id is what systemd itself uses to decide whether a boot is
    # the first one, and the rules are in machine-id(5) under FIRST BOOT
    # SEMANTICS: the file absent means first boot; the literal string
    # "uninitialized" means first boot; an EMPTY file means the boot is NOT a
    # first boot; a valid id means not a first boot. Reusing that definition is
    # what makes this branch and ConditionFirstBoot= agree by construction. The
    # empty case is the one that is easy to get backwards, and getting it
    # backwards would preset a live system every time systemd had not yet
    # committed the id back to disk.
    #
    # No package ships /etc/machine-id as payload, so installing packages never
    # creates the marker: the image build writes "uninitialized" into the
    # chroot, and the installer writes a real id in its config phase, which runs
    # AFTER the package phase this hook fires in. Both of those are first
    # installs and both still get the full pass.
    #
    # The root comes from PKM_PACKAGE_ROOT because pkm runs lifecycle hooks on
    # the HOST and never under chroot, passing the target root in that variable.
    # An unrooted systemctl in this hook therefore acted on whatever system the
    # installer was running from rather than the target being installed into. A
    # predicate about one root is meaningless unless the action targets the same
    # root, so both the read and the write are scoped to it. When the root is
    # "/" the unrooted form is kept, which is what the build chroot has always
    # run — same resolution, and same scoping rule, as this tree's other
    # root-aware hook in the kernel recipe.
    local root="${PKM_PACKAGE_ROOT:-/}"
    local marker="${root%/}/etc/machine-id"
    local first_install=0 why=""
    if [ ! -e "$marker" ]; then
        first_install=1
        why="$marker does not exist"
    elif [ "$(cat "$marker" 2>/dev/null)" = "uninitialized" ]; then
        first_install=1
        why="$marker reads uninitialized"
    elif [ -s "$marker" ]; then
        why="$marker holds a machine id, so that system has already booted"
    else
        why="$marker exists but is empty, which machine-id(5) defines as not a first boot"
    fi

    if [ "$first_install" -eq 1 ]; then
        echo "systemd: applying the service preset policy to ${root} — $why, so no service enablement there was chosen by anyone yet"
        if [ "${root%/}" = "" ]; then
            systemctl preset-all
        else
            systemctl --root "${root}" preset-all
        fi
    else
        echo "systemd: leaving service enablement on ${root} unchanged — $why, and the services enabled on a system that has booted are the ones its owner chose; the preset policy is applied when a system is installed, not when a package on it is upgraded"
    fi
}
