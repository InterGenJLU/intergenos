"""Install-order derivation tests — the L17kw class.

Forge's full-system install order is DERIVED from the runtime-dependency
graph (pkm.deporder, the one shared sorter), never hand-listed and never
alphabetical. The pinned regression is ge9b-10's intel-ucode hook failure:
alphabetical order put intel-ucode ('i') on the target before readline ('r'),
so its post-install hook's interpreter environment was incomplete (bash
present, libreadline absent) and the hook died rc=127.
"""

from pathlib import Path

from installer.backend.packages import (
    INSTALL_ORDER_ESSENTIALS,
    INSTALL_ORDER_LATE,
    _order_install_set,
)


def _archives(*names):
    return {n: ("1.0", Path(f"/archives/{n}-1.0.igos.tar.gz")) for n in names}


def _names(ordered):
    return [n for n, _v, _p in ordered]


def test_dependency_precedes_dependent_everywhere():
    archives = _archives("zlib", "openssl", "curl", "git", "aaa-leaf")
    deps = {
        "curl": ["openssl", "zlib"],
        "openssl": ["zlib"],
        "git": ["curl"],
    }
    order = _names(_order_install_set(archives, deps))
    for dependent, dep_list in deps.items():
        for dep in dep_list:
            assert order.index(dep) < order.index(dependent), (
                f"{dep} must precede {dependent}: {order}")


def test_regression_intel_ucode_after_readline_ge9b10():
    """The pinned ge9b-10 case: alphabetically intel-ucode < readline, and
    readline is not itself an essential — it is bash's (an essential's)
    runtime dependency. The essentials-closure preference plus the graph must
    put ncurses -> readline -> bash on the target before the non-essential
    mass, intel-ucode included."""
    archives = _archives("bash", "readline", "ncurses", "intel-ucode")
    deps = {"bash": ["readline"], "readline": ["ncurses"]}
    order = _names(_order_install_set(archives, deps))
    assert order.index("ncurses") < order.index("readline")
    assert order.index("readline") < order.index("bash")
    assert order.index("readline") < order.index("intel-ucode"), (
        f"the ge9b-10 rc=127 shape reproduced: {order}")
    assert order.index("bash") < order.index("intel-ucode")


def test_alphabetical_is_not_the_law():
    """'aaa-tool' depending on 'zzz-lib' must install after it despite
    sorting first alphabetically — the direct refutation of the pre-fix
    sorted() order."""
    archives = _archives("aaa-tool", "zzz-lib")
    order = _names(_order_install_set(archives, {"aaa-tool": ["zzz-lib"]}))
    assert order == ["zzz-lib", "aaa-tool"]


def test_out_of_set_deps_impose_no_constraint():
    archives = _archives("alpha", "beta")
    order = _names(_order_install_set(
        archives, {"alpha": ["not-in-set"], "beta": ["alpha"]}))
    assert order.index("alpha") < order.index("beta")
    assert set(order) == {"alpha", "beta"}


def test_cycle_appends_loudly_and_installs_everything(caplog):
    archives = _archives("ping", "pong", "solo")
    deps = {"ping": ["pong"], "pong": ["ping"]}
    with caplog.at_level("WARNING"):
        order = _names(_order_install_set(archives, deps))
    assert set(order) == {"ping", "pong", "solo"}
    assert order.index("solo") < order.index("ping")  # acyclic prefix first
    assert any("CYCLE" in rec.message for rec in caplog.records)


def test_deterministic_across_runs():
    archives = _archives("m", "k", "z", "a", "q")
    deps = {"z": ["a"], "q": ["m"]}
    first = _names(_order_install_set(dict(archives), dict(deps)))
    for _ in range(5):
        assert _names(_order_install_set(dict(archives), dict(deps))) == first


def test_essentials_still_lead_when_graph_permits():
    present_essentials = ["intergenos-base-files", "glibc-core", "bash"]
    archives = _archives(*present_essentials, "aardvark", "zebra")
    order = _names(_order_install_set(archives, {}))
    assert order[:3] == present_essentials  # declared order, ahead of 'aardvark'


def test_late_forcing_preserved_and_dependent_logged(caplog):
    archives = _archives("linux-kernel", "aaa-tool", "zzz-lib")
    assert "linux-kernel" in INSTALL_ORDER_LATE
    with caplog.at_level("WARNING"):
        order = _names(_order_install_set(
            archives, {"aaa-tool": ["linux-kernel"]}))
    assert order[-1] == "linux-kernel"
    assert any("late-forced" in rec.message for rec in caplog.records)


def test_essential_with_in_set_dep_waits_for_it():
    """The graph outranks the essentials list: bash (essential) with an
    in-set dep must not be emitted before that dep even though the list
    prefers bash early."""
    archives = _archives("bash", "readline")
    order = _names(_order_install_set(archives, {"bash": ["readline"]}))
    assert order == ["readline", "bash"]


def test_full_essentials_list_members_are_distinct():
    assert len(INSTALL_ORDER_ESSENTIALS) == len(set(INSTALL_ORDER_ESSENTIALS))
