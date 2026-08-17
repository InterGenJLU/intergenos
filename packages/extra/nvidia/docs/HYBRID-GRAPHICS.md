# NVIDIA on InterGenOS — Hybrid Graphics

Most NVIDIA laptops ship with a dual-GPU configuration: an Intel iGPU for
power-efficient desktop work + an NVIDIA dGPU for GPU-heavy workloads.
This is "Optimus" / "MUX-switchable" / "hybrid" graphics. This document
covers how the InterGenOS NVIDIA package interacts with hybrid hardware.

## BIOS modes

Most modern NVIDIA laptops expose three BIOS-level GPU configurations:

1. **Hybrid mode** (default): both GPUs active. Intel drives the display
   directly. NVIDIA renders to off-screen buffers and hands frames to
   Intel via PRIME. Best battery life. Supports PRIME offload.
2. **iGPU-only**: NVIDIA powered off entirely. No discrete GPU access.
   Best battery; no GPU acceleration on demanding workloads.
3. **dGPU-only** (often called "discrete mode" or "MUX dGPU"): NVIDIA
   drives the display directly. Best performance; worst battery.

InterGenOS's NVIDIA package supports all three modes:

| BIOS Mode    | Modules loaded         | Display driver | PRIME |
|--------------|------------------------|----------------|-------|
| Hybrid       | `i915` + `nvidia*`     | Intel via i915 | Yes   |
| iGPU-only    | `i915` only            | Intel via i915 | n/a   |
| dGPU-only    | `nvidia*` only         | NVIDIA         | n/a   |

## Default system-wide behavior

The package installs `/etc/environment.d/91-nvidia-wayland.conf`:

```
GBM_BACKEND=nvidia-drm
__GLX_VENDOR_LIBRARY_NAME=nvidia
```

These environment variables route the system-wide Wayland compositor +
all OpenGL/GLX applications to NVIDIA's libraries. On a hybrid laptop,
this means the dGPU is awake whenever the display is active — battery
drains faster.

This is the right default for users who installed NVIDIA because they
WANT the dGPU active. Hybrid users who want iGPU-default + NVIDIA-on-
demand should edit the file and remove the two environment variables.

## PRIME offload (NVIDIA-on-demand)

When the system-wide defaults route to Intel and NVIDIA is offload-only,
launching a specific app on NVIDIA is done via the `prime-run` wrapper
that ships with the package:

```
prime-run firefox
prime-run blender
prime-run steam
```

`prime-run` sets the per-app environment variables that tell libglvnd's
dispatch layer to route OpenGL + Vulkan calls to NVIDIA's libraries:

```
__NV_PRIME_RENDER_OFFLOAD=1
__GLX_VENDOR_LIBRARY_NAME=nvidia
__VK_LAYER_NV_optimus=NVIDIA_only
__NV_PRIME_RENDER_OFFLOAD_PROVIDER=NVIDIA-G0
```

GNOME 49 exposes this in the GUI: right-click a `.desktop` entry → "Launch
using Discrete Graphics Card." Mutter detects the dGPU and adds the env
vars before exec. `prime-run` is the CLI equivalent.

## How to switch to iGPU-default + NVIDIA-on-demand

For a hybrid laptop where you want iGPU to drive the desktop and NVIDIA
only on PRIME-launched apps:

```
# 1. Remove the system-wide NVIDIA defaults
sudo rm /etc/environment.d/91-nvidia-wayland.conf

# 2. Log out + log back in (the env vars are loaded by the session)

# 3. Launch GPU-heavy apps via prime-run
prime-run firefox
```

After this, `glxinfo -B` shows Intel and `prime-run glxinfo -B` shows
NVIDIA. The Wayland compositor (mutter) runs on Intel; only PRIME-launched
apps wake the dGPU.

## How to switch to NVIDIA-driven X11 (legacy)

For users on X11 instead of Wayland with a hybrid laptop, NVIDIA's X11
config at `/usr/share/X11/xorg.conf.d/10-nvidia-drm-outputclass.conf`
sets `PrimaryGPU "yes"` which makes NVIDIA the default Xorg rendering
target. Hybrid users on X11 who want iGPU primary should comment out
the `Option "PrimaryGPU"` line.

## Hardware-specific notes

### Ampere mobile (RTX 30xx laptops)

Some Ampere laptops have GSP firmware bugs that prevent the open kernel
modules from loading. Workaround:

```
echo 'options nvidia NVreg_EnableGpuFirmware=0' | sudo tee /etc/modprobe.d/nvidia-no-gsp.conf
```

Then rebuild the modules + reboot.

### Ada / Blackwell laptops

These newer architectures have working GSP firmware in driver 580. No
known issues with our default configuration.

### MUX vs dynamic switching

Some laptops support runtime MUX switching (BIOS-level GPU swap without
reboot). InterGenOS does not currently expose a UI for this — use your
laptop's vendor tool (e.g. Asus's `asusctl`, MSI's vendor tool) or the
BIOS configuration menu.

## Disabling the suspend / resume / hibernate services

The package ships three systemd services enabled by default that handle
GPU state save/restore across sleep transitions:

- `nvidia-suspend.service` — saves VRAM contents before system suspend.
- `nvidia-resume.service` — restores VRAM after resume.
- `nvidia-hibernate.service` — saves VRAM to swap at hibernate.

These are enabled by default because most laptop users expect lid-close
→ lid-open to "just work" — without these, suspend/resume on NVIDIA
typically leaves graphics broken or the system unable to wake.

If you're on a desktop tower that never suspends, or you want the
absolute minimum running daemons (security is not first, it is only —
fewer services is fewer attack surfaces), you can disable any or all
three:

```sh
sudo systemctl disable --now nvidia-suspend.service
sudo systemctl disable --now nvidia-resume.service
sudo systemctl disable --now nvidia-hibernate.service
```

Re-enable later with `systemctl enable --now` on the same unit names.

NVIDIA's upstream docs on the suspend/resume mechanism live at
[NVIDIA Linux driver suspend/resume](https://download.nvidia.com/XFree86/Linux-x86_64/580.159.04/README/powermanagement.html)
for full background on what each service actually does.

A future InterGenOS release will surface these toggles in the GNOME
Settings app directly (no terminal required) when an NVIDIA card is
detected — until then the commands above are the canonical control
path.
