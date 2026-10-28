# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Both surfaces send the model the same request for the same turn.

The console (the bus) routes a turn in full, and the route builds the model's
request itself. The panel (the browser) routes it with decide_only: the route
returns its decision alone, and the web server builds the request. Whatever
the route adds to its request beyond the shared prompt builder stayed behind
at that boundary. The freeform route's verified wiki passage and the page's
citation, the tool route's installed-tool facts and the freeform route's two
guards were never in the panel's request, and the panel read the system map's
data from the cache a second time instead of using what the route had read.
One surface answered without what the other one used.

THE TEST THAT FINDS THIS CLASS: one question, one router setup, each surface.
A loopback endpoint of the test's own stands in for the model server and keeps
the bytes each request delivered. The generation request's messages must be
equal on the two surfaces, message for message, and so must the delivered
answer with its citation line. Each case names the route it drives and the
condition that selects what that route adds, and checks first that the console
request carries it, so a case that stopped reaching its condition cannot pass.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

import pytest

import intergen.glass as glass
from intergen import persona
from intergen.interfaces.types import HardwareTierLevel
from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.session_manager import SessionManager
from intergen.tests import glass_rows
from intergen.tests.test_glass_model_request import _Capture, _sse
from intergen.tests.test_wiki_retrieval import _Fixture
from intergen.tool_registry import ToolRegistry
from intergen.web_server import ConnectionContext, WebServer

pytestmark = pytest.mark.reaches_a_server_it_starts_itself

PASSAGE = "The vulnerable code is located in the crypto af_alg file of the kernel."
# The model's answer draws on the passage, so the console cites the page.
ANSWER = PASSAGE
FACTS = "printing -> lpstat -p -d; lp, lpr, lpstat and lpoptions are installed"
SYSTEM_MAP = "services: 0 failed; disk: 41% used; load average: 0.30"


@dataclass(frozen=True)
class Case:
    """One route and the condition that selects what it adds."""
    name: str
    question: str
    route: str
    #: The label the console carries for what the route adds, and where:
    #: in the request it sends, or only in the prompt it assembled (the tool
    #: request keeps the system prompt and the question alone; see below).
    adds: str
    adds_in: str = "request"
    locked: bool = True
    wiki: bool = False
    reference: bool = False
    #: What each read of the state cache returns, in order (the last repeats).
    system_map: tuple = ()


CASES = (
    Case("freeform_wiki", "where is this file located", "llm_freeform",
         "grounding wiki", wiki=True),
    Case("freeform_reference", "how do I list the printers", "llm_freeform",
         "grounding reference", reference=True),
    # The model client sends a tool request as the system prompt and the
    # question alone, so the tool route's facts reach neither surface's
    # request; the two assemblies still have to agree.
    Case("tools_reference", "which printers can I print to", "llm_tools",
         "grounding reference", adds_in="assembly", locked=False,
         reference=True),
    Case("system_map", "is everything ok?", "system_map", "system map data",
         system_map=(SYSTEM_MAP,)),
    Case("system_map_read_again", "is everything ok?", "system_map",
         "system map data", system_map=(SYSTEM_MAP, None)),
    Case("freeform_diagnostic_guard", "why does my laptop feel slow lately",
         "llm_freeform", "guard FREEFORM_STATE_GUARD"),
    Case("freeform_system_category_guard",
         "why do you need permission to change things", "llm_freeform",
         "guard SYSTEM_CAPABILITY_GUARD"),
)


class _Reference:
    """The installed-tool reference index, standing in: facts for one
    question, nothing for any other."""

    def __init__(self, question: str):
        self._question = question

    def lookup(self, text):
        return FACTS if text == self._question else None

    def conventions(self):
        return "The package manager is pkm."


class _StateCache:
    """The state cache, standing in: the system map for each read in turn.
    Any other read refuses, so an unexpected one fails the case."""

    def __init__(self, maps: tuple):
        self._maps = list(maps)
        self.reads = 0

    def lookup_for_query(self, query):
        return None

    def get_system_map_data(self, query):
        self.reads += 1
        return self._maps[min(self.reads, len(self._maps)) - 1]

    def __getattr__(self, name):
        raise AttributeError(f"the state cache stand-in was asked for {name}")


class _Socket:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)


@dataclass
class Surface:
    """What one surface did with the case's question."""
    source: str
    requests: list[dict]
    answer: str | None
    cache_reads: int
    #: Each prompt the router's two builders returned, as role and content.
    assemblies: list[list[dict]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    assembly_labels: list[str] = field(default_factory=list)

    @property
    def generation(self) -> list[dict]:
        """The first request's messages: the one that generates the answer."""
        return _unclocked(self.requests[0]["messages"] if self.requests else [])

    @property
    def assembly(self) -> list[dict]:
        """The first prompt assembled: the one the generation was built from."""
        return _unclocked(self.assemblies[0] if self.assemblies else [])


# The system prompt ends with the date and the time to the minute, so two
# requests built either side of a minute differ there and nowhere else (seen
# once in the enumeration's runs). That one line is compared by its shape.
_CLOCK = re.compile(r"Today is [A-Za-z]+, [A-Za-z]+ \d\d, \d{4}\. "
                    r"Time: \d{1,2}:\d\d [AP]M\.$")


def _unclocked(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        content = m.get("content")
        if m.get("role") == "system" and isinstance(content, str) \
                and _CLOCK.search(content):
            m = {**m, "content": _CLOCK.sub("Today is <date>. Time: <time>.",
                                            content)}
        out.append(m)
    return out


def _wiki(root: Path):
    fx = _Fixture(str(root / "wiki"))
    fx._write("security/advisory.html", (
        "<html><body><main><h1>Security advisory</h1><p>" + PASSAGE
        + "</p></main></body></html>"))
    pages = json.loads((fx.root / "pages-manifest.json").read_text())
    pages["pages"]["security/advisory.html"] = fx._sha("security/advisory.html")
    (fx.root / "pages-manifest.json").write_text(json.dumps(pages))
    return fx.retrieval()


def _router(case: Case, root: Path, capture: _Capture):
    registry = ToolRegistry()
    registry.discover_tools()
    registry.execute = mock.Mock(side_effect=AssertionError("unexpected dispatch"))
    registry.set_tool_offering_locked(case.locked)
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    llm = LLMRouter(config=None)
    llm._endpoint = capture.url
    cache = _StateCache(case.system_map) if case.system_map else None
    router = ConversationRouter(
        tool_registry=registry, semantic_matcher=matcher, llm=llm,
        hardware_tier=HardwareTierLevel.TIER_2, lock_dispatch=case.locked,
        state_cache=cache)
    router._howto = None
    router._reference = _Reference(case.question) if case.reference else None
    # One installed wiki for both surfaces: the citation names its path.
    router._wiki_retrieval = _wiki(root.parent) if case.wiki else None
    assemblies: list[list[dict]] = []
    for name in ("_build_messages", "_build_system_map_messages"):
        builder = getattr(router, name)

        def kept(*args, _builder=builder, **kwargs):
            messages = _builder(*args, **kwargs)
            assemblies.append([{"role": m.role.value, "content": m.content}
                               for m in messages])
            return messages
        setattr(router, name, kept)
    return router, registry, cache, assemblies


def label(message: dict, question: str) -> str:
    """A name for one request message, by where its text comes from."""
    content = message.get("content")
    if not isinstance(content, str):
        content = json.dumps(content, sort_keys=True)
    if content == persona.FREEFORM_STATE_GUARD:
        return "guard FREEFORM_STATE_GUARD"
    if content == persona.SYSTEM_CAPABILITY_GUARD:
        return "guard SYSTEM_CAPABILITY_GUARD"
    if content.startswith("You have a VERIFIED passage from the InterGenOS wiki"):
        return "grounding wiki"
    if FACTS in content:
        return "grounding reference"
    if SYSTEM_MAP in content:
        return "system map data"
    if message.get("role") == "system":
        return "system prompt"
    if content == question:
        return "question"
    return "other " + hashlib.sha256(content.encode()).hexdigest()[:8]


def _finish(surface: Surface, question: str) -> Surface:
    surface.labels = [label(m, question) for m in surface.generation]
    surface.assembly_labels = [label(m, question) for m in surface.assembly]
    return surface


def console(case: Case, root: Path) -> Surface:
    """The bus: the turn routed in full, as the daemon's Ask routes it."""
    root = root / "console"
    capture = _Capture([(200, "text/event-stream", _sse(ANSWER))])
    try:
        router, _registry, cache, assemblies = _router(case, root, capture)
        result = router.route(case.question)
    finally:
        capture.close()
    return _finish(Surface(
        source=result.source,
        requests=[json.loads(b) for b in capture.bodies],
        answer=result.text, cache_reads=cache.reads if cache else 0,
        assemblies=assemblies), case.question)


def panel(case: Case, root: Path) -> Surface:
    """The browser: the shipped message handler over the same router setup,
    the router detached as the daemon leaves it."""
    root = root / "panel"
    capture = _Capture([(200, "text/event-stream", _sse(ANSWER))])
    try:
        router, registry, cache, assemblies = _router(case, root, capture)
        router.detach_conversation()
        server = WebServer(router=router, llm=router._llm, tools=registry,
                           governance=None)
        server._sessions = SessionManager(sessions_dir=root / "transcripts")
        tab = ConnectionContext(client_id="both_surfaces", source_interface="web",
                                ws=_Socket(),
                                conversation=router.new_conversation())
        asyncio.run(server._handle_client_message(
            tab, {"type": "message", "content": case.question}))
    finally:
        capture.close()
    ends = [m for m in tab.ws.sent if m.get("type") == "stream_end"]
    frame = ends[-1] if ends else (tab.ws.sent[-1] if tab.ws.sent else {})
    return _finish(Surface(
        source=frame.get("source", ""),
        requests=[json.loads(b) for b in capture.bodies],
        answer=frame.get("full_response", frame.get("content")),
        cache_reads=cache.reads if cache else 0,
        assemblies=assemblies), case.question)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("INTERGEN_GLASS", raising=False)
    glass._glass = None
    yield tmp_path
    glass._glass = None


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_both_surfaces_send_the_same_request_and_deliver_the_same_answer(
        case, state):
    bus, browser = console(case, state), panel(case, state)
    # Controls: both surfaces reached the route the case names, and the
    # console carries what that route adds where the case says it does.
    assert (bus.source, browser.source) == (case.route, case.route)
    carried = bus.labels if case.adds_in == "request" else bus.assembly_labels
    assert case.adds in carried, (bus.assembly_labels, bus.labels)
    assert all("Today is <date>. Time: <time>." in m["content"]
               for s in (bus, browser) for m in s.generation[:1]), (
        "the clock line is not where the comparison expects it")
    assert browser.assembly == bus.assembly, (
        f"the panel assembled {browser.assembly_labels}, "
        f"the console {bus.assembly_labels}")
    assert browser.generation == bus.generation, (
        f"the panel sent {browser.labels}, the console {bus.labels}")
    assert browser.answer == bus.answer
    assert browser.cache_reads == bus.cache_reads


def _paths(state) -> list[dict]:
    return [r["detail"] for r in glass_rows.read(state / "state")
            if r.get("phase") == "decision" and r.get("event") == "path"]


def test_both_surfaces_record_the_wiki_grounding_as_handed_on(state):
    """The decision path says, on each surface, that the passage the route
    admitted went into the request, with its source, size and citation."""
    case = CASES[0]
    console(case, state)
    on_console = _paths(state)
    panel(case, state)
    on_panel = _paths(state)[len(on_console):]
    for surface, paths in (("console", on_console), ("panel", on_panel)):
        prompts = [p for path in paths for p in path["prompts"]]
        handed = [p for p in prompts if p.get("kind") == "grounding_handed_on"]
        assert len(handed) == 1, (surface, prompts)
        assert handed[0]["source"] == "wiki" and handed[0]["citation"], handed
        assert handed[0]["chars"] > len(PASSAGE)
        assert not [p for p in prompts
                    if p.get("kind") == "grounding_not_handed_on"], surface
