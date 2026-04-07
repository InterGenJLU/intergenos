# InterGenOS Kernel Config — HP Laptop Target Hardware Briefing

**Context:** This message is from the Arch Linux machine that InterGenOS will be installed on. I have read the InterGenOS repo and identified specific kernel config changes needed for this hardware. Please apply these changes to the repo.

**Repo:** `git@github.com:InterGenJLU/intergenos.git`

---

## Target Machine: HP Laptop 14-dq1xxx

```
CPU:     Intel Core i5-1035G1 (Ice Lake, 10th gen, 4C/8T, x86_64)
RAM:     16 GB
Storage: 256 GB SK Hynix BC511 NVMe SSD  [PCI 1c5c:1339]
GPU:     Intel Iris Plus G1 (Ice Lake)    [PCI 8086:8a56]
WiFi:    Realtek RTL8821CE 802.11ac PCIe  [PCI 10ec:c821]
Audio:   Intel Ice Lake-LP SST            [PCI 8086:34c8]
USB:     Intel Ice Lake-LP xHCI 3.1       [PCI 8086:34ed]
I2C:     Intel Ice Lake-LP Serial IO I2C  [PCI 8086:34e8, 8086:34e9]
Boot:    UEFI, GPT, Secure Boot disabled
```

---

## Required Changes — 4 Issues Identified

---

### Issue 1 — Audio will not work at all (Critical)

Ice Lake's audio controller `[8086:34c8]` is SST-based and requires the SOF (Sound Open Firmware) kernel stack. The current `config/kernel/fragments/24-sound.config` only has HDA drivers, which will not bind to this device.

Add the following to `config/kernel/fragments/24-sound.config`:

```
# Intel SOF (Sound Open Firmware) — required for Ice Lake and newer Intel laptops
# HDA alone will NOT work on Ice Lake [8086:34c8]
CONFIG_SND_SOC_CORE=m
CONFIG_SND_SOC_ACPI=m
CONFIG_SND_SOC_ACPI_INTEL_MATCH=m
CONFIG_SND_HDA_EXT_CORE=m
CONFIG_SND_INTEL_DSPCFG=m
CONFIG_SND_INTEL_SDW_ACPI=m
CONFIG_SND_SOF=m
CONFIG_SND_SOF_PCI=m
CONFIG_SND_SOF_INTEL_ICL=m
```

Also, the SOF firmware blobs must be present in the image. These come from the upstream `linux-firmware` git repo. The specific files needed for Ice Lake are:

```
/lib/firmware/intel/sof/sof-icl.ri
/lib/firmware/intel/sof-tplg/  (directory — include all tplg files)
```

Upstream source: `https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git`
Path in that repo: `intel/sof/` and `intel/sof-tplg/`

---

### Issue 2 — WiFi chip variant never loaded (Critical)

The current `config/kernel/fragments/20-network.config` has `CONFIG_RTW88=m` and `CONFIG_RTW88_PCI=m`, but these are the framework modules only. The RTL8821CE chip `[10ec:c821]` requires its specific variant modules to be compiled. Without them the chip is detected but no driver binds.

Add the following to `config/kernel/fragments/20-network.config`:

```
# RTW88 chip-specific variants — RTW88/PCI alone is not enough
# RTL8821CE [10ec:c821] requires all four of these
CONFIG_RTW88_CORE=m
CONFIG_RTW88_8821C=m
CONFIG_RTW88_8821CE=m
```

Also ensure this firmware blob is in the image:

```
/lib/firmware/rtw88/rtw8821c_fw.bin
```

Source: same `linux-firmware` git repo, path `rtw88/rtw8821c_fw.bin`

---

### Issue 3 — Intel GPU not force-probed (Important)

The current `config/kernel/fragments/21-gpu.config` does not set `CONFIG_DRM_I915_FORCE_PROBE`, which means the merged config gets the default empty string `""`. The Ice Lake GPU `[8086:8a56]` requires this to be set to `"*"` or it may not be probed by the i915 driver.

Add to `config/kernel/fragments/21-gpu.config`:

```
# Force i915 to probe all supported devices including newer Ice Lake variants
CONFIG_DRM_I915_FORCE_PROBE="*"
```

Also add for HDMI audio passthrough from GPU:

```
CONFIG_SND_HDA_I915=y
```

And add the Ice Lake-specific firmware blobs to the image:

```
/lib/firmware/i915/icl_dmc_ver1_09.bin
/lib/firmware/i915/icl_guc_70.1.1.bin
/lib/firmware/i915/icl_huc_9.0.0.bin
```

---

### Issue 4 — Touchpad bus disabled (Important)

The touchpad on this laptop is connected via Intel LPSS I2C (not PS/2). The current kernel config has `CONFIG_X86_INTEL_LPSS` explicitly disabled, which means the I2C bus controllers are never enumerated and the touchpad is dead. The PS/2 Elan/Synaptics entries in `config/kernel/fragments/23-input.config` will not help — this is I2C HID, not PS/2.

Create a new file `config/kernel/fragments/26-intel-lpss.config`:

```
# Intel Low Power Subsystem — I2C/SPI/UART controllers on modern Intel laptops
# Required for touchpad, fingerprint reader, and other ACPI I2C devices
# Disabled by default; Ice Lake LP has two I2C controllers [8086:34e8, 8086:34e9]
CONFIG_X86_INTEL_LPSS=y
CONFIG_MFD_INTEL_LPSS=m
CONFIG_MFD_INTEL_LPSS_ACPI=m
CONFIG_MFD_INTEL_LPSS_PCI=m
CONFIG_PINCTRL_INTEL=y
CONFIG_I2C_DESIGNWARE_CORE=m
CONFIG_I2C_DESIGNWARE_PLATFORM=m
CONFIG_I2C_HID_ACPI=m
```

---

## Summary of File Changes

| File | Action |
|---|---|
| `config/kernel/fragments/24-sound.config` | Add SOF stack (9 lines) |
| `config/kernel/fragments/20-network.config` | Add RTW88 chip variants (3 lines) |
| `config/kernel/fragments/21-gpu.config` | Add force probe + HDA I915 (2 lines) |
| `config/kernel/fragments/26-intel-lpss.config` | Create new file (9 lines) |

After making these changes, re-run `scripts/merge-kernel-config.sh` against the kernel source tree to regenerate `config/kernel/intergenos.config`, then verify with:

```bash
grep -E "RTW88_8821CE|SND_SOF_INTEL_ICL|DRM_I915_FORCE_PROBE|MFD_INTEL_LPSS_ACPI" config/kernel/intergenos.config
```

All four should appear and be set (not `# ... is not set`).

---

*Generated from hardware analysis of the target HP Laptop 14-dq1xxx running Arch Linux kernel 6.19.11. The InterGenOS kernel targets 6.18.10 — all of the above options exist in that version.*
