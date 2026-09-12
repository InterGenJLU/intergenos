#!/bin/bash
# kernel-paths.sh — the release-stamped kernel paths the nvidia hooks share.
# Sourced by post-install.sh and sign-module.sh (installed beside them under
# /var/lib/pkm/hooks/nvidia/). Defines functions only; no side effects.
#
# An InterGenOS kernel release string is <version>-igos-<release>
# (6.18.51-igos-1) — never a bare "-igos" suffix. The module directory is
# /lib/modules/<version>-igos-<release>; the prepared kernel source the second
# kernel pass stages is /usr/src/linux-<version> — the BARE version. Two hooks
# got both wrong (independent review, 2026-09-11): a fallback glob of *-igos
# that could match no real directory, and a fallback source path that stripped
# only a trailing -igos. The derivations live here now, tested against fake
# trees by tests/test_nvidia_hook_kernel_release_paths.py.

# nvidia_kver_from_modules <modules-dir>
#   Print the ONE release-stamped kernel under <modules-dir> that has a prepared
#   build tree. Print nothing when there is none. When there is more than one,
#   print nothing, name the candidates on stderr and return 2 — a hook must not
#   guess which kernel a user meant.
nvidia_kver_from_modules() {
    local dir=${1:?modules dir} candidate
    local -a found=()
    for candidate in "$dir"/*-igos-*; do
        [ -d "$candidate/build" ] || continue
        found+=("${candidate##*/}")
    done
    case "${#found[@]}" in
        0) return 0 ;;
        1) printf '%s\n' "${found[0]}"; return 0 ;;
        *) printf 'more than one staged kernel with a build tree, refusing to guess: %s\n' "${found[*]}" >&2
           return 2 ;;
    esac
}

# nvidia_kernel_version <kver>
#   The bare upstream version of a release-stamped kernel string
#   (6.18.51-igos-1 -> 6.18.51).
nvidia_kernel_version() {
    printf '%s\n' "${1%%-igos-*}"
}

# nvidia_sign_file_path <kver> [modules-dir] [usr-src]
#   Print the kernel's sign-file: the prepared build tree's copy first, else the
#   staged source tree's (linux-<bare version>). When neither is executable,
#   print nothing, name both tried paths on stderr and return 1.
nvidia_sign_file_path() {
    local kver=${1:?kernel release} modules=${2:-/lib/modules} usrsrc=${3:-/usr/src}
    local primary="$modules/$kver/build/scripts/sign-file"
    local fallback="$usrsrc/linux-$(nvidia_kernel_version "$kver")/scripts/sign-file"
    if [ -x "$primary" ]; then printf '%s\n' "$primary"; return 0; fi
    if [ -x "$fallback" ]; then printf '%s\n' "$fallback"; return 0; fi
    printf 'scripts/sign-file not found for kernel %s\n  Tried: %s\n  Tried: %s\n' \
        "$kver" "$primary" "$fallback" >&2
    return 1
}
