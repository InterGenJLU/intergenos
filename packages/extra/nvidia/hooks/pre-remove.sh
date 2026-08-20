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
        # Narrowed instead of masked. The named condition is that a unit in
        # this list may legitimately not be installed on a given machine, and
        # stop or disable of a unit that does not exist returns 1 for "does
        # not exist" (measured 2026-08-19) — an impossible operation, not a
        # failed one. Existence is tested first, so whatever remains is a real
        # failure and is reported rather than absorbed.
        #
        # `systemctl cat` is a valid existence test ONLY here, where the
        # /run/systemd/system test above has already established that a live
        # manager owns this root. Measured 2026-08-19: on a live root it
        # returns 0 for a present unit and 1 for an absent one, but inside a
        # manager-less chroot it returns 0 even for an absent unit. Do not
        # copy this guard into a context that can run without a manager.
        systemctl cat -- "$unit" >/dev/null 2>&1 || continue

        systemctl stop "$unit"; rc=$?
        [ "$rc" -eq 0 ] || log "WARNING: 'systemctl stop $unit' exited $rc — the module unload below may find the device busy"

        systemctl disable "$unit"; rc=$?
        [ "$rc" -eq 0 ] || log "WARNING: 'systemctl disable $unit' exited $rc — the unit may still be started on the next boot"
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
