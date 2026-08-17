# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Training-bank JSONL -> SFT messages JSONL (the training-set emitter).

The evaluation corpus is EVAL-shaped: assertions describe the acceptance
boundary of a correct answer, they are not the answer. A trainer needs
(prompt, gold completion) pairs. This module is the bridge the training-loop
design names (`corpus_to_sft`): it reads a TRAINING BANK — entries in the
demand-corpus schema plus a required per-turn ``gold`` — validates everything
fail-closed, and emits the trl/HF chat-format JSONL the training stack
consumes, preserving provenance as the training-set audit trail.

EXTENDS, does not replace, the corpus toolchain: base-schema validation is
:func:`corpus_loader.validate_entry`, JSONL parsing is
:func:`corpus_loader.iter_corpus_records`. What this module adds is the gold
layer and the rendering.

THE GOLD LAYER, and why each rule is fail-closed:

* Every turn carries exactly one gold shape — ``{"content": <prose>}`` or
  ``{"tool_call": {"name", "arguments"}}``. A turn without gold is not a
  training example, and silently skipping it would ship a bank that LOOKS
  authored while training on a subset (the silent-loss class).
* A dispatch gold is validated against the REAL tool registry — the same
  discovery the daemon runs, no shadow list that could drift: the tool must
  exist, every argument key must be in that tool's declared schema, every
  schema-required key must be present.
* ``arguments`` MUST carry a valid ``source_of_request``
  (user_direct / user_implied / ingress_derived). The serving stack pops this
  field to build the ToolCall and the dispatcher REFUSES a call without it
  (D-008 §5.3 no-fallback) — so a gold dispatch without it would train the
  model toward emissions the runtime rejects.
* A dispatch gold is emitted as a STRUCTURED tool call — an assistant message
  with ``content: null`` and a ``tool_calls`` entry whose ``arguments`` are a
  **mapping** — because that is the only form the chat template renders as a
  tool call. Argument keys are sorted so the emission is deterministic and
  diffs are honest.

  Decided 2026-08-13, by rendering the candidate shapes through the shipped
  template rather than by reading. The template
  (``intergen/data/internvl-tool-template.jinja``) writes
  ``<tool_call><function=NAME>…</function></tool_call>`` and emits the
  ``<parameter=…>`` blocks only under its own guard ``{%- if
  tool_call.arguments is mapping %}``. Two shapes this module used to emit
  therefore rendered wrong:

  - the Hermes block ``<tool_call>{json}</tool_call>`` written into the
    assistant's CONTENT rendered as that literal text and produced no
    ``<function=`` block at all — while the template's own system block told the
    model, in the same rendered row, that a call MUST be an inner
    ``<function=…></function>`` block. Prompt and label disagreed;
  - a structured call whose ``arguments`` were a JSON STRING (the OpenAI wire
    convention) rendered as the function name with EVERY argument dropped.

  The proof is ``tests/intergen/test_corpus_to_sft_template_render.py`` (a real
  render through the real template) plus
  ``intergen/tests/test_corpus_to_sft_tool_call_shape.py`` (the shipped,
  dependency-free half, which also pins the template guard).

* Both emitted shapes go through :func:`assert_renderable_tool_calls`, a
  fail-closed post-condition on EVERY sample: no assistant message may carry
  literal ``<tool_call>`` text, and every call's arguments must be a NON-EMPTY
  mapping. Non-empty matters as much as mapping — an empty mapping is a mapping
  and still renders no parameters at all. It is a checked gate rather than a
  convention because a convention is what let both shapes ship.
* The system prompt is REQUIRED and comes from a file the caller names.
  Training without the serving-shaped system prompt would teach behaviour
  that evaporates under the real prompt; an empty prompt is refused rather
  than silently training prompt-free.

Each entry additionally carries ``training_provenance`` with a non-empty
``class`` (which authoring class the example serves — target classes,
retention, contrastive) and ``origin`` (authored / expanded-from-cell /
donated). It flows into every emitted sample.

WHAT THIS MODULE DOES NOT DO, stated rather than implied: the
embedding-similarity dedup pass (cosine > ~0.85) and the retention-ratio
balance check are separate calibrated steps in the authoring lane; the
held-out-family exclusion is an authoring rule enforced at authoring time
(the bank spec forbids authoring against held-out cells) — a mechanical
guard needs the split map and is a registered candidate, not quietly
half-implemented here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from intergen.interfaces.provenance import Provenance
from intergen.tests.corpus_loader import (
    CorpusError, iter_corpus_records, validate_entry,
)

VALID_ORIGINS = frozenset({"authored", "expanded-from-cell", "donated"})
PROVENANCE_VALUES = frozenset(p.value for p in Provenance)

# The literal text that must never appear in an emitted assistant message: a
# call written as content is a call the template does not render.
_LITERAL_CALL_MARKER = "<tool_call>"


def _require(cond: bool, locator: str, msg: str) -> None:
    if not cond:
        raise CorpusError(f"{locator}: {msg}")


def load_tool_schemas() -> dict[str, dict]:
    """name -> parameters (JSON schema) for every REAL registered tool.

    Runs the daemon's own discovery so the validation surface cannot drift
    from what actually dispatches. Refuses an empty discovery — an emitter
    that validated against zero tools would wave every dispatch gold through.
    """
    from intergen.tool_registry import ToolRegistry
    reg = ToolRegistry()
    reg.discover_tools()
    schemas = {s.name: s.parameters for s in reg.get_tool_schemas()}
    if not schemas:
        raise CorpusError(
            "corpus_to_sft: tool discovery returned no tools — cannot "
            "validate dispatch gold against an empty registry")
    return schemas


def _validate_tool_call(call, *, locator: str,
                        tool_schemas: dict[str, dict]) -> None:
    """One tool call against the live registry — shared by dispatch gold and
    tool_flow history (the same drift would poison either)."""
    _require(isinstance(call, dict), locator, "tool call must be an object")
    name = call.get("name")
    _require(isinstance(name, str) and name in tool_schemas, locator,
             f"tool call name {name!r} is not a registered tool "
             f"(known: {', '.join(sorted(tool_schemas))})")
    args = call.get("arguments")
    _require(isinstance(args, dict), locator,
             "tool call 'arguments' must be an object")
    src = args.get("source_of_request")
    _require(src in PROVENANCE_VALUES, locator,
             f"'arguments.source_of_request' is {src!r} — must be one of "
             f"{sorted(PROVENANCE_VALUES)}; the dispatcher refuses a call "
             "without it, so a gold without it trains toward rejection")
    schema = tool_schemas[name]
    props = set(schema.get("properties", {}))
    unknown = set(args) - props - {"source_of_request"}
    _require(not unknown, locator,
             f"arguments carry keys the {name} schema does not declare: "
             f"{sorted(unknown)}")
    missing = set(schema.get("required", [])) - set(args)
    _require(not missing, locator,
             f"arguments missing keys the {name} schema requires: "
             f"{sorted(missing)}")


def validate_tool_flow(turn: dict, *, locator: str,
                       tool_schemas: dict[str, dict]) -> None:
    """A tool_flow turn trains the POST-dispatch composition — the reply the
    model writes after a dispatch resolved (the ``continue_after_tool_call``
    serving path). All fail-closed:

    * the flow's tool_call is validated exactly like a dispatch gold — a
      history turn naming a tool that cannot dispatch would train against a
      serving state that cannot occur;
    * tool_result must be non-empty — the result (or refusal reason) is the
      substance the composition is trained to be honest about;
    * success/executed must both be present booleans — they select the REAL
      synthesis instruction, and a defaulted pair would silently train the
      wrong one (the deny/fail conflation the serving prompt exists to split);
    * the turn's gold must be prose content — the flow turn trains composition,
      not emission; dispatch emission is its own turn shape.
    """
    flow = turn["tool_flow"]
    _require(isinstance(flow, dict), locator, "'tool_flow' must be an object")
    _validate_tool_call(flow.get("tool_call"), locator=f"{locator} tool_flow",
                        tool_schemas=tool_schemas)
    tr = flow.get("tool_result")
    _require(isinstance(tr, str) and tr.strip() != "", locator,
             "'tool_flow.tool_result' must be a non-empty string")
    for key in ("success", "executed"):
        _require(isinstance(flow.get(key), bool), locator,
                 f"'tool_flow.{key}' must be a present boolean — it selects "
                 "the real synthesis instruction")
    gold = turn.get("gold")
    _require(isinstance(gold, dict) and "content" in gold
             and "tool_call" not in gold, locator,
             "a tool_flow turn's gold must be prose 'content' — the flow "
             "turn trains composition, not emission")


def validate_gold(turn: dict, *, locator: str,
                  tool_schemas: dict[str, dict]) -> None:
    """Raise CorpusError unless this turn's gold is well-formed."""
    if "tool_flow" in turn:
        validate_tool_flow(turn, locator=locator, tool_schemas=tool_schemas)
        # gold shape (non-empty content) still checked by the shared path below.
    gold = turn.get("gold")
    _require(isinstance(gold, dict), locator,
             "turn must carry a 'gold' object (a training bank is not an "
             "eval bank — every turn needs its completion)")
    has_content = "content" in gold
    has_call = "tool_call" in gold
    _require(has_content != has_call, locator,
             "'gold' must carry exactly one of 'content' or 'tool_call'")

    if has_content:
        _require(isinstance(gold["content"], str) and gold["content"].strip() != "",
                 locator, "'gold.content' must be a non-empty string")
        return

    call = gold["tool_call"]
    _require(isinstance(call, dict), locator, "'gold.tool_call' must be an object")
    name = call.get("name")
    _require(isinstance(name, str) and name in tool_schemas, locator,
             f"'gold.tool_call.name' {name!r} is not a registered tool "
             f"(known: {', '.join(sorted(tool_schemas))})")
    args = call.get("arguments")
    _require(isinstance(args, dict), locator,
             "'gold.tool_call.arguments' must be an object")

    src = args.get("source_of_request")
    _require(src in PROVENANCE_VALUES, locator,
             f"'arguments.source_of_request' is {src!r} — must be one of "
             f"{sorted(PROVENANCE_VALUES)}; the dispatcher refuses a call "
             "without it, so a gold without it trains toward rejection")

    schema = tool_schemas[name]
    props = set(schema.get("properties", {}))
    unknown = set(args) - props - {"source_of_request"}
    _require(not unknown, locator,
             f"arguments carry keys the {name} schema does not declare: "
             f"{sorted(unknown)}")
    missing = set(schema.get("required", [])) - set(args)
    _require(not missing, locator,
             f"arguments missing keys the {name} schema requires: "
             f"{sorted(missing)}")


def validate_training_entry(obj: dict, *, locator: str,
                            tool_schemas: dict[str, dict]) -> None:
    """Base demand-corpus schema + the training layer, all fail-closed."""
    validate_entry(obj, locator=locator)
    tp = obj.get("training_provenance")
    _require(isinstance(tp, dict), locator,
             "entry must carry a 'training_provenance' object")
    _require(isinstance(tp.get("class"), str) and tp["class"].strip() != "",
             locator, "'training_provenance.class' must be a non-empty string")
    _require(tp.get("origin") in VALID_ORIGINS, locator,
             f"'training_provenance.origin' must be one of {sorted(VALID_ORIGINS)}")
    for ti, turn in enumerate(obj["turns"]):
        validate_gold(turn, locator=f"{locator} turn[{ti}]",
                      tool_schemas=tool_schemas)


def _structured_call(call: dict, *, index: int = 0) -> dict:
    """One gold tool_call -> the OpenAI-shaped entry the chat template renders.

    ``arguments`` is the MAPPING itself, key-sorted. Sorting is what pins
    determinism now that there is no ``json.dumps(sort_keys=True)`` in the path;
    the mapping is what makes the template emit ``<parameter=…>`` when the
    template is applied DIRECTLY, as this training pipeline applies it. (The
    serving engine parses a JSON-object string into a mapping first, so it
    renders either shape the same — see :func:`render_tool_flow`.)
    """
    return {
        "id": f"call_{index}",
        "type": "function",
        "function": {"name": call["name"],
                     "arguments": dict(sorted(call["arguments"].items()))},
    }


def render_gold_message(gold: dict) -> dict:
    """The assistant MESSAGE for one turn's gold.

    Returns a message dict rather than a string: a dispatch gold's answer IS the
    call, so it belongs in ``tool_calls`` with null content, which is the only
    shape the chat template turns into a tool call. Prose gold is unchanged.
    """
    if "content" in gold:
        return {"role": "assistant", "content": gold["content"]}
    return {"role": "assistant", "content": None,
            "tool_calls": [_structured_call(gold["tool_call"])]}


def assert_renderable_tool_calls(sample: dict, *, locator: str) -> None:
    """Fail-closed post-condition: every call in this sample must render.

    Raises :class:`CorpusError` naming the offending message. Three refusals,
    each one a shape that was measured against the real template:

    * literal ``<tool_call>`` text in an assistant's content — renders as text,
      never as a call;
    * ``arguments`` that are not a mapping (a JSON string is the common case) —
      renders the function name with every argument dropped;
    * ``arguments`` that are an empty mapping — a mapping, and still renders no
      ``<parameter=…>`` blocks, so "is a mapping" alone is not the property that
      matters.

    SCOPE OF THE SECOND REFUSAL, measured 2026-08-13 and worth stating exactly,
    because the unqualified claim is wrong: "a JSON string drops the arguments"
    is true of DIRECT template application — what this training pipeline does,
    rendering the template itself over the emitted JSONL — and it is NOT true of
    the serving engine. llama-server parses ``function.arguments`` as JSON
    before it applies the template, so at serving time a JSON-object string and
    the mapping render byte-identically. This gate therefore exists for the
    TRAINING side, where nothing does that parsing for us. It is not a claim
    that the serving path is broken.

    If a future gold legitimately needs to QUOTE tool-call syntax — a turn that
    teaches the user what the format looks like, rather than making a call —
    the escape route is to put that text anywhere other than an assistant
    message's ``content``: in the user turn or in a tool result. A prose gold
    CANNOT carry it — a gold's prose becomes assistant content
    (:func:`render_gold_message`), exactly the position this gate refuses, so a
    prose gold may only DESCRIBE the format without the literal marker. The
    refusal below is scoped to assistant content precisely because that is the
    only position the template turns into a rendered row of the model's own
    output.
    """
    for mi, m in enumerate(sample.get("messages", [])):
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if isinstance(content, str) and _LITERAL_CALL_MARKER in content:
            raise CorpusError(
                f"{locator}: message[{mi}] carries literal "
                f"'{_LITERAL_CALL_MARKER}' text in its content. The chat "
                f"template renders that as text, not as a tool call, and its "
                f"own system block forbids the form in the same rendered row. "
                f"Emit the call in 'tool_calls' instead. If this gold is meant "
                f"to QUOTE the syntax rather than call anything, move that text "
                f"out of assistant content — a user turn or a tool result "
                f"carries it without teaching the model to emit it. A prose "
                f"gold cannot: gold prose becomes assistant content, this same "
                f"refused position — describe the format without the literal "
                f"marker instead.")
        for ci, call in enumerate(m.get("tool_calls") or []):
            fn = call.get("function")
            if not isinstance(fn, dict):
                raise CorpusError(
                    f"{locator}: message[{mi}] tool_call[{ci}] has no "
                    f"'function' object")
            args = fn.get("arguments")
            if not isinstance(args, dict):
                raise CorpusError(
                    f"{locator}: message[{mi}] tool_call[{ci}] arguments are "
                    f"{type(args).__name__}, not a mapping. The template emits "
                    f"<parameter=…> blocks only for a mapping, so this call "
                    f"would render with every argument dropped.")
            if not args:
                raise CorpusError(
                    f"{locator}: message[{mi}] tool_call[{ci}] would render "
                    f"with no arguments — its arguments mapping is empty. An "
                    f"empty mapping is still a mapping, so the mapping check "
                    f"alone does not catch it.")


def _synthesis_instruction(*, success: bool, executed: bool) -> str:
    """The REAL serving instruction for a resolved dispatch — called on the
    shipped ``LLMRouter._synthesis_prompt``, never a copied string, so the
    training text cannot drift from what serving sends."""
    from intergen.llm import LLMRouter
    return LLMRouter._synthesis_prompt(object.__new__(LLMRouter),
                                       success=success, executed=executed)


def render_tool_flow(messages: list[dict], turn: dict) -> None:
    """Append a tool_flow turn as the serving path renders it.

    Mirrors what ``LLMRouter.continue_after_tool_call`` RENDERS: the assistant
    message carries the STRUCTURED tool call (content null), the tool-role
    message carries the result or refusal reason, and the user-role instruction
    is the real ``_synthesis_prompt`` output.

    "Renders", not "is", and the difference is deliberate. That function passes
    ``json.dumps(tool_call.arguments)`` — a JSON string, which is what the
    OpenAI wire format specifies — while this emits the mapping. The two reach
    the SAME rendered bytes because llama-server parses ``function.arguments``
    as JSON before applying the template; measured 2026-08-13 against the real
    engine (llama.cpp b8796) with the shipped template
    (``internvl-tool-template.jinja``, sha256 7f0e529032c25183…): the two shapes
    produced identical prompts, 87 prompt tokens each, against 66 for a call
    with no parameters. So the serving path is NOT to be "fixed" into sending a
    mapping — that would trade a spec-compliant wire value for one that only
    works because this particular engine is lenient.

    The mapping is required HERE because the training pipeline applies the
    template directly to this JSONL, and nothing in that path does the parsing
    the engine does; a JSON string would render the function name with every
    argument dropped. :func:`assert_renderable_tool_calls` enforces it. The sort
    is this module's convention for deterministic emission — serving preserves
    parse order, which is unknowable here.
    """
    flow = turn["tool_flow"]
    call = flow["tool_call"]
    messages.append({"role": "user", "content": turn["user"]})
    messages.append({
        "role": "assistant",
        "content": None,
        "tool_calls": [_structured_call(call)],
    })
    messages.append({"role": "tool", "tool_call_id": "call_0",
                     "content": flow["tool_result"]})
    messages.append({
        "role": "user",
        "content": _synthesis_instruction(success=flow["success"],
                                          executed=flow["executed"]),
    })
    messages.append({"role": "assistant",
                     "content": turn["gold"]["content"]})


def entry_to_sample(obj: dict, *, system_prompt: str) -> dict:
    """One validated entry -> one SFT sample (full multi-turn message list).

    The renderability gate fires HERE, not in :func:`emit`, so it covers every
    path that produces a sample — including callers that build samples directly.
    A check the caller has to remember is the convention this replaces.
    """
    messages = [{"role": "system", "content": system_prompt}]
    for turn in obj["turns"]:
        if "tool_flow" in turn:
            render_tool_flow(messages, turn)
            continue
        messages.append({"role": "user", "content": turn["user"]})
        messages.append(render_gold_message(turn["gold"]))
    sample = {
        "messages": messages,
        "provenance": {
            "id": obj["id"],
            "category": obj["category"],
            "intent": obj["intent"],
            "training": obj["training_provenance"],
            "corpus": obj.get("provenance", {}),
        },
    }
    assert_renderable_tool_calls(sample, locator=obj.get("id", "<entry>"))
    return sample


def emit(bank_paths: list[str | Path], *, system_prompt: str,
         tool_schemas: dict[str, dict] | None = None) -> list[dict]:
    """Validate every bank entry and return the SFT samples.

    The first invalid entry aborts the whole emission with its locator — a
    partially-emitted training set that silently dropped entries would train
    on a different distribution than the one the authoring record claims.
    """
    if not system_prompt or not system_prompt.strip():
        raise CorpusError(
            "corpus_to_sft: the system prompt is empty — training without "
            "the serving-shaped prompt is refused, never defaulted")
    schemas = tool_schemas if tool_schemas is not None else load_tool_schemas()
    samples: list[dict] = []
    for path in bank_paths:
        records = iter_corpus_records(path)
        _require(len(records) > 0, str(path), "bank is empty")
        for lineno, obj in enumerate(records, start=1):
            locator = f"{path}:entry#{lineno}"
            validate_training_entry(obj, locator=locator, tool_schemas=schemas)
            samples.append(entry_to_sample(obj, system_prompt=system_prompt))
    return samples


def distribution_report(samples: list[dict]) -> str:
    """Per-class / per-shape counts — the balance guard's raw numbers.

    A trained dispatch and a tool_flow history call now carry the SAME shape
    (content null + tool_calls), so the report can no longer tell them apart by
    content the way it used to. It reads the position instead: a history call is
    the one whose NEXT message is the tool-role result. That is what actually
    distinguishes them — the history call is context the flow replays, the
    dispatch is the completion the turn trains.
    """
    by_class: dict[str, int] = {}
    dispatch = prose = history = 0
    for s in samples:
        cls = s["provenance"]["training"]["class"]
        by_class[cls] = by_class.get(cls, 0) + 1
        msgs = s["messages"]
        for i, m in enumerate(msgs):
            if m["role"] != "assistant":
                continue
            if m.get("tool_calls"):
                nxt = msgs[i + 1] if i + 1 < len(msgs) else None
                if nxt is not None and nxt.get("role") == "tool":
                    history += 1
                else:
                    dispatch += 1
            else:
                prose += 1
    lines = [f"samples: {len(samples)}",
             f"assistant turns: dispatch {dispatch} / prose {prose} / "
             f"dispatch-history {history}"]
    for cls in sorted(by_class):
        lines.append(f"  class {cls}: {by_class[cls]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Emit trl/HF chat-format SFT JSONL from a training bank.")
    ap.add_argument("--bank", action="append", required=True,
                    help="training-bank JSONL (repeatable)")
    ap.add_argument("--system-prompt-file", required=True,
                    help="file holding the train-time system prompt")
    ap.add_argument("--out", required=True, help="output JSONL path")
    args = ap.parse_args(argv)

    system_prompt = Path(args.system_prompt_file).read_text(encoding="utf-8")
    samples = emit(args.bank, system_prompt=system_prompt)
    with open(args.out, "w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(distribution_report(samples))
    print(f"wrote {len(samples)} samples -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
