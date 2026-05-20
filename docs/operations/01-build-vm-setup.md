# 01 — Setting up and validating a build VM

**Audience:** maintainers provisioning a new build VM (e.g., after hardware replacement, fresh workstation, or a deliberately-rebuilt golden start state).

## Goal

Stand up `igos-build` — a libvirt-managed Ubuntu 24.04 VM that:

1. Has the build toolchain on PATH (qemu-utils, parted, dosfstools, mtools, squashfs-tools, xorriso 1.5.6+, grub-pc-bin + grub-efi-amd64-bin for grub-mkstandalone).
2. Mounts the host's `/mnt/intergenos` clone via virtiofs at the same path inside the VM.
3. Has masked apt background timers (per the apt-timer-isolation operational note) so `unattended-upgrades` doesn't run mid-build.
4. Has `christopher` as the build user with NOPASSWD sudo and SSH key auth from the workstation.
5. Has an empty `/mnt/igos/` directory (where each build campaign's chroot will land).

This VM is the substrate every subsequent topic depends on. The final step (topic 07) freezes it as a "golden start state" libvirt snapshot for rapid reverts between builds.

## Prerequisites

- A workstation with libvirt + KVM acceleration: Ubuntu host with `qemu-kvm`, `libvirt-daemon-system`, `libvirt-clients`, `virt-manager`, `virtinst`, `virtiofsd` installed.
- ~32GB free RAM (the VM is sized 32GB; KVM doesn't require all of it as RSS but the allocation matters for overcommit policy).
- ~300GB free disk for the VM's qcow2 backing file.
- An Ubuntu 24.04 server install ISO (`ubuntu-24.04.X-live-server-amd64.iso`) downloaded from <https://ubuntu.com/download/server>. Verify the SHA256SUMS against Ubuntu's published signatures before use.
- A host clone of the InterGenOS repo at `/mnt/intergenos` (or a path you'll virtiofs-mount as `/mnt/intergenos` in the VM).
- Workstation SSH keypair available at `~/.ssh/id_ed25519.pub` (or your preferred algorithm).

## Step-by-step procedure

### 1. Prepare the cloud-init seed.iso

The repo ships a cloud-init template at `vm/cloud-init/` (`user-data` + `meta-data`). It contains three placeholders that must be substituted before use:

| Placeholder | Replace with |
|---|---|
| `<username>` | Your Linux username (e.g., `christopher`) |
| `REPLACE_WITH_PASSWORD_HASH` | Output of `mkpasswd --method=sha-512` (install with `apt install whois`) |
| `REPLACE_WITH_SSH_PUBLIC_KEY` | The full single-line content of `~/.ssh/id_ed25519.pub` (or your equivalent) |

**The cloud-init README references `scripts/build-vm-seed.sh` for automated substitution; that script is not yet authored.** Until it lands, perform the substitution manually with a private working copy:

```sh
# Per D-016, scratch artifacts live under ~/tmp/<workflow>/, not /tmp.
mkdir -p ~/tmp/igos-vm-seed
cp vm/cloud-init/meta-data vm/cloud-init/user-data ~/tmp/igos-vm-seed/

# Edit ~/tmp/igos-vm-seed/user-data — substitute the three placeholders.
# DO NOT commit the substituted file back to the repo.

cloud-localds ~/tmp/igos-vm-seed/seed.iso \
    ~/tmp/igos-vm-seed/user-data \
    ~/tmp/igos-vm-seed/meta-data
```

`cloud-localds` ships with the `cloud-image-utils` Ubuntu package. If unavailable, the equivalent `genisoimage` invocation is:

```sh
genisoimage -output ~/tmp/igos-vm-seed/seed.iso \
    -volid cidata -joliet -rock \
    ~/tmp/igos-vm-seed/user-data ~/tmp/igos-vm-seed/meta-data
```

Once `seed.iso` is built, the placeholder-substituted `user-data` can be deleted to keep credentials off-disk.

### 2. Create the qcow2 backing disk

```sh
qemu-img create -f qcow2 /var/lib/libvirt/images/igos-build.qcow2 300G
```

300GB matches the build chroot's worst-case footprint (~50GB chroot + headroom for sources, archives, signed artifacts). Adjust if you have less storage but keep in mind that running out of disk mid-build is a slow, painful failure mode.

### 3. Create the VM with virt-install

```sh
sudo virt-install --name igos-build \
    --memory 32768 \
    --vcpus 16 \
    --cpu host-passthrough \
    --machine q35 \
    --os-variant ubuntu24.04 \
    --disk path=/var/lib/libvirt/images/igos-build.qcow2,format=qcow2,bus=virtio \
    --cdrom /path/to/ubuntu-24.04.X-live-server-amd64.iso \
    --disk path="$HOME/tmp/igos-vm-seed/seed.iso",device=cdrom \
    --network network=default,model=virtio \
    --graphics vnc,listen=0.0.0.0 \
    --memorybacking source.type=memfd,access.mode=shared \
    --filesystem source=/mnt/intergenos,target=intergenos,driver.type=virtiofs \
    --noautoconsole
```

- `--memorybacking source.type=memfd,access.mode=shared` is required for virtiofs to work on q35 machines.
- `--filesystem source=...,target=intergenos,driver.type=virtiofs` mounts the host's `/mnt/intergenos` so the VM sees it at the path declared in `vm/cloud-init/user-data:32` (`intergenos /mnt/intergenos virtiofs defaults,nofail 0 0`).
- `--noautoconsole` lets you attach later via `virsh console igos-build` or VNC.

Cloud-init runs autoinstall from the seed.iso, lays down the OS, sets up the user, and exits. The VM reboots and you can SSH in once it comes back up (`virsh net-dhcp-leases default` to find the IP).

### 4. Mask apt background timers

Per the apt-timer-isolation operational note: `unattended-upgrades` will mutate host package state mid-build if left running, and the failure mode is non-deterministic — the build seems to work, then a fortnight later a package was installed/upgraded during a long build and the chroot ends up subtly different. Mask both:

```sh
ssh christopher@<vm-ip> 'sudo systemctl mask apt-daily.timer apt-daily-upgrade.timer && sudo systemctl stop apt-daily.timer apt-daily-upgrade.timer'
ssh christopher@<vm-ip> 'systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer'
# Expected: `masked` for both
```

### 5. Install the build toolchain

cloud-init installed `build-essential bison gawk texinfo python3 python3-pip`. Add the rest:

```sh
ssh christopher@<vm-ip> 'sudo apt update && sudo apt install -y \
    qemu-utils parted dosfstools mtools squashfs-tools \
    libisoburn xorriso \
    grub-pc-bin grub-efi-amd64-bin grub-common \
    cloud-image-utils \
    python3-pyyaml \
    rsync git curl wget jq \
    sbsigntool efitools'
```

Verify the version-sensitive ones:

```sh
ssh christopher@<vm-ip> 'xorriso --version | head -1 && grub-mkstandalone --version'
```

xorriso must be 1.5.6+ (the version that honors `SOURCE_DATE_EPOCH`; older versions silently produce non-reproducible ISOs).

### 6. Confirm /mnt/intergenos is mounted

```sh
ssh christopher@<vm-ip> 'mountpoint /mnt/intergenos && ls /mnt/intergenos | head'
# Expected: `/mnt/intergenos is a mountpoint` + the repo top-level listing
```

If not mounted, the cloud-init `late-commands` block at `vm/cloud-init/user-data:32` may have failed. Manually mount and update `/etc/fstab` to match the cloud-init line.

### 7. Create the empty build chroot directory

```sh
ssh christopher@<vm-ip> 'sudo mkdir -p /mnt/igos && sudo chown christopher:christopher /mnt/igos'
```

The build orchestrator (topic 02) populates `/mnt/igos/` as it runs.

### 8. (Optional but recommended) Run the validation checks before snapshotting

Topic 07's step 1 lists the validation queries. Run them and confirm a clean state before invoking `virsh snapshot-create-as`.

## Validation

A working build VM passes all of:

- `virsh list --all | grep igos-build` → state `running` (or `shut off` after shutdown).
- `ssh christopher@<vm-ip> id` → returns uid 1000 with `wheel`/`adm`/`sudo` group membership.
- `ssh christopher@<vm-ip> 'sudo -n true && echo OK'` → prints `OK` (NOPASSWD sudo works).
- `ssh christopher@<vm-ip> mountpoint /mnt/intergenos` → confirms virtiofs.
- `ssh christopher@<vm-ip> 'systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer'` → `masked` for both.
- `ssh christopher@<vm-ip> 'command -v xorriso && xorriso --version | head -1'` → 1.5.6+.

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `virt-install` fails with `unsupported configuration: memoryBacking source type memfd not supported` | host libvirt is old, doesn't know `memfd` | upgrade `libvirt-daemon-system` or fall back to `memoryBacking access.mode=shared` without `source.type` |
| VM boots but no virtiofs mount | `virtiofsd` not running on host OR domain XML missing `<memoryBacking>` | check `journalctl -u libvirtd` for virtiofsd errors; verify domain XML; restart libvirtd |
| cloud-init seems to do nothing | seed.iso not attached, or `user-data` failed schema validation | check `virsh console igos-build` log for cloud-init output; validate user-data with `cloud-init schema --config-file user-data` |
| `apt update` returns 401 Not Authorized from updates.archive.ubuntu.com | host or VM clock is wildly skewed | `timedatectl set-ntp true` inside the VM, retry |
| `xorriso --version` returns 1.5.4 on a recent Ubuntu | the `xorriso` package is split; need to apt-install `libisoburn` for the full version | apt install libisoburn |
| Build inside VM hits "device or resource busy" on /mnt/igos | a previous build left mounts inside the chroot | `scripts/chroot-teardown.sh` (or manual `umount -l` of the chroot's pseudo-fs); start from a clean `/mnt/igos` per step 7 |

## Cross-references

- Topic 07: How to snapshot a "Golden Builder" — the immediate next step once this VM validates
- Topic 02: How to run the builder — what runs inside this VM
- the apt-timer-isolation operational note — origin of the apt-timer-masking rule
- `vm/cloud-init/README.md` — cloud-init template details and placeholder substitution
- `vm/cloud-init/user-data` — the actual autoinstall config
- Note: `scripts/build-vm-seed.sh` is referenced in `vm/cloud-init/README.md` but not yet authored — see topic 10 (recommendations)
