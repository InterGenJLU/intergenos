# 06 — Test VM with the ISO + evaluating the running build

**Audience:** maintainers smoke-testing a fresh ISO before signing it for release, or debugging a regression that surfaced post-build.

## Goal

Boot a built ISO inside a Secure-Boot-enabled VM on the workstation, observe the three boot modes (`live`, `install-gui`, `install-tui`), and run the smoke checks against the live session and the installed target to validate the artifact end-to-end.

## Prerequisites

- A signed ISO at a known path (topic 05).
- Workstation has libvirt + KVM and access to OVMF firmware files (`apt install ovmf`). OVMF provides the UEFI firmware blob the test VM uses.
- `swtpm` installed if you want to exercise the TPM-PCR path (`apt install swtpm`).
- A VNC viewer or `virt-viewer` for the graphical console — the GDM-driven boot modes (live, install-gui) need a graphical surface; install-tui can be observed via serial console alone.
- ~16GB free RAM (test VM is sized 8GB by convention; 16GB is comfortable headroom on a workstation that's also running the build VM).

## Owner gates the VM start

Per the operational rule on observable lifecycle events: **owner clicks "Run" in virt-manager, not the agent.** When the boot sequence is the deliverable the owner needs to observe (POST output, firmware UI, MOK enrollment prompt, GDM login screen, install-tui banner), the start action is owner-driven so frame 1 isn't missed.

Agent role: prepare the VM definition + shut it off cleanly. Owner role: hit Run, observe, capture findings. Agent role thereafter: investigate findings via SSH or serial-console capture.

## Step-by-step procedure

### 1. Create the test VM definition (once per ISO)

```sh
# Per D-016, scratch artifacts live under ~/tmp/<workflow>/, not /tmp.
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

`--print-xml > ~/tmp/igos-test/igos-test.xml` + `virsh define` gives us a defined-but-shut-off VM — owner hits Run later. `--boot uefi` selects OVMF firmware. `--tpm backend.type=emulator` gives the VM a software TPM so the kernel's TPM measurement path exercises.

For Secure-Boot-enabled testing, the VM's NVRAM needs the Microsoft KEK/db preloaded — virt-manager's "Generate a new TPM/Secure Boot template" option in the GUI sets this up; the equivalent CLI is `--boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes`. If your libvirt is too old for `firmware.feature*`, manually copy `/usr/share/OVMF/OVMF_VARS_4M.ms.fd` to the per-VM NVRAM path libvirt expects (typically `/var/lib/libvirt/qemu/nvram/igos-test_VARS.fd`).

### 2. Owner boots the VM in the desired mode

The ISO's GRUB menu (from `installer/iso/grub/grub.cfg`) offers three entries:

- **InterGenOS Live** — boots `igos-live.efi` (kernel cmdline `igos.mode=live`)
- **InterGenOS Install (GUI)** — boots `igos-install-gui.efi` (cmdline `igos.mode=install-gui`)
- **InterGenOS Install (TUI)** — boots `igos-install-tui.efi` (cmdline `igos.mode=install-tui`)

Owner: select the relevant entry, hit Enter, observe.

### 3. Evaluate the boot sequence

What to look for:

| Phase | Expected | Failure signals |
|---|---|---|
| OVMF firmware splash | "TianoCore" briefly | Hung firmware = OVMF version too old / NVRAM corrupted |
| Shim load | No banner if signature OK | "Verification failed: ..." = vendor cert not enrolled / shim build mismatch |
| MokManager prompt (first boot only) | Prompts to enroll the InterGenOS vendor cert into the MOK list | If absent on first boot, the kernel's signature verify will fail downstream |
| GRUB menu | Three boot entries shown | Empty menu / "no kernel found" = grub.cfg drift or grubx64.efi sig fail |
| Kernel boot | systemd-stub then kernel messages then init.sh `[init] boot mode: <mode>` | Kernel panic = init.sh issue (likely from SHA256-verify-before-mount per topic 05 trust-gap closure) |
| squashfs verify | `[init] verifying squashfs sha256 (this takes a few seconds)...` followed by `[init] squashfs sha256 verified (<sha>)` | `[init] FATAL: squashfs sha256 mismatch` = ISO was tampered or the build-iso.sh assertion was bypassed |
| Mode-specific dispatch | live/install-gui → GDM autologin; install-tui → forge-tui.service on tty1 | Black screen for >30s on graphical modes = GDM/Wayland trouble; missing tty1 prompt on install-tui = forge-tui.service ConditionKernelCommandLine mismatch |

### 4. Run the smoke checks

Once a live or installed session is up, the smoke harness at `installer/smoke/` walks four areas:

```sh
# From inside the live or installed session:
cd /usr/lib/python3.14/site-packages/installer/smoke 2>/dev/null \
    || cd /mnt/intergenos/installer/smoke    # fallback for repo-mounted dev case
sudo bash smoke-test.sh
```

The harness runs four check scripts:

- `checks/boot.sh` — confirms the boot chain landed: shim, grub, UKI present in `/boot/efi/EFI/InterGenOS/` (installed) or `/run/iso/...` (live); kernel modules loaded.
- `checks/pkm.sh` — pkm database exists at `/var/lib/igos/packages`, `pkm list` returns ≥0 (non-error); a basic verify pass against a sample of installed packages.
- `checks/services.sh` — critical systemd units in expected state: gdm running (live + install-gui), forge-tui completed (install-tui), nftables loaded, sshd present.
- `checks/signing.sh` — the signed manifest + release key are present at `/var/lib/igos/manifest/` on installed systems (per the parallel-lane fix at `8b0aa3b2`). On live ISO this check `check_skip`s — expected.

A green smoke run means the ISO is structurally healthy at the live level. **It is NOT a guarantee that an actual install completes successfully** — that requires running Forge against the spare disk (step 5).

### 5. Run an actual install (install-gui or install-tui mode)

After the live or install-mode boot lands, exercise Forge against the test VM's spare disk:

- **install-gui:** the GDM-autologin session launches the Forge GTK4 wizard. Walk through screens (welcome → disk → user → confirm → progress → done). The "Install" button at the confirm step is the destructive trigger.
- **install-tui:** forge-tui.service claims tty1 and runs the declarative-builder. The "walking" phase asks the small set of yaml-bound questions (locale, timezone, hostname, package groups); the interactive phase prompts for disk + passwords; the run phase orchestrates the 13-phase install pipeline.

Owner observes progress. Agent captures `journalctl -b -u forge-tui` (or `~/.cache/forge/install.log` for GUI) and any failure messages.

### 6. Reboot into the installed target

After install completes, reboot the VM (remove the CD-ROM in virt-manager so it boots from disk):

```sh
virsh shutdown igos-test
virsh detach-disk igos-test /path/to/intergenos.iso --config
virsh start igos-test  # owner re-confirms via virt-manager Run for observation
```

The installed system boots through the BCD's InterGenOS entry → shim → grub → installed UKI (or kernel + initramfs, depending on installed config) → systemd → GDM (if desktop) or getty (if minimal).

On first boot of the installed target, the first-boot-password-greeter unit fires on tty1 (its kernel-cmdline guard means it only fires on installed-system boots, not live-ISO boots). Owner sets root + user passwords, system continues to GDM. Owner can now exercise the installed system as a regular user.

## What to grep journalctl for

Common signals when debugging a failed test boot:

```sh
# Live or installed session — pull boot-time logs
journalctl -b 0 --no-pager | less

# Init script messages (live boot diagnostics) — visible in early-boot section
journalctl -b 0 --no-pager | grep '\[init\]'

# Forge install run
journalctl -b 0 -u forge-tui     # install-tui
ls -lh ~/.cache/forge/install.log    # install-gui (file-based)

# Boot chain trust violations
journalctl -b 0 --no-pager | grep -iE 'verification failed|sb_verify|mok'

# Specific service failures
journalctl -b 0 -u gdm
journalctl -b 0 -u systemd-firstboot
journalctl -b 0 -p err           # all error-level events
```

## Validation

A passing test session:

- Owner observes all 6 boot-sequence phases above as expected (no failure signals).
- `smoke-test.sh` exits 0 with all four checks green (or `signing.sh` skipped on live, expected).
- An actual install via Forge completes; the installed system boots and presents a working desktop.
- `journalctl -b 0 -p err --no-pager` is empty or contains only known-noisy entries.

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Shim aborts on first boot with "verification failed" before MokManager prompt | Vendor cert not in MOK list AND shim's built-in MOK has changed | re-check shim binary's embedded vendor cert; rebuild + re-sign |
| MokManager prompts then immediately exits without enrolling | User pressed Cancel; vendor cert remains unenrolled | reboot, re-enter MokManager via prompt at next boot, complete enrollment |
| Init.sh fatal: missing filesystem.sha256 | build-iso.sh assertion bypassed OR ISO post-processed with sha256 file stripped | rebuild the ISO; the build-iso.sh:316-329 assertion should catch this at build time (topic 05) |
| init.sh fatal: squashfs sha256 mismatch | ISO was modified after build OR the squashfs corrupted in transit | re-download/re-`dd` the ISO from the source; sha256-check the host copy before writing to USB |
| Install-tui's Forge prompts work but the install fails partway | per-phase diagnostic in `journalctl -u forge-tui`; common: missing pre-flight package in chroot, disk too small, MOK enrollment skipped | inspect the journal output; topic 09 has the canonical pattern (deferred bugs surface during install, not at build time) |
| GDM never appears on install-gui | Wayland session crash or display-manager.service masked | `journalctl -b 0 -u gdm` and `systemctl status gdm`; common: a noisy-daemon mask in init.sh accidentally included gdm |

## Cross-references

- Topic 05: How to create an ISO — produces the artifact this topic boots
- Topic 09: Cost of deferral — case studies on regressions surfaced at test-VM time
- The smoke harness at `installer/smoke/` — canonical check set
- `installer/init/init.sh` — the [init] log messages this topic interprets
