# 07 — Snapshotting a "Golden Builder"

**Audience:** maintainers who have validated a clean Ubuntu build VM and want to freeze it as a known-good starting point for future build campaigns.

## Goal

Capture a libvirt snapshot of the build VM in a state where:

1. Ubuntu base packages are at a known-good level.
2. `apt`'s background timers are masked (see the apt-timer-isolation operational note) — no surprise `unattended-upgrades` mutates host state mid-build.
3. The build chroot at `/mnt/igos/` is **absent or empty** — the snapshot is a clean starting state, not a mid-build checkpoint.
4. The `christopher` user has working SSH key auth, `sudo NOPASSWD`, and the build orchestrator's required tooling (qemu-img, qemu-nbd, parted, mkfs.ext4, mkfs.vfat, xorriso, mcopy, grub-mkstandalone — see topic 01) on PATH.
5. `virtiofs` for `/mnt/intergenos` is wired in the domain XML, mounting the host clone of the repo into the VM.

Snapshots taken at this state let any maintainer revert to a known-clean builder in seconds rather than rebuilding the VM from scratch (~30 min).

## Prerequisites

- libvirt running on the workstation host, with the `igos-build` VM defined (topic 01).
- The VM's qcow2 backing file lives on a filesystem with enough free space for a delta — a fresh snapshot is roughly a few hundred MB; long-running snapshot chains can grow into tens of GB and slow the VM, so prune old snapshots periodically.
- `virsh` and `qemu-img` available on the host. Both ship with the `libvirt-clients` + `qemu-utils` Ubuntu packages.
- The VM is **shut down** before snapshotting. Live snapshots are technically supported but make the snapshot a hot-state capture; cold snapshots are simpler to reason about and exactly what we want for "golden start state."

## Step-by-step procedure

### 1. Verify the VM is in clean state before snapshotting

SSH into the VM and confirm:

```sh
# 1a. /mnt/igos is empty (no half-built chroot)
ssh christopher@192.168.122.249 'sudo ls /mnt/igos/ 2>/dev/null'
# Expected: empty output, OR `ls: cannot access '/mnt/igos/': No such file or directory`

# 1b. apt timers are masked (per the apt-timer-isolation operational note)
ssh christopher@192.168.122.249 'systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer'
# Expected: `masked` for both

# 1c. The virtiofs share for /mnt/intergenos is mounted
ssh christopher@192.168.122.249 'mountpoint /mnt/intergenos'
# Expected: `/mnt/intergenos is a mountpoint`

# 1d. Required build tooling is on PATH
ssh christopher@192.168.122.249 'for t in qemu-img qemu-nbd parted mkfs.ext4 mkfs.vfat xorriso mcopy grub-mkstandalone; do command -v "$t" >/dev/null || echo "MISSING: $t"; done'
# Expected: empty output (all tools found)
```

Anything missing → fix in the VM before snapshotting. The snapshot's value is being able to skip the fix step on every future build.

### 2. Shut down the VM cleanly

```sh
virsh shutdown igos-build
```

Wait for `virsh list --all` to show `igos-build` as `shut off`. If it doesn't shut down within ~60s, use `virsh shutdown igos-build --mode acpi` or as a last resort `virsh destroy igos-build` (the latter is `ungraceful poweroff` — only use if shutdown hangs).

### 3. Take the snapshot

```sh
virsh snapshot-create-as igos-build \
    --name fresh-ubuntu-$(date -u +%Y%m%d) \
    --description "Clean Ubuntu 24.04 build VM, apt-timers masked, no chroot, $(date -u --iso-8601=seconds)" \
    --atomic
```

The `--atomic` flag prevents a partial-snapshot state if the host loses power mid-operation. `--name` includes the date for human-readability when there are multiple golden snapshots over time.

### 4. Verify the snapshot landed

```sh
virsh snapshot-list igos-build
```

Expected: the new snapshot in the list, current state `shutoff`.

```sh
virsh snapshot-info igos-build --snapshotname fresh-ubuntu-$(date -u +%Y%m%d)
```

Should show `State: shutoff`, `Domain snapshot: yes`, and the description text.

### 5. Start the VM back up to confirm the snapshot didn't corrupt it

```sh
virsh start igos-build
ssh christopher@192.168.122.249 'uname -a && uptime'
```

A clean uptime under a minute confirms the cold start worked. If it doesn't boot, the snapshot may have caught an inconsistent disk state — revert to the prior snapshot and investigate.

## Validation

- `virsh snapshot-list igos-build` shows the new snapshot.
- VM boots cleanly from the snapshot (step 5 above).
- A test-revert (`virsh snapshot-revert igos-build --snapshotname fresh-ubuntu-<date>`) yields the same `/mnt/igos` empty + `apt-daily*.timer` masked + tooling present state.

## When to roll a new golden

A new golden snapshot is taken when:

- **The toolchain has materially changed on the build VM** (e.g., apt upgrade-bumped GCC, glibc, kernel). These are baseline tools, not InterGenOS tools — but they affect every chroot-internal build because the host toolchain is the bootstrap. Test that LFS Ch5-7 builds clean on the new toolchain before snapshotting.
- **The VM's network/SSH/virtiofs configuration has changed** in a way that subsequent builds need (e.g., new key authorized for fleet auth, virtiofs path adjusted).
- **A known-bad state was fixed** — never snapshot a still-degraded VM.

Snapshots are cheap, so err on the side of taking them more often. Old snapshots are pruned with `virsh snapshot-delete igos-build --snapshotname <old>`.

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `virsh shutdown` hangs >2 min | systemd-shutdown stuck on a unit | `virsh destroy igos-build` then start + investigate the stuck unit before re-snapshotting |
| `virsh snapshot-create-as` fails with `internal error: rename` | filesystem full where the qcow2 lives | free space on the host filesystem, retry |
| Snapshot exists but `virsh start` errors with `error: internal error: process exited while connecting to monitor` | qemu can't open the backing file (permissions or corruption) | `qemu-img check <path>` against the backing qcow2; if corrupted, revert to prior snapshot |
| VM boots but `/mnt/igos` is unexpectedly populated | snapshot captured a mid-build state | not a clean golden; re-snapshot from the clean state per step 1 |

## Cross-references

- Topic 01: How to set up and validate a build VM — covers the VM creation flow that produces the candidate-for-snapshot state
- Topic 02: How to run the builder — the workflow this snapshot enables (revert to clean → build → discard chroot → revert to clean for next build)
- the apt-timer-isolation operational note — why apt timer masking is part of "golden state"
