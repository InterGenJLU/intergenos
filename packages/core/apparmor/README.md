# InterGenOS AppArmor

This package builds **AppArmor v3.1.7** — the libapparmor C library, the
apparmor_parser binary, and the upstream profile substrate — and installs
the InterGenOS-specific profile additions on top.

It implements Option A from the 2026-04-29 fleet consensus vote (AppArmor
as the InterGenOS MAC framework).

## What this package compiles and installs

1. **libraries/libapparmor** — autotools, produces `libapparmor.so` and the
   `libapparmor.pc` pkg-config file. Consumed by systemd, polkit, dbus, and
   anything else that links `-lapparmor`. Build #6 Halt #8 traced back to a
   prior stub build.sh that never compiled this library.

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
   - `usr.bin.forge` — Secure Boot installer / MOK-enrollment flow
   - `usr.libexec.intergenos.first-boot-greeter` — first-boot prompt that
     ensures zero default credentials.

6. **Complain-mode marker** — `/usr/share/intergenos-apparmor/default_mode`
   contains `complain`. The first-boot orchestrator reads this after the
   kernel boots with the apparmor LSM available and runs `aa-complain` on
   every profile in `/etc/apparmor.d/`.

## Posture: complain-by-default

In alignment with the Prime Directive ("user in control of their own
machine"), InterGenOS ships all AppArmor profiles in **complain mode
(learning mode)** by default.

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

## Build-time history (for context)

* **Build #5 audit-fix `04e36a7`** declared `apparmor` as a build-dep of
  `systemd-pass2`, expecting `libapparmor.so` to be available at meson
  configure time.
* **Build #6 Halt #7b** (master `f190f1c`) — surfaced that apparmor's
  `tier: core` was declarative-only: the package was never wired into any
  `chroot-build-*.sh` `run_package` list. Tier moved to `desktop` so the
  Python builder picks it up via topological sort.
* **Build #6 Halt #8** (this commit) — surfaced that the build.sh itself
  was a profile-only stub: `configure()` and `build()` were no-ops, so
  libapparmor was *still* never compiled. Path-shape bugs in `do_install()`
  also referenced a post-strip-1 version-prefixed dir and a `work/` path
  from the secondary tarball that the orchestrator never auto-extracts.

This commit replaces the stub with a real build per upstream's expected
recipe (autotools + Makefiles), preserves all InterGenOS-specific profiles,
and explicitly extracts the secondary tarball in `build()`.
