#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# lib32-nvidia 580.159.04 — NVIDIA proprietary 32-bit userspace (multilib).
#
# The 32-bit TWIN of extra/nvidia. It consumes the SAME NVIDIA .run payload as
# the 64-bit recipe and ships the COMPAT32 (32-bit) userspace libraries the
# 64-bit recipe deliberately skips (its lib glob walks the .run top level =
# NATIVE only; the 32-bit blobs live under ./32/). This closes the gaming
# closure's 32-bit-to-dGPU gap: a 32-bit title's Vulkan/GLX calls reach the
# NVIDIA dGPU only through the proprietary 32-bit ICD + vendor libs installed
# here, under /usr/lib32.
#
# It is a PREBUILT-payload package — nothing is compiled. configure() extracts
# the .run; do_install() lays the ./32/ vendor libs into /usr/lib32, reproduces
# the manifest's COMPAT32 symlinks, and installs the i686 Vulkan ICD. The
# open-gpu-kernel-modules tarball (source[0]) is present only to satisfy the
# orchestrator's source[0]-must-be-a-tarball auto-extract (the .run cannot be
# auto-extracted); it is NOT used here — the kernel module is the 64-bit
# sibling's concern.
#
# GLVND SPLIT (mirrors the 64-bit recipe's GBC001 fix): libglvnd owns the
# client dispatcher sonames (libEGL/libGL/libGLX/libOpenGL/libGLESv*/
# libGLdispatch). lib32-libglvnd owns their 32-bit copies. We must NOT ship
# NVIDIA's own copies of those sonames (they would outrank glvnd's in the
# 32-bit ldconfig and break EGL/GL dispatch); only NVIDIA's *_nvidia vendor
# libs, which glvnd routes to via the vendor JSON. Same split, same reason,
# 32-bit width.

NV_VERSION="580.159.04"
RUN_FILE="NVIDIA-Linux-x86_64-${NV_VERSION}.run"

# libglvnd-owned client sonames — must never come from nvidia (see header).
# Anchors lib<STEM>.so followed by '.' or end-of-string so it matches both the
# versioned client libs (libEGL.so.1) and the bare dev symlink (libEGL.so),
# without matching the *_nvidia vendor libs (libEGL_nvidia.so.*).
GLVND_OWNED_RE='^lib(EGL|GL|GLX|OpenGL|GLESv1_CM|GLESv2|GLdispatch)\.so(\.|$)'

configure() {
    set -e
    # The orchestrator extracted source[0] (open-gpu-kernel-modules) into cwd.
    # We do not use it; just note the parent for the sibling .run extract.
    SRC_PARENT="$(dirname "$PWD")"

    # Extract the NVIDIA .run (source[1]) WITHOUT executing its interactive
    # installer — --extract-only lands the payload into a directory, exactly
    # as the 64-bit recipe does. NEVER auto-extracted (it is source[1]).
    cd "$SRC_PARENT"
    rm -rf nvidia-run
    sh "${IGOS_SOURCES}/${RUN_FILE}" --extract-only --target nvidia-run
    NV_RUN_SRC="$SRC_PARENT/nvidia-run"

    # The 32-bit payload MUST be present, or this package has nothing to ship.
    if [ ! -d "$NV_RUN_SRC/32" ]; then
        echo "ERROR: $NV_RUN_SRC/32 absent — the .run payload carries no COMPAT32" \
             "(32-bit) userspace; lib32-nvidia cannot be built from it." >&2
        exit 1
    fi
    echo "$NV_RUN_SRC" > /tmp/.lib32-nvidia-run-src
}

build() {
    # Prebuilt payload — nothing to compile. The 32-bit vendor libs ship as
    # extracted from the .run.
    echo "lib32-nvidia: prebuilt .run payload — no compile step."
}

check() {
    set -e
    # No runtime test is possible in the chroot (no GPU, no 32-bit loader
    # target) — same environment-bounded shape as the 64-bit sibling.
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        true
}

do_install() {
    set -e
    NV_RUN_SRC=$(cat /tmp/.lib32-nvidia-run-src)

    # ---- 32-bit userspace libraries from ./32/ -> /usr/lib32/ --------------
    # DERIVED from the payload (never a hardcoded lib list): every versioned
    # .so blob in ./32/, minus the libglvnd-owned client sonames (the split).
    install -d -m 755 "$DESTDIR/usr/lib32"
    shipped=0; split=0
    for so in "$NV_RUN_SRC"/32/*.so.${NV_VERSION}; do
        [ -f "$so" ] || continue
        base=$(basename "$so")
        if printf '%s\n' "$base" | grep -Eq "$GLVND_OWNED_RE"; then
            echo "[lib32-nvidia:do_install] glvnd-split: NOT shipping libglvnd-owned $base" \
                 "(lib32-libglvnd provides the 32-bit dispatcher; nvidia registers" \
                 "its 32-bit vendor libs via the glvnd vendor JSON)"
            split=$((split+1)); continue
        fi
        install -m 755 "$so" "$DESTDIR/usr/lib32/$base"
        shipped=$((shipped+1))
    done
    if [ "$shipped" -eq 0 ]; then
        echo "ERROR: no 32-bit *_nvidia vendor libs installed from $NV_RUN_SRC/32 —" \
             "the .run payload shape changed; refusing an empty package." >&2
        exit 1
    fi
    echo "[lib32-nvidia:do_install] 32-bit libs: shipped=$shipped glvnd-excluded=$split"

    # ---- Reproduce the manifest's COMPAT32 symlinks into /usr/lib32/ -------
    # Mirror of the 64-bit recipe's NATIVE symlink loop, keyed on m_arch=COMPAT32
    # and targeting /usr/lib32. Fail-closed accounting: every COMPAT32 *_SYMLINK
    # line is partitioned into {created, glvnd-excluded, dangling-pruned} and the
    # count is asserted against an independent parse, so a manifest-shape drift
    # cannot silently drop a loader alias (the class that produced the 64-bit
    # PI-Z20 GBM crash). Manifest grammar (6 or 7 fields; a 7th means a <path>
    # column exists): <name> <perm> <TYPE> <ARCH> [<path>] <target> MODULE:<m>.
    if [ ! -f "$NV_RUN_SRC/.manifest" ]; then
        echo "ERROR: $NV_RUN_SRC/.manifest absent — cannot reproduce COMPAT32 symlinks." >&2
        exit 1
    fi
    sym_glvnd=0; sym_created_paths=""; sym_accounted=""
    while read -r m_name m_perm m_type m_arch m_f5 m_f6 m_f7; do
        case "$m_type" in *_SYMLINK) ;; *) continue ;; esac
        [ "$m_arch" = "COMPAT32" ] || continue
        if [ -n "$m_f7" ]; then m_target="$m_f6"; else m_target="$m_f5"; fi
        sym_accounted="$sym_accounted $m_name"
        if printf '%s\n' "$m_name" | grep -Eq "$GLVND_OWNED_RE"; then
            sym_glvnd=$((sym_glvnd+1))
            echo "[lib32-nvidia:do_install] glvnd-split: NOT reproducing libglvnd-owned symlink $m_name"
            continue
        fi
        # All shipped 32-bit libs live flat in /usr/lib32; the target is taken
        # verbatim (already relative to the symlink's own directory).
        ln -sf "$m_target" "$DESTDIR/usr/lib32/$m_name" || {
            echo "ERROR: failed to create COMPAT32 symlink /usr/lib32/$m_name -> $m_target" >&2
            exit 1
        }
        sym_created_paths="$sym_created_paths usr/lib32/$m_name"
    done < "$NV_RUN_SRC/.manifest"

    # Prune any created link whose target is not in our ship set (would dangle).
    sym_created=0; sym_pruned=0; sym_pruned_list=""
    for rel in $sym_created_paths; do
        if [ -e "$DESTDIR/$rel" ]; then
            sym_created=$((sym_created+1))
        else
            sym_pruned_list="$sym_pruned_list $rel(-> $(readlink "$DESTDIR/$rel"))"
            rm -f "$DESTDIR/$rel"; sym_pruned=$((sym_pruned+1))
        fi
    done
    [ -n "$sym_pruned_list" ] && echo "[lib32-nvidia:do_install] pruned" \
        "$sym_pruned non-shipped-target COMPAT32 symlink(s):$sym_pruned_list"

    # Anti-rot: every COMPAT32 *_SYMLINK line MUST be accounted (independent parse).
    sym_total=$(awk '$4=="COMPAT32" && $3 ~ /_SYMLINK$/' "$NV_RUN_SRC/.manifest" | wc -l)
    if [ "$((sym_created + sym_glvnd + sym_pruned))" -ne "$sym_total" ]; then
        echo "ERROR: COMPAT32 symlink accounting mismatch — created=$sym_created" \
             "glvnd=$sym_glvnd pruned=$sym_pruned != COMPAT32 *_SYMLINK total=$sym_total." >&2
        for n in $(awk '$4=="COMPAT32" && $3 ~ /_SYMLINK$/ {print $1}' "$NV_RUN_SRC/.manifest"); do
            printf '%s\n' "$sym_accounted" | grep -qw "$n" || echo "    MISSING: $n" >&2
        done
        exit 1
    fi
    echo "[lib32-nvidia:do_install] COMPAT32 symlinks: created=$sym_created" \
         "glvnd-excluded=$sym_glvnd pruned=$sym_pruned (total=$sym_total, all accounted)"

    # ---- i686 Vulkan ICD -> /usr/share/vulkan/icd.d/nvidia_icd.i686.json ---
    # The ICD's library_path is a BARE soname (libGLX_nvidia.so.0), which the
    # 32-bit Vulkan loader (lib32-vulkan-loader) resolves against /usr/lib32 —
    # so the JSON body is arch-neutral; only the FILENAME must be distinct from
    # the 64-bit nvidia_icd.json so both ICDs coexist. Prefer a 32-bit ICD the
    # payload ships under ./32/; else use the top-level (bare-soname) ICD. Fail
    # CLOSED if neither is present (the package's whole purpose).
    install -d -m 755 "$DESTDIR/usr/share/vulkan/icd.d"
    icd_src=""
    for cand in "$NV_RUN_SRC/32/nvidia_icd.json" "$NV_RUN_SRC/nvidia_icd.json"; do
        if [ -s "$cand" ]; then icd_src="$cand"; break; fi
    done
    if [ -z "$icd_src" ]; then
        echo "ERROR: no nvidia_icd.json found in the .run (checked ./32/ and top level) —" \
             "cannot ship the i686 Vulkan ICD, which is the reason this package exists." >&2
        exit 1
    fi
    install -m 644 "$icd_src" "$DESTDIR/usr/share/vulkan/icd.d/nvidia_icd.i686.json"
    echo "[lib32-nvidia:do_install] i686 Vulkan ICD from ${icd_src#$NV_RUN_SRC/} -> nvidia_icd.i686.json"
}
