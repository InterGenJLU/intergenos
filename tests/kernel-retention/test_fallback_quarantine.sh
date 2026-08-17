#!/bin/bash
# Tests for the unbootable-fallback quarantine in scripts/prune-old-kernels.sh.
#
# The 2026-07-24 incident class: keep-N retains an old release's UKI while the
# package replacement removed its module tree — the retained "fallback" boots
# into emergency mode. The helper must quarantine (.disabled) any retained UKI
# whose module tree is absent or holds zero modules, on EVERY invocation
# (including prune-set-empty runs), and never touch the just-installed kernel
# or a fallback with a usable tree.
# Run: bash tests/kernel-retention/test_fallback_quarantine.sh
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PRUNE="$REPO/scripts/prune-old-kernels.sh"

PASS=0; FAIL=0
T=""
setup() { T="$(mktemp -d)"; }
teardown() { [ -n "$T" ] && rm -rf "$T"; }
ck() { if eval "$2"; then PASS=$((PASS+1)); else echo "  FAIL: $1"; FAIL=$((FAIL+1)); fi; }

mk_full() { # mk_full <kver> — all surfaces + a usable module tree
    mkdir -p "$T/boot/efi/EFI/Linux" "$T/lib/modules/$1/kernel"
    : > "$T/boot/vmlinuz-$1"
    : > "$T/boot/efi/EFI/Linux/intergenos-$1.efi"
    : > "$T/lib/modules/$1/modules.dep"
    : > "$T/lib/modules/$1/kernel/dummy.ko.gz"
}
mk_gutted() { # mk_gutted <kver> — UKI present, module tree dir present but EMPTY
    mkdir -p "$T/boot/efi/EFI/Linux" "$T/lib/modules/$1"
    : > "$T/boot/efi/EFI/Linux/intergenos-$1.efi"
}
run() { # run <running_kver> <just_installed>
    BOOT_DIR="$T/boot" ESP_UKI_DIR="$T/boot/efi/EFI/Linux" \
    MODULES_DIR="$T/lib/modules" KEEP_COUNT=2 RUNNING_KVER="$1" \
        bash "$PRUNE" "$2" >/dev/null 2>&1
}
live()        { [ -e "$T/boot/efi/EFI/Linux/intergenos-$1.efi" ]; }
quarantined() { [ -e "$T/boot/efi/EFI/Linux/intergenos-$1.efi.disabled" ] && [ ! -e "$T/boot/efi/EFI/Linux/intergenos-$1.efi" ]; }

echo "== fallback-quarantine tests =="

# The incident state exactly: 2 kernels, prune-set empty, old tree gutted.
setup; mk_gutted 6.18.10-igos-6; mk_full 6.18.10-igos-7
run 6.18.10-igos-6 6.18.10-igos-7
ck "gutted retained fallback quarantined (prune-set empty)" 'quarantined 6.18.10-igos-6'
ck "just-installed UKI stays live" 'live 6.18.10-igos-7'
teardown

# A REAL fallback (usable tree) must stay live.
setup; mk_full 6.18.10-igos-6; mk_full 6.18.10-igos-7
run 6.18.10-igos-7 6.18.10-igos-7
ck "usable fallback stays live" 'live 6.18.10-igos-6'
ck "current stays live" 'live 6.18.10-igos-7'
teardown

# modules.dep present but zero .ko files = still unusable -> quarantine.
setup; mk_gutted 6.18.10-igos-6; : > "$T/lib/modules/6.18.10-igos-6/modules.dep"; mk_full 6.18.10-igos-7
run 6.18.10-igos-7 6.18.10-igos-7
ck "dep-file-only tree still quarantined" 'quarantined 6.18.10-igos-6'
teardown

# The just-installed kernel is never quarantined, even mid-transaction when
# its tree check races (guard is on JUST_INSTALLED, not tree state).
setup; mk_gutted 6.18.10-igos-7
run 6.18.10-igos-6 6.18.10-igos-7
ck "just-installed never quarantined" 'live 6.18.10-igos-7'
teardown

# Idempotence: second run with the .disabled file already present is a no-op.
setup; mk_gutted 6.18.10-igos-6; mk_full 6.18.10-igos-7
run 6.18.10-igos-6 6.18.10-igos-7
run 6.18.10-igos-6 6.18.10-igos-7
ck "idempotent rerun keeps single .disabled" 'quarantined 6.18.10-igos-6'
teardown

# Prune still removes .disabled twins when a kernel leaves the keep-set.
setup; mk_full 6.18.10-igos-5; mk_full 6.18.10-igos-6; mk_full 6.18.10-igos-7
rm -rf "$T/lib/modules/6.18.10-igos-5"; mkdir -p "$T/lib/modules/6.18.10-igos-5"
run 6.18.10-igos-6 6.18.10-igos-7   # first run quarantines igos-5 (gutted)
run 6.18.10-igos-7 6.18.10-igos-7   # igos-5 now outside keep-2 -> pruned incl. .disabled
ck "pruned kernel's .disabled twin removed" '[ ! -e "$T/boot/efi/EFI/Linux/intergenos-6.18.10-igos-5.efi.disabled" ]'
teardown

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
