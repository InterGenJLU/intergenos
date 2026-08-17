# 02 — Running the builder

**Audience:** maintainers driving a full or partial InterGenOS build.

## Goal

Take a known-clean build VM (topic 01 plus the topic 07 reference snapshot) through the full 21-phase build pipeline. The pipeline produces a bootable disk image at `/mnt/intergenos/build/intergenos.qcow2` and a bootable hybrid ISO at `/mnt/intergenos/build/intergenos-<version>.iso`, along with the targeted-rebuild tools for iterating on a subset of packages without re-running the full chain.

The canonical entry point is `scripts/build-intergenos.sh` running inside the build VM. It delegates to two lower layers:

- **Bash static-list builders** (`scripts/chroot-build-<phase>.sh`) for the `core`, `base`, `core-extra`, and `bootloader` phases (plus the LFS Chapter 5-8 toolchain).
- **Python tier driver** (`igos-build.py`) for the `desktop`, `extra`, `compute`, and `ai` tiers.

## Prerequisites

- Build VM is at the reference-snapshot state (topic 07): `/mnt/igos` empty, apt timers masked, virtiofs mounted, build toolchain on PATH.
- The host clone of the repository is current — run `git pull --ff-only origin master` on the workstation, and confirm the change is visible inside the VM via `/mnt/intergenos`.
- Sources are downloaded and mirrored — `python3 scripts/download-sources.py` (or the active mirror fetcher) has produced `build/sources/<tarball>` per the `source: url:` declarations in every `package.yml`.
- Patches are available at `build/patches/` if any package recipe consumes one.
- Disk free: at least 100GB for a fresh build, with 200GB more comfortable. The chroot, sources, logs, archives, and the final qcow2 image add up.
- For signing-pipeline runs: an offline signing workstation is ready and the maintainer is available for the ceremony (topic 03).

## Full-build invocation

This is the clean-VM happy path. The build is a **long-running operation** with several attention points (signing-ceremony pauses, MOK enrollment, smoke evaluation), so plan for it as a multi-hour session.

**Launch the orchestrator as a `systemd-run` transient unit, not a bare `sudo bash`.** This is the canonical method. A multi-hour build tied to your SSH shell dies on disconnect and has no clean control surface. A transient unit (a) survives disconnect, (b) provides a named unit for `journalctl -u <unit> -f` live logs and `systemctl status/stop <unit>` control, and (c) isolates the build in its own cgroup.

```sh
ssh <user>@<build-vm-ip>
UNIT=igos-build-$(date +%Y%m%d-%H%M%S)
echo "$UNIT" | sudo tee /tmp/igos-current-unit          # remember the unit name
sudo systemd-run --unit="$UNIT" --working-directory=/mnt/intergenos \
    bash scripts/build-intergenos.sh \
        --user <user> \
        --checkpoint \
        --debug-verbose
```

`systemd-run` returns immediately; the build runs in the background unit. Monitor and control it:

```sh
journalctl -u "$UNIT" -f                                 # live orchestrator narration
systemctl status "$UNIT"                                 # active(running) / inactive(exit code)
tail -f /mnt/intergenos/build/logs/<tier>-build.log      # the active Python tier (desktop/ai/extra)
sudo systemctl stop "$UNIT"                              # immediate stop
touch /mnt/igos/.build-stop                              # OR: graceful halt at next phase boundary
```

To route the forensic trace to a large off-host volume, add `--setenv` before the `bash`:
`sudo systemd-run --unit="$UNIT" --setenv=IGOS_TRACE_ROOT=<off-host-volume>/intergenos_build_trace --working-directory=/mnt/intergenos bash scripts/build-intergenos.sh …`

### Every orchestrator launch variant

The shell is always `systemd-run … bash scripts/build-intergenos.sh <FLAGS>`; only `<FLAGS>` changes. (All flags verified against the `scripts/build-intergenos.sh` argument parser, 2026-06-11.)

| Goal | `<FLAGS>` |
|---|---|
| **Full clean build → ISO** (all 21 phases; pauses for signing at `ukis-verity`) | `--user <user> --checkpoint --debug-verbose` |
| **Full build to a snapshot point** (stop so you can snapshot the result) | `--user <user> --checkpoint --debug-verbose --stop-after ai` |
| **Resume at a phase** (after fixing a failure) | `--user <user> --checkpoint --debug-verbose --start-at <phase>` |
| **Resume at a package within a phase** | `--user <user> --checkpoint --debug-verbose --start-at <phase> --start-at-pkg <pkg>` |
| **Resume after the signing ceremony** | `--user <user> --debug-verbose --start-at iso` |
| **Publish the signed repo** (optional) | append `--publish` |

Flag reference (verbatim from the parser):
- `--user <name>` — **required**; sets IMAGE_USER.
- `--root-password <pw>` / `--user-password <pw>` — **optional**; omit for the built-in default `intergenos:intergenos`. These set the credentials baked into the live ISO, not the credentials of an installed system — the installer collects those from the user. An empty string is rejected — omit the flag instead.
- `--image-user <name>` — override the in-image username (defaults to `--user`).
- `--checkpoint` — emit a chroot tarball after each major phase, enabling a resume from the failure point. Always use it on a real run.
- `--debug-verbose` — **mandatory** (live streaming plus JSONL forensic trace). Equivalent to `IGOS_BUILD_DEBUG_VERBOSE=1`.
- `--start-at <phase>` / `--stop-after <phase>` — **phase** names (see the phase table below).
- `--start-at-pkg <pkg>` — per-package resume *within* the `--start-at` phase. Requires `--start-at`. **Use this, not the bare `IGOS_START_AT` environment variable** — the orchestrator unsets `IGOS_START_AT` defensively to prevent an ambient value from leaking into the run.
- `--publish` — run `publish-repo.sh` after the ISO (the optional 21st phase).
- `--iso-name <file.iso>` — the target ISO filename (bare name; lands in `build/`). Give it ONCE at launch: it persists in `build/.iso-name` across the ceremony-resume chain, so the final `--start-at iso` resume mints under the launch-chosen name — ISOs are never hand-renamed after creation. `phase_iso` logs which source won (flag / persisted / legacy default); a malformed name fails closed. A fresh full launch (no `--start-at`) without the flag clears the previous chain's persisted name so it cannot leak across arcs. **Naming rule (operator ruling 2026-07-05): if an arc's ISO is superseded or destroyed, its replacement ROTATES the candidate ordinal (`…-ge-01-…` → `…-ge-02-…`) — the label of a destroyed ISO is never reused, so the artifact name itself says which mint it is.**

### Single-package / single-tier builds (the `--only` flag)

The single-package command flag is **`--only <name>`** on the Python builder (`python3 -m igos-build` / `igos-build.py`), NOT a flag on the orchestrator. It builds exactly one package into the already-populated chroot. See the full procedure (mount → refresh recipe copy → stage source → build) in **"Build a single package"** below; the one-line essence:

```sh
# inside the chroot (the toolchain — meson/ninja/gcc — exists ONLY in /mnt/igos):
cd /mnt/intergenos && python3 -m igos-build --only <name> --build --debug-verbose --sources-dir /sources
```

**`--debug-verbose` is mandatory here too.** It applies to every invocation of the builder, single-package builds included, not only the orchestrator.

| Goal | Builder command (run inside the chroot, from `/mnt/intergenos`) |
|---|---|
| **One package** (deps must already be in the chroot) | `python3 -m igos-build --only <name> --build --debug-verbose --sources-dir /sources` |
| **One whole tier** (`desktop`/`extra`/`compute`/`ai`) | `python3 -m igos-build --tier <tier> --build --debug-verbose --sources-dir /sources` |
| **Preview only (no build)** | `python3 -m igos-build --only <name> --dry-run` |
| **Skip already-built, unchanged-template packages** | add `--skip-built` |

Builder flags (verified against `igos-build/__main__.py`, 2026-07-14): `--build`, `--only <name>`, `--tier <tier>`, `--sources-dir <dir>`, `--tracked` (accepted no-op — tracked deployment is the default), `--stage-only` (explicit opt-out: build into the staging system root with NO deploy/archive/registration), `--skip-built`, `--dry-run`, `--verbose`/`-v`, `--debug-verbose` (**always pass this on a real build**).

> **Tracked deployment is the default for `--build`.** Earlier builder versions required an explicit `--tracked`; without it the build pointed DESTDIR at `build/system`, skipped deploy/verify/registration entirely, and still exited 0 — so the commands on this page "succeeded" without changing the chroot or producing an archive. Decided 2026-07-14: the deploying mode is the default, and the non-deploying mode requires the clearly-named `--stage-only`.

> **Choosing the substrate + what to do after the rebuild:** which chroot to build on (the live in-place chroot vs. a rollback to a `gbcNNN_<datestamp>` snapshot) and the post-rebuild iteration paths (slipstream into a booted ISO vs. mirror-republish to installed systems) are in [`09-gbc-iteration-methodology.md`](09-gbc-iteration-methodology.md) → "The build substrate" + the three iteration-path sections. `core`/`base` packages use the bash `chroot-build-<phase>.sh` driver scoped via `IGOS_START_AT=<name> IGOS_STOP_AFTER=<name>` instead of `--only` (the bash tiers — §3.4 of the private build-rules); `pkm` is one such `core` package.

- `--user <name>` is required; sets the IMAGE_USER created in the disk image.
- `--root-password` / `--user-password` are **optional**. The built-in default is **`intergenos:intergenos`** (IMAGE_USER password : root password) — omit the flags to get it, or pass a flag to override. **These are live-ISO credentials only.** `scripts/create-image.sh` locks the root account on every shipped artifact, and the installer collects a username and passwords in its wizard and applies them to the target during the install (`installer/backend/users.py`), so an installed system never carries the build-time values. The fixed live-session default follows the same convention other live media use, and is intentional; do not retire or randomize it.
- `--checkpoint` is recommended for long runs. It emits a chroot tarball after each significant phase to `/mnt/intergenos/checkpoints/` (a symlink to an off-host volume with room to spare). Use it for any non-trivial build: checkpoints turn "phase 12 failed → start over" into "phase 12 failed → start at phase 12."
- `--debug-verbose` does two things. (1) **Streams the build narration live:** every tier's output — the Python builder's package list, the dependency-graph step, per-package progress, and any error — is written to its `<tier>-build.log` and the parent log in real time via `trace_run`, so you can watch a build as it happens instead of waiting for a long-running tier (one `igos-build.py` process building hundreds of packages) to finish before its output appears. (2) **Exhaustive forensic logging:** full-byte capture of every subprocess (stdin/stdout) plus structured JSONL trace events under `$IGOS_TRACE_ROOT` (defaults to `build/logs/trace/`; set `IGOS_TRACE_ROOT=<off-host-volume>/<dir>` to route it to a large off-host volume) — every byte the build produces, input and output, attributable to the run. Without the flag the build still streams its output via a plain `tee`, but no forensic trace is produced.

> **Watching a build live.** `tail -f build/logs/build-intergenos-<ts>.log` for the orchestrator narration; `tail -f build/logs/<tier>-build.log` (e.g. `desktop-build-*.log`) for the active tier; per-package logs are `build/logs/<pkg>-<ts>.log` (no tier in the name). For tiers built by the Python builder (`desktop`/`ai`/`extra`), judge liveness from **newest per-package log age + compiler CPU** (`ps -eo pcpu,comm --sort=-pcpu`: `cc1`/`cc1plus`/`ninja`/`meson`/`rustc` at high %), since the orchestrator log is quiet during a long compile by nature.

The orchestrator writes its log to `build/logs/build-intergenos-<timestamp>.log` and records the phase state at `build/logs/.build-phase`. The phase state is what `--start-at` reads to know where to resume.

### Phase sequence (canonical order)

| # | Phase | What runs | Builder layer |
|---|---|---|---|
| 1 | `validate` | Host pre-flight: required tools, kernel features, free disk, user identity, plus the tier-coverage and audit-coverage gates (`preflight-tier-coverage.py`, `preflight-audit-coverage.py`) | n/a |
| 2 | `verify-sources` | Confirms every `package.yml`'s declared sha256 matches the local tarball at `build/sources/` | n/a |
| 3 | `setup` | Builds the `/mnt/igos` skeleton and stages the build infrastructure (`scripts`, `packages`, `config`, `installer`, `docs`, `assets`, `igos-build`, `pkm`, `intergen`) into the chroot's `/mnt/intergenos/` mirror. Also runs `scripts/build-forge-tarball.sh` to regenerate `build/sources/forge-1.0.0.tar.xz` from in-tree state | n/a |
| 4 | `toolchain` | LFS Chapter 5-6 — bootstrap toolchain on the host targeting `x86_64-igos-linux-gnu` | `scripts/toolchain-build.sh` |
| 5 | `chroot-prep` | Mounts pseudo-fs into `/mnt/igos`, copies kernel headers, sets up `/etc` skeleton | `scripts/chroot-setup.sh` |
| 6 | `chroot-tools` | LFS Chapter 7 — temporary tools inside the chroot | `scripts/temp-tools-build.sh` (chroot-internal) |
| 7 | `core` | LFS Chapter 8 — core packages (glibc, gcc-pass2, binutils-pass2, etc.) — bash static list | `scripts/chroot-build-ch8.sh` |
| 8 | `config` | LFS Chapter 9 — system configuration (network, /etc/hosts, basic services) | `scripts/chroot-config-ch9.sh` |
| 9 | `core-extra` | Tier `core` packages outside the LFS Ch8 set — bash static list | `scripts/chroot-build-core-extra.sh` |
| 10 | `base` | Tier `base` packages — bash static list | `scripts/chroot-build-base.sh` |
| 11 | `kernel` | Linux kernel build (LFS Chapter 10, Section 10.3) | `scripts/chroot-build-ch10.sh` |
| 12 | `desktop` | Tier `desktop` packages — GNOME + deps, Python topological sort | `igos-build.py --tier desktop` |
| 13 | `extra` | Tier `extra` packages — user applications | `igos-build.py --tier extra` |
| 14 | `compute` | Tier `compute` packages — opt-in GPU compute stacks and the engine variants built against them. Mirror-only: `iso_include` defaults false for this tier, so they never ship on the ISO | `igos-build.py --tier compute` |
| 15 | `ai` | Tier `ai` packages — the InterGen assistant stack. **The final package phase**, which is why the candidate-capture halt is `--stop-after ai` | `igos-build.py --tier ai` |
| 16 | `bootloader` | initramfs + standalone GRUB assembly (unsigned; UKIs themselves now built later by `ukis-verity`). Stages vmlinuz + initramfs + microcode cpios + os-release at the host bootloader dir for the post-squashfs UKI build | `scripts/chroot-build-bootloader.sh` |
| 17 | `image` | `create-image.sh` → qcow2 disk image; runs `scripts/check-d007-compliance.sh` as a ship-time gate that blocks the build on any violation | `scripts/create-image.sh` |
| 18 | `manifest` | Emits the BSD-style archive-integrity sha256 manifest (`build/intergenos-archive-manifest.txt`) covering every `*.igos.tar.gz` the build produced (signed later by `sign-release.sh --manifest`, embedded in the ISO by `build-iso.sh`). Declared-vs-built completeness is NOT checked here — it is enforced by `phase_validate`'s coverage gates + the `phase_squashfs` audits (Steps 4.4/4.5/4.6) | n/a |
| 19 | `squashfs` | `build-squashfs.sh` — live-ISO root filesystem squashfs; runs AFTER `phase_image` cleans the chroot. Also runs `veritysetup format` to emit the dm-verity hashtree + params consumed by `ukis-verity` | `scripts/build-squashfs.sh` |
| 20 | `ukis-verity` | Host-side UKI assembly with verity-augmented sealed cmdlines (consumes squashfs verity params plus the chroot-staged kernel and initramfs); halts at the end for the maintainer signing ceremony, which covers grub and all 3 UKIs in one session | `scripts/build-ukis-verity.sh` |
| 21 | `iso` | `build-iso.sh` — assembles the live ISO (signed-release or unsigned-test mode); consumes signed grub + 3 UKIs + squashfs + verity hashtree | `scripts/build-iso.sh` |

Optional 22nd phase: `publish` (gated by `--publish` flag) — runs `scripts/publish-repo.sh` to push the signed repo to the VPS.

Phase ordering changed 2026-05-28 with the dm-verity rollout. The live-mode UKI's sealed cmdline now carries the squashfs's verity root hash, which is only known after `phase_squashfs` runs `veritysetup format`. UKI assembly moved out of `phase_bootloader` into the new `phase_ukis_verity` between `squashfs` and `iso`, and the signing-ceremony pause moved with it (it now sits between `ukis-verity` and `iso`, not between `bootloader` and `image`).

The package tiers were reordered again on 2026-07-21: `extra` and `compute` now run **before** `ai`, because the ai-tier GPU-native builds consume the compute SDKs and extra-tier libraries at build time. The previous order was `ai → compute → extra`. **The candidate-capture halt follows the final package phase by principle, not by name** — it is `--stop-after ai` today and would move again if a package tier were ever added after `ai`. Re-derive it from the `run_phase` block in `scripts/build-intergenos.sh` rather than assuming the word.

Resume semantics: `--start-at squashfs` skips phases 1-18; `--start-at ukis-verity` skips through 19; `--start-at iso` skips through 20 (the resume path after the signing ceremony).

### Per-build artifact lineage

Each full-build run writes its artifacts to a per-cycle directory under `build/` to preserve lineage: unsigned bootloader inputs, signed outputs, the manifest (emitted atomically with the final ISO), and the ISO itself all live in one identifiable per-build set. Earlier builds emitted the manifest before final ISO assembly, which meant the manifest's input SHAs could record a different generation than the ISO they were meant to describe (one such ISO referenced an earlier generation of UKIs than the ones the ESP actually shipped). The per-cycle layout and atomic manifest emit close that class of build-provenance defect.

## Targeted-rebuild invocations

When the chroot is already populated and you want to rebuild a small set of packages without re-running prior phases.

### Resume at a specific phase

```sh
sudo bash scripts/build-intergenos.sh --user <user> --start-at desktop \
    --root-password '…' --user-password '…'
```

`--start-at desktop` skips phases 1-11 and resumes at desktop. This is useful after a failed phase 12: fix the cause, then resume from the failure point. It requires `build/logs/.build-phase` to reflect a prior successful run through phase 11.

### Resume at a specific PACKAGE within a phase (combined invocation)

When a chroot-build sub-script fails on a specific package, the failure log emits:

```
!!! BUILD FAILED: <pkg>
!!! Fix the issue and re-run with the orchestrator (both flags required):
!!!   sudo IGOS_START_AT=<pkg> bash scripts/build-intergenos.sh \
!!!       --user $USER --root-password $RP --user-password $UP \
!!!       --start-at <phase> --checkpoint --stop-after bootloader
```

**Both flags are required.** They live at different layers:

- `--start-at <phase>` is the OUTER orchestrator's flag at `scripts/build-intergenos.sh` — it skips all phases prior to `<phase>`. Without it, the orchestrator runs every phase from `phase_validate` forward.
- `IGOS_START_AT=<pkg>` is an INNER environment variable consumed by the `chroot-build-<phase>.sh` sub-script — it skips packages within the phase until reaching `<pkg>`.

The combination `--start-at <phase> IGOS_START_AT=<pkg>` is the canonical "resume at this specific package within this specific phase" invocation. `IGOS_START_AT` alone does NOT short-circuit earlier phases.

**Why this matters:** running `IGOS_START_AT=<pkg>` alone (without `--start-at`) causes the orchestrator to re-run all earlier phases, including `phase_setup`. Re-running `phase_setup` on a populated chroot is a known hazard (see "Common failures" below).

### Failure mode — `phase_setup` chown on a populated chroot

`phase_setup` is designed for fresh-revert builds against an empty `/mnt/igos`. It includes a recursive `chown` to set `BUILD_USER` ownership on the freshly-created LFS directory layout. If the chroot is ALREADY populated (e.g., from a partial prior build) when `phase_setup` re-runs, two problems surface:

1. The chown walks into `/proc`, `/sys`, `/dev` pseudo-FS mounts left from the prior `phase_chroot_prep` and emits hundreds of "Operation not permitted" errors. Cosmetic but obscures real output. (Mitigated since the chown was narrowed to specific subdirs.)
2. The chown rewrites ownership of root-owned system files (`/etc/shadow`, setuid binaries) to `BUILD_USER`. This breaks the security trust model — setuid binaries elevate to `BUILD_USER`'s UID instead of root.

**Recovery if this happens:** restore `/mnt/igos` from the most recent checkpoint, then re-resume with the correct `--start-at <phase>` flag. Steps:

```sh
# 1. Unmount the chroot's pseudo-FS mounts
sudo umount /mnt/igos/dev/pts /mnt/igos/dev/shm /mnt/igos/dev \
            /mnt/igos/proc /mnt/igos/sys /mnt/igos/run

# 2. Restore from the relevant checkpoint
sudo rm -rf /mnt/igos/*
sudo tar -C /mnt/igos --zstd -xf \
    /mnt/intergenos/checkpoints/intergenos-<phase>-<timestamp>.tar.zst

# 3. Resume with the FULL canonical command (both flags)
sudo IGOS_START_AT=<pkg> bash scripts/build-intergenos.sh \
    --user $USER --root-password $RP --user-password $UP \
    --start-at <phase> --checkpoint --stop-after bootloader
```

**Prevention:** always use `--start-at <phase>` (or no `--start-at`, for fresh-revert) when resuming. Never run `IGOS_START_AT` alone on a populated chroot.

### Stop after a specific phase

```sh
sudo bash scripts/build-intergenos.sh --user <user> --stop-after core \
    --root-password '…' --user-password '…'
```

Stops cleanly after the named phase. Useful for getting to a known intermediate state (e.g., "stop after core so I can inspect the chroot before phase_config runs").

### Mid-run graceful halt

Touch `/mnt/igos/.build-stop` between phases. The orchestrator checks for this file between phase boundaries and halts cleanly. Useful when you want to pause a long-running build to investigate a failure without losing in-progress phase state.

### Ctrl+C — immediate stop

The orchestrator traps SIGINT and exits. Phase state is preserved (the current package is half-built). Resuming with `--start-at <last-incomplete-phase>` picks up where it left off, but the half-built package may need a manual rollback (`rm -rf "${IGOS_BUILD}/<name>-<version>"`) first.

### Prerequisite for ALL single-package builds — sync the chroot recipe copy first

> **The chroot builds from a COPY of the recipe tree, not the live host tree.** Inside the chroot, `/mnt/intergenos` is **not** the host virtiofs — it is a plain directory (`/mnt/igos/mnt/intergenos/`) that the build infrastructure was *copied* into (the self-contained-chroot model, topic 01). `phase_setup` seeds that copy, and **every** `phase_<tier>` re-runs `sync_chroot_scripts()` (`scripts/build-intergenos.sh`) — an `rsync -a --delete` of host `packages/ scripts/ config/ installer/ docs/ assets/ igos-build/ pkm/ intergen/` into the chroot copy — *before* invoking the tier driver, precisely so code changes between restarts are always reflected on a resume. A **manual** single-package build via `chroot-enter.sh` bypasses that step, so after a snapshot revert (topic 07) or any host-side recipe edit, the chroot copy is **frozen at snapshot state** and the build silently uses the **stale** recipe. (This has shipped a broken archive in practice: a post-revert manual `glibc-core` rebuild used a stale recipe whose `build.sh` lacked the DESTDIR-zoneinfo block, so the build "succeeded" against the wrong recipe and produced an archive with no zoneinfo data.)
>
> **Before any manual single-package build, refresh the chroot copy** (mirror what the orchestrator does):
>
> ```sh
> # mounts must exist first (topic 07 / chroot-setup.sh); then, on the host:
> for d in scripts packages config installer docs assets igos-build pkm intergen; do
>   sudo rsync -a --delete /mnt/intergenos/$d/ /mnt/igos/mnt/intergenos/$d/
> done
> sudo rsync -a /mnt/intergenos/igos-build.py /mnt/igos/mnt/intergenos/
> # verify the package you intend to build matches the host recipe:
> diff <(sha256sum /mnt/intergenos/packages/<tier>/<name>/build.sh | cut -d' ' -f1) \
>      <(sudo sha256sum /mnt/igos/mnt/intergenos/packages/<tier>/<name>/build.sh | cut -d' ' -f1) \
>   && echo "chroot recipe is CURRENT"
> ```

### Prerequisite for single-package builds — stage (and re-stage) source tarballs

> **The chroot `/sources` is frozen at snapshot-capture state too, exactly like the recipe copy above.** A reference snapshot (topic 07) bakes in whatever `/sources/*.tar.xz` existed when it was captured. Before a `--only` build of a tarball-backed package, re-stage the current host tarball — but the *failure mode of a stale tarball depends on the source type*:
>
> - **Downloaded (`https://`) sources keep a `sha256` pin.** A stale `/sources` tarball whose bytes no longer match the declared sha **fails source verification and aborts in about 0.1s, before any extraction**, with a `Verifying SHA256: <snapshot-sha>…` mismatch line. That fast-fail is the safety net.
> - **First-party generated sources carry `generated: true` and NO `sha256`** (forge, intergenos-theme, intergen-welcome, the four intergenos-extensions-*, bibata-cursor-theme, catppuccin-gtk-theme — the #3 change). With no pin there is **nothing to fast-fail on**: a stale-but-present `/sources` tarball is used *silently* and you build STALE content. For these, **regenerate** the tarball (`scripts/build-forge-tarball.sh` / `scripts/build-intergenos-source-tarballs.sh`) and re-stage it before the `--only` build; only a *missing* tarball hard-fails ("Source not found").
> - **`source: []` packages** (e.g. `intergen-mark`, `intergen-toggle`) build straight from the chroot **recipe copy** (via `IGOS_SOURCE_ROOT`/cwd), so the recipe-sync above is all they need — `/sources` is irrelevant to them.
>
> **Re-stage the current host tarball:**
>
> ```sh
> # for a generated source, regenerate it into build/sources/ first
> sudo cp /mnt/intergenos/build/sources/<name>-<ver>.tar.xz /mnt/igos/sources/
> # for a sha-pinned (https) source, confirm it now matches the declared sha:
> sudo sha256sum /mnt/igos/sources/<name>-<ver>.tar.xz
> ```
>
> **Diagnosing a 0.1s fast-fail:** a `--only` build that "FAILS in 0.1s" with no compile/patch output died at source staging — a **missing** `/sources` tarball (any source type), or a **sha mismatch** (sha-pinned `https://` sources only) — **not** a build-logic bug. Grep the log for `Verifying SHA256` / `Source not found`. Note a `generated: true` source with a *present-but-stale* tarball does **not** fast-fail — it silently builds the stale content, so regenerate to be sure.

### Build a single package (Python tier-driver)

> **Run this from *inside* the chroot.** The Python builder does **not** enter the chroot itself — it assumes it is already running inside `/mnt/igos`, which is how the orchestrator invokes it (each `phase_<tier>` runs `bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-<tier>.sh"` where `${SCRIPTS}` is the **absolute** `/mnt/intergenos/scripts` — chroot-enter execs `/bin/bash $*` with cwd `/` inside the chroot, so the script arg MUST be an absolute in-chroot path; a relative `scripts/...` fails `rc=127` command-not-found. That sub-script calls `python3 igos-build.py …` from in-chroot). The build toolchain (`meson`, `ninja`, the cross-compiler, and so on) exists **only inside the chroot**, never on the host. Running `python3 igos-build.py --only …` from a host shell therefore fails with `<tool>: command not found` (for example `meson: command not found`) for any package whose recipe needs those tools. (This has bitten in practice: a hand-rolled rebuild script ran the desktop/ai `--only` builds on the host and died at `xdg-user-dirs` with `meson: command not found`.)

The canonical single-package invocation is to enter the chroot first, then build inside it:

```sh
sudo bash scripts/chroot-enter.sh                       # drop into /mnt/igos
# now inside the chroot:
cd /mnt/intergenos && python3 igos-build.py --only <name> --build --sources-dir /sources
```

(For an automated/unattended single-package build, mirror the orchestrator: put the `python3 igos-build.py --only …` call in a small script and run it via `bash scripts/chroot-enter.sh /mnt/intergenos/scripts/<your-script>.sh` — the script path must be the absolute *in-chroot* path.)

> **Launch an unattended single-package (or multi-package batch) build as a `systemd-run` transient unit, not a foreground `sudo bash … chroot-enter.sh` over SSH.** The "mirror the orchestrator" guidance applies to the *launch* too, not only the wrapper script: a build tied to your SSH shell dies on disconnect and has no clean control surface. The same rule from the orchestrator section at the top of this topic — avoid a bare `sudo bash` — holds for `chroot-enter.sh`-driven single-package builds.
>
> ```sh
> UNIT=igos-onepkg-$(date +%H%M%S)
> sudo systemd-run --unit="$UNIT" --working-directory=/mnt/intergenos \
>   bash scripts/chroot-enter.sh /root/<wrapper>.sh
> # monitor:  journalctl -u "$UNIT" -f       control:  systemctl status|stop "$UNIT"
> ```
>
> The wrapper script's own redirects land **inside the chroot**: a wrapper that writes `> /tmp/build-<pkg>.log` is readable from the host at `/mnt/igos/tmp/build-<pkg>.log`.

This builds `<name>` and its dependency closure inside the existing chroot without touching other packages. The `--only` flag is the targeted-rebuild primitive used during package authoring (topic 08 step 5).

Variants:

- `--tier <tier>` — build every package in the named tier (`desktop` / `extra` / `compute` / `ai`).
- `--tracked` — accepted no-op: tracked deployment (manifest → archive → deploy → verify → register) is the default for `--build`. It is NOT a package-selection filter — an earlier revision of this page described it as one, incorrectly. The explicit non-deploying staging build is `--stage-only`.
- `--skip-built` — skip a package only when **both** conditions hold: (1) its `name-version` manifest already exists at `/var/lib/igos/packages/<name>-<version>`, **and** (2) its **template hash is unchanged**. The builder computes `sha256(package.yml + build.sh)` (truncated) and compares it to the `TEMPLATE_HASH:` marker recorded in the manifest (`igos-build/builder.py`, around lines 1004-1025). If the template changed, the log shows `Rebuilding <pkg> <ver> (template changed)` and the package **rebuilds even though the version is unchanged** — so you do *not* need to bump `release:` to force a rebuild after a `package.yml`/`build.sh` edit. (A *source-only* change that does not touch `package.yml`/`build.sh` will not flip the hash on its own — see topic 10 for that case and how to force it.) **This is not a name-version-only skip.** Describing it as "skip if the `name-version` match exists" is inaccurate and has led to planning mistakes (assuming a recipe change would be skipped when it actually rebuilds); the authoritative behavior is the template-hash trigger above, documented in full in topic 10. The manifest lookup in condition (1) is an **exact `name-version` match, not a greedy prefix** — an earlier greedy-prefix implementation silently swallowed packages whose name was a prefix of another (for example, an `at-*` match captured `at-spi2-core`, leaving `base/at` unbuilt and `/usr/bin/at` absent from the chroot). The pre-squashfs `verify_paths` audit (topic 04) catches the downstream symptom, and the exact-match behavior is now in place.

### Build a single package (bash static-list tier)

For tier `core` / `base`. **First do the recipe-sync above**, then select the one package with the `IGOS_START_AT` / `IGOS_STOP_AFTER` env-var pair and run the matching tier driver through `chroot-enter.sh`:

```sh
# pick the driver that lists the package:
#   LFS Ch.8 core set      -> chroot-build-ch8.sh        (e.g. glibc-core)
#   rest of tier core      -> chroot-build-core-extra.sh (e.g. openssh, intergenos-*)
#   tier base              -> chroot-build-base.sh
# (grep -l '<name>' scripts/chroot-build-*.sh  to find it)
sudo env IGOS_START_AT=<pkg-dir> IGOS_STOP_AFTER=<pkg-dir> IGOS_BUILD_DEBUG_VERBOSE=1 \
  bash /mnt/intergenos/scripts/chroot-enter.sh /mnt/intergenos/scripts/chroot-build-<driver>.sh
```

> **Unattended / SSH-launched form — wrap in `systemd-run`, env vars as a command prefix.** The systemd-run mandate (the Python-tier section above + the bare-`sudo bash` rule) applies to this bash driver too, but unlike the Python tier it has **no wrapper script** — package selection is the `IGOS_START_AT`/`IGOS_STOP_AFTER` env pair, so carry them as an `env …` prefix to the unit's command (NOT as the script body):
> ```sh
> U=igos-onepkg-$(date +%H%M%S)
> sudo systemd-run --unit="$U" --working-directory=/mnt/intergenos \
>   env IGOS_START_AT=<pkg-dir> IGOS_STOP_AFTER=<pkg-dir> IGOS_BUILD_DEBUG_VERBOSE=1 \
>   bash /mnt/intergenos/scripts/chroot-enter.sh /mnt/intergenos/scripts/chroot-build-<driver>.sh
> # monitor:  journalctl -u "$U" -f       status:  systemctl is-active "$U"
> ```
> `chroot-enter.sh` propagates the `IGOS_*` env into the chroot exactly as in the foreground form. (Verified 2026-06-15, GBC003.3 `pkm` rebuild via `chroot-build-core-extra.sh` — `IGOS_START_AT==IGOS_STOP_AFTER=pkm` built only `pkm` and stopped, archive written to `/var/lib/igos/archives/`.) Absolute in-chroot paths still required — see the next note.
>
> **Use ABSOLUTE in-chroot paths for BOTH the `chroot-enter.sh` and the inner driver script** (as `chroot-enter.sh`'s own usage example shows). `chroot-enter.sh` runs the inner script via `chroot "$IGOS" … /bin/bash $*` with the chroot's cwd at `/`, so a *relative* inner path (`scripts/chroot-build-<driver>.sh`) resolves to `/scripts/…` inside the chroot, which does not exist → the unit dies with **exit 127** (`bash: scripts/…: No such file or directory`) and builds nothing. The `/mnt/intergenos/scripts/…` recipe copy is the correct in-chroot location. (Verified 2026-06-15 during the GBC003.2 core rebuild — the relative form failed 127 repeatedly until switched to absolute.) The `systemd-run … --working-directory=/mnt/intergenos` orchestrator form only fixes the *outer* path resolution; the *inner* path must still be absolute.
>
> **⚠️ `chroot-enter.sh` takes a SCRIPT PATH argument ONLY — never an inline `-c "<command>"` form.** The script composes its command as `CHROOT_CMD="/bin/bash $*"` and expands it **unquoted** at the `chroot` invocation, so the shell word-splits it and every quote you wrote is discarded. An inline `bash scripts/chroot-enter.sh -c "cd /mnt/intergenos && python3 igos-build.py --only <pkg>"` therefore degrades to `/bin/bash -c cd` — which **runs `cd`, exits 0 in under a second, and builds nothing.** This is a silent false-green and is strictly more dangerous than the `exit 127` relative-path failure above, because the unit reports success and a tier log shows phase banners with no build output between them. (Passing `/bin/bash` as the first argument instead yields `/bin/bash /bin/bash` → `rc=126`.) Wrap any inline command in a driver script and pass that script's absolute in-chroot path; when rebuilding whole tiers, invoke the orchestrator's own `chroot-build-<tier>.sh` drivers, exactly as each `phase_<tier>` does.

`<pkg-dir>` is the package **directory name** (e.g. `glibc-core`, `openssh`) — the driver matches `IGOS_START_AT` against either the pkg-dir or the recipe `name`. Setting `START_AT == STOP_AFTER` builds exactly that one package and stops.

> **These drivers do NOT take a positional `<name>-<version>` argument.** Package selection is **only** via the `IGOS_START_AT` / `IGOS_STOP_AFTER` env vars (the script header documents the `IGOS_START_AT=nss IGOS_STOP_AFTER=nss` idiom). A bare `chroot-build-<phase>.sh foo-1.0` ignores `$1` entirely and — because `IGOS_START_AT` is empty — rebuilds the **whole tier** (hours for `core`). Always use the env-var pair for a single package.

> **Harvest the chroot-copy logs BEFORE any ISO-pipeline resume (`--start-at bootloader`/`image`).** The bash tiers write their per-package and tier logs to `$IGOS_LOGS` **inside the chroot copy** (host path `/mnt/igos/mnt/intergenos/build/logs/`), and `phase_image` tears the chroot down — destroying every log the post-burn trace audit needs (learned on a targeted burn where the bash-tier per-package logs were unrecoverable). After the rebuild completes and before resuming the ISO pipeline, copy them to the virtiofs-backed (host-persistent) tree:
> ```sh
> sudo rsync -a /mnt/igos/mnt/intergenos/build/logs/ \
>   /mnt/intergenos/build/logs/chroot-harvest-$(date +%Y%m%d-%H%M%S)/
> ```

## Orphan detection

```sh
python3 scripts/check-builder-coverage.py
```

Walks every `packages/<tier>/<name>/package.yml` and reports any package not reachable by exactly one builder. Run after authoring a new package (topic 08 step 4) and as a periodic health check on master. An orphan won't surface as a failure until the pre-squashfs audit halts; the orphan detector catches it earlier.

## Validation

A clean full build produces:

- `build/intergenos.qcow2` — bootable disk image (topic 06 boots it). Produced by `phase_image`.
- `build/intergenos-<version>.iso` — bootable hybrid ISO with signed UKIs. Produced by `phase_iso` after `phase_squashfs` lands the live filesystem and the topic-03 signing pass has produced signed bootloader artifacts.
- `build/intergenos-archive-manifest.txt` — BSD-style sha256 archive-integrity manifest covering every `*.igos.tar.gz`. Produced by `phase_manifest`. (Declared-vs-built completeness is not in this file; it is enforced by `phase_validate`'s coverage gates and the `phase_squashfs` audits, Steps 4.4/4.5/4.6.)
- `build/logs/build-intergenos-<timestamp>.log` — full log, with no FATAL entries and no "STOP" halt entries past phase boundaries.
- The pre-squashfs audit (step 4.5 of `build-squashfs.sh`) passes cleanly during `phase_squashfs`.
- The per-build artifact directory (per the lineage scheme above) contains the unsigned bootloader inputs, signed outputs, manifest, and ISO from this run, traceable end-to-end.
- The ship-time compliance gate passes during `phase_image` (`scripts/check-d007-compliance.sh` returns 0; no stray SSH, root, or credentials violations).

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `phase_validate` halts | Build VM is missing a required tool OR the host clone is out of sync | Install the missing tool per the halt message; `git pull --ff-only` the host clone |
| `phase_verify_sources` halts on sha256 mismatch | A source tarball was modified or the package.yml sha256 is stale | Investigate — if upstream changed, update the sha256 in package.yml (audit the diff first); if tarball corrupted, re-download |
| `phase_setup` fails on `build-forge-tarball.sh` | Forge sources moved or the tarball generator regressed | Inspect `scripts/build-forge-tarball.sh`; common cause: `installer/data/` content changed and the bundler's file-list is stale |
| `phase_setup` log fills with `chown ... '/mnt/igos/proc/...': Operation not permitted` (hundreds of lines) | `phase_setup` is re-running against an already-populated chroot with active `/proc` `/sys` `/dev` mounts. Indicates the resume command was `IGOS_START_AT=<pkg>` alone without the required `--start-at <phase>` companion flag | **STOP THE BUILD IMMEDIATELY**. The chown may have already rewritten ownership of root-owned system files in the chroot — see "Failure mode — phase_setup chown on a populated chroot" above for full recovery via checkpoint-restore. |
| Build "went back to the start" / re-ran `phase_validate` when you expected a resume | Used `IGOS_START_AT=<pkg>` without `--start-at <phase>` | The env var is consumed only by the INNER chroot-build sub-script and only when control reaches its phase. The outer orchestrator runs all phases unless `--start-at <phase>` is given. Add the `--start-at <phase>` flag — see "Resume at a specific PACKAGE within a phase (combined invocation)" above. |
| Phase X halts with `*-fail` or `Halt: error` | Step 0 — read the actual error. Classify per the symptom column. Don't apply forbidden workarounds (retiering, --tests-disable, and similar). | Bring the classification + canonical fix to the maintainer for review; resume with `--start-at <phase>` after fix |
| `phase_squashfs` Step 4.5 (verify_paths) halts | A package built but didn't register (DESTDIR bypass), OR a package registered without the declared verify_paths files landing | Inspect the failing paths — a declared file for a package isn't present in the assembled chroot; trace it to a DESTDIR bypass or a missing/wrong verify_paths entry |
| Build mysteriously slows down mid-run | Background `unattended-upgrades` woke up despite the apt-timer mask | Check `systemctl list-timers --all` for active apt timers; re-mask per topic 01 step 4 |
| Targeted rebuild leaves chroot inconsistent | Half-built package state OR stale `--skip-built` matching | Clean the package's build dir under `/mnt/igos/build/...`; re-run with exact `name-version` |
| `--skip-built` silently skips a package you intended to rebuild | Exact-version match means the chroot already has `<name>-<version>` | Bump release in package.yml OR remove the existing package from the chroot first |

## Cross-references

- Topic 01: build-VM setup — produces the state this script runs in
- Topic 03: signing — consumes the unsigned bootloader outputs from `phase_bootloader`
- Topic 04: squashfs generation — its own phase `phase_squashfs` (phase 19), after `phase_image` cleans the chroot
- Topic 05: ISO creation — its own phase `phase_iso` (phase 21), after `phase_ukis_verity` + the signing ceremony
- Topic 07: reference snapshot — clean starting state for fresh builds
- Topic 08: adding packages — what to do before running the build to onboard a new recipe
- `scripts/build-intergenos.sh` — canonical entry-point reference
- `igos-build.py` — Python tier driver, the source of truth for builder flags
