#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# check-license-bundle.sh — verify every installed package has a license bundle
#
# K21.B audit hook: walk all packages registered in pkm's SQLite database and
# verify each has at least one file under /usr/share/licenses/<package-name>/
# inside the target chroot. Per FHS + the K21 legal-readiness sprint scope.
#
# Built to be wired into phase_squashfs (in scripts/build-intergenos.sh) as a
# Class-A-style pre-squashfs verification. Until that wiring lands, this
# script is invokable manually:
#
#     bash scripts/check-license-bundle.sh /mnt/igos
#     bash scripts/check-license-bundle.sh /  # check the running system
#
# Exit codes:
#   0  every installed package has a license bundle
#   1  one or more packages have no license bundle (offenders printed to stderr)
#   2  arguments invalid OR pkm database not found at the expected location
#
# What "license bundle" means here:
#   /usr/share/licenses/<package-name>/ exists AND contains at least one file
#   (any file — LICENSE, COPYING, COPYRIGHT, NOTICE, vendor-specific names).
#   The igos-build/builder.py bundle_license() hook stages these at install
#   time. Packages with vendored upstream-licenses landed via their own
#   build.sh do_install logic should also pass (the hook is no-op when files
#   already exist).
#
# Composes with:
#   - igos-build/builder.py bundle_license() — the producer side
#   - packages/core/pkm/ — the installation-registry side
#   - K21 legal-readiness sprint (docs/audit/2026-05-18-design-decisions-matrix.md
#     Lane P entries P-004 + P-010 + P-019 + P-025)
#   - D-006 SSoT for gnome-extension licenses (P-026; handled by
#     intergenos-default-settings gschema-override package, not by this audit)
#
# Author: build-system coordinator, 2026-05-21, K21.B implementation.

set -euo pipefail

TARGET_ROOT="${1:-/mnt/igos}"

if [ ! -d "$TARGET_ROOT" ]; then
    echo "error: target root not found: $TARGET_ROOT" >&2
    echo "usage: $0 [target_root]   (default: /mnt/igos)" >&2
    exit 2
fi

# pkm's canonical database path is /var/lib/igos/pkm.db, set by
# pkm/database.py:17 (`DB_PATH = Path(os.environ.get("IGOS_PKM_DB",
# "/var/lib/igos/pkm.db"))`). This gate script previously hardcoded
# /var/lib/pkm/pkm.db, which never matched the canonical location;
# surfaced 2026-05-24 as a phase_squashfs halt when the gate finally
# fired post-T0-7 wiring at scripts/build-intergenos.sh:1449.
PKM_DB="${TARGET_ROOT}/var/lib/igos/pkm.db"
if [ ! -f "$PKM_DB" ]; then
    echo "error: pkm database not found at $PKM_DB" >&2
    echo "       check-license-bundle.sh expects an InterGenOS install layout" >&2
    exit 2
fi

LICENSES_DIR="${TARGET_ROOT}/usr/share/licenses"
if [ ! -d "$LICENSES_DIR" ]; then
    echo "error: licenses dir not found at $LICENSES_DIR" >&2
    echo "       no packages have been license-bundled yet" >&2
    exit 1
fi

# Enumerate installed packages via pkm SQLite.
# Use the CHROOT's sqlite3 binary (always present — sqlite is a core
# package) instead of the host's. build-intergenos.sh runs on the build
# VM which may not have sqlite3 installed (Ubuntu 24.04 minimal images
# don't ship it). Path inside the chroot is /var/lib/igos/pkm.db
# (canonical, see pkm/database.py:17); outside it's $TARGET_ROOT/var/lib/igos/pkm.db.
#
# Schema note: pkm tracks installed packages in the `installed` table
# (not a `packages` table with an `installed=1` column — the row's
# presence IS the install signal). See pkm/database.py for the canonical
# schema. The previous query "SELECT name FROM packages WHERE installed
# = 1" was authored against an obsolete schema model and never actually
# fired (this gate didn't run end-to-end until 2026-05-24).
INSTALLED_PACKAGES=$(chroot "$TARGET_ROOT" sqlite3 /var/lib/igos/pkm.db \
    "SELECT name FROM installed ORDER BY name;" 2>&1) || {
    echo "error: pkm database query failed" >&2
    echo "$INSTALLED_PACKAGES" >&2
    exit 2
}

TOTAL=0
MISSING=0
MISSING_LIST=""

while IFS= read -r pkg_name; do
    [ -z "$pkg_name" ] && continue
    TOTAL=$((TOTAL + 1))

    pkg_license_dir="${LICENSES_DIR}/${pkg_name}"

    if [ ! -d "$pkg_license_dir" ]; then
        MISSING=$((MISSING + 1))
        MISSING_LIST="${MISSING_LIST}\n  ${pkg_name} — no /usr/share/licenses/${pkg_name}/ directory"
        continue
    fi

    # Verify at least one regular file exists in the package's license dir.
    if [ -z "$(find "$pkg_license_dir" -mindepth 1 -maxdepth 2 -type f -print -quit 2>/dev/null)" ]; then
        MISSING=$((MISSING + 1))
        MISSING_LIST="${MISSING_LIST}\n  ${pkg_name} — directory exists but has no license files"
    fi
done <<< "$INSTALLED_PACKAGES"

PRESENT=$((TOTAL - MISSING))

echo "License-bundle audit against ${TARGET_ROOT}:"
echo "  Total installed packages: ${TOTAL}"
echo "  License bundle present:   ${PRESENT}"
echo "  License bundle missing:   ${MISSING}"

if [ "$MISSING" -gt 0 ]; then
    echo "" >&2
    echo "Packages without license bundles (K21.B audit FAIL):" >&2
    printf "%b\n" "$MISSING_LIST" >&2
    echo "" >&2
    echo "Resolution paths:" >&2
    echo "  1. Verify upstream source has LICENSE/COPYING/COPYRIGHT/NOTICE files; if so, the builder.py bundle_license() hook should have caught them automatically. Re-run the package build to re-stage." >&2
    echo "  2. If upstream has no explicit license files but SPDX is declared in package.yml, author a minimal LICENSE-by-SPDX file (citing the SPDX text and package.yml as the source-of-record) in the package's assets/ tree and stage it via build.sh do_install." >&2
    echo "  3. For first-party InterGenOS packages, ensure the package ships a LICENSE file in the source tree or its assets." >&2
    exit 1
fi

echo "All ${TOTAL} installed packages have license bundles. K21.B audit PASS."
exit 0
