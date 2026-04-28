# InterGenOS Full Build Pipeline Reference

**Created:** April 2, 2026
**Purpose:** Step-by-step reference for building InterGenOS from scratch, from empty VMs to a bootable system.
**Status:** Rebuilding after bricked VM. All code committed to master. Build VM needs reinstall.

---

## Pipeline Overview

```
Phase 1: Automated Ubuntu 24.04 install on KVM build VM (cloud-init)
Phase 2: LFS toolchain build (Chapters 5-7) in chroot
Phase 3: Core system build (Chapter 8) in chroot
Phase 4: System configuration (Chapter 9) in chroot
Phase 5: New core packages (19 packages moving from base tier) in chroot
Phase 6: Remaining base packages (20 packages) in chroot
Phase 7: Package the chroot into a bootable disk image
Phase 8: Create target VM from disk image
Phase 9: Validate
```

---

## Phase 1: Automated Ubuntu 24.04 Install on KVM Build VM

### What It Does
Creates or reinstalls the `igos-build` VM with Ubuntu 24.04 as the LFS build host.
The VM provides a known-good environment with all LFS prerequisites installed.

### Scripts/Tools Involved
- `virt-install` (on the physical host) -- creates the VM
- cloud-init user-data YAML -- automates the Ubuntu install
- **NO SCRIPT EXISTS YET** -- this needs to be written

### Inputs
- Ubuntu 24.04 cloud image (or server ISO)
- cloud-init configuration (user-data, meta-data)
- VM spec from `docs/research/vm_configurations_2026-04-02.md`

### Outputs
- Running `igos-build` VM with:
  - Ubuntu 24.04 installed
  - User `christopher` with SSH key access
  - All LFS build prerequisites installed (gcc, g++, bison, gawk, make, etc.)
  - virtiofs mount at `/mnt/intergenos` (shared with host)
  - `/mnt/igos` directory created (LFS build target root)
  - Sources directory at `/mnt/intergenos/build/sources/` populated

### VM Specifications (from prior config)
| Setting | Value |
|---------|-------|
| Name | igos-build |
| Disk | `/mnt/intergenos/vm/igos-build.qcow2`, 500 GiB qcow2 |
| Machine | pc-q35-noble |
| CPU | host-passthrough, 16 vCPUs |
| Memory | 32 GB (memfd shared, required for virtiofs) |
| Network | virtio, NAT via `default` |
| Graphics | SPICE |
| virtiofs | host `/mnt/intergenos` -> guest tag `intergenos` |

### What Needs to Happen Manually vs Automated
- **Automated (via cloud-init + virt-install):**
  - OS install
  - User creation
  - Package installation (build-essential, bison, gawk, texinfo, python3, etc.)
  - fstab entry for virtiofs
  - `/mnt/igos` directory creation and permissions
  - SSH key setup
- **Manual:**
  - Download the Ubuntu cloud image to the host (one-time)
  - Verify the virtiofs mount works after first boot

### Known Issues to Fix First
| Issue | Details |
|-------|---------|
| No automation script exists | Need to write a `create-build-vm.sh` that calls `virt-install` with cloud-init |
| Hardcoded IP in scripts | `host-check.py` and others reference `192.168.122.69` -- make configurable |
| virtiofs needs memfd | VM XML must include `<memoryBacking><source type="memfd"/><access mode="shared"/></memoryBacking>` |

### cloud-init User-Data (to be created)
Must install:
```
bash, binutils, bison, coreutils, diffutils, findutils, gawk, gcc, g++,
grep, gzip, m4, make, patch, perl, python3, sed, tar, texinfo, xz-utils,
wget, git, file, libarchive-tools (for bsdtar), zstd
```
Must configure:
```
- User: christopher (sudo, SSH key)
- fstab: intergenos /mnt/intergenos virtiofs defaults 0 0
- mkdir -p /mnt/igos
- Set /bin/sh -> bash (Ubuntu uses dash by default)
```

---

## Phase 2: LFS Toolchain Build (Chapters 5-7)

### What It Does
Builds the cross-compilation toolchain (Chapter 5), cross-compiled temporary tools (Chapter 6), and prepares the chroot environment (Chapter 7). This is the foundation everything else builds on.

### Scripts/Tools Involved

| Script | What It Does | Runs On |
|--------|-------------|---------|
| `scripts/host-check.py` | Validates the build VM meets LFS 13.0 requirements | Build VM (outside chroot) |
| `scripts/toolchain-build.sh` | Chapter 5: Builds cross-toolchain (5 packages) | Build VM (outside chroot) |
| `scripts/temp-tools-build.sh` | Chapter 6: Cross-compiles 18 temporary tools | Build VM (outside chroot) |
| `scripts/chroot-setup.sh` | Chapter 7.2-7.3: Mounts virtual filesystems, changes ownership | Build VM as root (outside chroot) |
| `scripts/chroot-enter.sh` | Chapter 7.4: Enters the chroot environment | Build VM as root (outside chroot) |
| `scripts/chroot-build.sh` | Chapter 7.5-7.12: Creates FHS dirs, essential files, builds 6 packages | Inside chroot |
| `scripts/chroot-teardown.sh` | Unmounts virtual filesystems | Build VM as root (outside chroot) |
| `packages/toolchain/*/build.sh` | Per-package build functions (configure/build/install) | Build VM (sourced by toolchain-build.sh) |

### Execution Order
```bash
# 1. Validate host requirements
ssh christopher@<build-vm> 'python3 /mnt/intergenos/scripts/host-check.py'

# 2. Build cross-toolchain (Chapter 5: 5 packages)
ssh christopher@<build-vm> 'bash /mnt/intergenos/scripts/toolchain-build.sh'

# 3. Cross-compile temporary tools (Chapter 6: 18 packages)
ssh christopher@<build-vm> 'nohup bash /mnt/intergenos/scripts/temp-tools-build.sh \
    > /mnt/intergenos/build/logs/temp-tools-stdout.log 2>&1 &'

# 4. Prepare chroot (Chapter 7.2-7.3)
ssh christopher@<build-vm> 'sudo bash /mnt/intergenos/scripts/chroot-setup.sh'

# 5. Build in chroot (Chapter 7.5-7.12: 6 packages + FHS layout)
ssh christopher@<build-vm> 'sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
    /mnt/intergenos/scripts/chroot-build.sh'
```

### Inputs
- Source tarballs in `/mnt/intergenos/build/sources/`
- Patches in `/mnt/intergenos/build/patches/` (glibc-fhs-1.patch)
- Package build scripts in `/mnt/intergenos/packages/toolchain/*/build.sh`

### Outputs
- Cross-toolchain installed to `/mnt/igos/tools/`
- 18 cross-compiled temporary tools installed to `/mnt/igos/usr/`
- FHS directory structure created in `/mnt/igos/`
- Essential files: `/etc/passwd`, `/etc/group`, `/etc/hosts`
- 6 in-chroot packages: gettext, bison, perl, python, texinfo, util-linux
- `/tools` directory removed (cleanup)
- Build logs in `/mnt/intergenos/build/logs/`

### Environment Variables
```bash
IGOS=/mnt/igos                          # Target root (replaces $LFS)
IGOS_TARGET=x86_64-igos-linux-gnu       # Target triple (replaces $LFS_TGT)
IGOS_SOURCES=/mnt/intergenos/build/sources  # Source tarballs
IGOS_PATCHES=/mnt/intergenos/build/patches  # Patch files
IGOS_LOGS=/mnt/intergenos/build/logs        # Build logs
IGOS_JOBS=$(nproc)                          # Parallel make jobs
```

### Chapter 5 Packages (Cross-Toolchain)
1. binutils-pass1 2.46.0
2. gcc-pass1 15.2.0 (with bundled GMP 6.3.0, MPFR 4.2.2, MPC 1.3.1)
3. linux-headers 6.18.10
4. glibc 2.43 (with FHS patch)
5. libstdc++ 15.2.0

### Chapter 6 Packages (Cross-Compiled Temporary Tools)
m4, ncurses, bash, coreutils, diffutils, file, findutils, gawk, grep, gzip,
make, patch, sed, tar, xz, binutils-pass2, gcc-pass2

### Chapter 7 Packages (In-Chroot Temporary)
gettext 1.0, bison 3.8.2, perl 5.42.0, python 3.14.3, texinfo 7.2, util-linux 2.41.3

### Known Issues to Fix First
| Issue | Script | Details |
|-------|--------|---------|
| Hardcoded IP | `toolchain-build.sh`, `temp-tools-build.sh`, `host-check.py` | `192.168.122.69` -- make configurable or remove |
| Shell injection in SSH | `host-check.py` line 209 | `run_command()` interpolates `remote` into shell string |
| Missing set -e | `toolchain-build.sh` | Intentionally omitted (SIGPIPE issue) but needs per-command error checking verified |

---

## Phase 3: Core System Build (Chapter 8)

### What It Does
Builds the complete LFS core system (82 packages) inside the chroot, with DESTDIR staging
and Slackware-style package tracking. This is the bulk of the work.

### Scripts/Tools Involved

| Script | What It Does |
|--------|-------------|
| `scripts/chroot-build-ch8.sh` | Drives the Chapter 8 build: sources build.sh per package, runs configure/build/check/install with DESTDIR staging |
| `scripts/pkg-functions.sh` | Package management: stage -> manifest -> archive -> deploy -> cleanup |
| `packages/core/*/build.sh` | Per-package build functions (configure, build, check, do_install, post_install) |
| `scripts/merge-kernel-config.sh` | Merges kernel config fragments into a .config file |
| `config/kernel/fragments/*.config` | 15 kernel configuration fragments |

### Execution
```bash
# Enter chroot and run Chapter 8 build
sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
    /mnt/intergenos/scripts/chroot-build-ch8.sh

# Resume after failure:
IGOS_START_AT=gcc-core sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
    /mnt/intergenos/scripts/chroot-build-ch8.sh
```

### Inputs
- Source tarballs (accessible inside chroot at `/sources`)
- Patches (accessible inside chroot at `/sources`)
- Build scripts at `/mnt/intergenos/packages/core/*/build.sh` (via virtiofs)
- Kernel config fragments at `/mnt/intergenos/config/kernel/fragments/`

### Outputs
- 82 installed packages with tracking:
  - Manifests: `/var/lib/igos/packages/<name>-<version>`
  - Archives: `/var/lib/igos/archives/<name>-<version>.igos.tar.gz`
- Kernel installed: `/boot/vmlinuz-6.18.10-igos`, `/boot/System.map-6.18.10`, `/boot/config-6.18.10`
- GRUB installed to `/usr/sbin/grub-install`, `/usr/bin/grub-mkconfig`
- Debug symbols stripped from binaries and libraries
- Test user removed
- Build logs in `/var/log/igos-build/`

### Inside-Chroot Environment (set by chroot-enter.sh)
```bash
HOME=/root
PATH=/usr/bin:/usr/sbin
MAKEFLAGS="-j$(nproc)"
TESTSUITEFLAGS="-j$(nproc)"
IGOS_JOBS=$(nproc)
IGOS_SOURCES=/sources           # Mapped from host /mnt/intergenos/build/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/var/log/igos-build
```

### Package Tracking Flow (pkg-functions.sh)
For each package:
1. **pkg_stage** -- Run `do_install()` with `DESTDIR=/tmp/igos-staging/<name>-<version>`
2. **pkg_manifest** -- Generate text manifest listing all files
3. **pkg_archive** -- Create `.igos.tar.gz` archive from staged files
4. **pkg_deploy** -- Copy staged files to live filesystem (/)
5. **pkg_cleanup** -- Remove staging directory

### Package Build Order (82 packages)
```
man-pages, iana-etc, glibc, zlib, bzip2, xz, lz4, zstd, file,
readline, pcre2, m4, bc, flex, tcl, expect, dejagnu, pkgconf,
binutils, gmp, mpfr, mpc, attr, acl, libcap, libxcrypt, shadow, gcc,
ncurses, sed, psmisc, gettext, bison, grep, bash, libtool, gdbm, gperf,
expat, inetutils, less, perl, xml-parser, intltool, autoconf, automake,
openssl, elfutils, libffi, sqlite, python, flit-core, packaging, wheel,
setuptools, ninja, meson, kmod, coreutils, diffutils, gawk, findutils,
groff, grub, gzip, iproute2, kbd, libpipeline, make, patch, tar, texinfo,
vim, nano, markupsafe, jinja2, systemd, dbus, man-db, procps-ng,
util-linux, e2fsprogs
```

### Kernel Build (linux-kernel package)
The kernel is built as part of Chapter 8. It requires a `.config` file.
- Config fragments are merged by `scripts/merge-kernel-config.sh`
- The merged config must be placed at the path the kernel build.sh expects
- The kernel build.sh looks for `$IGOS/config/kernel/intergenos.config` (this path reference needs clarification -- inside the chroot `$IGOS` is not set; the config must be accessible)
- **Note:** The kernel build.sh is NOT in the Chapter 8 script's package list -- it must be added or run separately as part of Chapter 10 (LFS book 10.3)

### Known Issues to Fix First
| Issue | Details |
|-------|---------|
| **CRITICAL: Missing set -e** | `chroot-build-ch8.sh` line 25 uses `set +h` but no `set -e`. Build failures in earlier packages can be silently ignored. |
| **CRITICAL: Unsafe string handling in stripping** | Lines 594-636 use unquoted variables in `for` loops and `objcopy`/`strip` commands. Filenames with spaces would break (unlikely in LFS but still wrong). |
| **HIGH: Wrong triplet in cleanup** | Line 668 searches for `x86_64-lfs-linux-gnu` but our triplet is `x86_64-igos-linux-gnu`. The LFS remnant cleanup won't match our files. |
| **HIGH: No error check on tar extraction** | Line 86: `tar -xf` can fail silently (missing tarball, wrong name), and the build continues with an empty directory. |
| **MAJOR: Fragile function type checking** | Lines 98-140: `type -t | grep -q function` can return false for aliases or builtins. |
| **HIGH: Archive format mismatch** | `pkg-functions.sh` comments say `.tar.zst` but code creates `.tar.gz`. Not wrong, but inconsistent documentation. |
| **Kernel config path** | `build.sh` looks for `$IGOS/config/kernel/intergenos.config` but inside chroot `$IGOS` is empty. Need to use `/mnt/intergenos/config/kernel/` via virtiofs or copy config into chroot. |
| **Kernel not in Ch8 package list** | `chroot-build-ch8.sh` does not include `linux-kernel`. Kernel build must be added (after systemd per LFS book). |

---

## Phase 4: System Configuration (Chapter 9)

### What It Does
Creates all system configuration files: network, hostname, locale, shell prompts,
systemd overrides, and InterGenOS identity files.

### Scripts/Tools Involved

| Script | What It Does |
|--------|-------------|
| `scripts/chroot-config-ch9.sh` | Creates all `/etc` configuration files inside the chroot |

### Execution
```bash
sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
    /mnt/intergenos/scripts/chroot-config-ch9.sh
```

### Inputs
- Running chroot environment with Chapter 8 packages installed

### Outputs
- `/etc/systemd/network/10-dhcp.network` -- DHCP for all `en*` interfaces
- `/etc/hostname` -- `intergenos`
- `/etc/hosts` -- localhost + intergenos entries
- `/etc/vconsole.conf` -- US keymap, Terminus16 font
- `/etc/locale.conf` -- `en_US.UTF-8`
- `/etc/profile` -- login shell setup, sources `/etc/profile.d/*.sh`
- `/etc/bashrc` -- non-login shell setup
- `/etc/profile.d/prompt.sh` -- custom PS1 (blue brackets, green path, red root)
- `/etc/inputrc` -- readline configuration
- `/etc/shells` -- `/bin/sh` and `/bin/bash`
- `/etc/os-release` -- freedesktop.org OS identification
- `/etc/lsb-release` -- LSB compatibility
- `/etc/igos-release` -- `1.0-dev`
- `/usr/bin/lsb_release` -- LSB query command
- `/etc/systemd/system/getty@tty1.service.d/noclear.conf` -- keep boot messages
- `/etc/systemd/coredump.conf.d/maxuse.conf` -- 5GB core dump limit

### Known Issues to Fix First
| Issue | Details |
|-------|---------|
| **MAJOR: Hardcoded Google DNS** | `/etc/systemd/network/10-dhcp.network` has `DNS=8.8.8.8` and `DNS=8.8.4.4`. DHCP already provides DNS servers. Remove the static DNS lines and let DHCP handle it, or use `UseDNS=true` to accept DHCP-provided nameservers. |
| **MODERATE: No systemd-networkd verification** | Script assumes systemd-networkd is installed but doesn't verify |

---

## Phase 5: New Core Packages (19 Packages Moving from Base)

### What It Does
Builds 19 packages that were originally in the `base` tier but need to be core
(built in the chroot) because they are foundational dependencies for everything else.
This was the lesson from the glib2 brick incident.

### Scripts/Tools Involved

| Tool | What It Does |
|------|-------------|
| `python -m igos-build` | The Python build system -- parses `package.yml` templates, resolves dependencies, executes builds |
| `igos-build/builder.py` | Build executor: extraction, phases, validation, tracking |
| `igos-build/parser.py` | Parses `package.yml` templates |
| `igos-build/graph.py` | Dependency graph resolution |
| `igos-build/styles/*.py` | Build style handlers (autotools, cmake, meson, make, custom) |
| `packages/base/<pkg>/package.yml` | Package templates (these need tier changed to `core`) |
| `packages/base/<pkg>/build.sh` | Build functions for custom-style packages |

### Execution
```bash
# Inside the chroot:
cd /mnt/intergenos
python -m igos-build --build --tracked --sources-dir /sources
```

### Build Order (19 packages, dependency-resolved)
```
 1. libtasn1          -- Foundation of TLS chain
 2. libunistring      -- Required by libidn2
 3. libuv             -- Async I/O, cmake dependency
 4. libarchive        -- Provides bsdtar, cmake dependency
 5. nghttp2           -- HTTP/2, curl dependency
 6. nspr              -- Required by nss
 7. linux-pam         -- (then rebuild shadow with PAM support)
 8. glib2             -- Most depended-upon library after glibc
 9. libidn2           -- Internationalized domain names
10. p11-kit           -- PKCS#11 trust module
11. sudo              -- Privilege management
12. libssh2           -- NEW PACKAGE NEEDED -- SSH transport for curl/git
13. nss               -- Network Security Services
14. make-ca           -- CA certificate bundle
15. libpsl            -- Public Suffix List
16. curl              -- Network transfer library
17. wget              -- Network file retriever (build system uses this)
18. cmake             -- Build system for many BLFS packages
19. git               -- Version control
```

### Inputs
- Source tarballs in `/sources` (inside chroot, mapped from host)
- Package templates in `/mnt/intergenos/packages/base/` (need tier change)
- Core system from Phase 3

### Outputs
- 19 tracked packages with manifests and archives
- Shadow rebuilt with PAM support
- Working TLS certificate chain (HTTPS works)
- curl, wget, cmake, git available inside the chroot

### Known Issues to Fix First
| Issue | Details |
|-------|---------|
| **Templates need tier change** | 19 package.yml files need `tier: base` changed to `tier: core` |
| **libssh2 doesn't exist yet** | New package template + build.sh needed |
| **Shadow PAM rebuild** | After linux-pam installs, shadow must be rebuilt with `--with-libpam`. Need a mechanism for rebuild (second pass or separate template). |
| **glib2 two-pass build** | glib2 needs gobject-introspection for full build, but g-i needs glib2. Need a two-pass strategy. |
| **Version mismatches in build.sh files** | 6 packages have build.sh hardcoded versions that don't match package.yml: libunistring (1.4.1 vs 1.4.2), libuv (1.52.0 vs 1.52.1), libarchive (3.8.5 vs 3.8.6), atop (2.11.0 vs 2.12.1), btop (1.4.4 vs 1.4.6), lsof (4.99.5 vs 4.99.6) |
| **lsof build.sh is broken** | Calls `./configure` but lsof has no configure script. Needs proper make-based build. |
| **Build order in chroot-build-ch8.sh** | The Chapter 8 script uses a hardcoded list. These 19 packages need to be appended to it, or run via igos-build separately. |

---

## Phase 6: Remaining Base Packages (20 Packages)

### What It Does
Builds the remaining packages that are end-user tools and services (not build infrastructure).
These are still built in the chroot, not on a live system.

### Scripts/Tools Involved
Same as Phase 5: `python -m igos-build` with the remaining base-tier packages.

### Execution
```bash
# Inside the chroot:
cd /mnt/intergenos
python -m igos-build --build --tracked --skip-built --sources-dir /sources
```
The `--skip-built` flag skips packages that already have tracked manifests (from Phase 5).

### Remaining Base Packages (20)
```
at, atop, btop, cpio, ed, exim, fcron, htop, iotop,
libnsl, libtirpc, lsof, pax, perl-file-fcntllock, popt,
rsync, screen, strace, time, which
```

### Inputs
- Source tarballs
- Package templates in `/mnt/intergenos/packages/base/`
- Core system + Phase 5 packages

### Outputs
- 20 tracked base packages with manifests and archives
- Complete base system ready for imaging

### Known Issues to Fix First
| Issue | Details |
|-------|---------|
| **exim build.sh** | `chmod 0770 /var/mail` without checking directory exists first |
| **lsof build.sh** | Calls `./configure` but lsof has no configure script |
| **atop version** | build.sh says 2.11.0, package.yml says 2.12.1 |
| **btop version** | build.sh says 1.4.4, package.yml says 1.4.6 |

---

## Phase 7: Package the Chroot into a Bootable Disk Image

### What It Does
Takes the completed chroot filesystem at `/mnt/igos` and turns it into a bootable
qcow2 disk image that can be used to create the target KVM virtual machine.

### Scripts/Tools Involved
- **NO SCRIPT EXISTS YET** -- this needs to be written
- Tools needed: `qemu-img`, `qemu-nbd` (or `losetup` + `kpartx`), `parted`/`sfdisk`, `mkfs.ext4`, `grub-install`

### The Process (to be automated)

#### Step 1: Create a raw disk image
```bash
# Create a 500G sparse qcow2 image
qemu-img create -f qcow2 /mnt/intergenos/vm/intergenos.qcow2 500G
```

#### Step 2: Connect the image as a block device
```bash
# Load the NBD kernel module
modprobe nbd max_part=8

# Connect the qcow2 image to /dev/nbd0
qemu-nbd --connect=/dev/nbd0 /mnt/intergenos/vm/intergenos.qcow2
```

#### Step 3: Partition the disk
Two layout options:

**Option A: BIOS boot (simpler, matches current GRUB build)**
```bash
# Create GPT partition table with BIOS boot partition + root
parted /dev/nbd0 mklabel gpt
parted /dev/nbd0 mkpart bios_grub 1MiB 2MiB
parted /dev/nbd0 set 1 bios_grub on
parted /dev/nbd0 mkpart root ext4 2MiB 100%
```

**Option B: UEFI boot (future-proof, needs additional packages)**
```bash
parted /dev/nbd0 mklabel gpt
parted /dev/nbd0 mkpart esp fat32 1MiB 512MiB
parted /dev/nbd0 set 1 esp on
parted /dev/nbd0 mkpart root ext4 512MiB 100%
# Requires: dosfstools (mkfs.fat), grub-efi, efibootmgr
```

**Recommendation:** Use Option A (BIOS boot) for now. GRUB is already built without EFI
support (`--disable-efiemu` in the GRUB build.sh). UEFI can be added later.

#### Step 4: Format the partition
```bash
mkfs.ext4 -L intergenos /dev/nbd0p2
```

#### Step 5: Mount and copy the chroot contents
```bash
mkdir -p /mnt/image-root
mount /dev/nbd0p2 /mnt/image-root

# Copy the entire chroot to the image
# Use tar to preserve permissions, ownership, symlinks, device nodes
tar -C /mnt/igos --one-file-system -cf - . | tar -C /mnt/image-root -xf -
```

**Important: Why tar, not cp or rsync:**
- `tar` with `--one-file-system` avoids copying virtual filesystems (/proc, /sys, /dev, /run)
- Preserves all permissions, ownership, timestamps, symlinks, hard links
- Handles special files (device nodes) correctly

#### Step 6: Create /etc/fstab inside the image
```bash
cat > /mnt/image-root/etc/fstab << 'EOF'
# /etc/fstab - InterGenOS
# <file system>  <mount point>  <type>  <options>         <dump>  <pass>
/dev/vda2         /              ext4    defaults          1       1
EOF
```

#### Step 7: Install GRUB bootloader
```bash
# Bind mount necessary filesystems
mount --bind /dev /mnt/image-root/dev
mount --bind /dev/pts /mnt/image-root/dev/pts
mount -t proc proc /mnt/image-root/proc
mount -t sysfs sysfs /mnt/image-root/sys

# Install GRUB to the disk (BIOS mode)
chroot /mnt/image-root grub-install --target=i386-pc /dev/nbd0

# Generate GRUB config
chroot /mnt/image-root grub-mkconfig -o /boot/grub/grub.cfg

# Unmount
umount /mnt/image-root/{sys,proc,dev/pts,dev}
```

**GRUB configuration notes:**
- `grub-mkconfig` auto-detects the kernel at `/boot/vmlinuz-6.18.10-igos`
- It reads `/etc/fstab` and `/etc/default/grub` for root device
- The root device will be `/dev/vda2` (virtio disk, partition 2)
- May need `/etc/default/grub` with `GRUB_CMDLINE_LINUX="root=/dev/vda2"` and `GRUB_DISABLE_OS_PROBER=true`

#### Step 8: Create /etc/default/grub
```bash
cat > /mnt/image-root/etc/default/grub << 'EOF'
# GRUB defaults for InterGenOS
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_DISTRIBUTOR="InterGenOS"
GRUB_CMDLINE_LINUX_DEFAULT=""
GRUB_CMDLINE_LINUX="root=/dev/vda2 console=ttyS0,115200"
GRUB_TERMINAL="console serial"
GRUB_SERIAL_COMMAND="serial --speed=115200"
GRUB_DISABLE_OS_PROBER=true
EOF
```
The `console=ttyS0` enables serial console, useful for `virsh console` access to the VM.

#### Step 9: Unmount and disconnect
```bash
umount /mnt/image-root
qemu-nbd --disconnect /dev/nbd0
```

### Inputs
- Completed chroot at `/mnt/igos` (with kernel, GRUB, all packages installed)

### Outputs
- Bootable qcow2 image at `/mnt/intergenos/vm/intergenos.qcow2`

### Known Issues / Considerations
| Issue | Details |
|-------|---------|
| **GRUB needs BIOS boot partition** | GRUB on GPT with BIOS requires a 1-2 MiB `bios_grub` partition. Without it, `grub-install` will fail. |
| **qemu-nbd requires root** | The entire imaging process runs as root on the build VM host |
| **Kernel modules path** | `depmod` must be run inside the chroot or the image to generate `/lib/modules/6.18.10/modules.dep` |
| **UUID vs device path** | `/etc/fstab` uses `/dev/vda2` which is stable for KVM virtio. For portability, could use `UUID=` or `LABEL=intergenos`. |
| **Serial console** | Adding `console=ttyS0` and serial GRUB config allows headless access via `virsh console`. Useful for debugging. |
| **No swap** | Single-partition layout has no swap. For a 12 GB RAM VM this is acceptable. Can add a swapfile later if needed. |
| **e2fsprogs must be installed** | `mkfs.ext4` comes from e2fsprogs, which is built in Chapter 8. But this runs on the HOST (build VM), not inside the chroot, so it needs to be available on Ubuntu. |
| **GRUB on host vs chroot** | `grub-install` must run from INSIDE the chroot (where the InterGenOS GRUB is installed) via `chroot`, not the host Ubuntu GRUB. The host GRUB won't match. |

### Alternative Approach: virt-builder / guestfish
Instead of manual `qemu-nbd` + `parted`, could use:
```bash
# Using guestfish (from libguestfs)
guestfish -N disk:500G -- \
    part-disk /dev/sda gpt : \
    mkfs ext4 /dev/sda1 : \
    mount /dev/sda1 / : \
    tar-in /tmp/chroot.tar / : \
    ...
```
This is more portable but adds a `libguestfs` dependency on the host. The `qemu-nbd` approach
is simpler and uses tools that are already present on a KVM host.

---

## Phase 8: Create Target VM from Disk Image

### What It Does
Creates the `intergenos` KVM virtual machine using the bootable disk image from Phase 7.

### Scripts/Tools Involved
- `virt-install` (on the physical host)
- **NO SCRIPT EXISTS YET** -- can be a simple one-liner

### Execution
```bash
virt-install \
    --name intergenos \
    --memory 12288 \
    --vcpus 12 \
    --cpu host-passthrough \
    --machine q35 \
    --os-variant generic \
    --disk /mnt/intergenos/vm/intergenos.qcow2,bus=virtio \
    --network network=default,model=virtio \
    --graphics vnc,listen=0.0.0.0 \
    --video virtio \
    --boot hd \
    --import \
    --noautoconsole
```

The `--import` flag tells virt-install to use the existing disk image (no install media needed).

### VM Specifications (from prior config)
| Setting | Value |
|---------|-------|
| Name | intergenos |
| Disk | `/mnt/intergenos/vm/intergenos.qcow2` |
| vCPUs | 12 |
| Memory | 12 GB |
| CPU | host-passthrough |
| Machine | q35 |
| Network | virtio, NAT |
| Graphics | VNC |

### Inputs
- Bootable qcow2 image from Phase 7

### Outputs
- Running `intergenos` VM, booted to a login prompt

---

## Phase 9: Validate

### What It Does
Verifies the system boots correctly, all packages are functional, and the system
matches the InterGenOS specification.

### Validation Checklist

#### Boot Validation
- [ ] VM boots without errors (check `virsh console` output)
- [ ] GRUB menu appears with InterGenOS entry
- [ ] Kernel loads: `uname -r` returns `6.18.10`
- [ ] systemd starts: `systemctl status` shows running
- [ ] Login prompt appears on tty1

#### System Identity
- [ ] `cat /etc/os-release` shows InterGenOS
- [ ] `lsb_release -a` works correctly
- [ ] `cat /etc/igos-release` shows `1.0-dev`
- [ ] `hostname` returns `intergenos`

#### Network
- [ ] DHCP acquires an address: `ip addr show`
- [ ] DNS resolution works: `getent hosts google.com`
- [ ] If curl/wget are installed: `curl -I https://google.com`

#### Core System
- [ ] `gcc --version` works
- [ ] `python3 --version` works
- [ ] `bash --version` works
- [ ] `systemctl list-units --type=service` shows expected services
- [ ] Shell prompt matches spec (blue brackets, green path, red root)

#### Package Tracking
- [ ] `/var/lib/igos/packages/` has manifests for all 82+ packages
- [ ] `/var/lib/igos/archives/` has `.igos.tar.gz` archives
- [ ] A sample `pkg_info` query works (source pkg-functions.sh first)

#### TLS Chain (if Phase 5 completed)
- [ ] `openssl s_client -connect google.com:443` succeeds with certificate verification
- [ ] CA certificates present in `/etc/ssl/certs/`

#### Filesystem
- [ ] `df -h` shows root partition mounted on `/dev/vda2`
- [ ] No stale virtual filesystem mounts
- [ ] `/var/log/igos-build/` contains build logs

### Known Issues to Watch For
| Issue | What to Check |
|-------|--------------|
| Kernel panic on boot | Kernel config missing virtio drivers. Check `40-kvm-guest.config` fragment includes `CONFIG_VIRTIO_BLK=y` and `CONFIG_VIRTIO_NET=y`. |
| No network | systemd-networkd not started. Check `systemctl status systemd-networkd`. |
| GRUB error: unknown filesystem | GRUB not installed correctly, or root device wrong in grub.cfg. |
| "Required file not found" | Library issue -- something from the chroot didn't copy correctly. Check `ldd /usr/bin/bash`. |

---

## Summary: What Exists vs What Needs to Be Written

### EXISTS and ready (after fixing known issues)
| Component | Path |
|-----------|------|
| Host requirements check | `scripts/host-check.py` |
| Chapter 5 toolchain build | `scripts/toolchain-build.sh` |
| Chapter 6 temp tools build | `scripts/temp-tools-build.sh` |
| Chroot setup/enter/teardown | `scripts/chroot-setup.sh`, `chroot-enter.sh`, `chroot-teardown.sh` |
| Chapter 7 chroot build | `scripts/chroot-build.sh` |
| Chapter 8 core build | `scripts/chroot-build-ch8.sh` |
| Package tracking functions | `scripts/pkg-functions.sh` |
| Chapter 9 config | `scripts/chroot-config-ch9.sh` |
| Kernel config merger | `scripts/merge-kernel-config.sh` |
| Kernel config fragments | `config/kernel/fragments/*.config` (15 fragments) |
| Python build system | `igos-build/` (parser, builder, graph, styles) |
| 84 core package templates | `packages/core/*/package.yml` |
| 28 toolchain packages | `packages/toolchain/*/build.sh` |
| 38 base package templates | `packages/base/*/package.yml` |

### DOES NOT EXIST -- needs to be written
| Component | Purpose |
|-----------|---------|
| `create-build-vm.sh` | Phase 1: cloud-init + virt-install for Ubuntu build VM |
| cloud-init user-data YAML | Phase 1: automated Ubuntu install configuration |
| `create-disk-image.sh` | Phase 7: partition, format, copy chroot, install GRUB |
| `create-target-vm.sh` | Phase 8: virt-install with --import |
| `validate-system.sh` | Phase 9: automated validation checklist |
| `libssh2` package template | Phase 5: needed for curl SSH and git SSH transport |
| Shadow PAM rebuild mechanism | Phase 5: rebuild shadow after linux-pam installs |

### NEEDS FIXING before rebuild
See detailed tables in each phase above. Critical items:
1. `chroot-build-ch8.sh`: Add `set -e` equivalent, fix stripping safety, fix triplet cleanup
2. `chroot-config-ch9.sh`: Remove hardcoded Google DNS
3. 6 base `build.sh` version mismatches
4. `lsof` build.sh: remove fake configure
5. `exim` build.sh: add /var/mail existence check
6. Hardcoded IPs in toolchain scripts
7. `pkg-functions.sh`: archive format documentation mismatch
8. Kernel not in Chapter 8 build list (needs adding or separate Phase 3.5)
9. 19 package.yml files need tier changed from `base` to `core`
