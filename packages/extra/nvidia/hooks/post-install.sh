#!/bin/bash
# nvidia post-install — runs at `pkm install nvidia` time on the live user
# system after pkm extracts the archive. Fires from pkm/installer.py:854
# against /var/lib/pkm/hooks/nvidia/post-install.
#
# Operational sequence:
#   1. Hardware check — Turing+ NVIDIA GPU present? Else refuse install.
#   2. Kernel-version detection — pick the running kernel ($(uname -r)).
#   3. Build the open-gpu-kernel-modules against the user's installed
#      kernel headers + sign each .ko with the per-machine MOK + install
#      signed modules to /lib/modules/<kver>/extra/nvidia/.
#   4. depmod + try modprobe -r nouveau (graceful: warn-and-continue
#      if nouveau is in-use by the framebuffer console).
#
# This hook is non-fatal per pkm/installer.py — its exit status is logged
# but does not roll back the archive deploy. The user can re-run the
# rebuild manually via /var/lib/pkm/hooks/nvidia/rebuild-modules.

set -uo pipefail

LOGFILE=/var/log/intergen-nvidia-postinstall.log
mkdir -p "$(dirname "$LOGFILE")" 2>/dev/null || true

log() {
    local msg="[nvidia:post-install] $*"
    echo "$msg" >&2
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') $msg" >> "$LOGFILE" 2>/dev/null || true
}

log "starting (PKM package: ${PKM_PACKAGE_NAME:-?} ${PKM_PACKAGE_VERSION:-?})"

# Step 1: hardware check
if [ -x /var/lib/pkm/hooks/nvidia/check-hardware.sh ]; then
    if ! /var/lib/pkm/hooks/nvidia/check-hardware.sh 2>&1 | tee -a "$LOGFILE" >&2; then
        log "FATAL: hardware check failed — no Turing+ NVIDIA GPU detected on this system"
        log "Aborting module rebuild. nouveau (already in kernel) handles pre-Turing NVIDIA hardware."
        log "If this is wrong (e.g. lspci returned stale data), re-run /var/lib/pkm/hooks/nvidia/rebuild-modules manually."
        exit 1
    fi
fi

# Step 2: kernel-version detection. uname -r gives the running kernel; in
# the Forge-install chroot context this resolves to the host kernel which
# is wrong. We probe /lib/modules/ for the InterGenOS kernel name pattern.
KVER=""
if [ -n "${PKM_PACKAGE_ROOT:-}" ] && [ "$PKM_PACKAGE_ROOT" = "/" ]; then
    # Live-system install — uname -r is reliable.
    KVER=$(uname -r)
fi
# Fallback: scan /lib/modules for the InterGenOS -igos suffix.
if [ -z "$KVER" ] || [ ! -d "/lib/modules/$KVER/build" ]; then
    for candidate in /lib/modules/*-igos; do
        [ -d "$candidate/build" ] || continue
        KVER="${candidate##*/}"
        break
    done
fi

if [ -z "$KVER" ]; then
    log "FATAL: no kernel with /lib/modules/<ver>/build/ symlink found"
    log "linux-kernel-pass2 must be installed before nvidia can rebuild modules."
    exit 1
fi

log "rebuilding nvidia modules for kernel $KVER"

if [ ! -d "/lib/modules/$KVER/build" ]; then
    log "FATAL: /lib/modules/$KVER/build missing — linux-kernel-pass2 not properly installed"
    exit 1
fi

# Step 3: invoke the rebuild (factored so the linux-kernel hook can call
# the same code path on kernel upgrade — see hooks/rebuild-modules.sh).
if ! /var/lib/pkm/hooks/nvidia/rebuild-modules "$KVER"; then
    log "WARNING: rebuild-modules exited non-zero for kernel $KVER"
    log "Modules may not load on next boot. Diagnose with:"
    log "  cat $LOGFILE"
    log "  /var/lib/pkm/hooks/nvidia/rebuild-modules $KVER"
    exit 1
fi

# Step 4: try to evict nouveau if currently loaded. Best-effort: nouveau
# is in-use when the framebuffer console is active, in which case unload
# fails with EBUSY and the user needs to reboot anyway for nvidia-drm to
# take over the framebuffer.
if lsmod 2>/dev/null | grep -q '^nouveau'; then
    log "nouveau is loaded — attempting modprobe -r nouveau"
    if modprobe -r nouveau 2>/dev/null; then
        log "nouveau unloaded"
    else
        log "nouveau is in-use (likely by the framebuffer console) — reboot required for nvidia to load"
    fi
fi

# Step 5: trigger UKI rebuild so /etc/kernel/cmdline.d/40-nvidia.conf
# (shipped by this package — nvidia-drm.modeset=1 + nvidia-drm.fbdev=1)
# gets merged into the signed UKI .cmdline section. linux-kernel post-
# install reads cmdline.d fragments at rebuild time. Without this trigger,
# the cmdline params don't take effect until the next kernel update.
# Skip in chroot install context — no UKI on the build VM.
if [ -n "${PKM_PACKAGE_ROOT:-}" ] && [ "$PKM_PACKAGE_ROOT" = "/" ]; then
    KERNEL_HOOK="/var/lib/pkm/hooks/linux-kernel/post-install"
    if [ -x "$KERNEL_HOOK" ]; then
        log "triggering UKI rebuild via $KERNEL_HOOK so cmdline.d/40-nvidia.conf takes effect"
        if "$KERNEL_HOOK" 2>&1 | tee -a "$LOGFILE" >&2; then
            log "UKI rebuild complete — nvidia cmdline params active on next boot"
        else
            log "WARNING: UKI rebuild failed (exit $?) — nvidia cmdline params take effect on next kernel update instead"
        fi
    else
        log "$KERNEL_HOOK absent — UKI rebuild skipped (cmdline params take effect on next kernel update)"
    fi
else
    log "chroot install context — skipping UKI rebuild trigger (no UKI on build VM)"
fi

log "post-install complete for kernel $KVER"
exit 0
