"""GATE 5 — maximum startup embedding load with an immediate user turn (§9 line 4).

WHAT COMPOSITION PROPERTY THIS CATCHES. At start-up the assistant indexes its
documentation and submits every passage to the embedding server in ONE request. The
embedding server is deliberately configured with a single decode slot, and the client
gives that request thirty seconds. On this machine the corpus is about 2100 passages,
the request always expires, and the retrieval layer falls back to keyword matching for
the entire life of the daemon. Meanwhile the abandoned batch is still being worked on
by the server, so the first user turn — which needs the same single slot — queues
behind it at exactly the moment the web interface starts accepting turns.

None of the three ingredients is visible to a source-tree test: corpus size comes from
the installed documentation, the single slot comes from the shipped server arguments,
and the deadline is a default in a third module.

WHAT THIS GATE READS. This machine's own service journal, one record per daemon start.
A machine with no recorded start is a FAILURE here, not a skip — an unmeasured
start-up must not read as a healthy one.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime

import pytest

UNIT = "intergen.service"

INDEXED = re.compile(r"wiki-retrieval: indexed (\d+) passage\(s\) from (\d+) verified")
FALLBACK = "embedder returned nothing; keyword fallback only"
EMBED_FAILED = "embed() request failed"
READY = "Web server started"
TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
PID = re.compile(r"intergen\[(\d+)\]")


def _journal() -> list[str]:
    proc = subprocess.run(
        ["journalctl", "--user", "-u", UNIT, "--no-pager", "-o", "short-iso"],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        pytest.fail(
            "Could not read this machine's service journal, so nothing about its "
            f"start-up behaviour is known.\nexit={proc.returncode}\n"
            f"stderr:\n{proc.stderr}")
    return proc.stdout.splitlines()


def _when(line: str):
    m = TS.match(line)
    return datetime.fromisoformat(m.group(1)) if m else None


def _pid(line: str):
    m = PID.search(line)
    return m.group(1) if m else None


@pytest.fixture(scope="module")
def startups() -> list[dict]:
    """One record per daemon PROCESS found in the journal.

    Grouped by the daemon's process id, not by a fixed number of lines after the
    indexing message. An earlier draft used a 400-line window; the messages that matter
    can be five hundred lines apart, so three of the four starts on this machine were
    reported as healthy when the journal says they were not. A process id is exact.
    """
    lines = _journal()
    by_pid: dict[str, list[str]] = {}
    order: list[str] = []
    for line in lines:
        pid = _pid(line)
        if pid is None:
            continue
        if pid not in by_pid:
            by_pid[pid] = []
            order.append(pid)
        by_pid[pid].append(line)

    starts = []
    for pid in order:
        block = by_pid[pid]
        indexed = next((ln for ln in block if INDEXED.search(ln)), None)
        if indexed is None:
            continue
        m = INDEXED.search(indexed)
        starts.append({
            "pid": pid,
            "at": _when(indexed),
            "passages": int(m.group(1)),
            "pages": int(m.group(2)),
            "fell_back": any(FALLBACK in ln for ln in block),
            "embed_failures": [ln for ln in block if EMBED_FAILED in ln],
            "ready_line": next((ln for ln in block if READY in ln), None),
        })
    if not starts:
        pytest.fail(
            "This machine's journal records no daemon start that indexed the "
            "documentation, so this gate measured nothing. An unmeasured start-up is "
            "not a healthy start-up; extend the journal retention or start the daemon "
            "and re-run.")
    return starts


def test_the_documentation_index_is_populated_after_every_start(startups):
    degraded = [s for s in startups if s["fell_back"]]
    report = ["", f"DAEMON STARTS RECORDED ON THIS MACHINE: {len(startups)}", ""]
    for s in startups:
        report.append(
            f"  pid {s['pid']:>6}  {s['at']}  {s['passages']} passages / "
            f"{s['pages']} pages  "
            f"{'KEYWORD FALLBACK' if s['fell_back'] else 'index populated'}  "
            f"({len(s['embed_failures'])} embedding timeout(s) in that process)")
    report.append("")
    report.append(
        "Every start marked KEYWORD FALLBACK ran its whole life without semantic "
        "retrieval over the documentation. The submission is one request carrying "
        "every passage, to a server configured with one decode slot, under a thirty "
        "second client deadline.")
    assert not degraded, "\n".join(report)


def test_the_interface_does_not_accept_turns_while_an_embedding_backlog_drains(startups):
    """Declaring ready while the single embedding slot is still occupied is the race.

    A turn typed in that window needs the same slot the abandoned start-up batch is
    still holding, so the user's first turn is the one most likely to exceed the
    browser's deadline.
    """
    racing = []
    for s in startups:
        if s["ready_line"] is None:
            continue
        ready_at = _when(s["ready_line"])
        for failure in s["embed_failures"]:
            failed_at = _when(failure)
            if ready_at and failed_at and failed_at > ready_at:
                racing.append((s["pid"], ready_at, failed_at, failure.strip()))
                break

    assert not racing, (
        "\nThe web interface began accepting turns while the single embedding slot was "
        "still occupied by the abandoned start-up batch:\n" +
        "\n".join(f"  pid {p}: ready at {r}, a further embedding call expired at {f}\n"
                  f"    {line}" for p, r, f, line in racing) +
        "\nA turn typed in that window competes with work the daemon has already "
        "abandoned. It is the concrete producer of the browser's turn deadline expiring."
    )


def test_the_startup_submission_is_not_one_request_for_the_whole_corpus(
        installed_intergen_dir):
    """The shape that makes the deadline unreachable, read from the shipped code."""
    text = (installed_intergen_dir / "wiki_retrieval.py").read_text(encoding="utf-8")
    whole_corpus = "self._embedder([c.text for c in self._chunks])" in text
    assert not whole_corpus, (
        "\nThe start-up index submits every passage in a single embedding request:\n"
        "  wiki_retrieval.py: self._embedder([c.text for c in self._chunks])\n"
        "The server has one decode slot and the client deadline is thirty seconds. "
        "Corpus size is a property of the installed documentation, so this request "
        "grows with the product while the deadline does not."
    )
