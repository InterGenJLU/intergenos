#!/bin/bash
# cargo-vendor-gen-all.sh — Bulk runner for cargo-vendor-gen.sh.
#
# Walks packages/*/*/package.yml, identifies Rust packages declaring
# `build_artifacts: ... generated_by: cargo-vendor`, and invokes
# scripts/cargo-vendor-gen.sh for each. Idempotent: if a previously-emitted
# Cargo.lock side artifact exists at ${OUTPUT_DIR}/<pkg>-<version>-Cargo.lock,
# the bulk runner passes it back via --cargo-lock for byte-reproducible re-runs.
#
# USAGE:
#   scripts/cargo-vendor-gen-all.sh [--dry-run] [--tier <tier>] [--package <name>]
#
# OPTIONS:
#   --dry-run         List packages that WOULD be vendored without doing it.
#   --tier <tier>     Restrict to one tier (core / desktop / extra / etc.). Repeatable.
#   --package <name>  Restrict to one package by name. Repeatable. Overrides --tier.
#
# ENVIRONMENT:
#   OUTPUT_DIR        Passed through to cargo-vendor-gen.sh (default build/vendor-artifacts/).
#   SOURCE_DATE_EPOCH Passed through.
#   INTERGENOS_ROOT   Repo root (default: autodetect from script location).
#
# EXIT:
#   0 = all packages vendored successfully (or dry-run completed)
#   1 = one or more packages failed; per-package error already on stderr
#   2 = bad arguments / preflight failure
#
# DOES NOT REQUIRE: chroot, sudo, build VM. Host-side maintainer tool.
# See docs/research/build_system/cargo_vendor_helper_v1.md for full design.

set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HELPER="$SCRIPT_DIR/cargo-vendor-gen.sh"

# Repo-root autodetect (matches in-repo standard from preflight gates).
if [ -z "${INTERGENOS_ROOT:-}" ]; then
    INTERGENOS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

OUTPUT_DIR="${OUTPUT_DIR:-${INTERGENOS_ROOT}/build/vendor-artifacts}"
DRY_RUN=0
declare -a TIER_FILTER=()
declare -a PACKAGE_FILTER=()

usage() {
    cat >&2 <<EOF
Usage: ${SCRIPT_NAME} [--dry-run] [--tier <tier>] [--package <name>]

Bulk-runs cargo-vendor-gen.sh against every package.yml declaring
\`build_artifacts: ... generated_by: cargo-vendor\`.

Options:
  --dry-run            List packages that would be vendored without doing it.
  --tier <tier>        Restrict to one tier. Repeatable.
                       Valid: core / desktop / extra / toolchain / etc.
  --package <name>     Restrict to one package by name. Repeatable.

Environment:
  OUTPUT_DIR           (passed to helper; default \${INTERGENOS_ROOT}/build/vendor-artifacts)
  SOURCE_DATE_EPOCH    (passed to helper)
  INTERGENOS_ROOT      Repo root (default: autodetect from script location)

Examples:
  ${SCRIPT_NAME} --dry-run
  ${SCRIPT_NAME} --tier core
  ${SCRIPT_NAME} --package cargo-c
EOF
    exit 2
}

log()  { printf '[cargo-vendor-gen-all] %s\n' "$*" >&2; }
die()  { log "ERROR: $*"; exit 2; }

# ---------- args ----------
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run)        DRY_RUN=1; shift ;;
        --tier)           [ "$#" -ge 2 ] || die "--tier requires a value"
                          TIER_FILTER+=("$2"); shift 2 ;;
        --package)        [ "$#" -ge 2 ] || die "--package requires a value"
                          PACKAGE_FILTER+=("$2"); shift 2 ;;
        --help|-h)        usage ;;
        *)                die "unknown argument: $1 (try --help)" ;;
    esac
done

# ---------- preflight ----------
[ -x "$HELPER" ] || die "cargo-vendor-gen.sh not executable at: $HELPER"
[ -d "$INTERGENOS_ROOT/packages" ] || die "packages/ not found under: $INTERGENOS_ROOT"
command -v python3 >/dev/null 2>&1 || die "python3 not in PATH (needed for YAML parsing)"

mkdir -p "$OUTPUT_DIR"

# ---------- enumerate cargo-vendor packages ----------
# Uses python3 + stdlib YAML parser to walk package.yml files. Emits TSV:
#   <tier>\t<name>\t<version>\t<source-url>\t<vendor-artifact-name>
# One row per package that declares a cargo-vendor build_artifact.
ENUMERATE() {
    python3 - "$INTERGENOS_ROOT" <<'PYEOF'
import os
import re
import sys
from pathlib import Path

# Stdlib-only YAML parser is too limited for general package.yml; rely on PyYAML
# the same way audit-yaml-source-pinning.sh does. (PyYAML is a required maintainer
# dependency per docs/research/build_system/offline_rust_builds_2026-04-03.md
# and the rest of the build tooling.)
try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

root = Path(sys.argv[1])

def resolve_var(s: str, name: str, version: str) -> str:
    """Mirror download-sources.py's variable substitution."""
    parts = version.split(".")
    major = parts[0] if parts else ""
    major_minor = ".".join(parts[:2]) if len(parts) >= 2 else version
    return (s.replace("${version_major_minor}", major_minor)
             .replace("${version_major}", major)
             .replace("${version}", version)
             .replace("${name}", name))

for yml in sorted((root / "packages").glob("*/*/package.yml")):
    try:
        with yml.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError:
        continue
    if not isinstance(data, dict):
        continue

    name    = str(data.get("name", yml.parent.name))
    version = str(data.get("version", ""))
    tier    = yml.parent.parent.name

    artifacts = data.get("build_artifacts") or []
    if not isinstance(artifacts, list):
        continue

    has_cargo_vendor = any(
        isinstance(a, dict) and a.get("generated_by") == "cargo-vendor"
        for a in artifacts
    )
    if not has_cargo_vendor:
        continue

    # Resolve the upstream source URL (first source: entry; matches build.sh's
    # `tar xf ... --strip-components=1` extract-from-cwd convention).
    sources = data.get("source") or []
    if not isinstance(sources, list) or not sources:
        continue
    first = sources[0]
    if not isinstance(first, dict):
        continue
    url = resolve_var(str(first.get("url", "")), name, version)
    if not url:
        continue

    vendor_name = f"{name}-{version}-vendor.tar.xz"
    print(f"{tier}\t{name}\t{version}\t{url}\t{vendor_name}")
PYEOF
}

mapfile -t ROWS < <(ENUMERATE)
[ "${#ROWS[@]}" -gt 0 ] || { log "no cargo-vendor packages found in tree"; exit 0; }

# ---------- filter ----------
FILTERED_ROWS=()
for row in "${ROWS[@]}"; do
    IFS=$'\t' read -r tier name version url vendor_name <<<"$row"

    # Tier filter
    if [ "${#TIER_FILTER[@]}" -gt 0 ]; then
        match=0
        for t in "${TIER_FILTER[@]}"; do
            [ "$tier" = "$t" ] && match=1 && break
        done
        [ "$match" = "1" ] || continue
    fi

    # Package filter (overrides tier)
    if [ "${#PACKAGE_FILTER[@]}" -gt 0 ]; then
        match=0
        for p in "${PACKAGE_FILTER[@]}"; do
            [ "$name" = "$p" ] && match=1 && break
        done
        [ "$match" = "1" ] || continue
    fi

    FILTERED_ROWS+=("$row")
done

[ "${#FILTERED_ROWS[@]}" -gt 0 ] || { log "no packages match filters"; exit 0; }

# ---------- report + run ----------
log "found ${#FILTERED_ROWS[@]} cargo-vendor package(s)"
printf '\n%-10s %-20s %-15s %-12s %s\n' "TIER" "NAME" "VERSION" "LOCKFILE" "VENDOR ARTIFACT" >&2
printf '%-10s %-20s %-15s %-12s %s\n' "----" "----" "-------" "--------" "---------------" >&2

# Pre-check: for each package, note whether a prior Cargo.lock side artifact exists
declare -a TODO_NAMES=()
declare -a TODO_VERSIONS=()
declare -a TODO_URLS=()
declare -a TODO_LOCK_PATHS=()

for row in "${FILTERED_ROWS[@]}"; do
    IFS=$'\t' read -r tier name version url vendor_name <<<"$row"
    lockfile_path="$OUTPUT_DIR/${name}-${version}-Cargo.lock"
    if [ -f "$lockfile_path" ]; then
        lock_status="pinned"
    else
        lock_status="fresh-gen"
    fi
    printf '%-10s %-20s %-15s %-12s %s\n' "$tier" "$name" "$version" "$lock_status" "$vendor_name" >&2

    TODO_NAMES+=("$name")
    TODO_VERSIONS+=("$version")
    TODO_URLS+=("$url")
    TODO_LOCK_PATHS+=("$lockfile_path")
done
echo "" >&2

if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: would invoke cargo-vendor-gen.sh × ${#TODO_NAMES[@]} (above)"
    exit 0
fi

# ---------- execute ----------
FAIL_COUNT=0
SUCCESS_COUNT=0
declare -a FAILED_PACKAGES=()

for i in "${!TODO_NAMES[@]}"; do
    name="${TODO_NAMES[$i]}"
    version="${TODO_VERSIONS[$i]}"
    url="${TODO_URLS[$i]}"
    lockfile="${TODO_LOCK_PATHS[$i]}"

    log ""
    log "============================================================"
    log "[$((i+1))/${#TODO_NAMES[@]}]  ${name} ${version}"
    log "============================================================"

    declare -a HELPER_ARGS=()
    if [ -f "$lockfile" ]; then
        HELPER_ARGS+=("--cargo-lock" "$lockfile")
    fi
    HELPER_ARGS+=("$name" "$version" "$url")

    if OUTPUT_DIR="$OUTPUT_DIR" \
       SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-0}" \
       bash "$HELPER" "${HELPER_ARGS[@]}"; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_PACKAGES+=("$name-$version")
        log "FAILED: $name $version (continuing with remaining packages)"
    fi
done

# ---------- summary ----------
log ""
log "============================================================"
log "BULK SUMMARY: ${SUCCESS_COUNT} succeeded, ${FAIL_COUNT} failed (of ${#TODO_NAMES[@]} attempted)"
log "============================================================"
if [ "${#FAILED_PACKAGES[@]}" -gt 0 ]; then
    log "Failed packages:"
    for p in "${FAILED_PACKAGES[@]}"; do log "  - $p"; done
    exit 1
fi
exit 0
