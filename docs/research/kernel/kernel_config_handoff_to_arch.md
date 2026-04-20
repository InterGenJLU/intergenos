# Kernel Config Handoff — Instructions for ArchLinux-Claude

**From:** InterGenOS Build Claude
**To:** ArchLinux Claude (HP Laptop 14-dq1xxx)
**Purpose:** Extract a hardware-proven kernel config from the running Arch system and push it to the VPS for retrieval by the build system.

---

## What We Need

A kernel config from the running Arch installation on the HP laptop that:
1. Has every driver needed for this specific hardware compiled (either built-in or as module)
2. Is trimmed to only the modules actually in use (no unnecessary drivers)
3. Is pushed to the VPS where the build system can retrieve it

---

## Step-by-Step Instructions

### Step 1: Extract the running kernel config

```bash
zcat /proc/config.gz > /tmp/intergenos-hp-laptop.config
```

### Step 2: Trim to only loaded modules (optional but recommended)

This removes drivers for hardware not present on this machine, producing a leaner config:

```bash
# Download the InterGenOS kernel source version (6.18.10)
# Or use the Arch kernel source if version is close enough
cd /tmp
curl -LO https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.18.10.tar.xz
tar -xf linux-6.18.10.tar.xz
cd linux-6.18.10

# Copy the running config as base
cp /tmp/intergenos-hp-laptop.config .config

# Trim to only currently loaded modules
make LSMOD=/tmp/lsmod.txt localmodconfig
# When prompted, answer 'm' for modules you're unsure about

# Save the trimmed config
cp .config /tmp/intergenos-hp-laptop-trimmed.config
```

If step 2 is too involved, step 1 alone is sufficient — we can trim later.

### Step 3: Capture hardware inventory

```bash
# PCI devices (drivers and IDs)
lspci -nnk > /tmp/hp-laptop-lspci.txt

# Loaded modules
lsmod | sort > /tmp/hp-laptop-lsmod.txt

# Firmware files in use
dmesg | grep -i firmware > /tmp/hp-laptop-firmware.txt

# CPU info
cat /proc/cpuinfo | head -30 > /tmp/hp-laptop-cpuinfo.txt
```

### Step 4: Push to VPS

**VPS SSH access:**
```
Host: origin.intergenstudios.com
Port: 2200
User: christopher
Sudo password: (check /home/christopher/Documents/c-vps-pe.txt on the laptop or ask the owner)
```

**Upload the files:**
```bash
scp -P 2200 \
    /tmp/intergenos-hp-laptop.config \
    /tmp/hp-laptop-lspci.txt \
    /tmp/hp-laptop-lsmod.txt \
    /tmp/hp-laptop-firmware.txt \
    /tmp/hp-laptop-cpuinfo.txt \
    christopher@origin.intergenstudios.com:/home/christopher/intergenos-kernel/
```

**If the directory doesn't exist:**
```bash
ssh -p 2200 christopher@origin.intergenstudios.com "mkdir -p /home/christopher/intergenos-kernel"
```

If the trimmed config was generated, also upload:
```bash
scp -P 2200 /tmp/intergenos-hp-laptop-trimmed.config \
    christopher@origin.intergenstudios.com:/home/christopher/intergenos-kernel/
```

### Step 5: Verify upload

```bash
ssh -p 2200 christopher@origin.intergenstudios.com "ls -la /home/christopher/intergenos-kernel/"
```

### Step 6: Notify via session endpoint (optional)

```bash
curl -X POST "https://intergenstudios.com/intergenos/sessions.php" \
    -d "key=5fa686d359dc87496f7f3f262ec18e3b31ebefd3000c7035ad02d6494e612bf1" \
    --data-urlencode "entry=[linux-x86_64 | $(date -u '+%Y-%m-%dT%H:%MZ') | arch-hp-laptop] Kernel config and hardware inventory uploaded to VPS at /home/christopher/intergenos-kernel/"
```

---

## Files We Expect to Find on the VPS

| File | Purpose |
|------|---------|
| `intergenos-hp-laptop.config` | Full running kernel config (required) |
| `intergenos-hp-laptop-trimmed.config` | Trimmed to loaded modules (preferred) |
| `hp-laptop-lspci.txt` | PCI device list with drivers and IDs |
| `hp-laptop-lsmod.txt` | Currently loaded kernel modules |
| `hp-laptop-firmware.txt` | Firmware files loaded at boot |
| `hp-laptop-cpuinfo.txt` | CPU identification |

---

## What Happens Next

The InterGenOS build system will:
1. Pull these files from the VPS
2. Merge the laptop config with our existing VM config (or use as a separate hardware profile)
3. Rebuild the kernel targeting the HP laptop hardware
4. Create a bootable USB image
5. The owner boots the laptop from USB and validates everything works

---

## Important Notes

- The Arch kernel version doesn't need to match ours exactly (6.18.10). Config symbols are stable across minor versions.
- If `localmodconfig` asks about unfamiliar options, default to `m` (module) — it's safer than `n`.
- Don't worry about optimizing — we'll do final tuning once we have a booting system.
