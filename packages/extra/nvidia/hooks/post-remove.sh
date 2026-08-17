#!/bin/bash
# nvidia post-remove — fires at `pkm remove nvidia` time AFTER pkm has
# removed the package files. By this point, /etc/kernel/cmdline.d/40-
# nvidia.conf is gone, but the UKI on the ESP still embeds the old
# cmdline (including nvidia-drm.modeset=1 + nvidia-drm.fbdev=1) until
# the next UKI rebuild.
#
# This hook triggers an immediate UKI rebuild via the linux-kernel
# post-install hook, so the next boot's UKI .cmdline section reflects
# the actual configured state (nvidia params removed) rather than the
# pre-uninstall state.
#
# Best-effort: failure here does not roll back the remove. Worst-case
# the user reboots once with the stale cmdline; nouveau still gets
# loaded (modules are gone) but nvidia-drm.modeset=1 lingers as a
# kernel arg that no module consumes. Next kernel update rebuilds the
# UKI cleanly.

set -uo pipefail

LOGFILE=/var/log/intergen-nvidia-postinstall.log

log() {
    local msg="[nvidia:post-remove] $*"
    echo "$msg" >&2
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') $msg" >> "$LOGFILE" 2>/dev/null || true
}

log "starting"

# Trigger UKI rebuild via the linux-kernel post-install hook. The hook
# reads /etc/kernel/cmdline + merges /etc/kernel/cmdline.d/*.conf
# fragments — both now reflect the post-uninstall state.
KERNEL_HOOK="/var/lib/pkm/hooks/linux-kernel/post-install"
if [ -x "$KERNEL_HOOK" ]; then
    log "triggering UKI rebuild via $KERNEL_HOOK"
    if "$KERNEL_HOOK" 2>&1 | tee -a "$LOGFILE" >&2; then
        log "UKI rebuild complete — next boot uses cmdline without nvidia params"
    else
        log "WARNING: UKI rebuild failed (exit $?) — stale UKI may include nvidia cmdline params until next kernel update"
    fi
else
    log "$KERNEL_HOOK absent or non-executable — skipping UKI rebuild trigger"
fi

log "complete"
exit 0
