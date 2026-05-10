#!/usr/bin/env bash
# installer/smoke/checks/boot.sh — Category 3: boot integrity.
#
# Confirms the system booted cleanly with the expected signing chain and
# that no kernel-level integrity errors are present. Each function emits
# exactly one check_* result.

check_boot_dmesg_clean() {
    if ! command -v dmesg >/dev/null 2>&1; then
        check_skip "boot/dmesg" "dmesg not in PATH"
        return
    fi

    # Pattern-match on real failures, not on the casual mention of any
    # word. Anchored to the dmesg timestamp prefix to avoid false hits
    # from quoted text in normal driver chatter.
    local hits
    hits="$(dmesg 2>/dev/null | grep -cE '^\[[ 0-9.]+\] (BUG:|kernel panic|integrity:.*invalid|integrity:.*failed|denied: integrity)' || true)"

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
        *"EFI variables are not supported"*) check_skip "boot/sb-state" "BIOS boot (no EFI) — signing chain not applicable" ;;
        *) check_warn "boot/sb-state" "unrecognized state: $(echo "$state" | head -1)" ;;
    esac
}

check_boot_efi_artifacts() {
    local efi_dir="/boot/efi/EFI"

    if [ ! -d "$efi_dir" ]; then
        check_skip "boot/efi-artifacts" "$efi_dir not present (BIOS boot)"
        return
    fi

    local found_shim=0 found_grub=0
    if find "$efi_dir" -maxdepth 4 -type f -iname 'shim*.efi' 2>/dev/null | grep -q .; then
        found_shim=1
    fi
    if find "$efi_dir" -maxdepth 4 -type f -iname 'grub*.efi' 2>/dev/null | grep -q .; then
        found_grub=1
    fi

    if [ $found_shim -eq 1 ] && [ $found_grub -eq 1 ]; then
        check_pass "boot/efi-artifacts" "shim + grub present in $efi_dir"
    elif [ $found_shim -eq 0 ] && [ $found_grub -eq 0 ]; then
        check_fail "boot/efi-artifacts" "neither shim nor grub found under $efi_dir"
    else
        check_warn "boot/efi-artifacts" "partial: shim=$found_shim grub=$found_grub"
    fi
}

check_boot_kernel_present() {
    # Kernel binary present in /boot. Doesn't validate signing here (covered
    # via mokutil + the install-time integrity verification); just confirms
    # the boot artifacts that grub points at are still on disk.
    local count
    count="$(find /boot -maxdepth 1 -type f \( -name 'vmlinuz*' -o -name 'kernel*' -o -name 'initramfs*' -o -name 'initrd*' \) 2>/dev/null | wc -l)"

    if [ "$count" -eq 0 ]; then
        check_fail "boot/kernel" "no vmlinuz/kernel/initramfs found in /boot"
        return
    fi
    check_pass "boot/kernel" "$count boot artifacts in /boot"
}

run_boot_checks() {
    check_boot_dmesg_clean
    check_boot_secureboot_state
    check_boot_efi_artifacts
    check_boot_kernel_present
}
