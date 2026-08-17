# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Target-volume enumeration + honest labels + setup options (spec §4, §2.5;
addendum 2026-07-24).

Enumerates candidate backup targets and labels each one HONESTLY, so the setup
flow never over-promises. Detection resolves each candidate partition to its
physical parent disk through the LUKS/dm holder chain (lsblk PKNAME), so a
partition that merely shares the system's drive is not mislabelled as
disk-failure protection.

Two ratified target classes:
  * whole-volume  — Chronicle owns the whole POSIX volume.
  * directory     — Chronicle owns a single size-capped subtree
                    (<mount>/ChronicleBackups) on an existing POSIX volume;
                    retention's room check evaluates against the cap, not the
                    volume's free space (addendum A).

Non-POSIX volumes (FAT32/exFAT/NTFS) cannot hold hardlinks/ownership/xattrs, so
they are not usable as-is. The flow offers an HONEST remediation matrix: a
guided hand-off to GParted (shipped) to shrink + create ext4 for the resizable
filesystems, a whole-drive reformat, or choosing a different target — and
exFAT, which generally cannot be resized, is told the truth (addendum B).
Chronicle never performs partition surgery itself: the partitioning tool
partitions, the backup tool backs up.

The classification logic here is pure over a parsed device list, so it is fully
unit-testable without real disks; scan() is the thin lsblk wrapper.
"""

import json
import subprocess

# POSIX filesystems that can hold hardlinks + ownership + permissions + xattrs.
POSIX_FSTYPES = frozenset({
    "ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "jfs", "reiserfs",
})
# Filesystems that cannot — the non-POSIX class (spec §2.5).
NON_POSIX_FSTYPES = frozenset({
    "vfat", "fat", "fat12", "fat16", "fat32", "msdos", "exfat", "ntfs",
})
# The non-POSIX filesystems that a partition editor can shrink in place
# (freeing room for a new ext4 partition). exFAT is deliberately absent.
SHRINKABLE_NON_POSIX = frozenset({
    "vfat", "fat", "fat12", "fat16", "fat32", "msdos", "ntfs",
})

# Protection labels (spec §4).
LABEL_SEPARATE_DISK = "separate-disk"
LABEL_SAME_DISK = "same-disk-partition"
LABEL_SYSTEM_REFUSED = "system-volume"
# Fail-closed label: the scan could not establish the disk topology (e.g. the
# device-mapper chain or the root mount is not visible in the scan
# environment). A separate-disk claim is UNVERIFIABLE here, so it must never
# be made — measured live on an installed ge9b-12 system, where a sandboxed
# engine saw no /dev/mapper node, found no "/" mount, and labelled every
# volume (including the partition holding the encrypted root) as
# separate-disk full protection.
LABEL_UNKNOWN = "undetermined"

_PROTECTION_TEXT = {
    LABEL_SEPARATE_DISK:
        "Full protection — survives disk failure.",
    LABEL_SAME_DISK:
        "Protects against OS damage and mistakes, but NOT disk failure — this "
        "partition shares the drive your system is on.",
    LABEL_SYSTEM_REFUSED:
        "This is the system volume and cannot be used as a backup target.",
    LABEL_UNKNOWN:
        "Protection class could not be determined — the scan could not "
        "establish which physical disk this volume or the running system is "
        "on. No target can be safely offered from this scan.",
}

# Protection strength for sorting (separate disk first).
_PROTECTION_RANK = {
    LABEL_SEPARATE_DISK: 0,
    LABEL_SAME_DISK: 1,
    LABEL_UNKNOWN: 2,
    LABEL_SYSTEM_REFUSED: 3,
}


def classify_candidate(cand, system_disk, home_estimate_bytes, floor_bytes,
                       gparted_present, cap_default_bytes=None):
    """Classify one candidate volume. Pure — no I/O.

    Args:
        cand: dict with keys name (device node, e.g. /dev/sdb1), fstype,
            size_bytes, free_bytes, parent_disk (physical disk node),
            mountpoint (or None), is_system (bool — this is the running system
            volume).
        system_disk: the physical disk node the running system is on.
        home_estimate_bytes: estimated size of the home tree to protect.
        floor_bytes: a floor sufficient for config-state + several restore
            points.
        gparted_present: whether gparted is installed (for the non-POSIX flow).
        cap_default_bytes: default size cap to suggest for the directory class.

    Returns:
        a result dict describing the candidate, its protection label, its
        supported target options, and (for non-POSIX) the honest remediation
        matrix.
    """
    fstype = (cand.get("fstype") or "").lower()
    posix = fstype in POSIX_FSTYPES
    result = {
        "device": cand["name"],
        "fstype": fstype or "(none)",
        "size_bytes": cand.get("size_bytes"),
        "free_bytes": cand.get("free_bytes"),
        "parent_disk": cand.get("parent_disk"),
        "mountpoint": cand.get("mountpoint"),
        "posix": posix,
        "disqualified": None,
        "protection_label": None,
        "protection_text": None,
        "supported_targets": [],
        "remediation": None,
    }

    # Protection label (independent of POSIX-ness; a non-POSIX drive still has a
    # real disk-failure story to state honestly after remediation).
    # FAIL CLOSED on unknown topology: separate-disk is a positive claim and
    # may only be made when BOTH the system's disk and this candidate's disk
    # are known. When either is unresolvable, the label is "undetermined" and
    # the candidate is refused outright below — offering adoption (or worse,
    # a reformat flow) over a topology the scan cannot see risks the system's
    # own volume.
    if cand.get("is_system"):
        result["protection_label"] = LABEL_SYSTEM_REFUSED
    elif system_disk is None or not cand.get("parent_disk"):
        result["protection_label"] = LABEL_UNKNOWN
    elif cand["parent_disk"] == system_disk:
        result["protection_label"] = LABEL_SAME_DISK
    else:
        result["protection_label"] = LABEL_SEPARATE_DISK
    result["protection_text"] = _PROTECTION_TEXT[result["protection_label"]]

    # Hard disqualifiers (spec §4).
    if cand.get("is_system"):
        result["disqualified"] = "system volume — refused as a backup target"
        return result
    if result["protection_label"] == LABEL_UNKNOWN:
        result["disqualified"] = (
            "disk topology could not be established in this scan — refused "
            "so a volume of unknown provenance (possibly the system's own "
            "disk) is never offered as a backup target"
        )
        return result
    min_free = max(home_estimate_bytes or 0, floor_bytes or 0)
    free = cand.get("free_bytes")

    if not posix:
        # Non-POSIX: not usable as-is. Build the honest remediation matrix
        # rather than a bare "unusable" (addendum B).
        result["disqualified"] = (
            f"{fstype or 'unknown'} is not a POSIX filesystem — it cannot hold "
            f"hardlinks, ownership, or permissions, so it cannot be used as-is"
        )
        result["remediation"] = _non_posix_remediation(
            fstype, gparted_present
        )
        return result

    # POSIX but too small.
    if free is not None and free < min_free:
        result["disqualified"] = (
            f"not enough free space: {free} bytes free, need at least "
            f"{min_free} bytes (the home tree plus config-state and several "
            f"restore points)"
        )
        return result

    # Viable POSIX volume: offer BOTH ratified target classes (addendum A).
    subtree = f"{(cand.get('mountpoint') or '<mountpoint>')}/ChronicleBackups"
    result["supported_targets"] = [
        {
            "mode": "whole-volume",
            "description": "Dedicate the whole volume to Chronicle.",
        },
        {
            "mode": "directory",
            "subtree": subtree,
            "cap_bytes": cap_default_bytes,
            "description": (
                "Keep the volume for your own use; Chronicle owns only "
                f"{subtree} up to a size cap you set. Retention frees room "
                "against the cap, not the volume's free space."
            ),
        },
    ]
    return result


def _non_posix_remediation(fstype, gparted_present):
    """The honest option matrix for a non-POSIX drive (addendum B)."""
    options = []
    shrinkable = fstype in SHRINKABLE_NON_POSIX
    if shrinkable:
        if gparted_present:
            options.append({
                "action": "gparted-guided",
                "description": (
                    f"Shrink the existing {fstype} partition in GParted and "
                    "create an ext4 partition in the freed space, then let "
                    "Chronicle re-scan and adopt it. Chronicle never edits "
                    "partitions itself — GParted does the partitioning."
                ),
            })
        else:
            options.append({
                "action": "install-gparted",
                "package": "gparted",
                "description": (
                    "The guided shrink-and-create path needs the GParted "
                    "partition editor, which is not installed. Install the "
                    "'gparted' package first, then re-run setup."
                ),
            })
    else:
        # exFAT and anything else non-resizable: tell the truth (addendum B).
        options.append({
            "action": "not-resizable",
            "description": (
                f"{fstype} generally cannot be resized in place, so there is "
                "no shrink-and-create path. Use whole-drive reformat below, or "
                "choose a different target."
            ),
        })
    options.append({
        "action": "reformat-whole-drive",
        "description": (
            "Reformat the entire drive as ext4 — this ERASES everything on it. "
            "Requires explicit confirmation."
        ),
    })
    options.append({
        "action": "choose-different-target",
        "description": "Pick a different volume that is already POSIX.",
    })
    return {"fstype": fstype, "shrinkable": shrinkable,
            "gparted_present": gparted_present, "options": options}


def sort_by_protection(results):
    """Sort candidates by protection strength (separate disk first), then by
    device name for stability."""
    return sorted(
        results,
        key=lambda r: (_PROTECTION_RANK.get(r["protection_label"], 9),
                       r["device"]),
    )


# --------------------------------------------------------------------------
# lsblk wrapper (the only impure part)
# --------------------------------------------------------------------------


def _flatten(nodes, acc):
    for n in nodes:
        acc[n.get("name")] = n
        if n.get("children"):
            _flatten(n["children"], acc)
    return acc


def _resolve_parent_disk(node, by_name):
    """Walk the PKNAME chain up to the TYPE=disk ancestor (through partitions,
    LUKS/dm holders). Returns the disk's device path, or None."""
    seen = set()
    cur = node
    while cur is not None and cur.get("name") not in seen:
        seen.add(cur.get("name"))
        if cur.get("type") == "disk":
            return cur.get("path") or ("/dev/" + cur.get("name"))
        pk = cur.get("pkname")
        if not pk:
            return None
        cur = by_name.get(pk)
    return None


def scan(home_estimate_bytes, floor_bytes, gparted_present=None,
         cap_default_bytes=None, _lsblk_json=None):
    """Enumerate candidate targets from lsblk and classify them.

    Args:
        home_estimate_bytes, floor_bytes: size gates (spec §4).
        gparted_present: override; None => probe for the gparted binary.
        cap_default_bytes: default directory-class cap to suggest.
        _lsblk_json: injected lsblk JSON (tests); None => run lsblk.

    Returns:
        candidates sorted by protection strength.
    """
    if gparted_present is None:
        gparted_present = _which("gparted")
    if _lsblk_json is None:
        out = subprocess.run(
            ["lsblk", "-J", "-b", "-o",
             "NAME,PATH,PKNAME,TYPE,FSTYPE,SIZE,FSAVAIL,MOUNTPOINT"],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(out.stdout or "{}")
    else:
        data = _lsblk_json

    by_name = _flatten(data.get("blockdevices", []), {})

    # Physical disk of the running root ("/").
    system_disk = None
    for node in by_name.values():
        if node.get("mountpoint") == "/":
            system_disk = _resolve_parent_disk(node, by_name)
            break

    results = []
    for node in by_name.values():
        ntype = node.get("type")
        if ntype not in ("part", "crypt", "lvm"):
            continue
        # Only leaf usable filesystems (skip a partition that only HOLDS a
        # crypt/lvm child — its child is the mountable fs).
        if node.get("children"):
            continue
        dev = node.get("path") or ("/dev/" + node.get("name", ""))
        parent = _resolve_parent_disk(node, by_name)
        # System-volume refusal covers the boot partitions on the system disk
        # too (/boot, /boot/efi): shrinking or reformatting them is system
        # surgery, not backup-target remediation.
        is_system = node.get("mountpoint") == "/" or (
            parent is not None and parent == system_disk
            and node.get("mountpoint") in ("/", "/boot", "/boot/efi")
        )
        cand = {
            "name": dev,
            "fstype": node.get("fstype"),
            "size_bytes": _int_or_none(node.get("size")),
            "free_bytes": _int_or_none(node.get("fsavail")),
            "parent_disk": parent,
            "mountpoint": node.get("mountpoint"),
            "is_system": is_system,
        }
        results.append(classify_candidate(
            cand, system_disk, home_estimate_bytes, floor_bytes,
            gparted_present, cap_default_bytes,
        ))
    return sort_by_protection(results)


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _which(name):
    import shutil
    return shutil.which(name) is not None
