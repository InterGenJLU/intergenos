"""GATE 10 — packet-filter tooling on the built image (section 9 line 9).

WHAT COMPOSITION PROPERTY THIS CATCHES. Package verification confirms that the
firewall binaries and their alternatives symlinks are installed. It does not confirm
that the backend those symlinks point at is one the shipped kernel supports. On the
released image ``/usr/sbin/iptables`` resolves to the legacy multi-call binary while
the kernel ships no ``ip_tables`` modules at all, so every program that shells out to
``iptables`` fails with a module-not-found error. The mesh networking daemon cannot
create its packet-filter chains; container and virtual-machine tooling fail the same
way. The newer backend binary IS installed and two sibling tools already default to
it — only these two symlinks point at the legacy one.

WHY THIS GATE DOES NOT RUN ``iptables -L``. Listing chains needs a capability an
ordinary account does not have, so an unprivileged run would fail for a reason that
has nothing to do with the defect and would report a wrong-reason red. Everything
asserted here is readable without privilege: where the symlinks point, and which
modules the running kernel has.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import pytest

FRONTENDS = ["/usr/sbin/iptables", "/usr/sbin/ip6tables",
             "/usr/sbin/arptables", "/usr/sbin/ebtables"]


def _resolve(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return "<absent>"
    return os.path.realpath(p)


@pytest.fixture(scope="module")
def kernel_modules() -> dict[str, int]:
    """Count the netfilter modules the RUNNING kernel actually ships."""
    release = platform.release()
    moddir = Path("/lib/modules") / release
    if not moddir.is_dir():
        pytest.fail(
            f"The running kernel's module directory {moddir} does not exist, so this "
            "gate cannot say which packet-filter backends the kernel supports.")
    counts = {"ip_tables": 0, "nf_tables": 0}
    for path in moddir.rglob("*"):
        name = path.name
        if not (name.endswith(".ko") or name.endswith(".ko.zst")
                or name.endswith(".ko.xz") or name.endswith(".ko.gz")):
            continue
        stem = name.split(".ko")[0]
        if stem == "ip_tables" or stem.startswith("iptable_"):
            counts["ip_tables"] += 1
        if stem == "nf_tables" or stem.startswith("nft_"):
            counts["nf_tables"] += 1
    return counts


def test_the_default_firewall_frontend_matches_a_backend_the_kernel_supports(kernel_modules):
    targets = {f: _resolve(f) for f in FRONTENDS}
    legacy = {f: t for f, t in targets.items() if t.endswith("xtables-legacy-multi")}
    nft_binary_present = Path("/usr/sbin/xtables-nft-multi").exists()

    report = ["", "PACKET-FILTER FRONTENDS AND WHAT THEY RESOLVE TO", ""]
    for f, t in targets.items():
        report.append(f"  {f:24} -> {t}")
    report.append("")
    report.append(f"  kernel {platform.release()} carries "
                  f"{kernel_modules['ip_tables']} iptables modules and "
                  f"{kernel_modules['nf_tables']} nftables modules")
    report.append(f"  the newer backend binary /usr/sbin/xtables-nft-multi is "
                  f"{'installed' if nft_binary_present else 'NOT installed'}")
    report.append("")
    report.append(
        "Every frontend pointing at the legacy backend on a kernel with zero iptables "
        "modules fails at the first call. The mesh networking daemon's own health "
        "surface reports the exact failing command, and no chain it needs exists.")

    broken = [f for f, _t in legacy.items() if kernel_modules["ip_tables"] == 0]
    assert not broken, "\n".join(report)


def test_the_installed_backends_are_consistent_with_each_other():
    """All four frontends should agree on a backend, or the split is a mistake.

    Failed separately from the kernel-support test because it is the evidence that the
    split is a packaging slip rather than a decision: two of the four already point at
    the newer backend.
    """
    targets = {f: _resolve(f) for f in FRONTENDS if Path(f).exists()}
    backends = sorted(set(targets.values()))
    assert len(backends) <= 1, (
        "\nThe shipped packet-filter frontends do not agree on a backend:\n"
        + "\n".join(f"  {f:24} -> {t}" for f, t in targets.items()) +
        "\nTwo of them already resolve to the newer backend, which is what makes the "
        "other two look like a symlink that was never updated rather than a choice."
    )


def test_a_consumer_of_the_firewall_tooling_reports_its_own_health():
    """If the mesh networking daemon is installed, its health surface is read.

    Reported rather than asserted-away: this is the concrete consumer whose failure the
    two tests above predict. When the daemon is not installed the check states that
    plainly instead of passing quietly.
    """
    tailscale = Path("/usr/bin/tailscale")
    if not tailscale.exists():
        pytest.skip("NOT VERIFIED: no mesh networking client is installed on this "
                    "machine, so no consumer of the firewall tooling was exercised.")
    proc = subprocess.run(["/usr/bin/tailscale", "status", "--json"],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        pytest.skip("NOT VERIFIED: the mesh networking client did not answer "
                    f"(exit {proc.returncode}); its health surface was not read.")
    import json
    try:
        status = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"The mesh client's status output is not JSON: {exc}")
    health = status.get("Health") or []
    firewall_complaints = [h for h in health
                           if "iptables" in str(h) or "ip_tables" in str(h)]
    assert not firewall_complaints, (
        "\nA shipped consumer of the firewall tooling is reporting the failure this "
        "gate predicts:\n" + "\n".join(f"  {h}" for h in firewall_complaints)
    )
