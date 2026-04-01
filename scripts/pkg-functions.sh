#!/bin/bash
# InterGenOS Package Functions — DESTDIR Staging + Slackware-style Tracking
#
# Sourced by the Chapter 8 build runner. Provides functions to:
#   1. Stage a package's installed files via DESTDIR
#   2. Generate a file manifest
#   3. Create a compressed archive (.igos.tar.zst)
#   4. Deploy staged files to the live filesystem
#   5. Run post-install hooks on the live system
#
# Database: /var/lib/igos/packages/<name>-<version>  (one text file per package)
# Archives: /var/lib/igos/archives/<name>-<version>.igos.tar.zst
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

    # Export DESTDIR for autotools/meson packages
    export DESTDIR="$PKG_DEST"

    pkg_log "Staging ${name}-${version} to ${PKG_DEST}"

    # Run the package's do_install function
    # Named do_install (not install) to avoid collision with /usr/bin/install.
    # Output goes directly to a log file (not a pipe) to prevent
    # child processes from blocking on a full pipe buffer.
    local install_log="${IGOS_LOGS}/${name}-install.log"

    if type -t do_install | grep -q function 2>/dev/null; then
        do_install > "$install_log" 2>&1
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
# ============================================================================

pkg_deploy() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    pkg_log "Deploying ${name}-${version} to live filesystem"

    # cp -a preserves permissions, ownership, timestamps, symlinks
    # --remove-destination handles the case where a file is being replaced
    # (important for shared libraries — avoids crashing running processes)
    cp -a --remove-destination "${dest}/." /

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Deploy failed for ${name}-${version}"
        return 1
    fi

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
