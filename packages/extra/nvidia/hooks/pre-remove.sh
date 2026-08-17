#!/bin/bash
# nvidia pre-remove — fires at `pkm remove nvidia` time before file removal.
#
# Operation:
#   1. Stop NVIDIA-related services (best-effort — daemon may not be
#      running).
#   2. Unload NVIDIA kernel modules in dependency order.
#   3. Purge /lib/modules/*/extra/nvidia/ — the .ko files were built
#      post-install on the user's machine, so they're not in the package
#      manifest and pkm's standard file-removal walk does not catch them.
#   4. depmod -a to refresh the module cache without the nvidia entries.

set -uo pipefail

LOGFILE=/var/log/intergen-nvidia-postinstall.log

log() {
    local msg="[nvidia:pre-remove] $*"
    echo "$msg" >&2
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') $msg" >> "$LOGFILE" 2>/dev/null || true
}

log "starting"

# Stop systemd units (best-effort; ignore failure if not running / not
# installed). systemd-aware: skip in chroot install context where
# the target system has no live systemd.
if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    for unit in nvidia-persistenced.service nvidia-suspend.service \
                nvidia-resume.service nvidia-hibernate.service; do
        systemctl stop "$unit" 2>/dev/null || true
        systemctl disable "$unit" 2>/dev/null || true
    done
fi

# Unload modules in dependency order. nvidia-uvm + nvidia-drm + nvidia-modeset
# all depend on nvidia, so they must unload first.
if command -v modprobe >/dev/null 2>&1; then
    for mod in nvidia_uvm nvidia_drm nvidia_modeset nvidia_peermem nvidia; do
        if lsmod 2>/dev/null | grep -q "^$mod"; then
            log "modprobe -r $mod"
            if ! modprobe -r "$mod" 2>/dev/null; then
                log "WARNING: $mod is in-use, cannot unload (reboot may be required to complete remove)"
            fi
        fi
    done
fi

# Purge built modules. These were compiled by rebuild-modules and never
# in the pkm archive manifest, so without this step they linger in
# /lib/modules/*/extra/nvidia/ even after pkm-remove completes.
for extra_dir in /lib/modules/*/extra/nvidia; do
    [ -d "$extra_dir" ] || continue
    log "removing built modules at $extra_dir"
    rm -rf "$extra_dir"
done

# Refresh module cache so depmod no longer references nvidia
if command -v depmod >/dev/null 2>&1; then
    depmod -a 2>/dev/null || true
fi

log "complete (reboot to revert to nouveau)"
exit 0
