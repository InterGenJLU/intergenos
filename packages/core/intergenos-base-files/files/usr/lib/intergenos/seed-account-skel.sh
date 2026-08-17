#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# seed-account-skel.sh — create the account databases from the shipped
# skeleton, ONLY where they do not already exist.
#
# Why this exists (decided 2026-07-24): intergenos-base-files used to ship
# /etc/{passwd,group,shadow,gshadow} as ordinary archive payload. That made
# the pristine skeleton deploy-target bytes, and on a system where the
# package had never been installed the deploy wrote the skeleton straight
# over live account databases — every real account row lost. pkm's config
# protection now refuses that overwrite, but the durable fix is that the
# bytes are not aimed at /etc in the first place: the skeleton ships as
# reference data under /usr/share, and reaching /etc requires this explicit,
# create-only step.
#
# The contract, and the whole reason the script is small: it CREATES a
# missing database and NEVER touches one that exists. There is no merge, no
# update, no "repair" mode. A file that is present is the system's own
# account state and is not ours to modify — the caller that wants to change
# accounts uses useradd/groupadd/chpasswd, which understand the format.
#
# Called from:
#   - pkm's canonical account-skel-seed hook, immediately ahead of the first
#     systemd-sysusers run on the root being installed to. This is the
#     effective caller on any target that receives packages: sysusers creates
#     the databases itself from its declared entries, and since this script
#     never rewrites a database that exists, anything downstream of that run
#     can only report what it found.
#   - the installer's config phase, against a freshly-populated target root,
#     as an idempotent belt and because account databases must exist before
#     useradd --root runs.
# The build chroot does not run this script (scripts/chroot-build.sh copies
# the skeleton directly), and the package's own post_install does not call it:
# the canonical hook already covers the install path, and on a running system
# the databases exist.
# Idempotent: on any system that already has the databases (which is every
# system in service) this is a no-op that reports what it found.
#
# Usage: seed-account-skel.sh [--root <dir>] [--quiet]
# Exit:  0 = every database present (seeded or already there)
#        1 = a database is missing and could not be created

set -uo pipefail

SKEL_DIR="usr/share/intergenos-base-files/account-skel"
DATABASES=(passwd group shadow gshadow)

ROOT="/"
QUIET=0
while [ $# -gt 0 ]; do
    case "$1" in
        --root) ROOT="${2:?--root needs a directory}"; shift 2 ;;
        --quiet) QUIET=1; shift ;;
        *) echo "seed-account-skel.sh: unknown argument: $1" >&2; exit 1 ;;
    esac
done

say() { [ "$QUIET" -eq 1 ] || echo "$@"; }

# Modes match what the databases must have on a live system. shadow and
# gshadow are 0640 root:shadow-readable; passwd and group are world-readable.
mode_for() {
    case "$1" in
        shadow|gshadow) echo 0640 ;;
        *) echo 0644 ;;
    esac
}

skel_root="${ROOT%/}/${SKEL_DIR}"
if [ ! -d "$skel_root" ]; then
    echo "FATAL: account skeleton missing at ${skel_root}" >&2
    echo "  intergenos-base-files ships it; a target without it is incomplete." >&2
    exit 1
fi

seeded=0
kept=0
failed=0

for db in "${DATABASES[@]}"; do
    src="${skel_root}/${db}"
    dest="${ROOT%/}/etc/${db}"

    if [ ! -f "$src" ]; then
        echo "FATAL: skeleton file missing: ${src}" >&2
        failed=1
        continue
    fi

    if [ -e "$dest" ]; then
        # Present — the system's own account state. Never touched.
        kept=$((kept + 1))
        say "  /etc/${db}: present, left untouched"
        continue
    fi

    if ! install -D -m "$(mode_for "$db")" -o 0 -g 0 "$src" "$dest" 2>/dev/null; then
        # Ownership flags fail for an unprivileged caller; the content still
        # matters more than the uid, so retry without them and say so.
        if install -D -m "$(mode_for "$db")" "$src" "$dest"; then
            say "  /etc/${db}: seeded from skeleton (ownership not set — not running as root)"
            seeded=$((seeded + 1))
        else
            echo "FATAL: could not seed /etc/${db} from ${src}" >&2
            failed=1
        fi
        continue
    fi
    seeded=$((seeded + 1))
    say "  /etc/${db}: seeded from skeleton"
done

if [ "$failed" -ne 0 ]; then
    echo "seed-account-skel.sh: FAILED — one or more account databases are absent." >&2
    exit 1
fi

say "account databases: ${seeded} seeded, ${kept} already present"
exit 0
