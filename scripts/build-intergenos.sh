#!/bin/bash
# ==========================================================================
# InterGenOS Master Build Orchestrator
#
# Drives the entire build from fresh VM to bootable disk image.
# One command, clear progress, full control.
#
# Usage:
#   sudo bash build-intergenos.sh --user <username>
#   sudo bash build-intergenos.sh --user <username> --start-at <phase>
#   sudo bash build-intergenos.sh --user <username> --stop-after <phase>
#   sudo bash build-intergenos.sh --user <username> --checkpoint
#
# Phases (in order):
#   validate       — Verify host meets all build requirements
#   verify-sources — Audit all source: SHAs against downloaded tarballs
#   setup          — Create build root, verify sources and patches
#   toolchain    — Cross-compilation toolchain (LFS Chapters 5-6)
#   chroot-prep  — Mount virtual filesystems for chroot (Chapter 7 prep)
#   chroot-tools — Build temporary tools inside chroot (Chapter 7)
#   core         — Build LFS core packages in chroot (Chapter 8)
#   config       — System configuration in chroot (Chapter 9)
#   core-extra   — Build additional core packages in chroot
#   base         — Build base packages in chroot
#   desktop      — Build desktop packages in chroot (GNOME + dependencies)
#   image        — Package chroot into bootable disk image
#
# Controls:
#   --start-at <phase>   Start (or resume) at a specific phase
#   --stop-after <phase> Stop after the named phase completes
#   --checkpoint          Save a tarball after each significant phase
#   touch /mnt/igos/.build-stop   Graceful halt between phases
#   Ctrl+C               Immediate stop (traps SIGINT)
#
# ==========================================================================

set -euo pipefail

# ==========================================================================
# Constants
# ==========================================================================

IGOS=/mnt/igos
IGOS_TARGET=x86_64-igos-linux-gnu
SCRIPTS=/mnt/intergenos/scripts
PACKAGES_DIR=/mnt/intergenos/packages
SOURCES=/mnt/intergenos/build/sources
PATCHES=/mnt/intergenos/build/patches
LOGS=/mnt/intergenos/build/logs
PHASE_FILE="${LOGS}/.build-phase"
STOP_FILE="${IGOS}/.build-stop"
CHECKPOINT_DIR="/mnt/intergenos/checkpoints"
BUILD_LOG="${LOGS}/build-intergenos-$(date '+%Y%m%d-%H%M%S').log"

PHASES=(
    validate
    verify-sources
    setup
    toolchain
    chroot-prep
    chroot-tools
    core
    config
    core-extra
    base
    kernel
    desktop
    ai
    extra
    bootloader
    image
    manifest
    squashfs
    iso
)

# ==========================================================================
# Argument parsing
# ==========================================================================

BUILD_USER=""
START_AT=""
STOP_AFTER=""
CHECKPOINT=false
PUBLISH=false
ROOT_PASSWORD_ARG=""
USER_PASSWORD_ARG=""
ROOT_PASSWORD_PROVIDED=false
USER_PASSWORD_PROVIDED=false
IMAGE_USER_NAME="intergenos"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)
            BUILD_USER="$2"
            shift 2
            ;;
        --start-at)
            START_AT="$2"
            shift 2
            ;;
        --stop-after)
            STOP_AFTER="$2"
            shift 2
            ;;
        --checkpoint)
            CHECKPOINT=true
            shift
            ;;
        --publish)
            PUBLISH=true
            shift
            ;;
        --root-password)
            ROOT_PASSWORD_ARG="$2"
            ROOT_PASSWORD_PROVIDED=true
            shift 2
            ;;
        --user-password)
            USER_PASSWORD_ARG="$2"
            USER_PASSWORD_PROVIDED=true
            shift 2
            ;;
        --image-user)
            IMAGE_USER_NAME="$2"
            shift 2
            ;;
        -h|--help)
            head -30 "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: sudo bash $0 --user <username> --root-password <pw> --user-password <pw> [--image-user <name>] [--start-at <phase>] [--stop-after <phase>]"
            exit 1
            ;;
    esac
done

if [ -z "$BUILD_USER" ]; then
    echo "Error: --user <username> is required"
    echo "Usage: sudo bash $0 --user <username> --root-password <pw> --user-password <pw> [--image-user <name>] [--start-at <phase>] [--stop-after <phase>]"
    exit 1
fi

# Path 4: image credentials must never be a guessable shared default. The
# literal "intergenos" default has been retired (S1/S2 design decision A
# 2026-04-29). Path 3's first-boot greeter overwrites these on first boot
# anyway, so the build-time values are a brief-window fallback that nobody
# normally encounters.
#
# Resolution policy (2026-05-10):
#   - If --root-password / --user-password are explicitly provided, use them.
#   - If omitted, the orchestrator generates strong randoms internally via
#     `openssl rand` (universally available, unlike pwgen). This eliminates the
#     human-error class in the kickoff line (weak typed values, shell history
#     leakage, copy-paste mistakes) and matches how mature distros' image-build
#     pipelines produce live-build credentials.
#   - Generated values are surfaced in the build log so an operator can recover
#     them during the brief pre-first-boot window if needed.
#   - Empty-string explicit values (--root-password '') remain rejected — that's
#     a kickoff bug, not an autogen request.
generate_password() {
    # 24-char URL-safe random; strips /+= so password-policy and shell
    # interpolation can't trip on the value.
    openssl rand -base64 24 2>/dev/null | tr -d '/+=' | head -c 24
}
if ! $ROOT_PASSWORD_PROVIDED; then
    ROOT_PASSWORD_ARG=$(generate_password)
    ROOT_PASSWORD_AUTOGEN=true
elif [ -z "$ROOT_PASSWORD_ARG" ]; then
    echo "Error: --root-password '' (empty) rejected. Omit the flag to autogenerate"
    echo "       or pass a non-empty value."
    exit 1
fi
if ! $USER_PASSWORD_PROVIDED; then
    USER_PASSWORD_ARG=$(generate_password)
    USER_PASSWORD_AUTOGEN=true
elif [ -z "$USER_PASSWORD_ARG" ]; then
    echo "Error: --user-password '' (empty) rejected. Omit the flag to autogenerate"
    echo "       or pass a non-empty value."
    exit 1
fi
export ROOT_PASSWORD="$ROOT_PASSWORD_ARG"
export IMAGE_USER_PASSWORD="$USER_PASSWORD_ARG"
export IMAGE_USER="$IMAGE_USER_NAME"

if [ "${ROOT_PASSWORD_AUTOGEN:-false}" = "true" ] || [ "${USER_PASSWORD_AUTOGEN:-false}" = "true" ]; then
    # Surface generated creds before phase_validate so they're recoverable
    # from the build log if needed during the pre-first-boot window. Both
    # passwords are overwritten on first boot by the greeter (Path 3).
    echo "================================================================"
    echo "  AUTOGENERATED IMAGE CREDENTIALS (first-boot greeter overwrites)"
    echo "================================================================"
    if [ "${ROOT_PASSWORD_AUTOGEN:-false}" = "true" ]; then
        echo "  root password:           $ROOT_PASSWORD"
    fi
    if [ "${USER_PASSWORD_AUTOGEN:-false}" = "true" ]; then
        echo "  $IMAGE_USER user password: $IMAGE_USER_PASSWORD"
    fi
    echo "================================================================"
fi

# Verify running as root (needed for chroot phases)
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: must run as root (use sudo)"
    exit 1
fi

# Verify build user exists
if ! id "$BUILD_USER" > /dev/null 2>&1; then
    echo "Error: user '$BUILD_USER' does not exist"
    exit 1
fi

# Validate --start-at and --stop-after are real phase names
validate_phase_name() {
    local name="$1"
    local label="$2"
    if [ -n "$name" ]; then
        local found=false
        for p in "${PHASES[@]}"; do
            if [ "$p" = "$name" ]; then
                found=true
                break
            fi
        done
        if ! $found; then
            echo "Error: unknown phase '$name' for $label"
            echo "Valid phases: ${PHASES[*]}"
            exit 1
        fi
    fi
}

validate_phase_name "$START_AT" "--start-at"
validate_phase_name "$STOP_AFTER" "--stop-after"

# Conditionally enable publish phase
if $PUBLISH; then
    PHASES+=(publish)
fi

# ==========================================================================
# Logging
# ==========================================================================

mkdir -p "$LOGS"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$BUILD_LOG"
}

# ==========================================================================
# Checkpoint support
# ==========================================================================

save_checkpoint() {
    local phase="$1"
    local checkpoint="${CHECKPOINT_DIR}/intergenos-${phase}-$(date '+%Y%m%d-%H%M%S').tar.zst"

    log ""
    log ">>> Saving checkpoint: $checkpoint"

    mkdir -p "${CHECKPOINT_DIR}"

    # Remove any checkpoint tarballs that landed inside the chroot
    # (from previous runs with old CHECKPOINT_DIR) so they don't compound
    rm -f "${IGOS}/home/${BUILD_USER}"/intergenos-*.tar.gz 2>/dev/null || true
    rm -f "${IGOS}/home/${BUILD_USER}"/intergenos-*.tar.zst 2>/dev/null || true

    # Tear down chroot mounts temporarily for a clean snapshot
    bash "${SCRIPTS}/chroot-teardown.sh" > /dev/null 2>&1 || true

    local start_time=$(date +%s)
    tar -C "$IGOS" --one-file-system --zstd -cf "$checkpoint" . 2>&1

    local elapsed=$(( $(date +%s) - start_time ))
    local size=$(du -h "$checkpoint" | cut -f1)

    log ">>> Checkpoint saved: $size in ${elapsed}s"
    log ">>> Restore with: rm -rf ${IGOS}/* && tar -C ${IGOS} --zstd -xf ${checkpoint}"

    # Re-mount chroot filesystems
    bash "${SCRIPTS}/chroot-setup.sh" > /dev/null 2>&1 || true
}

# ==========================================================================
# Signal handling
# ==========================================================================

CURRENT_PHASE=""

cleanup() {
    log ""
    log "!!! Build interrupted during phase: ${CURRENT_PHASE:-none}"
    log "!!! Cleaning up..."

    # Tear down chroot mounts to prevent host filesystem corruption
    if [ -f "${SCRIPTS}/chroot-teardown.sh" ]; then
        bash "${SCRIPTS}/chroot-teardown.sh" >/dev/null 2>&1 || true
    fi

    # Kill any child processes spawned by this build
    pkill -P $$ 2>/dev/null || true

    log "!!! Resume with: sudo bash $0 --user $BUILD_USER --start-at ${CURRENT_PHASE:-validate}"
    log ""
    exit 130
}

trap cleanup SIGINT SIGTERM SIGHUP

# ==========================================================================
# Phase runner
# ==========================================================================

SKIPPING=true
if [ -z "$START_AT" ]; then
    SKIPPING=false
fi

run_phase() {
    local phase="$1"
    local description="$2"
    shift 2
    # remaining args are the function/command to run

    # Handle --start-at
    if $SKIPPING; then
        if [ "$phase" = "$START_AT" ]; then
            SKIPPING=false
            # Refresh ld.so.cache in the chroot at the resume entry
            # point. /usr/lib64 libs installed by earlier resumes
            # (meson defaults to lib64 on x86_64; cache built once at
            # chroot-prep only knows /usr/lib) would otherwise be
            # invisible to any check() phase running runtime tests.
            # Caused 2026-05-07 sratom halt #13.
            if [ -x /mnt/igos/sbin/ldconfig ]; then
                log "[INFO ] Refreshing chroot ld.so.cache at --start-at $phase"
                chroot /mnt/igos /sbin/ldconfig 2>/dev/null || true
            fi
            # Source-staging sweep at resume entry. phase_setup was skipped
            # by --start-at, so packages added to master since the last full
            # phase_setup may have unfetched-or-unchroot-staged source
            # tarballs. ensure_sources_staged() backfills both. Scoped to
            # current + downstream tiers via tiers_for_start_at(). Halts
            # loudly on download failure (set -e propagates). Captured
            # 2026-05-12 after Build #9 r#21 halted at jemalloc 5.3.1.
            ensure_sources_staged
        else
            log "[SKIP ] $phase — $description"
            return 0
        fi
    fi

    # Check for graceful stop request
    if [ -f "$STOP_FILE" ]; then
        rm -f "$STOP_FILE"
        log ""
        log ">>> Stop requested (found $STOP_FILE)"
        log ">>> Stopped before phase: $phase"
        log ">>> Resume with: sudo bash $0 --user $BUILD_USER --start-at $phase"
        log ""
        exit 0
    fi

    CURRENT_PHASE="$phase"
    local start_time=$(date +%s)

    log ""
    log "================================================================"
    log "  PHASE: $phase — $description"
    log "  Started: $(date)"
    log "================================================================"
    log ""

    # Record current phase
    echo "$phase" > "$PHASE_FILE"

    # Run the phase
    "$@"
    local rc=$?

    local elapsed=$(( $(date +%s) - start_time ))
    local minutes=$(( elapsed / 60 ))
    local seconds=$(( elapsed % 60 ))

    if [ $rc -ne 0 ]; then
        log ""
        log "!!! PHASE FAILED: $phase ($description)"
        log "!!! Exit code: $rc"
        log "!!! Elapsed: ${minutes}m ${seconds}s"
        log "!!! Resume with: sudo bash $0 --user $BUILD_USER --start-at $phase"
        log ""
        exit $rc
    fi

    log ""
    log "[DONE ] $phase — ${minutes}m ${seconds}s"

    # Save checkpoint after significant phases
    if $CHECKPOINT; then
        case "$phase" in
            toolchain|core|kernel|desktop|ai)
                save_checkpoint "$phase"
                ;;
        esac
    fi

    # Handle --stop-after
    if [ "$phase" = "$STOP_AFTER" ]; then
        log ""
        log ">>> Stopping after phase: $phase (--stop-after)"
        local next_idx=0
        for i in "${!PHASES[@]}"; do
            if [ "${PHASES[$i]}" = "$phase" ]; then
                next_idx=$((i + 1))
                break
            fi
        done
        if [ $next_idx -lt ${#PHASES[@]} ]; then
            log ">>> Resume with: sudo bash $0 --user $BUILD_USER --start-at ${PHASES[$next_idx]}"
        fi
        log ""
        exit 0
    fi
}

# ==========================================================================
# Phase implementations
# ==========================================================================

phase_validate() {
    # LFS 13.0 requires /bin/sh -> bash (Ubuntu defaults to dash)
    if [ "$(readlink -f /bin/sh)" != "/usr/bin/bash" ]; then
        log "  /bin/sh does not point to bash — fixing..."
        ln -sf /usr/bin/bash /bin/sh
        log "  /bin/sh -> bash"
    fi

    log "Running host requirements check..."
    python3 "${SCRIPTS}/host-check.py"

    # Build Development Rulebook Rule 17: pre-flight tier-coverage check.
    # Halts the build if any tier-declared package is unreachable from its
    # phase's build invocation. This is the mechanical guard against the
    # silent-skip class of failures (Build #6 found 6 such orphans).
    log "Running pre-flight tier-coverage check (Rulebook Rule 17)..."
    python3 "${SCRIPTS}/preflight-tier-coverage.py"

    # Reproducibility gate (2026-05-11): every in-scope package must have a
    # current, reconciled audit record in build/blfs-packages.db's
    # package_audit table. The audit captures build-system, declared deps,
    # configure flags, bundled libs, install output, and reproducibility
    # primitives — gating the build on it ensures we never re-introduce the
    # "we never looked at it first" failure class.
    log "Running audit-coverage check (reproducibility gate)..."
    python3 "${SCRIPTS}/preflight-audit-coverage.py"

    # Rule 1 + cross-tier dependency check via the canonical-tier validator.
    log "Running tier-validator (Rule 1 + cross-tier-dep audit)..."
    python3 "${SCRIPTS}/validate-package-tiers.py" || {
        # Validator returns 1 if any package has a MOVE/UNCLEAR/CROSS-TIER-DEP
        # verdict. The known glib2 ↔ gobject-introspection false positive
        # (handled by glib2-bootstrap precedent) is the only acceptable
        # non-zero outcome; surface it for review without halting.
        if [ "$(python3 ${SCRIPTS}/validate-package-tiers.py 2>&1 | grep -c 'MOVE\|UNCLEAR')" -gt 0 ]; then
            log "ERROR: validator found tier violations requiring correction"
            return 1
        fi
        log "  validator: only known glib2-bootstrap false positive remains (acceptable)"
    }

    # Build-order ordering gate (Scan A): for every run_package "consumer" line,
    # verify every declared dependencies.build entry is built EARLIER in the
    # same phase OR in a strictly earlier phase. Catches the class of bug
    # that halted Build #8 at mitkrb/libgcrypt + rpm/libgcrypt before they
    # were caught + closed at master 55b4da4. Pure pre-build static analysis
    # against the repo source tree; no chroot dependency.
    log "Running preflight-build-order scan (Scan A — ordering violations)..."
    python3 "${SCRIPTS}/preflight-build-order.py"

    # Silent-feature-loss gate (Scan B): for every package installed in the
    # prior-build chroot, cross-reference declared deps + BLFS-truth deps
    # against the configure log to surface declared-but-undetected and
    # undeclared-required-but-attempted patterns. Canonical case is the
    # Build #8 systemd-without-15-security-deps finding (master 55b4da4).
    # SKIPS cleanly when chroot data is absent (first-build / post-revert)
    # so this gate doesn't block bootstrap scenarios — it only catches
    # regressions against post-install state from a previous run.
    log "Running preflight-silent-loss scan (Scan B — silent feature loss)..."
    python3 "${SCRIPTS}/preflight-silent-loss.py"

    # Undeclared-build-dep gate (Scan A.2): for every package's source[0]
    # tarball, extract build-system files (configure.ac / meson.build /
    # CMakeLists.txt) and parse for the 5 dep-discovery patterns
    # (PKG_CHECK_MODULES, AC_CHECK_LIB, AC_CHECK_HEADERS, meson
    # dependency() / find_program(), cmake find_package(REQUIRED)).
    # Cross-reference against declared dependencies.build and emit HARD
    # findings for upstream-required deps that aren't declared. Catches
    # the class of bug that halted Build #8 at linux-pam (undeclared
    # docbook → meson xmllint check) and Build #9 at rpm 4.18.2
    # (undeclared lua → PKG_CHECK_MODULES). Conditional-context tracking
    # (shell if/fi, meson if/endif, AS_IF/AS_CASE) reduces false-positive
    # rate; comment-stripping prevents matches inside `#` and `dnl`
    # comments; build-system filter reads consumer build.sh and only
    # scans the buildfiles we actually invoke.
    #
    # First run extracts source tarballs into <repo>/build/scan-cache/
    # (~12 min). Subsequent runs hit the cache and complete in ~10s.
    log "Running preflight-undeclared-deps scan (Scan A.2 — undeclared build deps)..."
    python3 "${SCRIPTS}/preflight-undeclared-deps.py"
}

phase_verify_sources() {
    # Anti-supply-chain gate (design doc §5.1).
    # Audit every package.yml source: AND patches: entry with a sha256 against
    # the artifact on disk. Missing sha256 or mismatch = HARD FAIL.
    # build_artifacts: entries are NOT checked here — those are
    # audited at the manifest phase (IGOSC Step 4).

    # Stage locally-vendored sources first. Packages whose source: is an
    # in-tree directory snapshot (currently: forge, which packages the
    # `installer/` tree + `man/forge.1`) need their tarball regenerated
    # to reflect the current on-disk content before SHA verification runs,
    # otherwise edits to installer/* are silently shadowed by the stale
    # snapshot. Same shape as chroot-rsync-coverage-gap.
    log "Staging locally-vendored sources (forge tarball)..."
    bash "$SCRIPTS/build-forge-tarball.sh"

    log "Verifying pinned source + patch SHAs against on-disk artifacts..."

    local PYSCRIPT PYEXIT UNPINNED MISMATCHES

    PYSCRIPT=$(python3 - "$PACKAGES_DIR" "$SOURCES" "$PATCHES" <<'PYEOF'
import sys, hashlib, os, re
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FATAL: pyyaml required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

# Mirror igos_build.parser._resolve_variables: package.yml URLs and filenames
# carry ${name}/${version}/${version_major}/${version_major_minor}/${version_patch}
# placeholders that the build pipeline expands. verify-sources reads the YAML
# directly, so it must perform the same substitution before checking tarballs.
# If this set drifts from parser.py, audit both consumers.
_VAR_RE = re.compile(r"\$\{(\w+)\}")
def _resolve(text, variables):
    return _VAR_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), text)

packages_dir = Path(sys.argv[1])
sources_dir = Path(sys.argv[2])
patches_dir = Path(sys.argv[3])

unpinned = []
mismatches = []
build_artifacts_count = 0
patches_checked = 0

for yml_path in sorted(packages_dir.rglob("package.yml")):
    # Per §1 B12: per-file YAML error handling. A malformed YAML file
    # used to produce a raw Python traceback that obscured which file
    # was bad. Catch + tag the file path so the operator can fix one
    # at a time instead of replaying tracebacks.
    try:
        with yml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        mismatches.append(f"{yml_path.relative_to(packages_dir)}: YAML parse error: {e}")
        continue

    if data is None:
        continue

    name = data.get("name", yml_path.parent.name)
    version = str(data.get("version", ""))
    version_parts = version.split(".")
    variables = {
        "name": name,
        "version": version,
        "version_major": version_parts[0] if version_parts else "",
        "version_major_minor": ".".join(version_parts[:2]) if len(version_parts) >= 2 else version,
        "version_patch": version_parts[2] if len(version_parts) >= 3 else "0",
    }
    src = data.get("source")
    build = data.get("build_artifacts", [])
    build_artifacts_count += len(build) if isinstance(build, list) else 0

    if not src or not isinstance(src, list):
        continue

    for i, item in enumerate(src):
        if not isinstance(item, dict):
            unpinned.append(f"{name}: source[{i}] malformed")
            continue
        url = _resolve(item.get("url", ""), variables)
        sha = item.get("sha256")
        if not sha or not isinstance(sha, str) or len(sha) != 64:
            unpinned.append(f"{name}: {url} (no sha256 or invalid)")
            continue

        filename_raw = item.get("filename")
        if filename_raw:
            filename = _resolve(filename_raw, variables)
        else:
            filename = url.rsplit("/", 1)[-1].split("?")[0]
        tarball = sources_dir / filename
        if not tarball.exists():
            mismatches.append(f"{name}: {filename} (not downloaded)")
            continue

        actual = hashlib.sha256(tarball.read_bytes()).hexdigest()
        if actual != sha:
            mismatches.append(f"{name}: {filename} — expected={sha[:12]}... actual={actual[:12]}...")

    # Verify declared patches. The chroot's /sources/ contains both ${PATCHES}/*
    # and ${SOURCES}/* (build-intergenos.sh phase_setup copies both). Check
    # patches_dir first, fall back to sources_dir. A patch with no sha256, no
    # file on disk, or content mismatch is a HARD FAIL — this is the same
    # supply-chain gate that protects sources, extended to declared patches.
    patches = data.get("patches") or []
    if isinstance(patches, list):
        for j, pitem in enumerate(patches):
            if not isinstance(pitem, dict):
                unpinned.append(f"{name}: patches[{j}] malformed")
                continue
            pfile_raw = pitem.get("file")
            psha = pitem.get("sha256")
            if not pfile_raw:
                unpinned.append(f"{name}: patches[{j}] missing 'file'")
                continue
            pfile = _resolve(pfile_raw, variables)
            if not psha or not isinstance(psha, str) or len(psha) != 64:
                unpinned.append(f"{name}: patch {pfile} (no sha256 or invalid)")
                continue
            ppath = patches_dir / pfile
            if not ppath.exists():
                ppath = sources_dir / pfile
            if not ppath.exists():
                mismatches.append(f"{name}: patch {pfile} (not found in patches/ or sources/)")
                continue
            pactual = hashlib.sha256(ppath.read_bytes()).hexdigest()
            if pactual != psha:
                mismatches.append(f"{name}: patch {pfile} — expected={psha[:12]}... actual={pactual[:12]}...")
            patches_checked += 1

if unpinned:
    print("UNPINNED:", file=sys.stderr)
    for e in unpinned:
        print(f"  {e}", file=sys.stderr)
if mismatches:
    print("MISMATCHES:", file=sys.stderr)
    for e in mismatches:
        print(f"  {e}", file=sys.stderr)

if unpinned or mismatches:
    sys.exit(1)

print(f"OK: {build_artifacts_count} build_artifacts skipped, {patches_checked} patches verified, 0 source/patch SHAs un-pinned, 0 mismatches")
PYEOF
)
    PYEXIT=$?

    if [ "$PYEXIT" -ne 0 ]; then
        log "ERROR: verify-sources FAILED. Fix the package.yml files or re-download"
        log "  the matching upstream tarballs before retrying the build."
        return "$PYEXIT"
    fi

    log "verify-sources: all source + patch SHAs verified"
}

# ==========================================================================
# Source-staging — idempotent helper used by phase_setup (the full-build path)
# AND by run_phase at the --start-at resume entry point. Closes the source-
# stage gap where a resume at --start-at <phase> would skip phase_setup and
# leave the chroot's /sources/ stale vs packages added to master since the
# last full phase_setup run.
#
# Captured 2026-05-12 after Build #9 r#21 halted at jemalloc 5.3.1 (extra
# tier, --start-at extra). Today's Wave 1b prereq landings (jemalloc,
# snappy, gflags, liburing, valkey, memcached, etcd, leveldb, rocksdb,
# scons) were all on master but their upstream tarballs had never been
# fetched to the host nor copied into the chroot, because phase_setup
# never ran on this resume.
#
# Historical pattern (POWER memory `feedback_source_stage_gap_on_start_at`)
# called for a manual `sudo cp` workaround per resume. This replaces that
# pattern with always-on automation.
# ==========================================================================

tiers_for_start_at() {
    # Echo the --tier flag set that download-sources.py needs, based on the
    # current --start-at value. Walks forward only — backtracking to fetch
    # sources for tiers that already completed is wasteful (their packages
    # are already in chroot + pkm-tracked).
    case "$START_AT" in
        ""|validate|verify-sources|setup)
            echo "--all" ;;
        toolchain|chroot-prep|chroot-tools|core|config|core-extra|base|kernel)
            echo "--tier core --tier base --tier desktop --tier ai --tier extra" ;;
        desktop)
            echo "--tier desktop --tier ai --tier extra" ;;
        ai)
            echo "--tier ai --tier extra" ;;
        extra|bootloader|image|manifest|publish)
            echo "--tier extra" ;;
        *)
            echo "--all" ;;
    esac
}

ensure_sources_staged() {
    # Idempotent: download missing tarballs to host, mirror host -> chroot.
    # Halts on download failure (set -euo pipefail in effect — non-zero
    # return propagates).
    #
    # Cheap when nothing's missing: download-sources.py is stat-only, cp -an
    # is no-op on already-present files. Only costs when there's real work.
    #
    # Tiers scoped to --start-at via tiers_for_start_at(): only fetch sources
    # for current + downstream tiers, not the entire tree.
    local tier_flags
    read -ra tier_flags <<< "$(tiers_for_start_at)"

    log "  Source-staging sweep (start-at=${START_AT:-<full-build>}, flags: ${tier_flags[*]})..."

    # Step 1: fetch any missing tarballs on the host. download-sources.py is
    # idempotent (only downloads what isn't cached + sha256-verifies what is)
    # so a corrupted prior fetch surfaces as a verify failure here.
    if ! python3 "${SCRIPTS}/download-sources.py" "${tier_flags[@]}" 2>&1 | tee -a "$BUILD_LOG"; then
        log "ERROR: download-sources.py failed — halting before chroot-stage"
        return 1
    fi

    # Step 2: mirror host /mnt/intergenos/build/sources/ -> chroot
    # /mnt/igos/sources/. rsync -a = archive + diff-aware: skips files
    # that are byte-identical on both sides (cheap for unchanged tarballs),
    # mirrors changes when host file differs from chroot file (size/mtime
    # delta detection). Replaces the prior cp -an (no-clobber) which only
    # mirrored NEW filenames — silently preserved stale chroot copies when
    # a vendor tarball was regenerated without a version bump (i.e.
    # same filename, different content). First case hit was influxdb
    # 3.9.0's vendor regen for the cargo-vendor-gen git-source config fix.
    mkdir -p "$IGOS/sources"
    chmod a+wt "$IGOS/sources"
    rsync -a "${SOURCES}/" "$IGOS/sources/" 2>/dev/null || true
    rsync -a "${PATCHES}/" "$IGOS/sources/" 2>/dev/null || true

    local count=$(ls "$IGOS/sources" 2>/dev/null | wc -l)
    log "  Sources staged: $count files in $IGOS/sources/"
}

phase_setup() {
    # Create build root
    if [ ! -d "$IGOS" ]; then
        mkdir -p "$IGOS"
    fi
    chown "${BUILD_USER}:${BUILD_USER}" "$IGOS"
    chmod 755 "$IGOS"
    log "  /mnt/igos owned by $BUILD_USER"

    # Create LFS directory layout (Section 4.2)
    # These directories and symlinks must exist before the toolchain build
    mkdir -pv "$IGOS"/{etc,var} "$IGOS"/usr/{bin,lib,sbin}
    for i in bin lib sbin; do
        if [ ! -L "$IGOS/$i" ]; then
            ln -sv "usr/$i" "$IGOS/$i"
        fi
    done
    case $(uname -m) in
        x86_64) mkdir -pv "$IGOS/lib64" ;;
    esac
    # Tools directory for cross-toolchain
    mkdir -pv "$IGOS/tools"
    chown -R "${BUILD_USER}:${BUILD_USER}" "$IGOS"
    log "  LFS directory layout created (Section 4.2)"

    # Verify virtiofs
    if ! mount | grep -q "intergenos.*virtiofs"; then
        log "ERROR: /mnt/intergenos not mounted via virtiofs"
        return 1
    fi
    log "  virtiofs mount OK"

    # Verify critical sources exist
    local missing=0
    for src in binutils-2.46.0.tar.xz gcc-15.2.0.tar.xz glibc-2.43.tar.xz \
               linux-6.18.10.tar.xz gmp-6.3.0.tar.xz mpfr-4.2.2.tar.xz mpc-1.3.1.tar.gz; do
        if [ ! -f "${SOURCES}/$src" ]; then
            log "  MISSING: $src"
            missing=$((missing + 1))
        fi
    done
    if [ $missing -gt 0 ]; then
        log "ERROR: $missing critical source tarballs missing from $SOURCES"
        return 1
    fi

    local total=$(ls "$SOURCES" | wc -l)
    log "  Sources: $total tarballs on host"

    # Verify patches
    if [ ! -f "${PATCHES}/glibc-fhs-1.patch" ]; then
        log "ERROR: glibc-fhs-1.patch missing from $PATCHES"
        return 1
    fi
    log "  Patches: OK"

    # --- Place everything directly on the target filesystem ---
    # Like build_003: no bind mounts, no tricks. The chroot is self-contained.
    # Everything the chroot needs is physically present on $IGOS.

    # Stage source tarballs + patches into the chroot. Delegates to the
    # shared ensure_sources_staged() helper so the same logic runs on
    # --start-at resumes (wired in run_phase at the resume entry point).
    # ensure_sources_staged() also runs download-sources.py first to
    # backfill any missing tarballs on the host.
    ensure_sources_staged

    # Copy build infrastructure (scripts, packages, igos-build)
    # Preserves paths so /mnt/intergenos/scripts/... works inside the chroot
    log "  Copying build infrastructure to $IGOS/mnt/intergenos/..."
    mkdir -pv "$IGOS/mnt/intergenos"
    cp -a /mnt/intergenos/scripts    "$IGOS/mnt/intergenos/"
    cp -a /mnt/intergenos/packages   "$IGOS/mnt/intergenos/"
    cp -a /mnt/intergenos/igos-build "$IGOS/mnt/intergenos/"
    # pkm is a runtime dependency of igos-build/tracker.py (per RFC v1
    # 2026-05-01: tracker imports pkm.database._sha256 for tracker/verifier
    # parity). Without this sync, desktop-phase Python orchestrator fails
    # with ModuleNotFoundError on import.
    cp -a /mnt/intergenos/pkm        "$IGOS/mnt/intergenos/"
    # intergen source must be copied into the chroot for phase_ai. The
    # ai-tier build.sh references /mnt/intergenos/intergen/*.py and the
    # subdirs (interfaces/, tools/, tests/). Without this copy, phase_ai
    # halts at intergen with `cp: cannot stat ...`. Build #6 Halt at
    # intergen 2026-05-09 surfaced the omission.
    cp -a /mnt/intergenos/intergen   "$IGOS/mnt/intergenos/"
    cp    /mnt/intergenos/igos-build.py "$IGOS/mnt/intergenos/" 2>/dev/null || true
    log "  Build infrastructure placed on target filesystem"

    chown -R "${BUILD_USER}:${BUILD_USER}" "$IGOS"
    log "  Build root: $IGOS ready (self-contained)"
}

phase_toolchain() {
    # Toolchain must run as the build user, NOT root
    # env -i wipes ALL host variables (LFS 13.0 Section 4.4 requirement)
    # Only HOME, TERM, and PATH survive — prevents host CFLAGS, LD_LIBRARY_PATH, etc.
    # from contaminating the cross-compilation
    log "Running cross-toolchain build as $BUILD_USER (Ch 5)..."
    # ${TERM@Q} (bash 4.4+) literal-quotes the value so any command-substitution
    # syntax inside $TERM does not re-evaluate when su's shell parses the -c arg.
    su - "$BUILD_USER" -c "env -i HOME=/home/${BUILD_USER} TERM=${TERM@Q} bash ${SCRIPTS}/toolchain-build.sh" 2>&1 | tee -a "$BUILD_LOG"
    # Check if toolchain produced the expected output
    if [ ! -x "${IGOS}/tools/bin/${IGOS_TARGET}-gcc" ]; then
        log "ERROR: Toolchain build did not produce ${IGOS_TARGET}-gcc"
        return 1
    fi
    log "  Cross-toolchain verified: ${IGOS_TARGET}-gcc exists"

    # Temp tools (Ch 6) — cross-compiled utilities needed inside the chroot
    log "Running temp-tools build as $BUILD_USER (Ch 6)..."
    su - "$BUILD_USER" -c "env -i HOME=/home/${BUILD_USER} TERM=${TERM@Q} bash ${SCRIPTS}/temp-tools-build.sh" 2>&1 | tee -a "$BUILD_LOG"
    # Verify coreutils installed (env is needed for chroot entry)
    if [ ! -x "${IGOS}/usr/bin/env" ]; then
        log "ERROR: Temp-tools build did not produce /usr/bin/env (coreutils)"
        return 1
    fi
    log "  Temp-tools verified: /usr/bin/env exists"
}

phase_chroot_prep() {
    log "Setting up chroot environment..."
    bash "${SCRIPTS}/chroot-setup.sh" 2>&1 | tee -a "$BUILD_LOG"

    # Verify mounts
    if ! mountpoint -q "${IGOS}/dev"; then
        log "ERROR: ${IGOS}/dev not mounted"
        return 1
    fi
    log "  Chroot mounts verified"
}

phase_chroot_tools() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    log "Building temporary tools in chroot..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build.sh" 2>&1 | tee -a "$BUILD_LOG"
}

sync_chroot_scripts() {
    command -v rsync >/dev/null || { log "FATAL: rsync required but not installed"; return 1; }

    # Ensure chroot virtual filesystems are mounted.
    # When using --start-at to resume from a later phase, the chroot-prep
    # phase (which normally mounts these) is skipped. Without mounts,
    # chroot-enter.sh refuses to enter.
    if ! mountpoint -q "${IGOS}/dev" 2>/dev/null; then
        log "  Chroot not mounted — running chroot-setup.sh..."
        bash "${SCRIPTS}/chroot-setup.sh" 2>&1 | tee -a "$BUILD_LOG"
    fi

    # Sync scripts and packages into the chroot copy.
    # The setup phase copies build infrastructure to $IGOS/mnt/intergenos/,
    # but --start-at skips setup and code changes between restarts aren't
    # reflected. This ensures the chroot always has the latest.
    log "  Syncing scripts into chroot..."
    rsync -a --delete /mnt/intergenos/scripts/   "$IGOS/mnt/intergenos/scripts/"
    rsync -a --delete /mnt/intergenos/packages/  "$IGOS/mnt/intergenos/packages/"
    rsync -a --delete /mnt/intergenos/config/    "$IGOS/mnt/intergenos/config/" 2>/dev/null || true
    rsync -a --delete /mnt/intergenos/installer/ "$IGOS/mnt/intergenos/installer/" 2>/dev/null || true
    # Sync Python builder for desktop tier (igos-build + its pkm dependency
    # per RFC v1 tracker/verifier parity)
    rsync -a /mnt/intergenos/igos-build.py "$IGOS/mnt/intergenos/" 2>/dev/null || true
    rsync -a --delete /mnt/intergenos/igos-build/   "$IGOS/mnt/intergenos/igos-build/" 2>/dev/null || true
    rsync -a --delete /mnt/intergenos/pkm/          "$IGOS/mnt/intergenos/pkm/"        2>/dev/null || true
    # intergen source for phase_ai (parity with phase_setup copy above)
    rsync -a --delete /mnt/intergenos/intergen/     "$IGOS/mnt/intergenos/intergen/"   2>/dev/null || true
}

phase_core() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building core system in chroot (Ch 8, LFS order)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-ch8.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_config() {
    # Clear IGOS_START_AT / IGOS_STOP_AFTER so per-package resume
    # context from one phase doesn't leak into subsequent phases
    # (config, core-extra, kernel).
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Configuring system in chroot (Ch 9)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-config-ch9.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_core_extra() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building additional core packages in chroot (BLFS)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-core-extra.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_base() {
    # Build the base tier — end-user CLI tools (htop, rsync, strace, screen,
    # etc.) that aren't core build dependencies but are expected on every
    # InterGenOS install. The base orchestration was dormant from 2026-04-04
    # (commit 45421d7 unified into chroot-build-tier.sh, then 66ef3da
    # restored chroot-build-base.sh from archive but never re-wired it into
    # build-intergenos.sh). 2026-05-09 stub-audit follow-up surfaced the
    # gap — without this phase, 16 base-tier packages were silently skipped
    # at install-time, degrading the user-facing CLI surface against the
    # Prime Directive.
    #
    # 2026-05-13 Build #9 audit: phase_base re-wired but ran 0-second because
    # an ambient IGOS_START_AT in the operator shell leaked through into
    # chroot-build-base.sh — every run_package returned 0 via the SKIP
    # logic without building anything. Result: 17 of 19 base packages
    # missing in chroot, surfaced 18h later when libreoffice configure
    # couldn't find /usr/bin/zip. See clear_per_pkg_resume_env() — unset
    # before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building base packages in chroot (end-user CLI tools)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-base.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_kernel() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building kernel in chroot (Ch 10)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-ch10.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_desktop() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building desktop packages in chroot (GNOME + dependencies)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-desktop.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_ai() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building AI tier packages in chroot (InterGen assistant)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-ai.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_extra() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building extra tier packages in chroot (user applications)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-extra.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_bootloader() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts

    # B-003 (T0-2 2026-05-18): wipe stale .signed before rebuilding. The
    # signing-ceremony output writes .signed files alongside the unsigned
    # .efi; on a fresh phase_bootloader run those .signed reflect a prior
    # cycle's artifacts. Re-signing a fresh build against stale .signed
    # in the directory would silently mix lineages — the cycle-5 manifest
    # vs ESP-content mismatch class. Always start a bootloader rebuild
    # from a clean directory.
    local host_bootloader_dir="/mnt/intergenos/build/bootloader"
    if [ -d "$host_bootloader_dir" ]; then
        local stale_signed=()
        while IFS= read -r -d '' s; do
            stale_signed+=( "$s" )
        done < <(find "$host_bootloader_dir" -maxdepth 1 -name '*.efi.signed' -print0 2>/dev/null)
        if (( ${#stale_signed[@]} > 0 )); then
            log "  B-003: wiping ${#stale_signed[@]} stale .signed artifact(s) before rebuild"
            rm -f "${stale_signed[@]}"
        fi
    fi

    log "Assembling unsigned bootloader artifacts in chroot..."
    log "  (grubx64.efi + initramfs.cpio.gz + igos-live.efi UKI)"
    log ""
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-bootloader.sh" 2>&1 | tee -a "$BUILD_LOG"
    log ""
    log "  Bootloader artifacts at: ${IGOS}/mnt/intergenos/build/bootloader/"

    # Copy bootloader artifacts from chroot to host-visible build dir so
    # phase_iso (and operator ceremony scripts at /mnt/intergenos/build/
    # bootloader/) can access them after phase_image cleans the chroot.
    # The chroot is a self-contained copy of the target filesystem (no
    # bind mount), so the copy is mandatory.
    local chroot_bootloader_dir="${IGOS}/mnt/intergenos/build/bootloader"
    mkdir -p "$host_bootloader_dir"
    if [ -d "$chroot_bootloader_dir" ]; then
        cp -av "$chroot_bootloader_dir"/*.efi "$host_bootloader_dir/" 2>&1 | tee -a "$BUILD_LOG" || true
        # Also copy initramfs.cpio.gz if present (UKI is the canonical path,
        # but the standalone initramfs is useful for diagnostic boots).
        [ -f "$chroot_bootloader_dir/initramfs.cpio.gz" ] && \
            cp -av "$chroot_bootloader_dir/initramfs.cpio.gz" "$host_bootloader_dir/" 2>&1 | tee -a "$BUILD_LOG"
        log "  Bootloader artifacts copied to host: $host_bootloader_dir/"
    else
        log "  WARN: chroot bootloader dir missing: $chroot_bootloader_dir"
        log "  phase_iso will fail unless bootloader artifacts are placed at $host_bootloader_dir/"
    fi
    log ""

    # A-002 (T0-2): UNSIGNED_TEST=1 lets the orchestrator run end-to-end
    # without an operator ceremony pause. The .unsigned-test.iso variant
    # is for dev iteration on Secure Boot OFF VMs; release ISOs still
    # require the operator-only signing ceremony described below.
    if [ "${UNSIGNED_TEST:-0}" = "1" ]; then
        log "================================================================"
        log "  UNSIGNED_TEST=1 — skipping operator-only ceremony pause"
        log "================================================================"
        log ""
        log "  The orchestrator will continue through phase_image,"
        log "  phase_manifest, phase_squashfs, and phase_iso to produce"
        log "  an .unsigned-test.iso artifact (Secure Boot OFF required)."
        log ""
        log "  For release-grade signed ISOs, re-run without UNSIGNED_TEST=1"
        log "  to hit the ceremony pause below."
        log ""
        return 0
    fi

    log "================================================================"
    log "  ENFORCED PAUSE: bootloader artifacts are UNSIGNED"
    log "================================================================"
    log ""
    log "  phase_image packages the bootloader into the disk image. Without"
    log "  signing between bootloader and image, the ISO ships with unsigned"
    log "  grub/kernel and fails shim Secure Boot verification at boot."
    log ""
    log "  This stop is HARD-CODED (not gated by --stop-after) because the"
    log "  signing ceremony is operator-only and cannot be skipped via flag."
    log "  See docs/signing-procedure.md for the operational runbook."
    log ""
    log "  Operator workflow:"
    log "    1. Run scripts/sign-release.sh on the signing workstation."
    log "    2. Place signed artifacts back at $host_bootloader_dir/."
    log "    3. Resume with: sudo bash $0 --user $BUILD_USER --start-at image"
    log ""
    exit 0
}

phase_image() {
    log "Packaging chroot into bootable disk image..."

    # D-007 compliance gate — refuse to assemble any shippable artifact
    # until SSH/credentials posture is correct. See
    # docs/owner-directives.md D-007 (Class A gate).
    log "  Running D-007 compliance gate..."
    if ! bash "${SCRIPTS}/check-d007-compliance.sh" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  ERROR: D-007 compliance gate FAILED."
        log "  Refusing to assemble disk image with SSH/credentials posture violations."
        log "  See docs/owner-directives.md D-007 for the canonical requirements."
        log "  Fix violations and re-run phase_image."
        exit 1
    fi
    log "  D-007 compliance gate PASS"

    # D-010 compliance gate — refuse to assemble any shippable artifact
    # if the InterGen AI assistant is enabled by default at any layer.
    # See docs/owner-directives.md D-010 (Class A gate).
    log "  Running D-010 compliance gate..."
    if ! bash "${SCRIPTS}/check-d010-compliance.sh" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  ERROR: D-010 compliance gate FAILED."
        log "  Refusing to assemble disk image with InterGen AI opt-in posture violations."
        log "  See docs/owner-directives.md D-010 for the canonical requirements."
        log "  Fix violations and re-run phase_image."
        exit 1
    fi
    log "  D-010 compliance gate PASS"

    # D-011 compliance gate — refuse to assemble any shippable artifact
    # until default-deny firewall posture is correct. See
    # docs/owner-directives.md D-011 (Class A gate).
    log "  Running D-011 compliance gate..."
    if ! bash "${SCRIPTS}/check-d011-compliance.sh" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  ERROR: D-011 compliance gate FAILED."
        log "  Refusing to assemble disk image with firewall-policy violations."
        log "  See docs/owner-directives.md D-011 for the canonical requirements."
        log "  Fix violations and re-run phase_image."
        exit 1
    fi
    log "  D-011 compliance gate PASS"

    # Tear down chroot mounts before imaging
    log "  Tearing down chroot mounts..."
    bash "${SCRIPTS}/chroot-teardown.sh" 2>&1 | tee -a "$BUILD_LOG" || true

    # Clean build infrastructure from target rootfs.
    # Kernel source is staged at /usr/src/linux-* by the linux-kernel(-pass2)
    # package's do_install (NOT under /mnt/intergenos or /sources or /tmp),
    # so these rm operations don't touch it. See packages/core/linux-kernel*.
    log "  Cleaning build artifacts from target..."
    rm -rf "${IGOS}/mnt/intergenos"
    rm -rf "${IGOS}/sources"
    rm -rf "${IGOS}/tmp"/*
    mkdir -p "${IGOS}/tmp"
    chmod 1777 "${IGOS}/tmp"
    log "  Build artifacts removed"

    # Sanity gate: kernel source MUST be staged to /usr/src/linux-* before
    # imaging. If missing, the linux-kernel-pass2 package's do_install
    # regressed — DKMS / out-of-tree modules (NVIDIA, VirtualBox, ZFS)
    # would not work on the shipped ISO. Fail loudly rather than ship a
    # broken rootfs.
    if ! ls -d "${IGOS}/usr/src/linux-"* >/dev/null 2>&1; then
        log "  ERROR: /usr/src/linux-* missing from chroot — kernel source not staged"
        log "  This is a regression in packages/core/linux-kernel-pass2/build.sh's do_install."
        log "  Refusing to image without source. Fix the kernel package + rebuild."
        exit 1
    fi
    local src_dir
    src_dir=$(ls -d "${IGOS}/usr/src/linux-"*/ | head -1)
    log "  Sanity gate PASS: kernel source staged at ${src_dir#${IGOS}}"

    # Create the image — write to virtiofs-shared path so the host
    # can access it directly without copying through SSH
    local image_path="/mnt/intergenos/build/intergenos.qcow2"
    bash "${SCRIPTS}/create-image.sh" "$image_path" 500G 2>&1 | tee -a "$BUILD_LOG"

    log ""
    log "  Disk image created at: $image_path"
    log "  (accessible from host via virtiofs)"
    log ""
    log "  Create a VM with:"
    log "    cp ${image_path} /mnt/jarvis-storage/VMs/intergenos.qcow2"
    log "    See create-image.sh output above for virt-install command."
}

phase_manifest() {
    # Step 4 of 7 ship-gate (install-time integrity verification design doc
    # docs/research/security/install-integrity-verification.md §5.2):
    # emit a BSD-style sha256sum manifest covering every .igos.tar.gz the
    # build produced. Manifest is unsigned at this point — sign-release.sh
    # --manifest signs it on the signing workstation; build-iso.sh embeds
    # the signed manifest + release-key public component in the ISO at
    # /install/intergenos-archive-manifest.txt + /install/intergenos-release-key.asc.
    log "Generating archive integrity manifest..."

    local archives_dir="${IGOS}/var/lib/igos/archives"
    local out_dir="/mnt/intergenos/build"
    local manifest="${out_dir}/intergenos-archive-manifest.txt"
    local build_id="${INTERGENOS_BUILD_ID:-v1.0-dev1}"
    local built_on="${INTERGENOS_BUILD_HOST:-$(hostname -f 2>/dev/null || hostname)}"
    local built_at_iso
    if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
        # Honor SDE for reproducibility (Q-REPRO-GOAL=v1.0 bit-identical)
        built_at_iso=$(date -u -d "@${SOURCE_DATE_EPOCH}" '+%Y-%m-%dT%H:%M:%SZ')
    else
        built_at_iso=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    fi

    if [ ! -d "$archives_dir" ]; then
        log "  ERROR: archives dir not found: $archives_dir"
        log "  (manifest phase requires phase_image to have completed; chroot still mounted)"
        return 1
    fi

    mkdir -p "$out_dir"

    # Emit header. Lines starting with '#' are comments per BSD sha256sum
    # convention; sha256sum -c ignores them.
    {
        printf '# InterGenOS archive integrity manifest\n'
        printf '# Build: %s\n' "$build_id"
        printf '# Built: %s\n' "$built_at_iso"
        printf '# Built-on: %s\n' "$built_on"
        printf '# Manifest-version: 1\n'
    } > "$manifest"

    # Walk archives_dir; sort for deterministic output (cross-host
    # byte-identity per Q-REPRO-GOAL). Path in the manifest is relative
    # to /var/lib/igos/archives/ so the install-time verifier doesn't
    # need to know the build host's absolute path.
    local archive_count=0
    local rel
    while IFS= read -r -d '' archive; do
        rel="${archive#${archives_dir}/}"
        local sha
        sha=$(sha256sum "$archive" | awk '{print $1}')
        printf 'SHA256 (%s) = %s\n' "$rel" "$sha" >> "$manifest"
        archive_count=$((archive_count + 1))
    done < <(find "$archives_dir" -type f -name '*.igos.tar.gz' -print0 | sort -z)

    printf '# End of manifest.\n' >> "$manifest"

    log "  Manifest emitted: $manifest"
    log "  Archives covered: $archive_count"
    log "  SHA256 of manifest: $(sha256sum "$manifest" | awk '{print $1}')"

    if [ "$archive_count" -eq 0 ]; then
        log "  WARN: 0 archives found in $archives_dir; manifest is empty."
        log "  This may be expected during partial-build runs (e.g. --stop-after toolchain)"
        log "  but is unexpected after a full build pipeline. Investigate before signing."
        # Per §1 B14: opt-in strict mode for full-build CI. When set,
        # an empty manifest fails the manifest phase rather than warning.
        # Useful for full builds where 0 archives indicates a real bug.
        if [ "${MANIFEST_STRICT:-0}" = "1" ]; then
            log "  FATAL: MANIFEST_STRICT=1 set; failing on empty manifest."
            return 1
        fi
    fi

    log ""
    log "  Next step (signing workstation, NOT this build host):"
    log "    sudo bash scripts/sign-release.sh --manifest $manifest --output <signed-out-dir>"
    log ""
    log "  Then place the signed manifest + intergenos-release-key.asc into the ISO"
    log "  at /install/ via build-iso.sh inputs (per design doc §5.2)."
}

phase_squashfs() {
    # A-002 (T0-2 2026-05-18): wire build-squashfs.sh into the orchestrator
    # pipeline. Previously the script existed but was operator-driven via
    # build/spoc-*.sh kickoffs; ops doc 02 framed orchestrator end-to-end
    # ISO build as if it worked, but neither phase_squashfs nor phase_iso
    # existed. Runs AFTER phase_image (which cleans build infrastructure
    # from the chroot — /mnt/intergenos, /sources, /tmp/*) so the squashfs
    # captures only the bootable end-user filesystem.
    log "Building live-ISO root filesystem squashfs from cleaned chroot..."
    OUTPUT="/mnt/intergenos/build/filesystem.squashfs" \
        bash "${SCRIPTS}/build-squashfs.sh" 2>&1 | tee -a "$BUILD_LOG"

    if [ ! -f "/mnt/intergenos/build/filesystem.squashfs" ]; then
        log "  ERROR: squashfs not produced at /mnt/intergenos/build/filesystem.squashfs"
        return 1
    fi
    local squashfs_size
    squashfs_size=$(stat -c '%s' "/mnt/intergenos/build/filesystem.squashfs")
    log "  squashfs at /mnt/intergenos/build/filesystem.squashfs (size=$squashfs_size bytes)"
}

phase_iso() {
    # A-002 (T0-2 2026-05-18): wire build-iso.sh into the orchestrator. The
    # bootloader artifacts come from phase_bootloader's host copy at
    # /mnt/intergenos/build/bootloader/ — either the unsigned originals
    # (UNSIGNED_TEST=1 path) or the .signed variants placed there by the
    # operator after running scripts/sign-release.sh on the signing
    # workstation.
    log "Assembling live ISO from bootloader artifacts + squashfs..."

    local bootloader_dir="/mnt/intergenos/build/bootloader"
    local squashfs="/mnt/intergenos/build/filesystem.squashfs"
    local iso_out="/mnt/intergenos/build/intergenos-1.0-dev1.iso"

    if [ ! -d "$bootloader_dir" ]; then
        log "  ERROR: bootloader dir missing: $bootloader_dir"
        log "  phase_bootloader copies artifacts there. If running --start-at iso,"
        log "  place signed (or unsigned-test) shimx64.efi/grubx64.efi/igos-live.efi/"
        log "  igos-install-gui.efi/igos-install-tui.efi at $bootloader_dir/ first."
        return 1
    fi
    if [ ! -f "$squashfs" ]; then
        log "  ERROR: squashfs missing at $squashfs"
        log "  phase_squashfs must complete before phase_iso. Run --start-at squashfs."
        return 1
    fi

    # Select shim/grub/UKI input filenames by signed state. The .signed
    # extension is sign-release.sh / sign-bootloader.sh's output convention.
    local shim grub uki_live uki_install_gui uki_install_tui
    if [ "${UNSIGNED_TEST:-0}" = "1" ]; then
        shim="$bootloader_dir/shimx64.efi"
        grub="$bootloader_dir/grubx64.efi"
        uki_live="$bootloader_dir/igos-live.efi"
        uki_install_gui="$bootloader_dir/igos-install-gui.efi"
        uki_install_tui="$bootloader_dir/igos-install-tui.efi"
    else
        shim="$bootloader_dir/shimx64.efi.signed"
        grub="$bootloader_dir/grubx64.efi.signed"
        uki_live="$bootloader_dir/igos-live.efi.signed"
        uki_install_gui="$bootloader_dir/igos-install-gui.efi.signed"
        uki_install_tui="$bootloader_dir/igos-install-tui.efi.signed"
    fi

    local missing=()
    for f in "$shim" "$grub" "$uki_live" "$uki_install_gui" "$uki_install_tui"; do
        [ -f "$f" ] || missing+=( "$f" )
    done
    if (( ${#missing[@]} > 0 )); then
        log "  ERROR: required bootloader artifact(s) missing:"
        for f in "${missing[@]}"; do
            log "    - $f"
        done
        if [ "${UNSIGNED_TEST:-0}" = "1" ]; then
            log "  Re-run phase_bootloader to regenerate unsigned artifacts."
        else
            log "  Run scripts/sign-release.sh on the signing workstation, then"
            log "  copy the .signed files to $bootloader_dir/ before resuming."
        fi
        return 1
    fi

    UNSIGNED_TEST="${UNSIGNED_TEST:-0}" \
    SHIM="$shim" \
    GRUB="$grub" \
    UKI_LIVE="$uki_live" \
    UKI_INSTALL_GUI="$uki_install_gui" \
    UKI_INSTALL_TUI="$uki_install_tui" \
    SQUASHFS="$squashfs" \
    OUTPUT="$iso_out" \
        bash "${SCRIPTS}/build-iso.sh" 2>&1 | tee -a "$BUILD_LOG"

    # build-iso.sh appends .unsigned-test.iso suffix when UNSIGNED_TEST=1,
    # so the actual output filename depends on mode.
    local actual_iso="$iso_out"
    if [ "${UNSIGNED_TEST:-0}" = "1" ]; then
        actual_iso="${iso_out%.iso}.unsigned-test.iso"
    fi
    if [ ! -f "$actual_iso" ]; then
        log "  ERROR: ISO not found at $actual_iso post-build (check build-iso.sh output)"
        return 1
    fi
    local iso_size
    iso_size=$(stat -c '%s' "$actual_iso")
    log "  ISO at $actual_iso (size=$iso_size bytes)"

    # B-018 + B-034 (T0-2 2026-05-18): atomic provenance manifest. The
    # cycle-5 ISO's manifest carried input-SHAs that did not match the
    # UKIs actually written into the ESP — i.e. the manifest existed for
    # a different build than the ISO. Re-emit at the moment of ISO
    # finalization (post-build-iso.sh success) so input-SHAs always
    # match what xorriso just consumed. Manifest filename mirrors the
    # ISO basename so lineage is unambiguous even when both .iso and
    # .unsigned-test.iso coexist in build/.
    local manifest_file="${actual_iso}.manifest"
    log "  Emitting build provenance manifest: $manifest_file"
    {
        printf '# InterGenOS ISO build provenance manifest\n'
        printf '# ISO basename: %s\n' "$(basename "$actual_iso")"
        printf '# Generated: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        printf '# Build mode: %s\n' \
            "$([ "${UNSIGNED_TEST:-0}" = "1" ] && echo "UNSIGNED_TEST" || echo "signed")"
        [ -n "${SOURCE_DATE_EPOCH:-}" ] && \
            printf '# SOURCE_DATE_EPOCH: %s\n' "$SOURCE_DATE_EPOCH"
        printf '# Build host: %s\n' "$(hostname -f 2>/dev/null || hostname)"
        printf '# Manifest-version: 1\n'
        printf '#\n'
        printf '# Input artifacts (SHAs as fed into build-iso.sh):\n'
        for input in "$shim" "$grub" "$uki_live" "$uki_install_gui" \
                     "$uki_install_tui" "$squashfs"; do
            local _sha
            _sha=$(sha256sum "$input" | awk '{print $1}')
            printf 'SHA256 (input %s) = %s\n' "$(basename "$input")" "$_sha"
        done
        printf '#\n'
        printf '# Output ISO:\n'
        local _iso_sha
        _iso_sha=$(sha256sum "$actual_iso" | awk '{print $1}')
        printf 'SHA256 (output %s) = %s\n' "$(basename "$actual_iso")" "$_iso_sha"
        printf '# End of manifest.\n'
    } > "$manifest_file"
    log "  Manifest written: $manifest_file"
}

phase_publish() {
    # Post-build publish hook (E1.B.8). Publishes the binary repository to
    # repo.intergenos.org if --publish flag was passed.
    # Only runs after a successful full build; gated behind --publish flag
    # to prevent accidental publishing from development/CI builds.
    #
    # Calls scripts/publish-repo.sh which:
    # 1. Generates InterGenOS.db index via pkm.repo.generate_index()
    # 2. PGP-signs it via pkm.repo.sign_index()
    # 3. Rsyncs archives + index + signature to staging
    # 4. Atomically promotes staging → live on remote
    log "Publishing binary repository..."
    log "  Archive dir: ${IGOS}/var/lib/igos/archives"

    local publish_script="${SCRIPTS}/publish-repo.sh"
    if [ ! -f "$publish_script" ]; then
        log "  ERROR: publish script not found: $publish_script"
        return 1
    fi

    if [ ! -d "${IGOS}/var/lib/igos/archives" ]; then
        log "  ERROR: archives dir not found: ${IGOS}/var/lib/igos/archives"
        return 1
    fi

    bash "$publish_script" --archive-dir "${IGOS}/var/lib/igos/archives" || {
        log "  ERROR: publish-repo.sh failed"
        return 1
    }

    log "  Repo published. Verify: pk sync + pkm install <test-pkg> on fresh target."
}

# ==========================================================================
# Main — run all phases
# ==========================================================================

BUILD_START=$(date +%s)

log ""
log "================================================================"
log "  InterGenOS Build"
log "  User: $BUILD_USER"
log "  Target: $IGOS"
log "  Started: $(date)"
if [ -n "$START_AT" ]; then
    log "  Starting at: $START_AT"
fi
if [ -n "$STOP_AFTER" ]; then
    log "  Stopping after: $STOP_AFTER"
fi
if $CHECKPOINT; then
    log "  Checkpoints: enabled (saving to ${CHECKPOINT_DIR}/)"
fi
log "================================================================"

run_phase "validate"       "Verify host requirements"            phase_validate
run_phase "verify-sources" "Audit source SHAs against tarballs"  phase_verify_sources
run_phase "setup"          "Create build environment"            phase_setup
run_phase "toolchain"    "Cross-compilation toolchain (Ch 5-6)" phase_toolchain
run_phase "chroot-prep"  "Prepare chroot environment (Ch 7)"   phase_chroot_prep
run_phase "chroot-tools" "Build temp tools in chroot (Ch 7)"   phase_chroot_tools
run_phase "core"         "Build core system (Ch 8, LFS order)" phase_core
run_phase "config"       "System configuration (Ch 9)"         phase_config
run_phase "core-extra"   "Build extra core packages (BLFS)"    phase_core_extra
run_phase "base"         "Build base CLI tools (end-user)"     phase_base
run_phase "kernel"       "Build kernel (Ch 10)"                phase_kernel
run_phase "desktop"     "Build desktop (GNOME on Wayland)"    phase_desktop
run_phase "ai"          "Build AI tier (InterGen assistant)"  phase_ai
run_phase "extra"       "Build extra tier (applications)"     phase_extra
run_phase "bootloader"  "Assemble unsigned bootloader artifacts" phase_bootloader
run_phase "image"       "Package bootable disk image"         phase_image
run_phase "manifest"    "Emit archive integrity manifest"     phase_manifest
run_phase "squashfs"    "Build live-ISO root filesystem squashfs" phase_squashfs
run_phase "iso"         "Assemble live ISO (signed or unsigned-test)" phase_iso
if $PUBLISH; then
    run_phase "publish" "Publish binary repository to repo.intergenos.org" phase_publish
fi

# ==========================================================================
# Done
# ==========================================================================

BUILD_ELAPSED=$(( $(date +%s) - BUILD_START ))
BUILD_HOURS=$(( BUILD_ELAPSED / 3600 ))
BUILD_MINUTES=$(( (BUILD_ELAPSED % 3600) / 60 ))

log ""
log "================================================================"
log "  InterGenOS Build Complete"
log "  Total time: ${BUILD_HOURS}h ${BUILD_MINUTES}m"
log "  Finished: $(date)"
log "================================================================"
log ""
