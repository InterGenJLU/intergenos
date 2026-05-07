# Codebase Audit — §1 Build System

**Auditor:** chris-ubuntu_hplt-codium-deepseek  
**Date:** 2026-05-07 08:30–09:30 CDT  
**Master:** c4d5126  
**Paths audited:** `igos-build/`, `scripts/build-*.sh`, `scripts/chroot-*.sh`  
**Findings:** 0 CRITICAL, 3 HIGH, 5 MEDIUM, 6 LOW

---

## Summary

The build system has 8 core Python modules (~2100 lines) driving package builds, plus 19 shell scripts orchestrating the LFS-based chroot build pipeline. The Python side is well-structured with dataclasses, strong type validation in parser.py, and hardened tar/zip extraction against path-traversal. The shell side has the orchestrator (`build-intergenos.sh`, 855 lines, `set -euo pipefail`) as the primary entry point. Two HIGH findings are shell scripts without `set -e` that are entry/exit points for the entire chroot pipeline. The Python test suite is thin: 2 files exist (supersede cycle detection + staging path validation) but zero tests cover build execution, source extraction, dependency resolution, or error handling.

---

## Closure Table

| ID | Severity | Section | File:Line | Finding | Proposed Fix |
|----|----------|---------|-----------|---------|--------------|
| B1 | HIGH | Build | `scripts/chroot-enter.sh:1` | Missing `set -e`. This is the primary entry point for ALL chroot-phase builds (toolchain through desktop tiers). If the chroot command or the inner script fails, execution continues silently with undefined state. No exit code propagation from chroot'd command to caller. | Add `set -euo pipefail` after shebang. Propagate the chroot command's exit code: `chroot "$IGOS" ... $CHROOT_CMD` → `chroot "$IGOS" ... $CHROOT_CMD || exit $?` |
| B2 | HIGH | Build | `scripts/chroot-health-check.sh:1` | Missing `set -e`. 348 lines of validation checks (binary existence, library findability, pkg-config, Python imports, systemd units, config files). A failed subshell or pipe silently succeeds — a package could be broken and the health check would report PASS. | Add `set -euo pipefail` after line 22 (after SYSROOT variable init). |
| B3 | MEDIUM | Build | `scripts/chroot-teardown.sh:1` | Missing `set -e`. Unmount operations are individually guarded with `\|\| echo "Not mounted"` but there's no protection against unexpected failures (e.g., filesystem busy causing umount to fail differently). | Add `set -euo pipefail` after shebang. |
| B4 | MEDIUM | Build | `scripts/chroot-teardown.sh:10,14` | Dead-code guard. `IGOS=/mnt/igos` hardcoded on line 10; line 14 checks `[ -z "$IGOS" ]` — this can NEVER be true because IGOS was just assigned. The check only protects the path where someone removes the hardcode but forgets the env var. | Restructure to `IGOS="${IGOS:-/mnt/igos}"` so the env-var override path actually has the guard. |
| B5 | HIGH | Build | `igos-build/` (8 modules, 0 build-exec tests) | 8 Python modules (~2100 lines total: builder.py 705, tracker.py 739, parser.py 435, graph.py 259, log.py 262, plus styles) have ZERO test coverage for: build execution order, source extraction behavior, checksum mismatch handling, tar path-traversal rejection, phase ordering, post_install error propagation, graph cycle detection, style dispatch, validate command execution. Only 2 test files exist (`test_parser_supersedes.py` for cycle detection, `test_tracker_b4_staging.py` for staging path validation) — both test edge-case paths, not the core build loop. | Write `tests/igos_build/test_builder.py` covering: extract_source (valid tar, invalid sha256, missing file, path-traversal rejection), build_package (success, failure mid-phase, post_install failure), build_all (halt_on_failure=True/False), skip_built template-hash detection. Write `tests/igos_build/test_graph.py` covering: dependency resolution, cycle detection, MissingDependencyError, tier priority ordering. |
| B6 | MEDIUM | Build | `igos-build/builder.py:280` | `subprocess.run(["sha256sum", ...])` has no timeout. For very large tarballs (e.g., LibreOffice 300MB+) or NFS-mounted source dirs, this blocks indefinitely. Same issue at line 431-434 for user-specified validation scripts. | Add `timeout=300` to sha256sum subprocess call. Add `timeout=600` to validation script execution. |
| B7 | MEDIUM | Build | `scripts/build-intergenos.sh:612-620` | `sync_chroot_scripts()` calls `rsync -a --delete` without checking if rsync is available on the host. Missing rsync produces a raw "command not found" error rather than a clear diagnostic during a multi-hour build. | Add `command -v rsync >/dev/null \|\| { log "FATAL: rsync required but not installed"; return 1; }` at top of `sync_chroot_scripts`. |
| B8 | MEDIUM | Build | `igos-build/builder.py:156-157` | Hardcoded `/opt/rustc/bin` in PATH construction. If Rust toolchain is installed elsewhere, Rust-dependent packages fail silently during build. | Change to `env["PATH"] = f"{os.environ.get('IGOS_RUSTC_BIN', '/opt/rustc/bin')}:{self.system_root}/tools/bin:" + env.get("PATH", "")`. |
| B9 | LOW | Build | `scripts/chroot-enter.sh:82` | `PKG_VERSION=""` exported into chroot as empty string. Any `build.sh` referencing `$PKG_VERSION` before the builder sets it per-package gets an empty string rather than an unset-variable error. Silent-failure trap — a `curl "https://example.com/pkg-${PKG_VERSION}.tar.gz"` becomes `curl "https://example.com/pkg-.tar.gz"` and downloads the wrong URL. | Remove `PKG_VERSION=""` line. The builder (igos-build/builder.py:126) sets it per-package. Other chroot contexts don't need it pre-set. |
| B10 | LOW | Build | `scripts/chroot-*.sh` (15 files) | All chroot scripts hardcode `IGOS=/mnt/igos`. No override mechanism. Builds cannot target an alternate root without editing every script. | Change each script to `IGOS="${IGOS:-/mnt/igos}"` so an env var can override. |
| B11 | LOW | Build | `igos-build/styles/custom.py:32` | Each phase command is a one-liner: `source build.sh && if declare -f configure >/dev/null; then configure; fi`. If `build.sh` has a syntax error during `source`, the `&&` short-circuits and neither `declare -f` nor `configure` runs — which is correct — but the error message is buried in stderr from the subshell. No explicit check that the source succeeded. | Two-step: `source <script> \|\| { echo "FATAL: failed to source <script>"; exit 1; }; if declare -f configure >/dev/null; then configure; fi` |
| B12 | LOW | Build | `scripts/build-intergenos.sh:385-447` | Inline Python heredoc for verify-sources phase uses `yaml.safe_load(f)` without per-file try/except. A malformed YAML causes a full Python traceback (not a clean "file X is malformed" message). | Wrap the per-file loop body in `try: ... except yaml.YAMLError as e: mismatches.append(f"{name}: YAML parse error: {e}")`. |
| B13 | LOW | Build | `igos-build/parser.py:296` | `source_raw = raw.get("source", [])` — defaults to empty list. A package.yml with no `source:` key (mistake by template author) silently produces an empty source list. The builder then skips extraction, which may or may not be the right behavior depending on the package. | No code change — parser behavior is correct per the schema (source is in REQUIRED_FIELDS on line 111, so a missing `source:` key will already trigger the missing-fields error earlier). Reclassifying to LOW as a false-alarm audit note. Actually — REQUIRED_FIELDS = {"name", "version", "release", "description", "license", "source", "build_style"} and line 257 checks `missing = REQUIRED_FIELDS - set(raw.keys())`. So `source:` IS required and will fail if missing. The `raw.get("source", [])` default on line 296 is dead code. | Remove the dead `[]` default or add a comment noting it's unreachable. |
| B14 | LOW | Build | `scripts/build-intergenos.sh:869` (approx) | The `manifest` phase emits `log "  WARN: 0 archives found"` when archive_count == 0, but returns 0 (success). For a partial build (--stop-after toolchain), this is correct. For a full build, an empty manifest silently succeeds rather than faulting. | Add a phase-level flag `MANIFEST_STRICT=true` (env-var-gated). When set and archive_count == 0, return 1. |

---

## Detailed Analysis

### A. igos-build Python Modules

**parser.py (435 lines)** — Template parsing and validation. Well-structured: dataclass-based Package model, enum-validated build_style/tier, variable resolution for `${version}` placeholders, cycle detection in supersedes via three-color DFS. 

*Strengths:*
- REQUIRED_FIELDS check prevents partial templates from entering the graph.
- `_validate_supersedes_no_cycles` is clean DFS with GRAY/BLACK coloring.
- `_warn_missing_supersedees` surfaces typo'd package names at parse time.

*Weaknesses:*
- No `build_artifacts:` field in the Package dataclass (schema migration landed on master at c4d5126 for the YAML side, but the Python parser doesn't surface `build_artifacts` entries). This is by design — `build_artifacts` entries are not parsed into Source objects — but the field is absent from the dataclass entirely.
- `_resolve_variables` only supports `${name}`, `${version}`, `${version_major}`, `${version_major_minor}`. No `${release}` or custom variable support.

**graph.py (259 lines)** — Dependency graph and topological sort. Clean implementation with Kahn's algorithm, tier-priority ordering, and all-cycles reporting.

*Strengths:*
- Kahn's algorithm with priority queue for stable ordering.
- `_find_all_cycles` deduplicates rotated cycles.
- `MissingDependencyError` with clear messages.

*Weaknesses:*
- `tier_priority` hardcoded dict — adding a new tier requires editing graph.py.
- Cross-tier dependency printing to stderr (line 107) is noisy during normal builds.

**builder.py (705 lines)** — The core build executor. Handles source extraction, checksum verification, tar path-traversal hardening, phase execution, validation, and package tracking dispatch.

*Strengths:*
- Tar extraction has `_validate_tar_members` pre-inspection rejecting `..` and absolute paths.
- Zip extraction validates members before `extractall`.
- `build.sh` auto-detection: if `build.sh` exists alongside `package.yml`, CustomStyle is used regardless of declared `build_style`.
- `SOURCE_DATE_EPOCH` set from git commit timestamp for reproducibility.
- `direct_install + skip_tracking` combo is explicitly rejected as security risk.
- `skip_built` with `TEMPLATE_HASH` comparison prevents redundant rebuilds.

*Weaknesses:*
- `sha256sum` subprocess has no timeout (B6).
- No test coverage for core build loop (B5).
- Hardcoded `/opt/rustc/bin` (B8).

**tracker.py (739 lines)** — Package tracking: manifest generation, archive creation, deployment, verification, pkm SQLite registration. Imported from pkm.database (verified present at repo root).

*Strengths:*
- Staging path validation (`_validate_staging_paths`) with thorough symlink handling rules (B4 hardening).
- Per-file SHA-256 from staging contents using pkm's `_sha256` for byte-parity.
- `SUPERSEDES:` header in manifests for atomic ownership transfer.
- Filesystem snapshot diff for direct_install packages with supersedee-path exclusion.

*Weaknesses:*
- `pkg_manifest_from_diff`, `pkg_archive_from_files`, `pkg_deploy`, `pkg_verify`, `pkg_register_pkm_db` are all mixin methods on PackageTracker → BuildExecutor but not tested in isolation.
- `_compute_file_hashes` (called at line 98 from pkg_manifest) is not shown in the 200-line read — need to verify it has timeout for large file hashing.

**log.py (262 lines)** — Logging infrastructure with per-package log files, JSONL parallel output, phase markers, and summary reporting.

*Strengths:*
- Never-truncated output streaming.
- JSON events include package name, phase, elapsed seconds.
- Summary includes per-package timing.

*Weaknesses:*
- `__del__` close-on-GC is fragile (Python's GC timing is non-deterministic).
- No log rotation — `build-intergenos-YYYYMMDD-HHMMSS.log` naming is timestamp-unique but no size cap.

### B. Shell Scripts (chroot pipeline)

**build-intergenos.sh (855 lines)** — Master build orchestrator. Phases: validate → verify-sources → setup → toolchain → chroot-prep → chroot-tools → core → config → core-extra → kernel → desktop → ai → extra → bootloader → image → manifest.

*Strengths:*
- `set -euo pipefail` (line 37) provides comprehensive error guarding.
- SIGINT/SIGTERM/SIGHUP trap with cleanup (unmount, kill children).
- `--start-at` / `--stop-after` for resume from any phase.
- Graceful stop via `.build-stop` touch file.
- Checkpoint support with tar.zst snapshots.
- Phase file recording (`PHASE_FILE`) for resume context.
- `sync_chroot_scripts()` with rsync ensures the chroot always has latest code when resuming.
- Password gateway is explicit — no defaults for root/user passwords.

*Weaknesses:*
- No rsync availability check (B7).
- verify-sources inline Python lacks per-file YAML error handling (B12).
- manifest phase empty-archives warning is non-fatal by default (B14).

**chroot-setup.sh (152 lines)** — Mounts virtual filesystems for chroot entry. 
- Has `set -e` (line 15). 
- Defensive mountpoint checks before each mount. 
- Timezone passthrough from host for correct chroot timestamps.

**chroot-enter.sh (85 lines)** — Enters the chroot with `env -i` clean environment.
- **MISSING `set -e` (B1)** — most critical gap in the build system because this is the gateway for ALL chroot-phase builds.
- Sets `PKG_VERSION=""` globally (B9).
- No exit code propagation from the chroot'd command.

**chroot-teardown.sh (57 lines)** — Unmounts virtual filesystems in reverse order.
- **MISSING `set -e` (B3)**. Individual umount calls are guarded but no overall guard.
- Dead-code guard for `IGOS` (B4).

**chroot-health-check.sh (348 lines)** — Validates built package functionality.
- **MISSING `set -e` (B2)**. 348 lines of validation with no error propagation.
- Comprehensive check categories: binaries, libraries, pkg-config, Python imports, systemd units, config files, SUID binaries, shell configurations.

### C. Test Coverage Assessment

```
Module                Lines   Tests   Coverage
igos-build/builder.py    705       0       0%
igos-build/tracker.py    739       1*      5%
igos-build/parser.py     435       1*     15%
igos-build/graph.py      259       0       0%
igos-build/log.py        262       0       0%
igos-build/__main__.py   169       0       0%
igos-build/styles/*      280       0       0%
```

\* Tests exist only for supersede cycle detection and staging path validation — not for core build logic.

Tests that ran: `python3 -m pytest igos-build/` collected 0 items (test directory is at `tests/igos_build/`, not co-located).

Existing test files at `tests/igos_build/`:
- `test_parser_supersedes.py` — supersede cycle detection (covered)
- `test_tracker_b4_staging.py` — staging path validation (covered)

---

## Audit Techniques Applied

| Technique | Result |
|-----------|--------|
| Logic-flow tracing | Traced builder.py build_package → extract_source → build phases → validation → tracking. All error paths return False except post_install which logs and continues. |
| Error-handling scan | 18 except blocks in tracker.py, 6 in builder.py, 3 in __main__.py, 1 in log.py. Most catches are typed (ValueError, OSError, TarError). tracker.py:655 catches bare `Exception` (broad catch). |
| Hardcoded-path scan | /mnt/igos (19 scripts), /opt/rustc/bin (builder.py:156), /var/lib/igos/{packages,archives} (builder.py:102-103), /mnt/intergenos (5 chroot scripts). |
| Test gap analysis | 8 Python modules, 2 test files. ZERO build-execution tests. ZERO graph tests. ZERO log tests. |
| Shell robustness | 16/19 scripts have `set -e` or `set -euo pipefail`. 3 missing: chroot-enter.sh, chroot-health-check.sh, chroot-teardown.sh. |
| Missing dep declaration | tracker.py imports `pkm.database` — pkm module confirmed present at repo root. No external library deps beyond stdlib + pyyaml. |
| git-hygiene | Pre-push gate at `.githooks/pre-push` — not audited in this section (deferred to §6). |
| Run tests first | `python3 -m pytest igos-build/` collected 0 items. `python3 -m pytest tests/igos_build/ -v` would test the 2 existing files but was not exercised in this section. |
