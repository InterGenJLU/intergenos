#!/bin/bash
# linux-kernel post-install hook — D-005 Phase A UKI rebuild + sign-with-user-MOK
#
# Fires at RUNTIME after pkm deploys a new linux-kernel package archive
# (Forge install on first-boot OR `pkm upgrade linux-kernel` on a live
# installed system). Picks up the just-installed vmlinuz from /boot/ +
# bundles it as a UKI under /boot/efi/EFI/Linux/ signed with the user's
# local MOK keypair per D-005 Option A.
#
# pkm provides env: PKM_PACKAGE_NAME, PKM_PACKAGE_VERSION, PKM_PACKAGE_ROOT.
#
# Phase A scope (this hook):
#   - Build UKI from new vmlinuz + intel-ucode.img + initramfs.img + cmdline
#   - Sign UKI with /var/lib/intergen/mok/mok.{key,crt}, asking the owner for
#     the key's passphrase; a signing step that cannot get it REFUSES (exit 1)
#     and leaves the previous release's signed image in place as the boot target
#   - Install UKI to /boot/efi/EFI/Linux/intergenos-<kver>.efi, and only after
#     its signature has been verified — an unsigned image is never placed where
#     the boot chain would pick it up
#   - Degrade without signing (exit 0) only where there is nothing to enforce it:
#     no builder yet during first-install ordering, or no key on a machine whose
#     firmware is not enforcing Secure Boot. Both say which case they are.
#
# The D-005 phases this header used to list as still to be done have landed,
# and saying otherwise above code that runs them sent readers away from it:
#   Phase B — landed. The ESP-size pre-checks are in the installer
#             (installer/backend/disks.py), and this hook repoints the ESP GRUB
#             menu to the kernel it just installed, further down.
#   Phase C — the per-machine MOK keypair is generated at install time
#             (installer/backend/install.py calls mok.generate_mok_keypair for
#             every EFI install), and this hook signs the UKI with it. The
#             MokManager enrolment experience is not asserted here either way.
#   Phase D — landed, and described where it happens: the LUKS-case
#             busybox+cryptsetup unlock initramfs is regenerated per kernel and
#             bundled into the unified kernel image, and the plain-install case
#             bundles the minimal stub instead. See the activation-chain block
#             below, which this hook then runs.
#
# B-041 cmdline drift: resolves via UKI .cmdline section bundling (sourced
# from /etc/kernel/cmdline; falls back to /proc/cmdline if unset).

set -uo pipefail

# Dedicated persistent log file for kernel install/upgrade operations.
# Kernel install is security-critical (UKI signing, FDE initramfs regen,
# Secure Boot composition) and users + ops need a stable place to look
# when boot-time troubleshooting (per docs/users/full-disk-encryption.md
# troubleshooting table). log() writes to BOTH stderr (so pkm's terminal
# / journalctl surface keeps showing it live) and this file (so a
# subsequent boot or recovery session can read the trail without re-
# running the failing operation). Append-only; rotation is left to the
# host's standard logrotate stack.
LOGFILE=/var/log/intergen-kernel-postinstall.log
mkdir -p "$(dirname "$LOGFILE")" 2>/dev/null || true

log() {
    local msg="[linux-kernel:post-install] $*"
    echo "$msg" >&2
    # Best-effort persistent append. Never let a logging failure
    # break the install — chmod issues, full-disk, ESP-mounted-ro,
    # etc. silently fall through to stderr-only.
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') $msg" >> "$LOGFILE" 2>/dev/null || true
}

# The one place that answers "may this machine sign, and with what". Sourced
# rather than reimplemented here, because the driver module signer asks the same
# question and the two must not answer it differently.
#
# Absent helper = refusal. It is shipped by this package and named in its
# verify_paths, so its absence means the payload on this machine is incomplete,
# and an incomplete payload must not produce a boot image that nothing checked.
MOK_HELPER=/usr/lib/intergen/mok-signing.sh
if [ -r "$MOK_HELPER" ]; then
    # shellcheck source=/dev/null
    . "$MOK_HELPER"
else
    log "FATAL: $MOK_HELPER is missing, so this hook cannot ask for the signing passphrase."
    log "  No boot image will be built or signed for this kernel, and the ESP keeps the"
    log "  previous release's signed image, which stays bootable. Reinstall the kernel"
    log "  package to restore the helper: sudo pkm reinstall linux-kernel"
    exit 1
fi

# Refuse, in one place, with the whole truth in it. Every refusal below leaves
# the ESP exactly as it was: the previous release's signed image is still there,
# it is still what the boot menu points at, and it still boots. What does NOT
# happen is the boot menu moving to a kernel with no signed image, and what does
# not happen is this hook exiting zero on a kernel that cannot boot.
refuse_to_sign() {
    local why="$1"
    log "REFUSING TO SIGN: $why"
    log "  Kernel $NEW_KVER is installed on disk but is NOT BOOTABLE UNTIL SIGNED."
    log "  Nothing was written to the ESP. The previous release's signed boot image is"
    log "  still there and is still the boot-menu target, so this machine still boots."
    log "  To finish the job with the passphrase in hand, at the console:"
    log "      sudo pkm reinstall linux-kernel"
    exit 1
}

# Has the system this hook is populating ever booted? The predicate is
# systemd's own (machine-id(5), FIRST BOOT SEMANTICS): the marker absent, or
# holding the literal string "uninitialized", means no boot has happened yet.
# An EMPTY file is explicitly NOT a first boot, and that is the case that is
# easy to get backwards. Same resolution, and the same root scoping, as the
# systemd recipe's post_install (packages/core/systemd/build.sh) — pkm runs
# lifecycle hooks on the HOST and passes the target root in PKM_PACKAGE_ROOT,
# so a predicate about that root is read under that root.
#
# WHY THIS HOOK NEEDS IT. Two conditions below are EXPECTED SEQUENCING during
# an install and a REAL FAILURE on a running machine, and until this predicate
# existed the messages could not tell the operator which one had happened:
#   - ukify absent. It is built by the desktop-tier systemd-pass2 recipe
#     (-D ukify=enabled), not by the core-tier systemd recipe
#     (-D ukify=disabled), so the core-tier linux-kernel package extracts and
#     fires this hook before any UKI builder is on the target.
#   - the ESP GRUB menu absent. The installer composes it AFTER the package
#     hooks run, from the installed kernel, so there is nothing to repoint yet.
# Both were measured on the R001.1 install of 2026-08-22, in the installed
# system's own /var/log/intergen-kernel-postinstall.log: the fire at 02:08:58
# found no ukify, the fire at 02:28:09 found it and built and signed the UKI,
# and the menu update at 02:28:09 failed because the menu did not exist yet.
first_install_ordering() {
    local root="${PKM_PACKAGE_ROOT:-/}"
    local marker="${root%/}/etc/machine-id"
    if [ ! -e "$marker" ]; then
        return 0
    fi
    if [ "$(cat "$marker" 2>/dev/null)" = "uninitialized" ]; then
        return 0
    fi
    return 1
}

# Identify the newly-installed kernel — pick the most-recently-modified
# /boot/vmlinuz-*-igos* image. Forge install + pkm install land vmlinuz at
# /boot/vmlinuz-<version>-igos-<release> per the package's do_install convention
# (the trailing -<release> is why the glob is *-igos* and not *-igos).
# Bash-builtin only (no sed/awk dependency). Forge install fires this
# hook at PHASE_PACKAGES while linux-kernel is extracting (~496/790),
# BEFORE sed-core (alphabetical 's' > 'l'). Any external-command
# pipeline here would exit 127 + script dies silently before the first
# log() call. Surfaced 2026-05-26 install attempt #21: UKI never built,
# ESP missing /EFI/Linux/intergenos-*.efi, GRUB fell back to bare-
# vmlinuz with no initramfs -> kernel panic on first boot. The deeper
# fix is install-order (essentials-first in installer/backend/packages.py);
# this hook stays sed-free as defense-in-depth.
NEW_KVER=""
NEW_KVER_MTIME=0
shopt -s nullglob
for vmlinuz_path in /boot/vmlinuz-*-igos*; do
    mtime=$(stat -c %Y "$vmlinuz_path" 2>/dev/null || echo 0)
    if [ "$mtime" -gt "$NEW_KVER_MTIME" ]; then
        NEW_KVER_MTIME=$mtime
        NEW_KVER="${vmlinuz_path##*/vmlinuz-}"
    fi
done
shopt -u nullglob
if [ -z "$NEW_KVER" ]; then
    log "no /boot/vmlinuz-*-igos found; nothing to do"
    exit 0
fi

log "regenerating UKI for kernel $NEW_KVER (PKM package: ${PKM_PACKAGE_NAME:-?} ${PKM_PACKAGE_VERSION:-?})"

VMLINUZ="/boot/vmlinuz-$NEW_KVER"
INITRD="/boot/initramfs.img"
UCODE_INTEL="/boot/intel-ucode.img"
UCODE_AMD="/boot/amd-ucode.img"
ESP_UKI_DIR="/boot/efi/EFI/Linux"
UKI="$ESP_UKI_DIR/intergenos-$NEW_KVER.efi"
# The image is built HERE first and only takes the name above once its
# signature has been verified. Same directory so the move is a rename on the
# same filesystem. Until that rename the ESP holds exactly what it held before,
# so a refusal, a crash or a power cut leaves the previous release's signed
# image as the boot target rather than a half-written or unsigned one.
UKI_STAGE="$ESP_UKI_DIR/.intergenos-$NEW_KVER.efi.new"
MOK_KEY="/var/lib/intergen/mok/mok.key"
MOK_CERT="/var/lib/intergen/mok/mok.crt"
CMDLINE_FILE="/etc/kernel/cmdline"
MICROCODE_HELPER="/usr/lib/intergen/build-microcode-cpio.sh"

# Regenerate microcode early-load cpios. The intel-ucode package's
# post-install would already have written /boot/intel-ucode.img after its
# own install, but linux-firmware ships AMD microcode blobs without a
# parallel cpio-generation step — the helper covers both vendors and is
# idempotent. Best-effort: failure here drops back to whatever cpios
# (if any) already exist at /boot/ from prior runs or intel-ucode's
# install-time generation.
if [ -x "$MICROCODE_HELPER" ]; then
    log "regenerating microcode early-load cpios via $MICROCODE_HELPER"
    if OUTPUT_DIR=/boot "$MICROCODE_HELPER" >/dev/null 2>&1; then
        log "microcode cpios refreshed (intel=$( [ -f "$UCODE_INTEL" ] && echo yes || echo no ), amd=$( [ -f "$UCODE_AMD" ] && echo yes || echo no ))"
    else
        log "WARNING: $MICROCODE_HELPER failed (exit $?) — falling back to existing /boot/{intel,amd}-ucode.img if present"
    fi
else
    log "$MICROCODE_HELPER absent — using existing /boot/{intel,amd}-ucode.img if present (intel-ucode package may have written intel-ucode.img; amd-ucode.img is only produced when the helper is staged)"
fi

# D-005 Phase D: LUKS install detection. Presence of /etc/crypttab implies
# Forge wired LUKS at install time (per D-001 opt-in LUKS-at-install).
# When LUKS install is detected the UKI must bundle the FDE-initramfs
# (busybox + cryptsetup-static + dm_crypt + ext4 + storage drivers + the
# fde-init.sh entry point from installer/init/fde-init.sh) instead of the
# plain-install minimal microcode cpio.
#
# Phase D activation chain (now landed):
#   /usr/lib/intergen/build-fde-initramfs.sh   — the packager (staged by
#                                                scripts/chroot-build-bootloader.sh)
#   /usr/lib/intergen/fde-init.sh              — the runtime entry point
#                                                (staged by chroot-build)
#   /usr/lib/intergen/cryptsetup-static        — statically-linked
#                                                cryptsetup binary (output
#                                                of packages/core/cryptsetup-static)
#   /usr/lib/intergen/fde-initramfs.cpio.gz    — the cpio this hook bundles
#                                                (regenerated per-kernel
#                                                on every hook fire so pkm
#                                                upgrade of linux-kernel
#                                                stays in sync with new
#                                                /lib/modules/$KVER)
#
# Regeneration on every hook fire ensures freshness across pkm-upgrade
# kernel changes (the build-time cpio's modules would be stale otherwise).
# If the packager or cryptsetup-static are absent (Phase D activation
# chain incomplete on this host), the existing /usr/lib/intergen/
# fde-initramfs.cpio.gz (if any) is used as-is; if no cpio exists, the
# UKI is built without an FDE initramfs and the LUKS install will fail
# to unlock at boot — recovery via grub-loads-vmlinuz with a manually-
# provided initramfs per D-005 fallback semantics.
FDE_INITRD_PATH="/usr/lib/intergen/fde-initramfs.cpio.gz"
FDE_BUILDER="/usr/lib/intergen/build-fde-initramfs.sh"
CRYPTSETUP_STATIC="/usr/lib/intergen/cryptsetup-static"
IS_LUKS_INSTALL="no"
if [ -f /etc/crypttab ] && grep -qE "^[^#]" /etc/crypttab 2>/dev/null; then
    IS_LUKS_INSTALL="yes"
    log "LUKS install detected (/etc/crypttab has active entries)"

    # Regenerate FDE initramfs against the freshly-installed kernel's
    # /lib/modules/$NEW_KVER. Required for pkm-upgrade correctness;
    # build-time cpio is keyed to the original install's KVER and would
    # ship stale modules after kernel upgrade. Graceful degrade if the
    # activation-chain pieces are absent — fall through to whatever
    # cpio (if any) exists at FDE_INITRD_PATH.
    if [ -x "$FDE_BUILDER" ] && [ -x "$CRYPTSETUP_STATIC" ]; then
        log "regenerating FDE initramfs for $NEW_KVER via $FDE_BUILDER"
        if INIT_SCRIPT=/usr/lib/intergen/fde-init.sh \
           BUSYBOX=/usr/bin/busybox.static \
           CRYPTSETUP_STATIC="$CRYPTSETUP_STATIC" \
           MODULES_DIR="/lib/modules/$NEW_KVER" \
           "$FDE_BUILDER" "$NEW_KVER" "$FDE_INITRD_PATH" >/dev/null 2>&1; then
            log "FDE initramfs regenerated at $FDE_INITRD_PATH"
        else
            log "WARNING: $FDE_BUILDER failed (exit $?) — using existing $FDE_INITRD_PATH if present"
        fi
    else
        log "Phase D activation chain incomplete on this host (missing $FDE_BUILDER or $CRYPTSETUP_STATIC) — using existing $FDE_INITRD_PATH if present"
    fi
fi

# Required tool: ukify. It is built by the desktop-tier systemd-pass2 recipe
# (-D ukify=enabled); the core-tier systemd recipe builds without it
# (-D ukify=disabled). Absence therefore means one of two different things,
# and the two get different messages — the message this replaced asserted that
# the install ships no UKI builder at all and that the grub-loads-vmlinuz
# entry is this host's canonical boot path, and neither is true. The signed
# UKI is the canonical path; that entry is the recovery fallback D-005
# preserves. Either way this is never fatal to the kernel install.
if ! command -v ukify >/dev/null 2>&1; then
    if first_install_ordering; then
        log "ukify not yet deployed (first-install ordering) — skipping UKI generation; it is shipped by the desktop-tier systemd-pass2 package, which extracts after this core-tier one, and this hook fires again with the builder present later in the same install."
    else
        log "WARNING: ukify absent on a system that has already booted — NO UKI was built or signed for $NEW_KVER, so the ESP still holds the previous release's UKI and the next boot will run the previous kernel. The signed UKI is this system's canonical boot path; the grub-loads-vmlinuz entry is the recovery fallback only. Fix: install the package that provides ukify (systemd-pass2), then re-run this hook with 'sudo pkm reinstall linux-kernel'."
    fi
    exit 0
fi

# Determine cmdline (B-041 resolution: cmdline travels with UKI .cmdline section).
if [ -f "$CMDLINE_FILE" ]; then
    CMDLINE=$(< "$CMDLINE_FILE")
elif [ -f /proc/cmdline ]; then
    CMDLINE=$(< /proc/cmdline)
    # Strip kernel-injected items (root=, ro/rw, initrd=) that ukify regenerates
    CMDLINE=$(echo "$CMDLINE" | tr ' ' '\n' | grep -v -E '^(BOOT_IMAGE|initrd)=' | tr '\n' ' ')
    log "no /etc/kernel/cmdline; sourced from /proc/cmdline (stripped boot-time injections)"
else
    log "no cmdline source (/etc/kernel/cmdline or /proc/cmdline) — aborting UKI generation"
    exit 0
fi

# Concatenate /etc/kernel/cmdline.d/*.conf fragments (sorted) onto the base cmdline.
# Per Prime Directive: packages that need kernel cmdline params drop a fragment
# here so users can audit every load-bearing flag at `cat /proc/cmdline` — the
# most discoverable single source of truth. Fragments are merged at UKI build
# time + embedded in the signed .cmdline section. Filenames sort lexically
# (40-nvidia.conf, 50-other.conf, etc.) so ordering is deterministic + auditable.
CMDLINE_FRAG_DIR="/etc/kernel/cmdline.d"
if [ -d "$CMDLINE_FRAG_DIR" ]; then
    FRAG_PARTS=""
    for frag in "$CMDLINE_FRAG_DIR"/*.conf; do
        [ -f "$frag" ] || continue
        # Strip blank lines + comments; collapse remaining lines to one
        # whitespace-separated string.
        frag_content=$(grep -v -E '^\s*(#|$)' "$frag" | tr '\n' ' ')
        if [ -n "$frag_content" ]; then
            FRAG_PARTS="$FRAG_PARTS $frag_content"
            log "cmdline fragment merged: $(basename "$frag") -> $frag_content"
        fi
    done
    if [ -n "$FRAG_PARTS" ]; then
        # Append fragments to base cmdline; trim duplicate whitespace.
        CMDLINE=$(echo "$CMDLINE $FRAG_PARTS" | tr -s ' ' | sed 's/^ //;s/ $//')
    fi
fi

# Ensure ESP UKI directory exists
if ! mkdir -p "$ESP_UKI_DIR" 2>/dev/null; then
    log "cannot create $ESP_UKI_DIR — ESP not mounted? Aborting UKI generation; grub-loads-vmlinuz fallback applies."
    exit 0
fi

# Build ukify args
UKIFY_ARGS=(
    "build"
    "--linux=$VMLINUZ"
    "--cmdline=$CMDLINE"
    "--output=$UKI_STAGE"
)
# Whatever happens after this point, the staging file does not survive it.
trap 'rm -f "$UKI_STAGE" 2>/dev/null || true' EXIT
# This hook creates an installed machine's UKI. Omit Intel-only firmware
# when every reported processor is AMD. An unreadable, empty or mixed CPU
# inventory retains both images; portable installation media also keep both.
CPU_VENDORS=$(awk '$1 == "vendor_id" {print $3}' /proc/cpuinfo | sort -u)
if [ "$CPU_VENDORS" = "AuthenticAMD" ]; then
    log "AMD-only processor inventory: omitting Intel microcode from this UKI"
else
    [ -f "$UCODE_INTEL" ] && UKIFY_ARGS+=("--initrd=$UCODE_INTEL")
fi
[ -f "$UCODE_AMD" ]   && UKIFY_ARGS+=("--initrd=$UCODE_AMD")

# D-005 Phase D: initramfs selection.
# LUKS install: bundle the FDE-initramfs cpio (fde-init.sh + busybox +
#   cryptsetup-static + dm_crypt + ext4 + storage drivers) — required for
#   the kernel to unlock the encrypted root before switch_root.
# Plain install: kernel-builtin storage drivers + PARTUUID + rootwait
#   handle root mount (per 2026-04-09 ratification narrowed by D-005);
#   the only initramfs bundled is the optional /boot/initramfs.img if
#   one was produced by Forge (which on plain installs is minimal /
#   empty — present for the few edge-case modules that benefit from
#   early-userspace handling without making the kernel-builtin set even
#   larger).
if [ "$IS_LUKS_INSTALL" = "yes" ]; then
    if [ -f "$FDE_INITRD_PATH" ]; then
        UKIFY_ARGS+=("--initrd=$FDE_INITRD_PATH")
        log "bundling FDE initramfs ($FDE_INITRD_PATH) into UKI"
    else
        log "LUKS install but $FDE_INITRD_PATH missing AND regen-chain absent. UKI will be built WITHOUT FDE initramfs; root unlock at boot will fail. grub-loads-vmlinuz fallback path with manual cryptsetup unlock is the operator recovery per D-005 fallback semantics."
    fi
else
    # Plain install — generic initramfs.img is optional
    [ -f "$INITRD" ] && UKIFY_ARGS+=("--initrd=$INITRD")
fi

# Sign with the machine owner's key (D-005 user-MOK signing model — the project
# signing key NEVER touches user systems; only the owner's own key).
#
# The key is encrypted at rest since 2026-09-17, so this is where the owner is
# asked. Three states and three different answers:
#
#   key present  -> migrate it first if it still has no passphrase, then ask for
#                   the passphrase. No passphrase, no signature, no boot image.
#   key absent,
#   Secure Boot on -> refusal. An unsigned image on a machine whose firmware
#                   enforces signatures is not a lesser outcome, it is an image
#                   that cannot boot, and writing it over a working one would
#                   take the machine down.
#   key absent,
#   Secure Boot off or unreadable -> build unsigned and say so plainly. This is
#                   the install-media and the deliberately-unenrolled case; the
#                   image boots, and the message says what it is missing.
SIGN_THIS_UKI="no"
if [ -f "$MOK_KEY" ] && [ -f "$MOK_CERT" ]; then
    if ! mok_key_is_encrypted "$MOK_KEY"; then
        log "this machine's signing key has no passphrase on it (installed before 2026-09-17)"
        if ! mok_migrate_plain_key "$MOK_KEY"; then
            refuse_to_sign "the signing key is unprotected and the owner did not set a passphrase for it"
        fi
    fi
    if [ -z "${MOK_PASSPHRASE:-}" ]; then
        if ! mok_resolve_passphrase "$MOK_KEY" "the boot image for kernel $NEW_KVER"; then
            refuse_to_sign "no usable passphrase for the machine owner signing key"
        fi
    fi
    UKIFY_ARGS+=(
        "--secureboot-private-key=$MOK_KEY"
        "--secureboot-certificate=$MOK_CERT"
    )
    SIGN_THIS_UKI="yes"
    log "signing the boot image with this machine's own key"
else
    if mok_secure_boot_enabled; then
        refuse_to_sign "Secure Boot is enabled on this machine and there is no signing key at $MOK_KEY, so any image built here could not load"
    fi
    log "no machine owner key at $MOK_KEY — the boot image will be UNSIGNED. That is"
    log "  usable only while Secure Boot stays off in firmware. Turning Secure Boot on"
    log "  without a key enrolled leaves this machine unable to boot the image."
fi

# Build the UKI. Capture ukify's stdout+stderr into LOGFILE for both
# Forge install (ingested post-PHASE_PACKAGES by install.py's
# ingest_kernel_hook_log) and pkm-upgrade (operator reads LOGFILE
# directly). Previously this call used `>/dev/null 2>&1` and swallowed
# every diagnostic — 2026-05-27 install #28 trace, anomaly A: UKI built
# without .initrd/.ucode sections but no log showed why, because the
# only ukify path was the swallowed-output pkm-hook fire.
log "ukify cmdline (${#UKIFY_ARGS[@]} args):"
printf '  %s\n' "${UKIFY_ARGS[@]}" | tee -a "$LOGFILE" >&2
# The passphrase reaches the boot-image signer on standard input. The builder
# runs that signer as a child and does not redirect its standard input, so what
# is piped here is what the signer reads — one line, which is exactly what it
# asks for. It is never an argument: arguments are readable in the process table
# by every user on the machine, and this one is also written verbatim into the
# ukify command line logged just above.
#
# The passphrase arrives on standard input through a process substitution
# rather than a pipeline, and the reason is not style. Inside a command
# substitution a pipeline's own statuses are not visible to the outer shell:
# PIPESTATUS there belongs to the outer shell's last pipeline, which under
# `set -u` is an unbound variable when there has not been one. Measured on a
# real firing of this hook, which failed at this line with exactly that.
#
# With the substitution the builder is the only command in the assignment, so
# $? is the builder's own status. printf is a shell builtin, so the passphrase
# never becomes a process with arguments anyone could read.
if [ "$SIGN_THIS_UKI" = "yes" ]; then
    UKIFY_OUTPUT=$(ukify "${UKIFY_ARGS[@]}" 2>&1 < <(printf '%s\n' "$MOK_PASSPHRASE"))
    UKIFY_RC=$?
else
    UKIFY_OUTPUT=$(ukify "${UKIFY_ARGS[@]}" 2>&1 </dev/null)
    UKIFY_RC=$?
fi
echo "----- ukify stdout+stderr (rc=$UKIFY_RC) -----" >> "$LOGFILE"
printf '%s\n' "$UKIFY_OUTPUT" >> "$LOGFILE"
echo "----- end ukify output -----" >> "$LOGFILE"
if [ $UKIFY_RC -eq 0 ]; then
    # Prove the signature before the image is allowed to take the boot path's
    # name. The builder exits non-zero when the signer refuses, so this is a
    # second reading rather than the only one — and a second reading is what
    # catches a builder that ever changes its mind about that, which is the
    # class of silence this whole change exists to remove.
    if [ "$SIGN_THIS_UKI" = "yes" ]; then
        if ! sbverify --cert "$MOK_CERT" "$UKI_STAGE" >/dev/null 2>&1; then
            rm -f "$UKI_STAGE"
            refuse_to_sign "the boot image was built but carries no valid signature from this machine's own certificate"
        fi
        log "boot image signature verified against $MOK_CERT"
    fi
    if ! mv -f "$UKI_STAGE" "$UKI"; then
        rm -f "$UKI_STAGE"
        refuse_to_sign "the finished boot image could not be put in place at $UKI"
    fi
    UKI_SIZE=$(stat -c %s "$UKI" 2>/dev/null || echo "?")
    log "UKI built at $UKI ($UKI_SIZE bytes)"
    # Audit the UKI's section table for the operator-required sections.
    # Surfacing this in the log makes it visible immediately whether
    # .initrd and .ucode landed (each conditional on /boot/initramfs.img
    # and /boot/{intel,amd}-ucode.img existing at ukify-invoke time).
    if command -v objdump >/dev/null 2>&1; then
        UKI_SECTIONS=$(objdump -h "$UKI" 2>/dev/null | \
            awk '/\.(linux|initrd|ucode|cmdline|osrel|sbat|sdmagic|dtb|splash|uname)\s/ {print "  " $2}')
        if [ -n "$UKI_SECTIONS" ]; then
            echo "UKI sections present:" >> "$LOGFILE"
            echo "$UKI_SECTIONS" >> "$LOGFILE"
        fi
    fi
else
    rm -f "$UKI_STAGE" 2>/dev/null || true
    if [ "$SIGN_THIS_UKI" = "yes" ]; then
        # A build that was supposed to be signed and failed is a refusal, not a
        # degrade. Exiting zero here is what let a machine report a successful
        # kernel upgrade and then boot the previous kernel with no one told.
        refuse_to_sign "the boot image could not be built or signed (builder exit $UKIFY_RC); its output is in $LOGFILE"
    fi
    log "ukify failed (exit $UKIFY_RC); output above. grub-loads-vmlinuz path remains as recovery per D-005 fallback semantics."
    exit 0  # an UNSIGNED build failing does not break the kernel install
fi

log "D-005 Phase A complete for kernel $NEW_KVER"

# Update the ESP GRUB menu to the just-built kernel — in the SAME transaction
# that minted the UKI. Without this, the install-era menu keeps chainloading
# the OLD release's UKI while the package replacement removes that release's
# module tree: the old UKI still boots (it embeds its kernel) into a system
# that can load no modules, and the boot drops to emergency mode. (Incident:
# 2026-07-24, the first kernel-release transition taken through a fleet
# upgrade round; this was the documented-but-unbuilt Phase B item.)
# Failure here does not fail the kernel install, but it is the one degrade
# path that leaves the NEXT BOOT broken — so the warning says exactly that.
MENU_UPDATER=/usr/lib/intergen/update-boot-menu.sh
if [ -x "$MENU_UPDATER" ]; then
    if "$MENU_UPDATER" "$NEW_KVER" 2>&1 | tee -a "$LOGFILE" >&2; then
        log "boot menu updated to $NEW_KVER"
    else
        # The menu path is RESOLVED BY THE UPDATER and never spelled here.
        # This message used to print a lowercase /boot/efi/EFI/intergenos/
        # grub.cfg — a second copy of the path that had drifted from the
        # updater's, and that exists on no installed system, so the manual fix
        # it offered could not work. The updater already resolves the
        # installer's mixed-case /boot/efi/EFI/InterGenOS/grub.cfg and
        # discovers a single menu elsewhere under the ESP; asking it is the
        # only way this message stays true when that resolution changes.
        MENU_PATH=$("$MENU_UPDATER" --print-menu-path 2>/dev/null)
        [ -n "$MENU_PATH" ] || MENU_PATH="the ESP GRUB menu"
        if first_install_ordering; then
            log "boot menu not updated to $NEW_KVER — the updater's own reason is on the line above. This system has not booted yet, and on an install the boot menu at $MENU_PATH is composed by the installer AFTER the package hooks run, from the installed kernel, so a menu that is not there yet is the expected order and nothing is lost. To check it afterwards: sudo $MENU_UPDATER $NEW_KVER"
        else
            log "WARNING: boot-menu update FAILED — the GRUB default entry may still chainload a PREVIOUS kernel whose modules were just removed; the next boot can drop to emergency mode. Manual fix: point every kernel-release token in $MENU_PATH at $NEW_KVER (sudo $MENU_UPDATER $NEW_KVER)."
        fi
    fi
else
    log "WARNING: $MENU_UPDATER absent — boot menu NOT updated; the GRUB default entry may still chainload a previous kernel (see the 2026-07-24 emergency-mode incident class)."
fi

# Chain to nvidia module rebuild if the nvidia package is installed.
# Per v1.0 NVIDIA mandate (2026-05-28): kernel-upgrade triggers a fresh
# DKMS-style rebuild + MOK-sign of the nvidia*.ko set against the new
# kernel. Without this, a kernel upgrade leaves /lib/modules/<new>/extra/
# nvidia/ unpopulated and `modprobe nvidia` fails on the next boot.
#
# Best-effort: nvidia rebuild failure does NOT roll back the kernel
# install. The kernel install is the load-bearing operation here; nvidia
# can be rebuilt manually via /var/lib/pkm/hooks/nvidia/rebuild-modules.
NVIDIA_REBUILD=/var/lib/pkm/hooks/nvidia/rebuild-modules
if [ -x "$NVIDIA_REBUILD" ]; then
    log "nvidia package detected — chaining to module rebuild for kernel $NEW_KVER"
    if "$NVIDIA_REBUILD" "$NEW_KVER" 2>&1 | tee -a "$LOGFILE" >&2; then
        log "nvidia module rebuild complete for kernel $NEW_KVER"
    else
        log "WARNING: nvidia module rebuild failed for kernel $NEW_KVER"
        log "  Manual rebuild path: sudo $NVIDIA_REBUILD $NEW_KVER"
    fi
fi

# Kernel retention (keep-2). The release-stamped KERNELRELEASE makes each
# release install to distinct /boot/vmlinuz-<kver>, /lib/modules/<kver>, and
# ESP UKI paths — a real previous-kernel fallback, but without bounded
# retention the hook-generated ESP UKIs (which pkm's upgrade file-reaping never
# touches, since they are not package-tracked) accumulate unbounded and can
# overflow the ESP. Run AFTER the new kernel's UKI + nvidia modules are fully in
# place, so the just-installed kernel is complete before any pruning. The helper
# hard-guards the running ($(uname -r)) and just-installed ($NEW_KVER) kernels,
# keeps the 2 newest by (version, release), and is best-effort (never fatal).
KERNEL_PRUNE=/usr/lib/intergen/prune-old-kernels.sh
if [ -x "$KERNEL_PRUNE" ]; then
    log "kernel retention (keep-2): pruning superseded kernels beyond the 2 newest"
    "$KERNEL_PRUNE" "$NEW_KVER" 2>&1 | tee -a "$LOGFILE" >&2 || \
        log "WARNING: kernel retention prune reported an error (non-fatal; install proceeds)"
else
    log "kernel retention helper absent ($KERNEL_PRUNE) — skipping prune (older kernels retained)"
fi

exit 0
