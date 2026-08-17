"""The D-011 gates refuse an accept rule nobody approved.

Before this, both D-011 gates only asked whether the accept rules basic
networking needs were PRESENT. That direction cannot see an addition: adding a
rule that opened a new port to the shipped default-deny ruleset, or widening an
existing rule until it lost its interface scope, produced byte-identical gate
output. The gate that blocks image creation could not fail on the change most
likely to weaken the posture, which made a passing gate no evidence at all
about that class.

scripts/lib/d011-accept-list.sh now records the authorized accept rules once.
scripts/check-d011-compliance.sh checks the recipe against it and
scripts/check-d011-runtime.sh checks the conf actually deployed into the
chroot, which is the one that can differ from the recipe if some package's
post_install rewrites /etc/nftables.conf.

These tests call the REAL scripts against synthetic trees rather than
reimplementing their logic, and they read the approved list out of the library
rather than restating it — a second copy of the list would be the next thing to
drift.

Nothing here reads the network, needs privilege, binds a port, or loads a
ruleset.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent

LIB = REPO / "scripts" / "lib" / "d011-accept-list.sh"
SOURCE_GATE = REPO / "scripts" / "check-d011-compliance.sh"
RUNTIME_GATE = REPO / "scripts" / "check-d011-runtime.sh"
CONF = REPO / "packages" / "core" / "intergenos-firewall-defaults" / "nftables.conf"
FRAGMENT = REPO / "packages" / "extra" / "libvirt" / "libvirt-nat-firewall.nft"

# Repo-relative paths a reader could be sent to. Only the two D-011 gates are
# asserted on here; the same citation exists in other gates and is reported
# separately rather than changed by this cut.
D011_GATES = (SOURCE_GATE, RUNTIME_GATE)


def _run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", *argv], capture_output=True, text=True, timeout=120)


def _sibling_chain_body(text: str, chain: str) -> list[str]:
    """The brace-counted chain reader from the sibling firewall test, loaded by
    file path rather than copied — a second copy is a thing that drifts. Loaded
    the way tests/preflight/test_gate_positive_inventory.py loads scripts."""
    import importlib.util
    path = Path(__file__).resolve().parent / "test_firewall_libvirt_bridge_accept.py"
    spec = importlib.util.spec_from_file_location("_d011_sibling_chain_reader", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._chain_body(text, chain)


def _approved_accepts() -> list[str]:
    """The approved list, read out of the library itself.

    Restating it here would create the second copy this cut exists to avoid,
    so the list is extracted from the array literal in the shell source.
    """
    text = LIB.read_text(encoding="utf-8")
    m = re.search(r"^APPROVED_ACCEPTS=\((.*?)^\)", text, re.S | re.M)
    assert m, "APPROVED_ACCEPTS array not found in scripts/lib/d011-accept-list.sh"
    entries = re.findall(r"^\s*'([^']+)'\s*$", m.group(1), re.M)
    assert entries, "APPROVED_ACCEPTS parsed as empty; the gates would certify nothing"
    return entries


def _synthetic_repo(tmp_path: Path, conf_text: str) -> Path:
    """A minimal tree the source gate can be run against.

    The gate resolves its repo root from its own location, so a copy of the
    script in <tmp>/scripts makes <tmp> the tree under test.
    """
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "packages" / "core" / "intergenos-firewall-defaults").mkdir(parents=True)
    (tmp_path / "packages" / "core" / "nftables").mkdir(parents=True)
    shutil.copy2(SOURCE_GATE, tmp_path / "scripts" / SOURCE_GATE.name)
    # Copied only if it exists, so that against a tree without the approved
    # list these tests fail on what the gate DID — "it passed a ruleset
    # carrying an unapproved accept" — rather than on a missing file. The
    # failure has to name the defect, not the scaffolding.
    if LIB.exists():
        shutil.copy2(LIB, tmp_path / "scripts" / "lib" / LIB.name)
    shutil.copy2(REPO / "packages" / "core" / "nftables" / "build.sh",
                 tmp_path / "packages" / "core" / "nftables" / "build.sh")
    shutil.copy2(REPO / "packages" / "core" / "nftables" / "90-nftables.preset",
                 tmp_path / "packages" / "core" / "nftables" / "90-nftables.preset")
    (tmp_path / "packages" / "core" / "intergenos-firewall-defaults"
     / "nftables.conf").write_text(conf_text, encoding="utf-8")
    return tmp_path


def _synthetic_chroot(tmp_path: Path, conf_text: str) -> Path:
    """A minimal chroot the runtime gate can be run against."""
    root = tmp_path / "chroot"
    (root / "etc" / "systemd" / "system" / "multi-user.target.wants").mkdir(parents=True)
    (root / "usr" / "lib" / "systemd" / "system").mkdir(parents=True)
    (root / "etc" / "nftables.conf").write_text(conf_text, encoding="utf-8")
    (root / "usr" / "lib" / "systemd" / "system" / "nftables.service").write_text(
        "[Unit]\n", encoding="utf-8")
    (root / "etc" / "systemd" / "system" / "multi-user.target.wants"
     / "nftables.service").symlink_to("/usr/lib/systemd/system/nftables.service")
    return root


def _run_source_gate(tmp_path: Path, conf_text: str) -> subprocess.CompletedProcess:
    tree = _synthetic_repo(tmp_path, conf_text)
    return _run(str(tree / "scripts" / SOURCE_GATE.name))


def _run_runtime_gate(tmp_path: Path, conf_text: str) -> subprocess.CompletedProcess:
    return _run(str(RUNTIME_GATE), str(_synthetic_chroot(tmp_path, conf_text)))


# --- the shipped ruleset agrees with the recorded list ----------------------

def test_the_shipped_ruleset_accepts_are_exactly_the_approved_list():
    """The recorded list and the ruleset are two artifacts; if they disagree,
    one of them is wrong and the gate is enforcing the wrong thing."""
    rules = _run("-c", f'source "{LIB}"; d011_extract_rules "{CONF}"')
    assert rules.returncode == 0, rules.stderr
    accepts = [ln for ln in rules.stdout.splitlines() if ln.endswith("accept")]
    assert sorted(accepts) == sorted(_approved_accepts()), (
        "the shipped ruleset's accept rules and APPROVED_ACCEPTS have diverged")


def test_the_extractor_reports_an_unterminated_statement_rather_than_dropping_it(tmp_path):
    """A ruleset the reader cannot parse must never be certified as clean."""
    broken = tmp_path / "broken.conf"
    broken.write_text(
        "table inet filter {\n  chain input {\n    icmpv6 type {\n      nd-router-advert\n",
        encoding="utf-8")
    out = _run("-c", f'source "{LIB}"; d011_extract_rules "{broken}"')
    assert "UNTERMINATED:" in out.stdout, out.stdout


def test_the_reader_and_a_dumb_line_count_agree_on_the_shipped_ruleset():
    """Two independent instruments on the same file. They must agree, and the
    gates fail when they do not — a malformed set can swallow a chain's closing
    brace and shift the reader past a rule it would then never check."""
    stmts = _run("-c", f'source "{LIB}"; d011_extract_rules "{CONF}"')
    read = len([ln for ln in stmts.stdout.splitlines() if ln.endswith("accept")])
    counted = _run("-c", f'source "{LIB}"; d011_count_accept_lines "{CONF}"')
    assert read == int(counted.stdout.strip()) == len(_approved_accepts())


def test_the_source_gate_fails_when_its_two_instruments_disagree(tmp_path):
    """Proven by making them disagree: a set brace that eats the input chain's
    closing brace leaves a later accept invisible to the reader."""
    text = CONF.read_text(encoding="utf-8")
    broken = text.replace(
        '        iifname "virbr*" tcp dport 53 accept',
        '        icmpv6 type {\n        iifname "virbr*" tcp dport 53 accept', 1)
    proc = _run_source_gate(tmp_path, broken)
    assert proc.returncode == 1, proc.stdout
    assert "disagree" in proc.stdout, proc.stdout
    assert "9 accept statements read, 10 accept lines" in proc.stdout, proc.stdout


# --- the source gate fails on every way an accept can be added -------------

MUTATIONS = [
    pytest.param(
        ('        iifname "virbr*" tcp dport 53 accept',
         '        iifname "virbr*" tcp dport 53 accept\n        tcp dport 8080 accept'),
        "tcp dport 8080 accept", id="a-new-port-is-opened"),
    pytest.param(
        ('        iifname "virbr*" udp dport 67 accept',
         '        udp dport 67 accept'),
        "udp dport 67 accept", id="an-approved-rule-loses-its-interface-scope"),
    pytest.param(
        ('        # No default forward rules.',
         '        iifname "virbr*" accept\n        # No default forward rules.'),
        'iifname "virbr*" accept', id="an-accept-appears-in-the-forward-chain"),
    pytest.param(
        ('        iifname "virbr*" udp dport 53 accept',
         '        iifname "virbr0" udp dport 53 accept'),
        'iifname "virbr0" udp dport 53 accept',
        id="the-bridge-class-is-narrowed-back-to-one-bridge"),
    pytest.param(
        ('        iif "lo" accept',
         '        ip saddr 10.0.0.0/8 udp dport 53 accept\n        iif "lo" accept'),
        "ip saddr 10.0.0.0/8 udp dport 53 accept",
        id="an-address-scoped-accept-is-added"),
    pytest.param(
        ('        ip6 nexthdr ipv6-icmp icmpv6 type packet-too-big accept',
         '        ip6 nexthdr ipv6-icmp icmpv6 accept'),
        "ip6 nexthdr ipv6-icmp icmpv6 accept",
        id="an-icmp-rule-is-widened-to-every-type"),
]


@pytest.mark.parametrize("mutation,expected", MUTATIONS)
def test_the_source_gate_refuses_an_unapproved_accept(tmp_path, mutation, expected):
    old, new = mutation
    text = CONF.read_text(encoding="utf-8")
    assert old in text, f"the ruleset no longer contains {old!r}; this test is stale"
    proc = _run_source_gate(tmp_path, text.replace(old, new, 1))
    assert proc.returncode == 1, (
        f"the gate passed a ruleset carrying {expected!r}:\n{proc.stdout}")
    assert expected in proc.stdout, (
        f"the gate failed but did not name the offending rule:\n{proc.stdout}")


@pytest.mark.parametrize("mutation,expected", MUTATIONS[:3])
def test_the_runtime_gate_refuses_an_unapproved_accept(tmp_path, mutation, expected):
    """Same class against the DEPLOYED conf. This gate matters more, because a
    package's post_install can rewrite /etc/nftables.conf after the recipe
    passed the source gate — the reason the runtime gate exists at all."""
    old, new = mutation
    text = CONF.read_text(encoding="utf-8")
    proc = _run_runtime_gate(tmp_path, text.replace(old, new, 1))
    assert proc.returncode == 1, (
        f"the runtime gate passed a deployed conf carrying {expected!r}:\n{proc.stdout}")
    assert expected in proc.stdout


# --- guards: both gates must still pass the real ruleset --------------------

def test_the_source_gate_passes_the_shipped_ruleset(tmp_path):
    proc = _run_source_gate(tmp_path, CONF.read_text(encoding="utf-8"))
    assert proc.returncode == 0, proc.stdout


def test_the_runtime_gate_passes_the_shipped_ruleset(tmp_path):
    proc = _run_runtime_gate(tmp_path, CONF.read_text(encoding="utf-8"))
    assert proc.returncode == 0, proc.stdout


def test_the_source_gate_counts_every_accept_it_checked(tmp_path):
    """A gate that silently checked nothing would also exit 0. It must say how
    many rules it actually read, and that must be the number in the ruleset."""
    proc = _run_source_gate(tmp_path, CONF.read_text(encoding="utf-8"))
    expected = len(_approved_accepts())
    assert f"all {expected} accept rules are on the approved list" in proc.stdout, (
        proc.stdout)


def test_a_ruleset_with_no_accepts_at_all_fails_rather_than_passing(tmp_path):
    """An empty result is not a clean result."""
    text = "table inet filter {\n  chain input {\n" \
           "    type filter hook input priority filter; policy drop;\n  }\n" \
           "  chain forward {\n" \
           "    type filter hook forward priority filter; policy drop;\n  }\n}\n"
    proc = _run_source_gate(tmp_path, text)
    assert proc.returncode == 1
    assert "no accept rules found" in proc.stdout


def test_the_source_gate_fails_closed_when_its_approved_list_is_missing(tmp_path):
    """A gate that cannot load the list it enforces must refuse, not wave the
    build through with the checks it happens to have left."""
    tree = _synthetic_repo(tmp_path, CONF.read_text(encoding="utf-8"))
    (tree / "scripts" / "lib" / LIB.name).unlink(missing_ok=True)
    proc = _run(str(tree / "scripts" / SOURCE_GATE.name))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "cannot evaluate the approved accept list" in (proc.stdout + proc.stderr)


# --- what the gates tell a reader to go and read ---------------------------

@pytest.mark.parametrize("gate", D011_GATES, ids=lambda p: p.name)
def test_the_gate_names_no_document_that_is_not_in_this_repository(gate):
    """A contributor who trips a gate is sent to read something. Naming a file
    that is not in the tree they have sends them nowhere."""
    text = gate.read_text(encoding="utf-8")
    cited = set(re.findall(r"\b((?:docs|scripts|packages|tests)/[\w./-]+\.md)\b", text))
    missing = sorted(c for c in cited if not (REPO / c).exists())
    assert not missing, f"{gate.name} cites documents absent from this repository: {missing}"


@pytest.mark.parametrize("gate", D011_GATES, ids=lambda p: p.name)
def test_the_gate_does_not_cite_the_private_directive_document(gate):
    """The specific pointer that was unreachable, pinned so it cannot return."""
    assert "owner-directives" not in gate.read_text(encoding="utf-8")


@pytest.mark.parametrize("gate", D011_GATES, ids=lambda p: p.name)
def test_the_gate_still_states_the_requirement_it_enforces(gate):
    """Removing the pointer must not leave a reader with nothing. The gate has
    to say, in the tree, what it is enforcing and where the rules live."""
    text = gate.read_text(encoding="utf-8")
    assert "THE REQUIREMENT" in text
    assert "packages/core/intergenos-firewall-defaults/nftables.conf" in text
    assert "scripts/lib/d011-accept-list.sh" in text


# --- the libvirt fragment matches the bridge class, not one bridge ---------

def test_the_fragment_matches_the_bridge_interface_class_everywhere():
    """A host with a second libvirt NAT network gets virbr1; a virbr0-only rule
    leaves that network unserved — the defect the shipped ruleset's own
    bridge-scoped accepts closed on the input side."""
    text = FRAGMENT.read_text(encoding="utf-8")
    rules = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    hardcoded = [ln for ln in rules if re.search(r'"virbr\d+"', ln)]
    assert not hardcoded, f"fragment rules still name one bridge: {hardcoded}"


@pytest.mark.parametrize("chain", ["input", "forward"])
def test_every_fragment_rule_in_each_chain_is_bridge_scoped(chain):
    """Widening the match must not have widened the SCOPE: every rule still
    matches on an interface name and nothing else."""
    body = _sibling_chain_body(FRAGMENT.read_text(encoding="utf-8"), chain)
    assert body, f"the fragment's {chain} chain is empty"
    for line in body:
        assert re.search(r'\b(iifname|oifname)\s+"virbr\*"', line), (
            f"fragment {chain}-chain rule is not scoped to the bridge interface "
            f"class: {line!r}")
        assert not re.search(r"\b(ip|ip6)\s+(saddr|daddr)\b", line), (
            f"fragment {chain}-chain rule matches on an address: {line!r}")
