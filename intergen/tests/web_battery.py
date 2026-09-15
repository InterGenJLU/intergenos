#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The fresh-user web battery: plain questions, over the shipped panel path,
against an INSTALLED machine, graded strictly, sealed.

WHY THIS EXISTS. The assistant shipped a release on which every web-chat turn
that needed the model ended in the red "Something went wrong on my end" banner
(a conversation-binding defect in intergen/web_server.py, 2026-09-14). No test
drove the shipped web surface — a real daemon, a real socket — on an installed
machine; the unit tests and the scenario harness bind the conversation for
themselves, so the front door a person actually uses had no prove-against-
reality step before the mint. This battery is that step: it asks the questions
a person asks in their first five minutes and refuses to call any of them
answered unless the frames say so.

WHAT IT DRIVES. ``ws://127.0.0.1:8089/ws`` on the target, reached through an
ssh port-forward the battery opens itself (``ssh -N -L``). The runner needs no
root anywhere: the panel token is read over the same ssh from the target
user's own ``~/.config/intergen/web-token`` (the file the daemon writes for its
local UI — see ``ws_harness.default_token``), held in memory only and never
written into the record. Every question in the battery goes down ONE socket,
because the server binds one conversation per connection and a follow-up must
see the turn before it (``ws_harness.WSConversation``).

HOW IT GRADES — every predicate is a FAIL, none is a warning:

  * an ``error`` frame ends the turn                          -> FAIL
  * the red-banner text appears in the delivered text         -> FAIL
  * the terminal source is a refusal, or the text is one of the
    canned non-answers (the "didn't quite catch that" nudge, the
    "could not tell which conversation" refusal)              -> FAIL
  * the row-22 sentence (arithmetic, "do not use tools ...") draws a
    ``gate_prompt``, a ``tool_ack``, a ``tool_executed``, a decomposition
    or a tool route                                            -> FAIL
  * a question marked MODEL-REQUIRED did not get a model answer
    (see "origin" below)                                       -> FAIL
  * no terminal frame inside the deadline, or a server close   -> FAIL

WHAT "CAME FROM THE MODEL" CAN MEAN HERE. The shipped frames carry the
server's OWN description of the turn: ``stream_start.source``,
``stream_end.used_llm``, ``stream_end.source``, ``stream_end.stats.tokens``
and the streamed ``stream_token`` frames. That is a self-declaration, not an
independent proof — a server that lied in its frames would pass this
predicate. The second witness this battery can reach is the daemon's own turn
trace on the target (``~/.local/state/intergen/glass.jsonl``, read over the
same ssh, read-only): the ``delivery/final`` row for the turn id the frames
named, with ``streamed: true`` and an answer linkage of kind ``model`` (or
``dispatch`` when a tool ran). When that file is readable the origin predicate
requires BOTH; when it is not, the verdict stands on the frames alone and the
record SAYS SO (``summary.json: origin_evidence``). Neither witness is
cryptographic. That gap is named here so a reader of the record does not take
"model" for more than the two records it rests on.

WHAT IT REFUSES, before any record exists (the ``run-installed-gates.py``
shape): running from inside a source checkout — its own file or the working
directory under a ``.git`` ancestor — because a battery run from a checkout
records a moving tree; and writing into an output directory that already
exists, because a record is written once. It runs from a pinned copy of the
tree (``git archive <sha> | tar -x``) and takes that sha on the command line as
a DECLARED fact; the sha256 of its own source and of the harness it imported
are MEASURED and recorded beside it, kept apart.

THE RECORD. ``<output>/`` holds ``frames.jsonl`` (every frame the server sent,
whole, with the wall time it arrived), ``verdicts.tsv`` (one line per
question), ``summary.json`` (counts, the target identity read from
``pkm info intergen`` over ssh, the tree sha, the file hashes, the exact
commands, wall times, the origin-evidence statement), ``target-identity.txt``
(the raw pkm/hostname output), ``glass-excerpt.jsonl`` (the target's trace
rows for the turn ids this run minted, when readable) and ``SHA256SUMS``,
written LAST. Exit status: 0 = every question PASS; 1 = at least one FAIL;
2 = refused or usage error (no record written).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_REFUSED = 2

SEAL_NAME = "SHA256SUMS"
TARGET_PORT = 8089
TOKEN_PATH = ".config/intergen/web-token"
GLASS_PATH = ".local/state/intergen/glass.jsonl"
DEFAULT_DEADLINE_S = 180.0

# The row-22 sentence, VERBATIM (tracker gating row 22 / walk row S1-16).
ROW22_SENTENCE = (
    "Calculate 17 times 23 mentally. Reply with the number only. Do not use "
    "tools, run commands, access files, or contact external services."
)

# The user-visible text web_server.py sends when a turn crashes (its
# "internal_error" frame) — the red banner of the field failure.
BANNER_TEXT = "Something went wrong on my end with that one."

# Text the daemon delivers when it did NOT answer: the empty-completion nudge
# (web_server._stream_llm_response) and the unbound-conversation refusal
# (router._route_named). A turn that ends in one of these was not answered.
CANNED_NON_ANSWERS = (
    "Sorry — I didn't quite catch that.",
    "I could not tell which conversation this message belongs to",
)
# Route sources that are refusals by construction, never an answer to a
# plain question.
REFUSAL_SOURCES = frozenset({"conversation_unbound", "safety_decline"})
# Sources under which the text was generated by the model (streamed).
MODEL_SOURCES = frozenset({"llm_freeform", "llm_tools", "system_map"})
# Frames that mean the turn asked for, or performed, an action.
ACTION_FRAMES = frozenset({"gate_prompt", "tool_ack", "tool_executed"})


@dataclass(frozen=True)
class Question:
    key: str
    shape: str
    text: str
    # True: the answer must come from the model (a fast path is not an
    # answer to this question). False: a code-owned answer (identity,
    # capability, live system data) is legitimate; the refusal/error/banner
    # predicates still apply.
    model_required: bool
    # True: any gate prompt, tool acknowledgement, tool execution,
    # decomposition or tool route on this question is a FAIL.
    no_action: bool = False


# At least twelve, fixed. The order matters: the follow-up and the elliptical
# reply read the turn before them, and the last question reads the first.
QUESTIONS: tuple[Question, ...] = (
    Question("q01", "greeting", "hi", model_required=False),
    Question("q02", "greeting-identity", "Hello! Who are you?",
             model_required=False),
    Question("q03", "capability", "What can you do?", model_required=False),
    Question("q04", "fact", "What year was Linux first released?",
             model_required=True),
    Question("q05", "follow-up-prior-turn", "Who created it?",
             model_required=True),
    Question("q06", "live-data-machine", "How much free disk space do I have?",
             model_required=False),
    Question("q07", "elliptical", "And memory?", model_required=False),
    Question("q08", "row-22-verbatim", ROW22_SENTENCE, model_required=True,
             no_action=True),
    Question("q09", "social-elliptical", "Thanks!", model_required=False),
    Question("q10", "live-data-machine", "Is my system up to date?",
             model_required=False),
    Question("q11", "fact", "What is the capital of Australia?",
             model_required=True),
    Question("q12", "plain-question", "Why is the sky blue?",
             model_required=True),
    Question("q13", "plain-question",
             "Can you explain what a kernel is, in one sentence?",
             model_required=True),
    Question("q14", "prior-turn-context", "What was my first question to you?",
             model_required=True),
)


def refuse(message: str) -> None:
    print(f"[web-battery] REFUSED: {message}", file=sys.stderr)
    print("[web-battery] No record was written.", file=sys.stderr)
    sys.exit(EXIT_REFUSED)


def _checkout_above(start: Path) -> Path | None:
    for d in [start, *start.parents]:
        if (d / ".git").exists():
            return d
    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ssh_base(target: str, ssh_port: int) -> list[str]:
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            "-p", str(ssh_port), target]


def _ssh_read(target: str, ssh_port: int, remote_cmd: str,
              timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(_ssh_base(target, ssh_port) + [remote_cmd],
                          capture_output=True, text=True, timeout=timeout)


@dataclass
class Verdict:
    key: str
    shape: str
    question: str
    verdict: str                    # PASS | FAIL
    reasons: list[str] = field(default_factory=list)
    turn_id: str = ""
    terminal: str = ""              # stream_end | response | error | none
    source: str = ""
    origin: str = ""                # model | code | refusal | error | none
    glass_corroborated: str = ""    # yes | no | unreadable
    elapsed_s: float = 0.0
    text_head: str = ""


def _grade(q: Question, r: Any, glass_rows: dict[str, list[dict]] | None,
           ) -> Verdict:
    """Apply the predicates to one collected turn. Pure; no I/O."""
    types = [m.get("type", "") for m in r.messages]
    terminal_frame = next(
        (m for m in reversed(r.messages)
         if m.get("type") in ("stream_end", "response", "error")), None)
    terminal = terminal_frame.get("type", "") if terminal_frame else "none"
    turn_id = ""
    for m in r.messages:
        if m.get("turn_id"):
            turn_id = str(m["turn_id"])
            break
    source = str(terminal_frame.get("source", "")) if terminal_frame else ""
    text = r.text or ""
    reasons: list[str] = []

    if not r.terminal or terminal == "none":
        reasons.append(f"no terminal frame (closed_by={r.closed_by}, "
                       f"elapsed={r.elapsed_s}s)")
    if terminal == "error":
        reasons.append("error frame: "
                       f"{terminal_frame.get('code')}: "
                       f"{terminal_frame.get('message')}")
    if BANNER_TEXT in text:
        reasons.append("red-banner text delivered")
    if source in REFUSAL_SOURCES:
        reasons.append(f"refusal source {source!r}")
    for canned in CANNED_NON_ANSWERS:
        if canned in text:
            reasons.append(f"canned non-answer delivered: {canned!r}")
    if q.no_action:
        seen = sorted(set(types) & ACTION_FRAMES)
        if seen:
            reasons.append(f"action frames on a no-action question: {seen}")
        if source in ("decomposed", "llm_tools"):
            reasons.append(f"tool/decomposition route on a no-action "
                           f"question: {source!r}")

    # Origin, from the frames.
    stream_end = terminal_frame if terminal == "stream_end" else None
    chunks = sum(1 for t in types if t == "stream_token")
    frames_say_model = bool(
        stream_end is not None
        and stream_end.get("used_llm") is True
        and source in MODEL_SOURCES
        and chunks > 0
        and int((stream_end.get("stats") or {}).get("tokens", 0) or 0) > 0)
    if terminal == "error":
        origin = "error"
    elif source in REFUSAL_SOURCES:
        origin = "refusal"
    elif frames_say_model:
        origin = "model"
    elif terminal in ("response", "stream_end"):
        origin = "code"
    else:
        origin = "none"

    glass_corroborated = "unreadable"
    if glass_rows is not None:
        rows = glass_rows.get(turn_id, []) if turn_id else []
        finals = [row for row in rows
                  if row.get("phase") == "delivery"
                  and row.get("event") == "final"
                  and (row.get("detail") or {}).get("iface") == "web"]
        if frames_say_model:
            ok = any(
                (row.get("detail") or {}).get("streamed") is True
                and ((row.get("detail") or {}).get("answer_linkage") or {}
                     ).get("kind") in ("model", "dispatch")
                for row in finals)
            glass_corroborated = "yes" if ok else "no"
            if not ok:
                origin = "code"
                reasons_note = ("frames say model but the target's trace has "
                                "no streamed model delivery for this turn id")
                if q.model_required:
                    reasons.append(reasons_note)
        else:
            glass_corroborated = "yes" if finals else "no"

    if q.model_required and origin != "model":
        reasons.append(f"model-required question answered by {origin!r} "
                       f"(source={source!r}, terminal={terminal})")

    return Verdict(
        key=q.key, shape=q.shape, question=q.text,
        verdict="FAIL" if reasons else "PASS", reasons=reasons,
        turn_id=turn_id, terminal=terminal, source=source, origin=origin,
        glass_corroborated=glass_corroborated, elapsed_s=r.elapsed_s,
        text_head=text.replace("\n", " ")[:160])


def _target_identity(target: str, ssh_port: int) -> tuple[dict, str]:
    """Read who the target is, from the target: hostname, release, kernel."""
    cmd = ("hostname; uname -r; cat /etc/os-release 2>/dev/null; "
           "echo '--- pkm info intergen ---'; pkm info intergen 2>&1")
    p = _ssh_read(target, ssh_port, cmd)
    if p.returncode != 0:
        refuse(f"the target identity could not be read over ssh "
               f"(rc={p.returncode}): {p.stderr.strip()}")
    raw = p.stdout
    ident: dict[str, Any] = {"hostname": "", "kernel": "", "intergen": ""}
    lines = raw.splitlines()
    if len(lines) >= 2:
        ident["hostname"], ident["kernel"] = lines[0].strip(), lines[1].strip()
    m = re.search(r"^\s*intergen\s+(\S+)\s*$", raw, re.MULTILINE)
    if not m:
        refuse("`pkm info intergen` on the target did not name an installed "
               "release; a record that cannot name what it measured is not a "
               "record of anything.\n" + raw)
    ident["intergen"] = m.group(1)
    for key in ("PRETTY_NAME", "IMAGE_VERSION", "BUILD_ID"):
        mm = re.search(rf"^{key}=\"?([^\"\n]*)\"?$", raw, re.MULTILINE)
        if mm:
            ident[key] = mm.group(1)
    return ident, raw


def _read_token(target: str, ssh_port: int) -> str:
    p = _ssh_read(target, ssh_port, f"cat ~/{TOKEN_PATH}")
    if p.returncode != 0 or not p.stdout.strip():
        refuse(f"the panel token could not be read from the target's "
               f"~/{TOKEN_PATH} (rc={p.returncode}): {p.stderr.strip()}")
    return p.stdout.strip()


def _read_glass(target: str, ssh_port: int, turn_ids: list[str],
                ) -> tuple[dict[str, list[dict]] | None, str, str]:
    """The target's trace rows for the given turn ids; None when unreadable."""
    if not turn_ids:
        return {}, "", "no turn ids were minted, nothing to read"
    pattern = " ".join(f"-e {shlex.quote(t)}" for t in turn_ids)
    cmd = f"grep -F {pattern} ~/{GLASS_PATH}"
    p = _ssh_read(target, ssh_port, cmd, timeout=120)
    if p.returncode not in (0, 1):
        return None, "", (f"glass.jsonl unreadable on the target "
                          f"(rc={p.returncode}): {p.stderr.strip()}")
    rows: dict[str, list[dict]] = {}
    for line in p.stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.setdefault(str(row.get("turn_id", "")), []).append(row)
    return rows, p.stdout, ""


async def _drive(tree_root: Path, local_port: int, token: str,
                 deadline_s: float, frames_out: Path,
                 ) -> list[tuple[Question, Any]]:
    from intergen.tests import ws_harness  # the pinned copy, put on sys.path by main()
    results: list[tuple[Question, Any]] = []
    with frames_out.open("w", encoding="utf-8") as fh:
        async with ws_harness.WSConversation(
                host="127.0.0.1", port=local_port, token=token) as conv:
            for m in conv.all_messages:
                fh.write(json.dumps({"wall": time.time(), "question": None,
                                     "frame": m}) + "\n")
            for q in QUESTIONS:
                sent_at = time.time()
                r = await conv.turn(q.text, deadline_s=deadline_s)
                for m in r.messages:
                    fh.write(json.dumps({"wall": time.time(),
                                         "question": q.key,
                                         "sent_at": sent_at,
                                         "frame": m}) + "\n")
                fh.flush()
                results.append((q, r))
                print(f"[web-battery] {q.key} {q.shape}: closed_by={r.closed_by} "
                      f"terminal={r.terminal} elapsed={r.elapsed_s}s "
                      f"events={[e[1] for e in r.events]}", flush=True)
    return results


def _seal(record_dir: Path) -> str:
    members = sorted(p for p in record_dir.rglob("*")
                     if p.is_file() and p.name != SEAL_NAME)
    lines = [f"{_sha256_file(p)}  {p.relative_to(record_dir)}" for p in members]
    seal = record_dir / SEAL_NAME
    seal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _sha256_file(seal)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the fresh-user web battery against an installed "
                    "machine over an ssh tunnel and seal the record.")
    parser.add_argument("--target", required=True,
                        help="ssh destination of the installed machine, "
                             "user@host")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--output", required=True, type=Path,
                        help="directory to write the sealed record into "
                             "(must not exist)")
    parser.add_argument("--tree-sha", required=True,
                        help="the commit the pinned copy of the tree was "
                             "exported from — recorded as DECLARED")
    parser.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE_S,
                        help="per-turn ceiling in seconds")
    parser.add_argument("--local-port", type=int, default=0,
                        help="local end of the tunnel (0 = pick a free port)")
    args = parser.parse_args(argv)

    here = Path(__file__).resolve()
    tree_root = here.parents[2]
    cwd = Path.cwd()
    for label, start in (("this battery's own file", here.parent),
                         ("the working directory", cwd)):
        co = _checkout_above(start)
        if co is not None:
            refuse(f"{label} ({start}) is inside a source checkout ({co}). "
                   f"Run the battery from a pinned copy of the tree "
                   f"(`git archive <sha> | tar -x`) and from a directory "
                   f"outside any checkout, so the record names one tree.")
    harness_path = tree_root / "intergen" / "tests" / "ws_harness.py"
    if not harness_path.is_file():
        refuse(f"the harness this battery reuses is not at {harness_path}.")
    if not re.fullmatch(r"[0-9a-f]{7,40}", args.tree_sha):
        refuse(f"--tree-sha {args.tree_sha!r} is not a commit id.")
    out: Path = args.output
    if out.exists():
        refuse(f"{out} already exists. A run record is written once; refusing "
               f"to write over one that may be somebody's evidence.")
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        refuse("aiohttp is not importable on the runner; the harness needs it.")

    started_wall = datetime.now(timezone.utc).isoformat()
    t_start = time.monotonic()
    identity, identity_raw = _target_identity(args.target, args.ssh_port)
    token = _read_token(args.target, args.ssh_port)

    local_port = args.local_port or _free_local_port()
    tunnel_cmd = _ssh_base(args.target, args.ssh_port)[:-1] + [
        "-N", "-L", f"127.0.0.1:{local_port}:127.0.0.1:{TARGET_PORT}",
        args.target]
    tunnel = subprocess.Popen(tunnel_cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, text=True)
    out.mkdir(parents=True)
    (out / "target-identity.txt").write_text(identity_raw, encoding="utf-8")
    try:
        sys.path.insert(0, str(tree_root))
        from intergen.tests import ws_harness
        reachable = False
        for _ in range(60):
            if tunnel.poll() is not None:
                break
            if ws_harness.daemon_reachable("127.0.0.1", local_port):
                reachable = True
                break
            time.sleep(0.5)
        if not reachable:
            err = ""
            if tunnel.poll() is not None:
                err = (tunnel.stderr.read() if tunnel.stderr else "")
            (out / "tunnel-error.txt").write_text(err, encoding="utf-8")
            print(f"[web-battery] the panel did not answer through the tunnel "
                  f"(127.0.0.1:{local_port} -> {args.target}:{TARGET_PORT}); "
                  f"tunnel rc={tunnel.poll()}", file=sys.stderr)
            summary = {"refused_after_record_opened":
                       "panel unreachable through the tunnel",
                       "tunnel_stderr": err}
            (out / "summary.json").write_text(json.dumps(summary, indent=2))
            _seal(out)
            return EXIT_FAIL

        results = asyncio.run(_drive(tree_root, local_port, token,
                                     args.deadline, out / "frames.jsonl"))
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=10)
        except subprocess.TimeoutExpired:
            tunnel.kill()

    turn_ids: list[str] = []
    for _q, r in results:
        for m in r.messages:
            if m.get("turn_id"):
                turn_ids.append(str(m["turn_id"]))
                break
    glass_rows, glass_raw, glass_err = _read_glass(
        args.target, args.ssh_port, turn_ids)
    (out / "glass-excerpt.jsonl").write_text(glass_raw, encoding="utf-8")

    verdicts = [_grade(q, r, glass_rows) for q, r in results]
    with (out / "verdicts.tsv").open("w", encoding="utf-8") as fh:
        fh.write("key\tshape\tverdict\torigin\tsource\tterminal\t"
                 "glass_corroborated\telapsed_s\tturn_id\treasons\t"
                 "text_head\tquestion\n")
        for v in verdicts:
            fh.write("\t".join([
                v.key, v.shape, v.verdict, v.origin, v.source, v.terminal,
                v.glass_corroborated, f"{v.elapsed_s:.2f}", v.turn_id,
                " | ".join(v.reasons), v.text_head, v.question]) + "\n")

    passed = sum(1 for v in verdicts if v.verdict == "PASS")
    failed = len(verdicts) - passed
    origin_evidence = (
        "frames (the server's own stream_end.used_llm/source/stats and the "
        "streamed tokens) AND the target's glass.jsonl delivery/final row for "
        "the same turn id (streamed, answer_linkage kind model/dispatch); "
        "neither is cryptographic"
        if glass_rows is not None else
        "frames ONLY (the server's own stream_end.used_llm/source/stats and "
        "the streamed tokens) — the target's glass.jsonl was not readable, so "
        "model origin rests on the server's self-declaration: " + glass_err)
    summary = {
        "battery": "intergen/tests/web_battery.py",
        "started_utc": started_wall,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "wall_s": round(time.monotonic() - t_start, 1),
        "runner": {"hostname": socket.gethostname(),
                   "python": sys.version.split()[0],
                   "cwd": str(cwd),
                   "argv": sys.argv},
        "declared": {"tree_sha": args.tree_sha},
        "measured": {
            "battery_sha256": _sha256_file(here),
            "harness_sha256": _sha256_file(harness_path),
            "harness_path": str(harness_path),
            "tree_root": str(tree_root),
            "target": identity,
        },
        "target_ssh": args.target,
        "tunnel": f"127.0.0.1:{local_port} -> 127.0.0.1:{TARGET_PORT}",
        "deadline_s": args.deadline,
        "questions": len(verdicts),
        "passed": passed,
        "failed": failed,
        "origin_evidence": origin_evidence,
        "glass_readable": glass_rows is not None,
        "per_question": [v.__dict__ for v in verdicts],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2),
                                      encoding="utf-8")
    seal_sha = _seal(out)
    print(f"[web-battery] {passed} PASS / {failed} FAIL of {len(verdicts)}; "
          f"target {identity.get('hostname')} intergen "
          f"{identity.get('intergen')}; record {out}; {SEAL_NAME} sha256 "
          f"{seal_sha}", flush=True)
    for v in verdicts:
        print(f"[web-battery]   {v.key} {v.verdict} {v.shape} origin={v.origin} "
              f"source={v.source or '-'} "
              + ("; ".join(v.reasons) if v.reasons else ""), flush=True)
    return EXIT_OK if failed == 0 else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
