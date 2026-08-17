# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-4.1 — the live cross-posture run driver (the daemon-facing entry point).

Every other harness module is either pure (loader / grader / report /
comparator) or unit-testable against the mock transport (runner). Nothing yet
*drives the battery against a live daemon over the bus and produces the
cross-posture artifacts a tier run needs*. This is that entry point:

1. **Load** the graded battery (``load_scenarios`` over the seeds tree).
2. **Per posture** (§2.4: a scenario runs once per declared posture against a
   daemon serving that tier), run only the scenarios that DECLARE that posture,
   over the real ``ClientTransport(mode="dbus")`` against the live daemon, with a
   trace lookup that joins each turn's reply (and, when supplied, the always-on
   ``glass.jsonl`` and a ``--observe`` ``decisions.jsonl`` capture) into the
   TraceView the grounding assertions read (§5.2). A grounding signal the capture
   does not carry FAILS CLOSED — the harness observes the gap, never masks it.
3. **Write** per-posture ``results.json`` + ``summary.txt`` via
   :func:`report.write_run`, co-located under one run-id dir (§6.1).
4. **Diff** the two postures head-to-head via :func:`comparator.compare`, scoped
   to the scenarios that ran under BOTH postures (a scenario that declares only
   one posture is posture-exclusive, not a regression — reported separately so a
   scope difference never reads as a coverage loss).

The driver OBSERVES: it drives the daemon only through the transport interface
and never reaches inside it. The two postures may be captured against the same
live daemon (the default — one transport reused) or against a re-served tier
daemon (``--reconnect-between-postures``, which rebuilds the transport so the
operator can restart the box into the other tier between legs); the driver is
agnostic to which, because the tier a connection serves is the box's config, not
the driver's assumption.

Small-model nondeterminism (§5.4): ``--repeat N`` runs each posture N times and
reports a per-scenario PASS-rate distribution so a flaky invariant is visible as
(e.g.) 7/10 rather than a coin-flip green.

Usage (on the box that serves the tier under test):
    python3 -m intergen.tests.scenario.live_run \
        --out ./scenario-live-runs --run-id live-9b-01 \
        [--posture 9B-native --posture 2B-locked] \
        [--glass ~/.local/state/intergen/glass.jsonl] \
        [--decisions ./decisions.jsonl] [--repeat 3] \
        [--reconnect-between-postures]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from intergen.tests.scenario import comparator, report
from intergen.tests.scenario.loader import load_scenarios
from intergen.tests.scenario.runner import TraceLookup, run_scenarios
from intergen.tests.scenario.schema import POSTURES, Scenario
from intergen.tests.scenario.trace import TraceView
from intergen.tests.scenario.transport import ScenarioTransport, TurnResult

# The seeds tree that ships with the harness — the default graded battery.
_SEEDS_DIR = Path(__file__).resolve().parent / "seeds"

# Ordered so a two-posture run diffs the locked FLOOR (2B) against the native
# tier (9B) — floor first so the comparator reads 2B->9B as the candidate axis.
_DEFAULT_POSTURES = ["2B-locked", "9B-native"]


def scenarios_for_posture(scenarios: list[Scenario], posture: str) -> list[Scenario]:
    """The scenarios that DECLARE ``posture`` (§2.4 — a scenario runs once per
    declared posture, never under a posture it did not target)."""
    return [s for s in scenarios if posture in s.postures]


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSONL trace file into a list of row dicts, skipping blank lines.

    A malformed line is a loud error, not a silent skip — a half-read trace would
    silently drop grounding signal and turn a real fail into a masked pass."""
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{i + 1}: invalid JSONL row: {e}") from e
    return rows


def _spans_by_trace(decisions_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group ``decisions.jsonl`` spans by the turn they attest.

    A span ties to a turn by ``trace_id``/``turn_id`` (top-level or under
    ``attributes``); the reply's ``trace_id`` is the join key (§4.2). A span
    whose id cannot be resolved is dropped from the join (its outcome flags then
    simply do not attach, and the dependent grounding assertion fails closed —
    the honest state, never a guessed attribution)."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for span in decisions_rows:
        attrs = span.get("attributes", {}) or {}
        tid = (span.get("trace_id") or span.get("turn_id")
               or attrs.get("trace_id") or attrs.get("turn_id"))
        if not tid:
            continue
        grouped.setdefault(str(tid), []).append(span)
    return grouped


def build_trace_lookup(glass_rows: list[dict[str, Any]] | None = None,
                       decisions_rows: list[dict[str, Any]] | None = None) -> TraceLookup:
    """A per-turn trace resolver for a live run, layering every available source.

    Base: the reply itself (``from_turn_result``) — always carries dispatched
    tool names + arguments + the route source + the delivered text, which is what
    the posture-conditional routing assertions read. Enrichment, when supplied:
    a ``decisions.jsonl`` capture provides the aggregate dispatch-outcome flags
    (the grounding assertions' outcome signal), and the always-on ``glass.jsonl``
    (§5.2 primary source) supplies the decomposition ``sub_queries`` and
    corroborates the route source. A signal no source carries stays unresolved,
    so the grader fails closed on it — verify, don't mask.
    """
    spans = _spans_by_trace(decisions_rows) if decisions_rows else {}

    def lookup(tr: TurnResult) -> TraceView | None:
        turn_spans = spans.get(tr.trace_id) if tr.trace_id else None
        view = TraceView.from_turn_result(tr, spans=turn_spans)
        if glass_rows and tr.trace_id:
            g = TraceView.from_glass_rows(glass_rows, trace_id=tr.trace_id)
            if g.sub_queries:
                view.sub_queries = g.sub_queries
            if not view.route_source and g.route_source:
                view.route_source = g.route_source
        return view

    return lookup


def eval_consent_state(transport: ScenarioTransport) -> dict[str, Any]:
    """The daemon's reported eval-consent posture, or an empty dict.

    A transport that cannot report status at all (or a daemon too old to carry
    the key) yields ``{}``, which :func:`require_eval_consent_armed` treats as
    NOT armed — the fail-closed reading. An unknown posture is never assumed to
    be the safe one.
    """
    try:
        status = transport.status() or {}
    except Exception:  # noqa: BLE001 — an unreadable status is simply unknown
        return {}
    state = status.get("eval_consent")
    return state if isinstance(state, dict) else {}


def require_eval_consent_armed(transport: ScenarioTransport, posture: str) -> dict[str, Any]:
    """Fail closed unless the daemon under test has the responder ARMED.

    The runner deliberately does NOT arm a daemon it does not own: arming lives
    on the daemon process's own command line, so a run driving an already-serving
    daemon over the bus asserts the precondition instead of reaching across to
    change another process's consent posture. Verified before every posture leg
    (and again after a reconnect), because a daemon restart between legs returns
    it to production behavior — an unnoticed disarm would put the run right back
    on the modal it exists to avoid.
    """
    state = eval_consent_state(transport)
    if state.get("armed") is True:
        return state
    raise RuntimeError(
        f"eval-consent responder is NOT armed on the daemon serving {posture!r} "
        f"(reported: {state or 'no eval_consent status'}). An unattended run "
        "would stall on a consent modal. Restart the daemon for this run with "
        "the eval-consent argument, e.g.\n"
        "    python3 -m intergen.dbus_daemon --eval-consent-deny\n"
        "then re-run. Refusing to grade an unarmed run."
    )


def consent_observations_from_glass(
    glass_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Extract this run's recorded consent denials from glass rows.

    The responder emits one ``consent``/``eval_denied`` row per denied gate,
    carrying the active turn id — which is the same join key a reply carries, so
    a denial attributes back to the scenario turn that provoked it. Arming and
    refusal rows are carried too: the audit sample should see the window's edges,
    not just its contents.
    """
    out: list[dict[str, Any]] = []
    for row in glass_rows or []:
        if row.get("phase") != "consent":
            continue
        event = str(row.get("event") or "")
        if not event.startswith("eval_"):
            continue
        detail = row.get("detail") or {}
        out.append({
            "event": event,
            "turn_id": (detail.get("turn_id") or row.get("turn_id")
                        or row.get("trace_id") or ""),
            "gate": detail.get("gate", ""),
            "verdict": detail.get("verdict", ""),
            "action": detail.get("action", ""),
            "detail": detail,
        })
    return out


def _scope_to_shared(a: dict, b: dict) -> tuple[dict, dict, list[str], list[str]]:
    """Restrict two results docs to the scenarios present in BOTH, recomputing
    per-axis metrics over the scoped set so the comparator's trend is over the
    same denominator. Returns the two scoped docs plus the a-only and b-only
    scenario ids (posture-exclusive — reported, never counted as a regression)."""
    ids_a = {s["id"] for s in a.get("scenarios", [])}
    ids_b = {s["id"] for s in b.get("scenarios", [])}
    shared = ids_a & ids_b

    def scope(res: dict) -> dict:
        scen = [s for s in res.get("scenarios", []) if s["id"] in shared]
        grades = [s["grade"] for s in scen]
        return {
            "run_id": res.get("run_id", ""),
            "scenarios": scen,
            "axis_metrics": report.axis_metrics(scen),
            "counts": {
                "scenarios": len(scen),
                "passed": grades.count("PASS"),
                "mixed": grades.count("MIXED"),
                "failed": len(grades) - grades.count("PASS") - grades.count("MIXED"),
            },
        }

    return scope(a), scope(b), sorted(ids_a - shared), sorted(ids_b - shared)


def cross_posture_diff(results_a: dict, results_b: dict) -> dict:
    """Head-to-head diff of two posture runs, scoped to the shared scenarios.

    ``comparator.compare`` treats a dropped scenario as a regression; across
    postures a scenario that declares only one tier is posture-exclusive, not a
    loss, so the diff runs over the shared set and the exclusives are surfaced
    separately. The full per-posture ``results.json`` remain on disk untouched —
    nothing is hidden, only the head-to-head is scoped to comparable cells."""
    scoped_a, scoped_b, a_only, b_only = _scope_to_shared(results_a, results_b)
    diff = comparator.compare(scoped_a, scoped_b)
    diff["posture_exclusive"] = {
        "baseline_only": a_only,
        "candidate_only": b_only,
        "note": ("ids present under only one posture (declares a single tier) — "
                 "posture scope, NOT a coverage regression"),
    }
    return diff


def run_live(
    scenarios: list[Scenario],
    transport_factory: Callable[[], ScenarioTransport],
    postures: list[str],
    out_dir: str | Path,
    run_id: str,
    *,
    trace_lookup: TraceLookup | None = None,
    repeat: int = 1,
    reconnect_between_postures: bool = False,
    compare: bool = True,
    require_eval_consent: bool = False,
) -> dict:
    """Drive the battery per posture and (optionally) diff the two.

    ``transport_factory`` builds a fresh transport — injected so the self-tests
    drive the whole pipeline against the mock transport with no daemon/bus/model.
    Artifacts land under ``out_dir/run_id/<posture>/`` (or ``.../rep-NN/`` when
    ``repeat>1``). Returns a manifest describing every artifact written plus the
    cross-posture diff when two postures ran.
    """
    if repeat < 1:
        raise ValueError(f"repeat must be >= 1, got {repeat}")
    for p in postures:
        if p not in POSTURES:
            raise ValueError(f"unknown posture {p!r} (known: {sorted(POSTURES)})")

    base = Path(out_dir) / run_id
    manifest: dict[str, Any] = {"run_id": run_id, "postures": {}, "out_dir": str(base)}
    # results.json of the FIRST repeat per posture — the diff's inputs.
    first_results: dict[str, dict] = {}

    shared_transport: ScenarioTransport | None = None
    if not reconnect_between_postures:
        shared_transport = transport_factory()

    try:
        for posture in postures:
            subset = scenarios_for_posture(scenarios, posture)
            posture_dir = base / posture
            passrate: dict[str, list[str]] = {s.id: [] for s in subset}
            rep_dirs: list[str] = []

            transport = transport_factory() if reconnect_between_postures else shared_transport
            assert transport is not None
            try:
                transport.await_ready()
                if require_eval_consent:
                    # Checked AFTER await_ready (a not-yet-ready daemon has no
                    # meaningful posture to report) and inside the per-posture
                    # loop, so a reconnect between legs is re-verified rather
                    # than assumed to have carried the arming forward.
                    consent_state = require_eval_consent_armed(transport, posture)
                    print(f"[eval-consent] {posture}: ARMED "
                          f"(policy={consent_state.get('policy')})", flush=True)
                for rep in range(1, repeat + 1):
                    out = posture_dir / f"rep-{rep:02d}" if repeat > 1 else posture_dir
                    rep_run_id = f"{run_id}-{posture}" + (f"-rep{rep:02d}" if repeat > 1 else "")
                    runs = run_scenarios(subset, transport, trace_lookup=trace_lookup,
                                         posture=posture)
                    results = report.write_run(runs, subset, out, rep_run_id)
                    rep_dirs.append(str(out))
                    for r in runs:
                        passrate[r.scenario_id].append(r.grade.grade)
                    if rep == 1:
                        first_results[posture] = results
            finally:
                if reconnect_between_postures:
                    transport.close()

            manifest["postures"][posture] = {
                "scenarios_run": [s.id for s in subset],
                "count": len(subset),
                "rep_dirs": rep_dirs,
                # End-of-leg consent roll-up straight from the daemon: how many
                # gates fired and were denied while this posture ran. Empty when
                # the responder was not required/armed.
                "eval_consent": eval_consent_state(transport) or None,
                "pass_rate": {
                    sid: {"pass": g.count("PASS"), "of": len(g), "grades": g}
                    for sid, g in passrate.items()
                },
            }
    finally:
        if shared_transport is not None:
            shared_transport.close()

    if compare and len(postures) == 2 and all(p in first_results for p in postures):
        a, b = postures[0], postures[1]
        diff = cross_posture_diff(first_results[a], first_results[b])
        (base / "cross-posture-diff.json").write_text(
            json.dumps(diff, indent=2), encoding="utf-8")
        (base / "cross-posture-diff.txt").write_text(
            comparator.format_report(diff, f"{run_id}:{a}", f"{run_id}:{b}")
            + _exclusive_note(diff), encoding="utf-8")
        manifest["cross_posture_diff"] = {
            "regression": diff["regression"],
            "baseline": a, "candidate": b,
            "path": str(base / "cross-posture-diff.json"),
        }

    (base / "manifest.json").parent.mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _exclusive_note(diff: dict) -> str:
    pe = diff.get("posture_exclusive", {})
    lines = ["", "Posture-exclusive scenarios (scope, not regression):"]
    lines.append(f"  baseline-only:  {pe.get('baseline_only') or '(none)'}")
    lines.append(f"  candidate-only: {pe.get('candidate_only') or '(none)'}")
    return "\n".join(lines) + "\n"


def _dbus_transport_factory() -> ScenarioTransport:
    # Imported lazily: pulls in gi/the daemon, which the self-tests must not need.
    from intergen.tests.scenario.transport import ClientTransport
    return ClientTransport(mode="dbus")


def _direct_transport_factory() -> ScenarioTransport:
    from intergen.tests.scenario.transport import ClientTransport
    return ClientTransport(mode="direct")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Drive the scenario battery per posture against a live "
                    "daemon and diff the tiers (WP-4.1 live cross-posture run).")
    ap.add_argument("--seeds", default=str(_SEEDS_DIR),
                    help="scenario seeds file or directory (default: shipped battery)")
    ap.add_argument("--posture", action="append", dest="postures", default=None,
                    help="posture to run (repeatable; default: 2B-locked then 9B-native)")
    ap.add_argument("--mode", choices=("dbus", "direct"), default="dbus",
                    help="transport mode (default: dbus — the live persistent daemon)")
    ap.add_argument("--out", default="./scenario-live-runs",
                    help="run-artifact root (default: ./scenario-live-runs)")
    ap.add_argument("--run-id", required=True, help="stable id naming this run's dir")
    ap.add_argument("--glass", default=None,
                    help="glass.jsonl to join for sub_queries + route corroboration")
    ap.add_argument("--decisions", default=None,
                    help="decisions.jsonl (--observe capture) for dispatch-outcome flags")
    ap.add_argument("--repeat", type=int, default=1,
                    help="runs per posture; reports a per-scenario PASS-rate (default: 1)")
    ap.add_argument("--reconnect-between-postures", action="store_true",
                    help="rebuild the transport per posture (restart the box into "
                         "the other tier between legs); default reuses one connection")
    ap.add_argument("--no-compare", action="store_true",
                    help="skip the cross-posture diff (per-posture artifacts only)")
    ap.add_argument("--require-eval-consent", action="store_true",
                    help="require the daemon to have the eval-consent deny-and-record "
                         "responder ARMED, and refuse to grade the run otherwise. Use "
                         "for unattended baselines: consent gates then answer with an "
                         "immediate recorded deny instead of stalling on a modal. Arm "
                         "the daemon by launching it with --eval-consent-deny.")
    args = ap.parse_args(argv)

    scenarios = load_scenarios(args.seeds)
    postures = args.postures or _DEFAULT_POSTURES

    glass_rows = _load_jsonl(args.glass) if args.glass else None
    decisions_rows = _load_jsonl(args.decisions) if args.decisions else None
    trace_lookup = build_trace_lookup(glass_rows, decisions_rows)

    factory = _dbus_transport_factory if args.mode == "dbus" else _direct_transport_factory

    manifest = run_live(
        scenarios, factory, postures, args.out, args.run_id,
        trace_lookup=trace_lookup, repeat=args.repeat,
        reconnect_between_postures=args.reconnect_between_postures,
        compare=not args.no_compare,
        require_eval_consent=args.require_eval_consent,
    )

    # Per-scenario consent observations. Re-read the glass file AFTER the run:
    # the daemon appends to it live, so the rows this run produced only exist by
    # now. Written as its own artifact so grading and the audit sample can
    # consume the denials without parsing the whole glass stream.
    if args.glass:
        try:
            post_rows = _load_jsonl(args.glass)
        except (OSError, ValueError) as e:
            print(f"[eval-consent] could not re-read glass for observations: {e}",
                  flush=True)
        else:
            observations = consent_observations_from_glass(post_rows)
            obs_path = Path(args.out) / args.run_id / "consent-observations.json"
            obs_path.parent.mkdir(parents=True, exist_ok=True)
            obs_path.write_text(json.dumps(observations, indent=2), encoding="utf-8")
            manifest["consent_observations"] = {
                "count": len(observations),
                "path": str(obs_path),
            }
            print(f"[eval-consent] recorded {len(observations)} consent "
                  f"observation(s) -> {obs_path}", flush=True)

    print(json.dumps(manifest, indent=2), flush=True)
    diff = manifest.get("cross_posture_diff")
    # Nonzero exit on a cross-posture regression makes this a CI gate too.
    return 1 if (diff and diff["regression"]) else 0


if __name__ == "__main__":
    sys.exit(main())
