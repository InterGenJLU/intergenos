#!/bin/bash
# nvidia rebuild-modules — compile + sign + install the open kernel modules
# for a specific kernel version.
#
# Args:
#   $1 — kernel version (default: $(uname -r))
#
# Called by:
#   - /var/lib/pkm/hooks/nvidia/post-install at pkm install nvidia time
#   - /var/lib/pkm/hooks/linux-kernel/post-install on kernel upgrade (chains
#     here so the upgraded kernel has signed nvidia modules without a
#     manual operator step)
#
# Operation:
#   1. cd /usr/src/nvidia-open-${NV_VERSION}/
#   2. make modules SYSSRC=/lib/modules/$KVER/build SYSOUT=/lib/modules/$KVER/build
#   3. For each nvidia*.ko: sign with /var/lib/intergen/mok/mok.{key,crt}
#      via the kernel's scripts/sign-file (PKCS#7 signature appended).
#   4. Install signed .ko files to /lib/modules/$KVER/extra/nvidia/.
#   5. depmod $KVER to rebuild module dependency cache.

set -uo pipefail

LOGFILE=/var/log/intergen-nvidia-postinstall.log
KVER="${1:-$(uname -r)}"
NV_VERSION="580.159.04"   # MUST match package version
NV_SRC="/usr/src/nvidia-open-${NV_VERSION}"

log() {
    local msg="[nvidia:rebuild] $*"
    echo "$msg" >&2
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') $msg" >> "$LOGFILE" 2>/dev/null || true
}

if [ ! -d "$NV_SRC" ]; then
    log "FATAL: NVIDIA source tree missing at $NV_SRC"
    log "Re-run pkm install nvidia to re-deploy the source tarball."
    exit 1
fi

if [ ! -d "/lib/modules/$KVER/build" ]; then
    log "FATAL: kernel build tree missing at /lib/modules/$KVER/build"
    log "Cannot rebuild without kernel headers. Is linux-kernel-pass2 installed for $KVER?"
    exit 1
fi

# Compile
cd "$NV_SRC"
log "compiling kernel-open/ against /lib/modules/$KVER/build"

JOBS=$(nproc 2>/dev/null || echo 1)
if ! make modules -j"$JOBS" KERNEL_UNAME="$KVER" \
    SYSSRC="/lib/modules/$KVER/build" \
    SYSOUT="/lib/modules/$KVER/build" 2>&1 | tee -a "$LOGFILE" >&2; then
    log "FATAL: make modules failed for kernel $KVER"
    log "Diagnose: $LOGFILE"
    log "After fixing the cause, re-run the FULL hook — /var/lib/pkm/hooks/nvidia/post-install —"
    log "NOT this script alone: post-install's later steps (the UKI rebuild that merges"
    log "cmdline.d/40-nvidia.conf) never ran, and re-running only rebuild-modules leaves the"
    log "kernel booting without nvidia-drm.modeset=1 (KMS off, dGPU outputs dark — PI-Z19)."
    log "Common causes, most likely first:"
    log "  - a missing build tool: grep the log above for 'command not found' /"
    log "    'Error 127' (cc, gcc, make, ld — PI-Z16 was a missing /usr/bin/cc)"
    log "  - NVIDIA driver $NV_VERSION not yet compatible with this kernel:"
    log "    check https://github.com/NVIDIA/open-gpu-kernel-modules/issues"
    exit 1
fi

# Sign each module with the per-machine MOK
SIGNED_OK=0
SIGNED_FAIL=0
for mod in nvidia nvidia-modeset nvidia-drm nvidia-uvm nvidia-peermem; do
    KO="$NV_SRC/kernel-open/$mod.ko"
    if [ ! -f "$KO" ]; then
        # Some module sub-dirs nest one deeper depending on NVIDIA layout
        KO="$NV_SRC/kernel-open/$mod/$mod.ko"
    fi
    if [ -f "$KO" ]; then
        log "signing $mod.ko with MOK"
        if /var/lib/pkm/hooks/nvidia/sign-module.sh "$KO" "$KVER" 2>&1 | tee -a "$LOGFILE" >&2; then
            SIGNED_OK=$((SIGNED_OK + 1))
        else
            SIGNED_FAIL=$((SIGNED_FAIL + 1))
        fi
    else
        log "WARNING: $mod.ko not produced by make modules"
    fi
done
log "signed $SIGNED_OK module(s); $SIGNED_FAIL signing failure(s)"

# Install signed .ko files
INSTALL_DIR="/lib/modules/$KVER/extra/nvidia"
mkdir -p "$INSTALL_DIR"
INSTALLED=0
for mod in nvidia nvidia-modeset nvidia-drm nvidia-uvm nvidia-peermem; do
    KO="$NV_SRC/kernel-open/$mod.ko"
    if [ ! -f "$KO" ]; then
        KO="$NV_SRC/kernel-open/$mod/$mod.ko"
    fi
    if [ -f "$KO" ]; then
        install -m 644 "$KO" "$INSTALL_DIR/$mod.ko"
        INSTALLED=$((INSTALLED + 1))
    fi
done
log "installed $INSTALLED module(s) to $INSTALL_DIR"

# Rebuild module dep cache so modprobe picks up the new modules
log "running depmod $KVER"
if ! depmod "$KVER" 2>&1 | tee -a "$LOGFILE" >&2; then
    log "WARNING: depmod $KVER returned non-zero — module load may not work until manual depmod"
fi

log "nvidia module rebuild complete for kernel $KVER (installed=$INSTALLED signed=$SIGNED_OK)"

# PI-Z17: MOK-signed modules can only LOAD when the kernel trusts the MOK,
# and the MOK reaches the kernel keyring exclusively via shim's MokListRT —
# which shim populates ONLY under Secure Boot. Our kernel enforces
# CONFIG_MODULE_SIG_FORCE=y regardless of SB state, so on an SB-OFF boot
# the freshly-built modules are REFUSED at load ("Key was rejected by
# service"; empty .machine keyring). Found on the first SB-OFF NVIDIA
# install (Zephyrus GE-02): build+sign reported success, the dGPU stayed
# driverless, and the user discovered it via a dead HDMI port and a failed
# nvidia-smi. The build + signing above are still worthwhile (everything
# is staged for the SB flip) — but SAY IT LOUDLY, never let the user walk
# into a silently-dark dGPU.
SB_STATE="$(mokutil --sb-state 2>/dev/null || true)"
if printf '%s' "$SB_STATE" | grep -qi "disabled"; then
    log "════════════════════════════════════════════════════════════════"
    log "NOTICE: Secure Boot is DISABLED on this system."
    log "The NVIDIA modules are built and MOK-signed, but this kernel"
    log "enforces module signatures unconditionally, and the signing key"
    log "(your Machine Owner Key) only reaches the kernel's trusted"
    log "keyring via Secure Boot's shim. Until Secure Boot is enabled"
    log "and the MOK enrolled, these modules CANNOT load — the NVIDIA"
    log "GPU stays on no driver (outputs wired to it stay dark, and"
    log "nvidia-smi cannot communicate with the driver)."
    log "To activate: enable Secure Boot in firmware setup and complete"
    log "the MOK enrollment prompt (queued at install) on the next boot."
    log "════════════════════════════════════════════════════════════════"
fi
exit 0
