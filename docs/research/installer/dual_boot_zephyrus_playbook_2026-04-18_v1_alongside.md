# Zephyrus 11 Dual-Boot Playbook — Windows 11 + InterGenOS

**Author:** claude-windows (with input from claude-main via channel)
**Date:** 2026-04-18
**Target machine:** ASUS ROG Zephyrus M16 GU603ZW
**Goal:** Install InterGenOS alongside existing Windows 11, preserving the Windows install and ASUS factory-restore partition, producing a working RTX 3070 Ti GPU inference environment for Tier 2 (9B) LLM benchmarks.
**Executable date:** Monday evening (after replacement panel lands and Windows-side panel health is confirmed).

---

## 0. Summary of the Target State

After this procedure, the Zephyrus boots to a rEFInd boot picker (recommended) or a GRUB menu with:

1. **Windows 11** — untouched, all Windows partitions intact, ASUS RESTORE partition intact, BitLocker status unchanged from pre-install.
2. **InterGenOS** — new root partition carved from shrunken Windows C:, NVIDIA proprietary driver installed for the RTX 3070 Ti, CUDA toolkit available for llama.cpp CUDA builds.
3. A shared EFI System Partition (ESP) containing both Windows bootmgfw and InterGenOS GRUB entries — no ESP duplication needed because Fast Startup + Hibernation are both disabled as part of this procedure.

Success criteria:

- Both OSes boot cleanly from cold power-on.
- `nvidia-smi` on InterGenOS reports the RTX 3070 Ti with 8GB VRAM.
- `llama.cpp` CUDA build can load `Qwen3.5-9B-Q4_K_M.gguf` onto the GPU and run the full 112-conversation InterGenOS test suite.
- Windows partition health unchanged; `chkdsk C: /scan` reports clean.

---

## 1. Current Zephyrus State (captured 2026-04-18)

From `Get-Disk` / `Get-Partition` on the live machine:

| # | Type | Size | Notes |
|---|---|---|---|
| 1 | ESP (System) | 272 MB | FAT32, houses Windows bootmgfw |
| 2 | Microsoft Reserved | 16 MB | MSR — do not touch |
| 3 | **Basic (C:, "OS")** | **930 GB** | **NTFS, 357 GB free — shrink target** |
| 4 | Recovery | 1 GB | Windows WinRE |
| 5 | **Basic ("RESTORE")** | **23.6 GB** | **ASUS factory restore — PRESERVE** |
| 6 | Recovery | 200 MB | Secondary recovery |

Disk: NVMe Micron 2450 MTFDKBA1T0TFK, 1 TB, GPT.

**Critical observations:**

- C: sits at offset 290 MB and extends 930 GB — shrinking it creates free space **after** C: and **before** the WinRE partition. No partition move needed. ← *This is the key fact that makes this install straightforward.*
- The **ASUS RESTORE partition** (P5) is the factory recovery image. Touching it breaks ASUS's Windows reset-to-factory workflow. Preserve it. It sits at offset 1000 GB (past C:, past WinRE) and has plenty of space above it for our InterGenOS install.
- UEFI firmware confirmed (`BiosFirmwareType: Uefi`).
- **Hibernate + Fast Startup are ENABLED.** This is the #1 dual-boot danger — Windows with hibernation active will resume from a stale state after Linux has written to shared filesystems, corrupting them. **Both must be disabled before partition work begins.**
- BitLocker status could not be retrieved from a non-elevated PowerShell session. Owner stated earlier that BitLocker engagement is deferred. If BitLocker is inactive, no action needed. If it's active, it must be suspended (not decrypted) before shrinking C:; after the install, it can be resumed and will automatically re-encrypt new writes.
- Secure Boot status could not be retrieved from a non-elevated PowerShell session (query returns "Access denied"). Needs to be checked in the firmware setup menu (F2 during boot).

---

## 2. Windows-side Prep Sequence (must complete before touching partitions)

These are ordered and the order matters.

### 2.1 Full disk image backup (non-negotiable)

Use **Macrium Reflect Free** (still available 2026) or **Windows 11's built-in File History + system image** to an external disk. The image should capture all 6 partitions, not just C:. This is the escape hatch if anything goes wrong.

### 2.2 Disable Fast Startup

```
Control Panel → Power Options → Choose what the power buttons do →
Change settings that are currently unavailable →
Uncheck "Turn on fast startup (recommended)" → Save changes
```

**Why:** Fast Startup is a partial hibernation. When enabled, Windows writes the kernel state to `hiberfil.sys` on shutdown and resumes from it on next boot. Any NTFS partition shared with Linux would be corrupted if Linux wrote to it between the hibernate and resume.

### 2.3 Disable Hibernation

Elevated command prompt:

```
powercfg /h off
```

This removes `hiberfil.sys` entirely. `powercfg /a` should then show Hibernate as unavailable. The earlier `powercfg /a` output on this machine showed Hibernate + Fast Startup both available — disabling both is required.

### 2.4 Check Secure Boot state

Reboot into firmware setup (F2 at ASUS logo). Navigate to `Advanced → Secure Boot`. Note the current state. We'll reference this in §4.

### 2.5 Check BitLocker state (elevated required)

```
manage-bde -status C:
```

If it shows `Protection On`, suspend it before partition work:

```
manage-bde -protectors -disable C: -RebootCount 0
```

This suspends BitLocker indefinitely. After install is complete and the system is verified stable, re-enable:

```
manage-bde -protectors -enable C:
```

BitLocker will re-seal with the new boot configuration. **Save the recovery key before doing any of this** — if something goes wrong, BitLocker can lock you out of C: entirely. The recovery key is in the Microsoft account (`aka.ms/myrecoverykey`) if your Windows is signed in; otherwise export it from `manage-bde -protectors -get C:`.

### 2.6 Update Windows fully

Run `Windows Update` until there are no pending updates. A bootloader write during install is less dangerous if Windows is current.

### 2.7 Defragment C: (HDD-era advice, still useful for NTFS shrinks)

Even on NVMe, the NTFS shrink operation can only shrink up to the last unmovable file. Running:

```
defrag C: /X
```

(with the `/X` consolidate-free-space flag) improves the maximum shrink size meaningfully. Run this after `chkdsk C: /scan` confirms no filesystem errors.

---

## 3. Partition Strategy

### 3.1 Recommended layout post-install

After shrinking C: by 150 GB and creating InterGenOS partitions:

| # | Type | Size | FS | Purpose |
|---|---|---|---|---|
| 1 | ESP | 272 MB | FAT32 | **Shared** — Windows bootmgfw + InterGenOS GRUB |
| 2 | MSR | 16 MB | — | Windows reserved (untouched) |
| 3 | Windows C: | **780 GB** | NTFS | Shrunk from 930 GB |
| 4 | Recovery | 1 GB | NTFS | Windows WinRE (untouched) |
| 5 | RESTORE | 23.6 GB | NTFS | ASUS factory (untouched) |
| 6 | Recovery | 200 MB | NTFS | (untouched) |
| 7 | **InterGenOS root** | **140 GB** | ext4 | New — between WinRE and RESTORE? See note below |
| 8 | **Linux swap** | **8 GB** | swap | New |

**Partition placement caveat:** The natural free space created by shrinking C: sits *between* P3 (Windows C:) and P4 (WinRE) — at offsets 780 GB through 930 GB. The remaining partitions (WinRE, RESTORE, Recovery2) sit *after* that gap. That means the new InterGenOS partitions will naturally be numbered P7 and P8 (appended to the end of the partition table), even though physically they occupy the gap between P3 and P4.

This is fine — GPT partition numbers don't have to match on-disk order. But it means the Forge installer's disk detection must present partitions by *offset*, not by *number*, or users will get confused about which slot to install into.

### 3.2 Size rationale

- **Root: 140 GB.** InterGenOS core + base + desktop + extra + dev tooling + 9B GGUF model (5.3 GB) + future 35B GGUF (likely 16-20 GB Q3) + CUDA toolkit (~5 GB) + Claude Code workspace. 140 GB leaves headroom for a year of accumulation.
- **Swap: 8 GB.** Zephyrus has 24 GB RAM. Swap isn't strictly required but helps with suspend-to-disk and with memory pressure during 9B loading. 8 GB is conservative.
- **Windows remaining: 780 GB.** Current C: usage is ~630 GB. 780 GB leaves 150 GB of Windows headroom.

If owner wants more InterGenOS space (say, for hosting multiple large models or a Windows VM inside InterGenOS), bump root to 200 GB and shrink Windows to 720 GB. Still leaves ~90 GB Windows headroom.

### 3.3 Shrink procedure (Windows Disk Management, safest)

1. Open `diskmgmt.msc` as Administrator.
2. Right-click C: → `Shrink Volume`.
3. Enter shrink amount in MB: `153600` for 150 GB.
4. Click Shrink. Wait.
5. Do **not** create any partition from the unallocated space in Windows — leave it unallocated and let the InterGenOS installer claim it.

**If the shrink size is capped lower than 150 GB:**

Windows won't let you shrink past the last unmovable file (pagefile, MFT, system restore snapshots). Mitigation sequence:

- `powercfg /h off` (already done in 2.3)
- Disable pagefile temporarily: `System Properties → Advanced → Performance Settings → Advanced → Virtual memory → No paging file → Set → Reboot`
- Disable system restore on C:: `System Properties → System Protection → Configure → Disable`
- Run Disk Cleanup, include "Previous Windows installations" and "System created Windows Error Reporting files"
- `defrag C: /X`
- Retry the shrink.
- Re-enable pagefile + system restore after the shrink succeeds.

---

## 4. Bootloader Approach

### 4.1 Options

| Option | Pros | Cons |
|---|---|---|
| **GRUB + os-prober** | Standard, what Forge already installs, Linux-distro-familiar | GRUB's os-prober detection is fragile; ugly default theme; no graphical polish |
| **rEFInd** | Graphical, automatic detection, user-friendly, coexists well with Windows bootloader | Not signed for Secure Boot by default (requires shim or MOK); additional install step Forge doesn't know about |
| **systemd-boot** | Simple config, no os-prober needed, kernel+initrd in ESP | ESP is only 272 MB — tight for multiple kernels + initrds; not friendly to BIOS fallback |
| **Windows bootmgr chainload** | Windows-first UX, boots Windows by default | Requires manual EasyBCD-style entry additions; ugly Windows boot menu; no graphical distro menu |

### 4.2 Recommendation: **GRUB with os-prober (Phase 1), rEFInd later if desired**

Reasoning:

1. **Forge already installs GRUB.** Adding os-prober support is a small code change. Adding rEFInd support is a larger one that duplicates functionality.
2. **os-prober detects Windows reliably** on UEFI systems where the Windows ESP entry exists — which is our exact case.
3. **rEFInd is a polish item**, not a correctness item. Owner can install it post-install if the GRUB menu feels clunky.
4. **systemd-boot is ruled out by ESP size.** 272 MB won't hold multiple Linux kernels + Windows + initrds comfortably.

### 4.3 GRUB os-prober config for Forge

The InterGenOS package set must include `os-prober` (check `/mnt/intergenos/packages/` — if missing, add to the `base` tier). The Forge installer's `bootloader.py` needs to:

1. Install os-prober package into the target (already in pkm flow if packaged).
2. Edit `/etc/default/grub` in the target before running `grub-mkconfig`:
   - `GRUB_DISABLE_OS_PROBER=false` (set explicitly — current GRUB defaults this to `true` as a CVE mitigation; must explicitly opt in).
3. Run `grub-mkconfig -o /boot/grub/grub.cfg` inside the chroot — os-prober will scan, find Windows on P1 ESP, and emit a GRUB menu entry.

**Code change needed in [installer/backend/bootloader.py](file:///mnt/intergenos/installer/backend/bootloader.py) `install_grub()`:** after the `grub-install` call, before `grub-mkconfig`, inject a line to set `GRUB_DISABLE_OS_PROBER=false` into `/etc/default/grub`. See §5.

---

## 5. Forge Installer Changes Required

The current Forge installer (`/mnt/intergenos/installer/`, v0.1.0) does not support install-alongside. Changes needed, grouped by file, in order of invasiveness:

### 5.1 `backend/disks.py` — install-alongside partition detection

**Current state:** The installer has `detect_disks()` that lists block devices and implicitly assumes the user will pick one and `partition_disk()` will wipe it.

**Needed:** An `install_mode` concept. At least three modes:

- `whole_disk` (current behavior) — wipe target disk, create fresh GPT, ESP + root + optional swap.
- `alongside_existing` (new) — accept the target disk already has partitions; accept a user-selected unallocated region; create new partitions inside that region; detect and reuse the existing ESP.
- `manual` (future) — user pre-partitions, installer only formats and deploys.

**Minimum API addition:**

```python
@dataclass
class DiskFreeRegion:
    disk_path: str           # e.g., /dev/nvme0n1
    start_offset_bytes: int  # e.g., 837_128_192_000
    size_bytes: int          # e.g., 161_061_273_600 (150 GB)
    following_partition: str # e.g., /dev/nvme0n1p4 (if any)

def detect_free_regions(disk_path) -> list[DiskFreeRegion]:
    """Parse `parted <disk> unit B print free` output, return list
    of free regions >= 10 GB (smaller ones aren't useful targets)."""

def detect_existing_esp(disk_path) -> str | None:
    """Return path to existing ESP (e.g., /dev/nvme0n1p1) if one exists
    on the disk, else None. Uses `blkid` + GPT type GUID matching."""

def partition_into_free_region(
    disk_path, free_region, root_size_gb, swap_size_gb
) -> dict:
    """Create new partitions inside a DiskFreeRegion without touching
    anything else on the disk. Returns same dict as partition_disk()."""
```

### 5.2 `backend/bootloader.py` — os-prober opt-in

After `grub-install`, before `grub-mkconfig`:

```python
# Enable os-prober detection of existing OSes (Windows etc.)
# GRUB 2.06+ defaults this to disabled as a CVE mitigation;
# explicitly opt in.
grub_defaults = Path(target) / "etc" / "default" / "grub"
if grub_defaults.exists():
    content = grub_defaults.read_text()
    if "GRUB_DISABLE_OS_PROBER" in content:
        content = re.sub(
            r"GRUB_DISABLE_OS_PROBER=.*",
            "GRUB_DISABLE_OS_PROBER=false",
            content
        )
    else:
        content += "\nGRUB_DISABLE_OS_PROBER=false\n"
    grub_defaults.write_text(content)
```

### 5.3 `backend/config.py` — fstab must not overwrite ESP

Currently `generate_fstab()` unconditionally emits the ESP entry if `partitions.get("efi")` is truthy. In `alongside_existing` mode, the ESP is shared with Windows, so:

- The UUID reference is fine (same filesystem).
- The mount options `defaults` are fine.
- **One thing to add:** the `fmask`/`dmask` should not allow the InterGenOS user to rewrite Windows bootmgfw by accident. Suggest `umask=0077` on the ESP mount to restrict non-root reads/writes.

### 5.4 `frontend/tui.py` — disk screen needs "install alongside" flow

New screen flow when `alongside_existing` is selected:

1. List disks with existing OS detections (e.g., "Disk 0 — NVMe Micron 2450 1TB — Windows detected").
2. User picks a disk.
3. Installer shows detected free regions, sizes, and what's adjacent ("150 GB free — after Windows C:, before Windows Recovery").
4. User picks a region.
5. Installer asks: root size (default: min(free_region - 8GB, 100 GB)), swap size (default: 8 GB if RAM < 16 GB else 4 GB or 0).
6. Installer shows the plan, asks to confirm.
7. Plan executed.

### 5.5 NVIDIA driver hook

Forge currently has no NVIDIA driver handling. Add a post-install hook or a package:

- **Option A:** Ship the NVIDIA proprietary driver as a separate optional InterGenOS package (`nvidia-driver-560`) that depends on kernel headers. Install only if user selects "NVIDIA GPU detected" in the hardware-profile screen.
- **Option B:** Defer NVIDIA driver install to first-boot; detect GPU during first-boot setup and prompt the user.

My lean: **Option A.** Installing drivers at install time (rather than first boot) keeps the first boot clean and avoids a reboot dance. Owner has already accepted that InterGenOS manages hardware-specific packages via tier detection.

The driver install recipe (to be embedded in `packages/extra/nvidia-driver-560/build.sh`):

```bash
# Download official NVIDIA run-file to /tmp during package build
# Install in post_install hook via chroot:
#   bash /tmp/NVIDIA-Linux-x86_64-560.XX.XX.run \
#     --silent --dkms --no-opengl-files --no-x-check
# (omit --no-opengl-files on desktop tier, include for server tier)
# Add "nouveau" to /etc/modprobe.d/blacklist-nouveau.conf
# Regenerate initramfs
```

Alternative: use NVIDIA's "open kernel modules" package (since driver 515+), which is signable for Secure Boot. Recommend this path.

---

## 6. NVIDIA Driver Considerations for This Machine

### 6.1 Hardware specifics

- GPU: NVIDIA GeForce RTX 3070 Ti Laptop GPU
- Architecture: Ampere (GA104 die)
- Compute capability: 8.6
- VRAM: 8 GB GDDR6
- Windows driver: 591.86 (current)

### 6.2 Driver options on InterGenOS

1. **Proprietary run-file driver** (traditional path):
   - Download from nvidia.com/drivers (version 560.X or newer as of April 2026)
   - Blacklist `nouveau`
   - `--dkms` flag so the kernel module rebuilds across kernel updates
   - Full CUDA support
2. **Open kernel modules** (recommended 2026+):
   - Available for Ampere and newer (Turing+ actually, but best-tested on Ampere+)
   - Same feature set as proprietary userspace, but the kernel module is open-source
   - Can be signed with MOK for Secure Boot
3. **Nouveau** (reject — insufficient for our use case):
   - Limited compute support on Ampere
   - No CUDA
   - OK for basic display, not usable for 9B LLM inference

**Recommendation: Open kernel modules + proprietary userspace.** Signable for Secure Boot, supported by NVIDIA, full compute/CUDA stack.

### 6.3 Kernel headers requirement

The DKMS hook rebuilds the kernel module against the running kernel's headers. InterGenOS's kernel package needs to ship the matching `linux-headers-$(uname -r)` package (or equivalent). Verify this is already in the `core` tier before shipping a driver package that depends on it.

### 6.4 CUDA toolkit

For llama.cpp CUDA builds:

- CUDA 12.6+ (April 2026 current) supports compute 8.6 natively
- Ships as separate package from the driver
- Install target: `/opt/cuda-12.6/` with `/usr/local/cuda` symlink
- llama.cpp build flag: `cmake -DGGML_CUDA=ON ..`

Package this as `cuda-toolkit-12.6` in the `extra` tier. Depends on `nvidia-driver-*`.

### 6.5 Mux switch consideration

The Zephyrus M16 GU603ZW has a MUX switch — it can route the display directly through the NVIDIA GPU (Ultimate / dGPU-only mode) or through the Intel iGPU (Optimus / hybrid mode). Per the earlier system audit, the owner currently runs the internal panel disabled and uses three external monitors. After the panel replacement, the MUX setting matters:

- **Ultimate mode:** NVIDIA drives everything. Higher power draw, best performance. Simpler Linux config.
- **Optimus mode:** Intel iGPU handles desktop, NVIDIA handles high-GPU-load apps. Complex on Linux — requires `nvidia-prime` or similar.
- **Recommendation for InterGenOS tier work:** Ultimate mode. The 9B inference work uses the GPU heavily; battery life is not a concern for a plugged-in dev machine. Avoids Optimus complexity on Linux entirely.

The MUX setting is configured in the ASUS firmware (F2 during boot) under `Advanced → Graphics → MUX Switch`.

---

## 7. Secure Boot Decision

### 7.1 Current state

Secure Boot status could not be determined without elevation. Will need to confirm in firmware at install time. Three possible states and their implications:

### 7.2 If Secure Boot is **disabled**

Simplest path. No signing or MOK enrollment needed. InterGenOS GRUB boots unsigned; NVIDIA kernel modules load unsigned.

**Downside:** Windows 11 is configured to require Secure Boot for some features (notably Windows Hello's virtualization-based isolation). Disabling Secure Boot may degrade Windows functionality.

### 7.3 If Secure Boot is **enabled**

Two sub-paths:

1. **Disable it** (1-minute firmware change). Simple, loses the aforementioned Windows features.
2. **Keep it enabled, enroll MOK.** InterGenOS must:
   - Install a shim bootloader (`shim-signed`, Microsoft-signed) in the ESP
   - Shim chains to GRUB, checking GRUB's signature against MOK
   - Enroll MOK key at first boot (blue MokManager screen)
   - Sign NVIDIA kernel modules with the same MOK

**Recommendation: Keep Secure Boot enabled, enroll MOK.** It's the right long-term posture. The shim + MOK enrollment is a well-trodden path for custom distros. Forge doesn't currently support this — it's a separate medium-sized code addition — but it's worth doing right from the start rather than retrofitting.

### 7.4 Forge changes for Secure Boot support

Not strictly required for Monday evening — can disable Secure Boot and proceed, defer MOK work. Flagging as follow-up:

- Package `shim-signed` (Microsoft's signed shim, freely redistributable)
- Package `mokutil` (for MOK management)
- Installer screen: "Enable Secure Boot support?" → if yes, generate MOK keypair, install to ESP, guide user through first-boot enrollment
- NVIDIA driver DKMS hook signs each module with MOK private key

---

## 8. Filesystem Layout

### 8.1 Final partition table after install

| # | Start | Size | FS | Mount | Notes |
|---|---|---|---|---|---|
| 1 | 1 MiB | 272 MB | FAT32 | `/boot/efi` | ESP, shared with Windows |
| 2 | 273 MB | 16 MB | — | — | MSR (Windows) |
| 3 | 290 MB | 780 GB | NTFS | (not mounted) | Windows C: (shrunk) |
| 7* | 780 GB | 140 GB | ext4 | `/` | **NEW InterGenOS root** |
| 8* | 920 GB | 8 GB | swap | (swap) | **NEW** |
| 4 | 928 GB | 1 GB | NTFS | — | Windows WinRE |
| 5 | 929 GB | 23.6 GB | NTFS | — | ASUS RESTORE |
| 6 | 953 GB | 200 MB | NTFS | — | Recovery 2 |

*P7/P8 are the InterGenOS partitions. Their GPT numbers depend on what gparted/parted assigns — probably 7 and 8 since partitions 4-6 are already allocated.

### 8.2 Key decisions

- **Root on ext4, not btrfs.** Conservative choice. InterGenOS's build system is ext4-tested. Btrfs introduces complexity (subvolumes, rollback, quotas) that's worth it for desktop UX but not for a dev-box install.
- **No separate `/home` partition.** Single-root simplifies the install and matches the spirit of "a dev machine, not a long-term user workstation." If owner wants `/home` separate later, that's a backup-and-repartition workstream.
- **No encrypted root.** Owner can add LUKS in a future install if needed. For a dev machine that's physically in their office, the threat model doesn't justify the complexity.
- **Swap partition, not swapfile.** ext4 swapfiles work but add a layer of indirection. Dedicated swap partition is simpler.

### 8.3 `/etc/fstab` after install

```
# <device>                              <mount>     <type>  <options>                 <dump> <pass>
UUID=<root-uuid>                         /           ext4    defaults,noatime           1      1
UUID=<esp-uuid>                          /boot/efi   vfat    defaults,umask=0077        0      2
UUID=<swap-uuid>                         none        swap    sw                         0      0
```

The `umask=0077` on the ESP keeps InterGenOS from accidentally exposing Windows bootmgfw in userspace file listings.

---

## 9. Monday Evening Playbook — Step-by-Step

**Assumes:** Replacement panel arrived, Windows boots with panel working, owner is sitting in front of the Zephyrus.

### Phase 1 — Windows-side prep (15-20 min)

1. Full disk backup to external drive (Macrium Reflect or similar). **Block until this completes.**
2. `powercfg /h off` (elevated)
3. Control Panel → Power Options → uncheck Fast Startup
4. `chkdsk C: /scan` (elevated, read-only — about 10 min)
5. `defrag C: /X` (about 10 min on NVMe)
6. `manage-bde -status C:` → if Protection On, suspend it with `manage-bde -protectors -disable C: -RebootCount 0`. **Export BitLocker recovery key first.**
7. Open Disk Management → right-click C: → Shrink Volume → shrink by 153600 MB (150 GB) → leave unallocated

### Phase 2 — Boot firmware check (5 min)

1. Reboot, hit F2 at ASUS logo
2. Note Secure Boot state (Advanced → Secure Boot)
3. Confirm MUX Switch setting (Advanced → Graphics → MUX). Recommend: **Ultimate mode** (dGPU-only) for InterGenOS work.
4. Save & Exit.

### Phase 3 — InterGenOS install media (assumes already prepared on USB)

Prerequisites not covered here: Forge ISO built with the `alongside_existing` install mode from §5. If that code change hasn't landed by Monday, fall back to either:
- Boot a Ubuntu Live USB, manually shrink and partition, then chroot-install InterGenOS package archives using `pkm install --root` directly
- Or do the Forge changes Sunday/Monday-morning as a separate workstream

1. Insert InterGenOS install USB
2. Boot, hit F8 at ASUS logo for boot menu, select USB
3. Select "Install InterGenOS alongside existing OS" → target disk NVMe Micron
4. Select "150 GB free region" (the one between Windows C: and Recovery)
5. Root size: 140 GB, swap size: 8 GB
6. Hostname: `zephyrus11` (or per owner preference)
7. Root password + user account
8. NVIDIA driver: yes (detected GeForce RTX 3070 Ti Laptop)
9. Secure Boot: if was enabled, select "Generate MOK keypair" → write down MOK password for first-boot enrollment; if was disabled, skip
10. Confirm plan, install, wait

### Phase 4 — First boot (20-30 min)

1. Remove USB, reboot
2. GRUB menu appears — should show "InterGenOS" and "Windows Boot Manager" entries
3. Boot InterGenOS
4. If MOK enrollment was configured: blue MokManager screen — "Enroll MOK" → enter MOK password → reboot
5. Log in
6. `nvidia-smi` — confirm RTX 3070 Ti reports with driver version and 8GB VRAM free
7. `lsblk` — confirm partition layout as planned
8. Boot back into Windows (GRUB menu → Windows Boot Manager) — confirm Windows starts normally
9. Back in Windows, `chkdsk C: /scan` — confirm NTFS is clean
10. If BitLocker was suspended, re-enable: `manage-bde -protectors -enable C:`

### Phase 5 — 9B GPU benchmark setup (on InterGenOS)

1. Install CUDA toolkit (if not bundled): pkm install cuda-toolkit-12.6
2. Clone llama.cpp from github
3. `cmake -B build -DGGML_CUDA=ON && cmake --build build --config Release -j`
4. `scp christopher@192.168.1.199:/mnt/intergenos-models/Qwen3.5-9B-Q4_K_M.gguf ~/models/` (or wherever owner keeps the model canonically)
5. Start llama-server with `--gpu-layers 99 --ctx-size 8192 --parallel 1 --reasoning off --jinja`
6. Run R28-9B on the Zephyrus GPU for the first time — this is the GPU-tuned 9B benchmark that the CPU-only test deferred to Monday.

---

## 10. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Windows shrink caps below 150 GB | Medium | Low (replan to smaller) | Pre-shrink cleanup sequence in §2.7 |
| GRUB overwrites Windows bootmgfw | Low | Medium | ESP size 272 MB is enough; os-prober doesn't remove existing entries |
| BitLocker recovery triggered | Low | High (locked out of C:) | Export recovery key before partition work |
| NVIDIA driver fails to load on first boot | Medium | Medium | Boot with `nomodeset`, install manually, rebuild initramfs |
| os-prober misses Windows | Low | Low | Manually add Windows entry to `/etc/grub.d/40_custom` and re-run `grub-mkconfig` |
| ASUS RESTORE partition damaged | Very low | High | **Never** let the installer touch P5. Forge changes in §5 must explicitly exclude it from wipe candidates |
| Panel replacement fails, laptop unbootable | Low | High | Forge changes can proceed on another machine; physical install waits |
| Forge `alongside_existing` mode not ready | High | Medium | Fallback: Ubuntu Live USB + manual partitioning + `pkm install --root` |

---

## 11. Open Questions for Owner Before Executing

1. **Secure Boot posture:** accept the disabled path for Monday, or invest in MOK work beforehand? Recommend: disabled for Monday; MOK is post-install polish.
2. **Forge `alongside_existing` code changes:** want them in before Monday (so install is a clean Forge flow), or accept Ubuntu Live USB fallback for Monday and follow up on Forge code afterward?
3. **MUX switch:** Ultimate mode recommended; confirm before install.
4. **Partition sizing:** 140 GB root / 8 GB swap the right sizes, or bigger?
5. **Distro-level Secure Boot strategy long-term:** should InterGenOS join the signed-shim path (Microsoft co-signs a shim per distro) or stay MOK-only?

---

## 12. Post-Install Handoff Items

Once the install is verified stable:

- Document the final partition layout in `docs/hardware/zephyrus-m16-gu603zw.md` for future reference.
- Write the 9B-on-GPU baseline into the test-round archive as R28-9B-GPU — the counterpart to R28-9B-CPU that got rejected.
- Verify the role-framing modifier refactor (ChatGPT's insight D, currently deferred) against 9B — hypothesis is that 9B's larger capacity changes which prompt patterns work best, and 2B-tuned guardrails may be over-constraining it (per claude-laptop's caveat on the CPU R28 result).
- If 9B GPU latency is acceptable and quality exceeds 107 PASS, flip InterGenOS's default tier logic so the Zephyrus auto-detects and uses the 9B tier.

---

## 13. Sources

Dual-boot general best practices:

- [Dual Boot Windows 11 and Linux in 2026 (Windows Forum)](https://windowsforum.com/threads/dual-boot-windows-11-and-linux-in-2026-uefi-secure-boot-grub-setup-guide.405905/)
- [Dual boot with Windows — ArchWiki](https://wiki.archlinux.org/title/Dual_boot_with_Windows)
- [Enabling Secure Boot with Linux and Windows Dual-Boot Setup (burakberk.dev)](https://burakberk.dev/complete-guide-enabling-secure-boot-with-linux-and-windows-dual-boot-setup/)

NVIDIA on Linux:

- [NVIDIA — ArchWiki](https://wiki.archlinux.org/title/NVIDIA)
- [NVIDIA GeForce RTX 3070 Ti Linux Performance Review (Phoronix)](https://www.phoronix.com/review/nvidia-rtx3070ti-linux)
- [Installing Ubuntu in Dual Boot (Mindee)](https://www.mindee.com/blog/installing-ubuntu-in-dual-boot-the-nvidia-cuda-and-cudnn-drivers-for-gpu-3070-rtx-on-a-laptop)

Internal references:

- `/mnt/intergenos/docs/research/installer/installer_design_plan_2026-04-05.md` — Forge design plan
- `/mnt/intergenos/docs/research/installer/custom_installer_examples_2026-03-31.md` — installer framework survey
- `/mnt/intergenos/installer/backend/*.py` — Forge v0.1.0 source (reviewed for §5)
- System audit `C:\Users\ccork\system-audit-2026-04-16.md` — Zephyrus hardware baseline
