# Hardware Test Report — Lenovo ThinkCentre 3306G3U

**Date:** April 10, 2026  
**Boot method:** USB (SanDisk Ultra 233GB)  
**Result:** PARTIAL — boots to console, GUI crashes (libffi invalid opcode)

---

## System

| Field | Value |
|-------|-------|
| Vendor | Lenovo |
| Model | ThinkCentre 3306G3U |
| Form factor | Desktop workstation |
| Boot mode | UEFI |

## CPU

| Field | Value |
|-------|-------|
| Model | Intel Core i5-3570 @ 3.40GHz |
| Architecture | Ivy Bridge (3rd gen, 2012) |
| Cores | 4 |
| x86-64 feature level | v2 (SSE4.2, POPCNT — but NO AVX2) |

## Memory

| Field | Value |
|-------|-------|
| Total | 8GB |
| Used at idle (console) | 365MB |

## GPU

| Field | Value |
|-------|-------|
| Device | Intel Xeon E3-1200 v2/3rd Gen Core [HD Graphics 2500] |
| Driver | i915 (loaded, Ivy Bridge detected) |
| Framebuffer | Working (efifb → i915drmfb) |
| Display outputs | VGA, 2x DP, 2x HDMI |

## Storage

| Device | Size | Type | Model |
|--------|------|------|-------|
| sda | 465.8GB | Internal HDD | Seagate ST500DM002 |
| sdb | 233.3GB | USB | SanDisk Ultra (InterGenOS) |
| sr0 | — | DVD-RW | PLDS DVD-RW DH16ACSH |

## Network

| Interface | Hardware | Status |
|-----------|----------|--------|
| eno1 | Intel 82579LM Gigabit | **Connected, working** |

## Audio

| Field | Value |
|-------|-------|
| Controller | HDA Intel PCH |
| Status | Initialized |

## What Worked

- [x] Boot from USB (UEFI)
- [x] GRUB bootloader
- [x] Kernel boot
- [x] Console login (multi-user.target)
- [x] i915 GPU driver loaded
- [x] Ethernet (Intel 82579LM)
- [x] Audio detection
- [x] SSH access
- [x] All console commands

## What FAILED

- [ ] **GNOME Shell crashes immediately** — `trap invalid opcode in libffi.so.8.2.0`
- [ ] GDM shows black screen with cursor, gnome-shell crash-loops

## Root Cause

`libffi` (and likely other libraries) were compiled on a newer CPU with instructions that Ivy Bridge doesn't support (likely AVX2 or newer SSE extensions). The `invalid opcode` trap means the binary contains CPU instructions this processor cannot execute.

**This is a build system issue, not a hardware issue.** The fix is to ensure all packages are built with `-march=x86-64` (generic baseline) or `-march=x86-64-v2` (which Ivy Bridge supports).

## Impact

This affects **all hardware older than the build machine's CPU**. Any x86-64 system without the specific instruction extensions used during compilation will hit the same crash. This is a critical decision for the distribution: what is the minimum CPU target?

## x86-64 Feature Levels

| Level | CPUs | Key instructions |
|-------|------|-----------------|
| x86-64 (baseline) | All 64-bit x86 | SSE, SSE2 |
| x86-64-v2 | 2009+ (Nehalem, Bulldozer) | SSE3, SSE4.1, SSE4.2, POPCNT |
| x86-64-v3 | 2013+ (Haswell, Zen) | AVX, AVX2, FMA3, BMI1/2 |
| x86-64-v4 | 2017+ (Skylake-X, Zen4) | AVX-512 |

**Ivy Bridge (this CPU) supports v2 but NOT v3.** If our build system targets v3 or higher, Ivy Bridge and older CPUs break.
