#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# qemu 11.0.2 — full system emulator and virtualizer (KVM)
# Not in BLFS at this scope — InterGenOS extra tier (virtualization stack)
#
# The host-side hypervisor: full x86_64 system emulation with KVM, plus
# the complete tools set (qemu-img/qemu-nbd close the create-image.sh
# pipeline gap) and the guest agent (InterGenOS runs as guest too — the
# build-VM role). Display/remoting: GTK, VNC (SASL+JPEG), SPICE.
# I/O: linux-aio + io_uring. Networking: libslirp user-mode. USB
# passthrough via usbredir; TPM passthrough/emulator wiring for swtpm;
# virtiofs guests via vhost-user (the standalone virtiofsd package) and
# virtfs/9p as the secondary share path.
#
# FIRMWARE: FROM-SOURCE ONLY, ENFORCED BY AN ALLOWLIST PRUNE IN
# do_install (NOT --disable-install-blobs: that option also suppresses
# BUILDING qemu's own from-source optionroms — proven at the first r5
# build, the DFB-04 mechanism adjust, 2026-07-20). Every firmware image
# the system ships is built from source — seabios/seavgabios (the
# seabios package) and OVMF (edk2-ovmf) via symlinks, plus qemu's own
# optionroms assembled by this very build. The prune removes every
# prebuilt vendor blob make install lays down. Consequence (recorded
# deliberately): no ipxe/SLOF netboot option roms ship in v1 —
# PXE-booting a guest NIC is absent until an ipxe package is authored
# from source.
#
# Deliberate scope (all recorded, none are dep-bypass):
# - --disable-libssh: qemu's ssh block driver requires libssh (NOT the
#   shipped libssh2 — qemu dropped libssh2 support upstream). No
#   consumer needs ssh:// disk access in v1. The option is named after
#   the library ("libssh"), not the protocol — "--disable-ssh" is not
#   a valid option (verified against scripts/meson-buildoptions.sh).
# - --disable-sdl: GTK is the shipped display toolkit; a second
#   toolkit path adds surface with no consumer.
# - --disable-docs: the sphinx toolchain is not in the distribution.
# - --disable-fdt: libfdt serves device-tree guest targets only; the
#   x86_64-softmmu target declares no TARGET_NEED_FDT (verified in
#   configs/targets/x86_64-softmmu.mak; meson.build:3467 errors only
#   for targets that declare it) and hw/i386 has zero libfdt
#   references. If arm/riscv guest targets are ever added, package
#   dtc/libfdt then — it becomes a real dependency at that point.
# - --disable-download: hermetic build. configure must never reach
#   the network (meson subproject git fetches OR mkvenv PyPI
#   installs); python deps resolve from the dist tarball's vendored
#   python/wheels/, and anything missing fails loudly. The dist
#   tarball ships subprojects/dtc/ EMPTY (wrap file only), so the
#   fdt-auto default would otherwise trigger an offline git fetch.
# - VirGL/3D acceleration absent: virglrenderer is not packaged in v1.

configure() {
    set -e
    ./configure \
        --prefix=/usr \
        --sysconfdir=/etc \
        --localstatedir=/var \
        --target-list=x86_64-softmmu \
        --disable-sdl \
        --disable-libssh \
        --disable-docs \
        --disable-fdt \
        --disable-download \
        --enable-kvm \
        --enable-slirp \
        --enable-spice \
        --enable-usb-redir \
        --enable-gtk \
        --enable-opengl \
        --enable-vnc \
        --enable-vnc-sasl \
        --enable-vnc-jpeg \
        --enable-png \
        --enable-seccomp \
        --enable-cap-ng \
        --enable-linux-aio \
        --enable-linux-io-uring \
        --enable-numa \
        --enable-curl \
        --enable-zstd \
        --enable-tpm \
        --enable-vhost-user \
        --enable-virtfs \
        --enable-tools \
        --enable-guest-agent \
        --enable-pipewire \
        --enable-alsa \
        --audio-drv-list=pipewire,alsa
}

build() {
    set -e
    make -j"$(nproc)"
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" install

    # Upstream's install target creates localstatedir/run as a side effect.
    # Never ship var/run/ members: /var/run is a symlink to /run on
    # installed systems (base-files r9) and an archive dir member would
    # materialize it as a real dir at install time (split-brain runtime dirs).
    rm -rf "${DESTDIR}/var/run"

    # Allowlist prune of the bundled firmware make install laid down
    # (prebuilt ipxe/SLOF/seabios-copies/edk2 + their descriptors): keep
    # only dtb/, keymaps/, and trace-events-all — matching what
    # --disable-install-blobs shipped — then re-install the from-source
    # optionrom set fail-loud below and wire bios/vgabios to the
    # from-source packages. See the FIRMWARE header note.
    find "$DESTDIR/usr/share/qemu" -maxdepth 1 -type f ! -name trace-events-all -delete
    rm -rf "$DESTDIR/usr/share/qemu/firmware"

    # Wire the from-source firmware into qemu's default search path.
    # These packages are runtime dependencies, so the targets exist on
    # any system that has qemu (and on the assembled chroot when the
    # pre-squashfs audit walks verify_paths).
    install -d "$DESTDIR/usr/share/qemu"
    ln -sfv ../seabios/bios.bin      "$DESTDIR/usr/share/qemu/bios.bin"
    ln -sfv ../seabios/bios-256k.bin "$DESTDIR/usr/share/qemu/bios-256k.bin"
    for vga in vgabios.bin vgabios-stdvga.bin vgabios-cirrus.bin \
               vgabios-qxl.bin vgabios-virtio.bin vgabios-vmware.bin \
               vgabios-bochs-display.bin vgabios-ramfb.bin; do
        ln -sfv ../seavgabios/$vga "$DESTDIR/usr/share/qemu/$vga"
    done

    # qemu's OWN optionroms (pc-bios/optionrom/) are ASSEMBLED FROM SOURCE
    # by this very build — --disable-install-blobs correctly excludes the
    # PREBUILT third-party blobs (ipxe/SLOF) but takes these down with
    # them, and x86 machine types load kvmvapic at domain start (DFB-04,
    # 2026-07-19). Install the from-source set explicitly; install -D
    # under set -e fails loudly if the build did not produce one, which
    # is the correct outcome (never ship a machine type whose ROM is
    # silently absent). ipxe/SLOF remain excluded: prebuilt, not
    # from-source — guest NIC boot ROMs stay absent until an ipxe
    # package is authored from source.
    # qemu 11 assembles only the DMA variants (linuxboot_dma/multiboot_dma)
    # plus kvmvapic and pvh — the legacy non-DMA rom names no longer exist
    # upstream (proven ge9b-06: the r4/r5 list carried them and fail-louded).
    for rom in kvmvapic.bin linuxboot_dma.bin multiboot_dma.bin pvh.bin; do
        install -Dm644 "build/pc-bios/optionrom/$rom" \
            "$DESTDIR/usr/share/qemu/$rom"
    done

    # Guest agent: condition-gated unit (literal no-op on bare metal).
    # The upstream contrib unit has NO [Install] section (static), so the
    # preset line cannot arm it — activation is the udev rule below, which
    # starts the unit the moment the guest-agent virtio-port appears
    # (the cross-distro packaging convention; DFB-08, 2026-07-19).
    install -Dm644 contrib/systemd/qemu-guest-agent.service \
        "$DESTDIR/usr/lib/systemd/system/qemu-guest-agent.service"
    install -Dm644 /dev/stdin \
        "$DESTDIR/usr/lib/udev/rules.d/99-qemu-guest-agent.rules" <<'UDEV'
SUBSYSTEM=="virtio-ports", ATTR{name}=="org.qemu.guest_agent.0", \
  TAG+="systemd", ENV{SYSTEMD_WANTS}="qemu-guest-agent.service"
UDEV
}
