# NVIDIA driver-open packaging research — InterGenOS

**Date:** 2026-04-20
**Status:** research only — no implementation, no code, no decision

## TL;DR

NVIDIA's `open-gpu-kernel-modules` project (GPLv2/MIT dual, released May 2022) gives us a kernel-side story we can actually ship under our security-first posture. The userspace (libGL, libcuda, libnvidia-*) stays proprietary — that part needs a download helper, not a shipped package. This doc covers package split, DKMS + MOK signing chain, Wayland/hybrid-graphics, and prior art. Recommendation at the bottom.

**Key inputs to owner decision:**
1. Userspace: redistribute under NVIDIA Linux EULA vs download-helper?
2. Package naming: follow Arch (`nvidia-open-dkms`) or split fine-grained (`nvidia-open-kmod` + `nvidia-userspace-fetch`)?
3. Driver branch: Production (long support) vs New Feature (latest)?

---

## Why driver-open, not proprietary or nouveau

**Proprietary NVIDIA driver** (nvidia-drm, closed kernel + userspace):
- Kernel modules are binary blobs from NVIDIA. Cannot audit. Cannot sign with MOK cleanly without NVIDIA's signing tooling.
- Security violation: kernel-space code we can't read is exactly the attack surface advanced adversaries target first.
- User-control violation: user does not control code running in ring 0.

**Nouveau** (in-tree `CONFIG_DRM_NOUVEAU`):
- Fully open, in-kernel, already part of our kernel config (ref: `kernel_config_strategy_2026-04-01.md` line 102).
- Reverse-engineered. Historically weak on power management, CUDA-less, lagging on new silicon.
- Acceptable fallback for pre-Turing cards and headless boxes. Not the daily-driver story for RTX 30/40xx.

**Driver-open** (`open-gpu-kernel-modules`, GPLv2/MIT):
- NVIDIA's own kernel-side source release. First-party, feature-parity with proprietary kernel, CUDA-capable.
- Can be built out-of-tree via DKMS, signed with user's MOK, loaded under `CONFIG_MODULE_SIG_FORCE=y` (already planned in A6 per `signing_key_custody_2026-04-18.md`).
- Userspace still proprietary (same .so files as proprietary driver). That boundary lives outside the kernel — acceptable from a security-posture standpoint, since ring 0 is auditable.

**Why this balance works for InterGenOS:**
- Kernel side is open-source and signed → attacker can't tamper with ring-0 code without MOK compromise.
- Userspace is proprietary but runs in userspace — sandboxed by kernel's own enforcement.
- User retains control: can unload module, run nouveau instead, audit the open kernel source.

---

## Upstream sources & licensing

- **Repo:** https://github.com/NVIDIA/open-gpu-kernel-modules
- **License (kernel):** dual GPLv2 OR MIT (choose at build). Most distros build GPLv2 to match kernel.
- **License (userspace libs):** NVIDIA Linux EULA (proprietary, not GPL-compatible).
- **License (firmware blobs):** proprietary NVIDIA, redistributable via `linux-firmware` under its own LICENCE.nvidia.
- **Release cadence:** tags matching proprietary driver versions (e.g., 555.58.02, 550.135). New-feature + Production branches.

**GPL compliance:** the kernel modules are clean — we can ship them alongside the GPLv2 kernel without taint concerns. `MODULE_LICENSE("Dual MIT/GPL")` is declared in the source.

---

## Hardware compatibility matrix

| Architecture | Example GPUs | driver-open support | Notes |
|---|---|---|---|
| Hopper | H100 | 525+ | Data center |
| Ada Lovelace | RTX 40xx | 535+ | |
| Ampere | RTX 30xx (incl. **owner's 3070 Ti**), A-series | 515+ | Covers Zephyrus M16 test bed |
| Turing | RTX 20xx, GTX 16xx | 515+ | |
| Volta | Titan V, V100 | **NOT SUPPORTED** | Proprietary only |
| Pascal | GTX 10xx | **NOT SUPPORTED** | Proprietary or nouveau |
| Maxwell, Kepler | older | **NOT SUPPORTED** | Nouveau only |

**Implication for InterGenOS target hardware:**
- Owner's Zephyrus M16 (RTX 3070 Ti, Ampere): supported ✓
- HP laptop (Intel integrated, no discrete NVIDIA): n/a
- Ubuntu desktop (AMD RX 7900 XT): n/a — AMD-side
- Any pre-Turing machine: must use proprietary driver OR nouveau — architectural gate to document in installer.

---

## Component inventory

What ships when NVIDIA driver-open is installed:

### Kernel modules (driver-open, redistributable under GPLv2/MIT)
- `nvidia.ko` — core GPU driver
- `nvidia-modeset.ko` — KMS (kernel mode setting)
- `nvidia-drm.ko` — DRM interface (required for Wayland with `nvidia-drm.modeset=1`)
- `nvidia-uvm.ko` — Unified Virtual Memory (for CUDA)
- `nvidia-peermem.ko` — GPUDirect (optional, data-center)

### Firmware (proprietary, redistributable via linux-firmware)
- `gsp_ga10x.bin`, `gsp_tu10x.bin`, etc. — GSP (GPU System Processor) RM firmware
- Lives in `/lib/firmware/nvidia/<version>/`
- Already aligned with our `linux-firmware` package path

### Userspace libraries (proprietary, EULA-gated)
- `libnvidia-gl-*.so` — OpenGL ICD
- `libGLX_nvidia.so` — GLX provider
- `libEGL_nvidia.so` — EGL provider
- `libcuda.so` — CUDA runtime
- `libnvidia-ml.so` — NVML (monitoring)
- `libnvidia-encode.so`, `libnvidia-opticalflow.so`, `libnvidia-decode.so` — codec acceleration
- `libvdpau_nvidia.so` — VDPAU
- `libnvidia-vulkan-producer.so` — Vulkan-WSI bridge
- `libnvidia-ngx.so`, `libnvidia-nvvm.so`, etc. — various compute/ML bits

### Tools
- `nvidia-smi` — monitoring (proprietary binary)
- `nvidia-settings` — X11 settings GUI (MIT-licensed itself, but depends on proprietary libs)
- `nvidia-persistenced` — systemd daemon for persistence mode (open source, GPL)
- `nvidia-xconfig` — X11 config generator (mostly open)
- `nvidia-installer` — NVIDIA's own installer. We do NOT use this.

---

## Package design

Two viable splits. Both have prior art. Recommendation below.

### Option A: Fine-grained split (InterGenOS-preferred per HG)

**Core tier (redistributable, built from source):**
- `nvidia-open-kmod` — DKMS package, kernel modules only, sources fetched from `github.com/NVIDIA/open-gpu-kernel-modules` tag
- `linux-firmware-nvidia` — firmware split from main linux-firmware (optional split — may leave in linux-firmware)

**Extra tier (proprietary userspace via fetch helper):**
- `nvidia-userspace` — fetches NVIDIA `.run` installer, extracts userspace libs only (no kernel modules, no installer machinery)
- `nvidia-utils` — nvidia-smi, nvidia-persistenced (open) + shims
- `nvidia-settings` — optional, X11 settings GUI

**Rationale:**
- Keeps the redistributable kernel-side in core. User gets driver-open even if they decline userspace.
- Userspace fetch helper matches our approved application helper pattern (56 apps: 42 source-build, 8 fetch helpers, 6 deferred).
- User can inspect the fetch helper, see exactly what URL is hit and what sha256 is verified. Prime Directive honored.
- Signing chain: DKMS fires `/etc/dkms/sign_helper.sh` which invokes sbsign with `/var/lib/intergen/mok/mok.key` + `mok.crt` (paths per `installer/backend/mok.py` MOK_DIR constant).

### Option B: Coarse split (Arch-style)

- `nvidia-open-dkms` — kernel modules + required userspace bundled together (redistributed under NVIDIA Linux EULA)
- `nvidia-utils` — nvidia-smi + nvidia-persistenced
- `nvidia-settings` — optional

**Rationale:**
- Matches Arch AUR-land expectations. Users coming from Arch will find this familiar.
- Simpler dependency graph.
- Requires us to take a position on EULA redistribution (NVIDIA's EULA permits it with terms — needs legal review, and "review" is not this agent's job).

### Recommendation

**Option A, with the fetch helper for userspace.** Three reasons:
1. Security-first posture ("every package decision is a security decision"): the clearer the open/proprietary boundary, the easier to audit and to reason about attack surface.
2. Prime Directive: user decides whether to accept the userspace fetch. Headless servers or compute-only boxes may want kernel modules without GL libraries at all (run pure CUDA against libcuda as an overlay).
3. Matches our existing helper pattern for VS Code, Claude Code, etc. One consistent story, no special case for NVIDIA.

Owner may weigh simplicity (Option B) vs the security/user-control clarity of Option A. This is an owner decision — flagging, not picking.

---

## DKMS + MOK signing chain

This is the piece that makes driver-open load under `CONFIG_MODULE_SIG_FORCE=y`. Already designed in `signing_key_custody_2026-04-18.md` §D1-1 / §D1-6. Summary applied to the package layer:

**DKMS config** (`/usr/src/nvidia-open-<version>/dkms.conf`):
```
PACKAGE_NAME="nvidia-open"
PACKAGE_VERSION="<driver-version>"
BUILT_MODULE_NAME[0]="nvidia"
BUILT_MODULE_NAME[1]="nvidia-modeset"
BUILT_MODULE_NAME[2]="nvidia-drm"
BUILT_MODULE_NAME[3]="nvidia-uvm"
BUILT_MODULE_NAME[4]="nvidia-peermem"
AUTOINSTALL="yes"
POST_BUILD="sign_helper.sh"
```

**Sign helper** (`/etc/dkms/sign_helper.sh` — InterGenOS ships this):
```sh
#!/bin/sh
# Sign the just-built NVIDIA module with the per-install MOK.
# Key paths match installer/backend/mok.py MOK_DIR constant.
MOK_KEY=/var/lib/intergen/mok/mok.key
MOK_CERT=/var/lib/intergen/mok/mok.crt
for mod in "$@"; do
    /usr/sbin/sbsign --key "$MOK_KEY" --cert "$MOK_CERT" \
                     --output "$mod" "$mod" || exit 1
done
```

**Ordering considerations:**
1. Forge installer generates MOK at install time → enrollment queued via mokutil.
2. User reboots, MokManager prompts, user enters password, MOK lands in kernel's secondary trusted keyring.
3. On next kernel install/upgrade (or DKMS rebuild), the sign_helper fires post-build.
4. `modprobe nvidia` succeeds under `MODULE_SIG_FORCE=y` because the signature chains to the enrolled MOK.

**Gotcha: first NVIDIA install before MOK is enrolled.**
If user installs `nvidia-open-kmod` in the same session that enrolls the MOK, DKMS signs the module with the MOK private key but the kernel hasn't loaded the MOK into trust yet. Module fails to load until after reboot + MokManager. Not a bug — just the expected first-boot flow. Document in installer TUI + post-install message.

**Claude-laptop Day 2 test:** verify `modinfo -F sig_hashalgo nvidia` reports `sha256` and `modinfo -F signer nvidia` reports our MOK CN. Worth adding to Class 1 harness for NVIDIA-present systems.

---

## Wayland & display-server considerations

**Driver 555+ (May 2024 onward):** native GBM support. Works with mutter/GNOME Shell on Wayland without EGLStreams shim. Required env: `WLR_NO_HARDWARE_CURSORS=1` may help on some older Wayland compositors but not for mutter.

**Driver 550 and older:** EGLStreams only. GNOME mutter had explicit support for this via `NvidiaDRM-KMS` logic but it was a shim. Avoid — target 555+.

**Required kernel params / modprobe config for Wayland KMS:**
```
# /etc/modprobe.d/nvidia.conf
options nvidia-drm modeset=1 fbdev=1
```
- `modeset=1` — required for KMS → required for Wayland.
- `fbdev=1` — provides a kernel framebuffer device (added in 560, optional for Wayland but improves console + early boot).

**Pin InterGenOS default driver branch to 560+** for cleanest Wayland story. Use Production Branch (555+) if conservative. Document in the package's default.

**GDM on Wayland:** works with 555+ driver-open on Ampere+. If GDM falls back to X11, check `nvidia-drm.modeset=1` is set and `/sys/module/nvidia_drm/parameters/modeset` reads `Y`.

---

## Hybrid graphics (PRIME offload) — Zephyrus M16 specific

Owner's Zephyrus has Intel integrated + RTX 3070 Ti. BIOS MUX modes:
1. **iGPU-only** — NVIDIA powered off. Best battery. No discrete GPU access.
2. **Hybrid (default)** — both active. Intel drives display, NVIDIA offloads on demand.
3. **Discrete-only** — NVIDIA drives display directly. Best performance, worst battery.

**For InterGenOS on Hybrid mode:**
- Both drivers must load: `i915` (in-tree) + `nvidia` (via DKMS-open, MOK-signed).
- Offload via environment variables:
  ```
  __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia <command>
  ```
- Wayland: `prime-run`-style wrapper or GNOME's "Run with discrete GPU" shortcut (mutter surfaces this when both drivers are present).

**For InterGenOS on Discrete-only mode:**
- Only `nvidia` needed. Simpler. GDM must land on NVIDIA from boot.

**Reverse PRIME (NVIDIA render → Intel display) for Hybrid:**
- Works out-of-box with 555+ driver-open. No manual xorg.conf required.
- Pre-555: needed xorg.conf with `Option "AllowNVIDIAGPUScreens"` — avoid pre-555.

---

## Kernel config alignment

Already aligned with Forge SB A6 work per `signing_key_custody_2026-04-18.md`:
- ✓ `CONFIG_MODULE_SIG_FORCE=y` — shipped (A6 commit 74054e4)
- ✓ `CONFIG_SECONDARY_TRUSTED_KEYRING=y` — planned (A6)
- ✓ `CONFIG_MODULE_SIG_HASH="sha256"` — kernel default
- ✓ `CONFIG_DRM_NOUVEAU=m` — already enabled per `kernel_config_strategy_2026-04-01.md` — lets users fall back if they decline proprietary userspace.

No new Kconfig symbols required for driver-open. The DKMS machinery is pure userspace + module loader.

**Potential future tightening:**
- `CONFIG_MODULE_SIG_ALL=y` — all in-tree modules auto-signed at build. Already default under `MODULE_SIG_FORCE=y`.
- `CONFIG_SYSTEM_REVOCATION_KEYS=""` — leave empty; we don't revoke through the kernel keyring.

---

## Prior art — per-distro package layouts

### Arch Linux
- `nvidia-open-dkms` — kernel modules (DKMS)
- `nvidia-utils` — userspace (includes libs, tools). Redistributes under NVIDIA EULA.
- `nvidia-settings` — optional GUI.
- Clean, compact, minimal options. Reference: https://wiki.archlinux.org/title/NVIDIA

### Fedora (RPM Fusion, nonfree)
- `akmod-nvidia-open` — kernel modules via akmod (Fedora's equivalent of DKMS)
- `xorg-x11-drv-nvidia-libs` — userspace
- Plus `kmod-nvidia-open` for pre-built binary. Fedora has both source-rebuild + binary paths.

### Debian (non-free)
- `nvidia-open-kernel-dkms` — kernel modules
- `nvidia-driver` — userspace + dependencies (bundles everything)
- `firmware-nvidia-gsp` — GSP firmware split out

### Ubuntu
- `linux-modules-nvidia-open-<ver>-server-<flavor>` — pre-built kernel modules (not DKMS for server/cloud)
- `nvidia-driver-<ver>-open` — meta-package pulling kernel + userspace
- Ubuntu ships pre-built binaries for their certified kernel ABIs. Not directly applicable to our DKMS-first approach.

### Gentoo
- `x11-drivers/nvidia-drivers` with USE flag `kernel-open`
- Uses Gentoo's `linux-mod.eclass` for DKMS-equivalent builds
- Extremely fine-grained USE flag control (closest philosophical match to our approach).

### NixOS
- `nvidiaPackages.open` — composable package
- Declarative module signing via `boot.kernelPackages.nvidiaPackages`
- Interesting reference for how a security-minded distro handles module signing.

---

## Testing plan (Day 2+)

Once package templates exist and a build run produces the `nvidia-open-kmod` DKMS package:

1. **Signature chain verification** (add to Class 1 harness):
   - `modinfo -F signer $(modprobe --resolve-alias nvidia)` → expected: MOK CN.
   - `modinfo -F sig_hashalgo nvidia` → expected: `sha256`.
   - Verify `lsmod | grep nvidia` loads cleanly under `MODULE_SIG_FORCE=y`.

2. **Smoke tests** (add to Class 2 harness — hardware-dependent):
   - `nvidia-smi` reports GPU, driver version, memory.
   - `glxinfo -B | grep "OpenGL renderer"` reports NVIDIA (hybrid: after `__NV_PRIME_RENDER_OFFLOAD=1`).
   - `vulkaninfo | grep deviceName` reports NVIDIA.
   - `nvidia-drm.modeset=1` confirmed in `/sys/module/nvidia_drm/parameters/modeset`.

3. **Wayland validation** (Zephyrus post-install):
   - `echo $XDG_SESSION_TYPE` = `wayland` under GDM + GNOME.
   - Full-screen video playback without tearing.
   - PRIME offload works: launching a game with env var uses NVIDIA, terminal apps use Intel.

4. **DKMS rebuild scenario** (simulate kernel upgrade):
   - `dkms autoinstall` on new kernel → module rebuilt, signed, loads.
   - `dmesg | grep -i "module verification"` shows no signature errors.

---

## Open questions for owner

| # | Question | Context | Suggested default |
|---|---|---|---|
| 1 | Fetch helper vs redistribution for userspace? | Security + user-control clarity vs Arch-style simplicity | Fetch helper (Option A) |
| 2 | Driver branch — Production or New Feature? | Stability vs latest Wayland/features | Production 560+ (ships clean Wayland + fbdev) |
| 3 | Ship `nvidia-settings` by default? | X11 config GUI; less useful on Wayland | No — make it extra-tier optional |
| 4 | Ship `nvidia-persistenced` by default? | Compute workloads; less useful for desktop | No — extra-tier, user opts in |
| 5 | Declare hardware gate in installer? | Users with Pascal/Maxwell get a misleading install otherwise | Yes — TUI detects GPU at install, routes to nouveau if pre-Turing |
| 6 | Policy on pre-enrollment install attempts? | DKMS will sign; module fails to load until MOK is enrolled at first boot | TUI installs the package but warns: "module loads after MokManager enrollment" |

---

## Cross-references

- `docs/research/installer/signing_key_custody_2026-04-18.md` — D1-1 ephemeral kernel signing, D1-6 MOK flow, A6 kernel config (authoritative for signing chain)
- `installer/backend/mok.py` — `MOK_DIR`, `sign_efi_binary` (key paths used by DKMS sign helper)
- `installer/backend/bootloader.py` — signed boot chain (shim → GRUB → kernel → modules)
- `docs/research/kernel/kernel_config_strategy_2026-04-01.md` — nouveau base config
- `docs/research/build_system/igr_specification_2026-04-10.md` — GPU-NVIDIA package slot
- Project approved-app-list memo — established fetch-helper pattern
- Project security-alignment doc — every build decision is a security decision

---

## What this doc is NOT

- A package template. No YAML, no build.sh.
- A build order decision.
- A commit to Option A vs Option B — that's an owner decision.
- A finalized version pin.
- A legal review of NVIDIA Linux EULA redistribution.

When the owner greenlights a direction, **package template work becomes a separate task** (likely mid-week after owner returns to desk, as originally queued). This doc is the upfront research so that implementation goes quickly and doesn't rediscover these constraints.
