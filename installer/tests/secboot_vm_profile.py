"""SecBoot VM profile — throwaway libvirt/QEMU VM for boot-chain empirical tests.

Stands up a Secure-Boot-enabled VM (OVMF + swtpm TPM2) suitable for booting
an installed InterGenOS target. Purpose: empirically validate GRUB behavior
against our PE/COFF sbsigned chain (kernel + grub signed with our MOK) in
an isolated environment before Monday/Tuesday hardware install.

Load-bearing question this profile is built to answer:
  Does GRUB `check_signatures=enforce` refuse our PE/COFF sbsigned kernel
  because `enforce` targets GRUB's PGP scheme, not PE-verification?
  (Hypothesis per claude-main 2026-04-20 16:02Z.)

Scope (what this file provides):
  - check_prerequisites(): host-level precheck for libvirtd/virt-install/OVMF/swtpm
  - provision(): create VM with OVMF secboot firmware + swtpm TPM2 + SB on
  - destroy(): tear down VM + scrub nvram + remove swtpm state (idempotent)
  - VMProfile dataclass: captures paths/state for dependent tests

NOT in scope (separate modules will consume this):
  - Actual GRUB check_signatures variants (test_grub_check_signatures.py, next)
  - Class 2 runtime SB state probing
  - Install-time MOK enrollment flow

Prerequisites (check_prerequisites() enforces):
  - libvirtd active (systemctl is-active libvirtd)
  - virt-install, virsh, swtpm, qemu-system-x86_64 in PATH
  - /usr/share/OVMF/OVMF_CODE_4M.secboot.fd
  - /usr/share/OVMF/OVMF_VARS_4M.ms.fd (MS KEK/db populated — we want the
    real MS-signed VARS so we can test against realistic SB policy)

Skip-gate verified: laptop (no libvirtd) -> clean skip; ubuntu2404 (full
stack: libvirtd active, OVMF secboot + swtpm 0.7.3 + qemu 8.2.2) -> run.

Usage (standalone CLI for Phase A manual spin-up):
    python3 -m installer.tests.secboot_vm_profile --check-only
    python3 -m installer.tests.secboot_vm_profile \\
        --name intergenos-secboot-test \\
        --disk /var/lib/libvirt/images/intergenos-target.qcow2 \\
        --iso /var/lib/libvirt/images/intergenos-installer.iso
    python3 -m installer.tests.secboot_vm_profile --destroy --name intergenos-secboot-test

Exit codes:
    0   operation succeeded (or VM never existed, for --destroy)
    1   libvirt/virt-install error
    2   prerequisites missing
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Optional

# --- Host-level SecBoot firmware + TPM paths ---------------------------------

DEFAULT_OVMF_CODE_SECBOOT = Path("/usr/share/OVMF/OVMF_CODE_4M.secboot.fd")
DEFAULT_OVMF_VARS_MS = Path("/usr/share/OVMF/OVMF_VARS_4M.ms.fd")

DEFAULT_NVRAM_DIR = Path("/var/lib/libvirt/qemu/nvram")
DEFAULT_SWTPM_BASE = Path("/var/lib/libvirt/swtpm")

# VM naming + libvirt defaults
DEFAULT_VM_NAME = "intergenos-secboot-test"
DEFAULT_MEMORY_MB = 4096
DEFAULT_VCPUS = 2

# Name guard: libvirt domain names tolerate a lot, but we want tight control
# to keep subprocess argv unambiguous and avoid foot-guns if the name is
# ever templated into a path. Mirrors mok.py M1-style validation.
VM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


# --- Result types ------------------------------------------------------------


@dataclass
class VMProfile:
    """Describes a provisioned SecBoot VM."""
    name: str
    memory_mb: int
    vcpus: int
    disk_path: Optional[str]
    iso_path: Optional[str]
    ovmf_code: str
    ovmf_vars_template: str
    nvram_path: str
    swtpm_dir: str
    mac: Optional[str] = None
    domain_uuid: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PrereqCheck:
    """Host prerequisite check result."""
    ok: bool
    missing: List[str] = field(default_factory=list)

    def reason(self) -> str:
        if self.ok:
            return ""
        return "missing prerequisites: " + "; ".join(self.missing)


# --- Prerequisite checking ---------------------------------------------------


def check_prerequisites(
    ovmf_code: Path = DEFAULT_OVMF_CODE_SECBOOT,
    ovmf_vars: Path = DEFAULT_OVMF_VARS_MS,
) -> PrereqCheck:
    """Verify libvirtd + virt-install + OVMF secboot + swtpm are available."""
    missing: List[str] = []

    for tool in ("virt-install", "virsh", "swtpm", "qemu-system-x86_64"):
        if not shutil.which(tool):
            missing.append(f"{tool} not in PATH")

    if not ovmf_code.exists():
        missing.append(f"OVMF secboot code not found at {ovmf_code}")
    if not ovmf_vars.exists():
        missing.append(f"OVMF MS VARS template not found at {ovmf_vars}")

    try:
        proc = subprocess.run(
            ["systemctl", "is-active", "libvirtd"],
            capture_output=True, text=True, timeout=5,
        )
        state = proc.stdout.strip() or "unknown"
        if state != "active":
            missing.append(f"libvirtd not active (state: {state})")
    except FileNotFoundError:
        missing.append("systemctl not in PATH")
    except subprocess.TimeoutExpired:
        missing.append("systemctl timeout probing libvirtd")

    return PrereqCheck(ok=not missing, missing=missing)


def _guard_name(name: str) -> None:
    if not VM_NAME_RE.match(name):
        raise ValueError(
            f"invalid VM name {name!r}: must match {VM_NAME_RE.pattern}"
        )


# --- VM lifecycle ------------------------------------------------------------


def _virsh_domain_exists(name: str) -> bool:
    proc = subprocess.run(
        ["virsh", "dominfo", name],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


def _virsh_destroy_if_running(name: str) -> None:
    proc = subprocess.run(
        ["virsh", "domstate", name],
        capture_output=True, text=True,
    )
    if proc.returncode == 0 and "running" in proc.stdout.lower():
        subprocess.run(
            ["virsh", "destroy", name],
            capture_output=True, text=True,
        )


def _lookup_mac(name: str) -> Optional[str]:
    proc = subprocess.run(
        ["virsh", "domiflist", name],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        parts = line.split()
        # domiflist columns: Interface  Type  Source  Model  MAC
        if len(parts) >= 5 and ":" in parts[-1]:
            return parts[-1]
    return None


def _lookup_uuid(name: str) -> Optional[str]:
    proc = subprocess.run(
        ["virsh", "domuuid", name],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def destroy(
    name: str = DEFAULT_VM_NAME,
    swtpm_base: Path = DEFAULT_SWTPM_BASE,
) -> int:
    """Tear down a VM: stop, undefine with --nvram, scrub swtpm state.

    Idempotent — returns 0 if the VM never existed. Still scrubs any stale
    swtpm state dir in that case.

    Returns 0 on clean teardown, 1 on libvirt error.
    """
    _guard_name(name)

    if not _virsh_domain_exists(name):
        swtpm_dir = swtpm_base / name
        if swtpm_dir.exists():
            shutil.rmtree(swtpm_dir, ignore_errors=True)
        return 0

    _virsh_destroy_if_running(name)

    # Try undefine with --nvram --tpm (newer libvirt). Fall back to --nvram
    # only if --tpm unsupported on the host's libvirt version.
    proc = subprocess.run(
        ["virsh", "undefine", name, "--nvram", "--tpm"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        proc = subprocess.run(
            ["virsh", "undefine", name, "--nvram"],
            capture_output=True, text=True,
        )
    if proc.returncode != 0:
        print(
            f"error: virsh undefine {name} failed: {proc.stderr.strip()}",
            file=sys.stderr,
        )
        return 1

    swtpm_dir = swtpm_base / name
    if swtpm_dir.exists():
        shutil.rmtree(swtpm_dir, ignore_errors=True)

    return 0


def provision(
    name: str = DEFAULT_VM_NAME,
    disk_path: Optional[Path] = None,
    iso_path: Optional[Path] = None,
    memory_mb: int = DEFAULT_MEMORY_MB,
    vcpus: int = DEFAULT_VCPUS,
    ovmf_code: Path = DEFAULT_OVMF_CODE_SECBOOT,
    ovmf_vars: Path = DEFAULT_OVMF_VARS_MS,
    nvram_dir: Path = DEFAULT_NVRAM_DIR,
    swtpm_base: Path = DEFAULT_SWTPM_BASE,
) -> VMProfile:
    """Create SecBoot VM (OVMF + swtpm TPM2 + SB enabled).

    Starts fresh: any pre-existing VM with `name` is destroyed first. The
    caller supplies the disk; we do not reformat or overwrite disk content
    beyond libvirt's normal provisioning behavior.

    Raises:
        ValueError       — name fails VM_NAME_RE guard
        RuntimeError     — prereq check fails, or virt-install exits non-zero
    """
    _guard_name(name)

    prereqs = check_prerequisites(ovmf_code, ovmf_vars)
    if not prereqs.ok:
        raise RuntimeError(prereqs.reason())

    # Clean slate for repeatability
    destroy(name, swtpm_base=swtpm_base)

    nvram_path = nvram_dir / f"{name}_VARS.fd"
    swtpm_dir = swtpm_base / name

    cmd: List[str] = [
        "virt-install",
        "--name", name,
        "--memory", str(memory_mb),
        "--vcpus", str(vcpus),
        "--os-variant", "linux2024",
        "--arch", "x86_64",
        "--machine", "q35",
        # SecBoot firmware: MS VARS template (contains MS KEK + db) +
        # loader.secure=yes so OVMF enforces signature checking on loaded
        # PE images at boot.
        "--boot",
        (
            f"loader={ovmf_code},loader.readonly=yes,loader.type=pflash,"
            f"loader.secure=yes,nvram.template={ovmf_vars}"
        ),
        # SMM required for SB to be effective — prevents write access to
        # VARS outside SMM mode, closing the classic OVMF SB bypass.
        "--features", "smm.state=on",
        # swtpm-backed TPM 2.0 emulator; libvirt manages the swtpm process
        # under swtpm_base/<name>/ automatically.
        "--tpm", "backend.type=emulator,backend.version=2.0,model=tpm-crb",
        "--network", "network=default,model=virtio",
        "--graphics", "none",
        "--console", "pty,target_type=serial",
        "--noautoconsole",
    ]

    if disk_path:
        cmd += ["--disk", f"path={disk_path},bus=virtio,format=qcow2"]
    else:
        # Firmware-only probe: give virt-install a small scratch disk so it
        # doesn't refuse to define a diskless domain.
        cmd += ["--disk", "size=2,bus=virtio,format=qcow2"]

    if iso_path:
        cmd += ["--cdrom", str(iso_path)]
    else:
        # No install media — import an existing disk (or the scratch one)
        # straight into the firmware. Phase A uses this to drop into the
        # EFI shell and experiment with check_signatures manually.
        cmd += ["--import"]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"virt-install failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    return VMProfile(
        name=name,
        memory_mb=memory_mb,
        vcpus=vcpus,
        disk_path=str(disk_path) if disk_path else None,
        iso_path=str(iso_path) if iso_path else None,
        ovmf_code=str(ovmf_code),
        ovmf_vars_template=str(ovmf_vars),
        nvram_path=str(nvram_path),
        swtpm_dir=str(swtpm_dir),
        mac=_lookup_mac(name),
        domain_uuid=_lookup_uuid(name),
    )


# --- CLI ---------------------------------------------------------------------


def _render_profile_text(profile: VMProfile) -> str:
    return "\n".join([
        f"provisioned SecBoot VM: {profile.name}",
        f"  UUID:    {profile.domain_uuid or '<unknown>'}",
        f"  MAC:     {profile.mac or '<unknown>'}",
        f"  memory:  {profile.memory_mb} MB",
        f"  vcpus:   {profile.vcpus}",
        f"  disk:    {profile.disk_path or '<scratch 2GB>'}",
        f"  iso:     {profile.iso_path or '<none, --import>'}",
        f"  nvram:   {profile.nvram_path}",
        f"  swtpm:   {profile.swtpm_dir}",
    ])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Provision / tear down a SecBoot VM for Forge boot-chain tests",
    )
    parser.add_argument("--name", default=DEFAULT_VM_NAME,
                        help=f"VM name (default: {DEFAULT_VM_NAME})")
    parser.add_argument("--disk", default=None,
                        help="path to qcow2 disk (optional; scratch 2GB otherwise)")
    parser.add_argument("--iso", default=None,
                        help="path to install ISO (optional; --import otherwise)")
    parser.add_argument("--memory-mb", type=int, default=DEFAULT_MEMORY_MB)
    parser.add_argument("--vcpus", type=int, default=DEFAULT_VCPUS)
    parser.add_argument("--destroy", action="store_true",
                        help="destroy VM instead of provisioning")
    parser.add_argument("--check-only", action="store_true",
                        help="only probe host prerequisites; do not touch libvirt")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of human-readable text")
    args = parser.parse_args(argv)

    try:
        _guard_name(args.name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check_only:
        prereqs = check_prerequisites()
        if args.json:
            print(json.dumps(
                {"ok": prereqs.ok, "missing": prereqs.missing}, indent=2,
            ))
        elif prereqs.ok:
            print("prerequisites: OK")
        else:
            print("prerequisites: MISSING")
            for item in prereqs.missing:
                print(f"  - {item}")
        return 0 if prereqs.ok else 2

    if args.destroy:
        return destroy(args.name)

    prereqs = check_prerequisites()
    if not prereqs.ok:
        print(f"error: {prereqs.reason()}", file=sys.stderr)
        return 2

    disk = Path(args.disk).resolve() if args.disk else None
    iso = Path(args.iso).resolve() if args.iso else None

    try:
        profile = provision(
            name=args.name,
            disk_path=disk,
            iso_path=iso,
            memory_mb=args.memory_mb,
            vcpus=args.vcpus,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(profile.to_dict(), indent=2))
    else:
        print(_render_profile_text(profile))

    return 0


if __name__ == "__main__":
    sys.exit(main())
