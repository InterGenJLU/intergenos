"""The shipped firewall opens DHCP and DNS to libvirt guests, and nothing else.

A host running libvirt NAT guests serves them DHCP and DNS from a host-side
dnsmasq listening on the virtual bridge. Under the shipped default-drop input
policy those requests were dropped, so guests came up with no address and no
resolver, and the only remedy was a rule typed in at runtime that did not
survive a reboot. packages/core/intergenos-firewall-defaults/nftables.conf now
carries the accept.

The whole risk of that change is scope creep, so these tests are mostly about
what must NOT have happened. The accepts must be bound to the bridge interface
class — never to an address, never to an interface-less rule that would open
these ports on every real network. They must live in the input chain, because
forwarding guest traffic onward is a separate capability that stays opt-in in
the fragment the libvirt package ships. And the rest of the default-deny
posture must be exactly as it was.

scripts/check-d011-compliance.sh already gates the posture lines from the
directive that governs this file, and it is bash run at build time. These are
the pytest half and they pin the carve-out itself, which that gate knows
nothing about.

Nothing here reads the network, needs privilege, or loads a ruleset. `nft
--check` would be the stronger assertion but it requires netlink access, so
these parse structurally instead.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent

CONF = REPO / "packages" / "core" / "intergenos-firewall-defaults" / "nftables.conf"
BUILD = REPO / "packages" / "core" / "intergenos-firewall-defaults" / "build.sh"
FRAGMENT = REPO / "packages" / "extra" / "libvirt" / "libvirt-nat-firewall.nft"

# The bridge-scoped accepts this cut adds, as (protocol, port).
CARVE_OUT = [("udp", "67"), ("udp", "53"), ("tcp", "53")]

# libvirt names the default NAT bridge virbr0 and later ones virbr1, virbr2...
BRIDGE_MATCH = re.compile(r'iifname\s+"virbr\*"')


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(text: str) -> list[str]:
    """Rule lines only. A comment that happens to contain rule-shaped words
    must never satisfy an assertion about rules."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(line)
    return out


def _chain_body(text: str, chain: str) -> list[str]:
    """Return the rule lines inside `chain <name> {` ... `}`.

    Brace-counted rather than regex-sliced, so a rule cannot be attributed to
    the wrong chain."""
    lines = _strip_comments(text)
    body: list[str] = []
    depth = 0
    collecting = False
    for line in lines:
        if not collecting and re.match(rf"^\s*chain\s+{re.escape(chain)}\s*\{{\s*$", line):
            collecting = True
            depth = 1
            continue
        if collecting:
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                break
            body.append(line.strip())
    assert collecting, f"chain {chain!r} not found in the ruleset"
    return body


@pytest.mark.parametrize("proto,port", CARVE_OUT)
def test_each_carve_out_rule_is_present_in_the_input_chain(proto, port):
    body = _chain_body(_text(CONF), "input")
    matches = [ln for ln in body
               if re.search(rf"{proto}\s+dport\s+{port}\b", ln) and ln.endswith("accept")]
    assert matches, (
        f"no {proto}/{port} accept in the input chain of {CONF.name}; libvirt guests "
        f"cannot reach the host-side dnsmasq without it")


@pytest.mark.parametrize("proto,port", CARVE_OUT)
def test_each_carve_out_rule_is_scoped_to_the_bridge_interface(proto, port):
    """The load-bearing assertion. An unscoped rule would open DHCP or DNS on
    every interface the host has, including whatever it is plugged into."""
    body = _chain_body(_text(CONF), "input")
    for line in body:
        if re.search(rf"{proto}\s+dport\s+{port}\b", line) and line.endswith("accept"):
            assert BRIDGE_MATCH.search(line), (
                f"the {proto}/{port} accept is not scoped to the libvirt bridge "
                f"interface: {line!r}")


def test_no_carve_out_rule_is_scoped_by_address_instead_of_interface():
    """An address-based match would trust whoever can put that address on the
    wire; the interface match cannot be spoofed from off-host."""
    body = _chain_body(_text(CONF), "input")
    for line in body:
        if any(re.search(rf"{p}\s+dport\s+{q}\b", line) for p, q in CARVE_OUT):
            assert not re.search(r"\b(ip|ip6)\s+(saddr|daddr)\b", line), (
                f"carve-out rule matches on an address rather than only the bridge "
                f"interface: {line!r}")


def test_the_carve_out_does_not_open_forwarding():
    """Answering a guest's DHCP and DNS is one capability; routing its traffic
    onward is another, and it stays opt-in in the libvirt fragment."""
    body = _chain_body(_text(CONF), "forward")
    assert not [ln for ln in body if ln.endswith("accept")], (
        f"the forward chain of {CONF.name} now contains an accept rule; forwarding "
        f"guest traffic is not part of the shipped defaults")


def test_the_default_deny_posture_is_otherwise_unchanged():
    text = _text(CONF)
    assert re.search(r"type filter hook input priority filter; policy drop;", text)
    assert re.search(r"type filter hook forward priority filter; policy drop;", text)
    assert re.search(r"type filter hook output priority filter; policy accept;", text)


def test_ssh_is_still_closed_by_default():
    """The one accept this ruleset has always refused. If a carve-out ever
    grows into a general 'open what the user needs' list, this is what notices."""
    body = _chain_body(_text(CONF), "input")
    assert not [ln for ln in body if re.search(r"tcp\s+dport\s+22\b", ln)], (
        "tcp/22 accept present in the shipped ruleset; SSH is closed by default")


def test_the_input_chain_opens_no_other_ports():
    """Whitelist assertion: every port-bearing accept in the input chain is one
    of the three this cut authorizes. Catches a future addition that lands
    without its own decision."""
    body = _chain_body(_text(CONF), "input")
    authorized = {(p, q) for p, q in CARVE_OUT}
    found = set()
    for line in body:
        m = re.search(r"\b(tcp|udp)\s+dport\s+(\d+)\b", line)
        if m and line.endswith("accept"):
            found.add((m.group(1), m.group(2)))
    assert found == authorized, (
        f"the input chain accepts ports {sorted(found)}; the authorized set is "
        f"{sorted(authorized)}")


def test_the_build_time_assert_still_guards_the_policy_lines():
    """build.sh refuses to package a ruleset whose policy drifted. The carve-out
    must not have disturbed those asserts."""
    text = _text(BUILD)
    assert "policy is not 'drop' per D-011" in text
    assert "tcp/22 (SSH) is allowed by default" in text


def test_the_libvirt_fragment_no_longer_claims_the_defaults_carry_no_bridge_rules():
    """Doc drift is caught by trying to cite the doc. The fragment used to say
    the shipped ruleset carries no rules for the bridge; that is now false for
    DHCP and DNS, and a reader deciding whether to arm it needs the truth."""
    text = _text(FRAGMENT)
    assert "carries no rules for libvirt's NAT bridge" not in text, (
        "the libvirt fragment still says the shipped ruleset has no bridge rules")
    assert "the shipped default-deny ruleset itself is unchanged" not in text, (
        "the libvirt fragment still claims the shipped ruleset is unchanged")
    assert "intergenos-firewall-defaults" in text, (
        "the fragment does not point at the package that now ships the DHCP/DNS accepts")


def test_the_fragment_still_carries_the_forward_rules_that_are_its_reason_to_exist():
    body = _chain_body(_text(FRAGMENT), "forward")
    assert [ln for ln in body if ln.endswith("accept")], (
        "the libvirt fragment's forward rules are gone; arming it would no longer "
        "route guest traffic and the fragment would have no purpose")
