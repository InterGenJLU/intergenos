# 09 — The GBCNNN Iteration Methodology

**Audience:** maintainers driving a release candidate from "all packages build" to "boots live, boots the installer, and installs cleanly on real hardware," plus the rationale for *why* that last mile is treated as a first-class, repeatable cycle rather than an afterthought.

This is the document the other runbooks point at for one principle: a build whose packages all compiled is **not** a build that is known-good. The bugs that matter most surface at *install* and *first boot*, not at compile time. This methodology exists to find them on purpose, fix them durably in the tree, and prove the fix on the next clean build — every time, the same way.

## Goal

Codify the **GBCNNN iteration methodology** so it is not reinvented each release candidate:

1. The terminology — **GBC** (candidate) vs **GB** (promoted Golden Builder), and how a GBC relates to the topic-07 Golden Builder.
2. The **cost-of-deferral doctrine** — why latent bugs surface at install time, and why that forces a real iteration loop.
3. The **GBC → GB promotion workflow** — iterate on a candidate snapshot until it cleanly boots + installs; mint the next candidate only by a full from-scratch build.
4. The **`[AUTO]` / `[CHROOT]` layer key** — what survives a clean iteration untouched vs what must be re-applied to the chroot each cycle.
5. **Grouping-as-layer** — how findings are organized into `GBCNNN.N` groupings, each a coherent layer applied and validated together.
6. The **iteration ledger** format and where it lives.

## Terminology

| Term | Meaning | Capture point |
|---|---|---|
| **GBCNNN** | Golden-Builder-**Candidate** N. A VM snapshot of the build VM taken at the `--stop-after ai` halt of a **full from-scratch** build. The thing under validation. | `--stop-after ai` (all package tiers built; bootloader **not** yet assembled) |
| **GB** | Promoted **Golden Builder** — the stable-channel substrate, reached only after a candidate passes a clean from-scratch end-to-end cycle. | conceptual promotion; see topic 07 for the fast-ISO Golden Builder snapshot |
| **`GBCNNN.N`** | A numbered **fix grouping** within a candidate's iteration — a coherent batch of related fixes applied and validated together as one layer. | n/a (a ledger construct) |
| **Candidate serial codes** (`ge-NN`, `ge9b-NN`, …) | Short codes naming individual build candidates within an iteration series: a series tag plus an ordinal that rotates on every re-mint (topic 05's ISO naming discipline). Engineering comments throughout this tree cite these codes to record which build run first surfaced a defect — the provenance trail behind a fix. A cited code is a historical run reference, nothing more. | n/a (a naming construct) |

### How a GBC differs from the topic-07 Golden Builder

Both are libvirt VM snapshots of the build VM, but they are captured one phase apart, by design:

- **The topic-07 Golden Builder** is captured at **`--stop-after bootloader`**. Its purpose is *fast ISO regeneration of an already-proven build* — the recipes are known-good, you just want a new ISO, so the bootloader/UKI assembly is already baked in and you resume at `--start-at image`.
- **A GBC** is captured one phase earlier, at **`--stop-after ai`** (the final package phase since the 2026-07-21 reorder). Its purpose is *validation of an unproven candidate*. The bootloader → UKI/verity → ISO → install chain is **deliberately re-exercised on every iteration**, because that chain is exactly where latent bugs surface (see the doctrine below). Capturing before `phase_bootloader` keeps that chain inside the loop instead of frozen into the snapshot.

They are complementary, not contradictory. A GBC is what you iterate a *candidate* on; once a candidate is promoted to GB, a topic-07 Golden Builder is the right artifact for routine ISO-only regeneration of that proven build.

> The canonical build-phase order (authoritative source: the `run_phase` block in [scripts/build-intergenos.sh](../../scripts/build-intergenos.sh)) is:
> `validate → verify-sources → setup → toolchain → chroot-prep → chroot-tools → core → config → core-extra → base → kernel → desktop → extra → compute → ai → bootloader → image → manifest → squashfs → ukis-verity → iso` (+ optional `publish`).
> Note `ai` runs **last** of the package phases (reordered 2026-07-21; it previously ran before `extra`). InterGen / LLM-stack fixes are ai-tier — they live *before* the candidate's capture point, and a bare `--start-at bootloader` will **not** pick them up.

## The doctrine: latent bugs surface at install, not build

The expensive failures of a from-source distribution are not compile errors. They are the class of defect that compiles fine, packages fine, and only manifests when the artifact is actually *used*:

- a service unit with `ProtectSystem=strict` and no `ReadWritePaths` — builds clean, fails the moment the daemon tries to write state on a real install;
- a directory omitted from a package's copy list — builds clean, the registry that depends on it silently initializes empty at runtime;
- a hardware-detection heuristic that's wrong for an integrated GPU — builds clean, picks a model the target can't run;
- a boot-device label collision — builds clean, drops to a GRUB prompt nondeterministically on some hardware.

None of these are caught by "all packages built, 0 failures." Every one of them is caught by *installing the artifact and booting it on real hardware*. That is why the candidate loop **always includes the bootloader → ISO → install → boot chain**, and why a candidate is never trusted on the strength of a clean build alone. A Golden Builder minted from recipes that were never install-validated can silently bake in a regression — the lesson topics 04, 06, and 07 all reinforce.

**Corollary — validate on a clean build + install, never on a hand-patched dev box.** A fix that only works because of a manual edit on the running target is not a fix; it is a note about a fix. Nothing is considered done until it lives in the tree *and* a clean build reproduces the behavior with zero manual intervention (signing ceremony excepted — that is the one sanctioned human step).

## The GBC → GB promotion workflow

```
                 ┌────────────────────────────────────────────────┐
                 │  full from-scratch build  --stop-after ai        │
                 │            snapshot  ⇒  GBCNNN                    │
                 └───────────────────────┬────────────────────────┘
                                         │
              ┌──────────────────────────▼───────────────────────┐
              │  ITERATE on the candidate (this doc):             │
              │   land fix in tree → rebuild affected tier into   │
              │   chroot → --start-at bootloader → ISO → install  │
              │   on real HW → validate → repeat                  │
              └──────────────────────────┬───────────────────────┘
                                         │  clean: live-boots, installer-boots, installs
                          ┌──────────────▼───────────────┐
                          │  next full from-scratch build  │  ← proving ground
                          │  every fix already in tree     │
                          │  signing = sole intervention   │
                          └──────────────┬────────────────┘
                       triggers? ────────┤───────── none
                  iterate the new GBCNNN  │  promote  GBC ⇒ GB
                                          ▼
                                  stable-channel substrate
```

1. **Iterate on the current candidate.** Apply fixes against the GBC snapshot substrate, rebuild the ISO from it via `--start-at` resumes + targeted chroot rebuilds (**not** from scratch), boot-test, repeat — until it cleanly (a) boots the live "Try InterGenOS" session, (b) boots into the installer, and (c) installs without issue. **All three.**
2. **Mint the next candidate from scratch.** Ensure every fix from step 1 is in the tree. Run a fresh **full** build, `--stop-after ai` → that snapshot is the next `GBCNNN`. The signing ceremony is the **sole** human intervention. It must then boot the installer and install. If anything breaks anywhere (ISO creation / live boot / installer boot / install), iterate on *that* GBCNNN exactly like step 1.
3. **Promote `GBC → GB`** only when a full-from-scratch cycle runs end-to-end — build → `--stop-after ai` snapshot → ISO → live → installer → install on target — with **no triggers** (nothing required a fix). Then promote; iteration is done for the stable channel.

### Surgical chroot edits are the iteration mechanism, not "debt"

Manual chroot surgery on a candidate snapshot is the **intended** tool for iterating closer to real-time — it is what the snapshots exist for, and it deliberately avoids a from-scratch rebuild for every fix. The discipline is twofold (it is **not** "minimize surgery"):

1. **Every fix is saved to the tree, always.**
2. **No surgical edit is allowed to "ride."** Each must be proven on the subsequent from-scratch cycle — the tree fix reproduces the result with no surgery. That prove-on-next-cycle loop *is* the iteration. The from-scratch run is the designed **proving ground**, not a dreaded event.

### What triggers a new GBC cycle

A new from-scratch candidate cycle is triggered whenever an issue prevents a clean end-to-end run with **zero** fixes required. In practice the strongest trigger is a **full install onto a target system**: reaching a clean install exercises the whole chain (ISO → live → installer → install), forcing every accumulated surgical edit to prove itself in the next from-scratch build.

## The `[AUTO]` / `[CHROOT]` layer key

Every fix in an iteration is tagged with how it propagates into the next ISO. This is the single most useful piece of bookkeeping in the loop, because it tells you exactly what work a `--start-at bootloader` resume does *not* do for you:

| Tag | Meaning | Re-apply cost per iteration |
|---|---|---|
| **`[AUTO]`** | In-tree, at the **bootloader phase or later** (lives under `/mnt/intergenos`, read fresh on every run). Pulled in automatically by a `--start-at bootloader` resume. | none — the resume reads it |
| **`[CHROOT]`** | Requires a rebuild/patch **into `/mnt/igos`** (e.g. rebuild a package whose phase is *before* the capture point). The fix is durable in the tree; the *rebuild-into-chroot* is the repeated manual step each cycle. | rebuild the affected tier/package into the chroot before resuming |
| **`[INFRA]`** | Lives outside the build entirely (VPS, mirror, host config). Validated/applied once; not part of the chroot or the resume. | one-time (plus re-validation) |

The decisive question for tagging is **"is the changed file read by a phase at or after the candidate's resume point?"** Since a GBC is captured at the final package phase (`ai`) and iteration resumes at `bootloader`:

- A fix to `installer/`, `packages/core/grub/`, image/ISO config, or anything consumed by `bootloader`/`image`/`squashfs`/`ukis-verity`/`iso` → **`[AUTO]`**.
- A fix to any package in any tier — `core`, `base`, `desktop`, `extra`, `compute`, or `ai`, all of which build before the capture point → **`[CHROOT]`** (rebuild that tier into the chroot first; `--start-at bootloader` alone will not rebuild it).

> **Worked example.** InterGen runtime fixes are ai-tier. The mechanics are: `virsh start igos-build` (boots straight into the candidate state — no revert) → re-establish + verify the chroot bind mounts (`proc`/`sys`/`dev`/`run` into `/mnt/igos` **drop on a VM reboot**) → land the fix in the tree → **rebuild the ai tier into the chroot** (`--only intergen` or `--start-at ai`) → then `--start-at bootloader` → image → squashfs → ukis-verity → iso → install → validate. Skipping the ai-tier rebuild ships the *old* binary in a new ISO — the classic `[CHROOT]`-mistagged-as-`[AUTO]` failure.

### The build substrate — live chroot vs. snapshot rollback

Every rebuild path below (full, slipstream, mirror-republish) starts by building the changed package into the chroot. There are **two valid substrates** to build it on, and they produce **identical archives** — the build only depends on the toolchain + deps + recipe being present, which both satisfy:

| Substrate | Cost | When to use |
|---|---|---|
| **Live chroot, in place** | No revert. If the chroot has already been through `phase_image` (a post-ISO state), its `/mnt/igos/mnt/intergenos` recipe scratch was stripped — recreate it first (the `mkdir -p` + recipe-rsync in slipstream step 1 below). `/sources` and `/var/lib/igos/archives` survive `phase_image`. | **Default** for single/few-package iteration — fastest, no Rule-D snapshot op. |
| **Rollback to `gbcNNN_<datestamp>`** | A `virsh snapshot-revert` (Rule D, ~1 min) — loses the live state. The snapshot (captured shutoff at the final-package-phase halt) already carries the recipe copy + sources, so no recreation step. | When the live chroot is contaminated/uncertain, or you want a **guaranteed-pristine** baseline. |

This is why topic 07 says *"don't restore — rebuild against the live chroot"* and the worked example above boots *"straight into the candidate state — no revert."* Rollback is the fallback for a known-clean start, not a prerequisite for every iteration. (Proven 2026-06-15: the `pkm`/helper republish built all 9 packages on the live post-ISO chroot after recreating the recipe copy — no revert needed.)

### Slipstream — the single-package light path (no re-squashfs, no new ISO)

The worked example above is the **full** path: rebuild the package, then re-run `bootloader → image → squashfs → ukis-verity → iso` to mint a fresh ISO. That is the right choice when you need a new bootable artifact (validating a from-scratch candidate, or a fix that changes the live ISO / an `[AUTO]` phase). But for iterating a **single package** against an ISO that is **already booted** on the target, there is a far lighter path that skips the entire squashfs/ISO rebuild — **slipstream the one rebuilt archive into the booted live ISO's install set.** This is the day-to-day iteration workhorse and turns a ~1-hour ISO rebuild into a one-file copy.

It works because of three facts — **verified in the code, not assumed**:

1. **Forge installs from package archives in a directory**, not from a squashfs-rootfs copy: `run_install(…, archive_dir, …)` → `get_archives(archive_dir)` installs each `<name>-<version>.igos.tar.gz` (`installer/backend/packages.py` `get_archives`/`install`).
2. **That directory defaults to `/var/lib/igos/archives`** (`installer/backend_service.py` `DEFAULT_ARCHIVE_DIR`; `installer/__main__.py` — `forge --archives …`, default applied when the flag is absent). The shipped squashfs carries every package archive there.
3. **The live root is an overlayfs** — `installer/init/init.sh` mounts a writable tmpfs **upper** over the dm-verity read-only squashfs **lower**. So `/var/lib/igos/archives` is **writable in the live session**: dropping a new archive there shadows the read-only one without touching the squashfs (or breaking its verity).

**The procedure:**

1. On the build VM, rebuild ONLY the changed package in the chroot — **not** the whole tier (`--only` builds just that package + its dependency closure):
   ```sh
   # Recipes must be current FIRST — sync after any snapshot revert (topic 02),
   # else the chroot builds the STALE recipe copy at /mnt/igos/mnt/intergenos.
   # On a snapshot where the recipe copy was never created (sync_chroot_scripts
   # only makes it during orchestrator phases — e.g. a final-package-phase-halt
   # snapshot), mkdir its parent first or the rsync fails "No such file":
   #   sudo mkdir -p /mnt/igos/mnt/intergenos
   #   for d in scripts packages config installer docs assets igos-build pkm intergen; do
   #     sudo rsync -a --delete /mnt/intergenos/$d/ /mnt/igos/mnt/intergenos/$d/ ; done
   #   # also stage the entry point if absent: sudo rsync -a /mnt/intergenos/igos-build.py /mnt/igos/mnt/intergenos/
   sudo bash scripts/chroot-enter.sh        # drop into /mnt/igos, then in-chroot:
   cd /mnt/intergenos && python3 igos-build.py --build --tracked --only <name> --sources-dir /sources
   ```
   **Tracked deployment is the DEFAULT (since 2026-07-14; `--tracked` is an accepted no-op).** The manifest→archive→deploy phase writes the fresh `/var/lib/igos/archives/<name>-<version>.igos.tar.gz` (the slipstream artifact) on every `--build`. Earlier builder versions required an explicit `--tracked`; without it the build installed to DESTDIR, reported `SUCCESS`, and produced **no** archive — silently leaving the stale one in place. The non-deploying staging build now requires the explicit `--stage-only`. (Mirrors `chroot-build-desktop.sh`, which invokes `--build --tracked --only`.)
   (Bash static-list tier packages — `core`/`base` — use `IGOS_START_AT=<name> IGOS_STOP_AFTER=<name>` via the matching `chroot-build-<phase>.sh` instead; topic 02 — that bash path archives on its own, no `--tracked`.)
   **For a desktop/ai package whose source lives under `assets/` or `intergen/`:** the build consumes a **pre-staged** source tarball at `/sources` and verifies its sha against the `package.yml` pin — the manual `--only` path does **not** auto-regenerate it (only the orchestrator's `ensure_sources_staged` does). So after editing the asset: (a) run `scripts/build-intergenos-source-tarballs.sh` (regenerates `build/sources/<name>-<v>.tar.xz` **and** rewrites the pin from the same bytes — keep only the pins you intend to change), then (b) stage it: `sudo cp build/sources/<name>-<v>.tar.xz /mnt/igos/sources/`. Ensure the asset tree has **no** `__pycache__/*.pyc` first — they're gitignored, but `rsync` still carries on-disk `.pyc` into the chroot and pollutes the tarball (→ sha mismatch).
2. Copy that one archive onto the **booted** live ISO and into its archive dir (same filename → replaces the old one):
   ```sh
   scp /mnt/igos/var/lib/igos/archives/<name>-<version>.igos.tar.gz <user>@<live-ip>:/tmp/
   ssh <user>@<live-ip> 'sudo cp /tmp/<name>-<version>.igos.tar.gz /var/lib/igos/archives/'
   ```
3. Run the installer on the live ISO — it reads `/var/lib/igos/archives` (the default) and installs the slipstreamed package; the installed system (and its first boot) reflects the new package.

**Use the full path (re-squashfs + new ISO) when:** minting/validating a from-scratch candidate, the live ISO itself must change (kernel, bootloader, the squashfs a *live* session reads, anything `[AUTO]`), or you need a distributable artifact. **Use slipstream when:** iterating one (or a few) installable package(s) against an ISO already booted on the target.

> **Doc provenance:** the three facts above were validated against the code on 2026-06-08 (the slipstream had been performed repeatedly in practice but was undocumented; the prior text described only the full-rebuild path). If `installer/` changes the archive-dir default or the live overlay model, re-verify and update here.

### Mirror-republish — shipping a package fix to ALREADY-INSTALLED systems

Slipstream targets a live ISO that has not been installed yet. Once a system is **installed**, it no longer reads an archive dir on a USB stick — it pulls packages from the binary mirror (`repo.intergenos.org`) via `pkm`. To get a fixed package onto every already-installed system, rebuild it and **republish it to the mirror**, then `pkm update` + `pkm install`/`upgrade` on each target. This is the third iteration path, alongside the full ISO rebuild and slipstream.

Use it when: a fix lives in an **installable package** (an `extra`/`desktop`/`ai`/`core`/`base` package, not an `[AUTO]` ISO/bootloader artifact) and the systems that need it are **already installed**. It does not mint a new ISO and does not touch a booted live session — it updates what installed machines fetch.

**The procedure:**

1. **Rebuild the changed package(s) into the chroot** — exactly the single-package build from topic 02, same recipe-refresh prerequisite as slipstream step 1 above:
   - **Python tiers (`desktop`/`ai`/`extra`):** `python3 -m igos-build --only <name> --build --tracked --debug-verbose --sources-dir /sources` (inside the chroot). tracked deployment is the default (since 2026-07-14; `--tracked` is an accepted no-op) — every `--build` writes the fresh `/var/lib/igos/archives/<name>-<version>.igos.tar.gz` the mirror push consumes.
   - **Bash static-list tiers (`core`/`base`):** the matching `chroot-build-<phase>.sh` driver (e.g. `chroot-build-core-extra.sh`) scoped to the one package — that path archives on its own (no `--tracked`). `pkm` itself is a `core` package, so a pkm fix goes through this path, not `--only`.
   - For a multi-package batch or an unattended run, wrap the chroot-enter invocation in a `systemd-run` transient unit (build-rules §3.0) — the systemd-run mandate covers single-package batches too, not just the orchestrator.
2. **Overlay the rebuilt archive(s) into the mirror staging set and republish** — the targeted-push procedure (`scripts/publish-repo.sh`; full mechanics + access in the publish runbook). The host staging dir is **persistent** and is the source-of-truth the signed index is regenerated from, so:
   - **Overlay only the rebuilt archives** onto the staging set (and remove any retired/renamed ones — the regenerated index drops whatever is absent). **NEVER `rsync --delete` the chroot into the staging dir** — that wipes the full injected archive set the targeted push relies on.
   - Run `scripts/publish-repo.sh --gpg-key NK1`: it regenerates and **NK1-signs the whole index** (the index is a single signed manifest of the entire repo — it cannot be partial; one signing-ceremony touch per republish is unavoidable and correct), delta-rsyncs only the changed archives (`--link-dest` hardlinks the rest), then does the atomic `current/` symlink swap and appends the transparency-log entry.
   - **In-flight ≠ failed:** the remote `current/` index stays on the previous publish until the final atomic swap, so a "stale" remote index *during* a publish is expected. Confirm liveness (`pgrep -af publish-repo` locally + the remote `…/sources/` rsync + a growing `_staging-<ts>/`) before concluding failure or resuming with `--skip-sign`.
3. **Update each installed target:** `pkm update` (syncs + signature-verifies the new index — `pkm update` refreshes only the repo index, never installed packages) → then `pkm install <name>` (proprietary-download helper) or `pkm upgrade <name>` (normal package) to pull and apply the fix.

**Choosing among the three paths:** **full ISO rebuild** when the live ISO itself must change (kernel/bootloader/squashfs/`[AUTO]`) or you need a distributable artifact; **slipstream** when iterating an installable package against an ISO **booted but not yet installed**; **mirror-republish** when the fix must reach systems that are **already installed**.

> **Doc provenance:** added 2026-06-15 when the mirror-republish loop was first run as a documented workflow (`pkm` proprietary-helper fixes — honest `[installed]` + `payload_license` routing). The single-package build half is topic 02; the publish half is the publish runbook + `scripts/publish-repo.sh`. If the publish script's staging model or the index-signing flow changes, re-verify and update here.

## Grouping-as-layer

Findings are not tracked as a flat bug list. They are organized into coherent **`GBCNNN.N` groupings**, where each grouping is a *layer* — a set of related fixes that are applied and validated **together** during integration. The grouping is first-class: it is the unit of "apply this, then validate this," not the individual line-item.

Why layers instead of a flat list:

- **Validation is per-layer.** When a grouping lands, you validate that grouping's surface end-to-end before stacking the next. A regression is localized to the layer that introduced it.
- **Tagging composes.** A grouping is `[AUTO]` only if *every* fix in it is `[AUTO]`; a single `[CHROOT]` member makes the whole grouping require a chroot rebuild. Tagging at the grouping level tells you the resume recipe for that layer at a glance.
- **The ledger reads as a build plan,** not a backlog. Each `GBCNNN.N` is a thing you do, in order, with a known apply-step and a known validation.

## The iteration ledger

This document defines the ledger's **format and method**; the current per-candidate entries are maintained separately in the project's operational records, not here. The format is:

- A header fixing the **base** (the candidate snapshot name + the from-scratch build SHA + package count it was minted from), the **methodology** restatement (durable tree fixes only; clean build+install validation; the acceptance criteria), the **layer key**, and the **build mechanics** for that candidate.
- A **pre-bootloader work set** — the fixes known before the iteration starts, each tagged `[AUTO]`/`[CHROOT]`/`[INFRA]` and assigned a lane/owner.
- An **audit** section (below).
- The **`GBCNNN.N` batches** — populated as the audit and the work produce fix groupings.

### The candidate audit

Once the planned pre-bootloader work is built and validated, and **before** integrating it into the build, audit the candidate:

1. **Enumerate every prior-candidate fix** (the previous candidate's `GBC(N-1).N` groupings and any individual troubleshooting fixes) → verify each is **in the tree** (its durable commit is an ancestor of the candidate's build SHA) **and** reproduced in the from-scratch candidate (no surgery needed). This confirms the candidate actually baked them all — it is the concrete check that nothing "rode" on surgery from the last cycle.
2. **Microscope the entire current candidate build** for anything new that will bite at install/boot.
3. Route findings into `GBCNNN.N` groupings, each tagged, applied as layers during integration.

## Validation — the acceptance bar

A candidate iteration is "done" only against the **clean-install** bar, on representative hardware (the low-end tier is the honest test — it surfaces model-selection and resource bugs the dev box hides):

- The from-scratch ISO boots the live session and boots the installer.
- A fresh install completes with **zero manual overrides** anywhere.
- On first boot of the installed system, the integrated assistant behaves correctly end-to-end with no hand-patching: the model auto-selects the tier appropriate to the hardware, the daemon starts, tools load, the panel connects, and the tool-call confirmation surface works.
- The signing ceremony is the only human step in the whole chain.

Two refinements for **boot-time and race-class** fixes (surfaced validating GB001 across the Intel and AMD laptops):

- **Validate on the hardware that reproduces the bug.** Boot/KMS-timing failures are hardware-specific — a first-boot greeter race fired on the AMD box (amdgpu, slower KMS) and never on the Intel box (i915 won the race every time). Confirming a fix on hardware that *can't* trigger the bug proves nothing; pick the box where it reproduces. The low-end / slower-GPU tier is the honest test.
- **A race is "validated" only after N consecutive clean cold boots, not one.** A single clean boot doesn't clear a race — the bug itself may only fire intermittently (the GB001 first-boot GDM crash fired once in a handful of boots). For race-class fixes, require several consecutive clean *cold* boots (a warm `reboot` is not a substitute for a cold power-cycle for KMS/firmware-timing races).

Anything short of that is a **trigger**: fix it in the tree, re-apply per its layer tag, and continue. Promotion to GB happens only when a full from-scratch cycle clears this bar with no triggers.

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| New ISO ships old behavior despite the fix being in the tree | An ai-tier (or other pre-`extra`) fix was treated as `[AUTO]`; `--start-at bootloader` never rebuilt it into the chroot | Re-tag as `[CHROOT]`; rebuild the affected tier (`--only <pkg>` / `--start-at <tier>`) before resuming bootloader |
| Chroot rebuild fails right after `virsh start` with missing `/proc` etc. | The bind mounts (`proc`/`sys`/`dev`/`run` into `/mnt/igos`) dropped on the VM reboot | Re-establish + verify the chroot mounts before any `--only`/`--start-at` against the candidate |
| A fix "worked" last cycle but the install regressed this cycle | A surgical edit rode without a matching tree fix — it was never reproduced from scratch | Find the tree gap; the prove-on-next-cycle loop exists precisely to catch this. Never let surgery ride |
| The audit can't find a prior fix's commit in the candidate's ancestry | The fix was committed after the candidate's build SHA, or only ever existed as surgery | Land it in the tree; it will be picked up by the next from-scratch candidate (or rebuilt into this one as `[CHROOT]`) |
| "All packages built" treated as ready-to-promote | Conflating build-success with install-validation — the exact failure this doc exists to prevent | A candidate is validated by install + boot on real hardware, not by a clean build. Run the chain |

## Cross-references

- **Topic 02** — running the builder: `--start-at` / `--stop-after` / `--only` mechanics this loop relies on.
- **Topic 03** — signing: the sole sanctioned human intervention in an otherwise zero-touch cycle.
- **Topic 06** — test VM + evaluation: the install/boot smoke surface a candidate is validated against ("latent bugs surface during install, not at build time").
- **Topic 07** — the Golden Builder: the `--stop-after bootloader` snapshot for fast ISO regeneration of an *already-proven* build; the complement to a GBC's final-package-phase (`--stop-after ai`) capture.
- [scripts/build-intergenos.sh](../../scripts/build-intergenos.sh) — the `PHASES` array + `run_phase` block are the authoritative phase order this doc cites.
