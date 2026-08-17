#!/bin/bash
# Tests for scripts/prune-old-kernels.sh — keep-N kernel retention.
#
# Validates the selection logic + the hard guards WITHOUT a real kernel build,
# by pointing BOOT_DIR / ESP_UKI_DIR / MODULES_DIR at a synthetic tmpdir and
# RUNNING_KVER at a chosen value. Run: bash tests/kernel-retention/test_prune_old_kernels.sh
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PRUNE="$REPO/scripts/prune-old-kernels.sh"

PASS=0; FAIL=0
T=""
setup() { T="$(mktemp -d)"; }
teardown() { [ -n "$T" ] && rm -rf "$T"; }
mk() { # mk <kver> — create all three on-disk surfaces for a BOOTABLE kernel.
    # The module tree carries modules.dep + one module: since the 2026-07-24
    # fallback-quarantine policy, a UKI whose tree is absent/empty is
    # neutralized on every run — a fixture without a usable tree models the
    # incident trap, not a kernel (test_fallback_quarantine.sh owns those).
    mkdir -p "$T/boot/efi/EFI/Linux" "$T/lib/modules/$1/kernel"
    : > "$T/boot/vmlinuz-$1"
    : > "$T/boot/efi/EFI/Linux/intergenos-$1.efi"
    : > "$T/boot/initramfs-$1.img"
    : > "$T/lib/modules/$1/modules.dep"
    : > "$T/lib/modules/$1/kernel/dummy.ko.gz"
}
run() { # run <running_kver> <just_installed> [keep_count]
    BOOT_DIR="$T/boot" ESP_UKI_DIR="$T/boot/efi/EFI/Linux" \
    MODULES_DIR="$T/lib/modules" KEEP_COUNT="${3:-2}" RUNNING_KVER="$1" \
        bash "$PRUNE" "$2" >/dev/null 2>&1
}
present() { [ -e "$T/boot/vmlinuz-$1" ] && [ -d "$T/lib/modules/$1" ] && [ -e "$T/boot/efi/EFI/Linux/intergenos-$1.efi" ]; }
gone()    { [ ! -e "$T/boot/vmlinuz-$1" ] && [ ! -d "$T/lib/modules/$1" ] && [ ! -e "$T/boot/efi/EFI/Linux/intergenos-$1.efi" ]; }
ck() { if eval "$2"; then PASS=$((PASS+1)); else echo "  FAIL: $1"; FAIL=$((FAIL+1)); fi; }

# Test 1 — keep-2, running below the newest: keep {newest-2} ∪ {running} ∪ {just}
setup
for k in 6.18.10-igos-1 6.18.10-igos-2 6.18.10-igos-3 6.18.10-igos-4; do mk "$k"; done
run 6.18.10-igos-2 6.18.10-igos-4
ck "T1 oldest (-1) pruned"        'gone 6.18.10-igos-1'
ck "T1 running (-2) kept"         'present 6.18.10-igos-2'
ck "T1 newest-2 (-3) kept"        'present 6.18.10-igos-3'
ck "T1 just-installed (-4) kept"  'present 6.18.10-igos-4'
teardown

# Test 2 — running kernel OLDER than the two newest is kept via the union guard
setup
for k in 6.18.10-igos-1 6.18.10-igos-5 6.18.10-igos-6; do mk "$k"; done
run 6.18.10-igos-1 6.18.10-igos-6
ck "T2 running-but-oldest (-1) kept" 'present 6.18.10-igos-1'
ck "T2 newest (-5) kept"             'present 6.18.10-igos-5'
ck "T2 newest (-6) kept"             'present 6.18.10-igos-6'
teardown

# Test 3 — idempotent: a second run changes nothing
setup
for k in 6.18.10-igos-1 6.18.10-igos-2 6.18.10-igos-3; do mk "$k"; done
run 6.18.10-igos-3 6.18.10-igos-3
run 6.18.10-igos-3 6.18.10-igos-3
ck "T3 -1 pruned once"  'gone 6.18.10-igos-1'
ck "T3 -2 kept"         'present 6.18.10-igos-2'
ck "T3 -3 kept"         'present 6.18.10-igos-3'
teardown

# Test 4 — fail-safe: an explicitly-empty RUNNING_KVER keeps EVERYTHING
setup
for k in 6.18.10-igos-1 6.18.10-igos-2 6.18.10-igos-3; do mk "$k"; done
run "" 6.18.10-igos-3
ck "T4 fail-safe keeps -1" 'present 6.18.10-igos-1'
ck "T4 fail-safe keeps -2" 'present 6.18.10-igos-2'
ck "T4 fail-safe keeps -3" 'present 6.18.10-igos-3'
teardown

# Test 5 — cross-version ordering (sort -V): 6.18.10 < 6.19.1
setup
for k in 6.18.10-igos-9 6.19.1-igos-1 6.19.1-igos-2; do mk "$k"; done
run 6.19.1-igos-2 6.19.1-igos-2
ck "T5 older version (6.18.10-igos-9) pruned" 'gone 6.18.10-igos-9'
ck "T5 6.19.1-igos-1 kept" 'present 6.19.1-igos-1'
ck "T5 6.19.1-igos-2 kept" 'present 6.19.1-igos-2'
teardown

# Test 6 — keep-1 still protects the running kernel (never prune what's running)
setup
for k in 6.18.10-igos-1 6.18.10-igos-2; do mk "$k"; done
run 6.18.10-igos-1 6.18.10-igos-2 1
ck "T6 keep-1: newest (-2) kept" 'present 6.18.10-igos-2'
ck "T6 keep-1: running (-1) kept despite keep-1" 'present 6.18.10-igos-1'
teardown

# Test 7 — an orphan UKI (no matching vmlinuz/modules) that falls OUTSIDE
# keep-2 is reaped. pkm's upgrade reaps the old kernel's tracked vmlinuz +
# modules, but the ESP UKI is hook-generated (untracked), so it orphans — this
# is the exact accumulation this prune exists to bound.
setup
mk 6.18.10-igos-2
mk 6.18.10-igos-3
: > "$T/boot/efi/EFI/Linux/intergenos-6.18.10-igos-1.efi"   # orphan UKI only
run 6.18.10-igos-3 6.18.10-igos-3
ck "T7 orphan UKI outside keep-2 pruned" '[ ! -e "$T/boot/efi/EFI/Linux/intergenos-6.18.10-igos-1.efi" ]'
ck "T7 newest-2 (-2) kept" 'present 6.18.10-igos-2'
ck "T7 current (-3) kept"  'present 6.18.10-igos-3'
teardown

echo "kernel-retention: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
