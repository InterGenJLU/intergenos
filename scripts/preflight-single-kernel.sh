#!/bin/bash
# preflight-single-kernel.sh — fail-closed staged-kernel exclusivity gate.
#
# Asserts the inspected root holds EXACTLY ONE staged kernel: one
# /usr/lib/modules/<kver> tree and one /boot/vmlinuz-<kver>, mutually
# agreeing on <kver>. A superseded kernel release leaves a twin behind
# (the module dir and vmlinuz are RELEASE-named, so a release bump changes
# the path and the old tree is orphaned rather than overwritten), and the
# downstream consumers pick ambiguously: create-image.sh symlinked the
# first glob match (alphabetical — the OLD release sorts first), and
# squashfs ships every module tree it finds.
#
# Origin: ge9b-02 burn, 2026-07-12 — the linux-kernel r3→r4 rebuild on the
# reverted candidate substrate left 6.18.10-igos-3 beside -igos-4; only the
# nvidia recipe's own single-kernel assertion caught it, and only because
# nvidia happened to be in that delta. This gate is the universal net,
# wired at every entry point into the build (decided).
#
# Usage: preflight-single-kernel.sh [--root <path>] [--allow-none] [--expect <kver>]
#   --root <path>   root to inspect (default /; pass the chroot path when
#                   running host/guest-side, omit when running in-chroot)
#   --allow-none    0 staged kernels is acceptable (pre-kernel phases /
#                   empty chroot); MORE THAN ONE always fails
#   --expect <kver> additionally assert the single staged kernel is exactly
#                   this version-release string (e.g. 6.18.10-igos-4)
#
# Exit: 0 = pass · 1 = violation (listing + remedy printed) · 2 = usage
set -u

ROOT="/"
ALLOW_NONE=0
EXPECT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --root)       ROOT="${2:?--root needs a path}"; shift 2 ;;
        --allow-none) ALLOW_NONE=1; shift ;;
        --expect)     EXPECT="${2:?--expect needs a kver}"; shift 2 ;;
        *) echo "preflight-single-kernel: unknown arg: $1" >&2; exit 2 ;;
    esac
done
ROOT="${ROOT%/}"

shopt -s nullglob
mod_trees=( "${ROOT}/usr/lib/modules"/*/ )
kernels=( "${ROOT}/boot"/vmlinuz-* )
shopt -u nullglob

# An absent root is only meaningful pre-chroot (phase_validate on a
# from-scratch run) — and only when /boot is ALSO kernel-free. The old
# check returned before inspecting /boot, so a root with staged kernels
# but no /usr (a half-torn or half-built tree) passed as "pre-chroot".
if [ ! -d "${ROOT}/usr" ]; then
    if [ "${#kernels[@]}" -gt 0 ]; then
        echo "preflight-single-kernel: FAIL — root ${ROOT:-/} has no /usr but" \
             "carries ${#kernels[@]} /boot/vmlinuz-* artifact(s) — inconsistent root" >&2
        exit 1
    fi
    if [ "$ALLOW_NONE" = "1" ]; then
        echo "preflight-single-kernel: PASS (no populated root at ${ROOT:-/} — pre-chroot)"
        exit 0
    fi
    echo "preflight-single-kernel: FAIL — root ${ROOT:-/} is not a populated system root" >&2
    exit 1
fi

mod_names=()
for d in "${mod_trees[@]}"; do
    d="${d%/}"
    mod_names+=( "${d##*/}" )
done
krn_names=()
for k in "${kernels[@]}"; do
    krn_names+=( "${k##*/vmlinuz-}" )
done

fail() {
    echo "preflight-single-kernel: FAIL — $*" >&2
    echo "  module trees (${#mod_names[@]}): ${mod_names[*]:-<none>}" >&2
    echo "  boot kernels (${#krn_names[@]}): ${krn_names[*]:-<none>}" >&2
    echo "  Remedy: prune the superseded release's /usr/lib/modules/<kver> tree" >&2
    echo "  and /boot/vmlinuz-<kver> (the kernel phase driver's prune step owns" >&2
    echo "  this; a twin here means a kernel deploy bypassed it). Never ship a" >&2
    echo "  root with two staged kernels." >&2
    exit 1
}

if [ "${#mod_names[@]}" -eq 0 ] && [ "${#krn_names[@]}" -eq 0 ]; then
    if [ "$ALLOW_NONE" = "1" ]; then
        echo "preflight-single-kernel: PASS (no staged kernel yet; --allow-none)"
        exit 0
    fi
    fail "no staged kernel found (expected exactly one)"
fi

[ "${#mod_names[@]}" -eq 1 ] || fail "expected exactly one module tree, found ${#mod_names[@]}"
[ "${#krn_names[@]}" -eq 1 ] || fail "expected exactly one /boot/vmlinuz-*, found ${#krn_names[@]}"
[ "${mod_names[0]}" = "${krn_names[0]}" ] || \
    fail "module tree (${mod_names[0]}) and vmlinuz (${krn_names[0]}) disagree"

# Artifact realness — names and counts are not a kernel. The globs above
# match dangling symlinks and empty files, so a half-staged or torn deploy
# could satisfy every name check while shipping nothing bootable.
[ -f "${kernels[0]}" ] && [ -s "${kernels[0]}" ] || \
    fail "vmlinuz-${krn_names[0]} is not a non-empty regular file (dangling symlink or truncated staging)"
mod_dir="${mod_trees[0]%/}"
[ -d "$mod_dir" ] && [ ! -L "$mod_dir" ] || \
    fail "module tree ${mod_names[0]} is not a real directory"
[ -s "${mod_dir}/modules.dep" ] || \
    fail "module tree ${mod_names[0]} lacks a non-empty modules.dep — depmod never ran (half-staged kernel)"

if [ -n "$EXPECT" ] && [ "${mod_names[0]}" != "$EXPECT" ]; then
    fail "staged kernel ${mod_names[0]} != expected ${EXPECT}"
fi

echo "preflight-single-kernel: PASS (exactly one staged kernel: ${mod_names[0]})"
exit 0
