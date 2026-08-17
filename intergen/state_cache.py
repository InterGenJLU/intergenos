# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen system state cache — proactive polling for instant responses.

Runs background threads that periodically execute system commands and
cache the results. When the user asks "how much disk space?", the
answer comes from cache (0ms) instead of live execution (50-200ms+).

Cache freshness:
  - Static tier (5 min): hostname, kernel, OS, CPU, GPU, packages
  - Dynamic tier (30s):  disk, memory, uptime, load, services
  - Never cached:        processes, connections, logs, file contents

The user gets data that's at most 30-60 seconds old. For system
monitoring queries, this is perfectly acceptable — they're asking
a question, not watching a real-time dashboard.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_STATIC_INTERVAL = 300    # 5 minutes
_DYNAMIC_INTERVAL = 30    # 30 seconds
# Floor between on-demand dynamic re-polls (refresh_dynamic). A burst of health
# asks must cost ONE scan, not one per ask.
_REFRESH_MIN_INTERVAL = 5.0


@dataclass
class CachedValue:
    value: str
    timestamp: float
    command: str
    stale_after: float


# Commands to cache, grouped by refresh interval.
#
# Each command is an argv PIPELINE: a list of argv STAGES executed shell=False,
# each stage's stdout piped to the next (a single-stage pipeline is a plain argv
# command). Dropping shell=True here (M8-1 leg 0) kills the classifier-vs-shell
# parse-divergence class at the executor: these strings are code-owned, static,
# and select by KEY only (no query/user interpolation), but the executor must
# not speak shell for ANY caller — so even a hypothetical future interpolation
# cannot pass a shell construct to a shell that no longer runs. The five
# pipelines that were shell pipes (`lscpu | head -20`, `lspci | grep -i vga`,
# `systemctl … | head -30`, `systemctl --failed … | head -8`,
# `ps … | head -5`) become explicit argv stages — same captured OUTPUT (see
# _run_pipeline for the SIGPIPE-vs-run-to-completion note).
_STATIC_COMMANDS: dict[str, list[list[str]]] = {
    "hostname": [["hostname"]],
    "kernel": [["uname", "-r"]],
    "os_release": [["cat", "/etc/os-release"]],
    "cpu_info": [["lscpu"], ["head", "-20"]],
    "gpu_info": [["lspci"], ["grep", "-i", "vga"]],
    "block_devices": [["lsblk"]],
    "usb_devices": [["lsusb"]],
    # network_interfaces ("ip -brief addr show") DROPPED: `ip` opens an
    # AF_NETLINK socket to talk to the kernel, which the daemon's hardened unit
    # denies via RestrictAddressFamilies (F-038). Each poll cycle the `ip` child
    # took SIGSYS and coredumped — the cascade seen at install #29. We do NOT
    # re-add AF_NETLINK (ratified F-038). A netlink-free interface diagnostic
    # (e.g. reading /sys/class/net) is a possible future grounding source.
}

_DYNAMIC_COMMANDS: dict[str, list[list[str]]] = {
    "disk_usage": [["df", "-h"]],
    "memory_usage": [["free", "-h"]],
    "uptime": [["uptime"]],
    "load_average": [["cat", "/proc/loadavg"]],
    "service_list": [
        ["systemctl", "list-units", "--type=service", "--state=running",
         "--no-pager", "--no-legend"],
        ["head", "-30"],
    ],
    # System Map facts (Goal-2 grounded retrieval).
    # All read-only literals selecting by KEY only (no query interpolation),
    # under the same load-guard + 5s timeout + in-memory invariant as above.
    # These are MULTI-LINE by nature, so lookup_for_query() never serves them
    # via the 0ms single-line template path — they flow through grounded LLM
    # synthesis (get_system_map_data + the router's system_map route), where the
    # model reads true data instead of fabricating it.
    # Kept SMALL on purpose: the grounded data is prefilled into the synthesis
    # prompt, and the A12 prefills at ~16 tok/s — a large blob (e.g. journalctl
    # -n20) wedges the single-slot model. -n5 / head -5 keeps the system-map
    # turn near the ~10s conversational floor instead of minutes.
    "failed_services": [
        ["systemctl", "--failed", "--no-legend", "--no-pager"],
        ["head", "-8"],
    ],
    "recent_errors": [["journalctl", "-p", "err", "-n", "5", "--no-pager", "-o", "cat"]],
    "top_processes": [
        ["ps", "-eo", "pcpu,pmem,comm", "--sort=-pcpu", "--no-headers"],
        ["head", "-5"],
    ],
}


# A live-state HEALTH/FAILURE ask — the class whose answer must be fresh or
# carry its age. Identity/inventory asks (hostname, hardware model) are NOT here:
# those are ratified as cache-served, and a five-minute-old hostname is still the
# hostname.
_HEALTH_QUERY_MARKERS = (
    "fail", "wrong", "broken", "issue", "problem", "error", "crash",
    "everything ok", "everything okay", "everything alright", "health",
    "status", "down", "degraded", "slow",
)


def _humanize_age(age: float) -> str:
    """A short, honest age phrase for a data block ('2 minutes ago')."""
    if age < 90:
        return f"{int(age)} seconds ago"
    if age < 5400:
        return f"{int(round(age / 60))} minutes ago"
    return f"{age / 3600:.1f} hours ago"


def _pipeline_display(pipeline: list[list[str]]) -> str:
    """Human-readable ' | '-joined form of an argv pipeline (metadata/logging)."""
    return " | ".join(" ".join(stage) for stage in pipeline)


# Query keyword → cache key. Module-level so both lookup_for_query() (which
# returns the cached VALUE) and matches_state_keyword() (which reports only that
# a query TARGETS system state, cold cache or not) read one source of truth.
_QUERY_TO_CACHE = {
    "hostname": ["hostname", "host name", "machine name", "box called"],
    "kernel": ["kernel", "uname"],
    "os_release": ["os version", "operating system", "os release", "what os"],
    "cpu_info": ["cpu", "processor", "lscpu"],
    "gpu_info": ["gpu", "graphics", "vga", "video card"],
    "disk_usage": ["disk", "storage", "space", "df", "full"],
    "memory_usage": ["memory", "ram", "free"],
    "uptime": ["uptime", "how long"],
    "load_average": ["load", "load average"],
    "block_devices": ["block device", "lsblk", "drives", "partitions"],
    "usb_devices": ["usb"],
    # network_interfaces mapping removed with its poll (see _STATIC_COMMANDS):
    # the `ip` netlink poll was dropped under F-038, so there is no grounded
    # data to serve these queries; mapping them to a missing key would only
    # return None. Restore alongside a netlink-free interface source.
    "service_list": ["services", "running services", "systemctl"],
}


class StateCache:
    """Background system state cache with tiered refresh intervals."""

    def __init__(self):
        self._cache: dict[str, CachedValue] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._static_thread: threading.Thread | None = None
        self._dynamic_thread: threading.Thread | None = None
        # On-demand refresh rate limiter (refresh_dynamic). Separate from
        # _lock, which guards the cache dict itself and must never be held
        # across a subprocess poll.
        self._refresh_lock = threading.Lock()
        self._last_refresh = float("-inf")
        # Load-guard pause bookkeeping (see _poll_all). None = polling running.
        self._poll_paused_since: float | None = None
        self._poll_paused_load: tuple[float, int] | None = None

    def start(self) -> None:
        """Start background polling threads."""
        self._stop_event.clear()

        # Initial population (blocking — fills cache before daemon reports ready)
        self._poll_all(_STATIC_COMMANDS, _STATIC_INTERVAL)
        self._poll_all(_DYNAMIC_COMMANDS, _DYNAMIC_INTERVAL)
        logger.info("State cache populated: %d entries", len(self._cache))

        # Background threads for ongoing refresh
        self._static_thread = threading.Thread(
            target=self._poll_loop,
            args=(_STATIC_COMMANDS, _STATIC_INTERVAL),
            daemon=True, name="intergen-cache-static",
        )
        self._dynamic_thread = threading.Thread(
            target=self._poll_loop,
            args=(_DYNAMIC_COMMANDS, _DYNAMIC_INTERVAL),
            daemon=True, name="intergen-cache-dynamic",
        )
        self._static_thread.start()
        self._dynamic_thread.start()
        logger.info("State cache threads started (static=%ds, dynamic=%ds)",
                     _STATIC_INTERVAL, _DYNAMIC_INTERVAL)

    def stop(self) -> None:
        """Stop background polling."""
        self._stop_event.set()
        logger.info("State cache stopped")

    def get(self, key: str) -> str | None:
        """Get a cached value by key. Returns None if not cached."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            return entry.value

    def get_if_fresh(self, key: str) -> str | None:
        """Get cached value only if within its freshness window."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            age = time.monotonic() - entry.timestamp
            if age > entry.stale_after * 2:
                return None
            return entry.value

    def get_age(self, key: str) -> float | None:
        """Get the age in seconds of a cached value."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            return time.monotonic() - entry.timestamp

    def poll_pause_notice(self) -> str | None:
        """A line stating that background polling is PAUSED, or None when it is
        running. Surfaced into a health answer so a reading that stopped
        advancing says why."""
        with self._lock:
            since = self._poll_paused_since
            load = self._poll_paused_load
        if since is None:
            return None
        paused_for = time.monotonic() - since
        detail = (f" (load {load[0]:.1f} across {load[1]} cores)" if load else "")
        return ("NOTE — background state polling is PAUSED because the system is "
                f"under sustained load{detail}; it has been paused for "
                f"{_humanize_age(paused_for)[:-4].strip()}. The readings below "
                "have not advanced since then — say so rather than presenting "
                "them as a live check.")

    def get_dated(self, key: str) -> tuple[str | None, float | None, bool]:
        """(value, age_seconds, is_fresh) for one key — the freshness-aware read.

        `get()` is age-blind, so a caller that renders a health verdict from it
        states a PAST condition in the present tense with nothing to reveal that
        it did. This returns the age alongside the value so the caller can either
        refresh or say how old the data is. `is_fresh` uses the same window as
        get_if_fresh (stale_after * 2), so the two never disagree.
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None, None, False
            age = time.monotonic() - entry.timestamp
            return entry.value, age, age <= entry.stale_after * 2

    def refresh_dynamic(self, *, min_interval: float = _REFRESH_MIN_INTERVAL
                        ) -> bool:
        """Re-poll the dynamic tier NOW, at most once per `min_interval`.

        The freshness contract for a live-state health ask is "fresh data, or
        say how old it is". This is the fresh-data half. Rate-limited and
        serialised so a burst of health asks produces ONE scan, not one per ask
        (no dispatch storm); a caller that loses the race proceeds with whatever
        the winner just wrote. Returns True if this call performed the poll.
        """
        now = time.monotonic()
        with self._refresh_lock:
            if now - self._last_refresh < min_interval:
                return False
            self._last_refresh = now
        self._poll_all(_DYNAMIC_COMMANDS, _DYNAMIC_INTERVAL)
        return True

    def get_all(self) -> dict[str, str]:
        """Get all cached values as a dict."""
        with self._lock:
            return {k: v.value for k, v in self._cache.items()}

    def lookup_for_query(self, query: str) -> str | None:
        """Try to answer a query from cache based on keywords.

        Returns cached output if the query matches a cached key,
        or None if no cache hit (caller should execute live).
        """
        lower = query.lower()

        for cache_key, keywords in _QUERY_TO_CACHE.items():
            if any(kw in lower for kw in keywords):
                value = self.get(cache_key)
                if value is not None:
                    return value

        return None

    @staticmethod
    def matches_state_keyword(query: str) -> bool:
        """True if the query TARGETS cached single-value system state — by
        keyword, independent of whether a value is currently cached.

        lookup_for_query() answers a query but returns None on a cold cache, so
        it cannot tell "not a system query" apart from "system query, value not
        polled yet." The compound handoff needs the former distinction: a clause
        like "what's my hostname" must count as fast-path-answerable even when the
        hostname poll has not run, or a mixed compound would be misread as pure
        knowledge and its system clause handed to an unground-able model turn."""
        lower = query.lower()
        return any(
            kw in lower
            for keywords in _QUERY_TO_CACHE.values()
            for kw in keywords
        )

    def _dated_block(self, key: str, label: str, empty_text: str) -> str:
        """One grounded block, carrying its age whenever the data is stale.

        A fresh value reads in the present tense, exactly as before. A STALE
        value is still shown — dropping it would answer a health question with
        less information than we hold — but it is labelled with how old it is,
        so the synthesis model cannot state a past condition as the current one
        and the user can see what they are being told. A key that was never
        populated says so rather than being rendered as a clean bill of health.
        """
        value, age, fresh = self.get_dated(key)
        if value is None:
            return f"{label}\n(no reading available yet — do not treat this as a clean result)"
        body = value if value else empty_text
        if fresh:
            return f"{label}\n{body}"
        return (f"{label} [STALE — this reading is from {_humanize_age(age)}; "
                f"say so rather than presenting it as the current state]\n{body}")

    def get_system_map_data(self, query: str) -> str | None:
        """Assemble grounded system-state data for a 'system map' query.

        Selects the relevant multi-line facts (failed services, recent errors,
        top processes — plus disk/memory/load for a broad health question) by
        keyword and returns them as labelled blocks of TRUE current data, or
        None if nothing relevant is cached. The caller feeds this to a
        constrained LLM synthesis prompt; the model phrases it for the user and
        is instructed never to add facts not present here (no fabrication).

        Goal-2 grounded retrieval. Read-only: query only selects
        which cached blocks to return — never interpolated into a command.
        """
        # Cold-cache guard (review finding): _poll_all only stores non-empty output,
        # so a MISSING failed_services/service_list is indistinguishable from a
        # genuinely-healthy one. Rendering "(none — healthy)" off a cold or
        # load-guard-skipped cache would assert health we cannot prove
        # (Commandment 4 — fail visibly, not fabricate). load_average is an
        # always-non-empty dynamic fact: if it is fresh the dynamic poll ran, so
        # an empty failed_services is TRUE; if it is absent/stale, return None and
        # let the caller's no-fabricate fallback say "I can't tell yet".
        if self.get_if_fresh("load_average") is None:
            return None

        lower = query.lower()

        # FRESHNESS CONTRACT for a live-state health/failure ask (ratified
        # 2026-07-25): a stale verdict presented as CURRENT is the defect. So a
        # health ask first tries to make the data fresh (one rate-limited scan
        # for the whole burst), and anything still stale afterwards is rendered
        # WITH ITS AGE by _dated_block below rather than in the present tense.
        # The load_average gate above stays: it is the cold-cache guard, and it
        # answers a different question (did the dynamic poll ever run) than the
        # per-key ages do.
        if any(k in lower for k in _HEALTH_QUERY_MARKERS):
            self.refresh_dynamic()

        want_failed = any(k in lower for k in (
            "fail", "wrong", "broken", "issue", "problem", "everything ok",
            "everything okay", "everything alright", "health", "status", "down",
        ))
        want_errors = any(k in lower for k in (
            "error", "crash", "log", "wrong", "fail", "broken",
        ))
        # "running" is intentionally NOT here — it routes to want_services
        # below (service membership), which is leaner than the top-procs dump.
        want_procs = any(k in lower for k in (
            "slow", "process", "cpu", "busy", "lag", "hog",
            "using", "consum", "memory", "load",
        ))
        # Service-status questions ("is sshd running", "what services are
        # running", "what's running") → feed the cached running-units list. The
        # service NAME is matched against the CACHED list by the synthesis model
        # — it is NEVER interpolated into a shell command (HG guardrail 1).
        want_services = any(k in lower for k in (
            "service", "services", "daemon", "daemons",
            "what's running", "whats running", "what is running",
        )) or (lower.split()[:1] in (["is"], ["are"], ["does"])
               and any(w in lower for w in (
                   "running", "active", "enabled", "started", "up")))

        # Broad health question ("is everything ok / how's the system / anything
        # wrong / system status") → a COMPACT health picture (failed units +
        # short error tail + load), NOT the full multi-fact dump — that blob's
        # prefill wedges the A12's single-slot model. Specific facets ("why
        # slow", "top processes") still pull procs/memory on demand.
        want_broad = any(k in lower for k in (
            "everything ok", "everything okay", "everything alright",
            "anything wrong", "system health", "system status",
            "how's the system", "hows the system", "how is the system",
            "how is my system", "is the system ok", "is my system ok",
        ))

        # No specific facet matched at all → treat as a broad health check.
        if not (want_failed or want_errors or want_procs or want_services
                or want_broad):
            want_broad = True

        blocks: list[str] = []
        if want_failed or want_broad:
            blocks.append(self._dated_block(
                "failed_services", "Failed systemd units (systemctl --failed):",
                "(none — all units are healthy)"))
        if want_errors or want_broad:
            blocks.append(self._dated_block(
                "recent_errors", "Most recent system errors:",
                "(none recorded)"))
        if want_services:
            # Phrase honestly (guardrail 2): the list can be bounded, so a
            # service that is absent is "not among the running services I can
            # see", not a flat "not running".
            blocks.append(self._dated_block(
                "service_list",
                "Currently running services (the running-units list I can "
                "see; if a service is not in this list, say it is not among "
                "the running services rather than flatly 'not running'):",
                "(none visible)"))
        if want_procs:
            v = self.get("top_processes")
            if v:
                blocks.append("Top processes by CPU (%CPU %MEM COMMAND):\n" + v)
            mem = self.get("memory_usage")
            if mem:
                blocks.append("Memory (free -h):\n" + mem)
        if want_procs or want_broad:
            load = self.get("load_average")
            if load:
                blocks.append("Load average (/proc/loadavg):\n" + load)

        if not blocks:
            return None
        # A paused polling cycle rides at the TOP of the grounded data, so the
        # synthesis model states it rather than answering as if it had just
        # looked.
        notice = self.poll_pause_notice()
        if notice:
            blocks.insert(0, notice)
        return "\n\n".join(blocks)

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._cache)

    # ── Internal ──

    @staticmethod
    def _run_pipeline(stages: list[list[str]], timeout: float) -> subprocess.CompletedProcess:
        """Execute an argv PIPELINE shell=False, stage by stage.

        Each stage is a full argv list; a stage's stdout is fed as the next
        stage's stdin. shell=False on every exec — the executor never speaks
        shell, so no command string (code-owned here, but the invariant is
        caller-agnostic) can carry a shell construct to a shell. Returns the
        final stage's CompletedProcess (its .stdout is the pipeline output).

        Behaviour vs the prior `a | b` shell pipe: identical OUTPUT. The only
        internal difference is stage a runs to completion before stage b trims
        it, instead of a receiving SIGPIPE when b (e.g. `head`) closes early —
        for the bounded read-only commands polled here the captured stdout is
        the same bytes either way. First stage reads from DEVNULL (these
        commands ignore stdin); a per-stage timeout bounds each exec.
        """
        prev_stdout = None
        result: subprocess.CompletedProcess | None = None
        for i, argv in enumerate(stages):
            if i == 0:
                result = subprocess.run(
                    argv, stdin=subprocess.DEVNULL, capture_output=True,
                    text=True, timeout=timeout,
                )
            else:
                result = subprocess.run(
                    argv, input=prev_stdout, capture_output=True,
                    text=True, timeout=timeout,
                )
            prev_stdout = result.stdout
        return result

    def _poll_all(self, commands: dict[str, list[list[str]]],
                  stale_after: float) -> None:
        """Execute all command pipelines and update cache.

        CRITICAL: pollers must be invisible to system performance.
        - Each pipeline stage has a 5-second timeout
        - If system load is high (>80% of cores), skip this cycle
        - 100ms sleep between commands to prevent burst

        Commands run as argv PIPELINES via _run_pipeline (shell=False) — no
        shell, and no nice/ionice wrapping. The wrapping was cargo-culted
        boilerplate from a heavy-I/O pattern; for the sub-millisecond commands
        InterGen polls (hostname, uptime, etc.) it added quoting complexity, hid
        what was actually executing, and triggered SIGSYS under the F-038
        hardened seccomp filter (which correctly denies setpriority +
        ioprio_set per @resources). The load-guard above is the right
        invisibility primitive; wrapping every microsecond command in two
        priority-management binaries is not.
        """
        # Skip if system is under heavy load
        try:
            load_1min = os.getloadavg()[0]
            cpu_count = os.cpu_count() or 1
            if load_1min > cpu_count * 0.8:
                logger.debug("System load high (%.1f/%d), skipping cache poll",
                             load_1min, cpu_count)
                # RECORD THE PAUSE. Skipping is correct — the poller must stay
                # invisible under load — but a health answer served while the
                # cycle is paused was previously indistinguishable from one
                # served while it was running. The readings simply stopped
                # advancing and nothing said so. This lets the answer SAY that
                # polling is paused, instead of silently ageing out into a
                # decline (fails-safe stays; it just speaks).
                with self._lock:
                    self._poll_paused_since = self._poll_paused_since or time.monotonic()
                    self._poll_paused_load = (load_1min, cpu_count)
                return
            with self._lock:
                self._poll_paused_since = None
                self._poll_paused_load = None
        except (OSError, AttributeError):
            pass

        for key, pipeline in commands.items():
            if self._stop_event.is_set():
                break
            try:
                result = self._run_pipeline(pipeline, timeout=5)
                output = result.stdout.rstrip()
                # A SUCCESSFUL poll writes its result even when that result is
                # EMPTY. `if output:` skipped the write on empty stdout, which
                # made a cleared condition unrepresentable: `systemctl --failed`
                # prints nothing when nothing is failing, so a unit that failed
                # and was then FIXED left its failure text in the cache forever —
                # every later health answer reported the resolved failure as
                # current. Empty-on-success is a real observation (the true
                # negative); only a command that did not run — non-zero exit,
                # timeout, exception — leaves the previous value in place, and
                # that value then ages out through the freshness checks.
                if result.returncode == 0:
                    with self._lock:
                        self._cache[key] = CachedValue(
                            value=output,
                            timestamp=time.monotonic(),
                            command=_pipeline_display(pipeline),
                            stale_after=stale_after,
                        )
                # Brief pause between commands — prevent burst
                time.sleep(0.1)
            except subprocess.TimeoutExpired:
                logger.debug("Cache command timed out: %s", _pipeline_display(pipeline))
            except Exception as e:
                logger.debug("Cache command failed (%s): %s",
                             _pipeline_display(pipeline), e)

    def _poll_loop(self, commands: dict[str, list[list[str]]],
                   interval: float) -> None:
        """Background polling loop."""
        while not self._stop_event.is_set():
            self._stop_event.wait(interval)
            if self._stop_event.is_set():
                break
            self._poll_all(commands, interval)
