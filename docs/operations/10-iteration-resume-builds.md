# 10 — Iteration resume builds (`--start-at <phase>`)

**Audience:** maintainers iterating on a populated build VM (for example a release-candidate snapshot, topic 07) who want to rebuild a changed tier and carry the result forward to the signing ceremony **without** a full from-scratch run.

This topic captures the mechanics that are easy to get subtly wrong: **what actually rebuilds on a resume, what the chroot sees, and which gates fire.** It complements topic 02 (full and targeted invocations) and topic 09 (the iteration methodology).

## The canonical resume invocation

From inside the build VM, against a populated chroot (`/mnt/igos` already built through the prior tier — for example a final-package-phase candidate snapshot — `--stop-after ai` since the 2026-07-21 reorder, `--stop-after extra` on older snapshots):

```sh
sudo bash scripts/build-intergenos.sh \
    --user <user> \
    --start-at ai \
    --checkpoint \
    --debug-verbose
```

`--start-at ai` skips phases 1–14 (validate … compute) and resumes at `ai`, then flows forward `ai → bootloader → image → manifest → squashfs → ukis-verity` and **halts at the signing-ceremony pause** (see below). To pick up a change in an earlier package tier, start there instead: `--start-at extra` skips phases 1–12 (validate … desktop) and flows `extra → compute → ai → …`. The package tiers were reordered on 2026-07-21 so that `extra` and `compute` run before `ai`. For long unattended runs, launch it detached as a transient unit so it survives an SSH disconnect and is journald-monitorable:

```sh
sudo systemd-run --unit=igos-resume-build --collect \
    --working-directory=/mnt/intergenos \
    bash scripts/build-intergenos.sh --user <user> --start-at ai --checkpoint --debug-verbose
```

## What the chroot sees on a resume — the source is auto-synced

A common worry: "`--start-at` skips `phase_setup`, which is what stages the repository into the chroot — so won't the chroot have stale packages or source?" **No.** Every phase that builds packages calls `sync_chroot_scripts()` first, and that function re-`rsync`s the latest host tree into the chroot copy on *every* resume, not just on a fresh build. It covers:

- `scripts/`, **`packages/`**, `config/`, `installer/`, `docs/`, `assets/`
- `igos-build.py`, `igos-build/`, `pkm/`, **`intergen/`**

(The authoritative list is the body of `sync_chroot_scripts()` in `scripts/build-intergenos.sh` — read it there rather than trusting a copy.)

All with `--delete`, so the chroot copy is an exact mirror of the host tree. You therefore do **not** manually stage packages or source into the chroot before a resume — the orchestrator does it. Verify after the sync runs:

```sh
# inside the VM, after phase_ai has started:
grep -c '<a string you just added>' /mnt/igos/mnt/intergenos/packages/<tier>/<pkg>/build.sh
ls /mnt/igos/mnt/intergenos/intergen/<your-new-file>.py
```

## What actually rebuilds — the template-hash trigger

The Python tier-driver's `--skip-built` does **not** key on version alone. For each already-tracked package it computes a **template hash** and compares it to the `TEMPLATE_HASH:` marker recorded in the package's manifest (the comparison lives in `igos-build/builder.py` and the hash itself in `igos-build/content_hash.py` — grep `TEMPLATE_HASH` and `template_hash` there for the live sites). The decision:

- **The fingerprint changed** → log shows `Rebuilding <pkg> <ver> (recipe or source changed)` → it rebuilds.
- **Unchanged** → `Skipping <pkg> <ver> (already tracked)` → it is left as-is.

What the fingerprint covers: `package.yml` and `build.sh` always; a package's feature-matrix sidecar when it has one; and **its source content** when that source lives outside the recipe — a `generated: true` tarball, a declared `source_tree:`, or the package's own directory for a first-party package not carried by a sha-pinned upstream tarball. A package whose source *is* a sha-pinned upstream tarball has no out-of-recipe content to fold, so its fingerprint remains exactly `sha256(package.yml + build.sh)` — which is why adding the source fold did not mass-rebuild the upstream corpus.

Three consequences follow:

- A package whose **version or release is unchanged still rebuilds** if its recipe changed. You do **not** need to bump `release:` to force a rebuild after a `build.sh` edit.
- A **source-only** change **does** flip the fingerprint for packages whose source is folded in — editing a declared `source_tree:` is enough on its own, and no longer needs a cosmetic `build.sh` edit to be noticed. The residual case is a package whose source lives outside the recipe and is **not** declared: nothing folds it, so it can be skip-built while stale. Declare it via `source_tree:` rather than relying on a manual nudge; if you cannot, force the rebuild by editing `build.sh`, bumping `release:`, or removing the tracked manifest.
- Everything you did **not** touch is correctly skip-built. A resume that changes one package is fast: only that package compiles, and the rest of the tier reduces to skip checks.

To see the decision for every package **before** launching, run `scripts/preflight-build-plan.py --chroot <substrate>`; it reproduces this comparison using the builder's own hash function rather than a second implementation of it, and reports the bash tiers' separate currency/deployment answer alongside.

### Confirm you built the *right* package

Do not trust the version label; check the installed bytes in the chroot:

```sh
grep -iE 'Rebuilding <pkg>|Building <pkg>|Skipping <pkg>' "$LOG"   # log says build vs skip
grep -c '<symbol you added>' /mnt/igos/usr/.../<pkg-file>           # installed file carries the change
ls -la /mnt/igos/usr/bin/<new-binary-you-added>                     # new artifacts landed
```

## Which gates fire on a resume

- **Audit-coverage / reproducibility gate** (`preflight-audit-coverage.py`) and the **tier-coverage** and **tier-validator** checks live in **`phase_validate`** (`build-intergenos.sh`). `--start-at <phase>` **skips `phase_validate`**, so a resume does **not** halt on a stale per-package audit. This is convenient for iterating, but it means **the audit can silently drift**: after changing a package's source or recipe, refresh its audit (`scripts/audit-package.py <pkg>`) and re-aggregate (`scripts/aggregate-package-audits.py`) before any **validate-inclusive** build — which is exactly what a candidate-to-golden promotion run is (topic 09). Do not let a resume's skipped validation hide audit debt.
- **`phase_manifest`** (archive-integrity sha256 manifest) and the **`phase_image`** D-007 compliance gate **do** run on the resume path — a resume is gated on those just like a full build.

## The signing-ceremony stop

The orchestrator **hard-exits at the end of `phase_ukis_verity`** for the operator-only Nitrokey signing ceremony; it does **not** auto-sign. On the dm-verity pipeline the single ceremony covers GRUB plus all three UKIs, because the live UKI's sealed cmdline carries the squashfs verity root hash that is only known after `phase_squashfs`. A `--start-at ai` resume therefore lands naturally at the signing pause with the image staged and unsigned. After the operator completes the ceremony (topic 03), resume the tail with:

```sh
sudo bash scripts/build-intergenos.sh --user <user> --start-at iso --checkpoint --debug-verbose
```

## Operational notes

- **Disk:** route `--checkpoint` tarballs off the build volume (the `checkpoints/` symlink already points at the large off-host volume). Watch `df -h /mnt/intergenos` across a run — the `image` and `squashfs` phases are the heavy consumers, and a disk-full condition stalls an unattended build.
- **Visibility:** use `--debug-verbose` for any iteration you intend to leave running, so failures stream live to the tier and per-package logs instead of surfacing hours later. Watch liveness via the newest per-package log's age and compiler CPU (`cc1`, `ninja`, `rustc`), since the orchestrator log is quiet during a long compile.
- **Do not reach for prohibited workarounds** on a failure: no reassigning a package to a different tier, no `--tests-disable`, no dropping the failing package. Read the actual error, classify it against topic 02's troubleshooting table, fix the cause, and resume from the failed phase.

## Cross-references

- Topic 02 — running the builder (full and targeted invocations, troubleshooting table)
- Topic 03 — signing (what the `ukis-verity` pause hands off to)
- Topic 07 — the golden builder (the populated state a resume runs against)
- Topic 09 — the iteration methodology (when and why to iterate versus mint a new candidate)
