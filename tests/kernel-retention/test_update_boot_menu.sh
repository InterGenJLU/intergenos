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

# A menu composed before its boot image existed: this system's own composition
# (the unified-kernel generator's section markers, the pinned default, the
# installer's theme block) with an EMPTY unified-kernel section and no kernel
# anywhere — the shape of the menu a real R001.3 install wrote on 2026-10-16.
empty_section_menu() {
    cat > "$T/grub.cfg" <<'EOF'
### BEGIN /etc/grub.d/00_header ###
   set default="intergenos-uki"
terminal_output gfxterm

### InterGenOS theme (Forge-injected) — 00_header's [ -f GRUB_THEME ]
insmod gfxmenu
if [ -e /EFI/InterGenOS/themes/intergenos/theme.txt ]; then
    set theme=/EFI/InterGenOS/themes/intergenos/theme.txt
fi
### END /etc/grub.d/00_header ###

### BEGIN /etc/grub.d/06_uki ###
### END /etc/grub.d/06_uki ###

### BEGIN /etc/grub.d/30_uefi-firmware ###
		menuentry 'UEFI Firmware Settings' $menuentry_id_option 'uefi-firmware' {
			fwsetup
		}
### END /etc/grub.d/30_uefi-firmware ###
EOF
}
# A stand-in for /etc/grub.d/06_uki: the entries it emits for the images in the
# fixture ESP, newest first, the newest with the pinned id (its real output's
# shape; the real generator is exercised against a real ESP in the lane's proof).
generator() {
    mkdir -p "$T/esp-linux"
    cat > "$T/06_uki" <<EOF
#!/bin/bash
first=1
for img in \$(ls -t "$T/esp-linux"/intergenos-*.efi 2>/dev/null); do
    n=\${img##*/}; kv=\${n#intergenos-}; kv=\${kv%.efi}
    if [ \$first -eq 1 ]; then id=intergenos-uki; first=0; else id=intergenos-uki-\$kv; fi
    echo "menuentry \"InterGenOS \$kv (UKI)\" --id \$id --class intergenos {"
    echo "    insmod chain"
    echo "    search --no-floppy --fs-uuid --set=root 1234-ABCD"
    echo "    chainloader /EFI/Linux/\$n"
    echo "}"
done
EOF
    chmod +x "$T/06_uki"
}
run_compose() { GRUB_CFG="$T/grub.cfg" UKI_GENERATOR="$T/06_uki" ESP_UKI_DIR="$T/esp-linux" bash "$UBM" "$1" >/dev/null 2>&1; }
outside() { awk '$0=="### BEGIN /etc/grub.d/06_uki ###"{s=1;next} $0=="### END /etc/grub.d/06_uki ###"{s=0;next} !s' "$1"; }

setup; empty_section_menu; generator; : > "$T/esp-linux/intergenos-6.18.51-igos-6.efi"
cp "$T/grub.cfg" "$T/before.cfg"
run_compose 6.18.51-igos-6; RC=$?
ck "a menu with an empty unified-kernel section is composed (exit 0)" '[ $RC -eq 0 ]'
ck "the composed default entry is the installed release" 'grep -qF "menuentry \"InterGenOS 6.18.51-igos-6 (UKI)\" --id intergenos-uki " "$T/grub.cfg"'
ck "it chainloads that release's image" 'grep -qxF "    chainloader /EFI/Linux/intergenos-6.18.51-igos-6.efi" "$T/grub.cfg"'
ck "nothing outside the section changed (the theme block kept)" '[ "$(outside "$T/before.cfg")" = "$(outside "$T/grub.cfg")" ]'
ck "the theme block is still there" 'grep -q "Forge-injected" "$T/grub.cfg"'
ck "backup written with the empty section" 'cmp -s "$T/before.cfg" "$T/grub.cfg.bak-6.18.51-igos-6"'
run_compose 6.18.51-igos-6; RC=$?
ck "a composed menu is then an ordinary menu: rerun exits 0" '[ $RC -eq 0 ]'
ck "and still holds exactly one pinned default" '[ "$(grep -c -- "--id intergenos-uki " "$T/grub.cfg")" -eq 1 ]'
teardown

setup; empty_section_menu; generator
cp "$T/grub.cfg" "$T/before.cfg"
run_compose 6.18.51-igos-6; RC=$?
ck "no image to give the menu: refused" '[ $RC -ne 0 ]'
ck "no image: menu untouched" 'cmp -s "$T/before.cfg" "$T/grub.cfg"'
teardown

setup; empty_section_menu; generator; : > "$T/esp-linux/intergenos-6.18.51-igos-6.efi"
printf '#!/bin/bash\nexit 0\n' > "$T/06_uki"
cp "$T/grub.cfg" "$T/before.cfg"
run_compose 6.18.51-igos-6; RC=$?
ck "a generator that emits nothing: refused" '[ $RC -ne 0 ]'
ck "a generator that emits nothing: menu untouched" 'cmp -s "$T/before.cfg" "$T/grub.cfg"'
teardown

setup; empty_section_menu; generator; : > "$T/esp-linux/intergenos-6.18.51-igos-6.efi"
rm -f "$T/06_uki"
cp "$T/grub.cfg" "$T/before.cfg"
run_compose 6.18.51-igos-6; RC=$?
ck "no generator on the system: refused" '[ $RC -ne 0 ]'
ck "no generator: menu untouched" 'cmp -s "$T/before.cfg" "$T/grub.cfg"'
teardown

# A PLAIN install's menu composed before its image existed: the stock Linux
# entries are kept (they carry kernel-release tokens) beside an EMPTY
# unified-kernel section. Its tokens used to send the script down the rewrite
# path: "nothing to do" with no unified-kernel entry at the same release, and a
# refusal (no intergenos-<new>.efi after the rewrite) on a release change.
plain_menu() { # plain_menu <kver of the stock Linux entries>
    cat > "$T/grub.cfg" <<EOF
### BEGIN /etc/grub.d/00_header ###
   set default="intergenos-uki"
terminal_output gfxterm
### END /etc/grub.d/00_header ###

### BEGIN /etc/grub.d/06_uki ###
### END /etc/grub.d/06_uki ###

### BEGIN /etc/grub.d/10_linux ###
menuentry 'InterGenOS GNU/Linux' --class intergenos --class gnu-linux --class os \$menuentry_id_option 'gnulinux-simple-0b1c' {
	load_video
	echo	'Loading Linux $1 ...'
	linux	/boot/vmlinuz-$1 root=PARTUUID=0b1c-02 ro rootwait vt.default_utf8=1
	echo	'Loading initial ramdisk ...'
	initrd	/boot/initramfs.img
}
submenu 'Advanced options for InterGenOS GNU/Linux' \$menuentry_id_option 'gnulinux-advanced-0b1c' {
	menuentry 'InterGenOS GNU/Linux, with Linux $1' --class intergenos \$menuentry_id_option 'gnulinux-$1-advanced-0b1c' {
		linux	/boot/vmlinuz-$1 root=PARTUUID=0b1c-02 ro rootwait vt.default_utf8=1
	}
}
### END /etc/grub.d/10_linux ###

### BEGIN /etc/grub.d/30_uefi-firmware ###
menuentry 'UEFI Firmware Settings' \$menuentry_id_option 'uefi-firmware' {
	fwsetup
}
### END /etc/grub.d/30_uefi-firmware ###
EOF
}
section() { awk '$0=="### BEGIN /etc/grub.d/06_uki ###"{s=1;next} $0=="### END /etc/grub.d/06_uki ###"{s=0;next} s' "$1"; }

setup; plain_menu 6.18.51-igos-6; generator; : > "$T/esp-linux/intergenos-6.18.51-igos-6.efi"
cp "$T/grub.cfg" "$T/before.cfg"
run_compose 6.18.51-igos-6; RC=$?
ck "plain install, same release: composed (exit 0)" '[ $RC -eq 0 ]'
ck "plain install, same release: the default entry is there" 'grep -qF "menuentry \"InterGenOS 6.18.51-igos-6 (UKI)\" --id intergenos-uki " "$T/grub.cfg"'
ck "plain install, same release: it chainloads the image" 'grep -qxF "    chainloader /EFI/Linux/intergenos-6.18.51-igos-6.efi" "$T/grub.cfg"'
ck "plain install, same release: nothing outside the section changed" '[ "$(outside "$T/before.cfg")" = "$(outside "$T/grub.cfg")" ]'
ck "plain install, same release: backup is the menu as it was" 'cmp -s "$T/before.cfg" "$T/grub.cfg.bak-6.18.51-igos-6"'
run_compose 6.18.51-igos-6; RC=$?
ck "plain install, same release: rerun exits 0 with one pinned default" '[ $RC -eq 0 ] && [ "$(grep -c -- "--id intergenos-uki " "$T/grub.cfg")" -eq 1 ]'
teardown

setup; plain_menu 6.18.51-igos-5; generator
: > "$T/esp-linux/intergenos-6.18.51-igos-5.efi"; sleep 1; : > "$T/esp-linux/intergenos-6.18.51-igos-6.efi"
cp "$T/grub.cfg" "$T/before.cfg"
run_compose 6.18.51-igos-6; RC=$?
ck "plain install, release change: composed (exit 0)" '[ $RC -eq 0 ]'
ck "plain install, release change: the new release is the default" 'grep -qF "menuentry \"InterGenOS 6.18.51-igos-6 (UKI)\" --id intergenos-uki " "$T/grub.cfg"'
ck "plain install, release change: the section is the generator's, older image kept by its own id" '[ "$(section "$T/grub.cfg")" = "$("$T/06_uki")" ] && grep -qF -- "--id intergenos-uki-6.18.51-igos-5 " "$T/grub.cfg"'
ck "plain install, release change: the stock entries now name the new release" '[ "$(grep -c "vmlinuz-6.18.51-igos-6 " "$T/grub.cfg")" -eq 2 ] && ! outside "$T/grub.cfg" | grep -q "igos-5"'
ck "plain install, release change: backup is the menu as it was" 'cmp -s "$T/before.cfg" "$T/grub.cfg.bak-6.18.51-igos-6"'
teardown

setup; plain_menu 6.18.51-igos-6
printf '### BEGIN /etc/grub.d/06_uki ###\n' >> "$T/grub.cfg"
generator; : > "$T/esp-linux/intergenos-6.18.51-igos-6.efi"
cp "$T/grub.cfg" "$T/before.cfg"
run_compose 6.18.51-igos-6; RC=$?
ck "a repeated section marker is never composed into" '! grep -q -- "--id intergenos-uki" "$T/grub.cfg" && cmp -s "$T/before.cfg" "$T/grub.cfg"'
teardown

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
