# Virtualization Decision — KVM over VirtualBox

**Date:** March 31, 2026
**Decision:** Use KVM/QEMU with libvirt for InterGenOS build environment

---

## Performance Comparison

| Factor | KVM | VirtualBox |
|--------|-----|-----------|
| CPU overhead | 0-5% (near-native) | Higher, more variable |
| Disk I/O | Excellent (virtio-scsi + IOThreads) | Good |
| Architecture | Type 1 (bare-metal, in-kernel) | Type 2 (hosted, userspace) |
| File sharing | virtiofs (kernel-native) | Guest Additions (DKMS) |

KVM executes guest code directly on host CPU via AMD-V hardware virtualization. No binary translation.

## Kernel 6.17 Compatibility

- **KVM:** Built into the kernel. Always compatible. Zero maintenance.
- **VirtualBox:** VBox 7.0.x (Ubuntu repos) is BROKEN on kernel 6.17. Requires VBox 7.2+ from Oracle's repo. DKMS breaks regularly with new kernels.

Documented issues:
- https://discourse.ubuntu.com/t/latest-hwe-kernel-upgrade-6-17-0-14-generic-conflicts-with-virtualbox-7-0-16-under-24-04/76218
- https://github.com/VirtualBox/virtualbox/issues/535

## Setup

```bash
sudo apt install qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst virt-manager
sudo usermod -aG libvirt christopher
# Log out and back in for group membership
```

## Recommended VM Configuration for LFS Builds

- **vCPUs:** 16 (leaves 8 cores + SMT for host)
- **RAM:** 32GB (leaves 32GB for host)
- **Disk:** 200GB qcow2 (supports snapshots)
- **Disk bus:** virtio-scsi with 4-8 IOThreads
- **Network:** virtio model
- **File sharing:** virtiofs for host-guest source sharing
- **Disk caching:** cache=none (let guest OS manage)

## Scriptable VM Creation

```bash
virt-install \
  --name igos-build \
  --memory 32768 \
  --vcpus 16 \
  --disk path=/var/lib/libvirt/images/igos-build.qcow2,size=200,bus=virtio \
  --network network=default,model=virtio \
  --os-variant ubuntu24.04 \
  --cdrom /path/to/ubuntu-24.04.iso \
  --graphics spice
```

Fully reproducible via virt-install + virsh. Superior to VBoxManage for automation.

## virtiofs for Host-Guest File Sharing

- Near-local filesystem performance
- No Guest Additions needed
- Supported on Linux kernel >= 5.4, QEMU >= 5.0, libvirt >= 6.2
- Guest mount: `mount -t virtiofs lfs-source /mnt/lfs-source`

## GUI

virt-manager provides functional GUI. Less polished than VirtualBox but fully capable.
For LFS work, CLI tools (virt-install, virsh) will be used more than GUI.

## Sources

- https://www.phoronix.com/review/virtualbox-60-kvm
- https://blog.purestorage.com/purely-technical/kvm-vs-virtualbox-which-one-should-you-use/
- https://help.ubuntu.com/community/KVM/Installation
- https://sysguides.com/share-files-between-kvm-host-and-linux-guest-using-virtiofs
- https://libvirt.org/kbase/virtiofs.html
