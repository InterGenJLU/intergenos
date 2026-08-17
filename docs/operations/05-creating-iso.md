# 05 — Creating the bootable ISO

**Audience:** maintainers assembling the final hybrid UEFI+BIOS ISO from signed components.

## Goal

Produce `intergenos-<version>.iso` — a single hybrid-bootable ISO image with:

- **GPT + ESP partition** for the UEFI Secure Boot path.
- **El Torito boot record** for the BIOS-legacy path (chainloads the same UEFI binary set).
- `/live/filesystem.squashfs` (the root filesystem the UKI's initramfs mounts).
- `/live/filesystem.verity` (the dm-verity merkle hashtree — the **sole** integrity path the initramfs verifies against; see [Trust-gap closure](#trust-gap-closure) below).
- `/live/filesystem.sha256` (a whole-file digest published as a **user-facing media diagnostic**. It is no longer a boot input; the boot path does not consult it.)

The ISO is the artifact users `dd` to a USB stick or boot in a VM.

## Prerequisites

- **Signed** shim, GRUB, and three UKI variants — all of these are pre-trust-boundary inputs to `build-iso.sh` and must already be signed by the time this script runs:
  - `shimx64.efi` (signed by the upstream MS-signed shim release; we don't re-sign shim).
  - `grubx64.efi` (signed by our vendor cert; see [Topic 03 — Automating signing](03-automating-signing.md) for the signing flow).
  - `igos-live.efi`, `igos-install-gui.efi`, `igos-install-tui.efi` (three UKI variants signed by our vendor cert).
- A built `filesystem.squashfs` (see [Topic 04 — Generating squashfs](04-generating-squashfs.md)).
- Host tooling on PATH: `xorriso 1.5.6+` (SOURCE_DATE_EPOCH honoring), `mkfs.vfat`, `mcopy`, `mmd`. Install via Ubuntu's `libisoburn`, `dosfstools`, `mtools` packages.
- `installer/iso/grub/grub.cfg` (the ESP-side grub.cfg with three menu entries pointing at the UKIs).
- `/usr/share/grub/unicode.pf2` (GRUB font) — ships with the host's `grub-common` Ubuntu package.

## Step-by-step procedure

The canonical entry point is `scripts/build-iso.sh`. The script is env-var-driven rather than flag-driven because most invocations come from `scripts/build-intergenos.sh`, which sets the variables from its own phase state.

### Manual invocation

```sh
ssh <builder-user>@<build-vm-ip>  # build VM
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

Set `SOURCE_DATE_EPOCH` explicitly whenever reproducibility matters. The script falls back to the current time with a warning, but a deliberate value makes the ISO bit-identical across rebuild attempts.

### What the script does (6 phases)

1. **Stage ESP layout** — copies signed binaries into a temp staging tree at `EFI/BOOT/{BOOTX64.EFI,grubx64.efi}` (firmware-fallback path) and `EFI/InterGenOS/{shimx64,grubx64,igos-live,igos-install-gui,igos-install-tui,grub.cfg,fonts/unicode.pf2}` (canonical post-install path). The embedded grub.cfg inside `grubx64.efi` self-locates the ESP via `$cmdpath` — the device this binary was loaded from (`regexp -s espdev '^\((.*)\)' "$cmdpath"`) — so it works whether grub was loaded from `/EFI/BOOT/` (fallback) or `/EFI/InterGenOS/` (post-install NVRAM entry). This deliberately avoids `search --label IGOS_ESP`, which would race when an install USB boots a machine that already has InterGenOS installed (both ESPs carry the `IGOS_ESP` label). The label search is only the `$cmdpath`-unset fallback.
2. **Build FAT32 ESP image** — `mkfs.vfat -F 32 -i <volserial> -n IGOS_ESP` with `volserial` deterministically derived from `SOURCE_DATE_EPOCH`. The label `IGOS_ESP` is what the embedded grub.cfg searches for. `find -exec touch -d @SDE` normalizes file mtimes so two same-SDE runs produce byte-identical ESP images.
3. **Stage ISO9660 root** — drops `filesystem.squashfs` to `/live/`, copies the dm-verity hashtree (`${SQUASHFS}.verity`, emitted by `build-squashfs.sh`) to `/live/filesystem.verity`, writes `/live/filesystem.sha256` (the user-facing media diagnostic), and drops a volume marker at `/IGOS_LIVE` containing the VOLID. The verity hashtree is a required input: the build fails if it is missing alongside the squashfs. The script then asserts that both `filesystem.verity` and `filesystem.sha256` landed non-empty. A silent `cp`/`sha256sum`/`awk`/redirect failure would otherwise let an unbootable or unverifiable ISO ship (see [Trust-gap closure](#trust-gap-closure) below).
4. **xorriso invocation** — `-as mkisofs -iso-level 3 -full-iso9660-filenames -volid $VOLID -append_partition 2 0xef <ESP> -appended_part_as_gpt -e --interval:appended_partition_2:all:: -no-emul-boot -isohybrid-gpt-basdat --mbr-force-bootable -output $OUTPUT $ISO_ROOT`. Honors `SOURCE_DATE_EPOCH` (xorriso 1.5.6+ requirement).
5. **Self-verify** — `xorriso -indev` report confirms GPT, El Torito boot record present; `file -b` confirms ISO9660/hybrid shape. Failure removes the partial OUTPUT so a stale-but-broken file can't be confused for a good build.
6. **Emit manifest** — `<OUTPUT>.manifest` lists input SHAs, output SHA, xorriso/mkfs.vfat versions, script SHA, SDE, VOLID, volserial. Diff-friendly self-describing reproduction recipe.

## Trust-gap closure for the squashfs {#trust-gap-closure}

The shim → GRUB → UKI signature chain is covered by Secure Boot, but the squashfs lives outside the UKI at `/live/filesystem.squashfs`. Without independent verification, an attacker could swap the squashfs on the media and still boot a trusted UKI that loads a malicious root filesystem.

**The gap is closed by dm-verity alone — sealed verity or loudly nothing.** The live UKI's *sealed* cmdline carries `igos.verity.roothash=<HEX>` (injected by the UKI phase from `filesystem.squashfs.verity-params`). The hashtree ships on the media at `/live/filesystem.verity`; `init.sh` activates a dm-verity device and the kernel verifies each 4 KiB block as it is read. Because the root hash lives inside the Secure-Boot-signed UKI, an attacker cannot swap the squashfs without invalidating the chain.

**There is no sha256 fallback boot path, deliberately.** An earlier design verified `/live/filesystem.sha256` when the cmdline carried no root hash. That was removed because the digest file sits on the *same media* as the squashfs an attacker would be swapping, so an attacker who can replace one can replace both — the fallback verified nothing an adversary could not also forge, while presenting as a security control. `init.sh` now **fails closed**: absent `igos.verity.roothash=` it refuses to boot, unless the explicit `igos.dev.allow_unverified=1` marker is present on the cmdline, which a sealed and signed release UKI cannot carry. `/live/filesystem.sha256` still ships, but purely as a user-facing media-integrity diagnostic.

The three checkpoints that keep this honest:

- **Build-time, UKI assembly (`scripts/build-iso.sh`, the sealed-roothash assertion that runs before staging):** every UKI's embedded `.cmdline` must seal an `igos.verity.roothash=` equal to `ROOT_HASH` in the squashfs's `.verity-params`. A mismatch is always fatal. Absence is fatal on a release build and downgrades to a loud warning only under `UNSIGNED_TEST=1`. This is the gate that catches a stale UKI signed against a different squashfs.
- **Build-time, ISO-root staging (`scripts/build-iso.sh`, phase 3 below):** copies `filesystem.verity` — required, the build fails if the hashtree is absent alongside the squashfs — writes the diagnostic `filesystem.sha256`, and asserts both landed non-empty.
- **Boot-time (`installer/init/init.sh`, the "Verify + mount squashfs" block):** parses `igos.verity.roothash=` from `/proc/cmdline`, activates dm-verity against `/run/iso/live/filesystem.verity`, and mounts `/dev/mapper/igos-root`. Without a root hash it fails closed as described above.

Keep the build-time and boot-time halves in lockstep, and never remove a build-time assertion on the assumption that `init.sh` will catch it: the failure then surfaces as an unbootable image on the user's machine instead of a `scripts/build-iso.sh` exit 1 on the build VM.

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

See [Topic 06 — Test VM and evaluation](06-test-vm-and-evaluation.md) for the full test-VM evaluation flow (Secure Boot enabled, MOK enrollment, install-gui/install-tui execution).

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ERROR: SHIM/GRUB/UKI_* not found: <path>` | env var pointing at the unsigned input | check the signing step ([Topic 03](03-automating-signing.md)) emitted the `.signed` artifact and the env points at it |
| `ERROR: <binary> is not a PE32+ binary` | env var pointing at the wrong file (e.g., the `.cer` cert instead of the `.efi`) | re-check the env vars against the signing-output directory |
| `ERROR: xorriso version <X> is older than 1.5.6` | host has an old `libisoburn` | apt-upgrade libisoburn or use a host with 1.5.6+ |
| `FAIL: verity hashtree not found alongside squashfs` | `phase_squashfs` didn't emit `${SQUASHFS}.verity` (veritysetup format skipped/failed, or ISO resumed from a squashfs built before the verity reorder) | re-run `scripts/build-squashfs.sh` (or resume from `phase_squashfs`) so the hashtree lands next to the squashfs |
| `FAIL: filesystem.verity` or `filesystem.sha256 empty or missing` | the cp/sha256sum/awk/redirect failed silently | inspect the immediately-preceding shell context: full filesystem, missing sha256sum, redirect to read-only mount, or SIGPIPE from awk |
| `FAIL: <UKI> seals no igos.verity.roothash=` (release build) | the UKI was built before the squashfs, or from a stale staging copy | rebuild the UKIs from the current squashfs's `.verity-params`; never sign a UKI staged from a prior ceremony |
| `FAIL: <UKI> seals roothash <X>` (mismatch against `.verity-params`) | the UKI and the squashfs on the media are from different runs — the stale-UKI class | re-run `phase_ukis_verity` against the current squashfs and re-sign; this gate is what prevents shipping a boot-time dm-verity failure |
| `FAIL: GPT not detected in xorriso -report_about output` | xorriso version too old (despite passing the 1.5.6 check) OR an `-as mkisofs` flag was tampered with | confirm xorriso version, restore the original flag set |
| Self-verify removes the OUTPUT after build | one of the GPT / El Torito / file-probe checks failed | inspect the `${LOG_DIR}/indev_<timestamp>.txt` report and re-trace which flag is causing the missing structure |
| Two same-SDE builds produce different sha256 | something fed mtime/random/locale-dependent input into the build | check FAT volserial derivation, mtimes of inputs, and locale env vars; see [`docs/architecture/reproducible-builds-design.md`](../architecture/reproducible-builds-design.md) for the full reproducibility framing |

## Cross-references

- [Topic 03 — Automating signing](03-automating-signing.md): produces the signed shim/GRUB/UKI inputs.
- [Topic 04 — Generating squashfs](04-generating-squashfs.md): produces the `SQUASHFS` input.
- [Topic 06 — Test VM and evaluation](06-test-vm-and-evaluation.md): spinning up a test VM with the ISO and evaluating the running build.
- [`docs/architecture/reproducible-builds-design.md`](../architecture/reproducible-builds-design.md): the reproducibility framing behind `SOURCE_DATE_EPOCH` and the deterministic ESP/ISO layout.
- `scripts/build-iso.sh`: canonical reference — the sealed-roothash assertion and the ISO-root staging assertions.
- `installer/init/init.sh`, the "Verify + mount squashfs" block: boot-time half of the trust-gap closure (dm-verity as the sole path, fail-closed without a sealed root hash).
- `installer/iso/grub/grub.cfg`: embedded ESP-side menu.
