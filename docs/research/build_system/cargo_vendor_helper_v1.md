# cargo-vendor-gen.sh — Host-Side Vendor Tarball Helper (v1)

**Date:** 2026-05-12
**Supersedes:** the manual procedure documented in `offline_rust_builds_2026-04-03.md` §"Vendoring Workflow (run on host with network)".
**Companion script:** `scripts/cargo-vendor-gen.sh`

## 1. Background

The InterGenOS chroot has no network by design. Rust packages that depend on crates from crates.io must have their dependencies vendored (pre-fetched and staged) before they can build inside the chroot. The in-tree pattern, established by `packages/core/cargo-c/` and `packages/extra/aardvark-dns/`, is:

1. **Maintainer-side:** run `cargo vendor` outside the build with network access, producing a `vendor/` directory and a `.cargo/config.toml` redirecting `crates-io` lookups to `vendored-sources`. Tar+xz the pair into `<pkg>-<version>-vendor.tar.xz`. Stage to the build mirror alongside upstream source tarballs.
2. **Build-side:** `configure()` in `build.sh` extracts the vendor tarball with `--strip-components=1`. `build()` runs `cargo build --release --frozen --offline`. The `.cargo/config.toml` inside the tarball makes cargo resolve everything to the vendored crates.

Before this helper, this pipeline was a manual operator procedure. A recent batch of 15 Rust + 3 Go user-tools packages added to the extra tier declared the vendor artifacts in `build_artifacts:` but did not generate them — every Rust build.sh expects `<pkg>-<version>-vendor.tar.xz` in `$IGOS_SOURCES` but the artifacts don't exist. The owner ratified the approach 2026-05-12: scale the cargo-c precedent into a tool.

## 2. Architecture

**Host-side, not chroot.** The helper runs on any maintainer machine with the Rust toolchain and crates.io reachability:

- Maintainer workstation / build coordinator host / any CI runner with the toolchain
- No sudo, no build VM, no chroot
- Outputs go to a designated directory (`build/vendor-artifacts/` by default); operator stages from there to the build mirror or directly into `build/sources/`

The helper is **maintainer infrastructure** — it produces artifacts that the build pipeline then consumes. The build pipeline itself (`download-sources.py` + `phase_validate` + `phase_build`) is unmodified; it just expects the artifacts to be present when phase_build runs.

## 3. CLI shape

```
scripts/cargo-vendor-gen.sh <pkg-name> <version> <source-url-or-path>
```

- `pkg-name` — must match `name:` in the package's `package.yml`
- `version` — must match `version:` in the package's `package.yml`
- `source-url-or-path` — either an `https://` URL (downloaded fresh) or a local path to an already-fetched source tarball (e.g., an entry in `build/sources/` from `download-sources.py`)

**Environment overrides:**

- `OUTPUT_DIR` — output directory (default: `build/vendor-artifacts/`)
- `SOURCE_DATE_EPOCH` — mtime stamp for tar entries (default: `0`)
- `KEEP_WORK=1` — preserve the temp extraction tree for inspection

**Outputs:**

- `<pkg-name>-<version>-vendor.tar.xz` — the vendor tarball
- `<pkg-name>-<version>-Cargo.lock` — the lockfile used during vendoring (side artifact for audit + drift detection; matches cargo-c precedent)

**Exit codes:**

- `0` — both artifacts produced + sha256 reported
- `1` — any step failed (network, cargo error, tar/xz error, etc.); error message on stderr
- `2` — bad arguments

**Machine-readable summary:** TSV on stdout, two lines (`vendor-tarball` + `cargo-lock`), each line `<kind>\t<absolute-path>\t<sha256>\t<meta>`.

## 4. Internals

The script walks these stages, each idempotent on its own work dir:

1. **Preflight** — tools on PATH, identifier validation, work dir created, cargo version captured (recorded for reproducibility).
2. **Resolve source** — URL → `curl` to work dir; path → use in place. Compute source sha256 for the report.
3. **Extract** — `tar -xaf` (auto-detects `.tar.gz` / `.tar.xz` / `.tar.bz2`). Locate project root: if exactly one top-level dir (GitHub archive convention), use it; otherwise use the extract dir.
4. **Cargo.lock handling** — if upstream ships `Cargo.lock`, mark as `upstream`. If absent, run `cargo generate-lockfile` and mark as `generated`. **Warn loudly that generated lockfiles are non-deterministic** (cargo resolves to latest compatible versions at generation time).
5. **`cargo vendor --locked --versioned-dirs`** — `--locked` refuses to update `Cargo.lock` mid-run; `--versioned-dirs` produces `<crate>-<version>/` subdirectories (multi-version safe + slightly more reproducible than the legacy unversioned shape).
6. **`.cargo/config.toml`** — written into the project root with the `[source.crates-io] replace-with = "vendored-sources"` + `[source.vendored-sources] directory = "vendor"` pair. This is what makes `cargo build --offline` actually use the vendored crates.
7. **Stage wrapper directory** — move `vendor/` and `.cargo/` into `<work>/stage/<pkg>-<version>/`. This is what gets archived. The wrapper-dir + `--strip-components=1` convention matches the cargo-c / aardvark-dns / G4-user-tools build.sh extract pattern.
8. **Reproducible tar+xz** — `tar --sort=name --owner=0 --group=0 --numeric-owner --mtime=@${SOURCE_DATE_EPOCH} --format=ustar` piped to `xz -T 1 -9`. These flags are the standard reproducible-builds.org recipe for tar archives.
9. **Emit Cargo.lock side artifact** — copy `<wrapper>/Cargo.lock` (which is wherever it ended up after step 7's move) to `<output-dir>/<pkg>-<version>-Cargo.lock`.
10. **Report** — full human-readable summary to stderr; TSV machine summary to stdout.

## 5. Reproducibility considerations

The archive is **byte-identical across runs** when these inputs are held constant:

| Input | How held | Notes |
|-------|----------|-------|
| Cargo version | Use the InterGenOS-built cargo (`/opt/rustc/bin/cargo`) or any pinned cargo version | Different cargo versions can produce slightly different vendor/ layouts |
| Cargo.lock contents | `--locked` in `cargo vendor` enforces this | If `Cargo.lock` is generated, run-to-run reproducibility breaks until the lock is committed somewhere durable |
| Crate availability on crates.io | Pin upstream Cargo.lock contents | Yanked crates can break re-vendoring |
| Tar format / mtimes / uid/gid | `--format=ustar` + zeroed metadata + `SOURCE_DATE_EPOCH` | See script for full flag list |
| xz compression | `-T 1 -9` | Multi-thread xz breaks block boundaries |
| Wrapper directory name | Derived from `<pkg-name>-<version>` args | Stable across runs |

**Not yet bit-identical with any pre-existing `<pkg>-<version>-vendor.tar.xz` on the build mirror.** Pre-existing artifacts predate this helper; their generation parameters (cargo version, tar/xz options, mtime) are unknown. v1 of the helper is reproducibility-going-forward — re-generating an existing package with this helper produces a new canonical artifact, and from then on subsequent re-generations match it byte-for-byte. The maintainer's swap-over step can:

(a) compare the new artifact's sha256 to the existing one (informational only — won't match);
(b) extract both and `diff -r` the contents — semantic equality should hold even if archive bytes don't;
(c) elect to swap the canonical artifact to the new helper output (recommended — kicks off the reproducibility-going-forward trajectory).

## 6. package.yml integration

The `build_artifacts:` field already exists in tree (4 in-master packages: cargo-c, cbindgen, rust-bindgen, librsvg + 18 packages in the recent G4 user-tools branch). `audit-yaml-source-pinning.sh` and `build-intergenos.sh phase_validate` already recognize the field as auditable-at-manifest-phase, not auditable-here. No package.yml schema change is needed.

The convention this helper enforces:

```yaml
source:
  - url: <upstream-source-url>
    sha256: <upstream-source-sha>
build_artifacts:
  - name: <pkg>-<version>-vendor.tar.xz
    generated_by: cargo-vendor
  - name: <pkg>-<version>-Cargo.lock        # only if upstream did not ship a Cargo.lock
    generated_by: cargo-vendor
```

The `generated_by: cargo-vendor` marker is what differentiates this from `generated_by: go-vendor` (see §9) or future generators (e.g., `generated_by: pip-vendor` for Python wheels-bundle, hypothetically).

## 7. Operator workflow

End-to-end, from "ripgrep needs a vendor tarball" to "Build #10 consumes it":

1. **Generate** (any host with network + rust toolchain):

   ```bash
   scripts/cargo-vendor-gen.sh ripgrep 14.1.0 \
     https://github.com/BurntSushi/ripgrep/archive/14.1.0/ripgrep-14.1.0.tar.gz
   ```

   This produces `build/vendor-artifacts/ripgrep-14.1.0-vendor.tar.xz` plus a side `Cargo.lock`. The script prints both sha256s.

2. **Stage** to the build mirror:

   ```bash
   rsync -av build/vendor-artifacts/ripgrep-14.1.0-vendor.tar.xz \
     <build-coordinator-host>:<repo-root>/build/sources/
   ```

   (Or directly into `build/sources/` if generating on the build coordinator host.) The build pipeline expects them in `$IGOS_SOURCES` (== `build/sources/` from project root).

3. **Verify** with `audit-yaml-source-pinning.sh` (it counts but doesn't validate `build_artifacts:` — that's fine; the validation happens at extract time in build.sh).

4. **Build #N runs.** `phase_build` for the ripgrep package: orchestrator extracts the upstream source. `configure()` in ripgrep's build.sh extracts the vendor tarball + `.cargo/config.toml`. `build()` runs `cargo build --release --frozen --offline`.

5. **Drift detection.** If upstream releases ripgrep 14.2.0, the maintainer bumps `version:` in `packages/extra/ripgrep/package.yml` + re-runs the helper with the new version + restages. Old artifacts can be archived or deleted at maintainer discretion.

## 8. Consumer-side requirements (build.sh shape)

The reference shape for a consumer build.sh, with the vendor tarball pattern fully applied:

```bash
#!/bin/bash
# <pkg> <version> — <description>

configure() {
    set -e
    # Extract vendor tarball (contains vendor/ + .cargo/config.toml)
    tar xf "${IGOS_SOURCES}/${PKG_NAME}-${PKG_VERSION}-vendor.tar.xz" --strip-components=1

    # If upstream did NOT ship a Cargo.lock, the helper emitted one as a side
    # artifact. Pull it into the source root before cargo build runs.
    if [ ! -f Cargo.lock ] && [ -f "${IGOS_SOURCES}/${PKG_NAME}-${PKG_VERSION}-Cargo.lock" ]; then
        cp -v "${IGOS_SOURCES}/${PKG_NAME}-${PKG_VERSION}-Cargo.lock" Cargo.lock
    fi
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/<binary> "${DESTDIR}/usr/bin/<binary>"
}
```

**Follow-on for current consumer build.sh files:** the recent G4 user-tools batch's build.sh files do `cargo build --release` without `--frozen --offline`. That's a follow-on fix — these flags enforce that cargo NEVER touches crates.io, which is critical in a no-network chroot. The 15 Rust build.sh files need a sweep to add `--frozen --offline`. Out of scope for this helper.

## 9. Go analog architecture (deferred impl)

3 Go packages (`gopls`, `hugo`, `lazygit`) in the recent G4 user-tools batch also need vendored dependencies. Go's analog of `cargo vendor` is `go mod vendor`. A `scripts/go-vendor-gen.sh` would mirror the cargo helper:

| Step | Cargo | Go |
|------|-------|----|
| Lockfile | `Cargo.lock` (always present after vendor) | `go.sum` (always present after vendor) + `go.mod` (project file) |
| Vendor cmd | `cargo vendor --locked --versioned-dirs` | `go mod vendor` |
| Config artifact | `.cargo/config.toml` redirecting `crates-io` | none needed; Go uses `vendor/` automatically when present at module root |
| Consumer build | `cargo build --frozen --offline` | `go build -mod=vendor` |

The wrapper-dir convention applies the same way: `<pkg>-<version>/vendor/`. Tar+xz reproducibility flags are identical. The script structure can be a near-duplicate of `cargo-vendor-gen.sh` with the cargo-specific steps swapped:

- Step 4 (lockfile) — check for `go.mod` instead of `Cargo.toml`; check for `go.sum` instead of `Cargo.lock`. `go mod download` if `go.sum` is incomplete.
- Step 5 (vendor) — `go mod vendor`.
- Step 6 (config) — skip; Go uses `vendor/` implicitly.
- Step 7+ (stage + tar+xz) — identical (just `vendor/` under the wrapper, no `.cargo/`).

**Deferred for v1.1** — implementation gated on the Rust v1 helper landing + at least one Rust package end-to-end-tested through `phase_build`. Once that's proven, the Go helper is a ~1h port. The recent G4 user-tools batch has 3 Go consumers that will need the Go artifacts before they can build.

## 10. Failure modes + recovery

| Mode | Symptom | Recovery |
|------|---------|----------|
| Source URL down (404 / timeout) | `curl failed for <url>` | Retry; switch to a mirror; pass a local path instead |
| Source SHA drift (upstream re-tagged a release) | `tar -xaf` succeeds but produces unexpected layout; or `cargo vendor` fails on missing `Cargo.toml` | Verify `source.sha256` in `package.yml` against upstream; if upstream re-tagged, ratchet the SHA and version-bump if appropriate |
| `Cargo.lock` missing AND `cargo generate-lockfile` produces a different lock each run | Helper warns + proceeds; subsequent re-runs produce different vendor tarballs | Commit the generated `Cargo.lock` somewhere durable (the side artifact emitted alongside `vendor.tar.xz` IS that durable copy); future re-vendoring should reuse that exact lockfile by passing it in (TODO: helper flag in v1.1 to accept an existing Cargo.lock as input) |
| `cargo vendor` fails — yanked crate | `cargo vendor failed`; cargo's own error message points at the yanked crate | Bump Cargo.lock to a replacement version (often a security-patched release); re-vendor |
| `cargo vendor` fails — crates.io unreachable | `cargo vendor failed` with network error | Retry; check `curl https://crates.io/` reachability; check `[source.crates-io]` registry override in `~/.cargo/config.toml` if maintainer is using a private registry |
| Network OK but `cargo vendor` produces 0 crates | `vendored 0 crates` in the report | Project has no external deps; this can be legitimate. Tar+xz still produces a valid (small) tarball |
| tar / xz failure | `tar+xz failed` | Out of disk space; permissions; check `OUTPUT_DIR` |
| KEEP_WORK=1 + multiple runs | Work dirs accumulate in `/tmp` | Manual cleanup via `rm -rf /tmp/cargo-vendor-gen.*` |

## 11. Security posture

- **Source verification at the boundary:** `package.yml` pins the upstream source sha256. The helper reports its source-sha for cross-checking against the pin (operator should diff). If they don't match, the operator has fetched the wrong artifact and should abort before staging.
- **Crate-level trust:** the vendored crates are fetched from crates.io directly. `cargo vendor` does NOT cryptographically verify them — it trusts whatever crates.io serves. This is the same trust model as `cargo build` itself; the helper doesn't degrade it.
- **No build-time network in chroot:** `--offline --frozen` in the consumer build.sh guarantees the chroot stays network-free. The helper is the only network-touching step in the whole package lifecycle.
- **Reproducibility as audit:** byte-identical re-generation means any later operator can run the helper again with the same Cargo.lock and verify they get the same sha256. Any divergence signals supply-chain tampering OR a cargo version mismatch (both worth investigating).
- **Out-of-scope for v1:** sigstore / SLSA attestations, sbom emission for the vendored crate set. Future work; the side `Cargo.lock` artifact contains enough info to bootstrap an SBOM later.

## 12. Verification + testing

This dispatch's acceptance verification:

- **Syntax:** `bash -n scripts/cargo-vendor-gen.sh` PASS
- **End-to-end against cargo-c-0.10.20:** the helper was run against `https://github.com/lu-zero/cargo-c/archive/v0.10.20/cargo-c-0.10.20.tar.gz` using the InterGenOS-built Cargo 1.93.1 toolchain. 419 crates vendored. Output sha256s captured in the branch commit message for peer-review.
- **Internal reproducibility:** the archive-shape determinism was verified by extracting the run-1 output and re-tar+xz-ing with identical flags — produced a byte-identical archive (sha256 match against run-1).
- **Reproducibility-against-existing:** when re-generating an existing package, expect the sha256 NOT to match any pre-existing artifact on the build mirror (generation parameters of pre-existing artifacts are unknown). Recommended swap-over: replace the canonical artifact with the new helper output going forward.

## 13. Limitations + future work

- **No `Cargo.lock`-as-input flag in v1.** When upstream doesn't ship `Cargo.lock`, the helper currently calls `cargo generate-lockfile` which is non-deterministic. v1.1 should add `--cargo-lock <path>` so the operator can pass a previously-emitted side artifact back in to lock the run.
- **No Go support in v1.** See §9.
- **No package.yml introspection.** The helper takes name + version as args rather than reading them from package.yml. Trade-off: simpler helper, easier to script in bulk. v1.1 could add a `--from-package-yml <path>` mode that derives args.
- **No bulk-run wrapper.** The current G4 user-tools batch has 15 Rust packages; running the helper 15 times manually is tractable but a `scripts/cargo-vendor-gen-all.sh` that walks `packages/*/*/package.yml` looking for `generated_by: cargo-vendor` artifacts and runs the helper for each is a reasonable v1.1.
- **No SBOM / attestation emission.** Mentioned in §11. v2 territory.
- **No automatic stage-to-mirror.** Operator runs `rsync` manually. A `--stage-to <host>:<path>` flag would close that loop. v1.1 candidate.

## 14. Provenance

Precedents consulted during design:

- `packages/core/cargo-c/{package.yml, build.sh}` — the cargo-c pattern (vendor tarball + separate Cargo.lock)
- `packages/extra/aardvark-dns/{package.yml, build.sh}` — the explicit `.cargo/config.toml` + `--frozen --offline` pattern
- `packages/core/{cbindgen, rust-bindgen}/package.yml` + `packages/desktop/librsvg/package.yml` — `build_artifacts:` field precedent
- `docs/research/build_system/offline_rust_builds_2026-04-03.md` — manual vendoring procedure this helper replaces; the archive-shape decision (vendor/ + .cargo/ under a wrapper dir, extracted by build.sh with `--strip-components=1`) is taken from §"Vendoring Workflow" of that doc.
