# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""What takes the space is answered by the machine's own measurement.

"What's taking up all my disk space" asked WHERE the space went; the assistant
answered with how much was FREE (the 9B), with a wiki miss (the 35B) or with
nothing (the 2B), because every phrasing of the question mapped to no command
and the turn fell to the model (measured on the four R001.4-03 records). This
holds the fix at the router:

  * a GENERATED set of the question — subjects × usage verbs × quantifiers ×
    disk nouns, the biggest/largest forms, the idioms, each in several
    surface forms (apostrophes dropped, a trailing ellipsis, capitals) — routes
    to the direct-answer class with the disk_usage intent, every one of them;
  * the free-space question stays on the free-space answer, the how-to stays on
    the teaching route, the memory and cpu questions stay where they were, and
    a size limit elsewhere ("the biggest file I can upload") is not this;
  * a bare scope follow-up ("and in my home folder?") binds only to a usage
    question in the turn before, and names the scope it asks for;
  * the answer is composed from the probe's report alone: on a fixture with
    known sizes the rendered text carries those sizes, the filesystem's total,
    and the word partial exactly when the measurement was;
  * a probe that cannot run declines — the turn goes on, nothing is invented.

The router is the bare hermetic one the curated-route regression uses (no
model, no embedder, a recording registry). The probe is stubbed on the route
tests and real on the renderer test.
"""
from __future__ import annotations

import itertools
import os
import re
from pathlib import Path
from unittest import mock

import pytest

from intergen import disk_usage as du
from intergen.howto import HowtoCorpus
from intergen.interfaces.types import HardwareTierLevel, LLMResponse, ToolResult
from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter, _DA_DISK_USAGE_RE, _DA_DISK_USAGE_FOLLOWUP_RE
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

CORPUS = Path(__file__).resolve().parents[1] / "data" / "howto"
KiB, MiB = 1024, 1024 * 1024

CANNED = {
    "df -h --output=size,avail /": "Size Avail\n250G 118G",
    "free -h": ("               total used free shared buff/cache available\n"
                "Mem:           16Gi 4Gi 8Gi 0Gi 4Gi 12Gi\nSwap: 0B 0B 0B"),
    "uptime -p": "up 3 hours",
    "df -h": "Filesystem Size Used Avail Use% Mounted on\n/dev/example 250G 132G 118G 53% /",
}


def _router() -> ConversationRouter:
    registry = ToolRegistry()
    registry.discover_tools()
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    llm = LLMRouter(config=None)
    # A route that reaches the model (a keyword dispatch's synthesis, the free
    # turn) gets a canned reply and is recorded as such; the probe never does.
    llm.chat = mock.Mock(return_value=LLMResponse(text="(a canned model reply)", model="test"))
    llm.stream_with_tools = mock.Mock(side_effect=AssertionError("unexpected streamed model call"))
    with mock.patch.dict(os.environ, {"INTERGEN_HOWTO_DIR": str(CORPUS)}):
        router = ConversationRouter(
            tool_registry=registry, semantic_matcher=matcher, llm=llm,
            hardware_tier=HardwareTierLevel.TIER_2, lock_dispatch=False)
    router._howto = HowtoCorpus(embedder=None, data_dir=CORPUS)
    router._current_query_type = "general"
    commands: list[str] = []

    def execute(call, **kwargs):
        command = call.arguments.get("command", "") if call.arguments else ""
        commands.append(command)
        return ToolResult(call_id=call.call_id, name=call.name,
                          content=CANNED.get(command, "example probe output"), success=True)

    router._tools.execute = mock.Mock(side_effect=execute)
    router._test_commands = commands
    return router


@pytest.fixture(scope="module")
def shared_router():
    return _router()


def _route(router, text, *, prior=None):
    """Route with the probe stubbed: the answer carries the scope the probe was
    asked for, so the test reads the decision without walking a disk."""
    scopes: list[str] = []

    def fake_answer(scope="both", **kwargs):
        scopes.append(scope)
        return f"PROBE({scope})"

    intents: list[str] = []
    real_emit = __import__("intergen.router", fromlist=["glass"]).glass.emit

    def emit(phase, event, *a, **kw):
        detail = kw.get("detail") or {}
        if phase == "decision" and event == "direct_answer" and detail.get("verdict") == "answered":
            intents.append(detail.get("intent", ""))
        return real_emit(phase, event, *a, **kw)

    router._conv.transcript.clear()
    with mock.patch.object(du, "answer", side_effect=fake_answer), \
            mock.patch("intergen.router.glass.emit", side_effect=emit):
        if prior is not None:
            router.route(prior, decide_only=True)
            intents.clear()
        result = router.route(text, conversation_active=prior is not None, decide_only=True)
    return result, (intents[-1] if intents else ""), (scopes[-1] if scopes else None)


# ── the generated question ────────────────────────────────────────────────

SUBJECTS = ["what's", "whats", "what is", "which thing is", "something is"]
VERBS = ["taking up", "using", "eating", "hogging", "filling up", "consuming", "chewing through", "using up"]
QUANTIFIERS = ["", "all", "all of", "so much of", "the most of", "most of"]
NOUNS = ["my disk", "the disk", "my disk space", "my storage", "the drive", "my space",
         "the room on my hard drive", "the ssd", "this partition"]
BIGGEST_LEADS = ["which", "what are the", "show me the", "list the", "find the"]
BIGGEST_NOUNS = ["folders", "directories", "files", "dirs", "space consumers"]
BIGGEST_TAILS = ["", " on this machine", " in my home", " here", " on the root filesystem"]
IDIOMS = [
    "storage is full again", "where did all my disk space go", "where is my storage going",
    "why is my disk full", "disk almost full... what is it", "the drive is nearly full, what's on it",
    "biggest space hogs?", "show disk usage by directory", "whats the breakdown of my disk usage by folder",
    "how big is the package cache", "how much space is the journal taking", "how much are the logs taking up",
    "what is my cache taking up", "i'm out of space... what is all this stuff",
    "I keep running out of space, what's the culprit", "whats big on this disk",
    "my laptop says its out of space, whats hogging it", "which folders are the biggest",
]


def _surface_forms(q: str):
    yield q
    yield q.replace("'", "")
    yield q + "?"
    yield q + "..."
    yield q[0].upper() + q[1:]


def generated_positive():
    out = []
    for s, v, q, n in itertools.product(SUBJECTS, VERBS, QUANTIFIERS, NOUNS):
        out.append(" ".join(w for w in (s, v, q, n) if w))
    for lead, noun, tail in itertools.product(BIGGEST_LEADS, BIGGEST_NOUNS, BIGGEST_TAILS):
        out.append(f"which {noun} are the biggest{tail}" if lead == "which" else f"{lead} biggest {noun}{tail}")
    out += IDIOMS
    seen, uniq = set(), []
    for q in out:
        for form in _surface_forms(q):
            if form not in seen:
                seen.add(form); uniq.append(form)
    return uniq


FREE_SPACE = [
    "how much space do I have left", "how much free space is there", "how much disk space is available",
    "is my disk full", "how full is the disk", "how much storage is remaining", "free disk space?",
    "whats my free space", "disk space left", "how much disk is free on this machine",
    "am I running low on disk space", "do I have enough space left for a 20 GB download",
]
HOW_TO = [
    "how do I clear up some space on my disk", "how can I free up storage", "how do I delete the package cache",
    "how do i remove old logs to save space", "how do I find large files with the terminal",
    "what command shows disk usage", "how do I check how much space a folder uses", "teach me to check disk usage",
    "how do I empty my cache folder", "how do I make more room on my hard drive",
]
NEIGHBOURS = [
    "what's using all my ram", "what's eating my cpu", "how much memory do I have free", "what's running right now",
    "how long has this machine been up", "what's the biggest file I can upload", "what is the maximum attachment size",
    "how much space does google drive give me",
]


def test_the_generated_question_has_hundreds_of_forms():
    forms = generated_positive()
    assert len(forms) > 2000
    assert len(set(forms)) == len(forms)


def test_the_detector_claims_every_generated_form_and_none_of_the_neighbours():
    missed = [q for q in generated_positive() if not _DA_DISK_USAGE_RE.search(q)]
    assert missed == [], missed[:20]
    # The how-to forms ("how do I find large files") and the size-limit questions
    # ("the biggest file I can upload") share the detector's words; the route
    # declines them before and at the probe (the teach guard, the not-this-machine
    # guard — the route tests below hold both). The detector itself claims none of
    # the free-space and resource neighbours.
    claimed = [q for q in FREE_SPACE + NEIGHBOURS[:5] if _DA_DISK_USAGE_RE.search(q)]
    assert claimed == [], claimed


@pytest.mark.parametrize("question", [
    "what's taking up all my disk space", "whats using up the drive", "which folders are the biggest",
    "storage is full again", "where did all my disk space go", "why is my disk full",
    "show disk usage by directory", "how big is the package cache", "the drive is nearly full, what's on it",
    "Something is eating the disk, find it", "what takes up the most space on the root partition",
    "what's hogging space in my home folder", "what's using the space in /home",
])
def test_the_question_routes_to_the_probe_with_the_scope_it_names(shared_router, question):
    result, intent, scope = _route(shared_router, question)
    assert result.source == "direct_answer" and intent == "disk_usage", (result.source, intent, result.text)
    assert result.text == f"PROBE({scope})"
    assert shared_router._test_commands[-1:] != ["df -h --output=size,avail /"]
    expected = "home" if "home" in question else "root" if "root" in question else "both"
    assert scope == expected


def test_every_generated_form_routes_to_the_probe(shared_router):
    wrong = []
    for question in generated_positive()[::7]:  # every seventh form: ~300 full route calls
        result, intent, _ = _route(shared_router, question)
        if not (result.source == "direct_answer" and intent == "disk_usage"):
            wrong.append((question, result.source, intent))
    assert wrong == [], wrong[:20]


@pytest.mark.parametrize("question", FREE_SPACE)
def test_the_free_space_question_never_reaches_the_probe(shared_router, question):
    result, intent, scope = _route(shared_router, question)
    assert scope is None and intent != "disk_usage"
    if result.source == "direct_answer":
        assert intent == "disk_free"


@pytest.mark.parametrize("question", HOW_TO + NEIGHBOURS)
def test_the_how_to_and_the_neighbours_never_reach_the_probe(shared_router, question):
    result, intent, scope = _route(shared_router, question)
    assert scope is None and intent != "disk_usage", (question, result.source, intent)


@pytest.mark.parametrize("prior,follow_up,expected_scope", [
    ("what's eating my disk", "and in my home folder?", "home"),
    ("where did all my disk space go", "what about my home directory", "home"),
    ("which folders are the biggest", "and the root filesystem?", "root"),
    ("storage is full again", "ok which ones are the biggest", "both"),
])
def test_a_scope_follow_up_binds_to_the_usage_question_before_it(shared_router, prior, follow_up, expected_scope):
    assert _DA_DISK_USAGE_FOLLOWUP_RE.search(follow_up)
    result, intent, scope = _route(shared_router, follow_up, prior=prior)
    assert result.source == "direct_answer" and intent == "disk_usage" and scope == expected_scope


@pytest.mark.parametrize("prior,follow_up", [
    ("what time is it", "and in my home folder?"),
    ("how much memory do I have free", "what about the root filesystem"),
    ("is steam installed", "ok which ones are the biggest"),
])
def test_a_scope_follow_up_after_another_question_is_not_the_probe(shared_router, prior, follow_up):
    result, intent, scope = _route(shared_router, follow_up, prior=prior)
    assert scope is None and intent != "disk_usage"


def test_a_probe_that_cannot_run_declines_and_the_turn_goes_on(shared_router):
    shared_router._conv.transcript.clear()
    with mock.patch.object(du, "answer", side_effect=OSError("no filesystem")):
        result = shared_router.route("what's taking up all my disk space", decide_only=True)
    assert "PROBE" not in (result.text or "")
    assert result.source != "direct_answer" or "free on the root filesystem" not in (result.text or "")


# ── the renderer, against a fixture with known sizes ──────────────────────

def _write(path: str, size: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


def _size_in(text: str, label: str) -> str:
    m = re.search(re.escape(label) + r" (?:at least )?([0-9.]+ (?:bytes|KiB|MiB|GiB|TiB))", text)
    assert m, (label, text)
    return m.group(1)


def test_the_answer_is_composed_from_the_fixtures_measurement(tmp_path, monkeypatch):
    rootfs = str(tmp_path / "rootfs"); home = str(tmp_path / "home")
    _write(f"{rootfs}/usr/a.bin", 6 * MiB); _write(f"{rootfs}/var/b.bin", 2 * MiB)
    _write(f"{home}/Videos/v.bin", 3 * MiB); _write(f"{home}/.cache/c.bin", 512 * KiB)
    _write(f"{home}/.local/share/Trash/t.bin", 256 * KiB)
    monkeypatch.setattr(du, "known_consumers", lambda h: [
        ("the package cache", f"{rootfs}/var/cache/pkm/packages"),  # absent on the fixture
        ("your cache", f"{h}/.cache"), ("your trash", f"{h}/.local/share/Trash")])
    text = du.answer("both", home=home, root=rootfs, budget_s=30, size_kind="apparent")
    assert _size_in(text, f"{rootfs}/usr") == "6.0 MiB" and _size_in(text, f"{rootfs}/var") == "2.0 MiB"
    assert _size_in(text, "~/Videos") == "3.0 MiB" and _size_in(text, "your cache") == "512.0 KiB"
    assert _size_in(text, "your trash") == "256.0 KiB"
    assert "the package cache is empty" in text
    assert "complete measurement" in text and "partial" not in text
    total = os.statvfs(rootfs)
    assert du.human_size(total.f_frsize * total.f_blocks) in text  # the filesystem's own total
    # every figure in the text is one the probe produced or statvfs's
    figures = set(re.findall(r"[0-9.]+ (?:bytes|KiB|MiB|GiB|TiB)", text))
    produced = {"6.0 MiB", "2.0 MiB", "3.0 MiB", "512.0 KiB", "256.0 KiB", "8.0 MiB",
                du.human_size(3 * MiB + 512 * KiB + 256 * KiB)}
    fs = {du.human_size(total.f_frsize * total.f_blocks), du.human_size(total.f_frsize * (total.f_blocks - total.f_bfree)),
          du.human_size(total.f_frsize * total.f_bavail)}
    assert figures <= produced | fs, figures - (produced | fs)


def test_a_partial_measurement_says_so_and_marks_its_lower_bounds(tmp_path, monkeypatch):
    rootfs = str(tmp_path / "rootfs"); home = str(tmp_path / "home")
    for i in range(40):
        _write(f"{rootfs}/d{i:02d}/f.bin", 4 * KiB)
    _write(f"{home}/x.bin", 1 * KiB)
    monkeypatch.setattr(du, "known_consumers", lambda h: [])
    ticks = {"t": 0.0}

    def clock():
        ticks["t"] += 0.05
        return ticks["t"]

    text = du.answer("root", home=home, root=rootfs, budget_s=0.4, size_kind="apparent", clock=clock)
    assert "This measurement is partial" in text and "budget ended" in text
    assert "at least" in text
