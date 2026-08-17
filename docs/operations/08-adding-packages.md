# 08 — Adding a package to the build

**Audience:** maintainers adding a new package (or rewiring an existing one) so it builds cleanly, installs into the chroot, and gets caught by every guard rail (Rule 20 verify_paths, Rule 21 no stubs, pre-push gate, pre-squashfs audit).

## Goal

Land a new `packages/<tier>/<name>/` recipe that:

1. Builds cleanly inside the chroot (Rule 11 — no stub `configure()`/`do_install()`).
2. Installs files the package claims to install — declared explicitly via `verify_paths:` (Rule 20).
3. Is reachable by exactly one builder: either the bash static-list builder (tiers `core` / `base` via `scripts/chroot-build-*.sh`) OR the Python tier-driver (tiers `desktop` / `extra` / `ai` via `igos-build.py --tier`). Never both.
4. Survives the pre-push hook (gate 8 — verify_paths or pending_acquisition required for new package.yml).
5. Survives the pre-squashfs audit gate at squashfs build time.
6. Is reachable by the orphan detector at `scripts/check-builder-coverage.py`.

## Prerequisites

- A working build VM ([Topic 01](01-build-vm-setup.md)).
- Familiarity with the build phases that consume packages: `phase_toolchain` (toolchain), `phase_chroot_prep` + `phase_chroot_tools` (chapter-7 chroot temporary tools), the tier-driven phases (`phase_core`, `phase_base`, `phase_desktop`, `phase_extra`, `phase_ai`), and `phase_bootloader`. [Topic 02](02-running-the-builder.md) covers them.
- Source tarball already mirrored to the InterGenOS source mirror, or a `file:///` URL pointing at a tarball staged inside the build chroot. Networked `https://` URLs are accepted but mirror-first is the discipline.
- (For Python-builder tiers) Build dependencies already in the chroot. The Python builder topologically sorts but doesn't resolve missing deps for you.

## Package layout

A package is a directory at `packages/<tier>/<name>/`:

```
packages/<tier>/<name>/
├── package.yml      ← required: metadata + verify_paths
├── build.sh         ← required for build_style: custom; optional for autotools/meson
├── <name>.1         ← optional manpage (some packages ship a tracked manpage in-recipe)
└── patches/         ← optional, build.sh-consumed
```

> **Patching bundled / upstream source — apply it MANUALLY in `build.sh`, do NOT use the `package.yml` `patches:` key.** There are two patch paths in this build system and they are not interchangeable:
> - The `package.yml` **`patches:`** key is auto-applied by `igos-build/styles/base.py` as `patch -Np1 -i $IGOS_PATCHES/<file>`, where `$IGOS_PATCHES` is **`/sources`** — so that patch file must be staged into `/sources` (it lives beside the source tarballs, e.g. `build/patches/`). This is for patches that ship alongside a fetched upstream tarball.
> - A patch carried in the package's own **`patches/`** dir (committed in-repo) is applied **by hand inside `build.sh`**, reading from `${IGOS_PACKAGE_DIR:-/mnt/intergenos/packages/<tier>/<name>}/patches/`. `IGOS_PACKAGE_DIR` is set to the recipe dir during the build; the absolute fallback covers surgical-rebuild invocations that don't propagate it. This is the convention for InterGenOS **downstream patches to bundled source** (precedent: `packages/desktop/gtk4/build.sh`, `packages/desktop/intergenos-extensions-layout/build.sh`).
>
> Do **not** declare an in-`patches/` file under `patches:` as well — the auto-applier would look for it in `/sources` (not there) or, if also staged, double-apply it. Generate the patch with `a/`-prefixed paths so a single `patch -p1` strips cleanly from the extracted source root, and `patch -p1 --dry-run` it against a fresh extraction before committing.

Tier semantics:

| Tier | Builder | Where wired |
|---|---|---|
| `core` | bash static list | `scripts/chroot-build-ch8.sh` + `scripts/chroot-build-base.sh` + `scripts/chroot-build-core-extra.sh` (one per phase) |
| `base` | bash static list | `scripts/chroot-build-base.sh` |
| `desktop` | Python topological sort | `igos-build.py --tier desktop` |
| `extra` | Python topological sort | `igos-build.py --tier extra` |
| `ai` | Python topological sort | `igos-build.py --tier ai` |

Pick the tier that matches: foundational utilities → `core`/`base`; GUI/desktop apps → `desktop`; servers, dev tooling, browsers, system utilities → `extra`; AI runtime → `ai`.

## Step-by-step procedure

### 1. Create the directory + package.yml

```sh
cd /mnt/intergenos
mkdir -p packages/<tier>/<name>
```

Author `packages/<tier>/<name>/package.yml`:

```yaml
name: <name>
version: "<semver>"
release: 1
description: <one-line description ending without a period>
license: <SPDX-identifier>
homepage: https://<upstream-homepage>
tier: <core|base|desktop|extra|ai>
build_style: <custom|autotools|meson|cmake|cargo|python>
source:
- url: https://<mirror-or-upstream-url>/<name>-<version>.tar.<ext>
  sha256: <expected-sha256-of-tarball>
dependencies:
  build: []     # build-only deps (autoconf, pkgconf, etc.)
  host: []      # host-only tooling (rare)
  runtime:     # runtime deps (libraries linked, interpreters required)
  - <dep1>
  - <dep2>
verify_paths:
- /usr/bin/<name>
- /usr/lib/lib<name>.so
- /etc/<name>/<name>.conf
```

Rule 20 authoring guidance for `verify_paths:` — pick 2-3 paths that prove the package landed:

1. Primary binary at `/usr/bin/<name>` or `/usr/sbin/<name>` — strongest identity signal.
2. Primary library at `/usr/lib/lib<name>.so*` — for lib-only packages.
3. Canonical directory at `/usr/share/<name>/`, `/usr/lib/<name>/`, `/etc/<name>/`, or `/usr/lib/firmware/<...>/` — for data/firmware/config packages.
4. For Perl/Python module packages, use the `site_perl` / `site-packages` path.
5. For the kernel, declare `/boot/vmlinuz-<version>` + `/usr/lib/modules/<version>`.

Each path must start with `/` and have ≥3 segments (e.g., `/usr/bin/x`). Avoid descriptive single-word entries that aren't actual filenames.

**Deferred-package case:** if the package legitimately can't be acquired yet (waiting on upstream sponsorship, etc.), replace the `verify_paths:` block with:

```yaml
pending_acquisition: "<reason — e.g., Microsoft UEFI CA sponsorship still pending>"
```

The pre-squashfs audit skips packages with `pending_acquisition` set. Don't use this as a workaround for unwilling-to-author cases; it's specifically for blocked-on-external dependencies.

#### Versioning policy — `version` vs `release` (what declares a version change)

`version` and `release` mean different things, and conflating them was a real
defect (we caught it 2026-06-16: pkm had absorbed a 29-finding audit yet still read
`0.1.0` because every change had bumped `release` only). The policy:

- **`version`** = the *software's own* version.
  - **Third-party packages:** mirror the upstream release exactly (e.g. `git`
    `2.51.0`). It changes only when we package a new upstream version.
  - **First-party packages** (we author them — pkm, intergen, forge, the theme,
    helper-lib, etc.): **we are upstream, so `version` is ours to bump, by SemVer:**
    - **PATCH** (`0.2.0 → 0.2.1`) — bug fixes only, no behavior/interface change.
    - **MINOR** (`0.1.0 → 0.2.0`) — new, backward-compatible behavior or features.
    - **MAJOR** (`0.x → 1.0`, `1.x → 2.0`) — a stability milestone or a breaking change.
- **`release`** = the *packaging* revision of the **same** `version`. Bump it **only**
  when the code is unchanged and the *packaging* changed: a `build.sh`/recipe tweak, a
  rebuild against a new dependency, a `.PKGINFO` fix, or a mirror republish of identical
  code. A `release` bump is **not** a substitute for a `version` bump when the software
  itself changed.

**Decision rule:** did the package's *own source* change in a way a user would
notice (a fix, new behavior, new output)? → bump **`version`** (and reset `release`
to `1`). Did only the *recipe/packaging* change? → bump **`release`**, keep `version`.

> **Both still matter mechanically.** A `version` *or* `release` change flips the
> template hash so `--skip-built` rebuilds it, and the mirror index orders by
> `(version, release)` so a same-version republish must still advance `release` to be
> visible to `pkm upgrade` (see `first-publish-runbook.md`). The policy above governs
> *which* field is semantically correct; the mechanical "must change to rebuild/ship"
> rule is unchanged.

**⚠️ A first-party `version` is declared in MORE THAN ONE place — bump them together.**
The build driver passes the version as a literal arg, separate from `package.yml`, and
some packages also carry their own `__version__`. For **pkm** the version lives in
THREE files that must all match, or the built archive/`pkm --version`/the recipe disagree:

1. `packages/core/pkm/package.yml` — `version:` (and `release:`).
2. `scripts/chroot-build-core-extra.sh` — the `run_package "pkm" "pkm" "<version>"` arg
   (the driver supplies the build/archive `pkgver`; `package.yml` supplies `pkgrel`).
3. `pkm/__init__.py` — `__version__` (what `pkm --version`, the User-Agent, and the
   index `min_pkm_version` check report).

When bumping any first-party package, grep its name + the old version across
`packages/`, `scripts/`, and the package's own source to catch every literal.

### 2. Author build.sh

For `build_style: custom`, `build.sh` defines `configure`, `build`, and `do_install` functions. The orchestrator sources the script inside the chroot's per-package work directory and calls each function in sequence.

Skeleton:

```sh
#!/bin/bash
# <name> <version> — <one-line description>

configure() {
    set -e
    ./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var
}

build() {
    set -e
    make -j"$(nproc)"
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" install

    # Post-install fixups go here if needed (move from /usr/local to /usr,
    # rename library to match expected SONAME, install systemd unit file
    # from source tree, etc.).
}

post_install() {
    set -e
    # Optional. Runs once after the chroot-tracker has registered the
    # package. Use for ldconfig, systemctl preset-all, etc. The main
    # install lands files via do_install; post_install is for
    # registration/cache-rebuild that depends on the files being in place.
    :
}
```

Per Rule 11 — **a stub `configure()` that's just `:` and a `do_install()` that produces nothing meaningful is forbidden.** If the package has source to compile, compile it. If it's a metapackage with no source, document that explicitly: `build_style: custom`, `source: []`, and `do_install()` only writes config files, with a header comment explaining the package is intentionally meta.

Per Rule 12 — pin a non-latest version with a comment justifying the pin. The pre-push gate doesn't check this but reviewers will.

Per Rule 5 — for multi-source packages, ensure `configure()` does the additional tarball extracts before invoking the upstream configure. A halt in <5s with "missing module/vendor" is the canonical missing-extract signal.

**Verify every recipe assumption against the ACTUAL pinned source — never memory or a distro config.** Before settling the `configure`/`meson`/`cmake` flag set, extract the exact source tarball you pinned and read its real option surface (`./configure --help`, `meson_options.txt`/`meson.options`, `Kconfig`, the install Makefiles) — and pick `verify_paths` from the files the install *actually* lays down (e.g. the real plugin `.so` name, the real `bindir` binary), not a guessed path. Fedora/Debian/openSUSE carry downstream patches, so their flags/options are a TRAP for "is this real in vanilla?" This is the build-development rulebook's **`build-rules.md` §2.8 (upstream-drift / stale-recipe-assumption class)** applied at authoring time; it is the one place authoring legitimately reads the upstream *package's* source, and it is required, not optional.

#### Service packages — ship a systemd unit + a preset (don't reverse-engineer it)

A package that installs a long-running daemon needs two extra artifacts, and the
pattern is identical across the tree (precedents: `packages/desktop/gdm`,
`packages/core/nftables`, `packages/desktop/avahi`). Follow it rather than
re-deriving it from those recipes:

1. **If upstream ships no systemd unit** (many don't — check the extracted tree
   for an `init/` dir / `*.service*`), author one as a tracked file in the
   package dir (`packages/<tier>/<name>/<name>.service`) and install it in
   `do_install()` to `/usr/lib/systemd/system/<name>.service`. It is **not a
   stub** (Rule 21) as long as its `ExecStart=` points at a binary the same
   `do_install()` actually installs. Gate it with a `ConditionVirtualization=`,
   `ConditionPathExists=`, or similar where the daemon is only meaningful in a
   subset of environments — a condition-gated unit is a literal no-op when unmet,
   which is the security-only-alignment posture (default-deny).
2. **Enablement is governed by the preset policy**, not by `systemctl enable` in
   `post_install()`. The installed image runs `systemctl preset-all` at squashfs
   build time; the catch-all `99-intergenos-default-disable.preset` (`disable *`,
   owned by `intergenos-base-files`) leaves every unit **disabled** unless an
   earlier-sorted preset explicitly enables it. Two ways to enable a new service:
   - **Per-package preset (preferred for a unit intrinsic to one package):** ship
     `packages/<tier>/<name>/90-<name>.preset` containing `enable <name>.service`
     and install it to `/usr/lib/systemd/system-preset/90-<name>.preset`. `90-`
     sorts before `99-`, and `preset-all` is first-match-wins in lexical filename
     order, so the explicit `enable` wins over the `disable *` catch-all. This
     keeps the package self-contained (no `intergenos-base-files` rebuild).
   - **Central whitelist (for cross-cutting policy):** add `enable <name>.service`
     to `intergenos-base-files`'s `80-intergenos-enable.preset` (that package owns
     the general preset *policy* — `build-rules.md` §2.7). Use this when the
     enablement is a system-wide decision rather than a property of one package.
   - **Only enable by default what should run by default.** A condition-gated unit
     (step 1) is safe to enable globally because the condition still confines
     execution; an unconditional network-facing daemon is not — leave it
     `disable`d (user installs, user enables) unless there's a deliberate reason.

Install both with `install -Dm644 "$BUILD_DIR/<file>" "$DESTDIR/<dest>"`, where
`BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"` resolves to the
recipe dir (the gdm idiom). Do **not** declare the daemon's enablement intent in
prose only — the unit + preset files ARE the wiring.

### 3. Wire into the tier's builder

#### Tier `core` (and `base`):

Add a `run_package` call to the appropriate static list in `scripts/chroot-build-<phase>.sh`. The calls enumerate exactly which packages that phase builds, in dependency order. The signature is **multi-argument** (verified against the `run_package()` definition + call sites in `chroot-build-ch8.sh` and `chroot-build-base.sh`):

```sh
run_package "<pkg-dir>" "<name>" "<version>" \
    "<source-tarball-filename>" \
    "<one-line description>"
```

- `<pkg-dir>` and `<name>` are the `packages/<tier>/<name>/` directory name (normally identical).
- `<version>` must match `package.yml`'s `version:`.
- `<source-tarball-filename>` must match the basename of the package's `source:` URL **exactly** (e.g. `whois_5.6.6.tar.xz` — underscore and all). A mismatch fails source staging in ~0.1s (`Verifying SHA256`).
- Place the call in **topological order** — after every package it depends on. The bash core/base drivers do NOT auto-order by `dependencies:` (that field is informational for these tiers), so a package placed before a dep it needs will fail to build.

Real example (from `chroot-build-base.sh`):

```sh
run_package "bind-utils" "bind-utils" "9.20.19" \
    "bind-9.20.19.tar.xz" \
    "BIND DNS client utilities (dig, host, nslookup)"
```

After adding the line, verify with `scripts/check-builder-coverage.py` (next step).

#### Tier `desktop` / `extra` / `ai`:

No script edits required. The Python builder (`igos-build.py`) walks `packages/<tier>/` at build time, builds the topological-sort closure of `dependencies:`, and installs everything reachable. The package's `tier:` field IS the entry point.

Confirm the Python builder picks the package up:

```sh
cd /mnt/intergenos
python3 igos-build.py --tier <tier> --dry-run | grep <name>
# Expected: the package appears in the build order
```

### 4. Confirm orphan-detector reachability

```sh
python3 scripts/check-builder-coverage.py
```

This walks every `packages/<tier>/<name>/package.yml`, checks whether the package is reachable by:

- A bash `chroot-build-*.sh` static list (tier `core` / `base`), OR
- The Python builder's tier dispatch (tier `desktop` / `extra` / `ai`).

A package whose recipe exists in the tree but is reachable by NEITHER builder is an **orphan**: it will never build, the chroot will never have it, the pre-squashfs audit will halt on its verify_paths, and the regression won't surface until much later. The orphan detector is the early-warning system for that class.

Expected exit-0 output: `OK: all packages reachable by exactly one builder`. Any orphans are listed by tier + package name — fix by adding to the correct builder's reach.

### 5. Dry-run the package build

For Python-builder tiers, build just the new package **inside the chroot** with the `--only <name>` flag. The toolchain (meson/ninja/gcc) lives only in `/mnt/igos`, so a host-side `python3 -m igos-build` fails with `meson: command not found`.

> **Prerequisites first ([Topic 02](02-running-the-builder.md) → "Build a single package"):** after a snapshot revert or any host-side recipe edit, (1) mount the chroot (`sudo bash scripts/chroot-setup.sh`), (2) **refresh the chroot recipe copy** (`rsync` host `packages/ scripts/ config/ installer/ docs/ assets/ igos-build/ pkm/ intergen/` → `/mnt/igos/mnt/intergenos/`) — the chroot builds from a COPY frozen at snapshot state, and (3) stage the source tarball into `/mnt/igos/sources/`. Skipping (2) silently builds the STALE recipe.

**`chroot-enter.sh` runs `/bin/bash $*` — it takes a SCRIPT PATH, not an inline command.** `chroot-enter.sh python3 igos-build.py …` does NOT work (it runs bash on a file literally named `python3`). Use one of the two correct forms:

```sh
# (a) interactive — drop into the chroot, then run the builder:
sudo bash scripts/chroot-enter.sh                 # → interactive chroot shell
#   ...now inside the chroot:
cd /mnt/intergenos && python3 -m igos-build --only <name> --build --debug-verbose --sources-dir /sources

# (b) unattended — put the builder call in a one-line in-chroot script, pass its path.
#     The `cd /mnt/intergenos` is REQUIRED (chroot cwd is /, and `python3 -m igos-build`
#     resolves the module from cwd); always pass `--debug-verbose` for live build output:
printf '#!/bin/bash\ncd /mnt/intergenos && exec python3 -m igos-build --only %s --build --debug-verbose --sources-dir /sources\n' "<name>" \
  | sudo tee /mnt/igos/root/build-one.sh >/dev/null
sudo bash scripts/chroot-enter.sh /root/build-one.sh
```

`--only <name>` builds ONLY that one named package; its dependencies must already be in the chroot (the Python builder does not resolve or build missing dependencies for you). Always pass `--debug-verbose` so build failures stream live and are visible immediately. This is the way to confirm a single new package compiles before committing to a full tier rebuild.

For bash-builder tiers, the surgical equivalent is invoking the `run_package` line directly from the chroot:

```sh
sudo chroot /mnt/igos /mnt/intergenos/scripts/chroot-build-<phase>.sh <name>-<version>
```

(The exact subcommand depends on the phase script's argument parser; check the phase script's header comment.)

### 6. Commit + push

Stage the new files:

```sh
git add packages/<tier>/<name>/package.yml packages/<tier>/<name>/build.sh
# Plus any patches/, manpages, or static config under the package dir.
```

Pre-push gate 8 (`.githooks/pre-push`) refuses to push a *new* `package.yml` without either `verify_paths:` or `pending_acquisition:`. If the gate blocks, you missed Rule 20 — add the verify_paths block and try again.

Conventional commit format:

```
feat(packages/<tier>): add <name> <version>

<one-paragraph description of what the package does and why we ship it>

Verify paths declared: <paths from package.yml>.
Wired into <chroot-build-XX.sh static list / Python builder via tier:>.

Co-Authored-By: <author-line>
```

### 7. After push — full-rebuild verification

The full rebuild via `scripts/build-intergenos.sh` is the definitive proof of integration:

- The orchestrator builds the new package as part of its phase.
- `scripts/build-squashfs.sh` step 4.5 audit verifies all declared paths land.
- [Topic 02](02-running-the-builder.md) covers the full-rebuild flow end-to-end.

## Validation

A successfully-integrated package passes all of:

- `scripts/check-builder-coverage.py` reports no orphans.
- A surgical build (step 5) compiles + installs cleanly.
- A full rebuild's pre-squashfs audit (step 4.5) reports the package's verify_paths as present.
- The package's archive appears in the post-build `build/intergenos-archive-manifest.txt` (the BSD-style sha256 archive-integrity manifest emitted by `phase_manifest`).
- The pre-push hook accepts the commit.

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `check-builder-coverage.py` reports the new package as orphan | Wired to wrong tier OR missing from chroot-build-<phase>.sh static list | Match `tier:` field with builder reach: core/base → bash static list edit; desktop/extra/ai → no script edit needed |
| `pre-push gate 8` blocks the commit | package.yml missing both `verify_paths:` and `pending_acquisition:` | Add the verify_paths block per Rule 20 |
| Build fails immediately in <5s with `cannot find ...` | Missing extract (Rule 5) for a multi-source package | Add `tar xf "${IGOS_SOURCES}/<extra>.tar.gz"` in `configure()` |
| Build succeeds but `pre-squashfs-audit` reports `MISSING <path>` | `do_install()` didn't actually install the path the verify_paths declares | Inspect the install function output; common cause: upstream renamed the binary in a version bump and verify_paths still points at the old name |
| Build succeeds, audit succeeds, but the package isn't in `/var/lib/igos/packages/` | The chroot tracker (pkm) didn't register the install — usually a do_install that bypasses DESTDIR | Restore `${DESTDIR}` to all install invocations |
| Greedy-glob matches an unrelated package | `run_package "<name>"` matches multiple names starting with `<name>` | Use the exact `name-version` literal form |
| `do_install()` produces nothing meaningful | Stub class (Rule 11) | Rewrite to actually compile + install. Don't ship the stub. |

## Cross-references

- [Topic 11 — Resolving the validate-phase gates](11-resolving-validation-gates.md): when a full build halts in `validate` on your new/changed package (audit-coverage, tier validation, reconciliation mismatches) — what each means and how to clear it correctly
- [Topic 02 — Running the builder](02-running-the-builder.md): the full-rebuild flow that consumes new packages
- [Topic 04 — Generating the live-ISO squashfs](04-generating-squashfs.md): the pre-squashfs audit (Rule 20 enforcement) lives here
- `scripts/check-builder-coverage.py` — orphan detector
- `scripts/pre-squashfs-audit.py` — verify_paths audit
- `igos-build/verify_paths_derive.py` — auto-derive fallback when a package.yml omits verify_paths (the human-curated field is still the source of truth)
- `igos-build.py` — Python tier-driver
- `scripts/chroot-build-*.sh` — bash static-list builders for the core/base tiers
