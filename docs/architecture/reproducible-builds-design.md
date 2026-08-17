# Reproducible Builds — Design Scope

**Status:** scoping / pre-implementation. InterGenOS 1.0-dev carries the goal but not yet the full toolchain plumbing. This document sizes the gap and sequences the work toward the 1.x reproducible-builds milestone.

**Audience:** maintainers planning the 1.x reproducible-builds work; auditors evaluating the build-chain transparency claim; contributors implementing the audit harness.

---

## 1. TL;DR

A **reproducible build** means: starting from the same source code, the same toolchain, and the same documented build environment, two independent builders produce byte-identical output. Bit-for-bit identical archive files. Bit-for-bit identical filesystem image. Cryptographic hashes that match without any "well, except for the timestamps and the build path embedded in the binary" caveats.

**Why InterGenOS cares.** Three threads converge:

1. **Auditability.** A third party who fetches our source, applies our recipes, and runs our build VM should produce a `.igos.tar.gz` that is byte-identical to the one we publish on `repo.intergenos.org`. If it does not match, then either the published recipe does not describe the real build, our infrastructure has been tampered with, or there is a non-determinism bug. All three are findable with reproducibility, and none are findable without it.

2. **Local-build trust.** The planned local-build-on-running-InterGenOS feature builds packages from source on the user's machine instead of pulling from the binary mirror. For that mode to be auditable, the user's local rebuild must be byte-equivalent to the published binary. Until reproducible builds are in place, local builds are advisory: they prove the source compiles but not that it produces the same artifact a trusted builder would have produced.

3. **Industry signal.** Debian's Reproducible Builds requirement goes into effect March 2026 (see `docs/package-tiers.md`, "Why this matters for reproducibility"). The bar for distributions that take security seriously is shifting from "we sign the binary" to "the binary is independently verifiable from source." Reproducible builds are a security primitive: they let downstream verifiers detect silent backdoor insertion or build-environment tampering that would not otherwise show up. This aligns directly with InterGenOS's security posture: security is not first. It is only.

**Current state.** Bit-identical output is the documented 1.0 goal, and three pieces of it are already in place:

- **The artifact-assembly pipeline pins `SOURCE_DATE_EPOCH` by construction.** `ensure_source_date_epoch()` in `scripts/build-intergenos.sh` derives the epoch from the recipe tree's tip commit time (respecting a caller-provided value, falling back to a hook-written stamp when the tree is a worktree whose git pointer does not resolve, and warning loudly rather than defaulting silently if neither is available) and exports it before the bootloader → squashfs → UKI → ISO phases. Deriving it from the recipe tip also keeps the value identical across the ceremony-resume chain, which a per-invocation wall clock never could.
- **The in-tree source tarballs are byte-reproducible.** `scripts/build-intergenos-source-tarballs.sh` packs with a fixed epoch and deterministic tar/xz settings; `scripts/build-source-archives.py` writes its archives through a deterministic writer (gzip with a zeroed mtime and no embedded filename, a sorted member walk, member times pinned to `SOURCE_DATE_EPOCH`, normalized ownership, and a pinned archive format). Two independent runs hash identically.
- **The cargo-vendor pipeline** (`scripts/cargo-vendor-gen.sh`) produces reproducible vendor tarballs with the standard recipe.

What is **not** yet reproducible is the piece that matters most for the published binaries: the `.igos.tar.gz` emission step (`scripts/emit-package-archives.py`) still uses Python's `tarfile` defaults and applies no normalization, and `SOURCE_DATE_EPOCH` is not exported into the per-package chroot builds. Several other gaps follow from those two; this document enumerates them.

**1.x target.** Byte-identical `.igos.tar.gz` per package across two builders running the same recipe, plus an audit harness (`scripts/verify-reproducible.sh <pkg>`, to be written) that any contributor can run locally to confirm the property holds for a given package. Implementation is sized in §8 below. The work is non-trivial but not exotic: every step has prior art in the projects surveyed in §2.

---

## 2. Prior art

The reproducible-builds problem is not new; multiple distributions have walked the same path. The patterns below are reference material, not a checklist to copy. InterGenOS's trust posture and architectural choices differ from each, so we reason from first principles even when we land on a similar solution.

| Distribution | Approach | What we would borrow |
|---|---|---|
| **Debian** | The reproducible-builds.org effort started here. Standard recipe: `SOURCE_DATE_EPOCH` env var consumed by every build system that supports it; tar archives normalized with `--sort=name --owner=0 --group=0 --numeric-owner --mtime=@${SOURCE_DATE_EPOCH}`; gzip in no-name mode (`gzip -n`); locale/timezone pinned (`LC_ALL=C TZ=UTC`); build-path normalization via `BUILD_PATH_PREFIX_MAP` (a compiler env var consumed by GCC and Clang). Tests-as-truth: `reprotest` re-runs a build under varied conditions (different hostname, locale, kernel version, ASLR seed) and bit-compares output. | The whole envelope. `SOURCE_DATE_EPOCH` + tar flags + gzip `-n` + `BUILD_PATH_PREFIX_MAP` are de-facto industry standard. `reprotest` itself is Python and could be vendored or our audit harness could re-implement the essential checks. |
| **Arch Linux** | Followed Debian's lead later but with tighter integration: makepkg honors `SOURCE_DATE_EPOCH` directly; the rebuilders.dev infrastructure independently rebuilds Arch packages and publishes a verified-bit-identical badge. | The rebuilders model. Eventually we would want third parties to publish independently-verified rebuilds of our binary mirror. This is a 2.x consideration, not a 1.x one. |
| **Nix / NixOS** | Whole-derivation determinism: every input (source, dependencies, build flags, compiler version) is fed into a hash that becomes the package's identity. If any input changes, you get a different output hash. The build sandbox is hermetic (no network, no `/usr` access, no time-of-day reads), so non-determinism is suppressed at the source. | The hermeticity principle. Our chroot is more permissive than Nix's sandbox; we can borrow the "what should be inputs to the recipe hash" framing without adopting the full Nix model. |
| **openSUSE** | `BUILD_DIR` isolation: the build runs in a per-package randomized path, then the build system rewrites that path to a constant in the output. `osc build --reproducible` enforces the reproducibility checks pre-merge. | The pre-merge gate framing. Our `verify-reproducible.sh` (§6) can run on every package change. |

The common thread: reproducibility is achieved by suppressing every input that varies between builders, including clock, hostname, build path, parallel-make ordering, and randomization-derived data. The recipe specifies the inputs that should matter; everything else gets pinned, normalized, or stripped.

A note on inspiration versus copy: the Debian recipe is well-documented and widely used precisely because it is the standard. Adopting the standard environment variable (`SOURCE_DATE_EPOCH`) and the standard tar flags is the right call, because third-party verifiers will already know what to look for. The InterGenOS-specific work is the integration layer: where in our build pipeline these environment variables enter, which scripts normalize output, and what the audit harness validates. That layer is the work, not the underlying technique.

---

## 3. What "reproducible" means for InterGenOS specifically

A precise definition matters: "reproducible" is too broad a word, and different distributions mean different things by it. The InterGenOS commitment for the 1.x series:

**A.** Given the same source tarball (sha256-pinned in `packages/<tier>/<pkg>/package.yml`), the same compiled toolchain (kernel + binutils + gcc + glibc + python + etc., at the versions pinned in `packages/core/`), and the same documented build environment (chroot at the same kernel version, same `SOURCE_DATE_EPOCH`, same locale + timezone), two builders produce **byte-identical `.igos.tar.gz` archives** for every package in `packages/`.

Specifically:

- The compressed `.tar.gz` itself bit-compares clean (sha256 match).
- Inside the archive: every file has the same content, same mode, owner=root, group=root, mtime=`@${SOURCE_DATE_EPOCH}`.
- The tar metadata is normalized: pax-format extended headers (per the fix in commit `719db93e`, where pax is the long-path-safe format), sorted file entries (`--sort=name`), and no per-build randomization in archive ordering.
- The gzip header carries no embedded filename and no timestamp (the gzip `-n` semantics, but achieved through Python's `tarfile` API tuning since `emit-package-archives.py` uses Python rather than shell gzip).

**B.** Given the same `.igos.tar.gz` set produced under (A), the published `InterGenOS.db` index (generated by `scripts/generate-repodb.py`) is byte-identical across builders. This follows from (A) plus the index generator's own determinism, which is already in place per the per-archive-signature decision (`docs/architecture/per-archive-sig-decision.md`).

**C.** Given the same package archive set and index, the final ISO image is byte-identical across builders. This is a stretch goal for the 1.x series. ISO determinism layers on top of (A) and (B) but also requires squashfs determinism, El Torito metadata normalization, and EFI System Partition image determinism. This is most likely a 1.2 milestone rather than 1.0, but it is worth scoping now so the 1.0 archive work does not preclude it.

**Not addressed by this document:** reproducibility of the build VM image itself (the chroot toolchain). That is a chicken-and-egg problem: making the toolchain reproducible requires a reproducible build of the toolchain, which requires a bootstrap. The standard treatment (Debian's "bootstrappable-builds" effort, Guix's "full source bootstrap") is multi-year work. This document covers reproducibility of `extra` and `desktop` tier packages with the build VM held fixed; bootstrap-tier reproducibility is a separate effort.

---

## 4. What's missing today — concrete survey

This section walks the actual code paths and identifies where non-determinism leaks in. It cites functions and call sites by name rather than by line number, so it stays valid as the files move.

### 4.1 `scripts/emit-package-archives.py` — the primary gap

The `.igos.tar.gz` emitter uses Python's `tarfile` module:

```python
# emit-package-archives.py (the tarfile.open / tar.add site, ~line 264)
with tarfile.open(archive_path, "w:gz") as tar:
    ...
    tar.add(fullpath, arcname=arcname)
```

By default, Python's `tarfile.open(..., "w:gz")`:

- Reads mtime from the source filesystem (`os.stat`) and embeds it in each tar header — **not deterministic**.
- Reads uid/gid from the source filesystem and embeds them — **not deterministic** (depends on whoever ran the build).
- Uses gzip via the `gzip` module which embeds the original filename and current timestamp in the gzip header — **not deterministic**.
- Adds files in iteration order of the manifest, which is fine if the manifest is sorted (need to verify).

**Fix sketch:**

```python
# Replace plain tar.add() with a transform that normalizes each entry
def reset_member(tarinfo):
    tarinfo.uid = 0
    tarinfo.gid = 0
    tarinfo.uname = "root"
    tarinfo.gname = "root"
    if SOURCE_DATE_EPOCH:
        tarinfo.mtime = int(SOURCE_DATE_EPOCH)
    return tarinfo

# For deterministic gzip, write to .tar first, then gzip with mtime=0 and no name:
import gzip
with tarfile.open(tar_path, "w") as tar:
    for fullpath, arcname in sorted(entries):  # explicit sort
        tar.add(fullpath, arcname=arcname, filter=reset_member)
with open(tar_path, "rb") as f_in:
    with gzip.GzipFile(archive_path, "wb", mtime=0) as f_out:
        f_out.write(f_in.read())
```

Estimated change: ~30 lines in `emit-package-archives.py`. Tests at `tests/repo-publish/test_emit_package_archives.py` would gain a "two runs produce byte-identical output" case.

### 4.2 `scripts/build-intergenos.sh` — SOURCE_DATE_EPOCH plumbing partial

The orchestrator pins the variable for the artifact-assembly phases (see `ensure_source_date_epoch()` and the manifest-emission path) but does **not** export it into the chroot, so the per-package build steps never see it. As a result:

- C/C++ compilers that consume `SOURCE_DATE_EPOCH` for `__DATE__` / `__TIME__` macro values fall back to live system time, which leaks into binaries.
- Python `.pyc` cache files embed source mtime, which leaks into Python packages.
- Build systems that consult `SOURCE_DATE_EPOCH` (autotools, meson, recent CMake) silently degrade to non-reproducible mode.

**Fix sketch:** export `SOURCE_DATE_EPOCH` into the chroot environment before invoking any per-package build, and choose a canonical timestamp source. Two reasonable choices:

- **Per-package timestamp:** use the source tarball's mtime, the upstream release date, or a value pinned in `package.yml`. Stable across rebuilds; varies per package, which is fine.
- **Per-build timestamp:** use the value passed in at top-level build invocation (`INTERGENOS_BUILD_TS` or similar). Same across all packages in a given build run; varies across builds, which means two builders need to agree on a value to get matching output.

The reproducible-builds.org consensus is the per-package model (typically derived from the most recent git commit touching that package's recipe, or the source tarball's release date). Recommended path: derive from `package.yml`'s pinned source mtime or a new `source_date_epoch:` field.

Estimated change: ~50-100 lines across `build-intergenos.sh` and each `build_style` helper.

### 4.3 Build-path leakage

The build chroot lives at `/mnt/intergenos/build/chroot/...`. Several leakage paths exist:

- **Debug info.** ELF binaries built with `-g` embed the source file path. `gcc -fdebug-prefix-map=/mnt/intergenos/build=/build` (or the newer `-ffile-prefix-map`) rewrites this at compile time. This applies to every C/C++/Rust binary in the tree.
- **`__FILE__` macro.** C/C++ source files using `__FILE__` for log messages or assertions embed the absolute path. The `-fmacro-prefix-map` flag handles this.
- **Build system embedded strings.** Some build systems (autotools `config.log`, meson `meson-info/`, CMake `CMakeCache.txt`) embed the build directory path in metadata files that ship inside the source tree even if not installed. These are typically not in the install output, but they are worth auditing per package.
- **Python `.pyc` cache.** CPython embeds the source path in the bytecode cache. Setting `PYTHONHASHSEED=0` plus a custom `tempfile.gettempdir` override is not sufficient; the canonical fix is to compile `.pyc` from a constant build-prefix root. Most distributions normalize the install path to `/usr/lib/python3.NN/` after install, which sidesteps this for installed bytecode but not for build-time-only artifacts.

**Fix sketch:** centralized export in the chroot env:

```bash
export BUILD_PATH_PREFIX_MAP="/mnt/intergenos/build=/build"
export CFLAGS="${CFLAGS} -ffile-prefix-map=/mnt/intergenos/build=/build"
export CXXFLAGS="${CXXFLAGS} -ffile-prefix-map=/mnt/intergenos/build=/build"
export RUSTFLAGS="${RUSTFLAGS} --remap-path-prefix=/mnt/intergenos/build=/build"
```

Estimated change: ~20 lines in the chroot bootstrap (`scripts/build-intergenos.sh` chroot setup, plus mirroring in each `igos-build` style class).

### 4.4 Locale + timezone

The chroot likely doesn't pin `LC_ALL` and `TZ`. Side effects: locale-dependent sort orders in build-system output, timezone-shifted timestamps in any embedded log lines.

**Fix sketch:** `export LC_ALL=C` and `export TZ=UTC` in the chroot env init. Trivial.

### 4.5 Parallel-make non-determinism

`make -j${IGOS_JOBS}` can produce different binaries depending on job ordering, typically because the linker's symbol resolution order depends on which object file is ready first, and that is parallel-dependent. The fix is not to drop to `-j1` (build time would balloon); the fix is to use deterministic linkers and link-order pinning.

- The `gold` linker is deterministic by default.
- The `lld` linker is deterministic by default.
- BFD `ld` (the GNU default) is deterministic when invoked with `--no-undefined-version` and explicit object-file ordering, which means the build system must pass objects in a stable order. Most modern build systems do, but it is worth checking.

**Fix sketch:** survey which packages use which linker. The ones most worth checking are those with hundreds of translation units (Firefox, WebKitGTK, LibreOffice). Most packages likely need zero work, but a one-time audit confirms it.

### 4.6 cargo-vendor pipeline — already reproducible

The cargo-vendor pipeline at `scripts/cargo-vendor-gen.sh` already produces reproducible vendor tarballs:

```bash
# scripts/cargo-vendor-gen.sh (paraphrased)
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime=@${SOURCE_DATE_EPOCH} --format=pax vendor/ | xz -T 1 -9
```

This is the canonical reproducible-tar recipe. The `--format=pax` choice (instead of `--format=ustar`) was a fix in commit `719db93e` to handle long crate paths that exceeded the ustar 100-character limit. It is functionally equivalent for reproducibility.

The cargo-vendor work demonstrates that reproducible builds are achievable in the InterGenOS tree. The same recipe, ported to the `.igos.tar.gz` emitter, achieves the same property.

### 4.7 Survey summary

| Gap | Severity | Fix complexity | File |
|---|---|---|---|
| `tarfile` defaults non-deterministic | high | ~30 lines | `scripts/emit-package-archives.py` |
| `SOURCE_DATE_EPOCH` not exported into chroot | high | ~50-100 lines | `scripts/build-intergenos.sh` |
| Build-path embedded in binaries | high | ~20 lines | chroot init + build-style classes |
| Locale + timezone not pinned | medium | trivial | chroot init |
| Parallel-link non-determinism | low (mostly already-reproducible) | audit only | per-package |
| Vendor tarballs already reproducible | DONE | — | `scripts/cargo-vendor-gen.sh` |

---

## 5. Concrete fix list (sequenced)

1. **Add `SOURCE_DATE_EPOCH` export to chroot init.** Source from `package.yml` (new field) or from source tarball mtime. Touches `scripts/build-intergenos.sh` chroot-setup + the style classes in `igos-build/`.

2. **Add `-ffile-prefix-map` / `--remap-path-prefix` to `CFLAGS` / `CXXFLAGS` / `RUSTFLAGS`** in the chroot env init. Same path: `/mnt/intergenos/build=/build`.

3. **Add `LC_ALL=C` and `TZ=UTC` to chroot env.** One-liner; should be in chroot bootstrap.

4. **Normalize `scripts/emit-package-archives.py`** to apply the per-member `reset_member` filter (uid/gid=0, mtime=`SOURCE_DATE_EPOCH`) and write the gzip layer separately with `mtime=0`. Add tests at `tests/repo-publish/test_emit_package_archives.py` covering the "two consecutive runs produce byte-identical output" property.

5. **Audit per-package compiler flags.** Some packages override `CFLAGS` entirely (glibc, gcc itself, the kernel). For those, ensure `-ffile-prefix-map` is preserved or re-injected.

6. **Build the audit harness** (`scripts/verify-reproducible.sh`, see §6).

7. **Run the harness against the current package tree.** Whatever fails the bit-identity check gets its own remediation pass.

8. **CI integration.** Per-package CI gate that runs `verify-reproducible.sh <pkg>` against the published archive.

9. **Documentation.** Update `docs/users/security-defaults.md` and `docs/repository-trust.md` to claim the reproducibility property once it is verified.

Sequencing rationale: items 1-3 are cheap and unblock everything else. Item 4 is the highest-leverage single change, since it captures most of the visible non-determinism in archive output. Items 5-7 form the validation loop. Item 8 makes the property self-maintaining going forward. Item 9 is the user-facing payoff.

---

## 6. Audit harness — `scripts/verify-reproducible.sh`

A single-package verifier:

```bash
#!/bin/bash
# verify-reproducible.sh <pkg-name>
#
# Rebuild <pkg-name> in a fresh chroot using the canonical recipe,
# then bit-compare the resulting .igos.tar.gz against the published
# archive on master. Exit 0 if identical, 1 with a diff summary if not.

set -e
pkg="$1"
[ -z "$pkg" ] && { echo "usage: $0 <pkg>"; exit 2; }

# Fetch the published archive into a per-run scratch directory.
mkdir -p "$HOME/tmp/repro-check"
published="$HOME/tmp/repro-check/${pkg}-published.igos.tar.gz"
curl -fsSL "https://repo.intergenos.org/x86_64/packages/${pkg}.igos.tar.gz" -o "$published"

# Rebuild in fresh chroot
rebuild_dir=$(mktemp -d)
SOURCE_DATE_EPOCH=$(yq '.source_date_epoch // ""' "packages/*/${pkg}/package.yml")
[ -z "$SOURCE_DATE_EPOCH" ] && SOURCE_DATE_EPOCH=$(stat -c %Y "packages/*/${pkg}/package.yml")
export SOURCE_DATE_EPOCH

scripts/build-intergenos.sh --single-package "$pkg" --output-dir "$rebuild_dir"
rebuilt="${rebuild_dir}/${pkg}.igos.tar.gz"

# Bit-compare
if cmp -s "$published" "$rebuilt"; then
    echo "REPRODUCIBLE: $pkg matches published archive"
    exit 0
else
    echo "MISMATCH: $pkg differs from published"
    # Use diffoscope if available, fall back to manual diff
    if command -v diffoscope >/dev/null; then
        diffoscope "$published" "$rebuilt" | head -100
    else
        # Decompress both, compare tar contents
        ...
    fi
    exit 1
fi
```

Output format: stdout = PASS/FAIL one-liner; exit code 0/1; diff details to stderr or a sidecar log file. Suitable for CI gate use.

`diffoscope` is the canonical reproducible-builds tool for diffing archives. It descends into nested archives, normalizes compression layers, and pinpoints the exact bytes that differ. It is worth packaging as a maintainer tool even if it is not in the default user install.

---

## 7. Integration with local-build-on-running-InterGenOS

The planned local-build feature adds a mode where users build packages from source on their own machine (a running InterGenOS host) rather than downloading the prebuilt binary from the mirror. Without reproducible builds, this mode is advisory: the user proves the source compiles, but they cannot prove the resulting binary matches what a trusted builder would have produced. With reproducible builds, the local build can be cryptographically equivalent to the canonical rebuild:

- User runs `pkm install --build-from-source <pkg>` (the eventual CLI surface).
- pkm fetches the source per `package.yml` and applies the recipe.
- pkm emits the local `.igos.tar.gz`.
- pkm fetches the published `InterGenOS.db` entry's sha256 for the same package.
- pkm bit-compares the local archive to the published sha256. If they match, the user's local build is byte-equivalent to the trusted build, and the user has independently verified the recipe produces the binary.
- If they do not match, pkm shows the diff and lets the user decide whether to install. This is rare but legitimate; a downstream patch the user applied locally would diverge here.

This gives users a real trust upgrade: they can rebuild from source and verify against the published binary without trusting our build infrastructure. The guarantee shifts from "we signed it" to "we signed it, and any user can independently confirm the binary matches the source recipe."

**Dependency:** local-build implementation can proceed in parallel with reproducible-builds work, but the audit-from-local mode is meaningful only after §5 items 1-4 land. The first iteration can ship as advisory-only (source compiles, archive produced, no cryptographic-equivalence claim), and the equivalence claim layers on once reproducibility is verified.

---

## 8. Sequencing and effort estimate

| Phase | Items | Effort | Dependencies |
|---|---|---|---|
| **Foundation** | §5.1-3 (env vars, chroot init) | ~150 lines code, 1-2 days | none |
| **Archive emitter** | §5.4 (`emit-package-archives.py`) + tests | ~80 lines code, ~50 lines tests, 1 day | foundation |
| **First audit** | §5.6 (build harness) + §5.7 (sweep) | harness ~100 lines, sweep is per-package remediation, 2-5 days depending on package count | emitter + foundation |
| **CI integration** | §5.8 (per-change check) | ~20 lines CI config, 0.5 day | first audit passing for a baseline set |
| **Documentation** | §5.9 (user-facing) | doc updates, 0.5 day | claim is verifiable |
| **Bootstrap reproducibility** | toolchain layer (separate effort) | months, multi-quarter | beyond 1.x |
| **ISO reproducibility** | squashfs + EFI image determinism | weeks, 1.2 milestone | per-package reproducibility solid |

**Total 1.x scope (excluding bootstrap and ISO):** roughly 5-10 days of focused work plus the package-by-package remediation pass. The remediation pass is the unknown. Most packages will be reproducible incidentally once items 1-4 are in place; some will have package-specific non-determinism that needs per-recipe fixes. The cargo-vendor pipeline already proved the technique on a real workload, so we have evidence the approach scales.

---

## 9. Open questions

1. **`SOURCE_DATE_EPOCH` source — per-package or per-build?** Recommendation: per-package, derived from a new `source_date_epoch:` field in `package.yml` (defaulting to source tarball mtime if unspecified). Per-package matches Debian's convention and means the value does not drift with the build clock. Per-build (a single value across all packages) is simpler but means two builders must agree on a number to get matching output, which is a coordination problem worth avoiding.

2. **Bootstrap-layer reproducibility — 1.x or 2.x?** Recommendation: 2.x. The toolchain bootstrap (kernel, binutils, gcc, glibc) is a multi-quarter effort and would dwarf the 1.0/1.x security work. The pragmatic 1.x posture is: every package above the bootstrap layer is reproducible; the bootstrap is held fixed at known-good versions and verified by hash.

3. **`diffoscope` as a maintainer dependency.** Recommendation: yes. Package it under the `extra` tier (it is Python, with no exotic dependencies) and document it in the audit-harness section, but do not include it in the default install footprint. Maintainers running `verify-reproducible.sh` would `pkm install diffoscope` if it is not already present.

4. **CI gate timing — block on reproducibility failures?** Recommendation: warn-only initially, escalating to block once the package tree reaches 90%+ reproducibility coverage. Blocking too early means a flaky non-determinism in a single legacy package halts unrelated work. Warn-only lets us track coverage and prioritize fixes without disrupting development.

5. **ISO reproducibility scope — 1.x stretch or 1.2 milestone?** Recommendation: 1.2 milestone. Squashfs determinism is a separate problem space (squashfs orders inodes by directory walk, which is filesystem-state-dependent) and should not block per-package reproducibility from landing. Squashfs has `-noI -noD -noF -noX -no-fragments` flags that help; this warrants a focused investigation rather than bolting it onto the package work.

6. **Rebuilders network — invite third parties to verify?** Recommendation: 2.x. Once the property is in place and the audit harness is stable, an outside-rebuilder posture (like rebuilders.dev for Arch) would be a strong public-trust signal. The prerequisite is internal verification at 99%+ coverage; setting it up before then would generate noise rather than signal.

---

## References

- `docs/package-tiers.md` ("Why this matters for reproducibility") — the user-facing rationale.
- `docs/research/build_system/cargo_vendor_helper_v1.md` — the canonical reproducible-tar recipe demonstration.
- `docs/research/build_system/security_remediation_plan_2026-04-08.md` — early enumeration of `SOURCE_DATE_EPOCH` as a known-needed fix.
- `scripts/build-intergenos.sh` — `ensure_source_date_epoch()` (recipe-tip epoch pinning for the assembly pipeline) plus the manifest-header plumbing.
- `scripts/emit-package-archives.py` — the `tarfile.open(..., "w:gz")` call site; the Python `tarfile` non-determinism site.
- `scripts/cargo-vendor-gen.sh` and `scripts/build-source-archives.py` — prior-art reproducible-archive implementations in this tree (the canonical recipe, and a deterministic Python writer respectively).
- reproducible-builds.org — upstream specification, `SOURCE_DATE_EPOCH` definition, `BUILD_PATH_PREFIX_MAP` semantics.
- Debian Reproducible Builds wiki — `reprotest` reference, `diffoscope` documentation.
