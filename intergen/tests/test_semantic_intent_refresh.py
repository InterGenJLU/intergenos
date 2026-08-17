# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for the embedding-intent recovery path (semantic matcher).

Covers the residual gap found on metal during the GDM-greeter cold-boot proof:
the embedding SERVER self-heals via the watchdog, but embedding INTENTS skipped
at startup (embedder down) used to be dropped and never re-registered, leaving
Layer-2 routing half-degraded for the whole session. register_intent now retains
a skipped intent as PENDING and refresh_pending_intents() (called by the embed
watchdog's restart-success) promotes them once the embedder recovers.
"""
import threading

import pytest

from intergen.semantic import SemanticMatcher


class FakeEmbedder:
    """Toggleable embedder: returns None while .up is False (backend down),
    else a deterministic 2-D one-hot keyed on the word 'time' so an intent
    example and a matching query align (cosine 1.0) and a non-match is orthogonal.
    """
    def __init__(self, up: bool = False):
        self.up = up

    def __call__(self, texts):
        if not self.up:
            return None
        out = []
        for t in texts:
            out.append([1.0, 0.0] if "time" in t.lower() else [0.0, 1.0])
        return out


def test_skipped_intent_is_pending_not_dropped():
    emb = FakeEmbedder(up=False)
    m = SemanticMatcher(embedder=emb)
    m.register_intent("get_time", ["what time is it", "current time"],
                      tool_name="clock")
    # Down at registration: retained as pending, NOT active, NOT dropped.
    assert "get_time" in m._pending_embedding_intents
    assert "get_time" not in m._embedding_intents
    assert m.get_intent_count() == 0
    # And it does not match while pending.
    assert m.match("please tell me the time").intent_id is None


def test_refresh_recovers_pending_and_routes():
    emb = FakeEmbedder(up=False)
    m = SemanticMatcher(embedder=emb)
    m.register_intent("get_time", ["what time is it", "current time"],
                      tool_name="clock")
    assert m.get_intent_count() == 0

    emb.up = True  # embedder recovers (watchdog restart-success)
    recovered = m.refresh_pending_intents()
    assert recovered == 1
    assert m.get_intent_count() == 1
    assert not m._pending_embedding_intents

    # The whole point: an embedding-routed query now actually routes.
    result = m.match("show me the time")
    assert result.intent_id == "get_time"
    assert result.tool_name == "clock"


def test_refresh_is_retry_safe_when_embedder_still_down():
    emb = FakeEmbedder(up=False)
    m = SemanticMatcher(embedder=emb)
    m.register_intent("get_time", ["what time is it"], tool_name="clock")

    # Refresh while still down: nothing recovered, intent STAYS pending
    # (skip-on-failure must not just move down a level).
    assert m.refresh_pending_intents() == 0
    assert "get_time" in m._pending_embedding_intents

    emb.up = True
    assert m.refresh_pending_intents() == 1
    assert "get_time" not in m._pending_embedding_intents
    # Idempotent once recovered.
    assert m.refresh_pending_intents() == 0


def test_refresh_leaves_keyword_intents_untouched():
    emb = FakeEmbedder(up=False)
    m = SemanticMatcher(embedder=emb)
    m.register_keyword_pattern("greet", [r"\bhello\b"], tool_name="greet")
    m.register_intent("get_time", ["what time is it"], tool_name="clock")
    assert len(m._keyword_intents) == 1

    emb.up = True
    m.refresh_pending_intents()
    # Keyword corpus must not be duplicated or re-touched.
    assert len(m._keyword_intents) == 1
    assert m.get_intent_count() == 2  # 1 keyword + 1 recovered embedding


def test_intent_registered_live_is_not_pending():
    emb = FakeEmbedder(up=True)  # backend up at registration
    m = SemanticMatcher(embedder=emb)
    m.register_intent("get_time", ["what time is it"], tool_name="clock")
    assert "get_time" in m._embedding_intents
    assert not m._pending_embedding_intents
    assert m.get_intent_count() == 1


def test_concurrent_match_and_refresh_no_dict_resize_crash():
    """match() must snapshot the intent set under the lock so a concurrent
    refresh promoting intents on another thread cannot mutate the dict mid
    iteration (RuntimeError: dictionary changed size during iteration)."""
    emb = FakeEmbedder(up=True)
    m = SemanticMatcher(embedder=emb)
    errors: list[Exception] = []
    stop = threading.Event()

    def hammer_match():
        try:
            while not stop.is_set():
                m.match("the time please")
        except Exception as e:  # any concurrency crash lands here
            errors.append(e)

    t = threading.Thread(target=hammer_match)
    t.start()
    try:
        for i in range(300):
            with m._lock:
                m._pending_embedding_intents[f"x{i}"] = (["time x"], 0.90, None)
            m.refresh_pending_intents()  # promotes into _embedding_intents live
    finally:
        stop.set()
        t.join()

    assert not errors, f"concurrency crash: {errors[:3]}"
    assert m.get_intent_count() >= 1


def test_throwing_embedder_degrades_like_down_no_crash():
    """An embedder that RAISES (transient connection error, or a malformed
    non-shape response from an up server) must degrade to None exactly like a
    cleanly-down embedder: registration takes the pending path without raising,
    a live match returns no-match without crashing, and a later working embedder
    plus refresh recovers the intent."""
    state = {"raise": True}

    def embedder(texts):
        if state["raise"]:
            raise ConnectionError("embed server connection reset")
        return [[1.0, 0.0] if "time" in t.lower() else [0.0, 1.0] for t in texts]

    m = SemanticMatcher(embedder=embedder)
    # Registration must NOT raise; the intent is retained pending.
    m.register_intent("get_time", ["what time is it"], tool_name="clock")
    assert "get_time" in m._pending_embedding_intents
    assert "get_time" not in m._embedding_intents
    # A live match against a raising embedder must NOT crash.
    assert m.match("show me the time").intent_id is None
    # Embedder recovers; refresh promotes the pending intent and it routes.
    state["raise"] = False
    assert m.refresh_pending_intents() == 1
    assert m.match("show me the time").intent_id == "get_time"
