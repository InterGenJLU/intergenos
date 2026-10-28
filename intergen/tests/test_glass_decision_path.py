# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The decision path: each turn's whole walk, in one glass record.

A turn used to be explained by its winner. The glass said which route answered
("route/decided") and nothing about the stages tried before it, the pattern
that decided each one, or the stages that never got the turn: "what's running
right now" was recorded as answered by the live-data offer, and nobody could
read from the record that the system map — whose own phrase list names that
question — was never reached. One row per turn now carries the walk
(decision/path): every stage of router.LADDER_STAGES in order, as matched,
declined, skipped (with the condition) or not_reached, and a stage before the
deciding one that recorded nothing as unrecorded; the retrieval with its
candidates, scores and threshold; each tool call with its consent state,
arguments and output; the prompt's sources; each screen's verdict and what it
changed.

These tests read the record a turn writes, from the file, as a reader would.
The model is a stub; nothing is dispatched for real.

What test finds each B-2 finding's CLASS (the success criterion is that each
failure is explainable from this record alone; the class is found elsewhere):
  F-A, a machine-state ask offered a web search: the generated suite's property
      "no class-2 phrasing is ever offered a web search" over the route (B-2
      leg 2); this record names the pattern that does it (_IMPLICIT_LIVE_RE).
  F-B, a what-takes-the-space ask answered with free space: generated class-3
      phrasings asserting a usage reading answers them; the ladder has no such
      stage, which this record shows by listing every stage it has.
  F-G, a referent-less question grounded on an unrelated passage: generated
      referent-less phrasings ("this file", "this folder") asserting one
      clarifying question, and a retrieval property that an admitted passage
      shares the question's referent; this record shows the passage, its score
      and the threshold that admitted it.
"""
from __future__ import annotations

import ast
import inspect
import json
from unittest import mock

import pytest

import intergen.glass as glass
import intergen.router as router_mod
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import (HardwareTierLevel, LLMResponse,
                                       ToolCall, ToolResult)
from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tests import glass_rows
from intergen.tool_registry import ToolRegistry

# A conversational ask no code-owned stage answers: on the locked floor it
# walks every stage and is answered by the model's free turn.
SYNTHETIC = "tell me a short story about a lighthouse keeper"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("INTERGEN_GLASS", raising=False)
    glass._glass = None
    yield tmp_path
    glass._glass = None


def _router(*, locked: bool = True, chat_text: str = "stub answer",
            tool_stream=None) -> ConversationRouter:
    registry = ToolRegistry()
    registry.discover_tools()
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    llm = LLMRouter(config=None)
    llm.chat = mock.Mock(return_value=LLMResponse(text=chat_text, model="stub"))
    llm.stream_with_tools = mock.Mock(
        side_effect=tool_stream or AssertionError("unexpected tool stream"))
    return ConversationRouter(
        tool_registry=registry, semantic_matcher=matcher, llm=llm,
        hardware_tier=HardwareTierLevel.TIER_2, lock_dispatch=locked)


def _paths(state) -> list[dict]:
    return [r["detail"] for r in glass_rows.read(state)
            if r.get("phase") == "decision" and r.get("event") == "path"]


def _walk(path: dict) -> dict[str, dict]:
    return {e["stage"]: e for e in path["ladder"] if e.get("pass") == 1}


# ── (a) every stage records its verdict, in the ladder's order ─────────────

def test_the_router_declares_its_ladder():
    stages = getattr(router_mod, "LADDER_STAGES", None)
    assert stages, "router.py declares no LADDER_STAGES"
    assert len(stages) == len(set(stages))


def test_every_declared_stage_is_recorded_somewhere_in_the_ladder():
    """Each stage name appears in a _stage(...) call in _route_impl, and every
    name a _stage(...) call there uses is declared: the list and the code
    cannot drift apart without this failing."""
    stages = getattr(router_mod, "LADDER_STAGES", ())
    assert stages, "router.py declares no LADDER_STAGES"
    source = inspect.getsource(ConversationRouter._route_impl)
    called = set()
    for node in ast.walk(ast.parse(source.lstrip().replace("\n    ", "\n"))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_stage" and node.args
                and isinstance(node.args[0], ast.Constant)):
            called.add(node.args[0].value)
    assert set(stages) == called, (set(stages) ^ called)


def test_every_ladder_stage_emits_its_verdict_for_a_synthetic_input(state):
    stages = getattr(router_mod, "LADDER_STAGES", None)
    assert stages, "router.py declares no LADDER_STAGES"
    _router().route(SYNTHETIC)
    paths = _paths(state)
    assert len(paths) == 1, f"{len(paths)} decision/path rows for one route()"
    walk = [e for e in paths[0]["ladder"] if e.get("pass") == 1]
    assert [e["stage"] for e in walk] == list(stages)
    by_outcome = {}
    for e in walk:
        by_outcome.setdefault(e["outcome"], []).append(e["stage"])
    assert "unrecorded" not in by_outcome, by_outcome["unrecorded"]
    assert "not_reached" not in by_outcome, by_outcome["not_reached"]
    assert walk[-1]["outcome"] == "matched"
    for e in walk[:-1]:
        assert e["outcome"] in ("declined", "skipped"), e
        assert e.get("reason") or e.get("failed_out") or e.get("pattern"), e
        assert isinstance(e.get("dur_ms"), (int, float)) or e["stage"] in (
            "empty_input", "forget_in_compound"), e


def test_the_stages_after_the_deciding_one_are_written_not_reached(state):
    _router().route("")
    walk = [e for e in _paths(state)[0]["ladder"] if e.get("pass") == 1]
    assert walk[0] == {**walk[0], "stage": "empty_input", "outcome": "matched"}
    assert {e["outcome"] for e in walk[1:]} == {"not_reached"}
    assert len(walk) == len(router_mod.LADDER_STAGES)


# ── (b) the three B-2 findings, explainable from the record alone ──────────

def test_fa_whats_running_right_now_names_the_live_data_pattern(state):
    _router().route("what's running right now")
    walk = _walk(_paths(state)[0])
    offer = walk["current_data_offer"]
    assert offer["outcome"] == "matched"
    assert offer["pattern"] == "_IMPLICIT_LIVE_RE"
    assert offer["matched_text"] == "right now"
    assert "_SYSTEM_SCOPE_RE" in offer["fail_outs_tested"]
    assert "_MACHINE_STATE_RE" in offer["fail_outs_tested"]
    assert walk["system_map"]["outcome"] == "not_reached"


def test_fb_the_free_space_tool_is_chosen_and_no_stage_probes_usage(state):
    def model_picks_df(messages, **_kw):
        yield ToolCall(name="run_command", arguments={"command": "df -h"},
                       call_id="call-fb",
                       source_of_request=Provenance.USER_DIRECT)

    r = _router(locked=False, tool_stream=model_picks_df)
    tool = r._tools.get_tool("run_command")
    tool.execute = mock.Mock(return_value=ToolResult(
        call_id="call-fb", name="run_command", success=True,
        content="Filesystem Size Used Avail Use%\n/dev/nvme0n1p3 435G 35G 397G 8% /"))
    r._template_synthesis = mock.Mock(return_value="Your disk is 8% full.")
    r._llm.continue_after_tool_call = mock.Mock(return_value=LLMResponse(
        text="Your disk is 8% full — 397G free.", model="stub"))
    r.route("what's taking up all my disk space")
    path = _paths(state)[0]
    walk = _walk(path)
    assert walk["llm_tools"]["outcome"] == "matched"
    assert [c["tool"] for c in walk["llm_tools"]["tool_calls"]] == ["run_command"]
    assert not [s for s in router_mod.LADDER_STAGES
                if "probe" in s or "usage" in s]
    results = [t for t in path["tools"] if t.get("event") == "result"]
    assert results and results[0]["tool"] == "run_command"
    assert results[0]["arguments"] == {"command": "df -h"}
    assert "397G" in results[0]["output"]
    consents = [t for t in path["tools"] if t.get("event") == "consent"]
    assert consents and consents[0]["call_id"] == results[0]["call_id"]
    assert consents[0]["consent_state"] in (
        "not_asked", "granted", "refused", "asked_not_granted")


def test_fg_the_admitted_passage_its_score_and_threshold_are_recorded(
        state, tmp_path):
    from intergen.tests.test_wiki_retrieval import _Fixture

    fx = _Fixture(str(tmp_path / "wiki"))
    fx._write("security/advisory.html", (
        "<html><body><main><h1>Security advisory</h1><p>The vulnerable code "
        "is located in the crypto af_alg file of the kernel.</p></main>"
        "</body></html>"))
    pages = json.loads((fx.root / "pages-manifest.json").read_text())
    pages["pages"]["security/advisory.html"] = fx._sha("security/advisory.html")
    (fx.root / "pages-manifest.json").write_text(json.dumps(pages))
    r = _router()
    r._howto = None
    r._wiki_retrieval = fx.retrieval()
    r.route("where is this file located")
    path = _paths(state)[0]
    hits = [e for e in path["retrieval"] if e.get("store") == "wiki"]
    assert len(hits) == 1
    hit = hits[0]
    assert hit["admitted"] is True
    assert hit["backend"] == "keyword"
    assert hit["threshold"] == pytest.approx(0.45)
    assert hit["best_score"] >= hit["threshold"]
    assert hit["page"] == "security/advisory.html"
    assert "crypto af_alg" in hit["passage"]
    assert hit["candidates"][0]["page"] == "security/advisory.html"
    assert hit["reason"] == "the best score reached the threshold"
    sources = [s["source"] for p in path["prompts"] if p.get("kind") == "assembly"
               for s in p["sources"]]
    assert "grounding:wiki" in sources


def test_a_below_threshold_miss_is_recorded_with_its_score(state, tmp_path):
    from intergen.tests.test_wiki_retrieval import _Fixture

    r = _router()
    r._howto = None
    r._wiki_retrieval = _Fixture(str(tmp_path / "wiki")).retrieval()
    r.route(SYNTHETIC)
    hit = [e for e in _paths(state)[0]["retrieval"] if e.get("store") == "wiki"][0]
    assert hit["admitted"] is False
    assert hit["reason"] in ("no passage shares a content word",
                             "the best score is below the threshold")
    assert "threshold" in hit and "best_score" in hit


# ── (c) one record per served turn, before its terminal row ────────────────

def test_a_served_turn_writes_one_path_just_before_its_terminal(state):
    r = _router()
    with glass.turn("t-served", "dbus"):
        glass.emit("route", "turn_start", detail={"user_msg": SYNTHETIC})
        res = r.route(SYNTHETIC)
        glass.emit("delivery", "final", detail={"text": res.text,
                                                "source": res.source})
    rows = [x for x in glass_rows.read(state) if x.get("turn_id") == "t-served"]
    kinds = [(x["phase"], x["event"]) for x in rows]
    assert kinds.count(("decision", "path")) == 1
    i = kinds.index(("decision", "path"))
    assert kinds[i + 1] == ("delivery", "final")
    final = rows[i]["detail"]["final"]
    assert final["event"] == "final" and final["source"] == "llm_freeform"


def test_an_entry_after_the_path_was_written_is_kept_as_path_late(state):
    with glass.turn("t-late", "web"):
        glass.emit("delivery", "final", detail={"text": "x"})
        glass.path_entry("tools", event="result", tool="late_tool")
    rows = [x for x in glass_rows.read(state) if x.get("turn_id") == "t-late"]
    late = [x for x in rows if x["event"] == "path_late"]
    assert late and late[0]["detail"]["entry"]["tool"] == "late_tool"


# ── (d) the new fields are redacted like every other field ─────────────────

def test_a_secret_shaped_tool_output_is_redacted_in_the_path(state):
    secret = "ghp_" + "A" * 36
    with glass.turn("t-secret", "web"):
        glass.path_entry("tools", event="result", tool="read_file",
                         arguments={"path": "/home/u/.netrc"},
                         output=f"machine github.com password {secret}\n"
                                f"PGPASSWORD=hunter2hunter2")
        glass.emit("delivery", "final", detail={"text": "done"})
    raw = (state / "intergen" / "glass.jsonl").read_text()
    assert secret not in raw
    assert "hunter2hunter2" not in raw
    path = _paths(state)[0]
    out = path["tools"][0]["output"]
    assert "<redacted:provider-token>" in out
    assert "PGPASSWORD=<redacted:credential-assignment>" in out
