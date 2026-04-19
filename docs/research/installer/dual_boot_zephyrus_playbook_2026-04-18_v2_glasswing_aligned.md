# Zephyrus 11 Dual-Boot Playbook — v2, Glasswing-Aligned

**Author:** claude-windows (revised with Mythos/Glasswing context)
**Date:** 2026-04-18 (v2, supersedes v1)
**Target machine:** ASUS ROG Zephyrus M16 GU603ZW (Erica's primary workhorse)
**Goal:** Install InterGenOS alongside Windows on a **dedicated second NVMe in M.2 slot 2**, with full Secure Boot chain enforcement from day one, aligned with the Project Glasswing security posture.

> **Supersedes v1.** v1 planned an install-alongside via NTFS shrink with Secure Boot optionally disabled. That approach was calibrated to a pre-Mythos / pre-Glasswing threat model and is no longer appropriate. See `dual_boot_zephyrus_playbook_2026-04-18_v1_alongside.md` for historical reference.

---

## 0. Why v2

Between v1 drafting and v2 drafting, the operational threat model shifted. Anthropic's Project Glasswing (announced 2026-04-07) and the Claude Mythos Preview model together have ended a roughly twenty-year attack/defense equilibrium. Mythos-class models can find novel vulnerabilities in every major OS and browser at human-expert-plus levels, at scale. The correct response is to **make every hard-security primitive we have as load-bearing as possible** — Secure Boot, signed boot chains, TPM attestation, verified kernel modules — rather than treating them as inconvenient setup steps that can be skipped for convenience.

**v2 design principles:**

1. **No variance from "what's the most secure way forward?"** Secure Boot stays on throughout. No disable-for-convenience. Ever.
2. **Physical separation over shared state.** A dedicated drive for InterGenOS is better than shrinking Windows, because it gives a cleaner trust boundary and zero risk to Erica's production Windows install.
3. **Signed everything.** Shim, GRUB, kernel, kernel modules. Every component in the boot chain is cryptographically verified at boot.
4. **MOK-based custom signing.** InterGenOS enrolls its own Machine Owner Key; everything InterGenOS ships is signed with it; NVIDIA's open kernel modules are signed with it via DKMS.
5. **Architecture forward-compatible with continuous attestation and post-quantum signatures.** These aren't Monday work, but the boot chain should not have to be rebuilt to accommodate them later.

---

## 1. Target State

After this procedure:

- **Drive 1 (existing, 1 TB Micron 2450):** untouched. Windows 11, its ESP, its Recovery partitions, and the ASUS RESTORE partition are exactly as they are today. No NTFS shrink, no BitLocker dance, no partition work. Erica's machine is physically incapable of being broken by InterGenOS work because InterGenOS isn't on that drive.
- **Drive 2 (new, 500 GB NVMe in M.2 slot 2):** full InterGenOS install. Its own ESP with shim-signed + GRUB. InterGenOS root, swap, optional future partitions.
- **Firmware:** Secure Boot **enabled**. MUX switch: Ultimate (dGPU-only) for NVIDIA passthrough.
- **Boot selection:** firmware's native boot picker (F8 at ASUS logo) or GRUB's Windows entry (via os-prober pointing at Drive 1's ESP). Either works; firmware picker is cleaner, GRUB is convenient.

**Success criteria:**

- Windows boots from Drive 1 exactly as before. `chkdsk` reports no filesystem changes.
- InterGenOS boots from Drive 2 with Secure Boot enabled all the way through the chain.
- `mokutil --sb-state` reports `SecureBoot enabled`.
- `nvidia-smi` reports the RTX 3070 Ti with 8 GB VRAM and the `open` variant of the kernel module loaded + signed.
- `llama-server` CUDA build loads Qwen3.5-9B-Q4_K_M.gguf onto the GPU and runs the 112-conversation InterGenOS test suite at materially better latency than 2B on CPU.

---

## 2. Shopping List

Order before end-of-day Thursday for Monday arrival.

| Item | Suggested model | Approx price (April 2026) | Notes |
|---|---|---|---|
| 500 GB NVMe PCIe 4.0 | Samsung 990 Pro, WD Black SN850X, Crucial T700, Micron 3500, Kingston KC3000 | $45-70 | Any reputable brand; all are comfortably fast for LLM inference bandwidth. Double-check TBW rating (~300 TBW+ preferred). |
| M.2 mounting screw | — | — | Check the Zephyrus service manual — most come with spare; owner likely has one in the accessories box. |

No other hardware required.

**Zephyrus M16 GU603ZW slot 2 specs (verified via ASUS docs):**
- Type: M.2 2280, PCIe 4.0 x4 NVMe
- Max capacity: 4 TB (500 GB is well within spec)
- Some users reported initial BIOS detection issues — resolved by firmware update. We applied 10.1.2.311 on 2026-04-16, which should already be current.

---

## 3. Pre-Monday Engineering Workstream (parallel to shipping)

This is the work that must land in InterGenOS before Monday's install can proceed with a Secure-Boot-enabled posture. claude-main and claude-laptop should be informed via the channel that this workstream exists and who owns what.

### 3.1 Packages to add

| Package | Purpose | Upstream |
|---|---|---|
| `shim-signed` | Microsoft-signed first-stage bootloader, chains to our signed GRUB | Fedora/Debian ship this; can be repackaged for InterGenOS |
| `mokutil` | User-space tool for enrolling Machine Owner Keys | Standard Linux tool |
| `sbsigntools` | Signing binaries with Secure Boot keys | Standard; needed for build pipeline |
| `efibootmgr` | Register UEFI boot entries | Likely already in base |
| `nvidia-driver-open` | NVIDIA open kernel modules variant (signable) + proprietary userspace | Replaces any existing nvidia-driver package |
| `tpm2-tools` | TPM2 interaction utilities | For future attestation hooks |
| `tpm2-tss` | TPM2 Software Stack (libraries) | Same |

### 3.2 Forge installer changes

- **backend/bootloader.py** rewrite for signed-boot flow:
  1. Install `shim-signed` (copies shim to ESP as `/boot/efi/EFI/InterGenOS/shimx64.efi`)
  2. Install GRUB signed with distro MOK (to ESP as `/boot/efi/EFI/InterGenOS/grubx64.efi`)
  3. Register shim as primary UEFI boot entry via `efibootmgr`
  4. Set `GRUB_DISABLE_OS_PROBER=false` (so GRUB detects Drive 1's Windows)
  5. Generate `grub.cfg`

- **backend/mok.py** (new file) — MOK keypair generation and enrollment helpers:
  - `generate_mok_keypair(target)` — create key + certificate in `/var/lib/intergen/mok/` with 0600 perms
  - `queue_mok_enrollment(target, mok_cert_path)` — call `mokutil --import` inside chroot so first boot triggers MokManager
  - `sign_efi_binary(binary, mok_key, mok_cert)` — wrapper around `sbsign`

- **frontend/tui.py** — new screens:
  - **MOK setup screen:** explain what MOK is in one paragraph (non-technical), require the user to pick a MOK enrollment password, clearly warn them that this password is needed at first boot to complete enrollment.
  - **Post-install screen:** display the MokManager first-boot instructions prominently.

- **packages/extra/nvidia-driver-open** (new package) with post_install hook:
  - Install via DKMS so kernel-module rebuilds auto-sign against the enrolled MOK on kernel updates
  - Blacklist nouveau in `/etc/modprobe.d/blacklist-nouveau.conf`
  - Regenerate initramfs

- **packages/core/kernel** build.sh addition:
  - Kernel sign-after-build step using the InterGenOS distro signing key
  - Kernel modules auto-signed against same key via `CONFIG_MODULE_SIG=y` + `CONFIG_MODULE_SIG_KEY` in kconfig

### 3.3 Distro signing key vs MOK — clarify

Two keys in play, both matter:

1. **Distro signing key** (InterGenOS project-owned, lives in the build pipeline) — signs every stock kernel and every shipped kernel module. Its public half is either: (a) enrolled in shim's "vendor DB" (requires Microsoft co-sign of our shim variant — infeasible for a new distro), OR (b) enrolled as part of MOK at install time.
2. **MOK** (Machine Owner Key, per-install, generated at install) — user's own key. Signs anything the user builds locally (DKMS modules, custom kernels). Also holds the enrolled InterGenOS distro public key if we go with path (b) above.

**Recommendation:** path (b). At install time, Forge generates a per-install MOK, and enrolls both the MOK public half AND the InterGenOS distro signing public half into the MOK database. This lets shim-signed verify our signed GRUB + kernel at boot (MOK database is consulted after vendor DB fails), and lets DKMS-signed modules verify too. No Microsoft co-sign dependency.

### 3.4 Work estimate

- Packages (shim-signed repack, nvidia-driver-open, tpm2 suite): 4-6 hours wall-clock if the owner-familiar parts exist; 1-2 days if greenfield
- Forge `bootloader.py` + `mok.py` + `tui.py` changes: ~1 day
- Kernel package signing config: half a day
- End-to-end test on a VM (Secure-Boot-enabled UEFI VM, fresh install, verify MokManager flow, verify signed chain boots): half a day

**Total:** 2-4 days. **This needs to start today or Saturday to be ready Monday.** claude-main is the natural owner (InterGenOS packaging + Forge). claude-laptop can own the VM-based end-to-end test harness.

---

## 4. Physical Install (Monday evening, after panel replacement is verified)

### 4.1 Install the new drive (10 min)

1. Power off, unplug AC, hold power button 10 sec to drain caps.
2. Remove bottom case (ASUS service manual on ROG site has the screw pattern).
3. Locate M.2 slot 2 (service manual will identify — typically right next to the primary M.2). **Do not** touch slot 1; that's Erica's Windows.
4. Remove the slot 2 mount screw and any thermal pad cover. Insert NVMe. Seat. Screw down.
5. Reinstall bottom case. Plug in AC.

### 4.2 Firmware configuration (5 min)

1. Power on, F2 at ASUS logo.
2. **Advanced → Storage Configuration:** verify slot 2 drive is detected. If not, save+exit, power-cycle, re-enter; typical first-boot post-install quirk.
3. **Advanced → Graphics → MUX Switch: Ultimate** (dGPU-only).
4. **Advanced → Secure Boot:** verify **Enabled** and in "Standard" or "Custom" mode. If "Custom," good; if "Standard," we need the Forge shim-signed path because Standard mode only accepts Microsoft-DB-signed binaries.
5. **Boot → Boot Option Priorities:** Windows Boot Manager should remain #1 until InterGenOS is installed and verified booting; then owner can set InterGenOS's shim entry as preferred.
6. Save & Exit.

### 4.3 Install InterGenOS (30-40 min, assuming install media ready)

1. Insert InterGenOS install USB (Forge ISO, built with the v2 engineering workstream from §3).
2. Boot, F8 at ASUS logo, select USB.
3. Forge TUI launches. Select the new 500 GB drive as the install target (Forge should enumerate both drives; pick the empty one by size + "unformatted" indicator).
4. Install mode: **whole disk** (no alongside mode needed — the second drive is dedicated).
5. Partition plan (auto-accepted defaults should be fine):
   - P1: ESP, 512 MB, FAT32
   - P2: root, 480 GB, ext4
   - P3: swap, 8 GB
6. System config: hostname `zephyrus11`, timezone, locale, user account.
7. **MOK setup screen:** generate a new MOK keypair; enter MOK enrollment password (write it down — needed at first boot).
8. NVIDIA driver: **yes** (detected RTX 3070 Ti). Select `nvidia-driver-open`.
9. Confirm plan → install.
10. Forge signs GRUB with MOK, signs kernel (already signed at build time with distro key, which will also be enrolled), installs shim-signed.

### 4.4 First boot — MOK enrollment (5 min)

1. Remove USB, reboot.
2. Firmware boot picker (F8) → InterGenOS shim entry (Forge should have registered it).
3. **MokManager appears** — blue screen: "Perform MOK management." Select "Enroll MOK" → "View key 0" (verify it's the InterGenOS key you expect) → "Continue" → enter MOK password from step 4.3.7 → "Yes" → "Reboot."
4. Second boot: shim now trusts MOK, verifies GRUB signature against MOK, GRUB loads, kernel verifies against enrolled distro key, boot succeeds.
5. Log in. `mokutil --sb-state` → `SecureBoot enabled`. `mokutil --list-enrolled` → shows InterGenOS MOK + distro key.

### 4.5 Windows verification (5 min)

1. Reboot. Firmware boot picker → Windows Boot Manager.
2. Windows boots normally.
3. In Windows, `chkdsk C: /scan` from elevated prompt — confirm zero changes to the NTFS layout.
4. Reboot back to InterGenOS.

### 4.6 NVIDIA verification (5 min)

Back in InterGenOS:

```
nvidia-smi
# Expected: "NVIDIA GeForce RTX 3070 Ti Laptop GPU", 8192 MiB VRAM, driver 560.x (or current)

lsmod | grep nvidia
# Expected: nvidia, nvidia_uvm, nvidia_modeset, nvidia_drm — all loaded

cat /proc/driver/nvidia/version
# Expected: kernel module version matches driver version, "open" variant indicated

dmesg | grep -i "nvidia\|secure"
# Expected: no unsigned-module rejections, no secure-boot warnings for nvidia modules
```

### 4.7 9B GPU benchmark setup (30-45 min for build + download)

```bash
# CUDA already installed via nvidia-driver-open meta-package
nvcc --version    # verify CUDA toolkit 12.6+

# Build llama.cpp CUDA
git clone https://github.com/ggerganov/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON
cmake --build build --config Release -j

# Fetch model from HP laptop or Ubuntu desktop
scp christopher@192.168.1.199:/mnt/intergenos/models/Qwen3.5-9B-Q4_K_M.gguf ~/models/

# Launch llama-server with full GPU offload
./build/bin/llama-server \
  --model ~/models/Qwen3.5-9B-Q4_K_M.gguf \
  --port 8080 --ctx-size 8192 --parallel 1 --reasoning off --jinja \
  --n-gpu-layers 99
```

Then claude-laptop or claude-main runs the 112-conversation test suite against `http://zephyrus11:8080` and records R28-9B-GPU as the GPU-tuned 9B baseline that the CPU-only R28 deferred.

---

## 5. Partition Layout (new drive only; old drive completely untouched)

### 5.1 Drive 2 layout

| # | Start | Size | Type | Mount | Notes |
|---|---|---|---|---|---|
| 1 | 1 MiB | 512 MB | ESP (FAT32) | `/boot/efi` | Contains shim-signed, signed GRUB, and any `loader/` configs |
| 2 | 513 MB | 480 GB | ext4 | `/` | InterGenOS root |
| 3 | 480.5 GB | 8 GB | swap | (swap) | Dedicated swap partition |
| (free) | 488.5 GB | ~11 GB | — | — | Reserved for future `/home` split or LUKS headroom |

Why 11 GB free: ext4 + swap don't precisely fit 500 GB after FAT32 overhead and partition alignment. Leaving ~11 GB unallocated gives us space to add a `/home` or LUKS partition later without backup-and-restore.

### 5.2 Drive 1 layout (UNCHANGED — documented for reference only)

| # | Size | Purpose |
|---|---|---|
| 1 | 272 MB | ESP (Windows) |
| 2 | 16 MB | MSR |
| 3 | 930 GB | Windows C: ("OS") |
| 4 | 1 GB | Windows WinRE |
| 5 | 23.6 GB | ASUS RESTORE |
| 6 | 200 MB | Recovery 2 |

**None of these partitions are touched.** InterGenOS's ESP is on Drive 2. Drive 1's ESP contains only Microsoft entries.

### 5.3 `/etc/fstab` (Drive 2 only)

```
# <device>               <mount>     <type>  <options>                   <dump> <pass>
UUID=<root-uuid>          /           ext4    defaults,noatime             1      1
UUID=<esp-uuid>           /boot/efi   vfat    defaults,umask=0077          0      2
UUID=<swap-uuid>          none        swap    sw                           0      0
```

InterGenOS never mounts Drive 1. os-prober peeks at Drive 1's ESP during `grub-mkconfig` but that's read-only and the entry it emits just chainloads Windows's bootmgfw — no mount, no write.

---

## 6. Boot Chain Architecture

```
UEFI firmware (Secure Boot ENABLED)
  │
  ├── Boot entry "InterGenOS" → points to shim-signed on Drive 2 ESP
  │     │
  │     └── shim-signed verifies against Microsoft vendor DB → LOADS
  │            │
  │            └── shim loads GRUB (Drive 2) → verifies GRUB signature against MOK
  │                   │
  │                   └── GRUB loads kernel → kernel verifies (signed, distro key in MOK)
  │                          │
  │                          └── kernel loads modules → verifies each against MOK
  │                                 │
  │                                 └── nvidia-driver-open module loads (DKMS-signed) → OK
  │
  └── Boot entry "Windows Boot Manager" → points to bootmgfw on Drive 1 ESP
        │
        └── firmware verifies bootmgfw against Microsoft DB → LOADS → Windows boots
```

**Three independent signature checks** for InterGenOS: shim → GRUB, GRUB → kernel, kernel → modules. **Two for Windows** (Microsoft's chain). All checks pass under Secure Boot Enabled.

---

## 7. Post-Monday Followups

These are intentionally out of scope for Monday but architecturally important:

### 7.1 TPM2 attestation

TPM is present on the Zephyrus (verified via earlier `Get-Tpm` — values not fully captured but TPM is listed as a capability). Post-Monday work:

- Package `systemd-cryptenroll` integration for LUKS rollout
- Set up PCR policies for measured boot: PCR 0-7 (firmware + bootloader + kernel command line) capture, sealed to TPM
- Attestation API: expose the current PCR hash set via a systemd service so another machine (or InterGen itself) can query "are you still in the state I expect?"

### 7.2 Post-quantum crypto readiness

- Track liboqs upstream; integrate once PQ algorithms are in mainline OpenSSL
- GRUB / shim / kernel signing: currently ECDSA-P256 or RSA-2048; plan migration to Dilithium or similar when upstream signs support it
- This is probably a 6-18 month horizon, not urgent, but the boot chain is architected so the signing algorithm is a swap, not a rebuild

### 7.3 Continuous attestation hook

Glasswing's third pillar is "continuous attestation." InterGenOS can participate by:

- Publishing the measured-boot hash at each boot
- Tracking package manifest hashes (pkm already tracks these — SQLite DB has the data)
- Exposing both via a local attestation service that tools like Glasswing participants can query

This is a real InterGenOS product feature, not install-time work. Flagging it so the current install doesn't lock out future work.

---

## 8. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Slot 2 BIOS detection fails | Low (firmware is current) | Low | Reseat drive, power-cycle, firmware update if available |
| shim-signed not in Microsoft DB for ASUS firmware | Low | High | Use Custom Mode Secure Boot + enroll Microsoft UEFI CA explicitly if needed |
| MOK enrollment fails (wrong password at MokManager) | Medium (user error) | Medium | Forge writes MOK enrollment password to a temp file at install time; display at post-install screen; user can retry via `mokutil --import` from a live USB |
| NVIDIA module fails signature verify after kernel upgrade | Medium (DKMS + sign hooks are fiddly) | Medium | Post-install verification script runs after every kernel upgrade; if module fails sign-check, drop to emergency shell with clear error |
| User accidentally selects Drive 1 as install target | Low | **Catastrophic (wipes Erica's Windows)** | Forge TUI must show drive labels, sizes, existing partition info, and require an explicit typed confirmation ("DESTROY nvme0n1") for drives that have existing partitions. Never auto-accept destructive targets. |
| Windows stops booting after Secure Boot config change | Very low | High | Windows Boot Manager on Drive 1 is already Microsoft-DB-signed; our changes don't touch Drive 1. If Windows fails, check firmware boot order. |
| Drive 2 fails within warranty | Low | Medium (reinstall) | Standard NVMe warranty 5 years; InterGenOS install is reproducible from USB in ~45 min |

---

## 9. Open Questions for Owner (v2)

1. **Forge engineering workstream — who owns it?** My lean: claude-main owns packaging + Forge code; claude-laptop owns VM end-to-end test harness; claude-windows stays on dual-boot research + orientation until Monday. Agree?
2. **Distro signing key storage.** Where does the InterGenOS project's signing private key live? (VPS secrets vault? Hardware token? Ephemeral per-build?) This matters long-term for supply-chain integrity — a compromised signing key forces a distro-wide re-sign.
3. **Kernel CONFIG_MODULE_SIG_FORCE.** Do we want it set? If yes (recommended), unsigned modules cannot load at all. If no, unsigned modules load but are reported. Per "no variance from most-secure-forward" — yes, force. Confirm?
4. **Microsoft UEFI CA pre-enrollment.** Some distros pre-enroll the Microsoft UEFI CA (for Windows) in their MOK-style tooling to smooth dual-boot. For InterGenOS, this is opt-in per owner preference — convenience vs. trust-root minimization. My lean: leave it off, document that Windows dual-boot requires the firmware's native MS DB (which is there on any Windows 11 machine anyway).
5. **Continuous attestation roadmap.** Is this a v1.0 InterGenOS feature, or post-1.0? Architecture notes in §7.3 work either way; implementation timing is your call.

---

## 10. Sources

Glasswing / Mythos context:

- [Project Glasswing — Anthropic](https://www.anthropic.com/project/glasswing)
- [Project Glasswing: Securing critical software for the AI era](https://www.anthropic.com/glasswing)
- [Anthropic's Mythos Preview and the End of a Twenty-Year Cybersecurity Equilibrium](https://postquantum.com/security-pqc/anthropic-mythos-preview-ai-offensive-security/)
- [Six Reasons Claude Mythos Is an Inflection Point for AI and Global Security (CFR)](https://www.cfr.org/articles/six-reasons-claude-mythos-is-an-inflection-point-for-ai-and-global-security)

Secure Boot + shim + MOK on Linux:

- [Dual boot with Windows — ArchWiki](https://wiki.archlinux.org/title/Dual_boot_with_Windows)
- [Enabling Secure Boot with Linux and Windows Dual-Boot Setup](https://burakberk.dev/complete-guide-enabling-secure-boot-with-linux-and-windows-dual-boot-setup/)

NVIDIA on Linux (open kernel modules):

- [NVIDIA — ArchWiki](https://wiki.archlinux.org/title/NVIDIA)
- [NVIDIA GeForce RTX 3070 Ti Linux Performance Review (Phoronix)](https://www.phoronix.com/review/nvidia-rtx3070ti-linux)

Zephyrus M16 hardware:

- [ASUS ROG Zephyrus M16 SSD & RAM Upgrades (Crucial)](https://www.crucial.com/compatible-upgrade-for/asus/rog-zephyrus-m16)
- [ROG Zephyrus M16 disassembly and upgrade options (LaptopMedia)](https://laptopmedia.com/highlights/inside-asus-rog-zephyrus-m16-gu603-disassembly-and-upgrade-options/)

Internal references:

- `dual_boot_zephyrus_playbook_2026-04-18_v1_alongside.md` — predecessor doc (pre-Glasswing posture)
- `/mnt/intergenos/installer/backend/*.py` — current Forge v0.1.0 (changes required per §3.2)
- `feedback_glasswing_security_posture.md` — the rule this playbook is an application of
