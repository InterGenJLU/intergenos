#!/bin/bash
# InterGenOS Package Functions — DESTDIR Staging + Slackware-style Tracking
set -e
#
# Sourced by the Chapter 8 build runner. Provides functions to:
#   1. Stage a package's installed files via DESTDIR
#   2. Generate a file manifest
#   3. Create a compressed archive (.igos.tar.gz)
#   4. Deploy staged files to the live filesystem
#   5. Run post-install hooks on the live system
#
# Database: /var/lib/igos/packages/<name>-<version>  (one text file per package)
# Archives: /var/lib/igos/archives/<name>-<version>.igos.tar.gz
#
# Design: Slackware-style manifests — human-readable, cat-inspectable,
# no binary database, no dependency resolution at install time.
# The build system handles build order; this layer tracks installed state.

# ============================================================================
# Configuration
# ============================================================================

IGOS_PKG_DB="/var/lib/igos/packages"
IGOS_PKG_ARCHIVES="/var/lib/igos/archives"
IGOS_PKG_STAGING="/tmp/igos-staging"

# ============================================================================
# Environment refresh — /etc/profile.d/*.sh
# ============================================================================

# Source every /etc/profile.d/*.sh in the chroot. Login shells do this via
# /etc/profile; the build pipeline runs commands as non-interactive bash and
# would otherwise miss PATH augmentations from BLFS-style installs that put
# their binaries under /opt/<tool>/bin and rely on profile.d to expose them.
#
# Originally surfaced by Build #9 resume #8 cargo-c halt (cargo: command not
# found at exit 127): rust installed cargo to /opt/rustc-1.95.0/bin and wrote
# /etc/profile.d/rustc.sh to extend PATH, but cargo-c's build.sh — running in
# a fresh non-interactive subshell — never saw the PATH extension. Same gap
# would bite future tools (java, go, other /opt/<x>/bin installs).
#
# Call this from each phase script's run_package right before sourcing the
# package's build.sh. Idempotent; safe to call repeatedly.
source_profile_d() {
    if [ -d /etc/profile.d ]; then
        local f
        for f in /etc/profile.d/*.sh; do
            [ -f "$f" ] && . "$f"
        done
    fi
}

# ============================================================================
# Source integrity verification
# ============================================================================

# Verify SHA256 checksum of a file before extraction.
# Usage: verify_source_checksum <filepath> <expected_sha256>
# Returns 0 on match, 1 on mismatch / missing / malformed checksum.
#
# Strict-type+length: expected MUST be exactly 64 lowercase hex characters.
# Empty / "NEEDS_CHECKSUM" placeholder / wrong-length / non-hex all FAIL.
# This matches the same-shape fix DeepSeek applied for pkm H1 in
# pkm/repo.py:_verify_checksum at master 2eee235. Audit finding S3 in
# docs/research/code_audits/scripts_audit_2026-04-29_spoc.md.
verify_source_checksum() {
    local file="$1"
    local expected="$2"

    if [[ ! "$expected" =~ ^[a-f0-9]{64}$ ]]; then
        echo "[pkg] ERROR: $(basename "$file") has no valid sha256 checksum"
        echo "[pkg]   got: '${expected:0:32}...'"
        echo "[pkg]   expected: 64 lowercase hex chars (sha256 hex digest)"
        return 1
    fi

    local actual
    actual=$(sha256sum "$file" | cut -d' ' -f1)

    if [ "$actual" != "$expected" ]; then
        echo "[pkg] ERROR: Checksum mismatch for $(basename "$file")"
        echo "[pkg]   expected: $expected"
        echo "[pkg]   actual:   $actual"
        return 1
    fi

    echo "[pkg] Checksum verified: $(basename "$file")"
    return 0
}

# Read SHA256 from a package.yml file.
# Usage: get_package_sha256 <package_yml_path>
# Outputs the sha256 value to stdout.
get_package_sha256() {
    local yml="$1"
    grep 'sha256:' "$yml" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'"
}

# Extract a source archive into $workdir, dispatching by file extension.
# Mirrors the dispatch in igos-build/builder.py for shell-side parity:
#   .zip   -> bsdtar -xf (no --strip-components; many upstream zips ship flat
#             without a top-level dir, e.g., docbook-xml-4.5.zip)
#   .lz    -> bsdtar -xf with --strip-components=1 (lzip — tar lacks native
#             support in the chroot's tar binary)
#   .tar*  -> tar -xf with --strip-components=1 + --no-same-owner/perms
#
# Args:
#   $1 — tarball basename (e.g. docbook-xml-4.5.zip)
#   $2 — workdir to extract into (must exist + be empty)
# Returns: 0 on success, propagates extractor's exit code on failure.
#
# Centralizes what was previously duplicated across chroot-build-ch8.sh,
# chroot-build-base.sh, chroot-build-core-extra.sh, chroot-build-ch10.sh.
# Build #9 halted at docbook-xml when the hardcoded `tar -xf` couldn't
# read the .zip; this helper closes that bug class.
extract_source() {
    local tarball="$1"
    local workdir="$2"
    local src="${IGOS_SOURCES}/${tarball}"

    case "$tarball" in
        *.zip)
            bsdtar -xf "$src" -C "$workdir"
            ;;
        *.lz)
            bsdtar -xf "$src" -C "$workdir" --strip-components=1 \
                --no-same-owner --no-same-permissions
            ;;
        *)
            tar -xf "$src" -C "$workdir" --strip-components=1 \
                --no-same-owner --no-same-permissions
            ;;
    esac
}

# Apply patches declared in package.yml's `patches:` block to the current
# working directory (assumed to be the extracted source tree, cd'd into
# before this is called).
#
# Mirrors igos-build/styles/base.py:_patch_commands() so tier:core and
# tier:base packages get the same patch-application behavior as
# tier:desktop/extra/ai packages built via igos-build.py.
#
# For each declared patch:
#   1. Verifies the file exists at $IGOS_PATCHES (which inside the chroot
#      resolves to /sources where both $SOURCES and $PATCHES were copied
#      during phase_setup).
#   2. Verifies SHA256 against the declared value (defense-in-depth alongside
#      phase_verify_sources).
#   3. Applies via `patch -Np1 -i ...` (or zcat/bzcat/xzcat for compressed).
#
# Usage: apply_package_patches <package_yml_path>
# Returns 0 if all declared patches applied cleanly, or if no patches declared.
# Returns 1 on any patch file missing, sha mismatch, or patch-apply failure.
#
# Background: tier:core / tier:base run_package helpers (build_ch8_package,
# build_core_package, build_base_package) historically did not apply patches.
# `# Patch applied by builder PATCH phase (package.yml)` comments in some
# build.sh files documented the intent but the wiring never landed. mitkrb
# halt 2026-05-10 surfaced the gap when mitkrb retiered desktop→core,
# moving from the igos-build.py path (auto-patch) to the run_package path
# (no auto-patch). This helper closes that gap for ALL tier:core+base
# packages.
apply_package_patches() {
    local yml="$1"
    if [ ! -f "$yml" ]; then
        # No package.yml means no declared patches. Don't fail; the build.sh
        # may be the source of truth for sourceless packages (e.g., pkm).
        return 0
    fi

    # Extract patches block via stdlib-only parser. The previous inline
    # `import yaml` approach broke at Ch 8 entry (Build #8 halt 2026-05-11)
    # because chroot Python has no PyYAML — it's itself a Ch 8 package.
    # Schema is ours; a targeted parser is right-sized.
    local patches_list
    patches_list=$(python3 "${SCRIPTS:-/mnt/intergenos/scripts}/parse-package-yml-patches.py" "$yml")
    local py_rc=$?
    if [ $py_rc -ne 0 ]; then
        echo "[pkg] FATAL: apply_package_patches could not parse $yml"
        return 1
    fi

    if [ -z "$patches_list" ]; then
        # No patches declared — common case, success.
        return 0
    fi

    # Apply each patch in declared order. Halt on first failure.
    local pfile psha patch_path actual
    while IFS='|' read -r pfile psha; do
        [ -z "$pfile" ] && continue
        patch_path="${IGOS_PATCHES}/${pfile}"
        if [ ! -f "$patch_path" ]; then
            echo "[pkg] FATAL: patch file not found: ${patch_path}"
            return 1
        fi
        if [ -n "$psha" ]; then
            if [[ ! "$psha" =~ ^[a-f0-9]{64}$ ]]; then
                echo "[pkg] FATAL: patch sha256 malformed for ${pfile}: '${psha:0:32}...'"
                return 1
            fi
            actual=$(sha256sum "$patch_path" | cut -d' ' -f1)
            if [ "$actual" != "$psha" ]; then
                echo "[pkg] FATAL: patch sha mismatch for ${pfile}"
                echo "[pkg]   expected: $psha"
                echo "[pkg]   actual:   $actual"
                return 1
            fi
        fi
        echo "[pkg] Applying patch: ${pfile}"
        case "$pfile" in
            *.gz)
                if ! zcat "$patch_path" | patch -Np1; then
                    echo "[pkg] FATAL: patch -Np1 (gz) failed for ${pfile}"
                    return 1
                fi
                ;;
            *.bz2)
                if ! bzcat "$patch_path" | patch -Np1; then
                    echo "[pkg] FATAL: patch -Np1 (bz2) failed for ${pfile}"
                    return 1
                fi
                ;;
            *.xz)
                if ! xzcat "$patch_path" | patch -Np1; then
                    echo "[pkg] FATAL: patch -Np1 (xz) failed for ${pfile}"
                    return 1
                fi
                ;;
            *)
                if ! patch -Np1 -i "$patch_path"; then
                    echo "[pkg] FATAL: patch -Np1 failed for ${pfile}"
                    return 1
                fi
                ;;
        esac
    done <<< "$patches_list"

    return 0
}

# ============================================================================
# Internal helpers
# ============================================================================

pkg_log() {
    echo "[pkg] $*" | tee -a "$IGOS_LOGS/pkg-install.log"
}

pkg_error() {
    echo "[pkg] ERROR: $*" | tee -a "$IGOS_LOGS/pkg-install.log" >&2
}

# ============================================================================
# pkg_init — Create database and archive directories
# ============================================================================

pkg_init() {
    mkdir -pv "$IGOS_PKG_DB"
    mkdir -pv "$IGOS_PKG_ARCHIVES"
    mkdir -pv "$IGOS_PKG_STAGING"
}

# ============================================================================
# pkg_stage — Run install() with DESTDIR pointing to a staging directory
#
# Usage: pkg_stage <name> <version>
#
# Expects:
#   - CWD is the package build directory
#   - An install() function is defined (from the package's build.sh)
#   - Or a pkg_custom_install() function for exception packages
#
# Sets: PKG_DEST (the staging root for this package)
# ============================================================================

pkg_stage() {
    local name="$1"
    local version="$2"

    export PKG_DEST="${IGOS_PKG_STAGING}/${name}-${version}"

    # Clean any prior staging attempt
    rm -rf "$PKG_DEST"
    mkdir -pv "$PKG_DEST"

    # Mirror root-level symlinks so DESTDIR installs follow them.
    # Without this, `make install DESTDIR=...` creates real /lib, /bin, /sbin
    # directories that collide with the root filesystem's symlinks.
    for link in bin lib sbin; do
        if [ -L "/$link" ]; then
            ln -sv "usr/$link" "${PKG_DEST}/$link"
        fi
    done
    case $(uname -m) in
        x86_64) mkdir -pv "${PKG_DEST}/lib64" ;;
    esac
    mkdir -pv "${PKG_DEST}/usr/"{bin,lib,sbin}

    # Export DESTDIR for autotools/meson packages
    export DESTDIR="$PKG_DEST"

    pkg_log "Staging ${name}-${version} to ${PKG_DEST}"

    # Run the package's do_install function
    # Named do_install (not install) to avoid collision with /usr/bin/install.
    # Output appends to the most recent build log for this package so all
    # output is in one place. Falls back to a standalone install log.
    local install_log
    install_log=$(ls -t "${IGOS_LOGS}/${name}-"*".log" 2>/dev/null | head -1)
    if [ -z "$install_log" ]; then
        install_log="${IGOS_LOGS}/${name}-install-$(date '+%Y%m%d-%H%M%S').log"
    fi

    if declare -f do_install > /dev/null 2>&1; then
        echo "=== [INSTALL] $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$install_log"
        do_install >> "$install_log" 2>&1
    else
        pkg_error "No do_install() function defined for ${name}"
        return 1
    fi

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Staging failed for ${name}-${version} (exit $rc)"
        return 1
    fi

    # Verify something was actually staged
    local file_count
    file_count=$(find "$PKG_DEST" -not -type d | wc -l)
    if [ "$file_count" -eq 0 ]; then
        pkg_error "Staging produced no files for ${name}-${version}"
        pkg_error "Check that do_install() uses \$DESTDIR or the correct staging variable"
        return 1
    fi

    pkg_log "Staged ${file_count} files for ${name}-${version}"

    # Unset DESTDIR so it doesn't leak into post-install steps
    unset DESTDIR

    return 0
}

# ============================================================================
# pkg_manifest — Generate a Slackware-style manifest from staged files
#
# Usage: pkg_manifest <name> <version> [description]
#
# Writes: $IGOS_PKG_DB/<name>-<version>
# ============================================================================

pkg_manifest() {
    local name="$1"
    local version="$2"
    local description="${3:-No description}"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"
    local manifest="${IGOS_PKG_DB}/${name}-${version}"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Calculate sizes
    local uncompressed_size
    uncompressed_size=$(du -sb "$dest" | cut -f1)
    local uncompressed_human
    uncompressed_human=$(du -sh "$dest" | cut -f1)

    # Generate file list — paths relative to staging root, sorted
    # Directories listed with trailing /
    local file_list
    file_list=$(cd "$dest" && find . -mindepth 1 | sed 's|^\./||' | sort)

    # Write the manifest
    cat > "$manifest" << EOF
PACKAGE NAME: ${name}-${version}
PACKAGE VERSION: ${version}
UNCOMPRESSED SIZE: ${uncompressed_human} (${uncompressed_size} bytes)
BUILD DATE: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
BUILD SYSTEM: InterGenOS LFS 13.0
DESCRIPTION:
${name}: ${description}

FILE LIST:
${file_list}
EOF

    pkg_log "Manifest written: ${manifest} ($(echo "$file_list" | wc -l) entries)"
    return 0
}

# ============================================================================
# pkg_archive — Create a .igos.tar.gz archive from staged files
#
# Usage: pkg_archive <name> <version>
#
# Creates: $IGOS_PKG_ARCHIVES/<name>-<version>.igos.tar.gz
#
# Uses gzip during initial build (available from Chapter 7).
# Archives can be re-compressed to zstd later if desired.
# ============================================================================

pkg_archive() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"
    local archive="${IGOS_PKG_ARCHIVES}/${name}-${version}.igos.tar.gz"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Create the archive — rooted at the staging directory so paths are relative
    # This means extracting to / will put files in the right place
    tar -C "$dest" -czf "$archive" .

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Archive creation failed for ${name}-${version}"
        return 1
    fi

    local archive_size
    archive_size=$(du -sh "$archive" | cut -f1)
    pkg_log "Archive created: ${archive} (${archive_size})"

    # Update manifest with compressed size
    local manifest="${IGOS_PKG_DB}/${name}-${version}"
    if [ -f "$manifest" ]; then
        local compressed_bytes
        compressed_bytes=$(stat -c%s "$archive")
        sed -i "/^BUILD DATE:/i COMPRESSED SIZE: ${archive_size} (${compressed_bytes} bytes)" "$manifest"
    fi

    return 0
}

# ============================================================================
# pkg_deploy — Copy staged files to the live filesystem
#
# Usage: pkg_deploy <name> <version>
#
# Copies everything from the staging directory to /
# Preserves permissions, ownership, and symlinks
#
# Safety: pre-checks for top-level entries that would collide with root-level
# symlinks (lib -> usr/lib, bin -> usr/bin, etc.). A package staging a real
# directory over one of these symlinks would kill the system.
# ============================================================================

pkg_deploy() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Pre-deploy safety check: detect staging entries that would collide with
    # root-level symlinks. These symlinks (lib -> usr/lib, bin -> usr/bin, etc.)
    # are load-bearing — replacing them with real directories is catastrophic.
    local dangerous=""
    for entry in lib lib64 bin sbin; do
        if [ -d "${dest}/${entry}" ] && [ ! -L "${dest}/${entry}" ] && [ -L "/${entry}" ]; then
            dangerous="${dangerous} ${entry}"
        fi
    done

    if [ -n "$dangerous" ]; then
        pkg_error "DANGEROUS: ${name}-${version} staging contains top-level dirs" \
                  "that would collide with root symlinks:${dangerous}"
        pkg_error "Fix the package build.sh to install to usr/ paths instead"
        return 1
    fi

    pkg_log "Deploying ${name}-${version} to live filesystem"

    # Use tar for deployment:
    # --no-overwrite-dir    preserves metadata of existing real directories
    # --keep-directory-symlink  follows existing symlinks to directories instead
    #                           of replacing them (e.g., /var/run -> /run)
    tar -C "${dest}" -cf - . \
        | tar -C / -xf - --no-overwrite-dir --keep-directory-symlink

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Deploy failed for ${name}-${version}"
        return 1
    fi

    # Setuid/setgid/sticky safety-net. The tar pipeline above SHOULD preserve
    # these bits when running as root. Empirically (May 16 2026 verification)
    # it does. But the May 12 2026 deploy of polkit/util-linux/shadow/sudo
    # dropped setuid on every binary they ship (pkexec, su, sudo, mount,
    # umount, passwd, chfn, chsh, newgrp, gpasswd, chage, expiry,
    # newuidmap, newgidmap), discovered when Forge GUI elevation failed in
    # the cycle-2 smoke test with "pkexec must be setuid root". Root cause
    # not pinpointed to a specific stripping operation. This loop closes the
    # loop regardless: re-applies any setuid/setgid/sticky bit present in
    # the package's staging directory to the deployed file. Idempotent —
    # if the tar pipeline preserved correctly, this is a no-op.
    while IFS= read -r -d '' staged_file; do
        local rel="${staged_file#${dest}}"
        local deployed_file="/${rel}"
        if [ -f "$deployed_file" ]; then
            local staged_mode
            staged_mode=$(stat -c '%a' "$staged_file" 2>/dev/null || echo "")
            if [ -n "$staged_mode" ] && [ "${#staged_mode}" -eq 4 ]; then
                # 4-digit mode means setuid/setgid/sticky present in high bit
                local deployed_mode
                deployed_mode=$(stat -c '%a' "$deployed_file" 2>/dev/null || echo "")
                if [ "$deployed_mode" != "$staged_mode" ]; then
                    chmod "$staged_mode" "$deployed_file"
                    pkg_log "  setuid-restore: ${deployed_file} -> ${staged_mode}"
                fi
            fi
        fi
    done < <(find "$dest" -type f \( -perm -4000 -o -perm -2000 -o -perm -1000 \) -print0)

    pkg_log "Deployed ${name}-${version}"
    return 0
}

# ============================================================================
# pkg_cleanup — Remove staging directory after successful install
#
# Usage: pkg_cleanup <name> <version>
# ============================================================================

pkg_cleanup() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"

    rm -rf "$dest"
}

# ============================================================================
# pkg_run_tests — Run a test suite under the project's allow-list policy.
#
# Usage: pkg_run_tests <package.yml> <test_cmd> [args...]
#
# Reads the optional `tests:` block from the given package.yml:
#
#   tests:
#     enabled: true                        # default; false = skip phase
#     failure_policy: strict|known_failures # default strict; halts on any fail
#     reason: "..."                        # required when enabled=false or
#                                          #   failure_policy=known_failures
#
# Behavior:
#   - No `tests:` block → strict mode (any test failure halts).
#   - tests.enabled=false → skip silently with a log line. Reason required.
#   - failure_policy=strict (default) → run command, halt on non-zero exit.
#   - failure_policy=known_failures → run command, log a warning on non-zero
#     exit but return 0. Reason required and printed to log.
#
# Spec: docs/test-allow-list.md
# Adopted: 2026-05-08 after Build #5 audit.
# ============================================================================

pkg_run_tests() {
    local pkg_yml="$1"
    shift
    local cmd=("$@")

    if [ ! -f "$pkg_yml" ]; then
        echo "[tests] ERROR: package.yml not found at $pkg_yml" >&2
        return 1
    fi

    # Parse the tests: block. We only honor exact keys at indent level 2,
    # under a top-level 'tests:' key. This matches the rest of the project's
    # bash-friendly YAML conventions (no full YAML parser).
    local enabled policy reason
    enabled=$(awk '/^tests:[[:space:]]*$/{f=1; next} /^[A-Za-z_]+:/{f=0} f && /^[[:space:]]+enabled:[[:space:]]*/{sub(/^[[:space:]]+enabled:[[:space:]]*/,""); gsub(/[[:space:]]+$/,""); print; exit}' "$pkg_yml")
    policy=$(awk '/^tests:[[:space:]]*$/{f=1; next} /^[A-Za-z_]+:/{f=0} f && /^[[:space:]]+failure_policy:[[:space:]]*/{sub(/^[[:space:]]+failure_policy:[[:space:]]*/,""); gsub(/[[:space:]]+$/,""); print; exit}' "$pkg_yml")
    reason=$(awk '/^tests:[[:space:]]*$/{f=1; next} /^[A-Za-z_]+:/{f=0} f && /^[[:space:]]+reason:[[:space:]]*/{sub(/^[[:space:]]+reason:[[:space:]]*/,""); gsub(/^"/,""); gsub(/"$/,""); print; exit}' "$pkg_yml")

    enabled="${enabled:-true}"
    policy="${policy:-strict}"

    if [ "$enabled" = "false" ]; then
        if [ -z "$reason" ]; then
            echo "[tests] ERROR: tests.enabled=false but no reason given in $pkg_yml" >&2
            return 1
        fi
        echo "[tests] phase skipped (enabled=false). Reason: $reason"
        return 0
    fi

    if [ "$policy" != "strict" ] && [ "$policy" != "known_failures" ]; then
        echo "[tests] ERROR: invalid failure_policy '$policy' in $pkg_yml (expected strict|known_failures)" >&2
        return 1
    fi

    if [ "$policy" = "known_failures" ] && [ -z "$reason" ]; then
        echo "[tests] ERROR: failure_policy=known_failures requires a reason in $pkg_yml" >&2
        return 1
    fi

    echo "[tests] policy=$policy"
    [ -n "$reason" ] && echo "[tests] reason: $reason"
    echo "[tests] running: ${cmd[*]}"

    # The `|| rc=$?` form keeps the policy-check below reachable when this
    # function is called from a context with `set -e` (errexit) active —
    # which is the norm: chroot-build-{ch8,core-extra}.sh, every package
    # build.sh's check() function, and the Python-builder's bash shell all
    # set -e. Without `||`, errexit would kill pkg_run_tests at the
    # `"${cmd[@]}"` line on test-command failure, BEFORE the
    # `failure_policy=known_failures` branch could suppress it. Build #6
    # Halt #5 (FLAC, exit 2) was that exact failure mode.
    local rc=0
    "${cmd[@]}" || rc=$?

    if [ $rc -eq 0 ]; then
        echo "[tests] PASSED"
        return 0
    fi

    if [ "$policy" = "known_failures" ]; then
        echo "[tests] WARNING: test suite exit $rc — allowed by failure_policy=known_failures"
        echo "[tests] WARNING reason: $reason"
        return 0
    fi

    echo "[tests] FAILED (exit $rc) — strict policy, halting build" >&2
    return $rc
}

# ============================================================================
# pkg_install — Full pipeline: stage -> manifest -> archive -> deploy -> cleanup
#
# Usage: pkg_install <name> <version> [description]
#
# This is the main entry point called by the build runner after
# configure/build/check have completed.
# ============================================================================

pkg_install() {
    local name="$1"
    local version="$2"
    local description="${3:-No description}"

    pkg_log "=========================================="
    pkg_log "Installing package: ${name}-${version}"
    pkg_log "=========================================="

    local start
    start=$(date +%s)

    # Ensure database directories exist
    pkg_init

    # Stage
    pkg_stage "$name" "$version" || return 1

    # Generate manifest
    pkg_manifest "$name" "$version" "$description" || return 1

    # Create archive
    pkg_archive "$name" "$version" || return 1

    # Deploy to live filesystem
    pkg_deploy "$name" "$version" || return 1

    # Clean up staging directory
    pkg_cleanup "$name" "$version"

    local elapsed=$(( $(date +%s) - start ))
    pkg_log "Package ${name}-${version} installed successfully (${elapsed}s)"
    pkg_log ""

    return 0
}

# ============================================================================
# pkg_info — Display information about an installed package
#
# Usage: pkg_info <name>-<version>
#    or: pkg_info (no args — list all installed packages)
# ============================================================================

pkg_info() {
    if [ -z "$1" ]; then
        # List all installed packages
        if [ -d "$IGOS_PKG_DB" ]; then
            for manifest in "$IGOS_PKG_DB"/*; do
                [ -f "$manifest" ] || continue
                local pkg_name pkg_version
                pkg_name=$(grep "^PACKAGE NAME:" "$manifest" | cut -d: -f2- | tr -d ' ')
                pkg_version=$(grep "^PACKAGE VERSION:" "$manifest" | cut -d: -f2- | tr -d ' ')
                local desc
                desc=$(grep "^${pkg_name%%"-$pkg_version"}:" "$manifest" | head -1)
                echo "${pkg_name}  ${desc:+— $desc}"
            done
        else
            echo "No packages installed."
        fi
    else
        # Show specific package
        local manifest="${IGOS_PKG_DB}/$1"
        if [ -f "$manifest" ]; then
            cat "$manifest"
        else
            echo "Package $1 is not installed."
            return 1
        fi
    fi
}

# ============================================================================
# pkg_files — List files owned by an installed package
#
# Usage: pkg_files <name>-<version>
# ============================================================================

pkg_files() {
    local manifest="${IGOS_PKG_DB}/$1"
    if [ ! -f "$manifest" ]; then
        echo "Package $1 is not installed."
        return 1
    fi

    # Extract file list (everything after "FILE LIST:" line)
    sed -n '/^FILE LIST:$/,$ { /^FILE LIST:$/d; p }' "$manifest"
}

# ============================================================================
# pkg_owner — Find which package owns a file
#
# Usage: pkg_owner /usr/bin/gcc
# ============================================================================

pkg_owner() {
    local target="$1"

    # Strip leading / for comparison against manifest paths
    target="${target#/}"

    if [ -d "$IGOS_PKG_DB" ]; then
        for manifest in "$IGOS_PKG_DB"/*; do
            [ -f "$manifest" ] || continue
            if sed -n '/^FILE LIST:$/,$ p' "$manifest" | grep -qx "$target"; then
                basename "$manifest"
            fi
        done
    fi
}

# ============================================================================
# pkg_remove — Remove an installed package
#
# Usage: pkg_remove <name>-<version>
#
# Removes all files owned by the package (in reverse order so dirs come last),
# then removes the manifest. Does NOT remove the archive.
# ============================================================================

pkg_remove() {
    local pkg="$1"
    local manifest="${IGOS_PKG_DB}/${pkg}"

    if [ ! -f "$manifest" ]; then
        pkg_error "Package ${pkg} is not installed."
        return 1
    fi

    pkg_log "Removing package: ${pkg}"

    # Get file list, reverse sorted (files before their parent directories)
    local files
    files=$(pkg_files "$pkg" | sort -r)

    local removed=0
    local skipped=0

    while IFS= read -r file; do
        [ -z "$file" ] && continue
        local fullpath="/${file}"

        if [ -d "$fullpath" ] && [ ! -L "$fullpath" ]; then
            # Only remove directory if empty
            rmdir "$fullpath" 2>/dev/null && removed=$((removed+1))
        elif [ -e "$fullpath" ] || [ -L "$fullpath" ]; then
            rm -f "$fullpath" && removed=$((removed+1))
        else
            skipped=$((skipped+1))
        fi
    done <<< "$files"

    # Remove the manifest
    rm -f "$manifest"

    pkg_log "Removed ${pkg}: ${removed} files/dirs removed, ${skipped} already absent"
    return 0
}
