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

- A working build VM (topic 01).
- Familiarity with the four build phases that consume packages — `phase_temp_tools` (toolchain), `phase_chroot` (ch5-8 LFS base), tier-driven phases (`phase_core`, `phase_base`, `phase_desktop`, `phase_extra`, `phase_ai`), and `phase_bootloader`. Topic 02 covers them.
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

### 3. Wire into the tier's builder

#### Tier `core` (and `base`):

Add the package to the appropriate static list in `scripts/chroot-build-<phase>.sh`. The `run_package` list near the top of each script enumerates exactly which packages that phase builds. Common gotcha: the lists use glob/prefix matching internally. **Use the exact `name-version` literal, not a greedy glob.** Example:

```sh
# Good:
run_package "<name>-<version>"

# Bad (greedy — matches packages whose name starts with `<name>`):
run_package "<name>"
```

The greedy-glob class has bitten the project before (the `at-*` glob silently swallowed `base/at` because it greedily matched `at-spi2-core`, leaving `base/at` un-built and the chroot missing `/usr/bin/at`). The pre-squashfs verify_paths audit catches the downstream symptom; the canonical fix is the exact-version match form.

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

For Python-builder tiers, build just the new package + its dep closure:

```sh
sudo python3 igos-build.py --only <name>
```

`--only <name>` builds the package and its dependencies but stops short of building the rest of the tier. Useful for confirming a single new package compiles before you commit to a full tier-rebuild.

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
- Topic 02 covers the full-rebuild flow end-to-end.

## Validation

A successfully-integrated package passes all of:

- `scripts/check-builder-coverage.py` reports no orphans.
- A surgical build (step 5) compiles + installs cleanly.
- A full rebuild's pre-squashfs audit (step 4.5) reports the package's verify_paths as present.
- The package appears in the post-build `build/manifest-reconciliation-<ts>.txt` (Rule 18) without diff.
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

- Topic 02: How to run the builder — the full-rebuild flow that consumes new packages
- Topic 04: How to generate squashfs — the pre-squashfs audit (Rule 20 enforcement) lives here
- Topic 09: Cost of deferral — case studies on what happens when a package is added but not verified
- `docs/build-development-rulebook.md` — Rules 1-21 in canonical form, especially Rule 11 (stub bans), Rule 12 (version pinning), Rule 17 (pre-flight coverage), Rule 18 (manifest reconciliation), Rule 20 (verify_paths), Rule 21 (no stubs)
- `scripts/check-builder-coverage.py` — orphan detector
- `scripts/pre-squashfs-audit.py` — verify_paths audit
- `igos-build/verify_paths_derive.py` — auto-derive fallback when a package.yml omits verify_paths (the human-curated field is still the source of truth)
- `igos-build.py` — Python tier-driver
- `scripts/chroot-build-*.sh` — bash static-list builders for the core/base tiers
