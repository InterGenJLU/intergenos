#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# installer/smoke/checks/boot.sh — Category 3: boot integrity.
#
# Confirms the system booted cleanly with the expected signing chain and
# that no kernel-level integrity errors are present. Each function emits
# exactly one check_* result.

SMOKE_DMESG="${SMOKE_DMESG:-dmesg}"
SMOKE_EFI_FIRMWARE="${SMOKE_EFI_FIRMWARE:-/sys/firmware/efi}"
SMOKE_ESP_ROOT="${SMOKE_ESP_ROOT:-/boot/efi}"
SMOKE_BOOT_EFI_DIR="${SMOKE_BOOT_EFI_DIR:-/boot/efi/EFI}"
SMOKE_BOOT_DIR="${SMOKE_BOOT_DIR:-/boot}"

check_boot_dmesg_clean() {
    if ! command -v "$SMOKE_DMESG" >/dev/null 2>&1; then
        check_skip "boot/dmesg" "dmesg not in PATH"
        return
    fi

    # Pattern-match on real failures, not on the casual mention of any
    # word. Anchored to the dmesg timestamp prefix to avoid false hits
    # from quoted text in normal driver chatter.
    local output rc=0 hits
    output="$("$SMOKE_DMESG" 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ]; then
        check_warn "boot/dmesg" "could not read dmesg (exit $rc) — $(smoke_root_rerun)"
        return
    fi
    hits="$(grep -cE '^\[[ 0-9.]+\] (BUG:|kernel panic|integrity:.*invalid|integrity:.*failed|denied: integrity)' <<<"$output" || true)"

    if [ "$hits" -gt 0 ]; then
        check_fail "boot/dmesg" "$hits integrity-class entries in dmesg (run 'dmesg | grep -E ...' to inspect)"
        return
    fi
    check_pass "boot/dmesg" "no integrity-class entries"
}

check_boot_secureboot_state() {
    if ! command -v mokutil >/dev/null 2>&1; then
        check_skip "boot/sb-state" "mokutil not in PATH"
        return
    fi

    local state
    state="$(mokutil --sb-state 2>&1 || true)"
    case "$state" in
        *"SecureBoot enabled"*) check_pass "boot/sb-state" "SecureBoot enabled" ;;
        *"SecureBoot disabled"*) check_warn "boot/sb-state" "SecureBoot disabled — signing chain not enforced" ;;
        *"EFI variables are not supported"*)
            if [ -d "$SMOKE_EFI_FIRMWARE" ]; then
                check_warn "boot/sb-state" "UEFI boot detected but EFI variables are unavailable — Secure Boot state is unknown"
            else
                check_skip "boot/sb-state" "BIOS boot (no EFI) — signing chain not applicable"
            fi
            ;;
        *) check_warn "boot/sb-state" "unrecognized state: $(echo "$state" | head -1)" ;;
    esac
}

check_boot_efi_artifacts() {
    local efi_dir="$SMOKE_BOOT_EFI_DIR"

    # This check asserts the INSTALLED system's ESP layout. A live-media
    # boot (igos.mode= on the cmdline: live | install-gui | install-tui)
    # booted shim/grub from the ISO's own ESP, which is never mounted at
    # /boot/efi — asserting the installed layout there is a false FAIL.
    if smoke_live_media; then
        check_skip "boot/efi-artifacts" "live-media boot (igos.mode= present) — installed-system ESP layout not applicable"
        return
    fi

    if [ ! -d "$SMOKE_EFI_FIRMWARE" ]; then
        check_skip "boot/efi-artifacts" "not booted via EFI (BIOS install — installed-system ESP layout not applicable)"
        return
    fi

    local state
    state="$(smoke_path_state "$SMOKE_ESP_ROOT")"
    if [ "$state" = "unreadable" ]; then
        check_warn "boot/efi-artifacts" "$SMOKE_ESP_ROOT is unreadable — $(smoke_root_rerun)"
        return
    fi
    if [ "$state" = "absent" ]; then
        check_fail "boot/efi-artifacts" "UEFI boot detected but ESP mount $SMOKE_ESP_ROOT is absent"
        return
    fi
    state="$(smoke_path_state "$efi_dir")"
    if [ "$state" = "unreadable" ]; then
        check_warn "boot/efi-artifacts" "$efi_dir is unreadable — $(smoke_root_rerun)"
        return
    fi
    if [ "$state" = "absent" ]; then
        check_fail "boot/efi-artifacts" "UEFI boot detected but $efi_dir is absent"
        return
    fi

    local files rc=0 found_shim=0 found_grub=0
    files="$(find "$efi_dir" -maxdepth 4 -type f -iname '*.efi' 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ]; then
        check_warn "boot/efi-artifacts" "could not scan $efi_dir (exit $rc) — $(smoke_root_rerun)"
        return
    fi
    grep -qiE '(^|/)shim[^/]*\.efi$' <<<"$files" && found_shim=1
    grep -qiE '(^|/)grub[^/]*\.efi$' <<<"$files" && found_grub=1

    if [ $found_shim -eq 1 ] && [ $found_grub -eq 1 ]; then
        check_pass "boot/efi-artifacts" "shim + grub present in $efi_dir"
    elif [ $found_shim -eq 0 ] && [ $found_grub -eq 0 ]; then
        check_fail "boot/efi-artifacts" "neither shim nor grub found under readable $efi_dir"
    else
        check_fail "boot/efi-artifacts" "incomplete boot chain under $efi_dir: shim=$found_shim grub=$found_grub"
    fi
}

check_boot_kernel_present() {
    # Kernel binary present in /boot. Doesn't validate signing here (covered
    # via mokutil + the install-time integrity verification); just confirms
    # the boot artifacts that grub points at are still on disk.
    local state files rc=0 kernel_count full_initramfs_count stub_count
    state="$(smoke_path_state "$SMOKE_BOOT_DIR")"
    if [ "$state" = "unreadable" ]; then
        check_warn "boot/kernel" "$SMOKE_BOOT_DIR is unreadable — $(smoke_root_rerun)"
        return
    fi
    if [ "$state" = "absent" ]; then
        check_fail "boot/kernel" "$SMOKE_BOOT_DIR is absent"
        return
    fi
    files="$(find "$SMOKE_BOOT_DIR" -maxdepth 1 -type f -printf '%f\t%s\n' 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ]; then
        check_warn "boot/kernel" "could not scan $SMOKE_BOOT_DIR (exit $rc) — $(smoke_root_rerun)"
        return
    fi
    kernel_count="$(awk -F '\t' '$1 ~ /^(vmlinuz|kernel)/ && $2 > 0 {n++} END {print n+0}' <<<"$files")"
    full_initramfs_count="$(awk -F '\t' '$1 ~ /^(initramfs|initrd)/ && $2 >= 512 {n++} END {print n+0}' <<<"$files")"
    stub_count="$(awk -F '\t' '$1 ~ /^(initramfs|initrd)/ && $2 < 512 {n++} END {print n+0}' <<<"$files")"

    if [ "$kernel_count" -eq 0 ]; then
        check_fail "boot/kernel" "no non-empty vmlinuz/kernel image in $SMOKE_BOOT_DIR ($stub_count tiny initramfs stub(s) do not prove a kernel)"
        return
    fi
    check_pass "boot/kernel" "$kernel_count kernel image(s), $full_initramfs_count full initramfs image(s); $stub_count tiny initramfs stub(s) excluded by design"
}

run_boot_checks() {
    check_boot_dmesg_clean
    check_boot_secureboot_state
    check_boot_efi_artifacts
    check_boot_kernel_present
}
