"""GATE — the INSTALLED router executes an explicitly recognised web search.

WHY THIS SITS BESIDE THE CLASSIFICATION GATE. Its neighbour,
test_router_classification_field_sentences.py, measures which intent the shipped matcher
resolves each field sentence to, and it reads 19/19. That number is true and it is not
what a person gets. Driving the INSTALLED router over the same nineteen sentences with
the real tool registry, ten were served: the web capability gate and the current-data
offer decide five of them before the matcher's verdict is ever consulted, and neither
stages anything. A gate can be green at its own layer while the product decides
somewhere else, so the router needs a gate of its own at the tier where the shipped
composition exists.

WHAT THIS ADDS OVER THE SOURCE-TREE GATE. intergen/tests/test_router_executes_recognised
_search.py runs in the ordinary suite with no embedding backend, so every verdict it
records is the KEYWORD layer's. That covers the five field sentences and the three
capability questions the keyword layer also resolves to web_search — the pair that has
to be told apart. It cannot cover the capability questions that only the EMBEDDING layer
recognises ("can you go online?", "are you able to search the web"), and those are
exactly where a future corpus change could make the router start searching the web for
the words of a question about itself. This gate covers them, against the real embedding
server, on the installed system.

EXPECTED TO FAIL ON R001.1 AS SHIPPED. That is the point of a red-first fixture. If it
passes on an unfixed R001.1 the fixture is wrong and must be reported, not softened.

NO TOOL IS EXECUTED BY THIS GATE. The shipped router RUNS the tool on the way to
building its answer, so ToolRegistry.execute is replaced with a recording stand-in.
Without that, running this gate would perform real web searches from the machine under
test and attempt real package operations.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

FIXTURES = (Path(__file__).resolve().parent / "fixtures" / "field_sentences.json")
EMBED_URL = "http://127.0.0.1:8081/v1/embeddings"

# The five sentences that reached a gate instead of a search, verbatim from the fixture.
FIELD_DISPATCH_INDICES = (7, 9, 11, 12, 14)

# Capability questions the keyword layer does NOT take — the ones only the embedding
# layer recognises, which is what makes them this gate's own subject.
EMBEDDING_ONLY_CAPABILITY_QUESTIONS = [
    "can you go online?",
    "are you able to search the web",
    "you can't search the web?",
    "can't you search the web",
]


def _real_embedder(texts: list[str]):
    """The same request shape the shipped llama manager uses. No mock, no stub."""
    if not texts:
        return []
    body = json.dumps({"input": list(texts), "model": "embedding"}).encode()
    req = urllib.request.Request(
        EMBED_URL, data=body, headers={"Content-Type": "application/json"})
    # 300s, not the 120s the classification gate beside this one uses. MEASURED
    # 2026-08-25: this gate embeds a query per ROUTED TURN rather than once per
    # sentence, and a run against the real server on this machine timed out mid-test at
    # 120s and reported a routing failure that was really a measurement failure. A gate
    # whose instrument gives up is not reporting on the product.
    with urllib.request.urlopen(req, timeout=300.0) as resp:
        data = json.loads(resp.read())
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != len(texts):
        raise RuntimeError(
            f"embedding server returned {len(rows) if isinstance(rows, list) else -1} "
            f"rows for {len(texts)} inputs")
    return [r["embedding"] for r in sorted(rows, key=lambda r: r.get("index", 0))]


@pytest.fixture(scope="module")
def router_parts(installed_intergen_dir):
    """The SHIPPED router, matcher, corpus and tool registry, with the REAL embedder.

    A router that cannot reach the embedding server is a FAILURE here, never a skip: a
    run with the embedding layer silently absent would measure the keyword layer alone
    and report a number that looks like it describes the router.
    """
    import sys
    sys.path.insert(0, str(installed_intergen_dir.parent))
    from intergen.interfaces.types import ToolResult
    from intergen.intents import register_all_intents
    from intergen.llm import LLMRouter
    from intergen.router import ConversationRouter
    from intergen.semantic import SemanticMatcher
    from intergen.tool_registry import ToolRegistry

    try:
        probe = _real_embedder(["probe"])
    except (urllib.error.URLError, OSError, RuntimeError) as exc:
        pytest.fail(
            "The embedding server this gate needs is not answering at "
            f"{EMBED_URL} ({exc}). REFUSING to report a routing verdict measured with "
            "the embedding layer absent — that verdict would describe the keyword "
            "layer alone while appearing to describe the router.")
    if not probe or not probe[0]:
        pytest.fail(f"The embedding server at {EMBED_URL} returned an empty vector.")

    class NonRaisingLLM(LLMRouter):
        """The shipped client with only its network entry points replaced.

        ``stream`` answers, because a dispatched search is phrased by the model and a
        stand-in that refused would fail the turns this gate wants to see succeed.
        ``stream_with_tools`` fails loudly: on the locked floor the model never decides
        a tool, so a turn that got there means the deterministic layers let it go.
        """

        INERT = ("Here is what the search returned, summarised from the tool output "
                 "above so the answer stays grounded in what was retrieved.")

        def stream(self, messages, **_kw):
            yield self.INERT

        def stream_with_tools(self, messages, *, tools, **_kw):
            raise AssertionError(
                "The model was offered tools on the locked floor — the deterministic "
                "layers let this sentence go.")

    matcher = SemanticMatcher(embedder=_real_embedder)
    register_all_intents(matcher)
    if matcher.get_intent_count() <= 0:
        pytest.fail("The shipped intent corpus registered zero intents.")
    registry = ToolRegistry()
    registry.discover_tools()
    if "web_search" not in registry.get_all_names():
        pytest.fail(
            "web_search is not registered on this system, so every verdict below would "
            "describe a machine without the tool rather than the shipped one.")
    executed: list[str] = []

    def recording_execute(call, **_kw):
        executed.append(call.name)
        return ToolResult(call_id=getattr(call, "call_id", "") or "", name=call.name,
                          content="[tool execution recorded, not performed]",
                          success=True)

    registry.execute = recording_execute
    router = ConversationRouter(tool_registry=registry, semantic_matcher=matcher,
                                llm=NonRaisingLLM({}), embedder=_real_embedder)
    return router, executed


def _sentences_by_index() -> dict[int, dict]:
    payload = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return {e["index"]: e for e in payload["sentences"]}


def test_the_five_gate_intercepted_sentences_dispatch_web_search(router_parts,
                                                                 record_property):
    """The five turns the field user typed and never got served."""
    router, executed = router_parts
    entries = _sentences_by_index()
    missing = [i for i in FIELD_DISPATCH_INDICES if i not in entries]
    if missing:
        pytest.fail(f"the fixture no longer carries sentences {missing}")

    rows, wrong = [], []
    for index in FIELD_DISPATCH_INDICES:
        entry = entries[index]
        router.reset_conversation_state()
        before = len(executed)
        result = router.route(entry["sentence"])
        staged = [c.name for c in getattr(result, "tool_calls", [])]
        ok = "web_search" in staged and executed[before:] == ["web_search"]
        rows.append((index, ok, getattr(result, "source", None), staged, entry))
        if not ok:
            wrong.append(rows[-1])

    record_property("gate_intercepted_dispatched", len(rows) - len(wrong))
    record_property("gate_intercepted_total", len(rows))
    report = [""] + [
        f"  {i:2d} {'DISPATCHED' if ok else 'NOT DISPATCHED'} source={src!r} "
        f"staged={staged}\n     :: {e['sentence']}"
        for i, ok, src, staged, e in rows] + [""]
    if wrong:
        report.append(
            "Each NOT DISPATCHED line is a turn a real first-time user typed. The "
            "matcher recognises every one of them as web_search; the capability gate "
            "and the current-data offer answered first and staged nothing.")
    assert not wrong, "\n".join(report)


def test_an_embedding_recognised_capability_question_is_still_answered(router_parts):
    """The half the source-tree gate cannot reach.

    These sentences are resolved to web_search by the EMBEDDING layer only. If the gate
    that steps aside for a recognised dispatch ever relaxed to recognition alone, these
    would be dispatched as searches for their own wording — an honest, grounded answer
    about what the assistant can do replaced by a nonsense search.
    """
    router, executed = router_parts
    wrong = []
    for sentence in EMBEDDING_ONLY_CAPABILITY_QUESTIONS:
        router.reset_conversation_state()
        before = len(executed)
        result = router.route(sentence)
        staged = [c.name for c in getattr(result, "tool_calls", [])]
        if staged or executed[before:]:
            wrong.append((sentence, getattr(result, "source", None), staged))
    assert not wrong, "\n".join(
        [""] + [f"  SEARCHED source={src!r} staged={staged}\n    :: {s}"
                for s, src, staged in wrong]
        + ["", "A question about the assistant's own capability was turned into a web "
           "search for the words of the question."])


def test_the_negative_field_sentences_still_stage_nothing(router_parts):
    """The fixture's own negatives, at router level.

    Six of the nineteen are owed no tool at all. A change that made the router keener to
    dispatch would show up here first, which is why they are measured in the same run as
    the five rather than argued about separately.
    """
    router, executed = router_parts
    entries = _sentences_by_index()
    negatives = [e for e in entries.values() if e["expected_verdict"] != "recognised"]
    assert negatives, "the fixture carries no negatives — it cannot hold precision"
    wrong = []
    for entry in negatives:
        router.reset_conversation_state()
        before = len(executed)
        result = router.route(entry["sentence"])
        staged = [c.name for c in getattr(result, "tool_calls", [])]
        if staged or executed[before:]:
            wrong.append((entry["index"], entry["sentence"],
                          getattr(result, "source", None), staged))
    assert not wrong, "\n".join(
        [""] + [f"  {i:2d} STAGED {staged} source={src!r}\n     :: {s}"
                for i, s, src, staged in wrong]
        + ["", "A sentence the fixture says needs no tool was dispatched anyway."])
