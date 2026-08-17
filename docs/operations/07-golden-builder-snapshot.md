# 07 — The Golden Builder

**Audience:** maintainers capturing the post-build, proven-good state of the build VM.

## The purpose of a Golden Builder (read this first — it is not what you might assume)

**A Golden Builder exists to be a proven, known-good, fully-buildable chroot that you
revert to and build *any additional package(s)* against.** The toolchain, every tier's
build dependencies, and the source tarballs are all present and intact, so a new — or
rebuilt — package compiles immediately, without re-paying the many hours of LFS Ch5-8 +
kernel + desktop-tier work. *That* — a ready substrate you can drop more packages into —
is the whole point.

**Fast ISO regeneration is NOT the purpose of a Golden Builder.** Mint-an-ISO is merely
one thing the substrate also happens to support (resume `--start-at bootloader` from the
golden and the pipeline runs through to an ISO). Do not describe, justify, or capture a
golden around ISO regen — the defining, load-bearing property is the **fat, ready-to-build
chroot**, full stop. (This was relitigated once because earlier revisions of this doc led
with the ISO-regen framing; it is settled now — the golden is a builder you add packages
to.)

## What a golden builder is

A **golden builder** is the build VM frozen at the point where:

1. Every package across every tier (toolchain → core → core-extra → base → kernel → desktop → extra → compute → ai — phase order reordered 2026-07-21) has built successfully.
2. The kernel and initramfs are built and standalone GRUB is staged. The Unified Kernel Images (UKIs) are not yet assembled at this halt; they build later in `phase_ukis_verity`, after `phase_squashfs` emits the verity root hash. Signing of GRUB and the UKIs is the signing ceremony at the end of `phase_ukis_verity`, covered in topic 03.
3. `phase_bootloader` has completed cleanly: shim and GRUB staging produced no FATAL entries.
4. The remaining phases are `image` → `manifest` → `squashfs` → `ukis-verity` → `iso`, the packaging, UKI-assembly, and ISO phases, all downstream of the builder.

The name reflects that the builder — the chroot tree, the toolchain, the package archives and manifests, and the recipes — has been proven good. Every package compiled cleanly, every dependency resolved, the kernel built, GRUB staged. This is the state every subsequent iteration restores from.

> **Note on terminology.** A golden builder is distinct from the "build-ready VM" of topic 01, which captures only the fresh-Ubuntu-with-tooling state. That earlier state is useful, but it is not a golden builder: a golden builder additionally carries a fully built chroot for every tier, the assembled kernel and bootloader artifacts, and a known-clean trail back to a validated ISO.

## The canonical capture sequence

This is the authoritative end-to-end procedure for minting a fresh golden builder. It deliberately follows the **clean-full-from-baseline** path rather than a checkpoint-restore shortcut, so the golden carries no risk that a reused checkpoint has drifted. Because the golden is a trust anchor for every future one-off build, security is not first; it is only.

**Run it only after a freshly built ISO has been validated to boot** (Secure Boot on, full-disk-encryption unlock, the GNOME 49 Wayland desktop reaches the greeter, and InterGen responds). A golden minted from unvalidated recipes can silently bake in a regression; topic 09 covers that lesson in detail.

1. **Validate the ISO boots.** Don't proceed until a real boot test passes. The boot test is what proves the recipes at the current `master` tip are golden.
2. **Freeze record.** Mark the exact commit that produced the validated ISO as the last contributor to a known-good build, and announce a change freeze so no further recipe changes land until the golden is captured:
   - Annotated tag on the validated commit (preferred over a marker commit, since tags are the conventional freeze anchor and add no history):
     `git -C /mnt/intergenos tag -a iso-validated-<YYYYMMDD> <validated-sha> -m "ISO-validated golden baseline — boot-tested <date>"` then `git push origin iso-validated-<YYYYMMDD>`.
   - Announce the **CHANGE FREEZE** to contributors, naming the frozen SHA. Documentation and other non-recipe commits remain freeze-neutral; recipe changes (`packages/`, `scripts/`, `config/`, `installer/`) wait until the golden is snapshotted.
3. **Clean old checkpoints** (the end-to-end run is about to recreate them, and stale checkpoints are a known hazard, per the build-rules reference):
   `rm -f /mnt/intergenos/checkpoints/intergenos-*.tar.zst` (frees the accumulated checkpoint space; the fresh run re-emits the `toolchain`, `core`, and `desktop` tarballs).
4. **Revert the build VM to the clean baseline** (this avoids dirty-chroot contamination; a stale `/mnt/igos` causes spurious failures such as the glibc `-lgcc_s` one). **STOP the VM before reverting** — never `snapshot-revert` a running domain (revert it cleanly from a `shut off` state, the same discipline as snapshot-create):
   ```sh
   virsh shutdown igos-build
   while [ "$(virsh domstate igos-build)" != "shut off" ]; do sleep 2; done   # virsh destroy as a last resort if shutdown hangs >2 min
   virsh snapshot-revert igos-build VALIDATED_COMPLETE_20260531
   virsh start igos-build
   ```
   Confirm `/mnt/igos` is empty (for the clean baseline) before launching. For a revert to a populated snapshot (e.g. a `GBCNNN`/golden substrate), confirm the expected fat chroot instead (`/mnt/igos/sources/*.tar.*` present).
5. **Run the full pipeline, stopping at the golden point** (end of `phase_bootloader`, just before `phase_image`):
   ```sh
   ssh <builder-user>@<build-vm-ip>
   cd /mnt/intergenos
   sudo bash scripts/build-intergenos.sh \
       --user christopher \
       --checkpoint \
       --stop-after bootloader \
       --debug-verbose
   ```
   `--stop-after` operates on **phase names, not package names**. The golden's
   essential property — the FAT, fully-buildable chroot (toolchain + every tier's deps +
   sources) — is present once the package set finishes (the `--stop-after ai` halt — the final package phase since the 2026-07-21 reorder) and
   stays intact through `phase_bootloader`; it is **`phase_image` that thins it** (strips
   `/sources` and the build trees to produce the lean disk image). So capture the golden at
   the final-package-phase halt — **`--stop-after ai`** since the 2026-07-21 reorder (`extra`
   before it, which is how `GB002` was captured 2026-06-18) — **or** at `--stop-after
   bootloader`. Both preserve the
   add-packages substrate that is the whole point. The only thing the extra `bootloader`
   phase adds is a pre-assembled bootloader (a minor convenience if you later
   `--start-at image`); it is **not** what makes the builder golden, and you can always
   resume `--start-at bootloader` from an `extra`-captured golden when you do want an ISO.
   (Capturing at the `extra` halt also *preserves* the fat builder you cannot otherwise
   recover without a full rebuild once `phase_image` has thinned it.)
6. **At the halt, shut down and snapshot:**
   ```sh
   virsh shutdown igos-build
   while [ "$(virsh domstate igos-build)" != "shut off" ]; do sleep 2; done
   virsh snapshot-create-as igos-build \
       --name golden-builder-<YYYYMMDD> \
       --description "Golden builder: all tiers built + bootloader assembled; pre-image; master tip <validated-sha>." \
       --atomic
   virsh start igos-build   # smoke-test the snapshot didn't corrupt boot
   ```

The resulting `golden-builder-<date>` VM snapshot is the substrate every one-off iteration reverts to (single-package rebuild, ISO regen via `--start-at image`, tier rebuild). See the iteration table below.

## Why it matters

Without a golden builder, every iteration cycle starts from either:
- a fresh build VM (hours of LFS Ch5-8, kernel, and the full desktop tier rebuild), or
- a partially restored mid-build state, which is fragile and can hide regressions.

With a golden builder, an iteration takes minutes:
- **Want one or more additional packages built? (the primary use.)** Revert to the golden, then build them against the intact toolchain/deps from inside the chroot via `chroot-enter.sh`: `python3 igos-build.py --build --tracked --only <pkg>` (topic 02). The package's dependencies are already present — that is exactly what the golden preserves.
- Need to rebuild an existing package? Same path — `--build --tracked --only <pkg>` against the golden's live chroot.
- Need to test a new compiler flag across a tier? Revert to the golden, set the flag, then inside the chroot run `python3 igos-build.py --build --tracked --skip-built --tier <name>`.
- Need to move the builder to another host? Ship the deepest checkpoint tarball plus the recipe tree (see the iteration table below).
- (Incidental) Need a new ISO from the same chroot? Resume `--start-at bootloader` from the golden and the pipeline runs through to an ISO — a capability the substrate supports, not its reason for being.

## The golden artifact and the checkpoint tarballs

The golden builder itself is a **libvirt VM snapshot** taken at the `--stop-after bootloader` halt:

| Artifact | Mechanism | Use case | Cost |
|---|---|---|---|
| **libvirt VM snapshot** (the golden) | `virsh snapshot-create-as igos-build --atomic` at the `--stop-after bootloader` halt | Fast local revert (seconds) to the proven-good full-chroot state. Host-bound: carries the build VM's entire Ubuntu OS, /etc, and /home. | Tens of GB of qcow2 overlay. |

**The `--checkpoint` tarballs are a separate, lesser thing: recovery points, not the golden.** Per the live cadence — the `case "$phase" in toolchain|core|desktop)` block in [`scripts/build-intergenos.sh`](../../scripts/build-intergenos.sh) — `--checkpoint` emits tarballs after `toolchain`, `core`, and `desktop` only. There is no `bootloader` tarball. It was removed deliberately (the orchestrator comment immediately above that case statement): a roughly 30GB tarball for a roughly 0.3GB delta over desktop, and an unsigned-bootloader userland tree is not a useful portable artifact, since any recipient still needs the signing key to produce a bootable ISO. The deepest tarball, `desktop`, is the "everything-but-ai/extra/bootloader" recovery point, useful for resuming a failed run but not a substitute for the golden snapshot.

> **Portable golden tarball — not emitted today.** A true portable (cross-host) golden tarball requires the build to run reliably end-to-end with the signing ceremony as the only interactive step. Today the golden is the local VM snapshot, and on-VM iteration uses near-instant `virsh snapshot-revert` rather than tens-of-GB tarball extraction. Once that zero-intervention gate is met, a post-signing capture point can be added.

## Prerequisites

- A build-ready VM per topic 01 (tooling installed, apt-daily timers masked, virtiofs mounted, `/mnt/igos/` empty).
- `/mnt/intergenos` repo at the master tip you want to capture against (`git -C /mnt/intergenos rev-parse master` matches `origin/master`).
- Sources downloaded and verified at `build/sources/` per every `package.yml`'s sha256 pins (`scripts/check-stable-urls.sh` plus the source-fetcher).
- Enough free disk for the build campaign (~100GB minimum, ~200GB comfortable) plus the checkpoint tarballs (roughly 40GB across the three checkpoints by the end).

## Step-by-step procedure

### 1. Run a full build through `phase_bootloader` with `--checkpoint` enabled

```sh
ssh <builder-user>@<build-vm-ip>
cd /mnt/intergenos
sudo bash scripts/build-intergenos.sh \
    --user christopher \
    --checkpoint \
    --stop-after bootloader
```

(Image credentials default to `intergenos:intergenos`. Omit `--root-password` and `--user-password` to get the default; pass them only to override. These are live-ISO credentials only — the installer collects the target system's username and passwords in its wizard, so they never reach an installed system.)

The orchestrator emits checkpoint tarballs to `/mnt/intergenos/checkpoints/` after these phases:

- `toolchain` — LFS Ch5-6 done (bootstrap toolchain ready); small (~3GB).
- `core` — LFS Ch8 done (userland baseline: glibc, gcc, bash, coreutils, and so on); ~8GB.
- `desktop` — full desktop tier complete; ~30GB. This is the deepest tarball, the "everything-but-ai/extra/bootloader" recovery point.

There is intentionally no `bootloader` tarball (see "The golden artifact and the checkpoint tarballs" above). The golden itself is the **VM snapshot** taken at the `--stop-after bootloader` halt: every tier built, kernel, initramfs, and standalone GRUB assembled, with `phase_image` not yet run. The UKIs build later in `phase_ukis_verity`, after `phase_squashfs` emits the verity hashtree.

(See the `case "$phase" in toolchain|core|desktop)` block in `scripts/build-intergenos.sh` for the live case statement and its history.)

### 2. Verify `phase_bootloader` landed cleanly

```sh
# No FATAL entries in the build log
grep -E "^\[FATAL\]|^\[ERROR\] (HALT|FAIL)" build/logs/build-intergenos-<timestamp>.log

# Archive-integrity manifest: phase_manifest emits build/intergenos-archive-manifest.txt
# (a BSD-style sha256sum manifest of every *.igos.tar.gz). It runs AFTER this
# bootloader halt, so there is no manifest yet at the snapshot point — confirm it
# exists only on a full run that proceeds past phase_image:
#   ls -l build/intergenos-archive-manifest.txt
# The declared-vs-built completeness gate is the Step 4.5 pre-squashfs verify_paths
# audit in build-squashfs.sh (also runs after this halt), which fails the build if any
# declared package's files are missing from the assembled chroot.

# Class A runtime gates pass (D-007 / D-008 / D-010 / D-011 / H-007 / K21.F)
for g in d007 d008 d010 d011 h007 k21f; do
    bash scripts/check-${g}-runtime.sh /mnt/igos 2>&1 | tail -1
done
```

If anything fails, do not snapshot. Fix the underlying issue and re-run the affected phase or phases.

### 3. Capture the libvirt VM snapshot

```sh
# Cleanly shut down the VM
virsh shutdown igos-build

# Wait for shutoff state
while [ "$(virsh domstate igos-build)" != "shut off" ]; do sleep 2; done

# Take the snapshot
virsh snapshot-create-as igos-build \
    --name golden-builder-$(date -u +%Y%m%d) \
    --description "Golden builder: all tiers built + grub staged; chroot fully built; UKIs NOT yet assembled (built later in phase_ukis_verity); ready for phase_image iteration. master tip $(git -C /mnt/intergenos rev-parse --short HEAD)." \
    --atomic

# Bring the VM back up to verify the snapshot didn't corrupt boot
virsh start igos-build
ssh <builder-user>@<build-vm-ip> 'uname -a && uptime'
```

### 4. The recovery-point tarballs are already in place

The `--checkpoint` flag in step 1 wrote the `toolchain`, `core`, and `desktop` tarballs automatically (not a bootloader tarball; see "The golden artifact and the checkpoint tarballs" above). The golden itself is the VM snapshot from step 3. Verify the deepest recovery tarball is whole:

```sh
ls -la /mnt/intergenos/checkpoints/intergenos-desktop-*.tar.zst
# ~30GB

# Verify the tarball is readable and structurally sound (without extracting it)
zstd -t /mnt/intergenos/checkpoints/intergenos-desktop-*.tar.zst
```

These tarballs are run-recovery points for resuming a failed campaign without re-running earlier tiers; they are not the golden. The golden is the step-3 VM snapshot.

## Iteration workflows the golden builder unlocks

| Workflow | Approach | Why this is fast |
|---|---|---|
| Generate a new ISO with current chroot state | Revert the VM snapshot (or restore the tarball) → re-mount the chroot via `chroot-setup.sh` → `sudo bash scripts/build-intergenos.sh --start-at image` | Skips hours of build phases 1-16. The pipeline runs image → manifest → squashfs (emits the verity hashtree) → ukis-verity (builds, then halts for signing) → iso. |
| Rebuild one package | Don't revert. Inside the chroot (via `chroot-enter.sh`): `python3 igos-build.py --build --tracked --only <pkg>` | Builds only that named package; its dependencies must already be in the chroot. Substrate choice (live vs. rollback) + post-rebuild paths: topic 09 "The build substrate" + iteration-path sections. |
| Test a new compiler flag across a tier | Revert to the golden, set the flag, then inside the chroot: `python3 igos-build.py --build --tracked --skip-built --tier <name>` | Avoids rebuilding unrelated tiers. |
| Move the builder to another host | Copy `checkpoints/intergenos-desktop-*.tar.zst` (the deepest emitted tarball) and the `/mnt/intergenos` repo to the new host. There: `sudo rm -rf /mnt/igos/* && sudo tar -C /mnt/igos --zstd -xf intergenos-desktop-*.tar.zst`. Run `chroot-setup.sh`, then rebuild the ai, extra, and bootloader phases and iterate. | The chroot tree is host-independent by LFS construction. |
| Recover from a corrupted chroot | Revert the VM snapshot (local), or wipe `/mnt/igos/` and untar the checkpoint (portable). | One command back to known-good. |

## When to roll a new golden

A fresh golden builder is captured when:

- **Substantial code changes** have landed in the package tree (toolchain version bumps, a major tier reshape, a new tier added). The old golden's chroot will not reflect them.
- **A security-policy directive lands** that changes build-time policy (a new Class A gate, a new SBAT level, and the like). A fresh build is needed to confirm the policy compiles cleanly across the tree.
- **The build VM's toolchain has materially shifted** (an apt upgrade bumped GCC, glibc, or the kernel on the host VM). The chroot's LFS Ch5-6 bootstrap is relative to the host toolchain, so if the host toolchain changes, re-bootstrap before re-snapshotting.
- **The old golden was based on code later found to be broken.** See topic 09. If a quality audit surfaces deep issues, the golden built before the audit is not trustworthy. Capture a fresh one once the issues are fixed.

Stale goldens take up real space (the qcow2 overlay plus tarballs run to tens of GB). Prune them with `virsh snapshot-delete` once a fresh golden is validated.

## Validation

A fresh golden builder passes:

- `virsh snapshot-list igos-build` shows the new `golden-builder-<date>` entry.
- A cold-boot smoke test from the VM snapshot: `virsh start` succeeds, SSH works, and the `/mnt/igos/` contents are present.
- The deepest checkpoint tarball exists at `checkpoints/intergenos-desktop-<timestamp>.tar.zst` (there is intentionally no `bootloader` tarball), and `zstd -t` validates its integrity.
- An iteration smoke test: revert to the golden, run `--start-at image`, sign at the `ukis-verity` pause, resume with `--start-at iso`, and confirm a working ISO comes out the other side. Run this once when you first need an iteration; it is the real-world validation that the golden is sound. The signing pause sits between `ukis-verity` and `iso` because the live-mode UKI's sealed cmdline carries the squashfs's verity root hash, which is known only after `phase_squashfs`.

## Common failures and troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `virsh shutdown` hangs longer than 2 minutes | systemd-shutdown is stuck on a unit | `virsh destroy igos-build` (a last-resort ungraceful poweroff), then start the VM and investigate the stuck unit before re-snapshotting. |
| `virsh snapshot-create-as` fails with `internal error: rename` | The filesystem where the qcow2 lives is full | Free space on the host (the stale checkpoints under `build/` are usually the first thing to prune), then retry. |
| Snapshot exists, but `virsh start` errors connecting to the monitor | qemu cannot open the backing file (permissions or corruption) | Run `qemu-img check <path>` against the backing qcow2; if it is corrupted, revert to the prior snapshot and re-take. |
| A tarball restore on another host produces a chroot that cannot build | The new host has a materially different kernel or libc, or the `/mnt/intergenos` repo is at a different master tip than the tarball was captured against | Use a host whose kernel is at least the version the chroot was built against; pin the repo to the captured master tip. |
| `phase_image` from the restored golden produces a different ISO than the original | Drift in `/mnt/intergenos/` between capture and restore | Pin both the chroot (golden tarball) and the recipes (`/mnt/intergenos` at a specific tip) for reproducible iteration. |

## Cross-references

- Topic 01: build-VM setup — produces the build-ready VM that this state is captured from.
- Topic 02: running the builder — the full pipeline whose end-of-`phase_bootloader` is the golden.
- Topic 03: automating signing — the signing ceremony, which runs at the end of `phase_ukis_verity`, downstream of the golden.
- Topic 04: generating the squashfs — `phase_squashfs`, which emits the verity root hash the live UKI seals.
- Topic 05: creating the ISO — `phase_iso` work, which iteration runs against the golden.
- Topic 09: iteration methodology — the lessons behind capturing a golden only from validated recipes.
- the `case "$phase" in toolchain|core|desktop)` block in `scripts/build-intergenos.sh` — the canonical checkpoint case statement.
