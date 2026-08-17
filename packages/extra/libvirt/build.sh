#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libvirt 12.5.0 — virtualization management daemon (QEMU/KVM)
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# The management layer over qemu/KVM: libvirtd + virsh + the qemu,
# network (dnsmasq NAT), interface, storage, secrets, and remote
# drivers. Every feature enable below has its provider in-tree; every
# disable is a deliberate scope decision, recorded:
# - Remote-hypervisor drivers (esx/hyperv/vbox/vmware/vz/openvz/bhyve/
#   libxl/lxc/ch) OFF: v1 hosts VMs with qemu/KVM; no consumer manages
#   foreign hypervisors.
# - storage iscsi/iscsi_direct/gluster/rbd/zfs/vstorage OFF: their
#   client stacks are not shipped (libiscsi et al. — no consumer).
# - libssh2 ON, libssh OFF: the shipped libssh2 satisfies the remote
#   transport; qemu's ssh block driver (which would need libssh) is
#   disabled there for the same no-consumer reason, recorded in the
#   qemu recipe.
# - audit OFF: the OS ships no audit subsystem (build matches the OS).
# - selinux/apparmor secdrivers OFF in v1: no LSM policy set is
#   shipped for libvirt yet; revisit with the apparmor-profiles arc.
# - firewall backend = nftables ONLY (the OS firewall is nftables;
#   never fall back to iptables silently).
# - docs ON: the docs subtree is what generates AND installs
#   /usr/share/libvirt/api/libvirt-api.xml (meson.build:2145 gates
#   subdir('docs') on the option) — libvirt-python's binding
#   generator hard-requires that XML, so docs are load-bearing, not
#   cosmetic. The toolchain is already shipped: rst2html5 (docutils,
#   core tier) + xsltproc (libxslt). The earlier "no rst toolchain
#   shipped" rationale was wrong — that was sphinx-class reasoning;
#   libvirt uses docutils.
# - tests OFF: test binaries have no in-image consumer.
#
# QEMU processes run as the dedicated qemu:qemu system user, created
# at boot via the shipped sysusers.d fragment (systemd-sysusers).
# libvirtd and its sockets ship DISABLED (the 99-default-disable
# preset catch-all): a privileged management daemon is armed by the
# user, one systemctl away.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    meson setup build \
        --prefix=/usr \
        --libdir=/usr/lib \
        --localstatedir=/var \
        --sysconfdir=/etc \
        --buildtype=release \
        -Dinit_script=systemd \
        -Ddriver_qemu=enabled \
        -Ddriver_network=enabled \
        -Ddriver_interface=enabled \
        -Ddriver_secrets=enabled \
        -Ddriver_remote=enabled \
        -Ddriver_esx=disabled \
        -Ddriver_hyperv=disabled \
        -Ddriver_vbox=disabled \
        -Ddriver_vmware=disabled \
        -Ddriver_vz=disabled \
        -Ddriver_openvz=disabled \
        -Ddriver_bhyve=disabled \
        -Ddriver_libxl=disabled \
        -Ddriver_lxc=disabled \
        -Ddriver_ch=disabled \
        -Dstorage_dir=enabled \
        -Dstorage_fs=enabled \
        -Dstorage_lvm=enabled \
        -Dstorage_disk=enabled \
        -Dstorage_scsi=enabled \
        -Dstorage_mpath=enabled \
        -Dstorage_iscsi=disabled \
        -Dstorage_iscsi_direct=disabled \
        -Dstorage_gluster=disabled \
        -Dstorage_rbd=disabled \
        -Dstorage_zfs=disabled \
        -Dstorage_vstorage=disabled \
        -Dcapng=enabled \
        -Dcurl=enabled \
        -Dlibnl=enabled \
        -Dlibpcap=enabled \
        -Dnumactl=enabled \
        -Dpolkit=enabled \
        -Dudev=enabled \
        -Dreadline=enabled \
        -Dsasl=enabled \
        -Dfuse=enabled \
        -Djson_c=enabled \
        -Dattr=enabled \
        -Dblkid=enabled \
        -Dlibssh2=enabled \
        -Dlibssh=disabled \
        -Daudit=disabled \
        -Dselinux=disabled \
        -Dsecdriver_selinux=disabled \
        -Dapparmor=disabled \
        -Dsecdriver_apparmor=disabled \
        -Dapparmor_profiles=disabled \
        -Dglusterfs=disabled \
        -Dlibiscsi=disabled \
        -Dwireshark_dissector=disabled \
        -Dsanlock=disabled \
        -Dopenwsman=disabled \
        -Dnumad=disabled \
        -Ddtrace=disabled \
        -Dfirewall_backend_priority=nftables \
        -Dqemu_user=qemu \
        -Dqemu_group=qemu \
        -Ddocs=enabled \
        -Dtests=disabled \
        -Dnls=enabled
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install

    # qemu system user/group, created at boot by systemd-sysusers.
    install -Dm644 "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/libvirt-qemu.sysusers" \
        "$DESTDIR/usr/lib/sysusers.d/libvirt-qemu.conf"

    # Opt-in NAT-guest firewall fragment — shipped INACTIVE outside the
    # /etc/nftables.d include glob; the user arms it (see file header).
    install -Dm644 "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/libvirt-nat-firewall.nft" \
        "$DESTDIR/usr/share/libvirt/nftables/50-libvirt-nat.conf"

    # Runtime dirs are tmpfiles-owned; drop any build litter under
    # /var/run so the package never ships transient state.
    rm -rf "$DESTDIR/var/run"
}
