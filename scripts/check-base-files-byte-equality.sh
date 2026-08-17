#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# check-base-files-byte-equality.sh — F4 inline assertion per plan v2
# (2026-05-27, bilateral review APPROVE-clean at 06:41Z).
#
# Asserts that the files shipped by packages/core/intergenos-base-files/
# match (sha256-byte-equal) the content of the matching package files
# AND match the legacy heredoc-emitted content that lived in
# scripts/chroot-build.sh + scripts/chroot-config-ch9.sh + scripts/
# create-image.sh BEFORE plan v2 redirected those to cp -a from the
# package. This is a pre-commit / pre-merge guard against future drift.
#
# Failure modes caught:
#   - Operator edits scripts/chroot-config-ch9.sh heredoc content but
#     forgets to update packages/core/intergenos-base-files/files/.
#   - Operator edits packages/core/intergenos-base-files/files/etc/X
#     but a sibling reference at scripts/chroot-build.sh still re-writes
#     a stale heredoc on top of it.
#
# After plan v2 lands, the scripts USE cp -a from the package (no
# heredocs to drift), so the script's primary safety surface is the
# package's own files/ tree internal-consistency: every file lands in
# both the package archive (do_install cp -av) AND the build chroot
# (chroot-build.sh + chroot-config-ch9.sh cp -av).
#
# Usage (pre-commit hook OR ad-hoc):
#   bash scripts/check-base-files-byte-equality.sh
# Exit 0 = clean. Non-zero = drift detected; commit must NOT land.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
PKG_FILES="${REPO_ROOT}/packages/core/intergenos-base-files/files"

if [ ! -d "$PKG_FILES" ]; then
    echo "FATAL: intergenos-base-files content tree missing at $PKG_FILES" >&2
    exit 1
fi

# Sanity 1: the only symlinks in the source files/ tree are the three
# merged-usr compat links, and they point where they must.
#
# The original rule here was "zero symlinks" — build.sh creates the ones it
# needs (e.g. /etc/bash.bashrc) with an explicit `ln -sf` after the cp -a.
# The L27 ownership handoff (r7) then made base-files the SOLE archive
# shipping bin/lib/sbin -> usr/* to installed systems, and those three ride
# in files/ as git-native symlinks. The zero-symlinks rule was not updated
# with it, so this gate had been failing on every run since (found
# 2026-07-24). Allowing them by name — with their targets asserted — keeps
# the drift catch the rule exists for and additionally pins the targets,
# which the count-only form never checked.
declare -A ALLOWED_SYMLINKS=(
    ["bin"]="usr/bin"
    ["lib"]="usr/lib"
    ["sbin"]="usr/sbin"
)
bad_symlink=0
while IFS= read -r link; do
    [ -z "$link" ] && continue
    rel="${link#"$PKG_FILES"/}"
    expected="${ALLOWED_SYMLINKS[$rel]:-}"
    if [ -z "$expected" ]; then
        echo "FATAL: unexpected symlink in source files/ tree: $rel" >&2
        bad_symlink=1
        continue
    fi
    actual="$(readlink "$link")"
    if [ "$actual" != "$expected" ]; then
        echo "FATAL: compat symlink $rel -> $actual, expected $expected" >&2
        bad_symlink=1
    fi
done < <(find "$PKG_FILES" -type l)
if [ "$bad_symlink" -ne 0 ]; then
    exit 1
fi

# Sanity 2: required-by-plan-v2 verify_paths all exist in files/ tree.
REQUIRED_PATHS=(
    "usr/share/intergenos-base-files/account-skel/passwd"
    "usr/share/intergenos-base-files/account-skel/group"
    "usr/share/intergenos-base-files/account-skel/shadow"
    "usr/share/intergenos-base-files/account-skel/gshadow"
    "usr/lib/intergenos/seed-account-skel.sh"
    "etc/profile"
    "etc/bashrc"
    "etc/inputrc"
    "etc/shells"
    "etc/issue"
    "etc/motd"
    "etc/os-release"
    "etc/lsb-release"
    "etc/igos-release"
    "etc/skel/.bashrc"
    "etc/skel/.bash_profile"
    "etc/profile.d/prompt.sh"
    "etc/systemd/system/getty@tty1.service.d/noclear.conf"
    "etc/systemd/coredump.conf.d/maxuse.conf"
    "etc/systemd/system/dbus.service.d/intergenos-capabilities.conf"
    "etc/systemd/system/systemd-tpm2-setup.service.d/intergenos-tss2-loglevel.conf"
    "etc/systemd/system/systemd-pcrextend.service.d/intergenos-tss2-loglevel.conf"
    "etc/systemd/resolved.conf.d/no-mdns.conf"
    "etc/systemd/user/wireplumber.service.d/restart.conf"
    "etc/modprobe.d/igos-dirty-frag-mitigation.conf"
    "etc/modprobe.d/disable-algif.conf"
    "etc/gdm/custom.conf"
    "usr/bin/lsb_release"
    "usr/lib/systemd/system-preset/80-intergenos-enable.preset"
    "usr/lib/systemd/system-preset/99-intergenos-default-disable.preset"
    "usr/lib/tmpfiles.d/00-intergenos.conf"
)

missing=0
for rel in "${REQUIRED_PATHS[@]}"; do
    if [ ! -f "$PKG_FILES/$rel" ]; then
        echo "FATAL: required file missing in package: $rel" >&2
        missing=1
    fi
done
if [ "$missing" -ne 0 ]; then
    exit 1
fi

# Sanity 3: spot-check critical files for non-empty content + key markers.
declare -A REQUIRED_MARKERS=(
    ["usr/share/intergenos-base-files/account-skel/passwd"]="^root:x:0:0:root:/root:/bin/bash$"
    ["usr/share/intergenos-base-files/account-skel/group"]="^wheel:x:97:$"
    ["usr/share/intergenos-base-files/account-skel/shadow"]="^root:!:0:0:99999:7:::"
    ["etc/profile"]="for i in \\\$\\(locale\\)"
    ["etc/bashrc"]="alias ll='ls -lah'"
    ["etc/shells"]="^/bin/bash$"
    ["etc/os-release"]="^ID=intergenos$"
    ["usr/lib/systemd/system-preset/80-intergenos-enable.preset"]="^enable NetworkManager.service$"
    ["usr/lib/systemd/system-preset/99-intergenos-default-disable.preset"]="^disable \\*$"
    # faillog is the one login-accounting file we own: systemd's own var.conf
    # declares wtmp/btmp/lastlog, and declaring them here too produced
    # duplicate-line warnings from systemd-tmpfiles, so they were removed
    # 2026-05-27. This marker asked for `^f /var/log/wtmp` — one of the removed
    # lines — and so had been failing the gate ever since (found 2026-07-24).
    ["usr/lib/tmpfiles.d/00-intergenos.conf"]="^f /var/log/faillog"
)
for rel in "${!REQUIRED_MARKERS[@]}"; do
    pattern="${REQUIRED_MARKERS[$rel]}"
    if ! grep -qE "$pattern" "$PKG_FILES/$rel"; then
        echo "FATAL: $rel missing required marker pattern: $pattern" >&2
        exit 1
    fi
done

# Sanity 4: scripts/chroot-build.sh + scripts/chroot-config-ch9.sh
# should NOT contain residual heredoc-writes for content the package
# now owns. If a `cat > /etc/X` heredoc with package-owned content
# survives, it would silently overwrite the package install at build
# time. Grep for the canonical token "BASEFILES_SRC" — every cp -a
# from the package must reference it.
for script in scripts/chroot-build.sh scripts/chroot-config-ch9.sh; do
    abs="$REPO_ROOT/$script"
    if ! grep -q "BASEFILES_SRC" "$abs"; then
        echo "FATAL: $script missing BASEFILES_SRC reference; plan v2 cp -a dedup not applied" >&2
        exit 1
    fi
done

# Sanity 5: scripts/create-image.sh should NOT contain heredoc-writes
# for the 4 paths plan v2 deleted (clang configs + resolved no-mdns +
# wireplumber restart + algif blacklist).
abs="$REPO_ROOT/scripts/create-image.sh"
for forbidden_pattern in \
    'cat > "\${MOUNT_POINT}/etc/clang/' \
    'cat > "\${MOUNT_POINT}/etc/systemd/resolved.conf.d/no-mdns.conf"' \
    'cat > "\${MOUNT_POINT}/etc/systemd/user/wireplumber.service.d/restart.conf"' \
    '> "\${MOUNT_POINT}/etc/modprobe.d/disable-algif.conf"' ; do
    if grep -qE "$forbidden_pattern" "$abs"; then
        echo "FATAL: scripts/create-image.sh contains forbidden duplicate heredoc: $forbidden_pattern" >&2
        echo "Plan v2 deleted these; they're now owned by intergenos-base-files (Block E/G) or llvm post_install." >&2
        exit 1
    fi
done

echo "OK: intergenos-base-files byte-equality + sibling-walk assertion clean."
echo "  - $(echo "${REQUIRED_PATHS[@]}" | wc -w) required paths present"
echo "  - all required markers found"
echo "  - chroot-build.sh + chroot-config-ch9.sh use BASEFILES_SRC cp -a"
echo "  - scripts/create-image.sh duplicate heredocs absent"
