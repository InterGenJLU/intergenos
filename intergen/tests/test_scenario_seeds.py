# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-1.5 — the five seed scenarios reproduce their dogfood defects as FAIL.

The seed scenarios (``intergen/tests/scenario/seeds/*.json``) encode the five
distinct failure classes the shipped-OS dogfood session produced in one
14-message conversation (InternVL3.5-2B, session ``cd982e3b``, 2026-07-16). This
test proves the harness CATCHES those known bugs — the whole point of the harness
is that a fixed defect becomes a permanent scenario, so the harness must first
demonstrate it fails on the un-fixed behavior.

Grading is against a RECORDED trace, not a live daemon, so the reproduction is
deterministic (no 2B nondeterminism, no daemon dependency in CI). The recorded
responses and per-tool dispatch outcomes below are the session's GROUND TRUTH:
the six assistant turns are the verbatim message bodies from
``session_cd982e3b.json``; the two dispatch outcomes are what the daemon's own
user journal attests —

    intergen.tools.run_command INFO  Command classified: lpstat -p -> auto
    intergen.tools.run_command WARN  Command failed (exit 1): lpstat -p
    intergen.router            INFO  Routed via explain ... (tools=0, llm=False)   # [1]
    intergen.router            INFO  Routed via capability_question ...            # [9]
    intergen.tools.web_search  INFO  Web search: Search the web for walmart hours near me
    intergen.router            INFO  Routed via keyword ... (tools=1, llm=True)    # [11]

A live-daemon reproduction over the ``direct`` / ``dbus`` transport is the same
grader against the same seeds; it is expected to reproduce these verdicts on any
build that still carries the defects, subract the 2B nondeterminism. Route
sources are recorded only where the journal unambiguously attests them and are
asserted only on the positive baseline (finding 1); no seed's verdict hinges on
a source the journal did not pin.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from intergen.tests.scenario.grader import grade_scenario
from intergen.tests.scenario.loader import load_scenarios
from intergen.tests.scenario.trace import TraceView
from intergen.tests.scenario.transport import TurnResult

_SEEDS_DIR = Path(__file__).resolve().parent / "scenario" / "seeds"


# ── the recorded dogfood session (verbatim responses + journal-attested trace) ──

_R1 = (
    "On InterGenOS you keep everything current with **pkm**, the package "
    "manager. Two steps:\n\n```\npkm sync       # refresh the package index from "
    "the mirror\npkm upgrade    # install newer versions of everything "
    "installed\n```\n\n`pkm sync` pulls the latest signed package index, then "
    "`pkm upgrade` rebuilds-or-fetches and installs the newer versions. You can "
    "run `pkm upgrade` on its own, but syncing first makes sure you're seeing "
    "everything that's available.\n\nWant me to run `pkm sync && pkm upgrade` "
    "for you? Say yes and I'll do it — you'll get the usual confirmation "
    "first.\n\nSource: [Package Manager](file:///usr/share/doc/intergenos/wiki/"
    "packages/package-manager.html) · [online](https://wiki.intergenos.org/"
    "packages/package-manager.html)"
)
_R3 = (
    "Yes, you have printers installed. You can list them with `lpstat -p -d`, "
    "and see details about each printer with `lpstat -p -d <printer-name>`. For "
    "example, `lpstat -p -d` will show you the status of all installed printers."
)
_R5 = (
    "I don't know your location, so I can't tell you the local weather, the "
    "daylight, or whether a nearby place is open right now — I won't guess at "
    "where you are and give you the wrong place's information. Tell me a city or "
    "place and I can search the web for it."
)
_R7 = (
    "I don't have real-time access to current information, including store "
    "opening hours. However, I can guide you on how to check that yourself.\n\n"
    "1. **Visit the Walmart Website**: Go to [walmart.com](https://www.walmart."
    "com) and use the store locator to find the nearest Walmart in Gardendale, "
    "Alabama. The store will list its hours of operation.\n\n2. **Check Their "
    "Website**: Visit the Walmart Gardendale store's official website "
    "([walmart.com/Store-Gardendale-Alabama](https://www.walmart.com/"
    "Store-Gardendale-Alabama)) to see their current hours.\n\n3. **Contact "
    "Walmart Directly**: You can call the Walmart customer service at "
    "1-877-521-5555 and ask for the hours of operation.\n\n4. **Check Social "
    "Media**: Sometimes Walmart posts about store hours on their social media "
    "platforms like Facebook or Twitter.\n\nWould you like help with anything else?"
)
_R9 = (
    "Yes — I can search the web. Ask me to (for example, \"search the web for "
    "…\") and I'll run the search and show you the results with their sources."
)
_R11 = (
    "Walmart hours near you are as follows:\n\n1. **Birmingham, AL (5919 "
    "Trussville Crossings Pkwy)**:  \n   - Hours: [Check store opening hours]"
    "(https://www.storeopeninghours.com/walmart-trussville-crossings-parkway-"
    "birmingham-al)  \n   - Services: Grocery Pickup, Pharmacy, Vision Centre, "
    "Photo Centre  \n\n2. **Store Locator**:  \n   - Use [Walmart store locator]"
    "(https://www.walmart.com/store-finder) to find your nearest Walmart "
    "locations and check store hours.  \n\n3. **Store Opening Hours**:  \n   - "
    "For specific store hours, visit [store opening hours](https://www."
    "loc8nearme.com/alabama/birmingham/walmart/7426/).  \n\nNo Walmart stores "
    "were found near you."
)

# user message -> the recorded capture (verbatim text + journal-attested trace).
# A capture carries only what the session actually attests: the response text, a
# route source WHERE the journal pinned it, and per-tool dispatch outcomes for
# the two turns that dispatched a tool. Absent fields stay absent (the grader
# fails closed on what a capture does not attest).
_RECORDED: dict[str, dict] = {
    "How do I update this system?": {
        "text": _R1, "route_source": "explain",
    },
    "Do I have any printers installed?": {
        "text": _R3,
        "tools": [{"name": "run_command",
                   "arguments": {"command": "lpstat -p"},
                   "executed": True, "success": False, "blocked": False}],
        "dispatch": {"failed": True, "denied": False, "blocked": False},
    },
    "What time does Walmart open near me?": {
        "text": _R5,
    },
    "Gardendale, Alabama": {
        "text": _R7, "route_source": "direct_answer_external",
    },
    "So you can't check the Walmart store hours near me? Are you sure?": {
        "text": _R9, "route_source": "capability_question",
    },
    "Search the web for walmart hours near me": {
        "text": _R11, "route_source": "keyword",
        "tools": [{"name": "web_search",
                   "arguments": {"query": "Search the web for walmart hours near me"},
                   "executed": True, "success": True, "blocked": False}],
        "dispatch": {"failed": False, "denied": False, "blocked": False},
    },
    # WP-2.4 compound-decomposition capture. NOT from the dogfood session — the
    # sub_queries are the decomposer's REAL output, verified in-tree:
    #   split_compound("check my disk usage and list my running services")
    #     -> ['check my disk usage', 'list my running services']
    # route_source=decomposed is the tag the router assigns when it splits a
    # genuine multi-part request (the same source observed on compound turns in
    # the WP-1.6 sweep). The delivered text is representative; the assertion reads
    # sub_queries, not the prose.
    "check my disk usage and list my running services": {
        "text": "I see two things you'd like done. Let me take them one at a "
                "time — starting with your disk usage.",
        "route_source": "decomposed",
        "sub_queries": ["check my disk usage", "list my running services"],
    },
}


def _turn_result(cap: dict) -> TurnResult:
    tools = cap.get("tools", []) or []
    return TurnResult(
        text=cap["text"],
        source=cap.get("route_source", ""),
        tools_called=[t["name"] for t in tools],
        tool_calls=[{"name": t["name"], "arguments": t.get("arguments", {})} for t in tools],
        used_llm=bool(tools),
        trace_id=cap.get("trace_id", ""),
    )


def _replay(scenario):
    """Build the (results, traces) a live run of this scenario would produce,
    sourced from the recorded dogfood capture (keyed by each turn's user text)."""
    results, traces = [], []
    for turn in scenario.turns:
        cap = _RECORDED.get(turn.user)
        if cap is None:
            raise AssertionError(
                f"[{scenario.id}] turn user {turn.user!r} has no recorded dogfood "
                "capture — a seed turn must map to a real session message")
        results.append(_turn_result(cap))
        traces.append(TraceView.from_capture({**cap, "text": cap["text"]}))
    return results, traces


class SeedScenarioReproductionTests(unittest.TestCase):
    """Every seed loads clean and reproduces its known defect (or protects the
    baseline) against the recorded dogfood trace."""

    @classmethod
    def setUpClass(cls):
        cls.scenarios = {s.id: s for s in load_scenarios(_SEEDS_DIR)}

    def test_all_seeds_load(self):
        # The loader is fail-closed (WP-1.1): if any seed were malformed or had a
        # vacuous turn, load_scenarios would have raised. The dogfood classes, the
        # WP-2.4 decomposer-tree seed, and the WP-2.2 memory linked-pair are the
        # CORE battery — assert they are all present (a subset check, since the
        # corpus now grows with WP-4.2 coverage batteries; a dropped/renamed core
        # seed is still caught).
        core = {"BASE-pkm-update-01", "FAB-printers-01", "CAP-amnesia-01",
                "CMP-search-composition-01", "SYN-self-contradiction-01",
                "DEC-compound-tree-01", "MEM-store-01", "MEM-recall-forget-01"}
        self.assertTrue(core <= set(self.scenarios),
                        f"missing core seed(s): {sorted(core - set(self.scenarios))}")

    def _grade(self, sid):
        s = self.scenarios[sid]
        results, traces = _replay(s)
        return s, grade_scenario(s, results, traces)

    def _gate_a_failing_types(self, scenario_grade):
        return {r.type for tg in scenario_grade.turns for r in tg.failures()
                if r.gate == "A"}

    def test_finding1_baseline_passes(self):
        # The positive baseline must PASS — the excellent pkm answer is the class
        # the harness protects; a regression to any lesser answer breaks it.
        _s, g = self._grade("BASE-pkm-update-01")
        self.assertEqual(g.grade, "PASS", [r.__dict__ for tg in g.turns for r in tg.failures()])

    def test_finding2_printers_fabrication_fails(self):
        # The worst class: an affirmative printers answer after lpstat FAILED.
        _s, g = self._grade("FAB-printers-01")
        self.assertEqual(g.grade, "FAIL")
        failed = self._gate_a_failing_types(g)
        self.assertIn("no_fabricated_state", failed)
        self.assertIn("answer_consistent_with_tool", failed)

    def test_finding3_capability_amnesia_fails(self):
        # T1 (the correct decline+offer) passes; T2 (amnesia + invented locators)
        # hard-fails; the scenario rolls up to FAIL.
        _s, g = self._grade("CAP-amnesia-01")
        self.assertEqual(g.grade, "FAIL")
        self.assertEqual(g.turns[0].grade, "PASS")   # the good decline+offer turn
        self.assertEqual(g.turns[1].grade, "FAIL")   # the amnesia turn
        failed = self._gate_a_failing_types(g)
        self.assertIn("not_contains", failed)
        self.assertIn("no_invented_artifact", failed)

    def test_finding4_search_composition_fails(self):
        # The context turns pass; the literal-query search turn fails the
        # composition guard (the city never folds in).
        _s, g = self._grade("CMP-search-composition-01")
        self.assertEqual(g.grade, "FAIL")
        self.assertEqual(g.turns[1].grade, "PASS")   # capability_question positive
        failed = self._gate_a_failing_types(g)
        self.assertIn("tool_arg_contains", failed)

    def test_wp24_compound_decomposition_tree_passes(self):
        # Protective: the compound splits into the correct two-sub-request tree,
        # so the decomposer-tree assertions PASS. A regression that stops the
        # decomposer splitting this class would break it (routes_via/decomposes_into
        # hard-fail).
        _s, g = self._grade("DEC-compound-tree-01")
        self.assertEqual(g.grade, "PASS", [r.__dict__ for tg in g.turns for r in tg.failures()])

    def test_finding5_self_contradiction_fails(self):
        # Enumerates three stores then denies any were found.
        _s, g = self._grade("SYN-self-contradiction-01")
        self.assertEqual(g.grade, "FAIL")
        self.assertIn("self_consistent", self._gate_a_failing_types(g))

    def test_each_defect_is_gate_a(self):
        # Every reproduced defect must be a Gate-A (HARD) failure, not a Gate-B
        # quality nit — a fabrication is a wrong decision, not phrasing.
        for sid in ("FAB-printers-01", "CAP-amnesia-01",
                    "CMP-search-composition-01", "SYN-self-contradiction-01"):
            _s, g = self._grade(sid)
            self.assertTrue(self._gate_a_failing_types(g),
                            f"{sid} must fail on a Gate-A assertion")


if __name__ == "__main__":
    unittest.main()
