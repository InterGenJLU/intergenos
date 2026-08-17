#!/bin/bash
# Tests for scripts/preflight-single-kernel.sh — staged-kernel exclusivity
# PLUS artifact realness (review finding H6): the gate must validate real
# artifacts, not names — a dangling symlink, an empty vmlinuz, or a module
# tree depmod never touched used to satisfy every glob/count check. Also
# pins the --allow-none reorder: a root with /boot kernels but no /usr is
# inconsistent, never "pre-chroot".
# Run: bash tests/kernel-retention/test_single_kernel_gate.sh
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATE="$REPO/scripts/preflight-single-kernel.sh"

PASS=0; FAIL=0
T=""
setup() { T="$(mktemp -d)"; }
teardown() { [ -n "$T" ] && rm -rf "$T"; }
mk_good() { # mk_good <kver> — a REAL staged kernel
    mkdir -p "$T/usr/lib/modules/$1" "$T/boot"
    echo "kernel-bytes" > "$T/boot/vmlinuz-$1"
    echo "mod.ko:" > "$T/usr/lib/modules/$1/modules.dep"
}
run_gate() { bash "$GATE" --root "$T" "$@" >/dev/null 2>&1; }
ck() { if eval "$2"; then PASS=$((PASS+1)); else echo "  FAIL: $1"; FAIL=$((FAIL+1)); fi; }

# 1. One real kernel → PASS
setup; mk_good 6.1-igos-1
ck "single real kernel passes" "run_gate"
teardown

# 2. Two module trees → FAIL
setup; mk_good 6.1-igos-1; mkdir -p "$T/usr/lib/modules/6.1-igos-2"
ck "twin module tree fails" "! run_gate"
teardown

# 3. Dangling vmlinuz symlink → FAIL (glob counted it; realness rejects)
setup; mkdir -p "$T/usr/lib/modules/6.1-igos-1" "$T/boot"
echo "mod.ko:" > "$T/usr/lib/modules/6.1-igos-1/modules.dep"
ln -s /nonexistent-target "$T/boot/vmlinuz-6.1-igos-1"
ck "dangling vmlinuz symlink fails" "! run_gate"
teardown

# 4. Empty vmlinuz → FAIL
setup; mk_good 6.1-igos-1; : > "$T/boot/vmlinuz-6.1-igos-1"
ck "empty vmlinuz fails" "! run_gate"
teardown

# 5. Missing modules.dep → FAIL (depmod never ran)
setup; mk_good 6.1-igos-1; rm "$T/usr/lib/modules/6.1-igos-1/modules.dep"
ck "missing modules.dep fails" "! run_gate"
teardown

# 6. Module tree is a symlink → FAIL
setup; mkdir -p "$T/usr/lib/modules" "$T/boot" "$T/elsewhere/6.1-igos-1"
echo "mod.ko:" > "$T/elsewhere/6.1-igos-1/modules.dep"
ln -s "$T/elsewhere/6.1-igos-1" "$T/usr/lib/modules/6.1-igos-1"
echo "kernel-bytes" > "$T/boot/vmlinuz-6.1-igos-1"
ck "symlinked module tree fails" "! run_gate"
teardown

# 7. No /usr but /boot carries a kernel → FAIL even with --allow-none
setup; mkdir -p "$T/boot"; echo "kernel-bytes" > "$T/boot/vmlinuz-6.1-igos-1"
ck "usr-less root with staged kernel fails --allow-none" "! run_gate --allow-none"
teardown

# 8. Truly empty root + --allow-none → PASS (pre-chroot)
setup
ck "empty root passes --allow-none" "run_gate --allow-none"
teardown

# 9. --expect mismatch → FAIL
setup; mk_good 6.1-igos-1
ck "--expect mismatch fails" "! run_gate --expect 6.1-igos-2"
teardown

echo
echo "single-kernel gate tests: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
