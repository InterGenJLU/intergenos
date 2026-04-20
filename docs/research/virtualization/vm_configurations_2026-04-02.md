# VM Configuration Reference — 2026-04-02

Captured before wiping `intergenos` VM. Use this to recreate VMs.

---

## VM: `intergenos` (target system — TO BE WIPED)

- **Purpose:** Running InterGenOS system (LFS-built)
- **Disk image:** `/mnt/jarvis-storage/VMs/intergenos.qcow2`
- **Disk format:** qcow2
- **Virtual size:** 500 GiB
- **Actual size:** 6.75 GiB
- **Machine type:** pc-q35-noble
- **CPU:** host-passthrough, 12 vCPUs
- **Memory:** 12 GB (12582912 KiB)
- **Network:** virtio, NAT via `default` network
- **Graphics:** VNC on 0.0.0.0 (autoport)
- **Video:** virtio
- **Disk bus:** virtio (vda)
- **Boot:** hd
- **Features:** ACPI, APIC
- **Guest agent:** org.qemu.guest_agent.0 (virtio-serial)
- **Audio:** none
- **OS hint:** Generic Linux 2022

### Notes
- No virtiofs mount (standalone system)
- No CDROM attached
- Had `pre-base-build` snapshot

---

## VM: `igos-build` (Ubuntu build VM — TO BE REINSTALLED)

- **Purpose:** Ubuntu host for LFS cross-compilation
- **Disk image:** `/mnt/intergenos/vm/igos-build.qcow2`
- **Disk format:** qcow2
- **Virtual size:** 300 GiB
- **Actual size:** 53.1 GiB
- **Machine type:** pc-q35-noble
- **CPU:** host-passthrough, 16 vCPUs
- **Memory:** 32 GB (33554432 KiB)
- **Memory backing:** memfd, shared access (required for virtiofs)
- **Network:** virtio, NAT via `default` network
- **Graphics:** SPICE (autoport, image compression off)
- **Video:** virtio
- **Audio:** ich9 via SPICE
- **Disk bus:** virtio (vda)
- **CDROM:** sata (sda), empty, readonly
- **Boot:** hd
- **Features:** ACPI, APIC, vmport off
- **Guest agent:** org.qemu.guest_agent.0 (virtio-serial)
- **SPICE channel:** com.redhat.spice.0 (virtio-serial)
- **USB redirection:** 2x spicevmc
- **OS hint:** Ubuntu 24.04

### virtiofs mount
- **Host path:** `/mnt/intergenos`
- **Guest tag:** `intergenos`
- **Access mode:** passthrough
- **Bus:** PCI (0x07:0x00.0)
- **Note:** Requires memoryBacking with memfd+shared in VM XML

### Key Differences from `intergenos` VM
| Setting | intergenos | igos-build |
|---------|-----------|------------|
| Purpose | Target OS | Build host |
| vCPUs | 12 | 16 |
| Memory | 12 GB | 32 GB |
| Disk location | /mnt/jarvis-storage/VMs/ | /mnt/intergenos/vm/ |
| virtiofs | None | /mnt/intergenos → intergenos |
| Graphics | VNC | SPICE |
| Audio | None | ich9/SPICE |
| memoryBacking | Default | memfd shared |

---

## Recreating the Build VM (Ubuntu reinstall)

1. Delete old disk or create new one:
   ```bash
   qemu-img create -f qcow2 /mnt/intergenos/vm/igos-build.qcow2 500G
   ```

2. Attach Ubuntu ISO to CDROM, set boot to cdrom temporarily

3. After install, mount virtiofs in guest:
   ```bash
   # In /etc/fstab:
   intergenos  /mnt/intergenos  virtiofs  defaults  0  0
   ```

4. Install LFS build prerequisites on Ubuntu

## Recreating the Target VM (when needed)

1. Create disk:
   ```bash
   qemu-img create -f qcow2 /mnt/jarvis-storage/VMs/intergenos.qcow2 500G
   ```

2. Use virt-install or virt-manager with:
   - 12 vCPUs, 12 GB RAM
   - virtio disk + network
   - VNC graphics
   - q35 machine type
   - host-passthrough CPU
