# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Package installation for InterGenOS installer — wraps pkm."""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# Add project root so we can import pkm
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pkm.database import PackageDB
from pkm.installer import PackageInstaller

from . import trace

LOG = logging.getLogger("forge.packages")


# ---------------------------------------------------------------------------
# Install-time hardware gate (GBC001 blocker fix, 2026-06-05).
#
# A package may declare `requires_pci_vendor: "<hex>"` in its package.yml. If
# it does, Forge installs it ONLY when the target has a display controller
# from that PCI vendor. The canonical case is `extra/nvidia`: NVIDIA's .run
# ships a non-glvnd libEGL.so.580 whose embedded SONAME is `libEGL.so.1`. On
# a machine with no NVIDIA GPU it still outranks glvnd's libEGL.so.1.1.0 in
# ldconfig, so EGL breaks on amdgpu/i915 -> gnome-shell fails -> GDM crash-
# loops on the *installed* system (the live ISO prunes nvidia, so the live
# desktop was unaffected; the first bare-metal install was not — it pulled
# nvidia onto an AMD box and crash-looped GDM).
#
# Detection is by display-class PCI vendor (lspci class 03xx), mirroring
# packages/extra/nvidia/hooks/check-hardware.sh (which is NOT on the ISO —
# the installer-hooks tree ships only build.sh + package.yml per package, so
# the detection is ported here rather than exec'd).
#
# FAIL-CLOSED: if the target's display vendors cannot be positively detected
# (lspci absent/fails), a gated package is SKIPPED. An unbootable desktop is
# far worse than a driver the user can add later via `pkm install nvidia`.
# Every skip is logged (no silent drop).
# ---------------------------------------------------------------------------

_PCI_VENDOR_CACHE = None


def detect_display_pci_vendors():
    """Return the set of PCI vendor IDs (lowercase 4-hex, no 0x) for display
    controllers (lspci class 03xx) present on this machine. Empty set if
    lspci is unavailable or fails — callers treat that as fail-closed.

    Memoized for the process: get_group_packages is called once per group in
    the empty-group pre-flight plus once for the real install, and lspci does
    not change mid-install.
    """
    global _PCI_VENDOR_CACHE
    if _PCI_VENDOR_CACHE is not None:
        return _PCI_VENDOR_CACHE

    vendors = set()
    try:
        proc = subprocess.run(
            ["lspci", "-n"], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        LOG.warning("hardware-gate: lspci unavailable (%s); gated packages "
                    "will be skipped (fail-closed)", exc)
        _PCI_VENDOR_CACHE = vendors
        return vendors

    if proc.returncode != 0:
        LOG.warning("hardware-gate: lspci exited %d; gated packages will be "
                    "skipped (fail-closed)", proc.returncode)
        _PCI_VENDOR_CACHE = vendors
        return vendors

    # lspci -n line: "<slot> <class>: <vendor>:<device> [...]"
    #   e.g. "01:00.0 0300: 10de:2484 (rev a1)"
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        cls = parts[1].rstrip(":")
        if not cls.startswith("03"):  # 03xx = VGA / 3D / display controllers
            continue
        vendor = parts[2].split(":")[0].strip().lower()
        if vendor:
            vendors.add(vendor)

    _PCI_VENDOR_CACHE = vendors
    return vendors


def _read_required_pci_vendor(pkg_yaml_path):
    """Read `requires_pci_vendor` from a package.yml. Returns the lowercase
    hex vendor string, or None if absent / unreadable / yaml missing. Never
    raises — a malformed manifest must not abort package selection."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        with open(pkg_yaml_path) as fh:
            data = yaml.safe_load(fh)
    except Exception:  # noqa: BLE001 — yaml.YAMLError + IO; never fatal to selection
        return None
    if not isinstance(data, dict):
        return None
    vendor = data.get("requires_pci_vendor")
    if vendor is None:
        return None
    return str(vendor).strip().lower()


def _read_eula_helper(pkg_yaml_path):
    """Read `eula_helper` from a package.yml. Returns the helper name string,
    or None if absent / unreadable / yaml missing. Never raises — a malformed
    manifest must not abort package selection."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        with open(pkg_yaml_path) as fh:
            data = yaml.safe_load(fh)
    except Exception:  # noqa: BLE001 — yaml.YAMLError + IO; never fatal to selection
        return None
    if not isinstance(data, dict):
        return None
    helper = data.get("eula_helper")
    if helper is None:
        return None
    return str(helper).strip()


def _read_runtime_deps(pkg_yaml_path):
    """Return the list of runtime dependency names from a package.yml (the
    `dependencies.runtime` list), or [] if absent/unreadable. Never raises —
    a malformed manifest must not abort package selection."""
    if pkg_yaml_path is None:
        return []
    try:
        import yaml
    except ImportError:
        return []
    try:
        with open(pkg_yaml_path) as fh:
            data = yaml.safe_load(fh)
    except Exception:  # noqa: BLE001 — yaml.YAMLError + IO; never fatal
        return []
    if not isinstance(data, dict):
        return []
    deps = data.get("dependencies")
    if not isinstance(deps, dict):
        return []
    runtime = deps.get("runtime")
    if not isinstance(runtime, list):
        return []
    return [str(d).strip() for d in runtime if d]


def _read_pkg_name(pkg_yaml_path):
    """Return the authoritative package `name:` from a package.yml — the field
    the build uses to name the archive `<name>-<version>.igos.tar.gz`
    (igos-build/tracker.py pkg_archive). Returns None if absent/unreadable.
    Never raises — a malformed manifest must not abort package selection."""
    if pkg_yaml_path is None:
        return None
    try:
        import yaml
    except ImportError:
        return None
    try:
        with open(pkg_yaml_path) as fh:
            data = yaml.safe_load(fh)
    except Exception:  # noqa: BLE001 — yaml.YAMLError + IO; never fatal
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    return str(name).strip() if name else None


# Package groups for installation
GROUPS = {
    "core": {
        "description": "Essential system (kernel, shell, coreutils, systemd, SSH)",
        "tiers": ["core"],
        "required": True,
    },
    "base": {
        "description": "CLI tools (htop, rsync, strace, screen)",
        "tiers": ["base"],
        "required": False,
        "default": True,
    },
    "desktop-gnome": {
        "description": "GNOME desktop environment on Wayland",
        "tiers": ["desktop"],
        # Decided 2026-05-26: GNOME is required for now. The
        # user-facing choice of other DEs (or none) is a planned future
        # feature; until that lands, InterGenOS without GNOME isn't a
        # supported install — the security + UX posture is built around
        # the GNOME-on-Wayland stack. Locking here cascades to both
        # Forge (renders the toggle locked-on) and the TUI installer.
        "required": True,
        "default": True,
    },
    # Descriptions state what the tier's ISO-shipped set ACTUALLY installs
    # (updated 2026-07-22 against the live iso_include:true membership —
    # the prior text advertised download-helpers that are mirror-only now
    # and labeled the AI runtime "text-only", both stale). Groups resolve
    # dynamically against the archives staged on the ISO, so the honest
    # description is the tier's shipped membership, not its full tree.
    "extra": {
        "description": (
            "Applications & virtualization (Firefox, Thunderbird, "
            "LibreOffice, GIMP, media players, QEMU/libvirt, CLI tools)"
        ),
        "tiers": ["extra"],
        "required": False,
        "default": False,
    },
    # C-007: "ai" was selectable in TUI PACKAGE_GROUP_CHOICES (frontend/tui.py:221)
    # but had no entry here, so get_group_packages did GROUPS.get("ai") → None →
    # tiers stayed empty → user-checked AI group installed zero packages silently.
    # packages/ai/ ships intergen + llama-cpp on the ISO (the multimodal
    # assistant + its serving engine); the training apparatus is mirror-only.
    "ai": {
        "description": "Local AI runtime (the InterGen assistant + llama.cpp serving)",
        "tiers": ["ai"],
        "required": False,
        "default": False,
    },
}


def _name_version_from_pkginfo(archive_path):
    """Authoritative (pkgname, pkgver) read from an archive's .PKGINFO.

    The builder stamps .PKGINFO into every archive and the mirror index is built
    from the very same fields (pkm/repo.py), so .PKGINFO — not the filename — is
    the single source of truth for a package's name/version. Returns (None, None)
    if absent/unreadable so the caller falls back to filename parsing (legacy
    archives predating the .PKGINFO ratification).

    NOTE: .PKGINFO is the LAST member in our archives, so reading it decompresses
    the whole tar. get_archives() therefore consults this ONLY to disambiguate a
    filename parse it cannot trust (a hyphen left in the parsed version), never
    for the clean-semver majority.
    """
    import tarfile
    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            for member in tf:
                if member.name in (".PKGINFO", "./.PKGINFO"):
                    fobj = tf.extractfile(member)
                    if fobj is None:
                        return (None, None)
                    name = ver = None
                    for line in fobj.read().decode("utf-8", "replace").splitlines():
                        if line.startswith("pkgname="):
                            name = line.split("=", 1)[1].strip()
                        elif line.startswith("pkgver="):
                            ver = line.split("=", 1)[1].strip()
                    return (name or None, ver or None)
    except (tarfile.TarError, OSError) as e:
        LOG.warning("get_archives: could not read .PKGINFO from %r (%s); "
                    "falling back to filename parse", archive_path.name, e)
    return (None, None)


def get_archives(archive_dir):
    """Scan archive directory and return dict of {name: (version, path)}.

    Archives are named: <name>-<version>.igos.tar.gz
    """
    archive_dir = Path(archive_dir)
    archives = {}

    if not archive_dir.exists():
        return archives

    for f in sorted(archive_dir.iterdir()):
        if not f.name.endswith(".igos.tar.gz"):
            continue
        stem = f.name.replace(".igos.tar.gz", "")
        # Archives are '<name>-<version>'. The digit-anchored regex handles the
        # common semver case (non-greedy name up to the first '-<digit>'). But
        # it SILENTLY DROPS any archive whose version does not start with a
        # digit — e.g. llama.cpp's upstream 'b5545' build tag. That single gap
        # kept the llama-server inference engine off EVERY Forge install: the
        # binary was in the chroot/squashfs so the live ISO worked and the
        # pre-squashfs verify-paths audit passed, but Forge installs from these
        # archives and get_archives() never even saw llama-cpp, so it was never
        # installed and never logged. GBC002.4 (2026-06-08): fall back to a
        # last-hyphen split so a non-digit version is PARSED, not dropped; warn
        # (never silently skip) if a name is genuinely unparseable.
        import re
        match = re.match(r'^(.+?)-(\d.*)$', stem)
        if match:
            name = match.group(1)
            version = match.group(2)
        elif "-" in stem:
            name, version = stem.rsplit("-", 1)
        else:
            LOG.warning("get_archives: cannot parse archive name %r — SKIPPING "
                        "(no version delimiter); this package will NOT install", f.name)
            continue
        # A hyphen surviving in the parsed VERSION means the filename split can't
        # be trusted: either the non-greedy regex mis-split a hyphen-in-name
        # package (ntfs-3g-2026.2.25 -> name=ntfs / version=3g-2026.2.25, which
        # then never matches the mirror's `ntfs-3g` and won't version-compare),
        # or the version is legitimately hyphenated. For these bounded cases use
        # the archive's authoritative .PKGINFO so the installed name/version match
        # the build + mirror exactly. Clean semver versions cannot be mis-split (a
        # -<digit>-bearing name is what leaves the hyphen behind), so they skip the
        # (expensive, full-decompress) .PKGINFO read entirely.
        # (GBC003.4, 2026-06-17 — ntfs-3g installed as name=ntfs/ver=3g-...).
        if version and "-" in version:
            pn, pv = _name_version_from_pkginfo(f)
            if pn and pv:
                name, version = pn, pv
        # A second archive resolving to the same package name would SILENTLY
        # clobber the first in this dict — the same silent-drop class as the
        # llama-cpp version-parse miss (one of the two packages then never
        # installs, unlogged). Surface every collision loudly; a clean staged
        # archive set has exactly one archive per name, so a collision is a
        # staging/naming bug the install-set audit must catch.
        if name in archives:
            prev_ver, prev_f = archives[name]
            LOG.warning("get_archives: DUPLICATE package name %r — %r (version "
                        "%r) overwrites %r (version %r); only one installs. "
                        "Staging/naming collision.",
                        name, f.name, version, prev_f.name, prev_ver)
        archives[name] = (version, f)

    return archives


def _archive_name_candidates(hook_name: str) -> list[str]:
    """Return every name the archive for a given installer-hook dir
    could legitimately be parsed as. Used by `get_group_packages` to
    bridge the LFS-staged-build naming convention where the source
    directory uses an `<X>-core` suffix (pass-1 minimal build) but
    the archive ships as `<X>` (final binary), plus a handful of
    other historical name variants where the dir name and archive
    name differ by a trailing version-major digit, a `-3g`-style
    suffix, or simply being absent altogether.

    Identity match (the hook name itself) is always FIRST so the
    happy path stays exact-string. Aliases are tried in order until
    one resolves to a real archive.

    This shouldn't be necessary going forward — the build pipeline
    at igos-build/tracker.py:175 names archives `<pkg.name>-<version>`
    where pkg.name comes straight from the YAML — so a new build
    should produce `glibc-core-2.43.igos.tar.gz` etc. But legacy
    archives on already-built live ISOs and the few dir/yaml/archive
    naming inconsistencies in the source tree (cairomm vs cairomm1,
    ntfs-3g vs ntfs, libsigcpp vs libsigcpp2) need this fallback so
    the install pipeline doesn't silently drop packages."""
    candidates = [hook_name]

    # LFS staged-build suffix: source dirs use `<X>-core` for pass-1
    # builds, archive is the final `<X>`. Affects ~20 packages in
    # tier:core (binutils, bison, coreutils, diffutils, findutils,
    # gawk, gcc, glibc, grep, gzip, m4, make, ncurses, patch, perl,
    # sed, tar, texinfo, util-linux, bison).
    if hook_name.endswith("-core"):
        candidates.append(hook_name[:-len("-core")])

    # Version-major-digit-in-name aliases. cairomm-1 / libsigcpp-2 etc.
    # are commonly versioned at the package-name level (so a future
    # cairomm-2 can coexist on disk). Source dirs use the unsuffixed
    # name; archives carry the major digit.
    _version_in_name_aliases = {
        "cairomm": "cairomm1",
        "libsigcpp": "libsigcpp2",
    }
    if hook_name in _version_in_name_aliases:
        candidates.append(_version_in_name_aliases[hook_name])

    # Legacy fallback only. `ntfs-3g` is the real package name (recipe + .PKGINFO
    # + mirror all agree). The bogus `ntfs` alias existed because get_archives()
    # used to mis-split the filename `ntfs-3g-2026.2.25` into name=`ntfs` — fixed
    # at the source (get_archives now reads the authoritative .PKGINFO for any
    # hyphen-bearing parse; GBC003.4 2026-06-17). This remap is retained ONLY so a
    # legacy archive predating .PKGINFO that still mis-splits to `ntfs` resolves.
    _name_remaps = {
        "ntfs-3g": "ntfs",
    }
    if hook_name in _name_remaps:
        candidates.append(_name_remaps[hook_name])

    return candidates


# Install-order essentials. These packages MUST install first, in this
# explicit order, BEFORE any alphabetical-tail packages. They are the
# tools every per-package post-install hook may invoke (sed, grep, stat,
# coreutils) plus the foundational chain that makes any binary exec'able
# at all (glibc-core's dynamic linker + UsrMerge dir symlinks + bash).
#
# Why this list exists: pre-2026-05-26-late, get_group_packages returned
# strict alphabetical order. linux-kernel ('l', pos ~496/790) installs
# BEFORE sed-core ('s', pos ~700+). The linux-kernel post-install hook
# is invoked by pkm RIGHT AFTER its package extracts (per pkm/installer.py
# _run_post_install_hook called from install()), and the hook's first
# action used a `sed`-bearing pipeline that exited 127 with sed absent +
# `set -uo pipefail` propagating. The script died silently before its
# first log() call -> UKI never built -> ESP missing /EFI/Linux/*.efi ->
# kernel panic on first boot.
#
# Surfaced 2026-05-26 install attempt #21 first-boot triage. Operator-
# direct fix during that session.
#
# Each entry is the ARCHIVE name (post-_archive_name_candidates resolution),
# not the source-dir name. "-core" suffixes are the LFS-pass-1 archives.
# Packages not present in the resolved archive set are silently skipped
# (lets the list double as documentation without requiring 1:1 presence).
INSTALL_ORDER_ESSENTIALS = [
    # Class 11 canonical owner of /etc baseline + FHS skeleton + systemd
    # preset policy + tmpfiles.d. Lands FIRST so /etc/{passwd,group,
    # shadow,gshadow} + /etc/profile + preset files exist BEFORE any
    # other package's post-install hook fires. Plan v2 (2026-05-27,
    # bilateral review APPROVE-clean at 06:41Z). Replaces the
    # d9911088 glibc-core /etc baseline (reverted same commit).
    "intergenos-base-files",
    # Foundation: dynamic linker + UsrMerge dir symlinks +
    # /etc/{nsswitch,ld.so}.conf land here (passwd/group/shadow/gshadow
    # baseline moved to intergenos-base-files above per plan v2).
    "glibc-core",
    "glibc",
    # Shell + shebang interpreter for every script-shaped post-install hook.
    "bash",
    # The minimum coreutils set hooks routinely call: ls, head, cat,
    # mkdir, dirname, date, stat, chmod, install, ln, cp, mv, rm, true.
    "coreutils-core",
    "coreutils",
    # Text-processing tools hooks routinely invoke in pipelines.
    "sed-core",
    "sed",
    "grep-core",
    "grep",
    "gzip-core",
    "gzip",
    # Archive + compression for any hook that extracts a sub-archive.
    "tar-core",
    "tar",
    "xz",
    # File-type detection — ukify (linux-kernel hook) uses libmagic at
    # runtime to identify the kernel ELF format.
    "file",
    # User/group management — useradd, groupadd, chpasswd are invoked by
    # several recipes' post_install hooks AND by installer.backend.users.
    "shadow",
    "shadow-pam",
]


# Packages that MUST install AFTER everything else (alphabetical default),
# in declared order. Used for packages whose pkm post-install hooks need
# OTHER packages' binaries to already be deployed on /mnt/target.
#
# Surfaced 2026-05-27 install #27: D-005 UKI primary path never produced
# a UKI on the ESP because the linux-kernel package's `/var/lib/pkm/
# hooks/linux-kernel/post-install` script (D-005 Phase A — runs `ukify
# build` + sign-with-user-MOK) fires DURING pkm install of linux-kernel
# (alphabetical 'l', archive index 502), BEFORE ukify (ships in systemd-
# pass2, index 694) and sbsign (ships in sbsigntool, index 652) exist
# on /mnt/target. The hook's `command -v ukify >/dev/null` check
# returned 1, the hook silently exited without producing a UKI, the
# system fell to grub-loads-vmlinuz — the OPPOSITE of D-005's ratified
# UKI-primary intent. Decided 2026-05-27 ratification: UKI
# primary is the directive; graceful-degrade-on-every-install is a
# defect, not a design.
#
# Safety analysis: linux-kernel is the ONLY package that fires the
# canonical depmod hook in the base install set (verified via install
# #27 trace + grep packages/*/*/build.sh for modprobe/insmod — only
# matches in comments, no runtime invocations). Moving linux-kernel
# to last in the install order does NOT affect any other package's
# install correctness.
INSTALL_ORDER_LATE = [
    "linux-kernel",
]


def _close_runtime_deps(in_tier, archives, yml_by_name):
    """Pull declared runtime deps that have a built archive but aren't yet in
    the install set, regardless of tier — closing the cross-tier silent-drop
    class. The canonical case: intergen lives in the `ai` tier but hard-depends
    on numpy, which ships only in the optional `extra` tier; without closure an
    AI-without-Extras install gets intergen but no numpy and the daemon crashes
    on `import numpy` at first launch. Transitive to a fixpoint; every pull is
    logged (never silent). The caller runs the hardware-gate pass AFTER this
    over the closed set, so a pulled package that is PCI-gated is still dropped
    when its vendor is absent.

    in_tier and archives are {name: (version, path)}; yml_by_name maps every
    archive-name candidate (across ALL tiers) to its package.yml path or None.
    Mutates and returns in_tier."""
    queue = list(in_tier.keys())
    while queue:
        name = queue.pop()
        yml_path = yml_by_name.get(name)
        if yml_path is None:
            # No package.yml known for this selected/pulled package: its
            # runtime deps are INVISIBLE to the closure — exactly how the
            # ge9b-04 dogfood install silently dropped plutosvg/plutovg
            # (sdl3-ttf was pulled by name from freerdp's yml, but its own
            # yml was missing from the installer-hooks tree, so its deps
            # were never read; PI-ge9b04-C). The hooks fold now ships every
            # package.yml, so this firing means the packages_dir is stale
            # or incomplete — say so LOUDLY, never skip silently.
            LOG.warning(
                "get_group_packages: no package.yml known for %r — its "
                "runtime dependencies CANNOT be closed over and may be "
                "silently missing from the install set (stale/incomplete "
                "installer-hooks tree?)", name)
            continue
        for dep in _read_runtime_deps(yml_path):
            resolved = None
            for cand in _archive_name_candidates(dep):
                if cand in archives:
                    resolved = cand
                    break
            if resolved is None or resolved in in_tier:
                # No built archive for this dep (the install-set preflight
                # surfaces that loudly) or it is already selected — skip.
                continue
            in_tier[resolved] = archives[resolved]
            LOG.info("get_group_packages: pulled %r as a runtime dependency "
                     "of %r (cross-tier closure)", resolved, name)
            queue.append(resolved)
    return in_tier


def _discover_tier_membership(package_dir, tiers):
    """Walk packages/ and return (tier_packages, gate_by_name, yml_by_name).

      tier_packages: archive names belonging to a SELECTED tier (`tiers` arg).
      gate_by_name:  archive name -> required PCI vendor (built across ALL
                     tiers so a cross-tier-pulled dep is still gated).
      yml_by_name:   archive name -> its package.yml path or None (ALL tiers),
                     for the cross-tier runtime-dependency closure.

    Discovery mirrors the BUILD system's own contract: igos-build/parser.py
    finds packages via `packages_dir.rglob("package.yml")` (ANY depth) and
    igos-build/tracker.py names each archive `<name>-<version>` from the yaml
    `name:` field. The installer historically keyed tier membership on the
    immediate-child DIRECTORY NAME at a fixed depth, so the two discoverers
    disagreed: a package nested deeper than packages/<tier>/<pkg>/ (or whose
    dir name != yaml name) was built and staged but never entered the install
    set — a SILENT drop. websockets at packages/extra/intergen-web-ui/
    websockets/ was the live case (G3-10): the build produced
    websockets-16.0.igos.tar.gz, get_archives() saw it, but it was filtered
    out because "websockets" was never in tier_packages.

    Two registration passes, both purely ADDITIVE (they can only ADD a name to
    the allowed set, never remove one — so this can fix silent drops, never
    cause one):

      (1) Every immediate child dir packages/<tier>/<pkg>/ registered by its
          DIRECTORY NAME (+ _archive_name_candidates aliases), package.yml
          present or not — preserves discovery for packages whose package.yml
          is absent on the ISO (the installer-hooks tree may ship build.sh
          only for some).

      (2) Every package.yml that rglob finds DEEPER than an immediate child
          registered by its authoritative yaml `name:` field (G3-10). Pass (1)
          never descends into it.
    """
    package_dir = Path(package_dir)
    tier_packages = set()
    gate_by_name = {}
    eula_by_name = {}
    yml_by_name = {}

    def _register(names, yml_path, in_selected):
        for cand in names:
            yml_by_name.setdefault(cand, yml_path)
            if in_selected:
                tier_packages.add(cand)
        if yml_path is not None:
            req = _read_required_pci_vendor(yml_path)
            if req:
                for cand in names:
                    gate_by_name[cand] = req
            helper = _read_eula_helper(yml_path)
            if helper:
                for cand in names:
                    eula_by_name[cand] = helper

    for tier_dir in sorted(package_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        in_selected = tier_dir.name in tiers
        # (1) immediate children — keyed by dir name (+ aliases) AND, when the
        #     package.yml is present, its authoritative yaml `name:` (retires
        #     the dir-name != archive-name silent-drop class the alias table
        #     only partially covered, e.g. cairomm -> cairomm1).
        for pkg_dir in sorted(tier_dir.iterdir()):
            if not pkg_dir.is_dir():
                continue
            yml = pkg_dir / "package.yml"
            yml_path = yml if yml.exists() else None
            names = set(_archive_name_candidates(pkg_dir.name))
            yaml_name = _read_pkg_name(yml_path)
            if yaml_name:
                names.add(yaml_name)
            _register(names, yml_path, in_selected)
        # (2) nested package.yml — keyed by authoritative yaml name (G3-10).
        for yml_path in sorted(tier_dir.rglob("package.yml")):
            if yml_path.parent.parent == tier_dir:
                continue  # immediate child, already handled by pass (1)
            names = set(_archive_name_candidates(yml_path.parent.name))
            yaml_name = _read_pkg_name(yml_path)
            if yaml_name:
                names.add(yaml_name)
            _register(names, yml_path, in_selected)

    return tier_packages, gate_by_name, eula_by_name, yml_by_name


def get_group_packages(groups, archive_dir, package_dir=None,
                       detected_vendors=None):
    """Get the list of archives to install for selected groups.

    Args:
        groups: list of group names (e.g., ["core", "base", "desktop-gnome"])
        archive_dir: path to archive directory
        package_dir: path to packages/ directory (for tier mapping)
        detected_vendors: set of display-controller PCI vendor IDs present on
            the target (lowercase hex). When None (production), it is detected
            via lspci. Injectable so the hardware-gate can be tested against a
            real archive set without depending on the test host's GPU.

    Returns:
        list of (name, version, archive_path) tuples in install order.
        INSTALL_ORDER_ESSENTIALS are emitted FIRST in declared order;
        all remaining packages follow in alphabetical order. This
        guarantees every per-package post-install hook fires AFTER the
        baseline tools it depends on are installed.

        Packages declaring `requires_pci_vendor` in their package.yml are
        omitted when no display controller from that vendor is present on the
        target (the GBC001 nvidia-on-AMD blocker fix). Each omission is
        logged; fail-closed when vendors can't be detected.
    """
    # Determine which tiers we need
    tiers = set()
    for group_name in groups:
        group = GROUPS.get(group_name)
        if group:
            tiers.update(group["tiers"])

    # Get available archives
    archives = get_archives(archive_dir)

    # If we have a packages directory, filter by tier. Build the
    # tier-allowed name set via _archive_name_candidates so the
    # source-dir naming convention (`<X>-core` for LFS pass-1
    # builds + a few minor aliases) maps correctly to the archive
    # name without silently dropping packages.
    if package_dir:
        tier_packages, gate_by_name, eula_by_name, yml_by_name = \
            _discover_tier_membership(Path(package_dir), tiers)

        # Filter archives to only include packages in requested tiers
        in_tier = {name: (ver, path) for name, (ver, path) in archives.items()
                   if name in tier_packages}

        # Cross-tier runtime-dependency closure: pull any declared runtime dep
        # that has a built archive but isn't in a selected tier (e.g. intergen
        # (ai) -> numpy (extra)). Without this a package installs while a hard
        # runtime dependency is silently absent — the same silent-drop class
        # that kept llama-cpp off every install.
        in_tier = _close_runtime_deps(in_tier, archives, yml_by_name)

        # Apply the install-time hardware gate (drop nvidia on non-NVIDIA HW).
        if gate_by_name:
            in_tier = _apply_hardware_gate(in_tier, gate_by_name,
                                           detected_vendors)

        # PI-Z6: EULA-gated packages are OMITTED from the non-interactive
        # install set wholesale. Their license gate requires an interactive
        # TTY accept/decline (pkm's helper exits 4 without one), so a Forge
        # install can never satisfy it — attempting one just manufactures a
        # spurious "package failed" line on an otherwise-clean install (the
        # Zephyrus first-NVIDIA install, 2026-07-06). The user installs them
        # post-boot with `pkm install <name>`, where the EULA pager can
        # actually run. Every omission is logged + traced (no silent drop).
        if eula_by_name:
            in_tier = _apply_eula_gate(in_tier, eula_by_name)
        # Dependency-derived install order (the L17kw fix): resolve each
        # in-set package's declared runtime deps to in-set archive names and
        # topologically sort. The prior order — essentials list + alphabetical
        # remainder — was a hand-curated stand-in for this derivation, and it
        # failed exactly where hand lists fail: intel-ucode's post-install
        # hook ran before readline was on the target ('i' < 'r'), rc=127,
        # because bash's own library was never on anyone's list.
        deps_by_name = {}
        for name in in_tier:
            resolved = []
            for dep in _read_runtime_deps(yml_by_name.get(name)):
                for cand in _archive_name_candidates(dep):
                    if cand in in_tier:
                        resolved.append(cand)
                        break
            deps_by_name[name] = resolved
        return _order_install_set(in_tier, deps_by_name)
    else:
        # No tier filtering — no package.yml tree, so no dependency data:
        # fall back to the essentials+alphabetical order and SAY SO. This
        # branch never runs on a real install (install.py always passes
        # packages_dir); it exists for bare archive-dir listings.
        LOG.warning("get_group_packages: no package_dir — install order is "
                    "NOT dependency-derived (essentials+alphabetical "
                    "fallback)")
        return _order_install_set(dict(archives), {})


def _apply_hardware_gate(in_tier, gate_by_name, detected_vendors):
    """Drop hardware-gated packages whose required PCI vendor is not present
    on the target. Returns the filtered {name: (ver, path)} dict. Every drop
    is logged (no silent omission); fail-closed when vendors can't be
    detected. `detected_vendors` may be injected for testing; None -> detect.
    """
    if detected_vendors is None:
        detected_vendors = detect_display_pci_vendors()

    kept = {}
    dropped = []
    for name, (ver, path) in in_tier.items():
        req = gate_by_name.get(name)
        if req and req not in detected_vendors:
            dropped.append((name, req))
            continue
        kept[name] = (ver, path)

    vendor_str = ",".join(sorted(detected_vendors)) or "none"
    for name, req in dropped:
        LOG.info("hardware-gate: skipping %r — requires PCI vendor %s, "
                 "but the target's display vendors are: %s",
                 name, req, vendor_str)
        trace.trace_event("hardware_gate_skip", package=name,
                          required_pci_vendor=req,
                          detected_display_vendors=sorted(detected_vendors))
    return kept


def _apply_eula_gate(in_tier, eula_by_name):
    """Omit packages whose recipe declares `eula_helper` from the
    non-interactive install set (PI-Z6). Returns the filtered dict; every
    omission is logged as an intentional policy decision with the
    post-install command the user runs to add the package interactively."""
    kept = {}
    for name, (ver, path) in in_tier.items():
        helper = eula_by_name.get(name)
        if helper:
            LOG.info("eula-gate: omitting %r from the non-interactive "
                     "install set — its license requires an interactive "
                     "accept (helper %r). Install it after first boot "
                     "with: pkm install %s", name, helper, name)
            trace.trace_event("eula_gate_omit", package=name,
                              eula_helper=helper,
                              post_install_command=f"pkm install {name}")
            continue
        kept[name] = (ver, path)
    return kept


def _order_install_set(archives, deps_by_name):
    """Order the install set by the runtime-dependency graph (pkm.deporder —
    the ONE sorter, shared with pkm's upgrade planner), so every package's
    in-set runtime deps are on the target before it installs and before its
    post-install hook fires. Ordering is DERIVED, never hand-listed.

    Within what the graph permits (the ready set at each rank),
    INSTALL_ORDER_ESSENTIALS are preferred first in declared order, then
    alphabetical — a PREFERENCE inside the graph's law, kept because the
    essentials closure (shell + coreutils + text tools) serves every
    script-shaped hook, not only its declared dependents. The graph outranks
    the list: an essential with an in-set dependency still waits for it.

    INSTALL_ORDER_LATE is forced last exactly as before (see its rationale
    above); any in-set package that runtime-depends on a late-forced package
    is loud-logged, never silently reordered under it.

    Dependency cycles (a corrupt/hand-edited graph) are appended as
    alphabetical groups after the acyclic prefix and loud-logged — the
    install proceeds; refusing to order would turn an index defect into an
    unbootable target. Missing essentials/lates are skipped silently — those
    lists are "preferred order if present" contracts."""
    from pkm.deporder import topological_order

    ess_rank = {n: i for i, n in enumerate(INSTALL_ORDER_ESSENTIALS)}

    # The preference tier is the essentials' transitive in-set dependency
    # CLOSURE, not the bare list: an essential's own libraries (readline and
    # ncurses under bash — the exact ge9b-10 failure) must be preferred just
    # as hard as the essential itself, or the alphabetical mass interleaves
    # ahead of them and the hook-interpreter environment assembles late.
    closure = set(n for n in ess_rank if n in archives)
    frontier = list(closure)
    while frontier:
        name = frontier.pop()
        for dep in (deps_by_name.get(name) or ()):
            if dep in archives and dep not in closure:
                closure.add(dep)
                frontier.append(dep)

    def ready_key(n):
        if n in closure:
            return (0, ess_rank.get(n, len(ess_rank)), n)
        return (1, 0, n)

    ordered_names, cycle_groups = topological_order(
        archives.keys(), deps_by_name, ready_sort_key=ready_key)
    for group in cycle_groups:
        LOG.warning("_order_install_set: dependency CYCLE among %r — group "
                    "appended after the acyclic prefix; the package graph "
                    "needs fixing", group)

    lates = [n for n in INSTALL_ORDER_LATE if n in archives]
    if lates:
        late_set = set(lates)
        for name in ordered_names:
            if name in late_set:
                continue
            blocked = late_set.intersection(deps_by_name.get(name) or ())
            if blocked:
                LOG.warning("_order_install_set: %r runtime-depends on "
                            "late-forced %r — it will install BEFORE its "
                            "dependency by the INSTALL_ORDER_LATE contract; "
                            "review that contract if this is load-bearing",
                            name, sorted(blocked))
        ordered_names = [n for n in ordered_names if n not in late_set]
        ordered_names.extend(lates)

    return [(n, archives[n][0], archives[n][1]) for n in ordered_names]


def compute_install_set_gap(groups, archive_dir, package_dir,
                            detected_vendors=None):
    """Return the sorted [(name, version), ...] of archives that are present on
    the media AND belong to a SELECTED tier, yet do NOT make it into the
    resolved install set for any reason OTHER than the hardware gate.

    A non-empty result is a silent-loss bug — a declared install-set package
    that would not install (the class that kept llama-cpp's engine off every
    early build and dropped websockets at G3-10). The preflight raises on it
    BEFORE any destructive write, so the operator sees a loud, named failure
    instead of a silently-incomplete system.

    Hardware-gated omissions (e.g. nvidia on non-NVIDIA HW) are LEGITIMATE and
    excluded — they are an intentional, logged policy decision, not a loss.
    """
    if not package_dir:
        return []
    tiers = set()
    for group_name in groups:
        group = GROUPS.get(group_name)
        if group:
            tiers.update(group["tiers"])
    if not tiers:
        return []

    archives = get_archives(archive_dir)
    tier_packages, gate_by_name, eula_by_name, _ = \
        _discover_tier_membership(Path(package_dir), tiers)

    # Expected: every staged archive whose name maps to a selected tier.
    expected = {name for name in archives if name in tier_packages}

    # Actual: the names the resolver will actually install.
    actual = {n for (n, _v, _p) in get_group_packages(
        groups, archive_dir, package_dir, detected_vendors)}

    # Legitimate hardware-gate drops are not losses.
    if detected_vendors is None:
        detected_vendors = detect_display_pci_vendors()
    gated = {name for name in expected
             if gate_by_name.get(name)
             and gate_by_name[name] not in detected_vendors}

    # PI-Z6: EULA-gated omissions are likewise an intentional, logged
    # policy decision (interactive-only installs), not a silent loss.
    eula_omitted = {name for name in expected if eula_by_name.get(name)}

    missing = expected - actual - gated - eula_omitted
    return sorted((name, archives[name][0]) for name in missing)


def reconcile_checksums(target, installed_names=None):
    """PKM-E: re-record installed file checksums from the live target filesystem
    after post-install hooks + signing have run, so `pkm verify` validates the
    true installed state (the MOK-signed kernel/UKI + hook-edited files) instead
    of false-flagging legitimate post-install mutations. Opens the target pkm.db
    the same way install_packages does. Returns the number of file rows updated
    (0 if the db is absent). Caller treats failure as non-fatal.

    installed_names: the resolved install-set names, as returned by
        install_packages. The reconcile is SCOPED to the union of those
        packages' owned file paths. This matters: an unscoped reconcile
        re-records every non-config file row on the target from disk, which
        re-blesses whatever is there as the recorded truth — and it runs
        immediately after the recipe post_install hooks have been sourced in a
        chroot of that target, so anything a hook wrote over another package's
        file is written in as that file's correct content. The scoped form
        covers exactly the same legitimate cases (signing and hook mutations
        both land on installed packages' own files) while leaving anything
        outside the install set to be judged by `pkm verify` on its merits.
        PackageDB.reconcile_checksums_from_live documents the same distinction
        and pkm's own installer already passes the scoped form.

        None keeps the whole-tree behaviour for callers that genuinely hold a
        known-good tree (fresh image assembly, tests); it is not what the
        installer passes."""
    db_path = Path(target) / "var" / "lib" / "igos" / "pkm.db"
    if not db_path.exists():
        return 0
    with PackageDB(str(db_path), root=target) as db:
        if installed_names is None:
            return db.reconcile_checksums_from_live()
        paths = []
        for name in installed_names:
            paths.extend(f["path"] for f in db.get_files(name)
                         if not f["is_dir"])
        if not paths:
            return 0
        return db.reconcile_checksums_from_live(paths=paths)


def install_packages(target, archive_dir, groups, package_dir=None,
                     progress_callback=None):
    """Install packages to a target root filesystem.

    Args:
        target: target root path (e.g., /mnt/target)
        archive_dir: path to .igos.tar.gz archives
        groups: list of group names to install
        package_dir: path to packages/ for tier mapping
        progress_callback: fn(current, total, name) called per package

    Returns:
        (success_count, fail_count, failed_packages, installed_names)
        installed_names is the resolved install-set name list (so the caller
        can run post-install hooks ONLY for packages actually installed).
    """
    packages = get_group_packages(groups, archive_dir, package_dir)
    total = len(packages)

    if total == 0:
        return 0, 0, [], []

    # Create pkm database on the target. Context-manager use guarantees
    # close() runs even if an installer.install() call raises mid-loop —
    # otherwise an exception leaks the SQLite handle (FD + WAL state).
    db_path = Path(target) / "var" / "lib" / "igos" / "pkm.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    success = 0
    failed = []

    trace.trace_event("packages_install_begin",
                      target=str(target), archive_dir=str(archive_dir),
                      groups=list(groups), total_packages=total)

    with PackageDB(str(db_path), root=target) as db:
        installer = PackageInstaller(db, root=target)

        # Build the install queue once so the per-package install() call can
        # enforce the supersede install-order invariant: a successor declaring
        # supersedes:[predecessor] must install AFTER its predecessor when both
        # are in the queue. Without this, ad-hoc ordering could let a successor
        # install first as a standard package, then the predecessor overwrites
        # the same paths, leaving pkm with a manifest inversion the user cannot
        # see. See pkm/installer.py install() and the Phase 4 RFC §4 design.
        queue_names = [pkg[0] for pkg in packages]

        for i, (name, version, archive_path) in enumerate(packages, 1):
            if progress_callback:
                progress_callback(i, total, name)

            t0 = time.monotonic()
            ok, msg = installer.install(
                name, archive_path=str(archive_path), queue=queue_names
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            trace.trace_event(
                "package_install",
                phase="packages",
                index=i, total=total,
                name=name, version=version,
                archive=str(archive_path),
                ok=ok, message=msg,
                duration_ms=duration_ms,
            )
            if ok:
                success += 1
            else:
                failed.append((name, msg))

    trace.trace_event("packages_install_end",
                      success_count=success, fail_count=len(failed),
                      failed=[name for name, _ in failed])

    return success, len(failed), failed, queue_names
