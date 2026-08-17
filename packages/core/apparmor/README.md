# InterGenOS AppArmor

This package builds **AppArmor v3.1.7** — the libapparmor C library, the
apparmor_parser binary, and the upstream profile substrate — and installs
the InterGenOS-specific profile additions on top.

It reflects the 2026-04-29 decision to use AppArmor as the InterGenOS
mandatory access control (MAC) framework.

## What this package compiles and installs

1. **libraries/libapparmor** — autotools, produces `libapparmor.so` and the
   `libapparmor.pc` pkg-config file. Consumed by systemd, polkit, dbus, and
   anything else that links `-lapparmor`.

2. **parser/** — Makefile-driven, produces:
   - `/usr/sbin/apparmor_parser` — the profile parser/loader
   - `/usr/sbin/aa-teardown` — profile-removal helper
   - `/usr/lib/apparmor/profile-load`, `rc.apparmor.functions` — boot helpers
   - `/etc/apparmor/parser.conf` — parser configuration
   - `/usr/lib/systemd/system/apparmor.service` — systemd unit
   - manpages for apparmor.d(5), apparmor(7), apparmor_parser(8),
     aa-teardown(8), apparmor_xattrs(7)

3. **profiles/** — Makefile-driven, installs the upstream profile substrate
   to `/etc/apparmor.d/` (top-level profiles, `abi/`, `abstractions/`,
   `tunables/`) plus extra-profiles to `/usr/share/apparmor/extra-profiles/`.

4. **apparmor-profiles-extra_1.35** — Debian-derived extras (irssi,
   pidgin, totem, etc.) extracted from the secondary tarball declared in
   `package.yml`. Added with a "never overwrite upstream" merge policy.

5. **InterGenOS-specific profiles** (in `profiles/` alongside this README):
   - `usr.bin.intergen-mcp` — local AI assistant daemon
   - `usr.bin.pkm` — InterGenOS package manager

   The Forge installer is intentionally **not** confined by AppArmor: its
   use of util-linux 2.41's new mount API (`fsopen`/`fsconfig`/`fsmount`/
   `move_mount`) produces detached mounts that AppArmor's mount mediation
   cannot match, returning EPERM even in complain mode (a known upstream
   regression). No mainstream distribution confines its installer with
   AppArmor, and the defense-in-depth gain on a short-lived, user-launched,
   already-privileged installer is negligible. Backend hardening lives in
   the systemd unit instead.

6. **Complain-mode marker** — `/usr/share/intergenos-apparmor/default_mode`
   contains `complain`. This declares the InterGenOS posture intent
   (profiles ship in learning/complain mode for graceful rollout). NOTE:
   as of 2026-05-15 there is no first-boot service wired to read this
   file; activation of complain mode is tracked separately. The marker
   is a documented policy declaration only.

## Posture: complain-by-default

In keeping with InterGenOS's goal of giving you a system you understand,
can modify, and can trust, InterGenOS ships all AppArmor profiles in
**complain mode (learning mode)** by default.

This posture provides a graceful rollout: it logs policy violations to the
journal (`/var/log/audit/audit.log` or `dmesg`) without blocking execution,
which lets us validate the profile set against real-world workloads
without breaking user systems.

As confidence builds, profiles graduate to `enforce` mode per-profile in
future releases.

## Disabling profiles (user control)

To disable a specific profile, symlink it into the `disable/` directory and
unload it via `apparmor_parser`:

```bash
sudo ln -s /etc/apparmor.d/usr.bin.intergen-mcp /etc/apparmor.d/disable/
sudo apparmor_parser -R /etc/apparmor.d/usr.bin.intergen-mcp
```

If the apparmor utils package is installed:

```bash
sudo aa-disable /usr/bin/intergen-mcp
```

To globally disable AppArmor (not recommended), append `apparmor=0` to your
kernel command line via the bootloader.

## Build notes (for context)

* `libapparmor.so` must be available at meson configure time, since systemd
  is built with AppArmor support and links `-lapparmor`. The package is
  therefore ordered ahead of systemd in the dependency graph.
* An earlier revision of this recipe shipped only the profile files: the
  `configure()` and `build()` steps were no-ops, so libapparmor was never
  actually compiled. The current recipe builds the full upstream stack
  (autotools for the library, Makefiles for the parser and profiles),
  preserves all InterGenOS-specific profiles, and explicitly extracts the
  secondary `apparmor-profiles-extra` tarball in `build()` (the build
  driver only auto-extracts the primary source archive).
