# 02 — Running the builder

**Audience:** maintainers driving a full or partial InterGenOS build campaign.

## Goal

Take a known-clean build VM (topic 01 + topic 07 golden snapshot) through the full 19-phase build pipeline that ends with a bootable disk image at `/mnt/intergenos/build/intergenos.qcow2` plus a bootable hybrid ISO at `/mnt/intergenos/build/intergenos-<version>.iso`, plus all the surgical-rebuild tools for iterating on a partial set of packages without re-running the full chain.

The canonical entry point is `scripts/build-intergenos.sh` running inside the build VM. It delegates to two lower layers:

- **Bash static-list builders** (`scripts/chroot-build-<phase>.sh`) for tiers `core` / `base` / `core-extra` / `bootloader` (and the LFS Chapter 5-8 tool-chain).
- **Python tier-driver** (`igos-build.py`) for tiers `desktop` / `extra` / `ai`.

## Prerequisites

- Build VM is at golden state (topic 07): `/mnt/igos` empty, apt-timers masked, virtiofs mounted, build toolchain on PATH.
- The host clone of the repo is current — i.e., `git pull --ff-only origin master` on the workstation, with the change visible inside the VM via `/mnt/intergenos`.
- Sources are downloaded and mirrored — `python3 scripts/download-sources.py` (or whichever mirror-fetcher is active for the campaign) has produced `build/sources/<tarball>` per the `source: url:` declarations in every `package.yml`.
- Patches available at `build/patches/` if any package recipe consumes one.
- Disk free: at least 100GB for a fresh build, more comfortable around 200GB. The chroot, sources, logs, archives, and the final qcow2 image add up.
- (For signing-pipeline runs) An offline signing workstation is ready and the operator is reachable for the ceremony (topic 03).

## Full-build invocation

The clean-VM happy path. Owner-direct discipline: the build is a **long-running operation** with multiple synchronous-attention points (signing ceremony pauses, MOK enrollment, smoke evaluation). Plan for it like a multi-hour session.

```sh
ssh christopher@192.168.122.249
cd /mnt/intergenos
sudo bash scripts/build-intergenos.sh \
    --user christopher \
    --root-password 'PASSWORD-FOR-IMAGE-ROOT' \
    --user-password 'PASSWORD-FOR-IMAGE-USER'
```

- `--user <name>` is required; sets the IMAGE_USER created in the disk image.
- `--root-password` and `--user-password` are required (no defaults permitted; the `intergenos` literal default has been permanently retired). These set the image's root + IMAGE_USER passwords. The first-boot greeter overwrites both on the end-user's first boot — these are the brief-window fallback nobody normally encounters.
- `--checkpoint` is recommended for long runs; emits a chroot tarball after each significant phase under `build/checkpoints/`. Required by Rule 16 for any non-trivial campaign — checkpoints turn "phase 12 failed → start over" into "phase 12 failed → start at phase 12."

The orchestrator writes its log to `build/logs/build-intergenos-<timestamp>.log` and records the phase state at `build/logs/.build-phase`. The phase state is what `--start-at` reads to know where to resume.

### Phase sequence (canonical order)

| # | Phase | What runs | Builder layer |
|---|---|---|---|
| 1 | `validate` | Host pre-flight: required tools, kernel features, free disk, user identity, `feedback_*` discipline checks if active | n/a |
| 2 | `verify-sources` | Confirms every `package.yml`'s declared sha256 matches the local tarball at `build/sources/` | n/a |
| 3 | `setup` | Builds `/mnt/igos` skeleton, stages `/mnt/intergenos/{scripts,packages,igos-build,pkm,intergen}` into the chroot's `/mnt/intergenos/` mirror. Also runs `scripts/build-forge-tarball.sh` to regenerate `build/sources/forge-1.0.0.tar.xz` from in-tree state | n/a |
| 4 | `toolchain` | LFS Chapter 5-6 — bootstrap toolchain on the host targeting `x86_64-igos-linux-gnu` | `scripts/toolchain-build.sh` |
| 5 | `chroot-prep` | Mounts pseudo-fs into `/mnt/igos`, copies kernel headers, sets up `/etc` skeleton | `scripts/chroot-setup.sh` |
| 6 | `chroot-tools` | LFS Chapter 7 — temporary tools inside the chroot | `scripts/temp-tools-build.sh` (chroot-internal) |
| 7 | `core` | LFS Chapter 8 — core packages (glibc, gcc-pass2, binutils-pass2, etc.) — bash static list | `scripts/chroot-build-ch8.sh` |
| 8 | `config` | LFS Chapter 9 — system configuration (network, /etc/hosts, basic services) | `scripts/chroot-config-ch9.sh` |
| 9 | `core-extra` | Tier `core` packages outside the LFS Ch8 set — bash static list | `scripts/chroot-build-core-extra.sh` |
| 10 | `base` | Tier `base` packages — bash static list | `scripts/chroot-build-base.sh` |
| 11 | `kernel` | Linux kernel + initramfs build | `scripts/chroot-build-bootloader.sh` (kernel portion) |
| 12 | `desktop` | Tier `desktop` packages — GNOME + deps, Python topological sort | `igos-build.py --tier desktop` |
| 13 | `ai` | Tier `ai` packages | `igos-build.py --tier ai` |
| 14 | `extra` | Tier `extra` packages | `igos-build.py --tier extra` |
| 15 | `bootloader` | shim + grub + UKI assembly (unsigned outputs; signing happens out-of-band via topic 03) | `scripts/chroot-build-bootloader.sh` |
| 16 | `image` | `create-image.sh` → qcow2 disk image; runs `scripts/check-d007-compliance.sh` as a Class A ship-gate (blocks on violation per D-007) | `scripts/create-image.sh` |
| 17 | `manifest` | Rule 18 manifest reconciliation: chroot packages vs package.yml-declared vs deferred — halt-with-diff if they disagree | n/a |
| 18 | `squashfs` | `build-squashfs.sh` — live-ISO root filesystem squashfs; runs AFTER `phase_image` cleans the chroot | `scripts/build-squashfs.sh` |
| 19 | `iso` | `build-iso.sh` — assembles the live ISO (signed-release or unsigned-test mode); consumes signed bootloader artifacts produced out-of-band via topic 03 | `scripts/build-iso.sh` |

Optional 20th phase: `publish` (gated by `--publish` flag) — runs `scripts/publish-repo.sh` to push the signed repo to the VPS.

Phases 18 (`squashfs`) + 19 (`iso`) were wired into the orchestrator at commit `23940db9`. Prior to that commit, the orchestrator ended at `phase_manifest` and the operator ran `build-squashfs.sh` + `build-iso.sh` by hand outside the orchestrator. Resume-from-failure semantics: `--start-at squashfs` skips phases 1-17 and resumes at squashfs; `--start-at iso` skips through 18 and resumes at iso. Both phases preserve resume state in `build/logs/.build-phase`.

### Per-build artifact lineage (cycleN)

Each full-build run writes its artifacts to a per-cycle directory under `build/` to preserve lineage: unsigned bootloader inputs, signed outputs, the manifest emitted atomically with the final ISO, and the ISO itself live in one identifiable per-build set. Prior to commit `23940db9` the manifest was emitted before final ISO assembly, with the result that the manifest's input-SHAs could record a different generation than the ISO they were intended to describe (the cycle-5 ISO at `build/intergenos-1.0-dev1-smoke.unsigned-test.iso` exhibited this exact drift — manifest input-SHAs referenced an earlier generation of UKIs than the ones the ESP shipped). The per-build cycleN layout + atomic manifest emit closes that class of build-provenance brokenness (audit B-018 + B-034).

## Surgical-rebuild invocations

When the chroot is already populated and you want to rebuild a small set of packages without re-running prior phases.

### Resume at a specific phase

```sh
sudo bash scripts/build-intergenos.sh --user christopher --start-at desktop \
    --root-password '…' --user-password '…'
```

`--start-at desktop` skips phases 1-11 and resumes at desktop. Useful after a failed phase 12 — fix the cause, resume from the failure point. Requires `build/logs/.build-phase` to reflect a prior successful run through phase 11.

### Stop after a specific phase

```sh
sudo bash scripts/build-intergenos.sh --user christopher --stop-after core \
    --root-password '…' --user-password '…'
```

Stops cleanly after the named phase. Useful for getting to a known intermediate state (e.g., "stop after core so I can inspect the chroot before phase_config runs").

### Mid-run graceful halt

Touch `/mnt/igos/.build-stop` between phases. The orchestrator checks for this file between phase boundaries and halts cleanly. Useful when you want to pause a long-running build to investigate a failure without losing in-progress phase state.

### Ctrl+C — immediate stop

The orchestrator traps SIGINT and exits. Phase state is preserved (the current package is half-built). Resuming with `--start-at <last-incomplete-phase>` picks up where it left off, but the half-built package may need a manual rollback (`rm -rf "${IGOS_BUILD}/<name>-<version>"`) first.

### Build a single package (Python tier-driver)

```sh
sudo python3 igos-build.py --only <name>
```

Builds `<name>` and its dependency closure inside the existing chroot. Doesn't touch other packages. The `--only` flag is the surgical-rebuild primitive used during package authoring (topic 08 step 5).

Variants:

- `--tier <tier>` — build every package in the named tier (`desktop` / `extra` / `ai`).
- `--tracked` — limit to packages currently registered in `/var/lib/igos/packages/` (skips never-built recipes).
- `--skip-built` — skip packages whose **exact `name-version` match** already exists in the chroot. **Exact-version match, NOT greedy prefix** — this distinction matters; an earlier greedy-prefix implementation silently swallowed packages whose name was a prefix of another (e.g., `at-*` matched `at-spi2-core`, leaving `base/at` un-built and `/usr/bin/at` absent from the chroot). The pre-squashfs verify_paths audit (topic 04) catches the downstream symptom; the canonical fix shipped at master tip `86109772`.

### Build a single package (bash static-list tier)

For tier `core` / `base`:

```sh
sudo chroot /mnt/igos /mnt/intergenos/scripts/chroot-build-<phase>.sh <name>-<version>
```

Exact-version literal; greedy prefix-match has the same hazard as above.

## Orphan detection

```sh
python3 scripts/check-builder-coverage.py
```

Walks every `packages/<tier>/<name>/package.yml` and reports any package not reachable by exactly one builder. Run after authoring a new package (topic 08 step 4) and as a periodic health check on master. An orphan won't surface as a failure until the pre-squashfs audit halts; the orphan detector catches it earlier.

## Validation

A clean full build produces:

- `build/intergenos.qcow2` — bootable disk image (topic 06 boots it). Produced by `phase_image`.
- `build/intergenos-<version>.iso` — bootable hybrid ISO with signed UKIs. Produced by `phase_iso` after `phase_squashfs` lands the live filesystem and the topic-03 signing pass has produced signed bootloader artifacts.
- `build/manifest-reconciliation-<timestamp>.txt` — Rule 18 reconciliation, no unaccounted diffs. Produced by `phase_manifest`.
- `build/logs/build-intergenos-<timestamp>.log` — full log, no FATAL entries, no "STOP" halt entries past phase boundaries.
- The pre-squashfs audit (step 4.5 of `build-squashfs.sh`) passes cleanly during `phase_squashfs`.
- Per-build cycleN artifact directory (per the lineage scheme above) contains the unsigned bootloader inputs, signed outputs, manifest, and ISO from this run — traceable end-to-end.
- D-007 compliance gate passes during `phase_image` (`scripts/check-d007-compliance.sh` returns 0; no SSH/root/credentials violations).

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `phase_validate` halts | Build VM is missing a required tool OR the host clone is out of sync | Install the missing tool per the halt message; `git pull --ff-only` the host clone |
| `phase_verify_sources` halts on sha256 mismatch | A source tarball was modified or the package.yml sha256 is stale | Investigate — if upstream changed, update the sha256 in package.yml (audit the diff first); if tarball corrupted, re-download |
| `phase_setup` fails on `build-forge-tarball.sh` | Forge sources moved or the tarball generator regressed | Inspect `scripts/build-forge-tarball.sh`; common cause: `installer/data/` content changed and the bundler's file-list is stale |
| Phase X halts with `*-fail` or `Halt: error` | Per the rulebook decision tree (`docs/build-development-rulebook.md` Section 2). Step 0 — read the actual error. Classify per the symptom column. Don't apply forbidden workarounds (Rule 19, retiering, --tests-disable, etc.) | Bring the classification + canonical fix to the maintainer per RULE #0; resume with `--start-at <phase>` after fix |
| `phase_manifest` halts with diff | A package built but didn't register (DESTDIR bypass), OR a package registered without the verify_paths landing, OR a deferred `*.deferred` entry isn't accounted for | Inspect the diff line-by-line — every entry has one of those three root causes |
| Build mysteriously slows down mid-run | Background `unattended-upgrades` woke up despite the apt-timer mask | Check `systemctl list-timers --all` for active apt timers; re-mask per topic 01 step 4 |
| Surgical rebuild leaves chroot inconsistent | Half-built package state OR stale `--skip-built` matching | Clean the package's build dir under `/mnt/igos/build/...`; re-run with exact `name-version` |
| `--skip-built` silently skips a package you intended to rebuild | Exact-version match means the chroot already has `<name>-<version>` | Bump release in package.yml OR remove the existing package from the chroot first |

## Cross-references

- Topic 01: build-VM setup — produces the state this script runs in
- Topic 03: signing — consumes the unsigned bootloader outputs from `phase_bootloader`
- Topic 04: squashfs generation — invoked from `phase_image`
- Topic 05: ISO creation — invoked from `phase_image`
- Topic 07: golden snapshot — clean starting state for fresh builds
- Topic 08: adding packages — what to do before running the build to onboard a new recipe
- `docs/build-development-rulebook.md` — canonical Rules 1-21; the decision tree at Section 2 is the halt-handler playbook
- `scripts/build-intergenos.sh` — canonical entry-point reference
- `igos-build.py` — Python tier-driver source of truth
