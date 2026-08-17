# Reproducible in-tree source tarballs

## Goal

This document explains how InterGenOS turns a handful of *in-tree* asset
directories (the welcome app, the shell theme, the bundled GNOME extensions, the
cursor and GTK themes) into the `*.tar.xz` source tarballs the builder consumes,
and why the `sha256:` pin for those packages is **generated, deterministic, and
content-addressed** rather than hand-maintained.

If a `packages/desktop/*/package.yml` shows up as modified in `git status` after
a build, the pin has not "drifted." The pin is a pure function of the asset
content, and the only way it changes is if the content changed. The usual cause
is the regenerate-but-forget-to-commit case: a tarball was regenerated and the
matching pin update was never committed. The fix is to commit it, not to distrust
the mechanism.

## Background — why these are generated, not downloaded

Most packages have an upstream `source:` URL plus a `sha256:` that pins the exact
upstream bytes, a security anchor for a download InterGenOS does not control. A
small set of packages instead ship content that lives **in this repo** under
`assets/`:

| Package | Canonical asset source |
|---|---|
| `intergen-welcome` | `assets/intergen-welcome/` |
| `intergenos-theme` | `assets/intergen-shell-theme/` |
| `intergenos-extensions-{appearance,layout,productivity,utilities}` | `assets/theming/extensions/<UUID>.zip` |
| `bibata-cursor-theme` | `assets/theming/cursor-themes/Bibata-Modern-{Classic,Amber,Ice}.tar.xz` |
| `catppuccin-gtk-theme` | `assets/theming/gtk-themes/catppuccin-mocha-blue.zip` |

Sibling pipelines, each covered by its own tooling: `scripts/build-forge-tarball.sh`
for the Forge installer, `scripts/cargo-vendor-gen.sh` for Rust vendor tarballs,
and the `go mod vendor` flow for `lego-*`.

For these, the `sha256:` does not pin an upstream download. It pins the
**reproducible build output** of `scripts/build-intergenos-source-tarballs.sh`.
Because the input is in-tree, the integrity question shifts from "did the
download match?" to "does the committed pin match the bytes a clean rebuild
produces?" That question only has a stable answer if generation is byte-for-byte
reproducible, and by construction it is.

## How reproducibility is forced

`scripts/build-intergenos-source-tarballs.sh` packs every bundle with
deterministic `tar`/`xz` settings (see `det_tar()`):

```
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --format=ustar --mtime='@1735689600' \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store'
XZ_OPT='-9 -T1 --no-warn'          # single-threaded; -T>1 is non-deterministic
```

The load-bearing choices:

- **`--sort=name`** — file order is fixed, not filesystem-walk order.
- **`--owner=0 --group=0 --numeric-owner`** — no uid/gid or username leakage.
- **`--mtime='@1735689600'` (a *fixed* epoch, 2025-01-01T00:00:00Z)** — the key
  choice. Every entry gets the same timestamp, so the archive bytes are a pure
  function of *content*. The pin changes only when an asset actually changes.

  > This replaced an earlier scheme that used each path's git commit time
  > (`git log -1 --format=%ct`). That scheme had a bootstrap-drift bug: the pin
  > committed *alongside* an asset change was computed at the OLD commit time,
  > but the *next* regeneration (after the change landed) used the NEW commit
  > time, so the bytes and the pin changed on every build and left the tree
  > permanently dirty. A fixed mtime removes time from the equation entirely.

- **`xz -T1`** — multi-threaded xz splits the stream into thread-dependent
  blocks, so the compressed bytes vary by core count. Single-threaded is stable.

After packing, the script computes the new `sha256` and rewrites the single
`sha256:` line in the package's `package.yml` (`update_pkg_sha()`, which refuses
to touch a multi-`sha256` file). So running the script *both* regenerates the
tarball *and* updates the pin to match.

### The build does this for you

`phase_verify_sources` in `scripts/build-intergenos.sh` invokes the generator
(alongside `build-forge-tarball.sh`) on every run, so a fresh build always packs
the current in-tree content and reconciles the pin. You do not have to remember
to regenerate before a build; the build regenerates.

### Byte-identity across machines

Byte-identical output requires the same archiver toolchain. The canonical
generation environment is the build VM, which carries **GNU tar 1.35 and XZ Utils
5.4.5**. The same versions on a maintainer workstation produce identical bytes
(verified). A different `tar`/`xz` major or minor version can emit a different
(still internally deterministic) byte stream and therefore a different pin; if
you regenerate on a mismatched toolchain, you will see a pin change that the build
VM rewrites back. When in doubt, treat the build VM as the source of truth.

## Step-by-step — editing an in-tree asset

1. Edit the asset under `assets/` (e.g. a welcome-app preview PNG, a theme CSS).
2. Regenerate the tarballs and pins:
   ```
   scripts/build-intergenos-source-tarballs.sh
   ```
3. Review what changed:
   ```
   git status
   git diff packages/desktop/*/package.yml
   ```
   Only the package(s) whose assets you touched should show a new `sha256:`.
4. **Commit the regenerated pin together with the asset change.** This is the
   whole discipline. The tarballs themselves live under `build/sources/` and are
   git-ignored; the *pin* in `package.yml` is the committed, reviewable record.

## Validation

A clean tree stays clean across regeneration. To prove the mechanism is in sync:

```
scripts/build-intergenos-source-tarballs.sh
git diff --stat packages/desktop/*/package.yml      # expect: no output
```

Empty output means every committed pin already equals the deterministic
regeneration of the current assets, which is the reproducible state. Non-empty
output means exactly one thing: an asset changed (or was regenerated) and the pin
was not committed. Commit it.

## Common failures + troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `package.yml` shows modified after a build | An asset changed (or a tarball was regenerated) and the pin update was never committed (the regenerate-but-forget-to-commit case) | `git diff` the pin, confirm it reflects a real asset change, and commit it. This is not drift. |
| Pin changes when you regenerate but you changed nothing | Your local `tar`/`xz` differs from the build VM's (GNU tar 1.35 / xz 5.4.5) | Regenerate on the build VM, or align your toolchain; treat the build VM output as canonical |
| `ERROR: <pkg>.yml has N sha256 lines; refusing to rewrite` | The package has multiple `source:` entries; the single-line rewriter bails by design | Such packages are not handled by this script; pin them through their own pipeline |
| Tarball entry order or paths look wrong at extract | Builder uses `--strip-components=1`; the packing layout must match (some bundles pack `.` to produce `./<dir>/…`) | See the per-package `det_tar` call and the layout comment above it |
| ONE package's pin/`content_hash` drifts on **every** build while the others stay stable (perpetual dirty git; `phase_verify_sources` rewrites it each run) | The generator **honors the ambient `SOURCE_DATE_EPOCH`** (`--mtime="@${SOURCE_DATE_EPOCH:-…}"`) instead of pinning its own fixed epoch. The build exports `SOURCE_DATE_EPOCH` (a build-moment value), so that package's tarball mtime — and thus its content_hash — tracks the build moment; a fresh or standalone regen never matches the committed baseline | Pin a **dedicated local epoch** in the generator, independent of the ambient env (mirror `build-intergenos-source-tarballs.sh`'s `epoch_for → EPOCH_FALLBACK`), then re-baseline once. Diagnose decisively by running the generator under two different `SOURCE_DATE_EPOCH` values — if the sha changes, it is env-sensitive. (Origin: `forge` was the sole such generator; fixed 2026-07-02 via `build-forge-tarball.sh`'s `FORGE_TARBALL_EPOCH`.) |

## Cross-references

- [`scripts/build-intergenos-source-tarballs.sh`](../../scripts/build-intergenos-source-tarballs.sh) — the generator (header comment carries the full per-package layout rationale)
- [`scripts/build-intergenos.sh`](../../scripts/build-intergenos.sh) — `phase_verify_sources` invokes the generator on every build
- [08 — Adding a package to the build](08-adding-packages.md) — package layout + `verify_paths` authoring
- [`scripts/cargo-vendor-gen.sh`](../../scripts/cargo-vendor-gen.sh) — the sibling reproducible-tarball tool for Rust vendor bundles (same deterministic-tar discipline)
