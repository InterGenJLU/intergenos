"""InterGen tool-result cache (perceived-latency design, artifact 2).

A small, correct read-through cache for read-only (AUTO) tool calls, with TTLs
and event-based invalidation. The point is NOT to speed up slow system calls
(the latency harness showed most are <120ms) but to serve REPEATED identical
reads instantly — skipping both the system call and, since the cached ToolResult
carries its model_summary, the LLM re-synthesis.

Security-first: a stale cache that confidently reports a wrong fact is a
fabrication-class failure. So the cache is load-bearingly conservative:

  * Per-user keyed (uid in the key) — a privileged read cached for one user is
    never served to another.
  * Short TTLs (read-only system state goes stale fast).
  * Event-based invalidation — a SUCCESSFUL state-changing call flushes the
    related read entries (a `manage_packages install` flushes the package
    reads; a `manage_services restart` flushes the service reads). Over-
    invalidation only costs a re-fetch; under-invalidation would serve a lie,
    so invalidation is coarse-by-tool (always safe).
  * Only AUTO read-only actions are cacheable (the policy table below); nothing
    privileged or state-changing is ever cached.

See docs/architecture/intergen-perceived-latency-design.md.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Any, Callable

logger = logging.getLogger(__name__)

# (tool, action) -> TTL seconds for a cacheable read-only call. action is None
# for tools without an action subcommand (matched as a fallback). A call not
# listed here is NEVER cached. Values informed by the A12 latency matrix.
_CACHE_POLICY: dict[tuple[str, str | None], int] = {
    ("manage_packages", "list"): 30,
    ("manage_packages", "search"): 60,
    ("manage_packages", "info"): 60,
    ("manage_packages", "verify"): 30,
    ("manage_services", "status"): 10,
    ("manage_services", "is-active"): 10,
    ("manage_services", "is-enabled"): 60,
    ("manage_services", "is-failed"): 10,
    ("manage_services", "list-units"): 10,
    ("manage_services", "list-unit-files"): 60,
    ("read_file", None): 15,
    ("web_search", None): 300,
}

# A SUCCESSFUL action here flushes every cached read entry (same uid) for the
# listed tools. Keyed (tool, action); action None = any action of the tool.
_INVALIDATES: dict[tuple[str, str | None], list[str]] = {
    ("manage_packages", "install"): ["manage_packages"],
    ("manage_packages", "remove"): ["manage_packages"],
    ("manage_packages", "uninstall"): ["manage_packages"],
    ("manage_packages", "update"): ["manage_packages"],
    ("manage_packages", "upgrade"): ["manage_packages"],
    ("manage_services", "start"): ["manage_services"],
    ("manage_services", "stop"): ["manage_services"],
    ("manage_services", "restart"): ["manage_services"],
    ("manage_services", "reload"): ["manage_services"],
    ("manage_services", "enable"): ["manage_services"],
    ("manage_services", "disable"): ["manage_services"],
    ("manage_services", "mask"): ["manage_services"],
    ("manage_services", "unmask"): ["manage_services"],
    ("write_file", None): ["read_file"],
    ("run_command", None): ["read_file", "manage_packages", "manage_services"],
}

_MAX_ENTRIES = 128  # bound the cache; oldest evicted on overflow


def _action(args: dict[str, Any] | None) -> str | None:
    return (args or {}).get("action")


def _ttl_for(tool: str, args: dict[str, Any] | None) -> int | None:
    a = _action(args)
    if (tool, a) in _CACHE_POLICY:
        return _CACHE_POLICY[(tool, a)]
    if (tool, None) in _CACHE_POLICY:
        return _CACHE_POLICY[(tool, None)]
    return None


def _invalidates_for(tool: str, args: dict[str, Any] | None) -> list[str] | None:
    a = _action(args)
    return _INVALIDATES.get((tool, a)) or _INVALIDATES.get((tool, None))


def _normalized_args(args: dict[str, Any] | None) -> str:
    # Drop provenance-only fields so the same logical read caches regardless of
    # how the model labelled it; stable JSON so arg order doesn't matter.
    relevant = {k: v for k, v in (args or {}).items()
                if k != "source_of_request"}
    return json.dumps(relevant, sort_keys=True, default=str)


class ToolCache:
    """Per-user, TTL'd, invalidation-aware read-through cache for AUTO reads."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        # key -> (expiry_monotonic, ToolResult); OrderedDict for LRU eviction.
        self._store: "OrderedDict[tuple, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self._clock = clock
        self.hits = 0
        self.misses = 0

    @staticmethod
    def is_cacheable(tool: str, args: dict[str, Any] | None) -> bool:
        return _ttl_for(tool, args) is not None

    def _key(self, uid: int, tool: str, args: dict[str, Any] | None) -> tuple:
        return (uid, tool, _normalized_args(args))

    def get(self, tool: str, args: dict[str, Any] | None, uid: int):
        """Return a fresh cached ToolResult, or None on miss/expiry/not-cacheable."""
        if _ttl_for(tool, args) is None:
            return None
        k = self._key(uid, tool, args)
        now = self._clock()
        with self._lock:
            entry = self._store.get(k)
            if entry is None:
                self.misses += 1
                return None
            if entry[0] <= now:  # expired
                del self._store[k]
                self.misses += 1
                return None
            self._store.move_to_end(k)
            self.hits += 1
            # D-5 (trust-boundary defense-in-depth): hand back a SNAPSHOT, never
            # the cached object itself. The dispatch chokepoint scans + spotlights
            # the served result by MUTATING it in place (content -> withheld
            # notice / wrapped, model_summary -> None / wrapped). Returning a copy
            # keeps those mutations off the cached entry, so the cache always
            # holds the RAW pre-scan result and every hit is scanned fresh from
            # clean bytes — the cache is purely a syscall-avoidance layer, fully
            # decoupled from the trust boundary. ToolResult's fields are scalars,
            # so a shallow replace() is a complete, independent snapshot.
            return replace(entry[1])

    def put(self, tool: str, args: dict[str, Any] | None, uid: int, result: Any) -> None:
        """Cache a SUCCESSFUL, cacheable read result. No-op otherwise."""
        ttl = _ttl_for(tool, args)
        if ttl is None or not getattr(result, "success", False):
            return
        k = self._key(uid, tool, args)
        with self._lock:
            # D-5: store a SNAPSHOT, never the caller's reference. put() is
            # called BEFORE the dispatch chokepoint scans/spotlights the result,
            # and that scan mutates the original in place — copying here keeps
            # the cached entry as the RAW pre-scan result (see get()).
            self._store[k] = (self._clock() + ttl, replace(result))
            self._store.move_to_end(k)
            while len(self._store) > _MAX_ENTRIES:
                self._store.popitem(last=False)  # evict oldest

    def invalidate_for_write(self, tool: str, args: dict[str, Any] | None,
                             uid: int) -> int:
        """Flush this uid's cached reads invalidated by a successful write.
        Returns the number of entries dropped. No-op for non-write tools."""
        targets = _invalidates_for(tool, args)
        if not targets:
            return 0
        wanted = set(targets)
        with self._lock:
            doomed = [k for k in self._store
                      if k[0] == uid and k[1] in wanted]
            for k in doomed:
                del self._store[k]
        if doomed:
            logger.debug("Tool cache: %s %s flushed %d read entries",
                         tool, _action(args) or "", len(doomed))
        return len(doomed)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
