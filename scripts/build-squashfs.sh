#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# build-squashfs.sh — build the live-ISO root filesystem squashfs from the
# InterGenOS chroot at /mnt/igos.
#
# Real-distro lineage:
#   - archiso's `_build_iso_squashfs` + `airootfs/root/customize_airootfs.sh`
#   - debian-live's `lb_binary_rootfs`
#   - lorax's livemedia-creator template chain
#
# The flow:
#   1. Mount /proc /sys /dev /run inside chroot
#   2. Run customize-airootfs hooks inside chroot (CA trust, caches, presets,
#      ldconfig, schema/icon/desktop databases) — this is where the systemd
#      presets we ship (90-gdm.preset, 90-nftables.preset, ...) get activated
#      via `systemctl preset-all`, creating the display-manager symlink and
#      the .wants/ links for the rest.
#   3. Clean runtime trash (logs, tmp, caches, machine-id reset)
#   4. Unmount chroot pseudo-fs
#   5. mksquashfs with content-exclusions that PRESERVE empty mount-point
#      directories (/sys /proc /dev /run /tmp as empty dirs in the output)
#
# Usage:
#   sudo ./build-squashfs.sh
#
# Optional env:
#   CHROOT=/mnt/igos              # path to the InterGenOS chroot
#   OUTPUT=<chroot>/mnt/intergenos/build/filesystem.squashfs
#   COMP=zstd                     # mksquashfs compressor (zstd-19, see below)
#   JOBS=$(nproc)                 # parallel mksquashfs workers
#   SKIP_CUSTOMIZE=0              # set 1 to skip the customize-airootfs hooks
#   SOURCE_DATE_EPOCH=<unix>      # reproducible-build epoch (falls back to now)
#
# Idempotency notes:
#   - Safe to re-run. Mounts are guarded with `mountpoint -q`. Customize hooks
#     are idempotent (the tools they invoke handle re-runs).
#   - The unmount step uses `umount -l` (lazy) defensively in case anything
#     inside the chroot is still holding a reference.

set -euo pipefail

CHROOT="${CHROOT:-/mnt/igos}"
OUTPUT="${OUTPUT:-${CHROOT}/mnt/intergenos/build/filesystem.squashfs}"
# Compressor: zstd at level 19 (GBC001.5, 2026-06-05). Replaced xz, which was
# an early ratio-pick (2026-04) never ratified. Live research on the GBC001.4
# boot proved xz squashfs DECOMPRESSION is the systemic boot bottleneck: USB
# 3.0 raw read 149 MB/s vs xz-decompressed file reads 37.9 MB/s (4x slower,
# ~0% iowait, CPU unsaturated — single-stream xz per-block latency). EVERY
# boot file read pays it. zstd-19 gives near-xz ratio with multi-threaded,
# far-faster decompression. Kernel already supports it (CONFIG_SQUASHFS_ZSTD=y
# + CONFIG_SQUASHFS_DECOMP_MULTI_PERCPU=y) so NO kernel rebuild is needed.
COMP="${COMP:-zstd}"
JOBS="${JOBS:-$(nproc)}"
SKIP_CUSTOMIZE="${SKIP_CUSTOMIZE:-0}"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(date +%s)}"

LOG_PREFIX="[build-squashfs]"

# Source the forensic-trace bash companion (no-op when verbose unset).
# This script already uses multiple EXIT traps (cleanup_mounts +
# rm-temp-files) that are reset throughout the flow, so we don't add
# ANOTHER EXIT trap here — instead the squashfs_phase_end event is
# emitted explicitly at the script's natural success boundary at end-
# of-script. die() also emits a build_failure event before exit.
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "host-build-squashfs"
    _SQ_START_MS=$(date +%s%3N)
    trace_event squashfs_phase_start chroot="$CHROOT" output="$OUTPUT" comp="$COMP" jobs="$JOBS"
fi

# Shared build-output library — one house style across the shell pipeline.
# We keep build-squashfs's own log()/warn()/die() wrappers (they carry the
# squashfs trace mirror + the build_failure_emit on die) but adopt the shared
# severity voice (warning:/error:) instead of the old [WARN]/[FATAL] tags.
# shellcheck source=lib/logging.sh
[ -f /mnt/intergenos/scripts/lib/logging.sh ] && source /mnt/intergenos/scripts/lib/logging.sh

# --- Log restructure (decided 2026-07-04; format 2026-07-07) --------
# The operator's ratified concept (screenshot 2026-07-07 19:09):
#   [build-squashfs] [2/6]                          <- SECTION separator line
#   [2026-07-07 18:41:21] running customize-airootfs hooks inside chroot...
#   [build-squashfs] [2/6] [airootfs]               <- sub-tool sub-section
#   [2026-07-07 18:41:21] CA bundle present:
#                         /etc/ssl/certs/ca-certificates.crt (227446 bytes)
#   [build-squashfs] [2/6]
#   [2026-07-07 18:41:21] customize-airootfs hooks .................. :DONE
# Rules: a section line ([build-squashfs] + step tag [+ sub-tool tag]) is
# emitted whenever the section CHANGES; content lines carry only the
# timestamp; text WORD-WRAPS just before the status column, continuations
# aligned under the text column; detail lines render continuation-style
# (indented, no timestamp); every completed action gets the right-aligned
# :DONE/:PASS/:FAIL/:SKIP verdict via status_line(). Every sub-tool's output
# flows through the ONE logpipe() chokepoint (own sub-section tag) — tools
# are not edited individually.
# Spacing knobs (operator-tunable): STATUS_COL = absolute column where the
# ":STATUS" token lands; STATUS_LEADER = the fill character.
STATUS_COL="${STATUS_COL:-74}"
STATUS_LEADER="${STATUS_LEADER:-.}"
TEXT_COL=22                      # width of "[YYYY-MM-DD HH:MM:SS] "
WRAP_COL=$(( STATUS_COL - 2 ))   # text never extends past here

# lastpipe: the final element of a pipeline (logpipe) runs in THIS shell, so
# the section-dedupe state survives `cmd | logpipe` — without it a sub-tool's
# sub-section line could mis-attribute the host's next line. Job control is
# off in a non-interactive script, so lastpipe is effective.
shopt -s lastpipe

_ts() { date '+%Y-%m-%d %H:%M:%S'; }

# Section state: _SEC_CUR is the current step tag (e.g. "[4.5/6]"), set by
# step_begin; _SEC_PRINTED is the last section line actually emitted.
_SEC_CUR=""
_SEC_PRINTED="__none__"
step_begin() { _SEC_CUR="${1:-}"; return 0; }
_sec_render() {   # $1 = effective section tag ("" = bare [build-squashfs])
    [ "$1" = "$_SEC_PRINTED" ] && return 0
    _SEC_PRINTED="$1"
    printf '%s%s\n' "$LOG_PREFIX" "${1:+ $1}"
}
# _wrap_emit <first-prefix> <cont-indent> <text> — greedy word-wrap so text
# never extends past WRAP_COL; continuation lines get <cont-indent> spaces
# and no timestamp. A single token longer than the field (a path, a sha256)
# is NEVER split — it overflows the boundary whole, because a mid-token
# break makes the log un-greppable for exactly the values that matter.
_wrap_emit() {
    local first_prefix="$1" cont_indent="$2" text="$3"
    local width=$(( WRAP_COL - cont_indent ))
    [ "$width" -lt 20 ] && width=20
    # Short lines pass through VERBATIM (preserves deliberate internal
    # alignment, e.g. the padded config block); only over-long lines wrap.
    if [ "${#text}" -le "$width" ]; then
        printf '%s%s\n' "$first_prefix" "$text"
        return 0
    fi
    # Subshell: set -f so a token like '*' cannot glob-expand during the
    # word split. Wrapping collapses runs of internal spaces — acceptable,
    # it only applies to lines already too long to keep verbatim.
    (
        set -f
        line="" emitted=0
        flush() {
            if [ "$emitted" = 0 ]; then
                printf '%s%s\n' "$first_prefix" "$line"; emitted=1
            else
                printf '%*s%s\n' "$cont_indent" '' "$line"
            fi
            line=""
        }
        for word in $text; do
            if [ -z "$line" ]; then
                line="$word"
            elif [ $(( ${#line} + 1 + ${#word} )) -le "$width" ]; then
                line="$line $word"
            else
                flush
                line="$word"
            fi
        done
        flush
    )
    return 0
}
# log — a narration line: section line if changed, then "[ts] text" wrapped.
log()  {
    _sec_render "$_SEC_CUR"
    _wrap_emit "[$(_ts)] " "$TEXT_COL" "$*"
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_event squashfs_narration text="$*"
    return 0   # the conditional trace mirror must not leak rc=1 into set -e
}
# detail — continuation-style line under the current section: aligned at the
# text column, no timestamp.
detail() {
    _sec_render "$_SEC_CUR"
    _wrap_emit "$(printf '%*s' "$TEXT_COL" '')" "$TEXT_COL" "$*"
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_event squashfs_narration text="$*"
    return 0
}
# logpipe [subtag] — THE chokepoint for sub-tool stdout/stderr. Each line
# renders under the current step's sub-section (e.g. "[2/6] [airootfs]") as
# a timestamped, wrapped content line. Always the LAST pipe element (see
# lastpipe above).
logpipe() {
    # Relayed sub-tool lines emit VERBATIM as one stamped line each — no
    # narrow wrap. The 72-col _wrap_emit here broke gate output mid-value
    # ("shipped pkm.db records / 0.46.3-r1") and rendered unlike every
    # other stamped line in the consolidated orchestrator log, defeating
    # per-line grep on exactly the values that matter (caught reading the
    # ge9b-11 Step-2.7 firing, 2026-07-30). Narration headers and the
    # :STATUS verdict column keep the wrapped design; relay does not.
    local _subtag="${1:-}" _l
    while IFS= read -r _l; do
        _sec_render "${_SEC_CUR}${_subtag:+ $_subtag}"
        printf '[%s] %s\n' "$(_ts)" "$_l"
    done
    return 0
}
# status_line <label> <DONE|PASS|FAIL|SKIP> — the right-aligned verdict column.
status_line() {
    local label="$1" st="$2"
    _sec_render "$_SEC_CUR"
    local body
    body="[$(_ts)] $label "
    local fill=$(( STATUS_COL - 1 - ${#body} ))
    [ "$fill" -lt 2 ] && fill=2
    local leaders
    leaders="$(printf '%*s' "$fill" '' | tr ' ' "$STATUS_LEADER")"
    printf '%s%s :%s\n' "$body" "$leaders" "$st"
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_event squashfs_narration text="$label :$st"
    return 0
}
# warn/die emit on STDOUT, not stderr: the orchestrator captures this script
# via `2>&1 | tee` into the build log, but the ge9b-10 Step-2.7 first firing
# measured die()'s stderr line vanishing from BOTH the build log and the unit
# journal under that pipeline while every stdout line survived — the run died
# with no failure line anywhere. A failure message the log cannot show is a
# silent failure; stdout is the logged stream, and the nonzero rc still
# carries the error to the caller.
warn() { printf '[%s] %s warning: %s\n' "$(_ts)" "$LOG_PREFIX" "$*"; }
die()  {
    printf '[%s] %s error: %s\n' "$(_ts)" "$LOG_PREFIX" "$*"
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && build_failure_emit --where build-squashfs.sh --why "$*" --phase squashfs
    exit 1
}

[ "$(id -u)" -eq 0 ] || die "must run as root (need to chroot + mount inside)"
[ -d "$CHROOT" ]      || die "chroot directory not found: $CHROOT"
[ -x "$CHROOT/bin/bash" ] || die "chroot does not appear bootable: missing $CHROOT/bin/bash"

OUTPUT_DIR=$(dirname "$OUTPUT")
mkdir -p "$OUTPUT_DIR"

# Resolve the project paths ONCE, up front. (Previously PROJECT_DIR was set
# inside Step 4.4's conditional and consumed by Steps 4.75/4.76 — a missing
# 4.4 script would have tripped `set -u` three gates later.)
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PKGS_DIR="$PROJECT_DIR/packages"

log "configuration:"
detail "chroot:            $CHROOT"
detail "output:            $OUTPUT"
detail "compressor:        $COMP"
detail "parallel jobs:     $JOBS"
detail "SOURCE_DATE_EPOCH: $SOURCE_DATE_EPOCH"

# ----------------------------------------------------------------------------
# Step 1: Mount pseudo-fs inside chroot
# ----------------------------------------------------------------------------
step_begin "[1/6]"
log "mounting pseudo-fs inside chroot..."

mount_if_needed() {
    local mnt="$1" type="$2" src="$3" opts="${4:-}"
    if mountpoint -q "$mnt"; then
        detail "$mnt already mounted, skipping"
    else
        mount -t "$type" ${opts:+-o "$opts"} "$src" "$mnt"
        detail "mounted $type at $mnt"
    fi
}

mkdir -p "$CHROOT"/{proc,sys,dev,run}
mount_if_needed "$CHROOT/proc" proc proc
mount_if_needed "$CHROOT/sys"  sysfs sysfs
mount_if_needed "$CHROOT/dev"  devtmpfs udev "mode=0755,nr_inodes=0"
mount_if_needed "$CHROOT/run"  tmpfs tmpfs "mode=0755,nr_inodes=800k"

# Bind /dev/pts for chroot exec sanity (some scripts expect it).
if [ -d "$CHROOT/dev/pts" ] && ! mountpoint -q "$CHROOT/dev/pts"; then
    mount -t devpts devpts "$CHROOT/dev/pts" -o "gid=5,mode=620"
    detail "mounted devpts at $CHROOT/dev/pts"
fi
status_line "mount chroot pseudo-fs" DONE

# ----------------------------------------------------------------------------
# Cleanup trap — always unmount even on failure.
# ----------------------------------------------------------------------------
cleanup_mounts() {
    log "cleanup: unmounting chroot pseudo-fs..."
    for mnt in "$CHROOT/dev/pts" "$CHROOT/run" "$CHROOT/dev" "$CHROOT/sys" "$CHROOT/proc"; do
        if mountpoint -q "$mnt"; then
            umount -l "$mnt" || warn "lazy-unmount failed on $mnt (will be reaped by VM reboot)"
        fi
    done
}
trap cleanup_mounts EXIT

# ----------------------------------------------------------------------------
# Step 2: Customize-airootfs hooks (chroot context)
# ----------------------------------------------------------------------------
step_begin "[2/6]"
if [ "$SKIP_CUSTOMIZE" = "1" ]; then
    log "customize-airootfs hooks skipped (SKIP_CUSTOMIZE=1)"
    status_line "customize-airootfs hooks" SKIP
else
    log "running customize-airootfs hooks inside chroot..."

    # Everything the chroot emits (the inner log() narration AND the raw
    # output of the tools it runs) is routed through logpipe so every line
    # lands prefixed + indented under this step header. pipefail (set at top)
    # makes a chroot failure propagate through the pipe to the || die.
    chroot "$CHROOT" /bin/bash -eu 2>&1 <<'CUSTOMIZE_AIROOTFS' | logpipe "[airootfs]" || die "customize-airootfs hooks failed inside the chroot"
log() { echo "$*"; }

# --- CA trust bundle -------------------------------------------------------
# Wave B.1 — verify /etc/ssl/certs/ca-certificates.crt is present + non-empty
# so curl/wget/git over TLS in the live boot can validate certificates against
# Mozilla's root store. The ca-certificates package (packages/core/ca-certificates)
# ships the curl.se snapshot of cacert.pem at this exact path on install, so
# the file should already be in place by squashfs build time. If it's missing
# or empty, halt — silently shipping a TLS-broken ISO is worse than a build
# failure.
CABUNDLE=/etc/ssl/certs/ca-certificates.crt
if [ -s "$CABUNDLE" ]; then
    log "CA bundle present: $CABUNDLE ($(wc -c < "$CABUNDLE") bytes)"
elif command -v update-ca-certificates >/dev/null 2>&1; then
    log "CA bundle missing — running update-ca-certificates --fresh"
    update-ca-certificates --fresh 2>&1 | tail -3 || true
    [ -s "$CABUNDLE" ] || { echo "error: CA bundle still empty after update-ca-certificates" >&2; exit 1; }
else
    echo "error: CA bundle missing AND update-ca-certificates unavailable — ca-certificates package not installed?" >&2
    exit 1
fi

# --- Dynamic linker cache --------------------------------------------------
log "ldconfig"
ldconfig

# --- System-account materialization (sysusers.d) ---------------------------
# Process every shipped /usr/lib/sysusers.d fragment into the sealed
# /etc/{passwd,group,shadow,gshadow} NOW, instead of leaving account
# creation to systemd-sysusers.service at first boot. Two reasons:
#   1. A targeted rebuild that redeploys intergenos-base-files AFTER other
#      packages were built overwrites the chroot's accumulated account DB
#      with the static baseline, silently dropping rows earlier package
#      builds created via their own sysusers fragments (2026-07-18: the
#      at/exim rows vanished this way, leaving 4750 root:atd / 6755
#      root:exim binaries with unresolvable gids — caught by the Step 4.76
#      inventory gate). Materializing here heals that class at the seal
#      point, from the fragments themselves (single source of truth).
#   2. A privileged (setuid/setgid) file whose owner/group name cannot be
#      resolved in the SHIPPED account DB is a latent defect even when
#      first boot would recreate the row — the sealed image must be
#      self-consistent, and Step 4.76 verifies it against this state.
# Idempotent on a from-scratch build (existing matching rows are no-ops;
# a fixed-ID conflict fails loudly). Fail-closed: no `|| true` — a broken
# account DB must halt the seal, not ship.
if command -v systemd-sysusers >/dev/null 2>&1; then
    log "systemd-sysusers (materialize shipped account fragments)"
    systemd-sysusers
else
    echo "error: systemd-sysusers unavailable in the chroot — cannot materialize sysusers.d account fragments" >&2
    exit 1
fi

# --- GLib/GTK/icon/desktop caches -----------------------------------------
# Each cache only refreshes if its corresponding directory exists; tolerate
# missing tools or directories on minimal-build profiles.
if command -v glib-compile-schemas >/dev/null 2>&1 && [ -d /usr/share/glib-2.0/schemas ]; then
    log "glib-compile-schemas"
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>&1 | tail -3 || true
fi

if command -v update-desktop-database >/dev/null 2>&1 && [ -d /usr/share/applications ]; then
    log "update-desktop-database"
    update-desktop-database -q /usr/share/applications || true
fi

if command -v update-mime-database >/dev/null 2>&1 && [ -d /usr/share/mime ]; then
    log "update-mime-database"
    update-mime-database /usr/share/mime 2>&1 | tail -3 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    # Every indexed theme, not just hicolor: a stale per-theme cache with an
    # mtime newer than the theme dirs makes GTK bypass the theme entirely and
    # fall back to hicolor (ge9b-11 live ISO shipped a ge9b-10-era InterGenOS
    # cache with zero app entries — the approved Chronicle mark was on disk
    # and invisible). Rebuilding here, after all payload is final, makes the
    # shipped cache always describe the shipped content.
    for theme_dir in /usr/share/icons/*/; do
        [ -f "$theme_dir/index.theme" ] || continue
        log "gtk-update-icon-cache ($(basename "$theme_dir"))"
        gtk-update-icon-cache -f -t "$theme_dir" 2>&1 | tail -3 || true
    done
fi

if command -v fc-cache >/dev/null 2>&1; then
    log "fc-cache"
    fc-cache -f 2>&1 | tail -3 || true
fi

# --- Systemd preset activation --------------------------------------------
# This is what consumes /usr/lib/systemd/system-preset/*.preset (the files
# we ship from packages like gdm + nftables), creating the .wants/ symlinks
# (including /etc/systemd/system/display-manager.service -> gdm.service via
# our 90-gdm.preset).
if command -v systemctl >/dev/null 2>&1; then
    log "systemctl preset-all"
    systemctl preset-all 2>&1 | tail -5 || true
fi

# --- Man-page index --------------------------------------------------------
# mandb is large + slow; skip if not installed. Operators wanting it can
# run `mandb -c` on first boot.
if command -v mandb >/dev/null 2>&1; then
    # /var/cache/man/ is normally created at boot by systemd-tmpfiles via
    # /usr/lib/tmpfiles.d/man-db.conf. We run mandb pre-boot inside the
    # chroot, so apply that rule manually here — otherwise mandb fails with
    # "can't create index cache ... No such file or directory".
    if [ -f /usr/lib/tmpfiles.d/man-db.conf ] && command -v systemd-tmpfiles >/dev/null 2>&1; then
        systemd-tmpfiles --create /usr/lib/tmpfiles.d/man-db.conf 2>&1 | tail -3 || true
    fi
    log "mandb -q (background-quiet)"
    mandb -q 2>&1 | tail -3 || true
fi

# --- Strip LFS test accounts (defense-in-depth) ----------------------------
# The LFS Ch8 test suite runs as a `tester` account: scripts/chroot-build-ch8.sh
# `userdel -r tester` after `make check`, and shadow's post_install no longer
# creates one (the useradd was removed from post_install). But a stray
# tester:1001 + /home/tester leaking onto a shipped system bit us once
# (GBC001.5 install), so GUARANTEE it here rather than trust the upstream
# removals: scrub any test account from the live ISO root and HALT the build if
# one survives — a silent leak is worse than a build failure.
for u in tester; do
    if id "$u" >/dev/null 2>&1; then
        log "stripping stray test account: $u"
        userdel -r "$u" 2>/dev/null || userdel "$u" 2>/dev/null || true
    fi
done
if id tester >/dev/null 2>&1 || grep -q '^tester:' /etc/passwd 2>/dev/null; then
    echo "error: 'tester' account present in the live ISO root after scrub" >&2
    exit 1
fi
log "test-account scrub OK (no tester in live ISO root)"

CUSTOMIZE_AIROOTFS

    status_line "customize-airootfs hooks" DONE
fi

# ----------------------------------------------------------------------------
# Step 2.5: pkm iso-prep — evict iso_include:false (MIRROR-only) packages
#                          from the chroot before mksquashfs assembles it.
# ----------------------------------------------------------------------------
# Path-b of the D-014 ISO curation (operator-picked 2026-05-28). Replaces
# the historical Path-a `mksquashfs -ef <exclusion-file>` approach with a
# pre-mksquashfs pkm uninstall of every MIRROR package. Advantages:
#   - pkm's runtime-dep graph enforces safety: if an iso_include:true
#     package depends on an iso_include:false package, pkm iso-prep
#     aborts with a clear metadata-bug error instead of mksquashfs
#     silently shipping a half-broken ISO.
#   - the chroot's contents ARE the final ISO contents — easier to
#     inspect + verify.
#   - mksquashfs runs with no -ef flag (simpler invocation).
#
# We derive the MIRROR-names list from the source-tree package.yml files
# (single source of truth) and write it into the chroot at
# /tmp/iso-mirror-packages.txt, then `chroot $CHROOT pkm iso-prep --yes
# --packages-from /tmp/iso-mirror-packages.txt`. pkm and its dependencies
# (python3, sqlite) are core-tier so they remain available inside the
# chroot even after iso-prep finishes.
#
# Toggle: ISO_PREP=0 to skip (e.g. for diagnostic builds shipping the full
# pre-prune chroot). Default ISO_PREP=1.
ISO_PREP="${ISO_PREP:-1}"
step_begin "[2.5/6]"
if [ "$ISO_PREP" = "1" ] && [ -x /mnt/intergenos/scripts/derive-iso-exclusions.py ]; then
    log "pkm iso-prep — pruning MIRROR-only packages from chroot..."
    NAMES_HOST="/tmp/iso-mirror-packages.txt"
    NAMES_CHROOT_REL="/tmp/iso-mirror-packages.txt"
    python3 /mnt/intergenos/scripts/derive-iso-exclusions.py \
        --mode=names \
        --packages /mnt/intergenos/packages \
        --output "$NAMES_HOST" 2>&1 | logpipe "[derive-iso]" \
        || die "iso-exclusion derivation failed — refusing to build a squashfs whose MIRROR set is unproven"

    # Stage the names file inside the chroot. chroot's /tmp is mounted
    # tmpfs by Step 1; safe to write here.
    cp "$NAMES_HOST" "$CHROOT$NAMES_CHROOT_REL"

    # A shipped package can co-own paths with a MIRROR-only package
    # (vala-pass1 vs desktop/vala: the bootstrap's whole payload sits at
    # the final compiler's paths). pkm remove is not co-ownership-aware,
    # so the prune deletes those files out from under the shipped package
    # and gate 4.5 fail-closes on the loss. Capture the to-be-pruned path
    # set BEFORE the prune so the post-prune heal below can restore any
    # co-owned casualty from its exact archive, byte-verified.
    # A FILE LIST entry may carry a trailing " sha256:<64 hex>" annotation;
    # the path is the line with that suffix removed, anchored at end of line
    # so paths containing spaces survive. Both this capture and the claim
    # scan below MUST normalize identically — they are compared against each
    # other, and two packages co-owning a path record their own content
    # hashes for it, so comparing annotated lines would never match and the
    # co-ownership heal would silently stop detecting casualties in a gate
    # whose whole purpose is to be fail-closed.
    PRUNED_PATHS="/tmp/iso-prep-pruned-paths.txt"
    : > "$PRUNED_PATHS"
    while IFS= read -r _pname; do
        [ -n "$_pname" ] || continue
        for _mf in "$CHROOT/var/lib/igos/packages/${_pname}"-[0-9]*; do
            [ -f "$_mf" ] || continue
            awk 'f{sub(/ sha256:[0-9a-f]+$/, ""); print} /^FILE LIST:$/{f=1}' "$_mf"
        done
    done < "$NAMES_HOST" | sort -u > "$PRUNED_PATHS"

    if [ -x "$CHROOT/usr/bin/pkm" ]; then
        # Run pkm inside chroot. --yes skips the [y/N] prompt; we trust
        # the curation walk's iso_include:false flags as the input
        # source of truth.
        chroot "$CHROOT" /usr/bin/pkm iso-prep \
            --yes --packages-from "$NAMES_CHROOT_REL" 2>&1 | logpipe \
            || die "pkm iso-prep failed — see error above. The chroot is in a partial state; restore from the golden snapshot before retrying."
        rm -f "$CHROOT$NAMES_CHROOT_REL"
        detail "pkm iso-prep done — chroot pruned to iso_include:true packages only"

        # Post-prune co-ownership heal (fail-closed). Any path that (a)
        # belonged to a pruned package, (b) is claimed by a still-installed
        # package's manifest, and (c) is now missing on disk is a co-owned
        # casualty of the prune. Restore each damaged package via the
        # proven recovery shape (DB-drop -> pkm install --archive ->
        # pkm verify) and refuse the build if it cannot be restored
        # byte-verified. The root fix (co-ownership-aware pkm remove)
        # rides the pkm work queue; this keeps the prune loss-proof.
        HEAL_LIST=""
        if [ -s "$PRUNED_PATHS" ]; then
            # (An empty PRUNED_PATHS — e.g. a resume where the prune already
            # ran — must NOT reach awk: an empty first file breaks the
            # NR==FNR two-file idiom and would treat the first manifest as
            # the pruned set.)
            _claims=$(awk 'NR==FNR{pruned[$0]=1; next}
                FNR==1{f=0}
                /^FILE LIST:$/{f=1; next}
                f{ p=$0; sub(/ sha256:[0-9a-f]+$/, "", p);
                   if (p in pruned) { n=split(FILENAME,a,"/"); print a[n] "\t" p } }' \
                "$PRUNED_PATHS" "$CHROOT"/var/lib/igos/packages/*)
            while IFS=$'\t' read -r _nv _p; do
                [ -n "$_nv" ] || continue
                [ -e "$CHROOT/$_p" ] && continue
                grep -Fxq "${_nv%%-[0-9]*}" "$NAMES_HOST" && continue
                case " $HEAL_LIST " in *" $_nv "*) ;; *) HEAL_LIST="$HEAL_LIST $_nv" ;; esac
            done <<< "$_claims"
        fi
        if [ -n "$HEAL_LIST" ]; then
            for _nv in $HEAL_LIST; do
                _ver=$(awk -F': ' '/^PACKAGE VERSION:/{print $2; exit}' "$CHROOT/var/lib/igos/packages/$_nv")
                _name="${_nv%-$_ver}"
                _arc="/var/lib/igos/archives/${_nv}.igos.tar.gz"
                [ -f "$CHROOT$_arc" ] || die "co-ownership heal: archive $_arc missing for $_name — cannot restore prune-deleted files"
                log "co-ownership heal: prune deleted files co-owned by shipped '$_name' — restoring from ${_nv}.igos.tar.gz"
                chroot "$CHROOT" /usr/bin/python3 -c "from pkm.database import PackageDB; PackageDB().remove_installed('$_name')" 2>&1 | logpipe "[heal]"
                chroot "$CHROOT" /usr/bin/pkm install "$_name" --archive "$_arc" --archive-trust loose 2>&1 | logpipe "[heal]" \
                    || die "co-ownership heal: pkm install $_name failed — see error above"
                chroot "$CHROOT" /usr/bin/pkm verify "$_name" 2>&1 | logpipe "[heal]" \
                    || die "co-ownership heal: $_name does not verify after restore"
                detail "co-ownership heal: $_name restored + byte-verified"
            done
        else
            detail "co-ownership heal: no shipped package lost files to the prune"
        fi
        rm -f "$PRUNED_PATHS"

        # GBC001.7: recompute the ld.so cache + soname symlinks AFTER the
        # prune. A pruned MIRROR-only package can leave a public soname
        # symlink dangling: e.g. both libglvnd (libEGL.so.1.1.0) and the
        # mirror-only nvidia driver (libEGL.so.580.x, also soname
        # libEGL.so.1) are in the chroot at install time; ldconfig points
        # /usr/lib/libEGL.so.1 at nvidia's (higher version wins). iso-prep
        # then removes nvidia's file but NOT the symlink -> dangling
        # libEGL.so.1 -> gnome-shell "error while loading shared libraries:
        # libEGL.so.1" (exit 127) -> gdm crash-loop -> black screen (the
        # GBC001.2 bare-metal boot). Re-running ldconfig recomputes
        # libEGL.so.1 -> the only surviving libEGL.so.1.1.0 (libglvnd).
        # PROVEN live on the GBC001.2 boot: ldconfig repoint -> desktop up.
        if [ -x "$CHROOT/sbin/ldconfig" ] || [ -x "$CHROOT/usr/bin/ldconfig" ]; then
            chroot "$CHROOT" ldconfig 2>&1 | logpipe "[ldconfig]" || true
            detail "ldconfig refreshed — dangling soname symlinks from pruned packages recomputed"
        fi
        status_line "pkm iso-prep (MIRROR-only prune)" DONE
    else
        warn "$CHROOT/usr/bin/pkm not found — skipping iso-prep (live-ISO may include MIRROR packages)"
        status_line "pkm iso-prep (MIRROR-only prune)" SKIP
    fi
else
    log "pkm iso-prep skipped (ISO_PREP=$ISO_PREP)"
    status_line "pkm iso-prep (MIRROR-only prune)" SKIP
fi

# ----------------------------------------------------------------------------
# Step 2.6: mirror-only ARCHIVE exclusion derivation (F41, decided 2026-07-22)
# ----------------------------------------------------------------------------
# iso-prep (Step 2.5) removes MIRROR packages' INSTALLED payloads, but the
# archive corpus at /var/lib/igos/archives sits outside pkm's ownership and
# was never excluded from mksquashfs — every prior ISO shipped ALL archives,
# mirror-only included (~10G class at the ai-tier scale; verified back to
# older release candidates, which carried their full corpus the same way).
# iso_include:false means "lives on the mirror, not the ISO", so the ISO
# ships exactly the iso_include:true archive set the installer consumes.
#
# EXCLUSION, not deletion: the chroot's full archive corpus is the
# mirror-publish source and part of the snapshot's banked state — the
# squashfs simply does not take the mirror-only members. Basenames come
# from derive-iso-exclusions --mode=archive-excludes (parsed package.yml
# name+version — the same fields archives are named from; no filename
# splitting, so a name that prefixes another name can never over-match).
# The Step 4.85 ownership gate independently fail-closes on any archive
# left in the tree that doesn't belong to an installed package.
MIRROR_ARCHIVE_EXCLUDES=()
MIRROR_ARCHIVE_EXCLUDES_FILE="/tmp/iso-mirror-archive-excludes.txt"
if [ "$ISO_PREP" = "1" ] && [ -f /mnt/intergenos/scripts/derive-iso-exclusions.py ]; then
    python3 /mnt/intergenos/scripts/derive-iso-exclusions.py \
        --mode=archive-excludes \
        --packages /mnt/intergenos/packages \
        --output "$MIRROR_ARCHIVE_EXCLUDES_FILE" 2>&1 | logpipe "[derive-arc]" \
        || die "archive-excludes derivation failed — refusing to build a squashfs that would ship mirror-only archives"
    _ARC_LISTED=0; _ARC_PRESENT=0
    while IFS= read -r _rel; do
        [ -n "$_rel" ] || continue
        case "$_rel" in \#*) continue ;; esac
        _ARC_LISTED=$((_ARC_LISTED + 1))
        if [ -f "$CHROOT/$_rel" ]; then
            MIRROR_ARCHIVE_EXCLUDES+=(-e "$_rel")
            _ARC_PRESENT=$((_ARC_PRESENT + 1))
        fi
    done < "$MIRROR_ARCHIVE_EXCLUDES_FILE"
    log "mirror-only archive exclusion: $_ARC_PRESENT archive(s) excluded from the squashfs ($_ARC_LISTED mirror packages declared)"
    status_line "mirror-only archive exclusion" DONE
else
    warn "mirror-only archive exclusion SKIPPED (ISO_PREP=$ISO_PREP) — this squashfs will carry the FULL archive corpus"
    status_line "mirror-only archive exclusion" SKIP
fi

# ----------------------------------------------------------------------------
# Step 2.7: metadata/payload sync gate (fail-closed, decided 2026-07-28)
# ----------------------------------------------------------------------------
# The current release candidate shipped a pkm.db describing a different
# build of 198 packages than the archives on the same ISO — a live
# session's pkm answered wrongly for ~24% of the corpus, /etc/sysconfig
# was absent while its owner still claimed it, and /etc/os-release was
# nearly empty. This gate compares every shipping archive's .PKGINFO and
# payload against the chroot database, text manifests and on-disk files,
# and refuses to build a squashfs whose metadata describes a different
# build than its archives. Check-only: the remedy for any hit is
# redeploying the named archive into the chroot (the gate prints the
# exact command), never hand-editing metadata.
METADATA_SYNC_GATE="$(dirname "$0")/check-iso-metadata-sync.py"
if [ -f "$METADATA_SYNC_GATE" ]; then
    log "metadata/payload sync gate — archives vs pkm.db vs image..."
    _SYNC_ARGS=(--chroot "$CHROOT" --report /tmp/iso-metadata-sync-report.txt)
    [ -f "$MIRROR_ARCHIVE_EXCLUDES_FILE" ] && \
        _SYNC_ARGS+=(--archive-excludes "$MIRROR_ARCHIVE_EXCLUDES_FILE")
    _SYNC_RC=0
    python3 "$METADATA_SYNC_GATE" "${_SYNC_ARGS[@]}" 2>&1 | logpipe "[meta-sync]" || _SYNC_RC=$?
    if [ "$_SYNC_RC" -ne 0 ]; then
        status_line "metadata/payload sync gate" FAIL
        log "the image would describe a different build than its own archives."
        log "Full detail: /tmp/iso-metadata-sync-report.txt; per-package"
        log "remedies are in the gate output above."
        die "metadata/payload sync gate FAILED (rc=$_SYNC_RC)"
    fi
    status_line "metadata/payload sync gate" DONE
else
    die "check-iso-metadata-sync.py not found beside build-squashfs.sh — refusing to build without the metadata/payload sync gate"
fi

# ----------------------------------------------------------------------------
# Step 2.8: stamp the build identity into the image's os-release (N-6)
# ----------------------------------------------------------------------------
# Two USB sticks side by side were indistinguishable except by a date only
# the builder could map back to a candidate. The candidate name is chosen
# at launch (--iso-name, persisted in build/.iso-name across the ceremony
# chain) — stamp it into the live image's /etc/os-release as BUILD_ID +
# IMAGE_VERSION (systemd os-release spec fields). Installed systems get
# IMAGE_VERSION from Forge's generate_os_release, which reads the live
# medium's BUILD_ID at install time.
ISO_NAME_FILE="/mnt/intergenos/build/.iso-name"
BUILD_TAG=""
if [ -f "$ISO_NAME_FILE" ]; then
    _iso_base=$(basename "$(cat "$ISO_NAME_FILE")")
    BUILD_TAG="${_iso_base#intergenos-}"
    BUILD_TAG="${BUILD_TAG%.iso}"
fi
if [ -z "$BUILD_TAG" ]; then
    BUILD_TAG="unnamed-$(date -u -d "@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m%d)"
    warn "no build/.iso-name — stamping BUILD_ID=$BUILD_TAG (launch with --iso-name for a real candidate tag)"
fi
if [ -f "$CHROOT/etc/os-release" ]; then
    sed -i '/^BUILD_ID=/d;/^IMAGE_VERSION=/d' "$CHROOT/etc/os-release"
    printf 'BUILD_ID="%s"\nIMAGE_VERSION="%s"\n' "$BUILD_TAG" "$BUILD_TAG" >> "$CHROOT/etc/os-release"
    log "os-release stamped: BUILD_ID=$BUILD_TAG"
    status_line "build-identity stamp (os-release)" DONE
else
    die "chroot /etc/os-release missing — cannot stamp build identity (base-files absent?)"
fi

# ----------------------------------------------------------------------------
# Step 3: Clean runtime trash
# ----------------------------------------------------------------------------
step_begin "[3/6]"
log "cleaning runtime trash..."

# Truncate logs (don't delete the files — services may have open fds).
if [ -d "$CHROOT/var/log" ]; then
    find "$CHROOT/var/log" -type f -exec truncate -s 0 {} + 2>/dev/null || true
    detail "truncated $CHROOT/var/log/*"
fi

# Clean /tmp + /var/tmp (preserving the directories themselves).
for d in "$CHROOT/tmp" "$CHROOT/var/tmp"; do
    [ -d "$d" ] && find "$d" -mindepth 1 -delete 2>/dev/null || true
done
detail "emptied /tmp and /var/tmp"

# Reset machine-id. Real distros write the literal string "uninitialized" so
# systemd-machine-id-setup generates a real one non-interactively on first
# boot of an installed system. Live ISO's init.sh overlay writes a real ID
# per-boot, so this default is correct for both paths.
echo "uninitialized" > "$CHROOT/etc/machine-id"
detail "reset /etc/machine-id to 'uninitialized' (installed-system default)"

# Purge Chronicle restore-point state. intergenos-backup's pre-transaction
# hook captures a restore point on every pkm operation, so a chroot that
# saw package surgery accumulates a content-addressed store of build-era
# blobs — 79 GB measured on the ge9b-10 mint (the Step-2.7 redeploy wave),
# caught by the 4.75 ELF NEEDED-closure gate scanning CAS blobs. Zero
# paths under /var/lib/chronicle are package-claimed (runtime state only);
# build-era restore points reference THIS chroot's history and are
# meaningless on the live ISO or an installed target — the engine
# initializes fresh state on first run. Keep the top-level dir.
if [ -d "$CHROOT/var/lib/chronicle" ]; then
    find "$CHROOT/var/lib/chronicle" -mindepth 1 -delete 2>/dev/null || true
    detail "purged /var/lib/chronicle build-era restore-point state"
fi

# Clear bash histories that may have leaked from build operations.
rm -f "$CHROOT/root/.bash_history"
find "$CHROOT/home" -maxdepth 2 -name '.bash_history' -delete 2>/dev/null || true
detail "cleared root + user bash histories"

# Clear /root build-time caches + tool state (F41 survey, decided
# 2026-07-22). Compilers and generators running as root during the build
# leave their caches under /root — measured 8.7G / ~138k files on the
# ge9b-08 chroot (go-build + ROCm comgr dominate) — and every prior ISO
# shipped them: unowned, useless on the live/installed system, and pure
# size. The list is explicit dirs/files (never a bare `rm -rf /root/*`):
# anything NEW appearing under /root is exactly what the Step 4.85
# ownership gate must surface, not silently delete.
for d in .cache .cargo .cmake .config .gdbm_history .gdbmtool_history \
         .links .local .mozbuild .npm go .triton; do
    if [ -e "$CHROOT/root/$d" ]; then
        rm -rf "${CHROOT:?}/root/$d"
    fi
done
detail "cleared /root build-time caches (go-build, comgr, mozbuild, npm, cmake, links, triton, state)"
status_line "clean runtime trash" DONE

# ----------------------------------------------------------------------------
# Step 4: Unmount pseudo-fs (via trap on exit; do an early unmount here so
#         mksquashfs sees clean empty dirs instead of bind-mount artifacts).
# ----------------------------------------------------------------------------
step_begin "[4/6]"
log "unmounting chroot pseudo-fs..."
cleanup_mounts
trap - EXIT   # trap fulfilled; clear to avoid double-unmount on script exit

# Verify the mount-point directories survived as EMPTY dirs in the chroot.
# This is what real distros do — empty /sys /proc /dev /run /tmp as part of
# the rootfs so init.sh's `mount --move` finds destinations.
for mnt in proc sys dev run tmp; do
    if [ ! -d "$CHROOT/$mnt" ]; then
        warn "$CHROOT/$mnt MISSING — recreating as empty dir"
        mkdir -p "$CHROOT/$mnt"
    fi
done
status_line "unmount chroot pseudo-fs" DONE

# ----------------------------------------------------------------------------
# Step 4.4: M-002 chroot-binary-presence gate (T0-3 sub-cluster 2)
# ----------------------------------------------------------------------------
# Verify every binary the installer Python invokes via subprocess + every
# binary the shell pipeline scripts depend on is present in the chroot at a
# standard search path. Closes the regression class that produced C-001
# (parted missing) + F-001 (iucode_tool path mismatch) — a binary-presence
# check that complements the broader verify_paths audit in Step 4.5.
# Runs FIRST so missing-binary regressions surface with a precise diagnostic
# before the verify_paths audit's package-level failure cascade.
M002_GATE="$(dirname "$0")/check-installer-runtime-deps.py"
step_begin "[4.4/6]"
if [ -x "$M002_GATE" ] || [ -f "$M002_GATE" ]; then
    log "M-002 chroot-binary-presence gate against chroot..."
    # Capture-then-log (errexit-safe, same idiom as Step 4.8) so the gate's
    # own summary lands prefixed + indented, and rc is read from the tool,
    # not the log pipe.
    # --strict-unowned (2026-08-19): a required binary present in the chroot
    # but claimed by no package.yml verify_paths entry now fails this gate
    # too, not just a missing one. The gate has always printed those as
    # UNOWNED and passed; the first release built with 29 of them, which is
    # 29 binaries the installer depends on that the pre-squashfs audit never
    # checked landed. All are declared now, so the flag holds at zero.
    m002_out="$(python3 "$M002_GATE" --chroot "$CHROOT" --project "$PROJECT_DIR" --strict-unowned 2>&1)" && m002_rc=0 || m002_rc=$?
    [ -n "$m002_out" ] && printf '%s\n' "$m002_out" | logpipe "[m-002]"
    if [ "$m002_rc" -eq 0 ]; then
        detail "all installer-runtime binaries present in chroot"
        status_line "M-002 chroot-binary-presence gate" PASS
    else
        detail "an installer-runtime binary is missing from the chroot, or is present"
        detail "but claimed by no package's verify_paths (--strict-unowned)"
        detail "re-run the gate directly for the full diagnostic:"
        detail "  python3 $M002_GATE --chroot $CHROOT --project $PROJECT_DIR --strict-unowned --verbose"
        status_line "M-002 chroot-binary-presence gate" FAIL
        die "M-002 gate failed (rc=$m002_rc) — refusing to build squashfs"
    fi
else
    log "M-002 gate script not found at $M002_GATE"
    status_line "M-002 chroot-binary-presence gate" SKIP
fi

# ----------------------------------------------------------------------------
# Step 4.45: staged-kernel exclusivity gate (ship-blocking backstop)
# ----------------------------------------------------------------------------
# The squashfs ships /usr/lib/modules and /boot wholesale; a superseded
# kernel release's orphaned twin (release-named module tree + vmlinuz)
# would ship BOTH kernels and downstream pickers resolve ambiguously.
# Fail-closed here so a twin can never reach the sealed image, whatever
# upstream entry point missed it (decided gate wave, 2026-07-12).
if ! bash "$(dirname "$0")/preflight-single-kernel.sh" --root "$CHROOT"; then
    status_line "staged-kernel exclusivity gate" FAIL
    die "staged-kernel exclusivity gate failed — the chroot holds a superseded kernel twin; refusing to build squashfs"
fi
status_line "staged-kernel exclusivity gate" PASS

# ----------------------------------------------------------------------------
# Step 4.5: pre-squashfs audit — verify every declared package landed
# ----------------------------------------------------------------------------
# Each packages/<tier>/<name>/package.yml declares verify_paths: — load-
# bearing files the package produces. Audit fails if any are missing from
# the chroot. This catches the linux-firmware-class regression where a
# package recipe exists in tree but the build silently produced no files
# (orchestrator-skip-built footgun, build-host-state-dependency, etc.).
# See feedback_orchestrator_skip_built_footgun memory.
AUDIT_SCRIPT="$(dirname "$0")/pre-squashfs-audit.py"
step_begin "[4.5/6]"
if [ -x "$AUDIT_SCRIPT" ] || [ -f "$AUDIT_SCRIPT" ]; then
    log "pre-squashfs audit (verify_paths) against chroot..."
    audit_out="$(python3 "$AUDIT_SCRIPT" --packages-dir "$PKGS_DIR" --chroot "$CHROOT" --quiet 2>&1)" && audit_rc=0 || audit_rc=$?
    [ -n "$audit_out" ] && printf '%s\n' "$audit_out" | logpipe "[audit]"
    if [ "$audit_rc" -eq 0 ]; then
        detail "all declared verify_paths present"
        status_line "pre-squashfs audit (verify_paths)" PASS
    else
        detail "either fix the regression (build the missing packages) or correct"
        detail "the verify_paths declarations. Run the audit script directly to"
        detail "see the full diagnostic."
        status_line "pre-squashfs audit (verify_paths)" FAIL
        die "verify_paths audit failed (rc=$audit_rc) — refusing to build squashfs"
    fi
else
    log "pre-squashfs audit script not found at $AUDIT_SCRIPT"
    status_line "pre-squashfs audit (verify_paths)" SKIP
fi

# ----------------------------------------------------------------------------
# Step 4.55: tmpfiles.d user/group resolvability gate (ship-blocking)
# ----------------------------------------------------------------------------
# A tmpfiles.d entry naming a user provided by a DIFFERENT package is an
# install-order race: on a fresh install the package manager fires each
# package's tmpfiles hook at its own install moment, and any order that
# places the referrer before the user's provider fails the hook and marks
# the package degraded. The chroot masks this class (users accrete into
# /etc/passwd in build order), so the gate resolves providers structurally
# from the pkm manifests + the recipe-tree baseline passwd — never from
# the chroot's live passwd. Decided 2026-07-19 after the ge9b-05 fresh
# install degraded swtpm exactly this way (tss arrived later, via
# tpm2-tss). Fail-closed: an unresolvable reference never ships.
if ! python3 "$(dirname "$0")/audit-tmpfiles-users.py" --root "$CHROOT" --packages-dir "$PKGS_DIR" --quiet; then
    status_line "tmpfiles.d owner resolvability gate" FAIL
    die "tmpfiles.d owner resolvability gate failed — a tmpfiles.d conf references a user/group its owning package does not provide; refusing to build squashfs"
fi
status_line "tmpfiles.d owner resolvability gate" PASS

# ----------------------------------------------------------------------------
# Step 4.6: install-set audit — every staged archive MUST be Forge-parseable
# ----------------------------------------------------------------------------
# Step 4.5 proves declared files landed in the chroot, but Forge installs from
# the .igos.tar.gz archives via packages.get_archives(); an archive that is
# physically present in the squashfs yet never yielded by that parser (a
# non-digit version, a duplicate-name clobber) ships in the image but never
# installs — and the chroot-side audit above still passes. That is exactly how
# llama-cpp-b5545 shipped with no inference engine. This audit runs the REAL
# Forge parser against the staged archive dir and refuses to build squashfs if
# any archive would be silently dropped at install time. --strict-tier rides
# the seal too: a parsed archive that maps to no packages/<tier>/ dir can never
# be selected by get_group_packages, which is the same ships-but-never-installs
# class. (Validated against the full ge9b-03 933-archive set 2026-07-15 —
# clean; the earlier 'left off until validated' condition is met.)
ISET_SCRIPT="$(dirname "$0")/preflight-install-set.py"
ARCHIVE_DIR="$CHROOT/var/lib/igos/archives"
step_begin "[4.6/6]"
# An install set that cannot be audited must not be sealed: an absent gate
# script or an absent archive dir is a hard refusal, never a SKIP. A
# --start-at squashfs resume against an unpopulated substrate previously
# SKIPped this audit (and the PI-12 sweep below) and sealed media carrying
# no Forge install set — media from which nothing can be installed.
if [ ! -f "$ISET_SCRIPT" ]; then
    status_line "install-set audit (Forge-parseable)" FAIL
    die "install-set audit script not found at $ISET_SCRIPT — the install set cannot be certified; refusing to build squashfs"
fi
if [ ! -d "$ARCHIVE_DIR" ]; then
    status_line "install-set audit (Forge-parseable)" FAIL
    die "no staged archive dir at $ARCHIVE_DIR — there is no install set to seal; refusing to build squashfs"
fi
# Sweep seal-gate quarantine files out of the install set before it seals.
# *.failed archives are the archive-seal gate's rejected attempts; a later
# successful rebuild sealed the real archive beside them. They are dead
# weight the media must not carry (ge9b-06 shipped 8 of them, 124 MB) — and
# if a package's ONLY artifact were a .failed, the audit below fails on the
# missing real archive, so this sweep can never mask a loss. Logged, loud.
failed_count=$(find "$ARCHIVE_DIR" -maxdepth 1 -name '*.failed' | wc -l)
if [ "$failed_count" -gt 0 ]; then
    log "sweeping $failed_count seal-gate quarantine file(s) (*.failed) from the install set:"
    find "$ARCHIVE_DIR" -maxdepth 1 -name '*.failed' -print | logpipe "[quarantine-sweep]"
    find "$ARCHIVE_DIR" -maxdepth 1 -name '*.failed' -delete
fi
log "install-set audit (Forge get_archives parses every staged archive; strict tier resolution)..."
iset_out="$(python3 "$ISET_SCRIPT" --archive-dir "$ARCHIVE_DIR" --packages-dir "$PKGS_DIR" --strict-tier 2>&1)" && iset_rc=0 || iset_rc=$?
[ -n "$iset_out" ] && printf '%s\n' "$iset_out" | logpipe "[install-set]"
if [ "$iset_rc" -eq 0 ]; then
    detail "every staged archive parses AND resolves to a packages/<tier>/ dir"
    status_line "install-set audit (Forge-parseable + tier-resolved)" PASS
else
    detail "an archive ships in the image but Forge would never install it"
    detail "(silent-drop / tier-orphan class). Run: python3 $ISET_SCRIPT --archive-dir"
    detail "$ARCHIVE_DIR --strict-tier  for the full list."
    status_line "install-set audit (Forge-parseable + tier-resolved)" FAIL
    die "install-set audit failed (rc=$iset_rc) — refusing to build squashfs"
fi

# ----------------------------------------------------------------------------
# Step 4.7: PI-12 .PKGINFO sweep — every staged archive must be self-describing
# ----------------------------------------------------------------------------
# pkg_archive (scripts/pkg-functions.sh) asserts each archive carries a well-
# formed ./.PKGINFO at build time (the precise per-package tripwire). This is
# the authoritative install-set fail-closed gate: before sealing the squashfs,
# every archive in the install set must carry pkgname/pkgver/pkgrel, else the
# binary repo index (pkm.repo.generate_index) can't see it and Forge can't
# install it — the PI-12 symptom (1000+ camouflaging `*.PKGINFO not found` tar
# failures at install). Mirrors the 4.5/4.6 refuse-to-seal shape.
step_begin "[4.7/6]"
if [ -d "$ARCHIVE_DIR" ]; then
    log "PI-12 .PKGINFO sweep (every staged archive self-describing)..."
    # Canonical predicate + sweep live in scripts/lib/pi12-sweep.sh (single
    # source of truth, also sourced by tests/pi12/test_pi12_gates.sh). The
    # honesty-first empty-set rule (no vacuous PASS) lives there.
    PI12_LIB="$(dirname "$0")/lib/pi12-sweep.sh"
    if [ ! -f "$PI12_LIB" ]; then
        status_line "PI-12 .PKGINFO sweep" FAIL
        die "$PI12_LIB not found — cannot run the PI-12 sweep; refusing to build squashfs"
    fi
    # shellcheck source=/dev/null
    . "$PI12_LIB"
    # pi12_sweep prints offending archive names + a one-line verdict; route it
    # through logpipe. rc 0 = PASS, 1 = refuse-to-seal (empty set or any miss).
    sweep_out="$(pi12_sweep "$ARCHIVE_DIR")" && sweep_rc=0 || sweep_rc=$?
    [ -n "$sweep_out" ] && printf '%s\n' "$sweep_out" | logpipe "[pi-12]"
    if [ "$sweep_rc" -ne 0 ]; then
        detail "a metadata-less archive is invisible to the repo index and uninstallable;"
        detail "an empty install set at seal time is itself a defect. Rebuild the offending"
        detail "package(s) — pkg_archive emits + asserts .PKGINFO; step 8.87 backfills pre-python."
        status_line "PI-12 .PKGINFO sweep" FAIL
        die "PI-12 sweep failed — refusing to build squashfs"
    fi
    status_line "PI-12 .PKGINFO sweep" PASS
else
    # Same rule as Step 4.6: an unauditable install set is a refusal, not a
    # SKIP (the dir was asserted present at 4.6, so reaching here means it
    # vanished mid-seal — worse, not better).
    status_line "PI-12 .PKGINFO sweep" FAIL
    die "no staged archive dir at $ARCHIVE_DIR — the PI-12 sweep cannot run; refusing to build squashfs"
fi

# ----------------------------------------------------------------------------
# Step 4.75: ELF NEEDED-closure + word-size backstop over the POST-EVICTION
#            chroot (igos-build/needclosure.py) — the sealed file set is the
#            exact set a live session / the installer runs from, and this is
#            the ONLY point that proves (a) every dynamic ELF's NEEDED
#            entries resolve to a same-width provider that actually SHIPS
#            (the eviction hazard: a consumer whose provider was evicted at
#            step 2.5), and (b) no wrong-width ELF rides the ISO — which is
#            also the universal backstop for the archive-time width audit's
#            sanctioned pre-python bootstrap skip. The i386-pc allow-prefix
#            is GRUB's BIOS-boot module tree: 32-bit protected-mode code by
#            design (grub-mkimage inputs, never ld.so-loaded), declared here
#            explicitly and mirrored by the grub recipe's elf_class contract.
#            Fail-closed: violations, a malformed ELF, an unreadable file,
#            or an EMPTY audit (zero dynamic ELFs = nothing was audited) all
#            refuse the seal.
# ----------------------------------------------------------------------------
NEEDCLOSURE_SCRIPT="$PROJECT_DIR/igos-build/needclosure.py"
step_begin "[4.75/6]"
if [ ! -f "$NEEDCLOSURE_SCRIPT" ]; then
    status_line "ELF NEEDED-closure + word-size backstop" FAIL
    die "NEEDED-closure sweep MISSING at $NEEDCLOSURE_SCRIPT — refusing to seal unaudited"
fi
log "ELF NEEDED-closure + word-size backstop (post-eviction chroot)..."
# The declared 32-bit homes (L28). /usr/lib/grub/i386-pc is GRUB's BIOS-boot
# module tree (grub-mkimage inputs, never ld.so-loaded); /usr/lib32 is the
# multilib twin lane's canonical prefix. The three toolchain 32-bit runtime
# homes (gcc's <ver>/32 multilib crt objects, clang's i386 compiler-rt, rust's
# i686 std) carry versions in their paths, so they are DERIVED from the chroot
# at sweep time rather than hardcoded — a version bump cannot rot the gate.
# Fail-closed either way: a glob that matches nothing adds no prefix, and any
# ELFCLASS32 outside the declared homes still refuses the seal.
ALLOW32_ARGS=(--allow32-prefix /usr/lib/grub/i386-pc --allow32-prefix /usr/lib32)
for d in "$CHROOT"/usr/lib/gcc/*/*/32 \
         "$CHROOT"/usr/lib/clang/*/lib/i386-unknown-linux-gnu \
         "$CHROOT"/opt/rustc-*/lib/rustlib/i686-unknown-linux-gnu; do
    [ -d "$d" ] || continue
    ALLOW32_ARGS+=(--allow32-prefix "${d#"$CHROOT"}")
done
# Stream-then-verdict (pipefail-safe). This sweep runs for many minutes and
# emits 15s heartbeats; the prior capture-then-log idiom batched them into
# one post-exit dump, defeating the progress indicator (observed live
# 2026-07-18 — every heartbeat stamped the same second). Under pipefail the
# pipeline rc IS the sweep's rc (logpipe always returns 0) — the same
# streaming idiom as the mksquashfs invocation below; fail-closed unchanged.
python3 "$NEEDCLOSURE_SCRIPT" --chroot "$CHROOT" "${ALLOW32_ARGS[@]}" 2>&1 | logpipe "[needclosure]" && nc_rc=0 || nc_rc=$?
if [ "$nc_rc" -ne 0 ]; then
    detail "a NEEDED with no shipped same-width provider is a runtime failure sealed"
    detail "silently; a wrong-width ELF on the ISO violates the width contract."
    status_line "ELF NEEDED-closure + word-size backstop" FAIL
    die "NEEDED-closure sweep failed (rc=$nc_rc) — refusing to build squashfs"
fi
status_line "ELF NEEDED-closure + word-size backstop" PASS

# ----------------------------------------------------------------------------
# Step 4.76: setuid/setgid + special-ownership inventory gate (L29).
#            The GE-01 corpus shipped every privileged binary stripped to
#            0755 (the staging chokepoint's blanket chown cleared the bits —
#            kernel behavior on chown) and NOTHING refused the seal. This
#            gate fail-closes both directions against the tracked inventory
#            (config/setuid-inventory.txt): a present entry with a stripped
#            bit / flattened ownership refuses, and ANY suid/sgid file not
#            in the inventory refuses (setuid-injection arm). Absent paths
#            are skipped — presence is owned by 4.5 verify_paths + eviction.
# ----------------------------------------------------------------------------
SETUID_GATE="$PROJECT_DIR/scripts/check-setuid-inventory.py"
step_begin "[4.76/6]"
if [ ! -f "$SETUID_GATE" ]; then
    status_line "setuid/setgid inventory gate" FAIL
    die "setuid-inventory gate MISSING at $SETUID_GATE — refusing to seal unaudited"
fi
log "setuid/setgid inventory gate (post-eviction chroot)..."
sg_out="$(python3 "$SETUID_GATE" --chroot "$CHROOT" \
        --inventory "$PROJECT_DIR/config/setuid-inventory.txt" 2>&1)" && sg_rc=0 || sg_rc=$?
[ -n "$sg_out" ] && printf '%s\n' "$sg_out" | logpipe "[setuid]"
if [ "$sg_rc" -ne 0 ]; then
    detail "a stripped suid bit ships a system with no working privilege"
    detail "escalation; an unexpected suid file is a potential injection."
    status_line "setuid/setgid inventory gate" FAIL
    die "setuid-inventory gate failed (rc=$sg_rc) — refusing to build squashfs"
fi
status_line "setuid/setgid inventory gate" PASS

# ----------------------------------------------------------------------------
# Step 4.8: install-integrity — stage the trust triplet into $CHROOT/install/
#           (Option 1: verity-sealed INTO the squashfs) before mksquashfs.
# ----------------------------------------------------------------------------
# This is the ONLY place that decides what trust state the sealed squashfs
# ships with — it owns everything under $CHROOT/install/.
#
# Release mode: copy the operator-signed {manifest, .sig, release-key} from
# the host signing-staging dir (SIGNED_MANIFEST_DIR, default the build dir
# where phase_manifest emitted the manifest + the phase_manifest ENFORCED
# PAUSE instructed the operator to place the signed set) into $CHROOT/install/,
# then run the fail-closed §4B gate (check-install-integrity-staging.sh):
# triplet present + non-empty, signature verifies against the staged key,
# every staged archive appears in the manifest. Any failure => refuse to seal.
#
# Dev mode (UNSIGNED_TEST=1): stage the explicit IGOS_DEV_ALLOW_UNVERIFIED
# marker instead — the sanctioned, loud dev seam the installer keys its skip
# off of (NEVER file-absence). No signed triplet required.
#
# On success either mode emits a host-side build marker (INTEGRITY_MARKER);
# build-iso.sh's Class-A gate asserts that marker because it cannot peer
# inside the already-sealed squashfs.
INSTALL_STAGING="$CHROOT/install"
SIGNED_MANIFEST_DIR="${SIGNED_MANIFEST_DIR:-/mnt/intergenos/build}"
STAGING_GATE="$(dirname "$0")/check-install-integrity-staging.sh"
INTEGRITY_MARKER="${INTEGRITY_MARKER:-${OUTPUT_DIR}/.install-integrity-staging.marker}"
# Always clear any stale marker so a failed/partial Step 4.8 can never leave a
# previous build's marker for build-iso to trust.
rm -f "$INTEGRITY_MARKER" 2>/dev/null || true

step_begin "[4.8/6]"
if [ "${UNSIGNED_TEST:-0}" = "1" ]; then
    log "install-integrity: UNSIGNED_TEST=1 — staging dev-allow marker..."
    mkdir -p "$INSTALL_STAGING"
    {
        echo "IGOS_DEV_ALLOW_UNVERIFIED"
        echo "# This is an UNSIGNED_TEST / dev ISO. The installer's archive-"
        echo "# integrity gate is bypassed EXPLICITLY because this marker is"
        echo "# present (never because files are merely absent). Secure Boot"
        echo "# OFF required. NOT a release artifact."
    } > "$INSTALL_STAGING/IGOS_DEV_ALLOW_UNVERIFIED"
    # Remove any stale signed triplet so a dev build cannot ship a half-set
    # that an installer might mistake for release-grade.
    rm -f "$INSTALL_STAGING/intergenos-archive-manifest.txt" \
          "$INSTALL_STAGING/intergenos-archive-manifest.txt.sig" \
          "$INSTALL_STAGING/intergenos-release-key.asc" 2>/dev/null || true
    mkdir -p "$(dirname "$INTEGRITY_MARKER")"
    {
        echo "install-integrity-staging: DEV (UNSIGNED_TEST)"
        echo "marker: IGOS_DEV_ALLOW_UNVERIFIED"
    } > "$INTEGRITY_MARKER"
    detail "dev-allow marker staged at $INSTALL_STAGING/IGOS_DEV_ALLOW_UNVERIFIED"
    status_line "install-integrity staging (dev marker)" DONE
else
    log "install-integrity: staging signed trust triplet into $INSTALL_STAGING..."
    src_manifest="$SIGNED_MANIFEST_DIR/intergenos-archive-manifest.txt"
    src_sig="$SIGNED_MANIFEST_DIR/intergenos-archive-manifest.txt.sig"
    src_key="$SIGNED_MANIFEST_DIR/intergenos-release-key.asc"
    for f in "$src_manifest" "$src_sig" "$src_key"; do
        if [ ! -s "$f" ]; then
            detail "signed trust artifact missing or empty: $f"
            detail "a release build requires the operator-signed triplet in"
            detail "$SIGNED_MANIFEST_DIR/ (see the phase_manifest enforced pause)."
            detail "for a dev ISO, re-run with UNSIGNED_TEST=1."
            status_line "install-integrity staging (signed triplet)" FAIL
            die "signed trust triplet incomplete — refusing to build squashfs"
        fi
    done
    mkdir -p "$INSTALL_STAGING"
    # Ensure no stale dev marker rides into a release squashfs.
    rm -f "$INSTALL_STAGING/IGOS_DEV_ALLOW_UNVERIFIED" 2>/dev/null || true
    cp "$src_manifest" "$INSTALL_STAGING/intergenos-archive-manifest.txt"
    cp "$src_sig"      "$INSTALL_STAGING/intergenos-archive-manifest.txt.sig"
    cp "$src_key"      "$INSTALL_STAGING/intergenos-release-key.asc"

    if [ ! -f "$STAGING_GATE" ]; then
        status_line "install-integrity staging (signed triplet)" FAIL
        die "$STAGING_GATE not found — cannot assert the staged triplet; refusing to build squashfs"
    fi
    REPO_ROOT_FOR_GATE="$(cd "$(dirname "$0")/.." && pwd)"
    # Capture rc explicitly (the log pipe below would otherwise mask it — same
    # errexit-safe idiom as Step 4.7's sweep).
    gate_out="$(INTEGRITY_MARKER="$INTEGRITY_MARKER" bash "$STAGING_GATE" \
        --install-dir "$INSTALL_STAGING" \
        --archive-dir "$ARCHIVE_DIR" \
        --repo-root "$REPO_ROOT_FOR_GATE" \
        --emit-marker "$INTEGRITY_MARKER" 2>&1)" && gate_rc=0 || gate_rc=$?
    [ -n "$gate_out" ] && printf '%s\n' "$gate_out" | logpipe "[integrity]"
    if [ "$gate_rc" -ne 0 ]; then
        detail "the release trust triplet is absent, the signature does not verify"
        detail "against the staged release key, or a staged archive is unmanifested."
        status_line "install-integrity staging (signed triplet)" FAIL
        die "install-integrity staging gate failed — refusing to build squashfs"
    fi
    status_line "install-integrity staging (signed triplet)" PASS
fi

# ----------------------------------------------------------------------------
# Step 4.85: fail-closed squashfs ownership gate (F41, decided 2026-07-22)
# ----------------------------------------------------------------------------
# Every file about to enter the squashfs must trace to an installed package's
# manifest (pkm.db), the pkm state rules, or a REASONED allowlist entry
# (config/squashfs-ownership-allowlist.txt); unowned empty directories fail
# the same way (the shipped-skeleton class a live-ISO eval read as payload).
# Runs LAST before mksquashfs so it sees the exact tree that ships —
# after the prune, the archive-exclusion derivation, and every clean step.
OWNERSHIP_GATE="$(dirname "$0")/check-squashfs-ownership.py"
OWNERSHIP_ALLOWLIST="/mnt/intergenos/config/squashfs-ownership-allowlist.txt"
step_begin "[4.85/6]"
if [ "$ISO_PREP" = "1" ]; then
    log "squashfs ownership gate (no unowned files ship)..."
    own_out="$(python3 "$OWNERSHIP_GATE" \
        --chroot "$CHROOT" \
        --allowlist "$OWNERSHIP_ALLOWLIST" \
        --archive-excludes "$MIRROR_ARCHIVE_EXCLUDES_FILE" 2>&1)" \
        && own_rc=0 || own_rc=$?
    [ -n "$own_out" ] && printf '%s\n' "$own_out" | logpipe "[ownership]"
    if [ "$own_rc" -eq 0 ]; then
        status_line "squashfs ownership gate" PASS
    else
        status_line "squashfs ownership gate" FAIL
        log "unowned content in the shipping tree — disposition every finding"
        log "(recipe manifest fix, chroot cleanup, or a reasoned allowlist"
        log "entry) before rebuilding the squashfs"
        die "squashfs ownership gate failed (rc=$own_rc)"
    fi
else
    warn "squashfs ownership gate SKIPPED (ISO_PREP=$ISO_PREP diagnostic build)"
    status_line "squashfs ownership gate" SKIP
fi

# ----------------------------------------------------------------------------
# Step 5: mksquashfs
# ----------------------------------------------------------------------------
step_begin "[5/6]"
log "running mksquashfs..."
detail "(this is the slow step — 3-10 minutes depending on tier scope)"

# Stale output: mksquashfs APPENDS by default unless told otherwise. Use
# `-noappend` to force a fresh filesystem.
NOAPPEND="-noappend"
[ ! -f "$OUTPUT" ] && NOAPPEND=""   # nothing to append to anyway

# Exclusion semantics: mksquashfs's `-e <path>` excludes the path entry +
# everything under it. To preserve mount-point dirs we use `-e <path>/*` with
# `-wildcards`, which excludes CONTENTS while leaving the directory intact.
#
# Excluded entirely (path + contents):
#   - mnt/intergenos    — build tree + sources, not part of installed system
#   - sources           — LFS build sources (if any leaked into root)
#   - var/cache         — package-manager + tool caches; rebuilt at first use
#   - var/log/journal/* — wipe journal but keep var/log/ structure
#   - root/.bash_history, home/*/.bash_history — already removed in step 3
#
# Excluded contents-only (directory preserved as empty mount point):
#   - tmp/*             — runtime tmpfs target
#   - var/tmp/*         — package-build tmpfs target
#   - proc/*, sys/*     — defensive (should be empty post-unmount anyway)
#   - dev/*, run/*      — defensive (same)
#
# ISO/MIRROR scoping: handled by Step 2.5 above (pkm iso-prep). The
# historical Path-a `mksquashfs -ef <exclusion-file>` derivation is
# gone — the chroot at this point contains ONLY iso_include:true
# packages because pkm iso-prep already pruned the MIRROR-only set.
# See the Step 2.5 block + scripts/derive-iso-exclusions.py docstring
# for the curation-walk history (D-014 2026-05-20, walk amendment
# 2026-05-28).

# Build-artifact stragglers that pkm doesn't know about (and therefore
# can't be evicted by iso-prep). These come from build-time tools
# leaving files outside any pkm-tracked package — distinct from the
# MIRROR-package surface.
EXTRA_EXCLUDES=()
# gid_Module_* — LibreOffice build-time artifacts that leaked to chroot root
for f in "$CHROOT"/gid_Module_*; do
    [ -e "$f" ] || continue
    EXTRA_EXCLUDES+=(-e "${f#"$CHROOT"/}")
done
# /home/*/.bash_history (only if /home users exist)
for h in "$CHROOT"/home/*/; do
    [ -d "$h" ] || continue
    user_dir="${h#"$CHROOT"/}"
    user_dir="${user_dir%/}"
    if [ -f "$CHROOT/$user_dir/.bash_history" ]; then
        EXTRA_EXCLUDES+=(-e "$user_dir/.bash_history")
    fi
done
# /root/.bash_history (always check)
[ -f "$CHROOT/root/.bash_history" ] && EXTRA_EXCLUDES+=(-e 'root/.bash_history')

# Note: -wildcards INTENTIONALLY OMITTED. The tmp/*, var/tmp/*, proc/*,
# sys/*, dev/*, run/* patterns the prior version of this script used
# were defensive against pre-unmount leftovers, but step-3 cleanup
# already truncates those dirs so excluding the dir itself is
# sufficient. Mount-point preservation is enforced by the audit at the
# end of this script; if mksquashfs accidentally drops a mount-point
# dir, that audit catches the regression.
# --- Build-user ownership guard (defense-in-depth, 2026-06-04) -----------
# NO file in the shipping rootfs may be owned by a regular user (uid >= 1000,
# excluding nobody=65534). The live/installed system creates its human users
# at runtime, not at build time, so any uid>=1000 file is a build-user (e.g.
# uid 1000) ownership leak from a `cp -a`/`cp -p` that preserved the repo
# source owner — which would ship /etc or /usr files owned by the live
# `intergenos` user (the uid-1000 privesc class: files/ overlay, then
# chroot-build-bootloader.sh `cp -p` of /usr/lib/intergen scripts). Catch +
# normalize here, at the squashfs boundary, regardless of which cp introduced
# it: auto-fix to root so a stray file can never ship, and WARN loudly so the
# introducing cp site gets fixed at the source.
detail "build-user ownership guard (no uid>=1000 in shipping tree)..."
_OWN_LEAK=$(find "$CHROOT" -xdev \( -uid +999 ! -uid 65534 \) \
    -not -path "$CHROOT/mnt/intergenos/*" -not -path "$CHROOT/sources/*" \
    -not -path "$CHROOT/var/cache/*" -not -path "$CHROOT/var/log/journal/*" \
    -not -path "$CHROOT/proc/*" -not -path "$CHROOT/sys/*" \
    -not -path "$CHROOT/dev/*" -not -path "$CHROOT/run/*" \
    -not -path "$CHROOT/tmp/*" 2>/dev/null)
if [ -n "$_OWN_LEAK" ]; then
    _OWN_N=$(printf '%s\n' "$_OWN_LEAK" | wc -l)
    warn "$_OWN_N build-user-owned file(s) (uid>=1000) in shipping tree — normalizing to root:root:"
    printf '%s\n' "$_OWN_LEAK" | sed "s#^$CHROOT##" | logpipe
    printf '%s\n' "$_OWN_LEAK" | xargs -r -d '\n' chown -h root:root
    detail "normalized to root:root — FIX the cp -a/-p site that introduced these (it preserved build-user uid)"
    status_line "build-user ownership guard (normalized — fix the cp site)" DONE
else
    detail "no build-user-owned files in shipping tree"
    status_line "build-user ownership guard" PASS
fi

if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_event squashfs_compress_start output_path="$OUTPUT" comp="$COMP" block_size="1M" processors="$JOBS"
fi
_MKSQ_START_MS=$(date +%s%3N)
# Heartbeat: -no-progress keeps the TTY bar out of the log, but mksquashfs
# then emits NOTHING for the entire multi-minute compression — a fresh
# builder tailing the log reads that silence as a hang. Emit the output
# file's growth every 20s until mksquashfs exits (size+elapsed is enough
# to prove liveness; version-independent, unlike -percentage).
detail "compressing (zstd-19, ${JOBS} workers) — a full corpus takes tens of minutes; 20s heartbeats below"
( _HB_START=$(date +%s)
  while :; do
      sleep 20
      _HB_SZ=$(du -BM "$OUTPUT" 2>/dev/null | cut -f1)
      echo "heartbeat: ${_HB_SZ:-0M} written, $(( ( $(date +%s) - _HB_START ) / 60 ))m elapsed"
  done ) | logpipe "[mksquashfs]" &
_MKSQ_HB_PID=$!
# Route mksquashfs's own output through the prefix chokepoint. The `&& || `
# capture also FIXES a latent errexit bug: the prior bare
# `mksquashfs ...; _MKSQ_RC=$?` exited via set -e on failure, so the explicit
# die() (and its build_failure trace event) below could never fire. With
# pipefail (set at top) the pipeline rc IS mksquashfs's rc.
mksquashfs "$CHROOT" "$OUTPUT" \
    $NOAPPEND \
    -comp "$COMP" \
    -b 1M \
    -Xcompression-level 19 \
    -processors "$JOBS" \
    -no-progress \
    -e mnt/intergenos \
    -e mnt/hot-storage \
    -e sources \
    -e var/cache \
    -e var/log/journal \
    -e tmp/lost+found \
    -e var/tmp/lost+found \
    -e .igos-chroot-ownership-normalized \
    "${EXTRA_EXCLUDES[@]}" \
    "${MIRROR_ARCHIVE_EXCLUDES[@]}" 2>&1 | logpipe "[mksquashfs]" && _MKSQ_RC=0 || _MKSQ_RC=$?
kill "$_MKSQ_HB_PID" 2>/dev/null || true
wait "$_MKSQ_HB_PID" 2>/dev/null || true
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    _MKSQ_DUR=$(( $(date +%s%3N) - _MKSQ_START_MS ))
    _MKSQ_SIZE=0
    [ -f "$OUTPUT" ] && _MKSQ_SIZE=$(stat -c%s "$OUTPUT" 2>/dev/null || echo 0)
    _MKSQ_SHA=""
    [ -f "$OUTPUT" ] && _MKSQ_SHA=$(sha256sum "$OUTPUT" | awk '{print $1}')
    trace_event squashfs_compress_end output_path="$OUTPUT" \
        size_bytes::=$_MKSQ_SIZE sha="$_MKSQ_SHA" \
        rc::=$_MKSQ_RC duration_ms::=$_MKSQ_DUR
fi
if [ $_MKSQ_RC -ne 0 ]; then
    status_line "mksquashfs" FAIL
    die "mksquashfs failed (rc=$_MKSQ_RC)"
fi

# Force the squashfs's data + metadata to disk before unsquashfs reads it.
sync
status_line "mksquashfs" DONE

# Post-build sanity check (regression detector for
# feedback_mksquashfs_keep_pseudofs_dirs).
#
# Earlier version called `unsquashfs -l` 5x in a loop with `2>/dev/null`,
# producing false-positive FATALs when unsquashfs hit a transient read
# error: stderr was suppressed, empty stdout → grep failed silently → loop
# concluded dirs were "missing". This verifier is now brutally honest:
#   - single cached listing (one read, not five)
#   - retry-with-backoff on transient unreadable + short-listing
#   - stderr is captured and printed on failure (no silent failures)
#   - distinguishes "file unreadable" from "dirs missing" (different fixes)
#   - sanity-check listing length (>100 entries) before trusting it
log "verifying mount-point directories in output..."
# (still step 5 — the post-mksquashfs regression detector)

LISTING_FILE=$(mktemp)
STDERR_FILE=$(mktemp)
trap 'rm -f "$LISTING_FILE" "$STDERR_FILE"' EXIT

ATTEMPT_SUCCESS=0
for attempt in 1 2 3; do
    : >"$LISTING_FILE"
    : >"$STDERR_FILE"
    if unsquashfs -l "$OUTPUT" >"$LISTING_FILE" 2>"$STDERR_FILE"; then
        LINES=$(wc -l <"$LISTING_FILE")
        if [ "$LINES" -gt 100 ]; then
            detail "unsquashfs -l attempt $attempt: SUCCESS ($LINES entries)"
            ATTEMPT_SUCCESS=1
            break
        fi
        detail "unsquashfs -l attempt $attempt: short listing ($LINES lines) — possible flush race"
    else
        rc=$?
        detail "unsquashfs -l attempt $attempt: failed (rc=$rc); stderr follows:"
        logpipe "[unsquashfs]" <"$STDERR_FILE" || true
    fi
    if [ "$attempt" -lt 3 ]; then
        detail "retrying in 2s after sync"
        sync
        sleep 2
    fi
done

if [ "$ATTEMPT_SUCCESS" -ne 1 ]; then
    die "unsquashfs -l produced no complete listing after 3 attempts — squashfs may be unreadable, corrupt, or still flushing"
fi

MISSING=""
for mnt in proc sys dev run tmp; do
    if ! grep -qE "^squashfs-root/${mnt}\$" "$LISTING_FILE"; then
        MISSING="$MISSING $mnt"
    fi
done
rm -f "$LISTING_FILE" "$STDERR_FILE"
trap - EXIT

if [ -n "$MISSING" ]; then
    status_line "mount-point directory verify" FAIL
    die "mount-point dirs MISSING from squashfs:$MISSING — regression of feedback_mksquashfs_keep_pseudofs_dirs"
fi
detail "all mount-point dirs present: /proc /sys /dev /run /tmp"
status_line "mount-point directory verify" PASS

# ----------------------------------------------------------------------------
# Step 6: veritysetup format — build the merkle hashtree for dm-verity
# ----------------------------------------------------------------------------
# Replaces the prior whole-file sha256 verification pattern (init.sh used to
# sha256sum the entire ~9 GiB squashfs at every boot, taking ~73s at USB
# read speeds and dominating the click-to-desktop latency). With the hashtree
# alongside the squashfs and the root hash sealed in the signed UKI cmdline,
# the kernel verifies each 4 KiB block as it's actually read — same crypto
# guarantee, zero up-front cost.
#
# The hashtree is a tiny file (~0.1% of squashfs size). It lands at
# ${OUTPUT}.verity and ships on the ISO alongside the squashfs. The root
# hash and the data-block count get written to ${OUTPUT}.verity-params so
# the bootloader phase can inject them into the live-mode UKI cmdline.
#
# We prefer the chroot's veritysetup-static binary so the build VM doesn't
# need a host-side install. cryptsetup-static now ships both
# cryptsetup-static (LUKS) and veritysetup-static (dm-verity) per the
# 2026-05-28 lever-4 package extension. Fall back to host's veritysetup
# if running outside a normal chroot context (e.g. ad-hoc squashfs
# regeneration on a clean VM).
step_begin "[6/6]"
log "generating dm-verity hashtree..."

VERITYSETUP_BIN=""
for candidate in \
    "${CHROOT}/usr/lib/intergen/veritysetup-static" \
    /usr/lib/intergen/veritysetup-static \
    /usr/sbin/veritysetup \
    /sbin/veritysetup ; do
    if [ -x "$candidate" ]; then
        VERITYSETUP_BIN="$candidate"
        break
    fi
done
[ -n "$VERITYSETUP_BIN" ] || die "veritysetup not found — install cryptsetup-static in chroot or veritysetup on host"
detail "using veritysetup: $VERITYSETUP_BIN"

VERITY_HASHTREE="${OUTPUT}.verity"
VERITY_PARAMS="${OUTPUT}.verity-params"

# Wipe stale outputs so a rerun is clean (veritysetup format refuses to
# overwrite an existing hashtree file).
rm -f "$VERITY_HASHTREE" "$VERITY_PARAMS"

# `--hash=sha256` matches what init.sh expects + what CONFIG_DM_VERITY supports
# without optional kernel modules. `--data-block-size=4096` + `--hash-block-size=4096`
# match the kernel page size + are the dm-verity defaults — making them explicit
# pins the layout against future cryptsetup default-changes that would silently
# invalidate the on-media format.
VERITYSETUP_LOG=$(mktemp)
trap 'rm -f "$VERITYSETUP_LOG"' EXIT

_VERITY_START_MS=$(date +%s%3N)
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_event veritysetup_format_start output="$OUTPUT" hashtree="$VERITY_HASHTREE" tool="$VERITYSETUP_BIN"
fi
if ! "$VERITYSETUP_BIN" format \
        --hash=sha256 \
        --data-block-size=4096 \
        --hash-block-size=4096 \
        "$OUTPUT" "$VERITY_HASHTREE" > "$VERITYSETUP_LOG" 2>&1; then
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_event veritysetup_format_end rc::=1 duration_ms::=$(( $(date +%s%3N) - _VERITY_START_MS ))
    fi
    cat "$VERITYSETUP_LOG" >&2
    die "veritysetup format failed"
fi
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_event veritysetup_format_end rc::=0 duration_ms::=$(( $(date +%s%3N) - _VERITY_START_MS ))
fi

# Route veritysetup's format report through the prefix chokepoint. (The prior
# `| tee -a "${LOG_FILE:-/dev/null}"` was vestigial — LOG_FILE is never set in
# this script, so the tee was a no-op; dropped.)
logpipe "[veritysetup]" <"$VERITYSETUP_LOG" || true

# Parse veritysetup format output. The output format is stable across
# cryptsetup 2.x: key: value lines with the root hash on its own line.
ROOT_HASH=$(awk '/^Root hash:/ {print $NF}' "$VERITYSETUP_LOG")
DATA_BLOCKS=$(awk '/^Data blocks:/ {print $NF}' "$VERITYSETUP_LOG")
# veritysetup prints these as "Hash block size:   4096 [bytes]" — the numeric
# value is the SECOND-to-last field; $NF would capture the literal "[bytes]"
# unit. (Cosmetic: these params are informational; init.sh reads the real values
# from the verity superblock, not this file. Fixed for correctness 2026-06-02.)
HASH_BLOCK_SIZE=$(awk '/^Hash block size:/ {print $(NF-1)}' "$VERITYSETUP_LOG")
DATA_BLOCK_SIZE=$(awk '/^Data block size:/ {print $(NF-1)}' "$VERITYSETUP_LOG")
SALT=$(awk '/^Salt:/ {print $NF}' "$VERITYSETUP_LOG")

[ -n "$ROOT_HASH" ] || die "veritysetup format succeeded but root hash could not be parsed from output"
[ -n "$DATA_BLOCKS" ] || die "veritysetup format succeeded but data block count could not be parsed"

# Emit a machine-readable params file. The bootloader phase reads this to
# inject the root hash into the live-mode UKI cmdline. Salt + block sizes
# are recorded for completeness; the verity superblock at the start of the
# hashtree file actually carries them so init.sh's veritysetup-open call
# does not need to pass them explicitly.
cat > "$VERITY_PARAMS" <<EOF
# InterGenOS dm-verity params for ${OUTPUT##*/}
# Generated by build-squashfs.sh on $(date -u --iso-8601=seconds)
ROOT_HASH=${ROOT_HASH}
DATA_BLOCKS=${DATA_BLOCKS}
DATA_BLOCK_SIZE=${DATA_BLOCK_SIZE}
HASH_BLOCK_SIZE=${HASH_BLOCK_SIZE}
SALT=${SALT}
HASH_ALGO=sha256
EOF

VERITY_SIZE_KB=$(($(stat -c%s "$VERITY_HASHTREE") / 1024))
detail "verity hashtree: $VERITY_HASHTREE (${VERITY_SIZE_KB} KiB)"
detail "verity params:   $VERITY_PARAMS"
detail "root hash:       $ROOT_HASH"
status_line "dm-verity hashtree" DONE
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_event dmverity_seal volume_path="$OUTPUT" hashtree="$VERITY_HASHTREE" \
        root_hash="$ROOT_HASH" data_blocks::="$DATA_BLOCKS" \
        data_block_size::=$DATA_BLOCK_SIZE hash_block_size::=$HASH_BLOCK_SIZE \
        salt="$SALT" hash_algo=sha256
fi

# Summary.
SIZE_MB=$(($(stat -c%s "$OUTPUT") / 1024 / 1024))
SHA=$(sha256sum "$OUTPUT" | awk '{print $1}')

# install-integrity (FLAG B): bind the Step-4.8 staging marker to THIS squashfs's
# identity. build-iso's Class-A gate asserts marker.squashfs-sha256 == the
# squashfs it is about to package, so a standalone build-iso run cannot trust a
# stale prior-build marker over a rebuilt tree. Reuses the SHA already computed
# above (free); the marker was written by Step 4.8 (release or dev path).
if [ -n "${INTEGRITY_MARKER:-}" ] && [ -f "$INTEGRITY_MARKER" ]; then
    echo "squashfs-sha256: $SHA" >> "$INTEGRITY_MARKER"
fi

step_begin ""
log "summary:"
detail "squashfs:   $OUTPUT"
detail "size:       ${SIZE_MB} MB"
detail "sha256:     $SHA"
detail "verity:     $VERITY_HASHTREE (${VERITY_SIZE_KB} KiB)"
detail "root hash:  $ROOT_HASH"
status_line "build-squashfs complete" DONE

if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_event squashfs_phase_end output="$OUTPUT" rc::=0 \
        size_bytes::=$(stat -c%s "$OUTPUT") sha="$SHA" \
        duration_ms::=$(( $(date +%s%3N) - _SQ_START_MS ))
    trace_close
fi
