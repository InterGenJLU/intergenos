# 04 — Generating the live-ISO squashfs

**Audience:** maintainers driving a build to its squashfs stage and anyone debugging a failed squashfs build.

## Goal

Produce `filesystem.squashfs` — the compressed root filesystem the live ISO mounts via overlayfs — plus its dm-verity sidecars: `filesystem.squashfs.verity` (the merkle hashtree) and `filesystem.squashfs.verity-params` (the root hash + block layout the bootloader phase seals into the live UKI cmdline). It contains the full InterGenOS desktop image and is consumed by:

- The live-ISO init script (`installer/init/init.sh`), which verifies the squashfs against dm-verity — the sole boot-trust path, with no sha256 fallback: absent a sealed `igos.verity.roothash=` the script refuses to boot — and mounts it read-only under an overlayfs union. The whole-file `filesystem.sha256` that also ships on the media is a user-facing diagnostic the boot path does not read.
- The shim/UKI/initramfs chain referenced by `scripts/build-iso.sh` (via the `SQUASHFS=` env var) when assembling the bootable ISO — which also copies the `.verity` hashtree onto the media.

The squashfs is built from the chroot at `/mnt/igos/` after package installation completes.

## Prerequisites

- The chroot is built far enough that the desktop tier is installed and the customize-airootfs hooks (CA bundle, ldconfig, schema/icon/desktop databases, mandb, preset-all) can run inside it. Typically every package tier through `ai` — the final package phase — plus the bootloader, image, and manifest phases have completed (squashfs is phase 19 of 21).
- `mksquashfs` is on PATH on the build VM (ships with the `squashfs-tools` Ubuntu package).
- `unsquashfs` is also on PATH — the post-build verification step uses it.
- `python3` is on PATH if you want the pre-squashfs audit gate (step 4.5 below) to run; absent python3, the gate self-skips with a warning.
- Root privilege (or `sudo`) — the script mounts pseudo-fs inside the chroot.

## Step-by-step procedure

The canonical entry point is `scripts/build-squashfs.sh`. Invocation:

```sh
ssh <builder-user>@<build-vm-ip>  # build VM
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

### Step 2.5 — MIRROR-only package prune (`pkm iso-prep`)

`scripts/derive-iso-exclusions.py --mode=names` derives every package whose `iso_include` resolves False from the package.yml tree, and `pkm iso-prep --packages-from` evicts them from the chroot before mksquashfs. pkm's runtime-dep graph aborts if any shipped package depends on a mirror-only one, and a co-ownership heal restores any shared-path casualty byte-verified. The chroot's contents after this step ARE the ISO's contents. Skip via `ISO_PREP=0` (diagnostic full-corpus builds only).

### Step 2.6 — MIRROR-only ARCHIVE exclusion (decided 2026-07-22)

The prune removes installed payloads, but `/var/lib/igos/archives` sits outside pkm ownership and previously shipped in full — every archive, mirror-only included. `derive-iso-exclusions.py --mode=archive-excludes` emits the exact `<name>-<version>.igos.tar.gz` basenames of every mirror-only package (composed from the parsed package.yml fields, never filename-splitting, so prefix-colliding names cannot over-match), and the script turns each into a per-file `mksquashfs -e` entry. Exclusion — not deletion — so the chroot's full archive corpus survives as the mirror-publish source. The ISO ships exactly the `iso_include:true` archive set the installer consumes.

### Step 2.7 — Metadata/payload sync gate (fail-closed, decided 2026-07-28)

The squashfs carries three descriptions of the same package set — the pkm database, the text manifests, and the archive corpus the installer consumes — and nothing previously compared them. One release candidate shipped a database describing a different build of 198 packages than the archives beside it, so a live session's `pkm` answers were wrong for roughly a quarter of the corpus and one claimed FHS directory was absent from the image. `scripts/check-iso-metadata-sync.py` streams every shipping archive once (`.PKGINFO` plus payload member hashes) and requires: the database row matches the archive's exact `(version, release)`; the text manifest exists (and any `PACKAGE RELEASE` header agrees); every claimed path exists in the chroot; and regular files outside `etc/` content-match at least one claiming archive (bootstrap twins legitimately share byte-identical paths; `etc/` files are existence-checked only, the same conffile policy `pkm verify` applies; absences are excused only by pkm's own expected-absent classes). The gate never mutates the chroot — every hit prints the exact `pkm install --archive` redeploy remedy, and the build refuses to continue until image, metadata and archives all describe the same build.

### Step 3 — Clean runtime trash

- `/var/log/*` files are truncated (not deleted — open file descriptors in services may break otherwise).
- `/tmp` and `/var/tmp` contents are removed (directories preserved).
- `/etc/machine-id` is reset to the literal `uninitialized` — this is the systemd convention for "regenerate at first boot." The live-boot path overrides this in init.sh's overlay; installed systems generate a real ID on first boot of the installed target.
- `/root/.bash_history` + any `/home/*/.bash_history` are removed.
- `/root` build-time caches are removed (explicit list: `.cache`, `.cmake`, `.config/go`, `.links`, `.mozbuild`, `.npm`, `go`, `.local/state`, `.triton`, gdbm histories). Root-run compilers and generators leave these behind — measured at 8.7G / ~138k files on one candidate — and they shipped in every prior ISO. The list is explicit so anything NEW under `/root` surfaces at the Step 4.85 ownership gate instead of being silently deleted.

### Step 4 — Unmount pseudo-fs

Same `cleanup_mounts` function from step 1, run early (before mksquashfs) so the mksquashfs walker sees empty mount-point dirs instead of bind-mount artifacts. Trap is cleared after this so we don't double-unmount on script exit.

### Step 4.4 — Chroot-binary-presence gate

`scripts/check-installer-runtime-deps.py` verifies every binary the installer Python invokes via subprocess (and every binary the shell pipeline scripts depend on) is present in the chroot at a standard search path. **Refuses to build squashfs if any are missing.** It runs first, before the verify_paths audit, so a missing-binary regression surfaces with a precise diagnostic rather than as a package-level cascade. This closes the regression class where a runtime helper (such as a missing `parted` or a path-mismatched `iucode_tool`) silently never made it into the chroot. The gate self-skips with a `[4.4/5] … SKIPPED` line if its script is absent.

### Step 4.5 — Pre-squashfs audit gate (Rule 20 enforcement)

`scripts/pre-squashfs-audit.py` walks every `packages/<tier>/<name>/package.yml`, extracts `verify_paths:`, and confirms each declared path exists on the chroot. **Refuses to build squashfs if any verify_paths fail.** This audit enforces Rule 20 (see topic 08): every package recipe must produce the files it claims. It catches the silent-skip regression class, where a recipe is reachable by neither builder, a greedy glob swallows a package name, or an upstream version bump renames an installed path so a file the recipe promised never lands in the chroot.

If the audit fails:

- Read the audit output — it lists the missing paths per-package.
- For each missing path, decide whether to **build the package** (most cases) or **correct the verify_paths declaration** (occasional — when the path was renamed by an upstream version bump).
- Re-run `build-squashfs.sh` once the audit passes.

If `python3` is absent or the script is missing, the gate self-skips with `[4.5/5] pre-squashfs audit SKIPPED (script not found at …)`. Do not rely on the skip. Install `python3` on the build VM so the gate runs on every build. This script is part of the release validation set.

### Step 4.6 — Install-set audit (Forge-parseability gate)

Step 4.5 proves declared files landed in the chroot, but Forge installs from the staged `.igos.tar.gz` archives via `installer/backend/packages.py get_archives()`. An archive that is physically present in the squashfs yet never *yielded* by that parser (a non-digit version string, a duplicate-name clobber) ships in the image but never installs, and the chroot-side verify_paths audit still passes. `scripts/preflight-install-set.py` runs the actual Forge parser against the staged archive dir (`<chroot>/var/lib/igos/archives`) and **refuses to build squashfs if any archive would be silently dropped at install time.** This catches the silent-drop class, the same defect that once let `llama-cpp-b5545` ship with no inference engine (its `b5545` version is non-digit-leading, so the parser skipped it). It self-skips with a `[4.6/5] … SKIPPED` line if the staged archive dir or the audit script is absent.

### Step 4.85 — Squashfs ownership gate (fail-closed; decided 2026-07-22)

`scripts/check-squashfs-ownership.py` runs last before mksquashfs, against the exact tree that ships. Every file must be traceable to an installed package's manifest (the pkm database), to the package system's own state rules (archives must be `<name>-<version>.igos.tar.gz` of an installed package or on the mirror-exclusion list; manifests and helper manifests must match installed rows), or to a reviewed entry in `config/squashfs-ownership-allowlist.txt` — where every entry carries a mandatory reason and acknowledged debt classes are tagged with their ledger references. Unowned empty directories fail the same way (the shipped-skeleton class: a pruned package's leftover directory tree reads as "present" to a bare python namespace-package import). **Any violation refuses the squashfs build.** Disposition paths: fix the owning recipe's manifest, remove the stray from the chroot, or add a reasoned allowlist entry for legitimate generated state.

### Step 5 — mksquashfs

The actual squashfs build. Key flags:

- `-comp zstd -b 1M -Xcompression-level 19` — zstd at level 19, 1MB blocks. This replaced xz on 2026-06-05 because xz squashfs *decompression* was the systemic boot bottleneck: every boot-time file read pays single-stream xz per-block latency, roughly 4x slower than a raw USB read. zstd-19 gives a near-xz compression ratio with multi-threaded, far-faster decompression, and the kernel already supports it (`CONFIG_SQUASHFS_ZSTD=y`), so no kernel rebuild is needed. The compressor is overridable via the `COMP=` env.
- `-processors $JOBS` — defaults to `nproc`. Override via `JOBS=N` env if running on a busy host.
- `-noappend` — fresh filesystem; without this, mksquashfs would append to an existing squashfs at `$OUTPUT`. The script auto-omits this on first run when `$OUTPUT` doesn't exist.
- **Excluded entirely (plain `-e <path>` form):** `mnt/intergenos` (build tree shouldn't ship), `mnt/hot-storage` (trace/checkpoint share — forensic trace writes land in the chroot's plain dir whenever the bind is absent, and one candidate shipped 221 trace files before this exclude), `sources` (LFS tarballs), `var/cache` (rebuilt at first use), `var/log/journal` (per-build noise), `tmp/lost+found`, `var/tmp/lost+found`, `.igos-chroot-ownership-normalized`. Plus a conditionally-built `EXTRA_EXCLUDES` array (`gid_Module_*` LibreOffice build leftovers, `root/.bash_history`, any `home/*/.bash_history`) and the Step 2.6 `MIRROR_ARCHIVE_EXCLUDES` per-archive entries.
- **`-wildcards` is intentionally omitted.** There is no `<path>/*` contents-only exclusion form. The mount-point dirs (`/proc /sys /dev /run /tmp`) are preserved as empty dirs by the step-3 cleanup truncation plus the early step-4 unmount, *not* by excluding their contents with a wildcard. The end-of-script post-build audit (below) enforces their presence: it fails the build if any mount-point dir is missing from the output.

Default `$OUTPUT` is `${CHROOT}/mnt/intergenos/build/filesystem.squashfs` (so the artifact lands inside the chroot tree, accessible to build-iso.sh).

### Post-build sanity check

The script runs `unsquashfs -l "$OUTPUT" | grep -qE "^squashfs-root/<mnt>$"` for each of `proc sys dev run tmp` and dies if any are missing. This is the regression detector for pseudo-fs mount-point preservation: if the squashfs ever ships without the mount-point dirs, init.sh's `mount --move` would fail and boot would die in the initramfs.

### Step 6 — Generate the dm-verity hashtree

`veritysetup format` (preferring the chroot's `veritysetup-static`, falling back to a host `veritysetup`) builds a merkle hashtree over the squashfs with `--hash=sha256 --data-block-size=4096 --hash-block-size=4096`. Outputs:

- `${OUTPUT}.verity` — the hashtree file (~0.1% of squashfs size); ships on the ISO alongside the squashfs.
- `${OUTPUT}.verity-params` — `ROOT_HASH`, `DATA_BLOCKS`, block sizes, salt, `HASH_ALGO=sha256`. The bootloader phase reads this to inject `igos.verity.roothash=` into the live-mode UKI cmdline (which is why squashfs runs *before* the UKI/verity-seal phase).

This replaced the prior whole-file boot-time sha256 (which sha256summed the entire ~9 GiB squashfs at every boot, ~73s at USB read speed). With the hashtree alongside and the root hash sealed in the signed UKI cmdline, the kernel verifies each 4 KiB block as it's actually read — same crypto guarantee, zero up-front cost. The script dies if `veritysetup` is unavailable or the root hash can't be parsed.

## Validation

After successful completion the script prints:

```
DONE.
  squashfs:   <OUTPUT>
  size:       <SIZE> MB
  sha256:     <SHA256>
  verity:     <OUTPUT>.verity (<N> KiB)
  root hash:  <ROOT_HASH>
```

Independently verify:

```sh
ls -lh "$OUTPUT"
sha256sum "$OUTPUT"
unsquashfs -l "$OUTPUT" | head -20  # inspect the rootfs
file "$OUTPUT"  # "Squashfs filesystem, little endian, version 4.0, zstd compressed"
```

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Step 2 fatal: `CA bundle missing AND update-ca-certificates unavailable` | `packages/core/ca-certificates` not built into the chroot | build the package, re-run squashfs |
| Step 4.5 audit FAILED with N packages missing paths | recipes claim verify_paths the build didn't produce (silent-skip regression, greedy-glob match, package not built) | per audit output — build the missing packages OR correct the verify_paths declarations |
| Post-build sanity check fails: `mount-point dirs MISSING from squashfs: …` | step-3 cleanup or the early step-4 unmount didn't leave `/proc /sys /dev /run /tmp` as empty dirs, OR a new `-e` exclusion removed a mount-point dir | confirm the mount-point dirs survive in the chroot post-unmount; do not add a `-e <mnt>` for any of them |
| mksquashfs OOM-kills mid-build | zstd-19 at high parallelism is RAM-heavy on small VMs | reduce `JOBS=N` to halve nproc, retry |
| `veritysetup format failed` / `veritysetup not found` | `cryptsetup-static` not built into the chroot and no host `veritysetup` | build `cryptsetup-static` into the chroot, or install `cryptsetup`/`veritysetup` on the build VM, then re-run |
| Output sha256 differs between runs of the same chroot | non-reproducible inputs (timestamps inside the chroot, locale-dependent ordering) | beyond the scope of this doc; bit-for-bit squashfs reproducibility is a known open item |

## Cross-references

- [Topic 05: Creating the ISO](05-creating-iso.md) — consumes the squashfs as the `SQUASHFS=` input
- [Topic 08: Adding a package](08-adding-packages.md) — covers Rule 20 (verify_paths declaration), which is what the step 4.5 audit gates against
- `scripts/build-squashfs.sh` — canonical reference, including the inline rationale for the omitted `-wildcards` form and mount-point preservation
- `scripts/pre-squashfs-audit.py` — audit gate driver
