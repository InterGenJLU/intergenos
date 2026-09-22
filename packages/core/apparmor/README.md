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
   to `/etc/apparmor.d/` (`abi/`, `abstractions/`, `tunables/`, `local/`)
   plus upstream's extra-profiles to `/usr/share/apparmor/extra-profiles/`.

4. **apparmor-profiles-extra_1.35** — Debian-derived extras (irssi,
   pidgin, totem, etc.) extracted from the secondary tarball declared in
   `package.yml`. Added with a "never overwrite upstream" merge policy.

4b. **Top-level profiles are staged, not loaded.** Every top-level profile
   from steps 3 and 4 is moved to `/usr/share/apparmor/extra-profiles/`,
   except `lsb_release` and `nvidia_modprobe`: those two attach to no program
   and exist to be switched to by other profiles, so they stay loaded. See
   "Where a profile lives" below.

5. **InterGenOS-specific profiles** (in `profiles/` alongside this README):
   - `usr.bin.pkm` — InterGenOS package manager

   A profile for a local assistant daemon was removed 2026-09-22: no recipe in
   this repository installs a binary of that name, so the profile attached to
   nothing, loaded into the kernel on every machine and confined no process,
   while still being counted in every summary of the policy. A profile that
   confines nothing makes the policy look wider than it is.

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
can modify, and can trust, the InterGenOS-authored profile (`usr.bin.pkm`)
ships in **complain mode (learning mode)** by default. Upstream profiles keep
upstream's mode: the thirteen that owning packages link into `/etc/apparmor.d`
(see "Where a profile lives") carry no complain flag and load in **enforce**
mode, as they did before they moved.

This posture provides a graceful rollout: it logs policy violations to the
journal (`/var/log/audit/audit.log` or `dmesg`) without blocking execution,
which lets us validate the profile set against real-world workloads
without breaking user systems.

As confidence builds, profiles graduate to `enforce` mode per-profile in
future releases.

## Where a profile lives

The apparmor unit loads every file in `/etc/apparmor.d` at every boot. A
profile whose program is not on the machine is still parsed, loaded and counted
in every summary of the policy, and confines nothing. On an ordinary install
measured on 2026-09-22, 39 of the 55 top-level profiles this package then
shipped named a program that machine did not have.

Only the package that installs a program knows whether that program is on the
machine, so a profile is placed in `/etc/apparmor.d` by the package that owns
its program: it installs a symlink at `/etc/apparmor.d/<name>` pointing at the
copy staged in `/usr/share/apparmor/extra-profiles/<name>`, and it declares
this package as a runtime dependency so the link cannot dangle. The staged
copy is upstream's own file, so a new upstream release updates the profile
under every link at once. Packages that link a profile this way:

| package | profiles |
|---|---|
| inetutils | `bin.ping` |
| samba | `samba-bgqd`, `samba-dcerpcd`, `samba-rpcd`, `samba-rpcd-classic`, `samba-rpcd-spoolss`, `usr.sbin.nmbd`, `usr.sbin.smbd`, `usr.sbin.winbindd` |
| avahi | `usr.sbin.avahi-daemon` |
| dnsmasq | `usr.sbin.dnsmasq` |
| traceroute | `usr.sbin.traceroute` |
| gzip | `zgrep` |

To confine a program you installed yourself with one of the staged profiles,
link it the same way and load it:

```bash
sudo ln -s /usr/share/apparmor/extra-profiles/<name> /etc/apparmor.d/
sudo apparmor_parser -r /etc/apparmor.d/<name>
```

## Disabling profiles (user control)

To disable a specific profile, symlink it into the `disable/` directory and
unload it via `apparmor_parser`:

```bash
sudo ln -s /etc/apparmor.d/usr.bin.pkm /etc/apparmor.d/disable/
sudo apparmor_parser -R /etc/apparmor.d/usr.bin.pkm
```

To read back which profiles are loaded and in which mode, use the status tool
this package installs (it needs privilege to read the kernel's policy and says
so plainly when it has none):

```bash
sudo aa-status
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
