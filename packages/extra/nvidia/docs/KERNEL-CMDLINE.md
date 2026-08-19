# NVIDIA on InterGenOS — Kernel Cmdline Reference

This document covers the kernel cmdline parameters the InterGenOS NVIDIA
package ships by default + the broader cmdline.d framework users can
extend.

## Why kernel cmdline (and not modprobe.d)

InterGenOS exists to give you a system you understand, can modify, and
can trust. Module options that affect boot behavior live on the kernel
cmdline because the most discoverable place to audit "what's loaded and
how" is `cat /proc/cmdline` — so every load-bearing flag goes there.
`/etc/modprobe.d/` is the conventional Linux answer, but that convention
is exactly the kind of "you have to know to look here" hidden layer
InterGenOS avoids, regardless of how conventional it may be.

Module-load *policy* (which modules are allowed to load at all) still
lives in `/etc/modprobe.d/` — that's the correct layer for it. See
`MODPROBE-OPTIONS.md` for the modprobe-layer config (nouveau blacklist,
optional extras).

## Default-shipped cmdline fragment

`/etc/kernel/cmdline.d/40-nvidia.conf`:

```
nvidia-drm.modeset=1 nvidia-drm.fbdev=1
```

### `nvidia-drm.modeset=1`

Enables Kernel Mode Setting (KMS) for the NVIDIA driver. Required for
Wayland compositors on NVIDIA — Wayland expects KMS-mode-set display.

GDM checks `/sys/module/nvidia_drm/parameters/modeset` and falls back to
X11 if it reads `N`. With `modeset=1`, it reads `Y` and GDM picks
Wayland.

### `nvidia-drm.fbdev=1`

Provides a kernel framebuffer device for the NVIDIA driver. Required on
Linux 6.11+; InterGenOS ships kernel 6.18.10 so this is non-negotiable.

## How cmdline.d fragments get merged

`linux-kernel/hooks/post-install.sh` reads every `*.conf` file under
`/etc/kernel/cmdline.d/` in lexical filename order, strips comments and
blank lines, and appends the merged content to the base cmdline at
`/etc/kernel/cmdline`. The final cmdline is embedded in the UKI's signed
`.cmdline` section.

Filename prefixes (`40-`, `50-`, etc.) set ordering — useful if a later
flag needs to override an earlier one. The nvidia package uses `40-` to
sit comfortably between the system base (which ships nothing in
cmdline.d by default) and user customization (`90-user.conf` style).

## How users add their own cmdline params

Drop a file at `/etc/kernel/cmdline.d/90-user.conf` (or any name; sort
order applies). For example, to enable verbose kernel logging during a
debug session:

```
# /etc/kernel/cmdline.d/90-user.conf
loglevel=7
```

Then trigger a UKI rebuild by reinstalling the kernel package, which
re-runs its post-install hook:

```sh
sudo pkm reinstall linux-kernel
```

The new cmdline is signed into the UKI and active on next boot. To
revert, delete or empty the `.conf` file and reinstall the kernel again.

## Inspecting the active cmdline

At runtime, the canonical view is:

```sh
cat /proc/cmdline
```

The UKI's embedded cmdline can be inspected with:

```sh
objcopy --dump-section .cmdline=/dev/stdout /boot/efi/EFI/Linux/intergenos-<kver>.efi
```

## When the UKI rebuilds

Any change to `/etc/kernel/cmdline` or any file under
`/etc/kernel/cmdline.d/` triggers a UKI rebuild when:

- A kernel package post-install hook runs (e.g., kernel update)
- A package that ships a cmdline.d fragment installs or upgrades
- The user runs `sudo pkm reinstall linux-kernel` manually (this re-runs
  the kernel post-install hook)

The UKI rebuild is logged to `/var/log/intergen-kernel-postinstall.log`,
including the full `ukify` output, so you can inspect exactly what was
embedded in the signed UKI.

## Reverting to nouveau (uninstalling nvidia)

`pkm remove nvidia` removes the package's shipped files — including
`/etc/kernel/cmdline.d/40-nvidia.conf` and
`/etc/modprobe.d/nvidia-nouveau-blacklist.conf` — as part of its standard
file-removal walk. Before that walk, `pkm` runs the package's pre-remove
hook (`/var/lib/pkm/hooks/nvidia/pre-remove`), which stops the NVIDIA
services, unloads the kernel modules, and purges the built `.ko` files
under `/lib/modules/*/extra/nvidia/`. Those `.ko` files were compiled on
this machine after the package was installed, so no manifest records them
and the file-removal walk cannot see them; the hook is what clears them.

The UKI is NOT rebuilt as part of the removal. Until it is rebuilt, the
signed `.cmdline` section still carries the nvidia kernel parameters, so
the first boot after the removal passes `nvidia-drm.modeset=1` to a kernel
with no nvidia module to consume it — harmless, but stale. The next kernel
update rebuilds the UKI and clears them. To clear them immediately:

```
sudo /var/lib/pkm/hooks/linux-kernel/post-install
```

On next boot, nouveau is the active GPU driver.
