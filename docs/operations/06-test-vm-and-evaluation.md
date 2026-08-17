# 06 — Test VM: booting the ISO and evaluating the running build

**Audience:** maintainers smoke-testing a fresh ISO before signing it for release, or debugging a regression that surfaced after a build.

## Goal

Boot a built ISO inside a Secure-Boot-enabled VM on the workstation, observe the three boot modes (`live`, `install-gui`, `install-tui`), and run the smoke checks against the live session and the installed target to validate the artifact end-to-end.

## Prerequisites

- A signed ISO at a known path (topic 05).
- Workstation has libvirt + KVM and access to OVMF firmware files (`apt install ovmf`). OVMF provides the UEFI firmware blob the test VM uses.
- `swtpm` installed if you want to exercise the TPM-PCR path (`apt install swtpm`).
- A VNC viewer or `virt-viewer` for the graphical console. The GDM-driven boot modes (live, install-gui) need a graphical surface; install-tui can be observed via serial console alone.
- ~16GB free RAM. The test VM is sized at 8GB; 16GB is comfortable headroom on a workstation that is also running the build VM.

## Start the VM by hand for boot-sequence observation

When the boot sequence itself is what you need to observe — POST output, firmware UI, MOK enrollment prompt, GDM login screen, install-tui banner — start the VM manually (the "Run" button in virt-manager, or `virsh start`) so the first frame is not missed.

The pattern is: define the VM and leave it shut off, start it by hand to observe the boot, then investigate any findings via SSH or serial-console capture.

## Step-by-step procedure

### 1. Create the test VM definition (once per ISO)

```sh
# Keep scratch artifacts under ~/tmp/<workflow>/ rather than /tmp.
mkdir -p ~/tmp/igos-test

virt-install --name igos-test \
    --memory 8192 \
    --vcpus 4 \
    --cpu host-passthrough \
    --machine q35 \
    --boot uefi \
    --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb \
    --disk path=/var/lib/libvirt/images/igos-test.qcow2,size=64,format=qcow2,bus=virtio \
    --disk path=/path/to/intergenos.iso,device=cdrom,readonly=on \
    --network network=default,model=virtio \
    --graphics vnc,listen=0.0.0.0 \
    --video qxl \
    --noautoconsole \
    --print-xml > ~/tmp/igos-test/igos-test.xml

virsh define ~/tmp/igos-test/igos-test.xml
virsh shutdown igos-test 2>/dev/null || true  # ensure shut-off state
```

`--print-xml > ~/tmp/igos-test/igos-test.xml` plus `virsh define` produces a defined-but-shut-off VM, ready to start by hand. `--boot uefi` selects OVMF firmware. `--tpm backend.type=emulator` gives the VM a software TPM, so the kernel's TPM measurement path is exercised.

For Secure-Boot-enabled testing, the VM's NVRAM needs the Microsoft KEK/db preloaded — virt-manager's "Generate a new TPM/Secure Boot template" option in the GUI sets this up; the equivalent CLI is `--boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes`. If your libvirt is too old for `firmware.feature*`, manually copy `/usr/share/OVMF/OVMF_VARS_4M.ms.fd` to the per-VM NVRAM path libvirt expects (typically `/var/lib/libvirt/qemu/nvram/igos-test_VARS.fd`).

### 2. Boot the VM in the desired mode

The ISO's GRUB menu (from `installer/iso/grub/grub.cfg`) offers three entries:

- **InterGenOS Live** — boots `igos-live.efi` (kernel cmdline `igos.mode=live`)
- **InterGenOS Install (GUI)** — boots `igos-install-gui.efi` (cmdline `igos.mode=install-gui`)
- **InterGenOS Install (TUI)** — boots `igos-install-tui.efi` (cmdline `igos.mode=install-tui`)

Select the relevant entry, press Enter, and observe.

### 3. Evaluate the boot sequence

What to look for:

| Phase | Expected | Failure signals |
|---|---|---|
| OVMF firmware splash | "TianoCore" briefly | Hung firmware = OVMF version too old / NVRAM corrupted |
| Shim load | No banner if signature OK | "Verification failed: ..." = vendor cert not enrolled / shim build mismatch |
| MokManager prompt (first boot only) | Prompts to enroll the InterGenOS vendor cert into the MOK list | If absent on first boot, the kernel's signature verify will fail downstream |
| MokManager availability (virgin hardware) | `mmx64.efi` staged beside EVERY shim instance on the ESP (`EFI/BOOT/` and `EFI/InterGenOS/`, live and installed), plus `EFI/InterGenOS/intergenos-secure-boot-ca.cer` for Enroll-key-from-disk | Decided 2026-07-16: a shim launched from a directory without `mmx64.efi` cannot offer enrollment — on factory-key Secure Boot hardware the boot dead-ends at `Verification Failed (0x1A)`, and with a pending enrollment it bootloops ("MOK Manager not found"). A pre-enrolled test VM never reproduces this — verify on hardware with virgin firmware keys |
| GRUB menu | Three boot entries shown | Empty menu / "no kernel found" = grub.cfg drift or grubx64.efi sig fail |
| Kernel boot | systemd-stub then kernel messages then init.sh `[init] boot mode: <mode>` | Kernel panic = init.sh issue (likely in the verify-before-mount path — topic 05 trust-gap closure) |
| squashfs verify | `[init] activating dm-verity for squashfs (root hash: <prefix>…)` followed by `[init] dm-verity active at /dev/mapper/igos-root` | `[init] FATAL: veritysetup open failed` = the squashfs or hashtree on the media does not match the root hash sealed in the signed UKI. `[init] FATAL: no igos.verity.roothash= on the cmdline` = the UKI seals no root hash, which a release UKI always does — the ISO was built or signed out of lockstep |
| Mode-specific dispatch | live/install-gui → GDM autologin; install-tui → forge-tui.service on tty1 | Black screen for >30s on graphical modes = GDM/Wayland trouble; missing tty1 prompt on install-tui = forge-tui.service ConditionKernelCommandLine mismatch |

### 4. Run the smoke checks

Once a live or installed session is up, run the smoke harness at `installer/smoke/`:

```sh
# From inside the live or installed session:
cd /usr/lib/python3.14/site-packages/installer/smoke 2>/dev/null \
    || cd /mnt/intergenos/installer/smoke    # fallback for the repo-mounted dev case
sudo bash smoke-test.sh
```

The harness runs four check scripts:

- `checks/boot.sh` — confirms the boot chain landed: shim, grub, UKI present in `/boot/efi/EFI/InterGenOS/` (installed) or `/run/iso/...` (live); kernel modules loaded.
- `checks/pkm.sh` — pkm database exists at `/var/lib/igos/packages`, `pkm list` returns ≥0 (non-error); a basic verify pass against a sample of installed packages.
- `checks/services.sh` — critical systemd units in expected state: gdm running (live + install-gui), forge-tui completed (install-tui), nftables loaded, sshd present.
- `checks/signing.sh` — the signed manifest and release key are present at `/var/lib/igos/manifest/` on installed systems. On a live ISO this check skips (`check_skip`), which is expected.

A green smoke run means the ISO is structurally healthy at the live level. **It is not a guarantee that an actual install completes successfully.** Confirming that requires running Forge against the spare disk (step 5).

### 5. Run an actual install (install-gui or install-tui mode)

After the live or install-mode boot lands, exercise Forge against the test VM's spare disk:

- **install-gui:** the GDM-autologin session launches the Forge GTK4 wizard. Walk through screens (welcome → disk → user → confirm → progress → done). The "Install" button at the confirm step is the destructive trigger.
- **install-tui:** forge-tui.service claims tty1 and runs the declarative builder. The walking phase asks the small set of yaml-bound questions (locale, timezone, hostname, package groups); the interactive phase prompts for disk and passwords; the run phase orchestrates the 13-phase install pipeline.

Capture progress and any failure messages from the journal: `journalctl -b -u forge-tui` for install-tui, or `journalctl -b -u forge-installer-backend` for the GUI (which runs its install through the D-Bus backend service).

### 6. Reboot into the installed target

After install completes, reboot the VM (remove the CD-ROM in virt-manager so it boots from disk):

```sh
virsh shutdown igos-test
virsh detach-disk igos-test /path/to/intergenos.iso --config
virsh start igos-test  # or hit Run in virt-manager to observe the boot
```

The installed system boots through the UEFI boot manager's InterGenOS entry (created by `efibootmgr` during install) → shim → grub → installed UKI (or kernel plus initramfs, depending on the installed config) → systemd → GDM (if desktop) or getty (if minimal).

First boot of the installed target goes **straight to GDM** — there is no first-boot password prompt. The installer already collected the username and the root and user passwords in its wizard and applied them to the target during the install (`installer/backend/users.py`), so the credentials are set before the system ever boots. Log in as the user the install created and exercise the system from there. (A tty1 first-boot password greeter existed in an earlier design and was removed on 2026-05-22 once the installer owned credential collection; it had become a redundant second prompt for a password the user had already chosen.)

## What to grep journalctl for

Common signals when debugging a failed test boot:

```sh
# Live or installed session — pull boot-time logs
journalctl -b 0 --no-pager | less

# Init script messages (live boot diagnostics) — visible in early-boot section
journalctl -b 0 --no-pager | grep '\[init\]'

# Forge install run
journalctl -b 0 -u forge-tui                 # install-tui
journalctl -b 0 -u forge-installer-backend   # install-gui (D-Bus backend service)

# Boot chain trust violations
journalctl -b 0 --no-pager | grep -iE 'verification failed|sb_verify|mok'

# Specific service failures
journalctl -b 0 -u gdm
journalctl -b 0 -u systemd-firstboot
journalctl -b 0 -p err           # all error-level events
```

## Validation

A passing test session:

- Every boot-sequence phase in the table above behaves as expected, with no failure signals.
- `smoke-test.sh` exits 0 with all four checks green (or with `signing.sh` skipped on live, which is expected).
- An actual install via Forge completes; the installed system boots and presents a working desktop.
- `journalctl -b 0 -p err --no-pager` is empty or contains only known-noisy entries.

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Shim aborts on first boot with "verification failed" before MokManager prompt | Vendor cert not in MOK list AND shim's built-in MOK has changed | re-check shim binary's embedded vendor cert; rebuild + re-sign |
| MokManager prompts then immediately exits without enrolling | User pressed Cancel; vendor cert remains unenrolled | reboot, re-enter MokManager via prompt at next boot, complete enrollment |
| init.sh fatal: `no igos.verity.roothash= on the cmdline` | the booted UKI seals no root hash — an unsigned or dev-built UKI, or an ISO assembled out of lockstep with its squashfs | rebuild the UKIs from the current squashfs's `.verity-params` and re-sign; `build-iso.sh` asserts this at assembly time and should catch it first (topic 05). For a deliberate dev boot, add `igos.dev.allow_unverified=1` to the cmdline explicitly |
| init.sh fatal: `veritysetup open failed` | the squashfs or the hashtree on the media does not match the sealed root hash — media modified after build, corrupted in transit, or a stale UKI signed against a different squashfs | re-`dd` the ISO from the source and sha256-check the host copy first; if it reproduces from a clean write, the ISO itself was assembled with mismatched UKI and squashfs |
| Install-tui's Forge prompts work but the install fails partway | per-phase diagnostic in `journalctl -u forge-tui`; common causes: a missing pre-flight package in the chroot, a disk too small, or MOK enrollment skipped | inspect the journal output; topic 09 covers the iteration pattern for bugs that surface during install rather than at build time |
| GDM never appears on install-gui | Wayland session crash or display-manager.service masked | `journalctl -b 0 -u gdm` and `systemctl status gdm`; common: a noisy-daemon mask in init.sh accidentally included gdm |

## Cross-references

- Topic 05: How to create an ISO — produces the artifact this topic boots
- The smoke harness at `installer/smoke/` — the check set run in step 4
- `installer/init/init.sh` — the source of the `[init]` log messages this topic interprets
