# 05 — Creating the bootable ISO

**Audience:** maintainers assembling the final hybrid UEFI+BIOS ISO from signed components.

## Goal

Produce `intergenos-<version>.iso` — a single hybrid-bootable ISO image with:

- **GPT + ESP partition** for the UEFI Secure Boot path.
- **El Torito boot record** for the BIOS-legacy path (chainloads the same UEFI binary set).
- `/live/filesystem.squashfs` (the root filesystem the UKI's initramfs mounts).
- `/live/filesystem.sha256` (the on-media digest the initramfs verifies before mounting — see "trust gap closure" below).

The ISO is the artifact users `dd` to a USB stick or boot in a VM.

## Prerequisites

- **Signed** shim, GRUB, and three UKI variants — all of these are pre-trust-boundary inputs to `build-iso.sh` and must already be signed by the time this script runs:
  - `shimx64.efi` (signed by the upstream MS-signed shim release; we don't re-sign shim).
  - `grubx64.efi` (signed by our vendor cert — see topic 03 for the signing flow).
  - `igos-live.efi`, `igos-install-gui.efi`, `igos-install-tui.efi` (three UKI variants — signed by our vendor cert).
- A built `filesystem.squashfs` (topic 04).
- Host tooling on PATH: `xorriso 1.5.6+` (SOURCE_DATE_EPOCH honoring), `mkfs.vfat`, `mcopy`, `mmd`. Install via Ubuntu's `libisoburn`, `dosfstools`, `mtools` packages.
- `installer/iso/grub/grub.cfg` (the ESP-side grub.cfg with three menu entries pointing at the UKIs).
- `/usr/share/grub/unicode.pf2` (GRUB font) — ships with the host's `grub-common` Ubuntu package.

## Step-by-step procedure

Canonical entry point is `scripts/build-iso.sh`. The script is env-var-driven (not flag-driven) because most invocations come from `scripts/build-intergenos.sh` which sets the vars from its own phase state.

### Manual invocation

```sh
ssh christopher@192.168.122.249  # build VM
cd /mnt/intergenos

SHIM=/path/to/shimx64.efi.signed \
GRUB=/path/to/grubx64.efi.signed \
UKI_LIVE=/path/to/igos-live.efi.signed \
UKI_INSTALL_GUI=/path/to/igos-install-gui.efi.signed \
UKI_INSTALL_TUI=/path/to/igos-install-tui.efi.signed \
SQUASHFS=/path/to/filesystem.squashfs \
OUTPUT=build/intergenos-1.0-dev1.iso \
SOURCE_DATE_EPOCH=$(date -u +%s) \
bash scripts/build-iso.sh
```

Critical: set `SOURCE_DATE_EPOCH` explicitly when you care about reproducibility. The script will fall back to "now" with a warning, but a deliberate value makes the ISO bit-identical across rebuild attempts.

### What the script does (6 phases)

1. **Stage ESP layout** — copies signed binaries into a temp staging tree at `EFI/BOOT/{BOOTX64.EFI,grubx64.efi}` (firmware-fallback path) and `EFI/InterGenOS/{shimx64,grubx64,igos-live,igos-install-gui,igos-install-tui,grub.cfg,fonts/unicode.pf2}` (canonical post-install path). The embedded grub.cfg inside `grubx64.efi` does `search --label IGOS_ESP` to locate the ESP at runtime, so files can live in either location.
2. **Build FAT32 ESP image** — `mkfs.vfat -F 32 -i <volserial> -n IGOS_ESP` with `volserial` deterministically derived from `SOURCE_DATE_EPOCH`. The label `IGOS_ESP` is what the embedded grub.cfg searches for. `find -exec touch -d @SDE` normalizes file mtimes so two same-SDE runs produce byte-identical ESP images.
3. **Stage ISO9660 root** — drops `filesystem.squashfs` to `/live/`, writes `/live/filesystem.sha256` (the sha256 of the squashfs), drops a volume marker at `/IGOS_LIVE` containing the VOLID. **Trust-gap-close assertion: the script then verifies `filesystem.sha256` landed non-empty.** init.sh hard-requires this file at boot; a silent sha256sum/awk/redirect failure would otherwise let an unbootable ISO ship (see "trust gap closure" below).
4. **xorriso invocation** — `-as mkisofs -iso-level 3 -full-iso9660-filenames -volid $VOLID -append_partition 2 0xef <ESP> -appended_part_as_gpt -e --interval:appended_partition_2:all:: -no-emul-boot -isohybrid-gpt-basdat --mbr-force-bootable -output $OUTPUT $ISO_ROOT`. Honors `SOURCE_DATE_EPOCH` (xorriso 1.5.6+ requirement).
5. **Self-verify** — `xorriso -indev` report confirms GPT, El Torito boot record present; `file -b` confirms ISO9660/hybrid shape. Failure removes the partial OUTPUT so a stale-but-broken file can't be confused for a good build.
6. **Emit manifest** — `<OUTPUT>.manifest` lists input SHAs, output SHA, xorriso/mkfs.vfat versions, script SHA, SDE, VOLID, volserial. Diff-friendly self-describing reproduction recipe.

## Trust-gap closure for the squashfs

The shim → GRUB → UKI signature chain is covered by Secure Boot, but the squashfs lives outside the UKI at `/live/filesystem.squashfs`. Without independent verification, an attacker could swap the squashfs on the media and still boot to a trusted UKI that loads a malicious rootfs. **The trust gap is closed in two places that must remain paired:**

- **Build-time (`scripts/build-iso.sh:316-329`):** writes `/live/filesystem.sha256` alongside the squashfs and asserts the write landed non-empty. Fails the ISO build if the digest file is empty/missing.
- **Boot-time (`installer/init/init.sh:113-131`):** reads `/run/iso/live/filesystem.sha256` after mounting the ISO, computes `sha256sum` of the squashfs, compares, drops to recovery shell on mismatch.

Changing either side without the other re-opens the gap. Keep them in lockstep — and never remove the build-time assertion thinking "init.sh will catch it" (the failure surfaces as kernel-panic-class abort on the user's machine instead of `scripts/build-iso.sh` exit 1 on the build VM).

## Validation

After successful completion:

- `<OUTPUT>` is a hybrid ISO9660+GPT image (`file -b`).
- `<OUTPUT>.manifest` exists with input/output SHAs.
- `xorriso -indev <OUTPUT> -report_about ALL` shows GPT + ESP partition + El Torito boot record.
- The accompanying log at `${LOG_DIR}/build_<timestamp>.log` (default `build/logs/iso/`) records the full run.

Boot test (smoke-level):

```sh
qemu-system-x86_64 -bios /usr/share/OVMF/OVMF_CODE.fd \
    -drive file=<OUTPUT>,format=raw,if=virtio -m 4G
```

See topic 06 for the full test-VM evaluation flow (Secure Boot enabled, MOK enrollment, install-gui/install-tui execution).

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ERROR: SHIM/GRUB/UKI_* not found: <path>` | env var pointing at the unsigned input | check the signing step (topic 03) emitted the `.signed` artifact and the env points at it |
| `ERROR: <binary> is not a PE32+ binary` | env var pointing at the wrong file (e.g., the `.cer` cert instead of the `.efi`) | re-check the env vars against the signing-output directory |
| `ERROR: xorriso version <X> is older than 1.5.6` | host has an old `libisoburn` | apt-upgrade libisoburn or use a host with 1.5.6+ |
| `FAIL: filesystem.sha256 empty or missing after sha256sum` | sha256sum/awk/redirect failed silently | inspect the immediately-preceding shell context: full filesystem, missing sha256sum, redirect to read-only mount, or SIGPIPE from awk |
| `FAIL: GPT not detected in xorriso -report_about output` | xorriso version too old (despite passing the 1.5.6 check) OR an `-as mkisofs` flag was tampered with | confirm xorriso version, restore the original flag set |
| Self-verify removes the OUTPUT after build | one of the GPT / El Torito / file-probe checks failed | inspect the `${LOG_DIR}/indev_<timestamp>.txt` report and re-trace which flag is causing the missing structure |
| Two same-SDE builds produce different sha256 | something fed mtime/random/locale-dependent input into the build | check FAT volserial derivation, mtimes of inputs, locale env vars; see the broader reproducibility-verification operational note for the broader reproducibility framing |

## Cross-references

- Topic 03: How to automate signing — produces the signed shim/GRUB/UKI inputs
- Topic 04: How to generate squashfs — produces the SQUASHFS input
- Topic 06: How to spin up a test VM with the ISO + evaluate the running build
- `scripts/build-iso.sh` — canonical reference
- `installer/init/init.sh:113-131` — boot-time half of the trust-gap closure
- `installer/iso/grub/grub.cfg` — embedded ESP-side menu
