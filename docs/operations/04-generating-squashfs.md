# 04 — Generating the live-ISO squashfs

**Audience:** maintainers driving a build to its squashfs stage and anyone debugging a failed squashfs build.

## Goal

Produce `filesystem.squashfs` — the compressed root filesystem the live ISO mounts via overlayfs. It contains the full InterGenOS desktop image and is consumed by:

- The live-ISO init script (`installer/init/init.sh`), which mounts it read-only under an overlayfs union.
- The shim/UKI/initramfs chain referenced by `scripts/build-iso.sh` (via the `SQUASHFS=` env var) when assembling the bootable ISO.

The squashfs is built from the chroot at `/mnt/igos/` after package installation completes.

## Prerequisites

- The chroot is built far enough that the desktop tier is installed and the customize-airootfs hooks (CA bundle, ldconfig, schema/icon/desktop databases, mandb, preset-all) can run inside it. Typically: orchestrator phases through `desktop` have completed.
- `mksquashfs` is on PATH on the build VM (ships with the `squashfs-tools` Ubuntu package).
- `unsquashfs` is also on PATH — the post-build verification step uses it.
- `python3` is on PATH if you want the pre-squashfs audit gate (step 4.5 below) to run; absent python3, the gate self-skips with a warning.
- Root privilege (or `sudo`) — the script mounts pseudo-fs inside the chroot.

## Step-by-step procedure

The canonical entry point is `scripts/build-squashfs.sh`. Invocation:

```sh
ssh christopher@192.168.122.249
cd /mnt/intergenos
sudo bash scripts/build-squashfs.sh
```

What happens (each step from the script source):

### Step 1 — Mount pseudo-fs inside the chroot

`proc`, `sysfs`, `devtmpfs`, `tmpfs`, and `devpts` are mounted under `/mnt/igos/{proc,sys,dev,run,dev/pts}`. Each mount is guarded by `mountpoint -q` so re-runs are idempotent. A `cleanup_mounts` trap fires on script exit even if mksquashfs fails.

### Step 2 — Customize-airootfs hooks

A chroot-internal block runs:

- **CA bundle verification** at `/etc/ssl/certs/ca-certificates.crt`. Fatal if missing AND `update-ca-certificates` is unavailable — silently shipping a TLS-broken ISO is worse than a build failure.
- `ldconfig` to refresh the dynamic linker cache.
- `glib-compile-schemas`, `update-desktop-database`, `update-mime-database`, `gtk-update-icon-cache` (hicolor), `fc-cache` — each guarded by `command -v` + directory-presence so minimal-build profiles tolerate missing tools.
- `systemctl preset-all` — consumes `/usr/lib/systemd/system-preset/*.preset` (e.g., the `90-gdm.preset` shipped by `packages/desktop/gdm`) to wire the `display-manager.service` symlink and other `.wants/` links.
- `mandb` (optional, slow).

Skip the hook block via `SKIP_CUSTOMIZE=1`. Only useful for fast iteration — the resulting squashfs is missing all the indices and won't boot a usable desktop.

### Step 3 — Clean runtime trash

- `/var/log/*` files are truncated (not deleted — open file descriptors in services may break otherwise).
- `/tmp` and `/var/tmp` contents are removed (directories preserved).
- `/etc/machine-id` is reset to the literal `uninitialized` — this is the systemd convention for "regenerate at first boot." The live-boot path overrides this in init.sh's overlay; installed systems generate a real ID on first boot of the installed target.
- `/root/.bash_history` + any `/home/*/.bash_history` are removed.

### Step 4 — Unmount pseudo-fs

Same `cleanup_mounts` function from step 1, run early (before mksquashfs) so the mksquashfs walker sees empty mount-point dirs instead of bind-mount artifacts. Trap is cleared after this so we don't double-unmount on script exit.

### Step 4.5 — Pre-squashfs audit gate (Rule 21 enforcement)

`scripts/pre-squashfs-audit.py` walks every `packages/<tier>/<name>/package.yml`, extracts `verify_paths:`, and confirms each declared path exists on the chroot. **Refuses to build squashfs if any verify_paths fail.** The audit is the in-tree equivalent of the canonical Rule 21 example — every package recipe must produce the files it claims, and the audit catches silent-skip regressions (the kind that motivated the operational note on silent-skip regressions).

If the audit fails:

- Read the audit output — it lists the missing paths per-package.
- For each missing path, decide whether to **build the package** (most cases) or **correct the verify_paths declaration** (occasional — when the path was renamed by an upstream version bump).
- Re-run `build-squashfs.sh` once the audit passes.

If `python3` is absent or the script is missing, the gate self-skips with `[4.5/5] pre-squashfs audit SKIPPED (script not found at …)`. Don't rely on the skip — install `python3` on the build VM and have the gate running every build. The script is part of the golden-builder validation set.

### Step 5 — mksquashfs

The actual squashfs build. Key flags:

- `-comp xz -b 1M -Xbcj x86` — XZ compression, 1MB blocks, x86 bytecode filter for binary density. Yields ~23% better ratio than gzip on a typical InterGenOS rootfs.
- `-processors $JOBS` — defaults to `nproc`. Override via `JOBS=N` env if running on a busy host.
- `-noappend` — fresh filesystem; without this, mksquashfs would append to an existing squashfs at `$OUTPUT`. The script auto-omits this on first run when `$OUTPUT` doesn't exist.
- `-wildcards` — enables the `<path>/*` exclusion syntax used below.
- **Excluded entirely:** `mnt/intergenos` (build tree shouldn't ship), `sources` (LFS tarballs), `var/cache` (rebuilt at first use), `var/log/journal` (per-build noise), `root/.bash_history`, `home/*/.bash_history`.
- **Excluded contents-only:** `tmp/*`, `var/tmp/*`, `proc/*`, `sys/*`, `dev/*`, `run/*`. Directories preserved as empty mount points so the init.sh `mount --move` lands somewhere — see the operational note on pseudo-fs mount-point preservation. **The `-e <path>` form would exclude the directory itself; `-e '<path>/*'` excludes only contents.**

Default `$OUTPUT` is `${CHROOT}/mnt/intergenos/build/filesystem.squashfs` (so the artifact lands inside the chroot tree, accessible to build-iso.sh).

### Post-build sanity check

The script runs `unsquashfs -l "$OUTPUT" | grep -qE "^squashfs-root/<mnt>$"` for each of `proc sys dev run tmp` and dies if any are missing. This is the regression detector for the operational note on pseudo-fs mount-point preservation — if the squashfs ever ships without the mount-point dirs, init.sh's `mount --move` would fail and boot would die in the initramfs.

## Validation

After successful completion the script prints:

```
DONE.
  path:   <OUTPUT>
  size:   <SIZE> MB
  sha256: <SHA256>
```

Independently verify:

```sh
ls -lh "$OUTPUT"
sha256sum "$OUTPUT"
unsquashfs -l "$OUTPUT" | head -20  # inspect the rootfs
file "$OUTPUT"  # "Squashfs filesystem, little endian, version 4.0, xz compressed"
```

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Step 2 fatal: `CA bundle missing AND update-ca-certificates unavailable` | `packages/core/ca-certificates` not built into the chroot | build the package, re-run squashfs |
| Step 4.5 audit FAILED with N packages missing paths | recipes claim verify_paths the build didn't produce (silent-skip regression, greedy-glob match, package not built) | per audit output — build the missing packages OR correct the verify_paths declarations |
| Post-build sanity check fails: `mount-point dirs MISSING from squashfs: …` | someone added or modified a `-e` exclusion in mksquashfs without using the wildcard form | restore the `-e '<path>/*'` form per the operational note on pseudo-fs mount-point preservation |
| mksquashfs OOM-kills mid-build | xz compressor at -Xbcj x86 is RAM-heavy at high parallelism on small VMs | reduce `JOBS=N` to halve nproc, retry |
| Output sha256 differs between runs of the same chroot | non-reproducible inputs (timestamps inside chroot, locale-dependent ordering) | beyond scope of this doc; see topic 09 cost-of-deferral context on B2 reproducibility |

## Cross-references

- Topic 05: How to create an ISO — consumes the squashfs as the `SQUASHFS=` input
- Topic 08: Adding packages — covers Rule 20 (verify_paths declaration) which is what the step 4.5 audit gates against
- the operational note on pseudo-fs mount-point preservation — origin story of the wildcard exclusion form
- the operational note on silent-skip regressions — motivates the step 4.5 audit
- `scripts/build-squashfs.sh` — canonical reference
- `scripts/pre-squashfs-audit.py` — audit gate driver
