#!/bin/bash
# Tests for scripts/update-boot-menu.sh — ESP GRUB menu repoint on kernel install.
#
# Validates the token rewrite, backup, verification restores, idempotence, and
# the fail-safe refusals WITHOUT a real ESP, by pointing GRUB_CFG at a tmpdir
# fixture. Run: bash tests/kernel-retention/test_update_boot_menu.sh
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UBM="$REPO/scripts/update-boot-menu.sh"

PASS=0; FAIL=0
T=""
setup() { T="$(mktemp -d)"; }
teardown() { [ -n "$T" ] && rm -rf "$T"; }
ck() { if eval "$2"; then PASS=$((PASS+1)); else echo "  FAIL: $1"; FAIL=$((FAIL+1)); fi; }

fixture() { # fixture <kver> — an installer-shaped menu pinned at <kver>
    cat > "$T/grub.cfg" <<EOF
set default="0"
menuentry "InterGenOS $1 (UKI)" --class intergenos {
    chainloader /EFI/Linux/intergenos-$1.efi
}
menuentry 'InterGenOS GNU/Linux' {
	echo	'Loading Linux $1 ...'
	linux	/boot/vmlinuz-$1 root=/dev/mapper/cryptroot ro rootwait
}
submenu 'Advanced options' {
	menuentry 'InterGenOS GNU/Linux, with Linux $1' {
		echo	'Loading Linux $1 ...'
		linux	/boot/vmlinuz-$1 root=/dev/mapper/cryptroot ro
	}
}
EOF
}
run() { GRUB_CFG="$T/grub.cfg" bash "$UBM" "$1" >/dev/null 2>&1; }

echo "== update-boot-menu tests =="

setup; fixture 6.18.10-igos-6
run 6.18.10-igos-7; RC=$?
ck "repoint exits 0" '[ $RC -eq 0 ]'
ck "chainloader now igos-7" 'grep -q "chainloader /EFI/Linux/intergenos-6.18.10-igos-7.efi" "$T/grub.cfg"'
ck "no igos-6 token survives" '! grep -q "igos-6" "$T/grub.cfg"'
ck "every vmlinuz ref repointed" '[ "$(grep -c "vmlinuz-6.18.10-igos-7" "$T/grub.cfg")" -eq 2 ]'
ck "backup written with prior content" 'grep -q "igos-6" "$T/grub.cfg.bak-6.18.10-igos-7"'
run 6.18.10-igos-7; RC=$?
ck "idempotent rerun exits 0" '[ $RC -eq 0 ]'
ck "idempotent rerun leaves menu at igos-7" '! grep -q "igos-6" "$T/grub.cfg"'
teardown

setup; fixture 6.18.10-igos-6
run bogus-release; RC=$?
ck "non-igos release refused" '[ $RC -ne 0 ]'
ck "refusal leaves menu untouched" 'grep -q "igos-6" "$T/grub.cfg"'
teardown

setup
GRUB_CFG="$T/absent/grub.cfg" bash "$UBM" 6.18.10-igos-7 >/dev/null 2>&1; RC=$?
ck "missing menu exits non-zero" '[ $RC -ne 0 ]'
teardown

setup
echo "menuentry 'Some Other OS' { chainloader /EFI/other/other.efi }" > "$T/grub.cfg"
run 6.18.10-igos-7; RC=$?
ck "unrecognized menu (no -igos tokens) refused" '[ $RC -ne 0 ]'
ck "unrecognized menu untouched" 'grep -q "Some Other OS" "$T/grub.cfg"'
teardown

setup; fixture 6.18.9-igos-12
run 6.18.10-igos-7; RC=$?
ck "cross-version transition exits 0" '[ $RC -eq 0 ]'
ck "old cross-version token gone" '! grep -q "6.18.9-igos-12" "$T/grub.cfg"'
ck "cross-version chainloader correct" 'grep -q "intergenos-6.18.10-igos-7.efi" "$T/grub.cfg"'
teardown

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
