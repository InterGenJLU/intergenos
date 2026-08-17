#!/usr/bin/env python3
"""
Kernel Config Convergence Analysis
===================================
Compares kernel configs from Ubuntu, Arch, Fedora, Debian, and openSUSE
to find universal agreement on options — building an empirical baseline
for InterGenOS kernel configuration.

Note: Arch config is a trimmed laptop config (smaller than others).
"""

import os
import re
import sys
from collections import defaultdict, Counter
from pathlib import Path

CONFIG_DIR = Path("docs/research/kernel_configs")

# The shipped kernel config fragment. This file is GENERATED — it is written
# here and must not be hand-edited, because the next regeneration silently
# discards any hand edit. InterGenOS-specific choices and corrections belong in
# config/kernel/fragments/99-intergenos-overrides.config, which is concatenated
# after this one and therefore wins.
FRAGMENT_PATH = Path("config/kernel/fragments/00-universal-baseline.config")

# Kernel source used to classify each symbol as driver/hardware vs policy.
# Overridable with IGOS_KERNEL_SRC. There is deliberately no silent fallback:
# an unavailable tree is a hard failure, not a guess (see build_symbol_tree_map).
DEFAULT_KERNEL_SRC = "/usr/src/linux-6.18.10"

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


# ── Hardware-vs-policy split (the derivation rule) ────────────────────
#
# Decided 2026-08-07. The baseline used to be derived with ONE rule for every
# option: enable it when at least 4 of the 5 surveyed distros enable it.
# That rule is correct for POLICY, and wrong for DRIVERS.
#
# WHY IT IS WRONG FOR DRIVERS. A threshold rule can never exceed what its
# leanest inputs carry. Two of the five inputs are systematically lean — the
# Arch config is a trimmed laptop config and the Debian config is a split
# base+arch pair — so any driver those two both omit is dropped even when
# Ubuntu, Fedora and openSUSE all ship it. Measured on this corpus: 1,493
# driver options that all three of those ship were absent from our kernel.
# Coverage is a UNION question, not an agreement question: if any mainstream
# distro ships a driver, some user owns that hardware, and a kernel that omits
# it does not fail loudly — the device is simply, silently, not there.
#
# WHY IT IS RIGHT FOR POLICY. Security and behaviour options are exactly where
# broad agreement IS the signal, and where taking the union of everything
# anyone enables would import other projects' choices wholesale. Those keep
# the agreement threshold.
#
# The split is made on where the symbol is DEFINED in the kernel source, read
# from the Kconfig files themselves — not on the symbol's name, which is not a
# reliable guide (network interface drivers, for one, share no common prefix).
HARDWARE_TREES = ("drivers", "sound")

# The architecture this distribution is built for. Only this arch/ subtree is
# read when classifying symbols — see build_symbol_tree_map for why.
TARGET_ARCH = "x86"

# The general-purpose distros that aim at broad hardware coverage. The union is
# taken across these. Arch (trimmed laptop config) and Debian (split base+arch
# config) are deliberately NOT union inputs: both are lean for reasons that have
# nothing to do with whether hardware is worth supporting, and including them
# adds nothing a union can use while their omissions are what broke the old rule.
MAINSTREAM_DISTROS = ("Ubuntu", "Fedora", "openSUSE")

# Agreement threshold retained for policy/security options (of 5 distros).
POLICY_AGREEMENT_THRESHOLD = 4

# Kernel-developer test and fake-hardware drivers.
#
# Decided 2026-08-07: these are STRIPPED from the baseline regardless of how
# many distros ship them. They add no hardware coverage — they exist to test the
# kernel, and several of them synthesise fake devices (a fake SCSI disk, a fake
# battery, a mock SoundWire codec). On a security-first OS a driver that
# fabricates a device the machine does not have is a masking primitive: it
# makes a system look like it has hardware it does not. Kernel self-testing, if
# ever wanted, is a deliberate build-lane decision and never resident on a user
# machine.
#
# Any symbol whose name contains KUNIT is a kernel unit-test module by
# construction. The rest are named explicitly, because there is no reliable
# name pattern for them and a loose pattern would strip real drivers — the
# Creative EMU10K1 sound chips, for instance, are real hardware whose name
# merely looks like "emulator".
# ⚠️ ENUMERATED PER SYMBOL, NEVER BY PATTERN. A pattern sweep over names
# containing DUMMY/TEST/STUB/FAKE matches 37 symbols in the produced config, and
# several of those are load-bearing REAL features: CONFIG_EFI_STUB is the UEFI
# boot stub, DUMMY_CONSOLE is the console placeholder the kernel needs before a
# real console binds, PCI_STUB and PCI_PF_STUB serve VFIO passthrough and SR-IOV,
# and CONFIG_DUMMY is the ordinary dummy0 network interface every distro ships.
# Stripping by pattern would have unbootable results. Each member below is here
# because it FABRICATES A DEVICE THE MACHINE DOES NOT HAVE.
TEST_CLASS_EXPLICIT = frozenset({
    # -- named in the decision that created this class --
    "SCSI_DEBUG",                      # fabricates a fake SCSI disk
    "SCSI_PROTO_TEST",                 # SCSI protocol unit test
    "TEST_POWER",                      # fabricates a fake battery/charger
    "USB_TEST",                        # USB gadget/host test driver
    "USB_LINK_LAYER_TEST",             # USB link-layer test driver
    "SND_SOC_SDW_MOCKUP",              # mock SoundWire codec
    "SND_SOC_CS_AMP_LIB_TEST",         # codec library unit test
    "SND_SOC_INTEL_AVS_MACH_I2S_TEST", # synthetic I2S test machine
    # -- found by sweeping the PRODUCED config and the BUILT MODULE LIST, which
    #    is the only place this class is visible; the fragment cannot show it --
    "SND_DUMMY",                       # registers a fake ALSA sound card;
                                       # see the note below for why this one
                                       # needed its own decision
    "GPIO_MOCKUP",                     # fabricates a GPIO controller
    "USB_DUMMY_HCD",                   # fabricates a USB host controller
    "IIO_SIMPLE_DUMMY",                # fabricates an IIO sensor
    "PTP_1588_CLOCK_MOCK",             # fabricates a PTP hardware clock
    "IEEE802154_FAKELB",               # fabricates a radio (loopback)
    "VME_FAKE",                        # fabricates a VME bridge
    "DVB_DUMMY_FE",                    # fabricates a DVB tuner frontend
    "ATM_DUMMY",                       # fabricates an ATM adapter
    "DUMMY_IRQ",                       # fabricates an interrupt source
    "STM_DUMMY",                       # fabricates an STM trace sink
    "PPS_GENERATOR_DUMMY",             # fabricates a PPS generator
    "SPEAKUP_SYNTH_DUMMY",             # fabricates a speech synthesiser
    "I2C_STUB",                        # fabricates an I2C chip
    "I2C_SLAVE_TESTUNIT",              # I2C slave test unit
    "SPI_LOOPBACK_TEST",               # SPI loopback test driver
    "PCI_ENDPOINT_TEST",               # PCI endpoint test driver
    "PCI_EPF_TEST",                    # PCI endpoint test function
    "EFI_TEST",                        # EFI runtime test driver (NOT EFI_STUB)
    "THERMAL_CORE_TESTING",            # synthetic thermal zones
    "MTD_TESTS",                       # MTD test modules
    "COMEDI_TEST",                     # fabricates a comedi DAQ device
    "COMEDI_TESTS",                    # comedi unit-test modules
    "COMEDI_TESTS_EXAMPLE",
    "COMEDI_TESTS_NI_ROUTES",
    # -- found by sweeping the BUILT MODULE LIST of a real kernel build with a
    #    wider name vocabulary than the first sweep used. Every one of these was
    #    compiled into the proof kernel while the config-level check reported
    #    the class clean, because the config check can only look for names
    #    somebody already thought of --
    "BLK_DEV_NULL_BLK",                # "Null test block driver": fabricates block devices
    "SND_PCMTEST",                     # "Virtual PCM test driver": fabricates an ALSA PCM device
    "VDPA_SIM",                        # "vDPA device simulator core", under the kernel's own testing menu
    "VDPA_SIM_NET",                    # simulated vDPA network device (loops TX back to RX)
    "VDPA_SIM_BLOCK",                  # simulated vDPA block device. NOTE the name: the
                                       # symbol is VDPA_SIM_BLOCK, the module is
                                       # vdpa_sim_blk.ko. Reading the symbol off the module
                                       # filename gives VDPA_SIM_BLK, which does not exist —
                                       # that mistake was made here and caught by sweeping
                                       # the built module list.
    "RC_LOOPBACK",                     # fabricates an infrared remote: "mostly useful for debugging"
    "NFC_VIRTUAL_NCI",                 # "NCI device simulator driver": fabricates an NFC device
    "REGULATOR_VIRTUAL_CONSUMER",      # virtual regulator consumer, "mainly useful for test purposes"
})

# Deliberately NOT stripped, recorded so the decision is not re-litigated and so
# nobody "tidies" them into the set above:
#   EFI_STUB          the UEFI boot stub — removing it makes the kernel unbootable
#   DUMMY_CONSOLE     the console placeholder before a real console binds
#   PCI_STUB          real: VFIO device passthrough
#   PCI_PF_STUB       real: SR-IOV physical-function driver
#   XEN_PCI_STUB      real: Xen passthrough
#   DUMMY             real: the dummy0 network interface
#   SND_SEQ_DUMMY     real: ALSA sequencer loopback ports, not a fake card
#   MEDIA_TEST_SUPPORT / V4L_TEST_DRIVERS / RUNTIME_TESTING_MENU
#                     Kconfig MENU gates, not drivers
#   SND_SEQ_MIDI_EMUL real: a MIDI emulation LIBRARY that real wavetable synth
#                     hardware drivers select (SND_SYNTH_EMUX); it has no prompt
#                     and registers no device of its own
#   GREYBUS_LOOPBACK  real: a Greybus CLASS driver — it binds a loopback module
#                     that is physically present on the bus rather than
#                     fabricating one. (Its in-tree help text is an upstream
#                     copy-paste of the Debug Log class and describes the wrong
#                     module; read the driver, not the help.)
#   VIRT_WIFI         real, and a JUDGEMENT CALL worth stating: it makes an
#                     EXISTING ethernet link appear as wifi, and it registers a
#                     wiphy — which the hardware smoke check counts. It is not
#                     in the class because it fabricates nothing on its own: a
#                     user must explicitly create the device over a real link,
#                     exactly like the dummy0 network interface above. If that
#                     judgement is ever reversed, this is the line to change.
#
# ⚠️ DDBRIDGE_DUMMY_FE WAS REMOVED FROM THE SET ABOVE, 2026-08-07, and the
# reason matters more than the entry did. It is NOT a Kconfig symbol in this
# kernel — nothing declares it — so listing it stripped nothing while reading
# like coverage. The real module is built unconditionally by
# `obj-$(CONFIG_DVB_DDBRIDGE) += ddbridge.o ddbridge-dummy-fe.o`: it is a
# component of a real DVB card driver, exporting an attach function the card
# driver uses for inputs that have no demodulator. No config value can remove
# it, and removing the card driver to satisfy a name check would be disabling a
# real feature. A strip list entry that names a symbol the kernel does not have
# is worse than no entry, because it answers the coverage question falsely.
#
# ── CONFIG_SND_DUMMY: why it is in the set above ─────────────────────────────
# Decided 2026-08-07, as its own decision rather than by omission, because it is
# the only member whose removal a user could conceivably notice.
#
# SND_DUMMY registers a fake ALSA sound CARD. That makes it the sharpest case of
# what this whole class does wrong here: this repository's hardware smoke check
# tests audio by counting registered cards, so a fabricated card reads as
# working audio on a machine whose codec is dead — the exact silent-masking
# failure the check exists to catch. Disabling it costs nothing that ships: no
# InterGenOS feature consumes a virtual dummy card, and a user who genuinely
# wants one is making the deliberate build-lane choice this class reserves.
#
# The recovery path if that judgement is ever reversed is 99-intergenos-overrides.config.


def is_test_class(symbol):
    """True if the symbol is a kernel-developer test / fake-hardware driver.

    `symbol` is the bare name without the CONFIG_ prefix.
    """
    return "KUNIT" in symbol or symbol in TEST_CLASS_EXPLICIT


RE_KCONFIG_SYMBOL = re.compile(r"^\s*(?:menu)?config\s+([A-Za-z0-9_]+)\s*$")
RE_KCONFIG_TYPE = re.compile(r"^\s*(bool|tristate|string|int|hex)\b")


def build_symbol_tree_map(kernel_src):
    """Map every Kconfig symbol to (defining top-level tree, declared type).

    Returns {SYMBOL: (tree, type)} where tree is "drivers"|"sound"|"security"|…
    and type is "bool"|"tristate"|… (empty string when the declaration carries
    no type line). Read from the kernel source's own Kconfig files, so the
    classification tracks the kernel rather than a hand-maintained list that
    would rot.

    The TYPE matters: a symbol that is `bool` in this kernel accepts only y or n.
    Handing it =m is not an error the build stops on — `olddefconfig` prints a
    warning and silently turns the symbol OFF. Three symbols hit exactly this on
    the first run of the union rule (ANDROID_BINDERFS, ANDROID_BINDER_IPC and
    HYPERV), because the surveyed distro configs come from kernels where those
    were tristate. CONFIG_HYPERV switching off would have taken the whole
    Hyper-V guest set with it. The type is therefore read here and enforced.
    """
    kernel_src = Path(kernel_src)
    if not kernel_src.is_dir():
        raise SystemExit(
            f"FATAL: kernel source tree not found at {kernel_src}.\n"
            "The hardware/policy split is derived from the kernel's own Kconfig\n"
            "files and cannot be guessed. Point IGOS_KERNEL_SRC at the matching\n"
            "kernel source tree and re-run. Refusing to emit a baseline derived\n"
            "from an unverified classification."
        )
    mapping = {}
    # SORTED, and with two whole trees excluded. All three of these are
    # correctness requirements, not tidiness:
    #
    #   SORTED — `rglob` yields in filesystem order, which differs between
    #     machines. 205 symbols in this kernel are declared in more than one
    #     Kconfig, and a first-match-wins walk over an unsorted list therefore
    #     produces a machine-dependent answer. That is not theoretical: it made
    #     this generator emit CONFIG_KVM=y on one box and =m on another from the
    #     same commit, because KVM is declared in SIX arch trees and
    #     arch/powerpc declares it `bool` while arch/x86 declares it `tristate`
    #     — so the type clamp fired on one machine and not the other. An
    #     artifact that depends on directory iteration order is not reproducible
    #     and cannot be verified by anyone.
    #
    #   OTHER ARCHITECTURES EXCLUDED — this is an x86_64 distribution. A symbol's
    #     type and location must be read from the architecture actually being
    #     built; arch/powerpc has no vote on what CONFIG_KVM is here.
    #
    #   KCONFIG TEST FIXTURES EXCLUDED — scripts/kconfig/tests/ contains toy
    #     Kconfig files written to test the kconfig parser itself. They declare
    #     symbols that collide with real ones (CONFIG_DUMMY is declared `bool`
    #     there and `tristate` in drivers/net) and they are not part of the
    #     kernel's configuration surface at all.
    def _eligible(path):
        rel = path.relative_to(kernel_src).parts
        if not rel:
            return False
        if rel[0] == "arch" and len(rel) > 1 and rel[1] != TARGET_ARCH:
            return False
        if rel[:3] == ("scripts", "kconfig", "tests"):
            return False
        return True

    for kconfig in sorted(kernel_src.rglob("Kconfig*")):
        if not kconfig.is_file():
            continue
        if not _eligible(kconfig):
            continue
        try:
            text = kconfig.read_text(errors="replace")
        except OSError:
            continue
        top = kconfig.parent.relative_to(kernel_src).parts
        top = top[0] if top else ""
        pending = None
        for line in text.splitlines():
            match = RE_KCONFIG_SYMBOL.match(line)
            if match:
                pending = match.group(1)
                mapping.setdefault(pending, (top, ""))
                continue
            if pending:
                type_match = RE_KCONFIG_TYPE.match(line)
                if type_match:
                    # Only fill the type for the first declaration we saw.
                    if mapping.get(pending, ("", ""))[1] == "":
                        mapping[pending] = (mapping[pending][0], type_match.group(1))
                    pending = None
                elif line.strip() and not line.startswith((" ", "\t")):
                    # Left the symbol's indented block without finding a type.
                    pending = None
    if not mapping:
        raise SystemExit(
            f"FATAL: parsed ZERO Kconfig symbols from {kernel_src}. The tree is\n"
            "not a kernel source tree, or its layout changed. Refusing to treat\n"
            "an empty classification as 'nothing is hardware'."
        )
    return mapping


def is_hardware(symbol, tree_map):
    """True if the symbol is defined under a driver/hardware tree.

    Unknown symbols are treated as NOT hardware, so an unrecognised symbol keeps
    the stricter agreement rule rather than being waved in by the union.
    """
    return tree_map.get(symbol, ("", ""))[0] in HARDWARE_TREES


def clamp_to_type(symbol, value, tree_map):
    """Coerce a chosen y/m value to something the symbol can actually hold.

    A `bool` symbol cannot be =m. Emitting one anyway makes olddefconfig warn and
    silently disable the symbol, which is the exact silent-drop class this whole
    change exists to end — so it is corrected here, at the point the value is
    chosen, rather than discovered later in a produced config.
    """
    if value == "m" and tree_map.get(symbol, ("", ""))[1] == "bool":
        return "y"
    return value


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
    baseline.append("#")
    baseline.append("# ⚠️  GENERATED FILE — DO NOT HAND-EDIT.")
    baseline.append("# Produced by docs/research/kernel_configs/analyze_convergence.py from the")
    baseline.append("# five distro configs beside it. Any hand edit here is silently destroyed by")
    baseline.append("# the next regeneration. Put InterGenOS-specific choices and corrections in")
    baseline.append("# config/kernel/fragments/99-intergenos-overrides.config, which is")
    baseline.append("# concatenated after this file and therefore wins in olddefconfig.")
    baseline.append("#")
    baseline.append("# DERIVATION RULE (decided 2026-08-07) — two rules, not one:")
    baseline.append("#")
    baseline.append("#   Driver/hardware symbols (defined under drivers/ or sound/ in the kernel")
    baseline.append("#   source): UNION across " + ", ".join(MAINSTREAM_DISTROS) + ". If any one of")
    baseline.append("#   them ships a driver, some user owns that hardware. Built in (=y) only")
    baseline.append("#   when every one of them that enables it builds it in; otherwise a module.")
    baseline.append("#")
    baseline.append("#   Policy/security symbols (everything else): enabled when at least")
    baseline.append(f"#   {POLICY_AGREEMENT_THRESHOLD} of the 5 surveyed distros enable it. Broad agreement is the")
    baseline.append("#   right signal for behaviour; a union there would import other projects'")
    baseline.append("#   security choices wholesale.")
    baseline.append("#")
    baseline.append("#   Kernel-developer test and fake-hardware drivers are stripped outright.")
    baseline.append("#")
    baseline.append("# WHY THE SPLIT: a single agreement threshold can never exceed what its")
    baseline.append("# leanest inputs carry. Two of the five configs are systematically lean (Arch")
    baseline.append("# is a trimmed laptop config; Debian is a split base+arch pair), so any driver")
    baseline.append("# those two both omit was dropped even when all three general-purpose distros")
    baseline.append("# shipped it. That silently cost 1,493 driver options. A missing driver does")
    baseline.append("# not fail loudly — the device is simply not there.")
    baseline.append("#")
    baseline.append("# Source: Ubuntu, Arch, Fedora, Debian, openSUSE kernel configs.")
    baseline.append("#")
    baseline.append("")

    # Apply the derivation rule: UNION across the mainstream distros for
    # driver/hardware symbols, AGREEMENT THRESHOLD for policy/security symbols,
    # and strip the kernel-developer test / fake-hardware class outright.
    kernel_src = os.environ.get("IGOS_KERNEL_SRC", DEFAULT_KERNEL_SRC)
    print(f"\n  Classifying symbols against kernel source: {kernel_src}")
    tree_map = build_symbol_tree_map(kernel_src)
    print(f"  Kconfig symbols classified: {len(tree_map)}")

    baseline_options = {}
    hardware_union = []
    policy_agreed = []
    stripped_test = []

    for opt in sorted(all_options):
        symbol = opt[len("CONFIG_"):] if opt.startswith("CONFIG_") else opt
        values = {d: distros[d].get(opt) for d in distro_names}

        # Numeric/string-valued options are not y/m/n toggles.
        if any(v == "other" for v in values.values()):
            continue

        # Kernel-developer test / fake-hardware drivers never enter the baseline.
        if is_test_class(symbol):
            if any(v in ("y", "m") for v in values.values()):
                stripped_test.append(opt)
            continue

        if is_hardware(symbol, tree_map):
            # UNION: any mainstream distro shipping it is sufficient.
            enabling = [values[d] for d in MAINSTREAM_DISTROS if values.get(d) in ("y", "m")]
            if not enabling:
                continue
            # Modules are the safe default for hardware. Build in only when EVERY
            # mainstream distro that enables it builds it in — that is the signal
            # the symbol has no module form at all.
            chosen = "y" if all(v == "y" for v in enabling) else "m"

            # THE UNION MAY ONLY ADD COVERAGE — IT MAY NEVER TAKE ANY AWAY.
            #
            # The value rule above is deliberately conservative, and on its own it
            # DOWNGRADES symbols that the agreement rule built in: measured on this
            # corpus it moved 39 symbols from =y to =m, among them CONFIG_USB,
            # CONFIG_USB_XHCI_HCD, CONFIG_USB_HID and CONFIG_HID. That is not a
            # cosmetic difference. The install medium is a USB stick and this
            # kernel is built to mount root with no initramfs, so demoting the USB
            # host controller to a module breaks booting the installer on hardware
            # that has nothing else to boot from — a coverage CHANGE dressed up as
            # a coverage increase.
            #
            # So the two rules are combined rather than substituted: whatever the
            # agreement rule would have chosen is a FLOOR the union cannot go
            # under. Built-in wins over module. This makes the derivation change
            # additive by construction, which is exactly the property that can then
            # be verified — the regenerated baseline adds thousands of options and
            # removes none.
            agreement = [v for v in values.values() if v in ("y", "m")]
            if len(agreement) >= POLICY_AGREEMENT_THRESHOLD:
                floor = "y" if agreement.count("y") >= agreement.count("m") else "m"
                if floor == "y":
                    chosen = "y"

            baseline_options[opt] = clamp_to_type(symbol, chosen, tree_map)
            hardware_union.append(opt)
        else:
            # AGREEMENT: unchanged from the original rule for policy/security.
            enabled = [v for v in values.values() if v in ("y", "m")]
            if len(enabled) >= POLICY_AGREEMENT_THRESHOLD:
                chosen = "y" if enabled.count("y") >= enabled.count("m") else "m"
                baseline_options[opt] = clamp_to_type(symbol, chosen, tree_map)
                policy_agreed.append(opt)

    print(f"\n  Derivation rule results:")
    print(f"    hardware symbols added by UNION of {'/'.join(MAINSTREAM_DISTROS)}: {len(hardware_union)}")
    print(f"    policy symbols kept by >={POLICY_AGREEMENT_THRESHOLD}/5 agreement:            {len(policy_agreed)}")
    print(f"    kernel-developer test/fake-hardware symbols STRIPPED:  {len(stripped_test)}")
    for opt in sorted(stripped_test):
        print(f"      stripped: {opt}")

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

    # ── Fake-hardware drivers, forced OFF ────────────────────────────────
    #
    # OMITTING a symbol does NOT disable it. `make olddefconfig` resolves an
    # unstated symbol from its Kconfig default and from any `imply` pointing at
    # it, so a symbol merely left out of this fragment comes BACK.
    #
    # That is not hypothetical: SND_SOC_SDW_MOCKUP was stripped from this
    # fragment and still appeared in the produced config at =m and shipped as a
    # built module, because sound/soc/intel/boards/Kconfig and
    # sound/soc/codecs/Kconfig both carry `imply SND_SOC_SDW_MOCKUP`. It is the
    # same silent-resolution mechanism that dropped the MMC drivers, running in
    # the opposite direction — and stripping-by-omission could not see it,
    # because the evidence only exists in the OUTPUT.
    #
    # So the strip is stated explicitly here and rides through to the merged
    # config, where olddefconfig must honour it.
    # The disable set is the WHOLE named class plus anything the strip caught,
    # NOT just the symbols some distro happened to vote for. A fabricator with
    # zero distro votes never enters `stripped_test`, so it would get no disable
    # line — and `imply` would still be free to bring it back.
    stripped_now = sorted(
        set(stripped_test) | {f"CONFIG_{s}" for s in TEST_CLASS_EXPLICIT}
    )
    if stripped_now:
        baseline.append("#")
        baseline.append("# ── Fake-hardware / kernel-developer test drivers — FORCED OFF")
        baseline.append("#")
        baseline.append("# These fabricate devices the machine does not have. On a security-first")
        baseline.append("# OS that is a masking primitive: a fabricated device reads as working")
        baseline.append("# hardware to anything that inspects the system, including this")
        baseline.append("# repository's own hardware smoke checks. Disabled explicitly rather")
        baseline.append("# than omitted, because omission does not disable.")
        baseline.append("#")
        for opt in stripped_now:
            baseline.append(f"# {opt} is not set")
        baseline.append("")

    baseline_text = "\n".join(baseline) + "\n"

    cfg_path = CONFIG_DIR / "universal-baseline.config"
    cfg_path.write_text(baseline_text)
    print(f"Wrote baseline config to: {cfg_path}")

    # The SHIPPED fragment is written from the same text, so the build's input
    # and the research artifact cannot drift apart. Previously the shipped copy
    # was maintained by hand alongside this one; a hand edit there flipped
    # CONFIG_DM_VERITY to =y, which meant regenerating this script would have
    # silently destroyed a root-integrity guarantee. That value now lives in the
    # overrides fragment where a generator cannot erase it.
    FRAGMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    FRAGMENT_PATH.write_text(baseline_text)
    print(f"Wrote shipped fragment to: {FRAGMENT_PATH}")
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
