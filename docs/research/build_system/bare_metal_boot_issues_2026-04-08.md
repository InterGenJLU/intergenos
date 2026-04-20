# Bare Metal Boot Issues — 2026-04-08 (HP Laptop, USB Boot)

## Issues Hit (all need automation fixes)

### 1. Kernel panic: unable to mount root fs on unknown-block(0,0)
- **Root cause:** Filesystem UUID (`UUID=`) not resolvable by kernel at boot time
- **Fix:** Use `PARTUUID=` (GPT partition UUID) instead of filesystem UUID
- **Also needed:** `rootwait` kernel parameter (waits indefinitely for USB enumeration)
- **Automation:** Update `create-image.sh` and GRUB config generation to use PARTUUID + rootwait

### 2. SSH host keys missing — sshd won't start
- **Root cause:** `ssh-keygen -A` never ran during image creation
- **Fix:** Run `ssh-keygen -A` in chroot during image setup
- **Automation:** Add to Ch.9 config or image creation script

### 3. christopher password not working (PAM auth failure)
- **Root cause:** `chpasswd` failed during image setup due to PAM error in chroot. Shadow entry created with `openssl passwd` directly, but had issues
- **Fix:** Ensure shadow-pam is properly configured before setting passwords, or use `chpasswd` inside a properly mounted chroot
- **Automation:** Fix password creation in `create-image.sh`

### 4. GDM not enabled (inactive/dead)
- **Root cause:** GDM service not enabled during build — `systemctl enable gdm` never ran
- **Fix:** Enable GDM in Ch.9 config phase or image creation
- **Automation:** Add `systemctl enable gdm` to chroot config

### 5. gnome-shell crashes: "Wrong ownership for /tmp/.X11-unix"
- **Root cause:** GDM greeter runs as uid 60578 (gdm-greeter), creates /tmp/.X11-unix owned by that uid. When user session starts, XWayland refuses the directory because ownership doesn't match
- **Fix:** Ensure `/tmp/.X11-unix` has correct ownership (root:root) and permissions (1777), or configure systemd-tmpfiles to manage it
- **Automation:** Add tmpfiles.d config:
  ```
  # /etc/tmpfiles.d/x11-unix.conf
  d /tmp/.X11-unix 1777 root root -
  ```

### 6. USB NIC shows "down" / "no carrier"
- **Status:** Not a bug — just needed ethernet cable plugged in. WiFi (wlo1) also available via RTW88 driver

## Things That Worked

- GRUB BIOS + EFI dual boot (with PARTUUID)
- Kernel 6.18.10 with i915 GPU driver (Intel HD Graphics)
- NetworkManager (active, managing interfaces)
- WiFi hardware detected (wlo1 visible)
- USB ethernet detected (enp0s20f0u3 visible)
- Mesa/OpenGL (gnome-shell created GBM renderer, EGL context)
- PipeWire/rtkit (high priority threads)
- gnome-keyring (unlocked on login)
- GNOME Shell 49.4 on Wayland
- Mutter 49.4 with atomic mode setting on i915
- linux-firmware loaded (RTW88, i915)

## Fixes Needed in Automation

All of these should be applied to `scripts/chroot-config-ch9.sh` or `scripts/create-image.sh`:

1. GRUB: Use `root=PARTUUID=... rootwait` instead of `root=UUID=...`
2. SSH: Run `ssh-keygen -A` during chroot config
3. Passwords: Fix chpasswd in chroot (ensure PAM is functional)
4. GDM: `systemctl enable gdm` during chroot config
5. tmpfiles: Create `/etc/tmpfiles.d/x11-unix.conf` for /tmp/.X11-unix
