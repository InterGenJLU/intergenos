#!/usr/bin/env python3
"""
Kernel Config Convergence Analysis
===================================
Compares kernel configs from Ubuntu, Arch, Fedora, Debian, and openSUSE
to find universal agreement on options — building an empirical baseline
for InterGenOS kernel configuration.

Note: Arch config is a trimmed laptop config (smaller than others).
"""

import re
import sys
from collections import defaultdict, Counter
from pathlib import Path

CONFIG_DIR = Path("/home/christopher/intergenos/research/kernel_configs")

# ── Subsystem classification ──────────────────────────────────────────

SUBSYSTEM_RULES = [
    # (pattern, category) — first match wins
    (r"^CONFIG_(NET_|IP_|IPV6_|TCP_|UDP_|INET|NETFILTER|NF_|BRIDGE|VLAN|BONDING|TUN|TAP|VETH|MACVLAN)", "Networking"),
    (r"^CONFIG_(DRM_|FB_|FRAMEBUFFER|BACKLIGHT|VIDEO_)", "GPU/Display"),
    (r"^CONFIG_(SND_|SOUND)", "Audio"),
    (r"^CONFIG_(USB_|HID_|HID |INPUT_|KEYBOARD_|MOUSE_)", "USB/Input"),
    (r"^CONFIG_BT_", "Bluetooth"),
    (r"^CONFIG_(NVME|ATA_|ATA |SATA_|SCSI_|SCSI |BLK_DEV_SD|BLK_DEV_SR|BLK_DEV_NVME|MD_|DM_|RAID)", "Storage"),
    (r"^CONFIG_(EXT4|BTRFS|XFS|FAT|VFAT|NTFS|FUSE|OVERLAY|TMPFS|SQUASHFS|ISO9660|UDF|CIFS|NFS|F2FS|EROFS)", "Filesystems"),
    (r"^CONFIG_VIRTIO", "Virtualization"),
    (r"^CONFIG_(KVM|VHOST)", "Virtualization"),
    (r"^CONFIG_CRYPTO", "Crypto"),
    (r"^CONFIG_(SECURITY|SELINUX|APPARMOR|TOMOYO|SMACK|IMA_|EVM_|INTEGRITY|LSM|HARDENED|FORTIFY|STACKPROTECTOR|RANDOMIZE)", "Security"),
    (r"^CONFIG_(WIRELESS|CFG80211|MAC80211|WLAN|IEEE80211)", "WiFi"),
    (r"^CONFIG_(ACPI_|ACPI |PM_|SUSPEND|HIBERNAT|CPU_FREQ|CPU_IDLE|THERMAL)", "Power Management"),
    (r"^CONFIG_(CGROUP|NAMESPACES|PID_NS|NET_NS|USER_NS|UTS_NS|IPC_NS|MEMCG|CPUSETS)", "Containers/cgroups"),
    (r"^CONFIG_(I2C_|SPI_|GPIO_|PINCTRL|REGULATOR|MFD_|WATCHDOG|RTC_|HWMON)", "Hardware Monitoring"),
    (r"^CONFIG_(PRINTK|DEBUG|FTRACE|KPROBE|TRACING|DYNAMIC_DEBUG|BPF|PERF)", "Debug/Tracing"),
    (r"^CONFIG_(MODULES|MODULE_)", "Modules"),
    (r"^CONFIG_(SMP|NR_CPUS|NUMA|PREEMPT|HZ_|TICK_|NO_HZ|HIGH_RES)", "Scheduling/SMP"),
    (r"^CONFIG_(EFI|ACPI_BGRT|UEFI)", "EFI/Boot"),
]


def classify_option(name):
    """Classify a CONFIG_ option into a subsystem category."""
    for pattern, category in SUBSYSTEM_RULES:
        if re.match(pattern, name):
            return category
    return "Other"


# ── Config parsing ────────────────────────────────────────────────────

RE_SET = re.compile(r"^(CONFIG_\w+)=(y|m|.+)$")
RE_NOTSET = re.compile(r"^# (CONFIG_\w+) is not set$")

# Options that are compiler/toolchain-specific, not real kernel features
SKIP_PREFIXES = (
    "CONFIG_CC_",
    "CONFIG_GCC_",
    "CONFIG_CLANG_",
    "CONFIG_AS_IS_",
    "CONFIG_AS_VERSION",
    "CONFIG_LD_IS_",
    "CONFIG_LD_VERSION",
    "CONFIG_LLD_",
    "CONFIG_RUSTC_",
    "CONFIG_RUST_IS_",
    "CONFIG_PAHOLE_",
    "CONFIG_CC_CAN_",
    "CONFIG_CC_HAS_",
    "CONFIG_TOOLS_SUPPORT",
    "CONFIG_CC_VERSION_TEXT",
)


def parse_config(path):
    """Parse a kernel .config file into {option: value} dict."""
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("##"):
                continue

            m = RE_SET.match(line)
            if m:
                key, val = m.group(1), m.group(2)
                if any(key.startswith(p) for p in SKIP_PREFIXES):
                    continue
                # Normalize: y and m stay as-is, quoted strings and numbers → "other"
                if val not in ("y", "m"):
                    val = "other"  # numeric or string values
                config[key] = val
                continue

            m = RE_NOTSET.match(line)
            if m:
                key = m.group(1)
                if any(key.startswith(p) for p in SKIP_PREFIXES):
                    continue
                config[key] = "n"
    return config


def merge_debian(base_path, override_path):
    """Merge Debian base + arch-specific config (amd64 overrides base)."""
    base = parse_config(base_path)
    override = parse_config(override_path)
    base.update(override)
    return base


# ── Main analysis ─────────────────────────────────────────────────────

def main():
    print("Loading kernel configs...")

    distros = {}
    distros["Ubuntu"] = parse_config(CONFIG_DIR / "ubuntu.config")
    distros["Arch"] = parse_config(CONFIG_DIR / "arch.config")
    distros["Fedora"] = parse_config(CONFIG_DIR / "fedora.config")
    distros["Debian"] = merge_debian(CONFIG_DIR / "debian-base.config", CONFIG_DIR / "debian-amd64.config")
    distros["openSUSE"] = parse_config(CONFIG_DIR / "opensuse.config")

    for name, cfg in distros.items():
        print(f"  {name:10s}: {len(cfg):6d} options parsed")

    # Collect all unique option names
    all_options = set()
    for cfg in distros.values():
        all_options.update(cfg.keys())

    print(f"\n  Total unique options across all configs: {len(all_options)}")

    # ── Categorize each option ────────────────────────────────────────

    # For each option, collect the value from each distro (None if absent)
    distro_names = list(distros.keys())

    unanimous_builtin = []      # 5/5 =y
    unanimous_enabled = []      # 5/5 =y or =m (but not all =y, those are in builtin)
    near_unanimous_enabled = [] # 4/5 enabled, with the dissenter noted
    unanimous_disabled = []     # 5/5 =n

    for opt in sorted(all_options):
        values = {}
        for dname in distro_names:
            values[dname] = distros[dname].get(opt)  # None = not mentioned

        # Count
        present = {d: v for d, v in values.items() if v is not None}

        # Skip options with "other" (numeric/string) — they're not y/m/n toggles
        if any(v == "other" for v in present.values()):
            continue

        enabled_count = sum(1 for v in values.values() if v in ("y", "m"))
        builtin_count = sum(1 for v in values.values() if v == "y")
        disabled_count = sum(1 for v in values.values() if v == "n")
        # "not mentioned" = effectively absent; we treat it as absent, not disabled
        mentioned_count = sum(1 for v in values.values() if v is not None)

        # Unanimous built-in: all 5 say =y
        if builtin_count == 5:
            unanimous_builtin.append(opt)
            continue

        # Unanimous enabled: all 5 say y or m
        if enabled_count == 5:
            unanimous_enabled.append(opt)
            continue

        # Near-unanimous enabled: 4/5 say y or m
        if enabled_count == 4:
            # Find the dissenter
            dissenters = []
            for d, v in values.items():
                if v not in ("y", "m"):
                    dissenters.append((d, v))
            near_unanimous_enabled.append((opt, dissenters))
            continue

        # Unanimous disabled: all 5 explicitly say "not set"
        if disabled_count == 5:
            unanimous_disabled.append(opt)
            continue

    print(f"\n  5/5 unanimous built-in (=y):  {len(unanimous_builtin)}")
    print(f"  5/5 unanimous enabled (y|m):  {len(unanimous_enabled)} (excludes the above)")
    print(f"  4/5 near-unanimous enabled:   {len(near_unanimous_enabled)}")
    print(f"  5/5 unanimous disabled:       {len(unanimous_disabled)}")

    # ── Group by subsystem ────────────────────────────────────────────

    def group_by_subsystem(options):
        groups = defaultdict(list)
        for opt in options:
            # Handle tuples (near-unanimous with dissenter info)
            if isinstance(opt, tuple):
                name = opt[0]
            else:
                name = opt
            groups[classify_option(name)].append(opt)
        # Sort groups by size descending
        return dict(sorted(groups.items(), key=lambda x: -len(x[1])))

    builtin_groups = group_by_subsystem(unanimous_builtin)
    enabled_groups = group_by_subsystem(unanimous_enabled)
    near_groups = group_by_subsystem(near_unanimous_enabled)
    disabled_groups = group_by_subsystem(unanimous_disabled)

    # ── Generate Markdown report ──────────────────────────────────────

    md = []
    md.append("# Kernel Config Convergence Analysis")
    md.append("")
    md.append("**Generated:** 2026-04-06")
    md.append("**Distros compared:** Ubuntu, Arch (trimmed laptop), Fedora, Debian, openSUSE")
    md.append("")
    md.append("## Methodology")
    md.append("")
    md.append("Parsed CONFIG_* lines from 5 distro kernel configs. Debian = base + amd64 overlay.")
    md.append("Arch is a trimmed laptop config — smaller than others, so options absent from Arch")
    md.append("may still be widely supported. Compiler/toolchain-specific options (CONFIG_CC_*,")
    md.append("CONFIG_GCC_*, etc.) are excluded. Numeric/string-valued options are excluded from")
    md.append("the toggle analysis.")
    md.append("")

    md.append("## Summary Statistics")
    md.append("")
    md.append(f"| Metric | Count |")
    md.append(f"|--------|-------|")
    md.append(f"| Total unique options | {len(all_options)} |")
    for name, cfg in distros.items():
        md.append(f"| {name} options | {len(cfg)} |")
    md.append(f"| 5/5 unanimous built-in (=y) | {len(unanimous_builtin)} |")
    md.append(f"| 5/5 unanimous enabled (y or m) | {len(unanimous_enabled)} |")
    md.append(f"| 4/5 near-unanimous enabled | {len(near_unanimous_enabled)} |")
    md.append(f"| 5/5 unanimous disabled | {len(unanimous_disabled)} |")
    md.append("")

    # ── Section: Unanimous Built-in ───────────────────────────────────

    md.append("---")
    md.append("")
    md.append("## MUST HAVE: Unanimous Built-in (5/5 =y)")
    md.append("")
    md.append("These options are built into the kernel (not modules) by every distro.")
    md.append("They represent the absolute core of a desktop Linux kernel.")
    md.append("")

    for category, opts in builtin_groups.items():
        md.append(f"### {category} ({len(opts)} options)")
        md.append("")
        # For large categories, just list them compactly
        if len(opts) > 30:
            md.append(f"<details><summary>Click to expand ({len(opts)} options)</summary>")
            md.append("")
            md.append("```")
            for o in sorted(opts):
                md.append(f"{o}=y")
            md.append("```")
            md.append("</details>")
        else:
            md.append("```")
            for o in sorted(opts):
                md.append(f"{o}=y")
            md.append("```")
        md.append("")

    # ── Section: Unanimous Enabled ────────────────────────────────────

    md.append("---")
    md.append("")
    md.append("## SHOULD HAVE: Unanimous Enabled (5/5 y or m)")
    md.append("")
    md.append("All 5 distros enable these (some as built-in, some as modules).")
    md.append("Values shown are the majority choice.")
    md.append("")

    for category, opts in enabled_groups.items():
        md.append(f"### {category} ({len(opts)} options)")
        md.append("")
        if len(opts) > 30:
            md.append(f"<details><summary>Click to expand ({len(opts)} options)</summary>")
            md.append("")
            md.append("```")
            for o in sorted(opts):
                # Show majority value
                vals = [distros[d].get(o) for d in distro_names]
                y_count = vals.count("y")
                m_count = vals.count("m")
                val = "y" if y_count >= m_count else "m"
                md.append(f"{o}={val}")
            md.append("```")
            md.append("</details>")
        else:
            md.append("```")
            for o in sorted(opts):
                vals = [distros[d].get(o) for d in distro_names]
                y_count = vals.count("y")
                m_count = vals.count("m")
                val = "y" if y_count >= m_count else "m"
                md.append(f"{o}={val}")
            md.append("```")
        md.append("")

    # ── Section: Near-Unanimous Enabled ───────────────────────────────

    md.append("---")
    md.append("")
    md.append("## RECOMMENDED: Near-Unanimous Enabled (4/5)")
    md.append("")
    md.append("4 out of 5 distros enable these. The dissenting distro is noted.")
    md.append("")

    for category, opts in near_groups.items():
        md.append(f"### {category} ({len(opts)} options)")
        md.append("")
        if len(opts) > 40:
            md.append(f"<details><summary>Click to expand ({len(opts)} options)</summary>")
            md.append("")
            md.append("| Option | Dissenter |")
            md.append("|--------|-----------|")
            for o, dissenters in sorted(opts, key=lambda x: x[0]):
                d_str = ", ".join(f"{d} ({v if v else 'absent'})" for d, v in dissenters)
                md.append(f"| `{o}` | {d_str} |")
            md.append("")
            md.append("</details>")
        else:
            md.append("| Option | Dissenter |")
            md.append("|--------|-----------|")
            for o, dissenters in sorted(opts, key=lambda x: x[0]):
                d_str = ", ".join(f"{d} ({v if v else 'absent'})" for d, v in dissenters)
                md.append(f"| `{o}` | {d_str} |")
        md.append("")

    # ── Section: Unanimous Disabled ───────────────────────────────────

    md.append("---")
    md.append("")
    md.append("## SAFELY DISABLED: Unanimous Disabled (5/5 not set)")
    md.append("")
    md.append("All 5 distros explicitly disable these. Safe to leave out.")
    md.append("")

    for category, opts in disabled_groups.items():
        count = len(opts)
        md.append(f"### {category} ({count} options)")
        md.append("")
        if count > 30:
            md.append(f"<details><summary>Click to expand ({count} options)</summary>")
            md.append("")
            md.append("```")
            for o in sorted(opts):
                md.append(f"# {o} is not set")
            md.append("```")
            md.append("</details>")
        else:
            md.append("```")
            for o in sorted(opts):
                md.append(f"# {o} is not set")
            md.append("```")
        md.append("")

    # ── Section: Coverage by subsystem ────────────────────────────────

    md.append("---")
    md.append("")
    md.append("## Coverage Summary by Subsystem")
    md.append("")
    md.append("| Subsystem | Must Have (5/5 y) | Should Have (5/5 y|m) | Recommended (4/5) | Safely Disabled (5/5 n) |")
    md.append("|-----------|------------------:|---------------------:|------------------:|------------------------:|")

    all_categories = set()
    all_categories.update(builtin_groups.keys())
    all_categories.update(enabled_groups.keys())
    all_categories.update(near_groups.keys())
    all_categories.update(disabled_groups.keys())

    for cat in sorted(all_categories):
        b = len(builtin_groups.get(cat, []))
        e = len(enabled_groups.get(cat, []))
        n = len(near_groups.get(cat, []))
        d = len(disabled_groups.get(cat, []))
        md.append(f"| {cat} | {b} | {e} | {n} | {d} |")

    md.append("")

    # Write markdown
    md_path = CONFIG_DIR / "convergence_analysis.md"
    md_path.write_text("\n".join(md) + "\n")
    print(f"\nWrote analysis to: {md_path}")

    # ── Generate baseline config fragment ─────────────────────────────

    baseline = []
    baseline.append("#")
    baseline.append("# InterGenOS Universal Baseline Config Fragment")
    baseline.append("# Generated: 2026-04-06")
    baseline.append("#")
    baseline.append("# Options where 4+ out of 5 major distros agree on enabling.")
    baseline.append("# Source: Ubuntu, Arch, Fedora, Debian, openSUSE kernel configs.")
    baseline.append("#")
    baseline.append("# Arch is a trimmed laptop config — its absence does NOT mean")
    baseline.append("# an option is unimportant.")
    baseline.append("#")
    baseline.append("")

    # Collect all 5/5 and 4/5 enabled options with their recommended value
    baseline_options = {}

    for opt in unanimous_builtin:
        baseline_options[opt] = "y"

    for opt in unanimous_enabled:
        vals = [distros[d].get(opt) for d in distro_names]
        y_count = vals.count("y")
        m_count = vals.count("m")
        baseline_options[opt] = "y" if y_count >= m_count else "m"

    for opt, dissenters in near_unanimous_enabled:
        # Use majority value from the 4 who agree
        vals = [distros[d].get(opt) for d in distro_names if distros[d].get(opt) in ("y", "m")]
        y_count = vals.count("y")
        m_count = vals.count("m")
        baseline_options[opt] = "y" if y_count >= m_count else "m"

    # Group by subsystem for the config file too
    cfg_groups = defaultdict(list)
    for opt in sorted(baseline_options.keys()):
        cat = classify_option(opt)
        cfg_groups[cat].append((opt, baseline_options[opt]))

    for cat in sorted(cfg_groups.keys()):
        baseline.append(f"#")
        baseline.append(f"# ── {cat}")
        baseline.append(f"#")
        for opt, val in sorted(cfg_groups[cat]):
            baseline.append(f"{opt}={val}")
        baseline.append("")

    cfg_path = CONFIG_DIR / "universal-baseline.config"
    cfg_path.write_text("\n".join(baseline) + "\n")
    print(f"Wrote baseline config to: {cfg_path}")
    print(f"  Baseline options: {len(baseline_options)}")

    # ── Print key highlights ──────────────────────────────────────────

    print("\n" + "=" * 70)
    print("KEY FINDINGS")
    print("=" * 70)

    print("\n── Desktop-Critical Unanimous Built-in (all 5 =y) ──")
    desktop_critical = [
        "Security", "Scheduling/SMP", "EFI/Boot", "Modules",
        "Power Management", "Containers/cgroups",
    ]
    for cat in desktop_critical:
        if cat in builtin_groups:
            opts = builtin_groups[cat]
            print(f"\n  {cat} ({len(opts)}):")
            for o in sorted(opts)[:15]:
                print(f"    {o}=y")
            if len(opts) > 15:
                print(f"    ... and {len(opts) - 15} more")

    print("\n── Desktop Hardware Unanimous Enabled (all 5) ──")
    hw_cats = ["GPU/Display", "Audio", "USB/Input", "WiFi", "Bluetooth", "Storage", "Filesystems"]
    for cat in hw_cats:
        total = len(builtin_groups.get(cat, [])) + len(enabled_groups.get(cat, []))
        if total > 0:
            print(f"\n  {cat}: {total} options unanimously enabled")
            # Show a few highlights
            highlights = sorted(builtin_groups.get(cat, []))[:5] + sorted(enabled_groups.get(cat, []))[:5]
            for o in highlights:
                v = "y" if o in unanimous_builtin else baseline_options.get(o, "?")
                print(f"    {o}={v}")
            remaining = total - len(highlights)
            if remaining > 0:
                print(f"    ... and {remaining} more")

    print("\n── Arch Dissent Analysis ──")
    arch_dissent = sum(1 for _, dis in near_unanimous_enabled if any(d == "Arch" for d, _ in dis))
    non_arch_dissent = len(near_unanimous_enabled) - arch_dissent
    print(f"  Arch is the lone dissenter in {arch_dissent} of {len(near_unanimous_enabled)} near-unanimous options")
    print(f"  Other distros dissent in {non_arch_dissent} cases")
    print(f"  (Arch is a trimmed laptop config — many absences are size, not disagreement)")

    # Break down dissent by distro
    dissent_by_distro = Counter()
    for _, dissenters in near_unanimous_enabled:
        for d, v in dissenters:
            dissent_by_distro[d] += 1
    print(f"\n  Dissent counts:")
    for d, count in dissent_by_distro.most_common():
        print(f"    {d}: {count}")

    print("\n── Virtualization ──")
    virt_all = sorted(builtin_groups.get("Virtualization", []) + enabled_groups.get("Virtualization", []))
    print(f"  Unanimously enabled: {len(virt_all)}")
    for o in virt_all[:10]:
        v = "y" if o in unanimous_builtin else baseline_options.get(o, "?")
        print(f"    {o}={v}")
    if len(virt_all) > 10:
        print(f"    ... and {len(virt_all) - 10} more")

    print()


if __name__ == "__main__":
    main()
