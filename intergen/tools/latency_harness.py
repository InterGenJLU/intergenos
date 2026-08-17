"""InterGen tool-latency timing harness (perceived-latency design, artifact 1).

Exercises every SAFE, read-only (AUTO-tier) tool call cold + warm, samples the
wall-clock of `tool.execute()`, and emits the latency matrix consumed by the
band classifier and the caching subsystem. See
docs/architecture/intergen-perceived-latency-design.md.

SAFETY: this harness NEVER runs a state-changing or side-effectful action.
manage_packages install/remove/update, manage_services start/stop/restart/
enable/disable, write_file, run_command, take_screenshot and open_application
are deliberately excluded — they are CONFIRM-tier (they show a permission card
first) and several mutate the system. Only read-only AUTO calls are timed.

Run ON A REAL TARGET (pkm / systemctl only exist there), e.g. the A12 slow
floor:

    python3 -m intergen.tools.latency_harness            # -> stdout JSON
    python3 -m intergen.tools.latency_harness --out matrix.json --warm 7

The matrix is a PREDICTION (which band to pre-arm); a wall-clock timer in the
request path is the runtime guarantee for mis-classified calls.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Callable

# Default band thresholds (ms). Tunable per hardware tier; the harness records
# them in the matrix so the loader and the bands stay in sync.
DEFAULT_THRESHOLDS_MS = {
    "instant": 1500,   # < this -> no filler, just stream the answer
    "slow": 7000,      # > this -> hop-2 progress nudges + cache
    "timer_backstop": 5000,  # runtime timer fires hop-2 even on a "fast" call
}

# The safe, read-only call set. Each entry: (tool_name, label, arguments,
# cacheable, cache_ttl_s, network). `label` disambiguates calls of the same
# tool. Only AUTO-tier read-only actions appear here — see the SAFETY note.
SAFE_CALLS: list[dict[str, Any]] = [
    {"tool": "manage_packages", "label": "list",   "args": {"action": "list"},
     "cacheable": True,  "ttl": 30, "network": False},
    {"tool": "manage_packages", "label": "search", "args": {"action": "search", "query": "lib"},
     "cacheable": True,  "ttl": 60, "network": False},
    {"tool": "manage_packages", "label": "info",   "args": {"action": "info", "package": "bash"},
     "cacheable": True,  "ttl": 60, "network": False},
    {"tool": "manage_packages", "label": "verify", "args": {"action": "verify", "package": "bash"},
     "cacheable": False, "ttl": 0,  "network": False},
    {"tool": "manage_services", "label": "status",     "args": {"action": "status", "service": "dbus"},
     "cacheable": True,  "ttl": 10, "network": False},
    {"tool": "manage_services", "label": "is-active",  "args": {"action": "is-active", "service": "dbus"},
     "cacheable": True,  "ttl": 10, "network": False},
    {"tool": "manage_services", "label": "is-enabled", "args": {"action": "is-enabled", "service": "dbus"},
     "cacheable": True,  "ttl": 60, "network": False},
    {"tool": "manage_services", "label": "is-failed",  "args": {"action": "is-failed", "service": "dbus"},
     "cacheable": True,  "ttl": 10, "network": False},
    {"tool": "manage_services", "label": "list-units", "args": {"action": "list-units"},
     "cacheable": True,  "ttl": 10, "network": False},
    {"tool": "read_file",    "label": "os-release", "args": {"path": "/etc/os-release"},
     "cacheable": True,  "ttl": 30, "network": False},
    {"tool": "analyze_file", "label": "os-release", "args": {"path": "/etc/os-release"},
     "cacheable": True,  "ttl": 30, "network": False},
    {"tool": "web_search",   "label": "query", "args": {"query": "InterGenOS"},
     "cacheable": True,  "ttl": 300, "network": True},
]


def _percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile in ms (samples already in ms). pct in [0,100]."""
    if not samples:
        return 0.0
    s = sorted(samples)
    k = max(0, min(len(s) - 1, round(pct / 100.0 * len(s) + 0.5) - 1))
    return s[k]


def _band(warm_p50_ms: float, th: dict[str, int]) -> str:
    if warm_p50_ms < th["instant"]:
        return "instant"
    if warm_p50_ms > th["slow"]:
        return "slow"
    return "medium"


def _instantiate_tools() -> dict[str, Any]:
    """Instantiate the safe tools directly (no ToolRegistry/Sentinel init).

    Tools take no constructor args. A tool that fails to import/instantiate is
    skipped with a note rather than aborting the whole run.
    """
    from intergen.tools.manage_packages import ManagePackagesTool
    from intergen.tools.manage_services import ManageServicesTool
    from intergen.tools.read_file import ReadFileTool
    from intergen.tools.analyze_file import AnalyzeFileTool
    from intergen.tools.web_search import WebSearchTool

    candidates = [
        ManagePackagesTool, ManageServicesTool, ReadFileTool,
        AnalyzeFileTool, WebSearchTool,
    ]
    out: dict[str, Any] = {}
    for cls in candidates:
        try:
            t = cls()
            out[t.name] = t
        except Exception as e:  # noqa: BLE001 — record, don't abort
            print(f"[harness] could not instantiate {cls.__name__}: {e}",
                  file=sys.stderr)
    return out


def _time_call(tool: Any, args: dict[str, Any]) -> tuple[float, bool, int]:
    """Run one execute() and return (elapsed_ms, success, content_len)."""
    t0 = time.perf_counter()
    try:
        res = tool.execute(dict(args))
        elapsed = (time.perf_counter() - t0) * 1000.0
        return elapsed, bool(getattr(res, "success", False)), len(getattr(res, "content", "") or "")
    except Exception as e:  # noqa: BLE001 — a throwing tool is still a data point
        elapsed = (time.perf_counter() - t0) * 1000.0
        return elapsed, False, 0


def run(warm: int, thresholds: dict[str, int],
        progress: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    tools = _instantiate_tools()
    calls: list[dict[str, Any]] = []

    for spec in SAFE_CALLS:
        name = spec["tool"]
        tool = tools.get(name)
        ident = f"{name}:{spec['label']}"
        if tool is None:
            progress(f"skip {ident} (tool unavailable)")
            continue
        progress(f"timing {ident} ...")

        # Cold sample (first touch — what the cache later removes).
        cold_ms, cold_ok, content_len = _time_call(tool, spec["args"])

        # Warm samples.
        warm_ms: list[float] = []
        warm_ok = cold_ok
        for _ in range(max(1, warm)):
            ms, ok, clen = _time_call(tool, spec["args"])
            warm_ms.append(ms)
            warm_ok = warm_ok and ok
            content_len = max(content_len, clen)

        p50 = _percentile(warm_ms, 50)
        p99 = _percentile(warm_ms, 99)
        calls.append({
            "tool": name,
            "action": spec["label"],
            "args": spec["args"],
            "cold_ms": round(cold_ms, 1),
            "warm_p50_ms": round(p50, 1),
            "warm_p99_ms": round(p99, 1),
            "warm_min_ms": round(min(warm_ms), 1),
            "warm_max_ms": round(max(warm_ms), 1),
            "content_len": content_len,
            "success": warm_ok,
            "network": spec["network"],
            "cacheable": spec["cacheable"],
            "cache_ttl_s": spec["ttl"],
            "band": _band(p50, thresholds),
        })

    import socket
    return {
        "version": 1,
        "captured_on": socket.gethostname(),
        "warm_samples": warm,
        "thresholds_ms": thresholds,
        "calls": calls,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="InterGen tool-latency harness")
    ap.add_argument("--out", help="write matrix JSON here (default: stdout)")
    ap.add_argument("--warm", type=int, default=7, help="warm samples per call (default 7)")
    args = ap.parse_args(argv)

    matrix = run(args.warm, dict(DEFAULT_THRESHOLDS_MS),
                 progress=lambda m: print(f"[harness] {m}", file=sys.stderr))
    blob = json.dumps(matrix, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(blob + "\n")
        print(f"[harness] wrote {args.out}", file=sys.stderr)
    else:
        print(blob)

    # Human-readable band summary to stderr.
    for c in matrix["calls"]:
        print(f"[harness] {c['tool']:18s} {c['action']:12s} "
              f"p50={c['warm_p50_ms']:8.1f}ms  cold={c['cold_ms']:8.1f}ms  "
              f"len={c['content_len']:6d}  -> {c['band']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
