# NVIDIA on InterGenOS — Modprobe Options Reference

This document covers the modprobe-layer configuration the InterGenOS
NVIDIA package ships + the optional modprobe options users can add.

For the kernel cmdline params (modeset, fbdev, etc.) see
[`KERNEL-CMDLINE.md`](KERNEL-CMDLINE.md). So you can understand and audit
your own machine, options that affect boot live on the cmdline where
they're visible at `cat /proc/cmdline`; modprobe.d is reserved for
module-load *policy* (blocking modules, setting non-boot module params).

## Default-shipped configuration

`/etc/modprobe.d/nvidia-nouveau-blacklist.conf`:

```
blacklist nouveau
options nouveau modeset=0
```

The two-line nouveau-blocker pattern is belt-and-suspenders:

- `blacklist nouveau` blocks `modprobe nouveau` (covers
  systemd-modules-load and udev module-load paths).
- `options nouveau modeset=0` covers the early-KMS auto-detection path
  that bypasses the blacklist.

If only the blacklist were set, an auto-detect path could still bring
nouveau up with full KMS attached, fighting nvidia for the framebuffer.

## Optional modprobe options

Add to `/etc/modprobe.d/nvidia-extras.conf` (or any name; modprobe reads
all `.conf` files under `/etc/modprobe.d/`).

### `options nvidia NVreg_EnableGpuFirmware=0`

**When**: Ampere mobile (RTX 30xx laptops) with broken GSP firmware.

**Effect**: Disables GSP firmware load; reverts to driver-resident
firmware.

**Cost**: Slightly higher driver memory footprint; some advanced
features (NVENC AV1 on some silicon) unavailable.

**Not for**: Desktop Ampere or any Ada/Blackwell hardware. They need
GSP.

### `options nvidia NVreg_PreserveVideoMemoryAllocations=1`

**When**: Documented for clarity. Default is already ON in driver 555+.

**Effect**: Holds VRAM contents across suspend, preventing display
corruption on resume. Required for working Wayland suspend.

### `options nvidia NVreg_DynamicPowerManagement=0x02`

**When**: Hybrid graphics laptops, aggressive battery savings.

**Effect**: D3 power-down of the dGPU when no app is using it. Reduces
idle power draw significantly. Default on most Optimus systems.

### `options nvidia-uvm uvm_disable_hmm=0`

**When**: Default; HMM enabled.

**Effect**: Heterogeneous Memory Management — required for CUDA Unified
Memory. Disable only if you've hit a specific HMM-related bug.

## Why some flags live in cmdline.d and others here

The split is by mechanism:

- **Kernel cmdline** (`/etc/kernel/cmdline.d/40-nvidia.conf`): flags the
  kernel reads at boot time and that affect early init — `nvidia-drm`
  modeset, fbdev. The kernel sees these before modprobe runs. Auditable
  at `cat /proc/cmdline`.

- **modprobe.d** (`/etc/modprobe.d/nvidia-*.conf`): flags processed by
  the userspace `modprobe` tool when a module is loaded after boot.
  Module *blacklists* (don't load module X) and module-runtime options
  for modules loaded post-init. Auditable at `lsmod` + `modinfo`.

If you're unsure where a flag belongs: kernel cmdline if it affects how
the driver initializes at boot, modprobe.d if it's about which modules
are blocked or how a module behaves once loaded.

## How to verify options are applied

After install + reboot:

```sh
cat /sys/module/nvidia_drm/parameters/modeset     # should be: Y (from cmdline)
cat /sys/module/nvidia_drm/parameters/fbdev       # should be: Y (from cmdline)
cat /sys/module/nvidia/parameters/NVreg_EnableGpuFirmware
cat /proc/cmdline                                  # full kernel cmdline
lsmod | grep -E 'nvidia|nouveau'                   # nouveau should be absent
```
