# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen memory manager — user-controlled persistent fact storage.

Ported from a prior internal AI assistant project (Phases 1 + 6 only).
Deliberately simple: explicit pattern extraction, text search,
full user transparency, soft deletes. No FAISS, no batch LLM
extraction, no proactive surfacing.

The user controls what InterGen remembers:
  "Remember that my backup drive is /dev/sdb1"
  "What do you know about me?"
  "Forget about my backup drive"

Design principle: the user owns the memory — transparent, inspectable,
deletable, with no silent profiling.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
import uuid
import weakref
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from intergen import glass
from intergen.private_state import private_dir, private_touch

logger = logging.getLogger(__name__)


def fact_cache_text(key: str, value: str) -> str:
    """The one string a stored fact is known by wherever it is embedded.

    The router builds the ranker's candidate list from this, and the relevance
    index keys its fact-vector cache on it. Both used to spell it out
    separately, which meant a forget could only clear the cache by spelling it a
    third time and hoping all three agreed. One definition makes them agree by
    construction.
    """
    return f"{key}: {value}"


# ── Live relevance indexes ───────────────────────────────────────────────────
#
# A forget removes a fact from the store. The fact's verbatim text is also held
# in RAM by every SessionTurnIndex that has ranked it, because fact vectors are
# cached to avoid re-embedding a fact on every turn. Those indexes belong to
# conversations, not to the store, and neither side holds a reference to the
# other — so before this registry existed there was no path from a forget to
# the copies, and they outlived the forget by the life of the daemon.
#
# The set is weak so an index that IS collected leaves nothing behind. In
# practice an index is not collected while its background worker runs (the
# worker's target is a bound method, which holds it), and that is exactly the
# case this exists for: a conversation the user ended, whose cache they can no
# longer reach and can no longer clear.
#
# Process-wide, deliberately. A daemon serves one user from one store across
# every conversation, so "every live index" is the correct reach. A test process
# holding two stores can have a forget in one drop a cached vector an index of
# the other would have reused; the cost of that is one re-embed, never a wrong
# answer, and erring wide is the right direction for removing something the user
# asked to have removed.
_LIVE_INDEXES: "weakref.WeakSet[SessionTurnIndex]" = weakref.WeakSet()
_LIVE_INDEXES_LOCK = threading.Lock()


def _register_live_index(index: "SessionTurnIndex") -> None:
    """Record an index so a forget can reach the fact vectors it caches."""
    with _LIVE_INDEXES_LOCK:
        _LIVE_INDEXES.add(index)


def _live_indexes() -> list["SessionTurnIndex"]:
    """A snapshot, so an index built or collected mid-sweep cannot disturb it."""
    with _LIVE_INDEXES_LOCK:
        return list(_LIVE_INDEXES)


def forget_fact_vectors(texts) -> int:
    """Drop the cached vectors of named facts from every live index.

    Returns the number of cache entries actually removed, across all indexes —
    the number the turn record reports, so a reader can see that the in-memory
    copies went with the stored rows.
    """
    texts = list(texts)
    if not texts:
        return 0
    return sum(index.forget_facts(texts) for index in _live_indexes())


def forget_all_fact_vectors() -> int:
    """Empty every live index's fact-vector cache.

    For "clear all my memories": no stored fact remains, so no cached fact
    vector is legitimate.
    """
    return sum(index.forget_all_facts() for index in _live_indexes())


def _default_db_path() -> Path:
    """Per-user memory DB under XDG_DATA_HOME (fallback ~/.local/share).

    Memory is per-user state. intergen.service is a USER service hardened
    with ProtectSystem=strict, so the old /var/lib/intergen/data default is
    unwritable at runtime (root-owned + remounted read-only) — and a shared
    /var memory.db would leak one user's stored facts to every other user.
    XDG per-user storage is both the writable AND the security-correct
    location (per-user isolation). Mirrors session_manager / model_manager.
    """
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "intergen" / "memory.db"


@dataclass
class Fact:
    fact_id: str
    key: str
    value: str
    category: str = "general"
    source: str = "explicit"
    confidence: float = 0.95
    created_at: float = 0.0
    updated_at: float = 0.0
    deleted: bool = False


# ── Pattern extraction ──

_REMEMBER_PATTERNS = [
    # "remember that X is Y"
    (r"(?:remember|save|store|note)\s+that\s+(.+?)\s+(?:is|are|was)\s+(.+)",
     lambda m: (m.group(1).strip(), m.group(2).strip())),

    # "remember X as Y"
    (r"(?:remember|save|store)\s+(.+?)\s+as\s+(.+)",
     lambda m: (m.group(1).strip(), m.group(2).strip())),

    # "my X is Y"
    (r"my\s+(.+?)\s+(?:is|are)\s+(.+)",
     lambda m: (m.group(1).strip(), m.group(2).strip())),

    # "X is at Y" / "X is on Y" (system locations)
    (r"(?:the\s+)?(\w+(?:\s+\w+)?)\s+is\s+(?:at|on|in)\s+(\/\S+)",
     lambda m: (m.group(1).strip(), m.group(2).strip())),

    # "I prefer X" / "I like X"
    (r"I\s+(?:prefer|like|use|want)\s+(.+)",
     lambda m: ("preference", m.group(1).strip())),
]

_COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), ext) for p, ext in _REMEMBER_PATTERNS]

# Transparency patterns
_TRANSPARENCY_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"what do you (?:know|remember) about me",
        r"what have you (?:learned|stored|saved|remembered)",
        r"show (?:me )?(?:my |your )?(?:memories|facts|knowledge)",
        r"what do you have on me",
        r"list (?:my |your )?(?:memories|facts)",
        # "show me everything you remember" / "tell me all you know" / "list all
        # you've stored": a dump-everything request whose object is the verb
        # (know/remember/stored), not the noun (memories/facts) the earlier
        # patterns require. High-precision: needs all|everything → you → a
        # knowledge verb, the transparency shape ("everything YOU REMEMBER").
        r"\b(?:everything|all)\b.{0,15}\byou(?:'ve| have)?\b.{0,12}"
        r"\b(?:know|remember|stored|saved|learned|got on me)\b",
        r"what (?:all )?do you (?:know|remember)\b",
    ]
]

# Forget patterns
_FORGET_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"forget (?:about |that )?(.+)",
        r"delete (?:the )?(?:fact|memory) (?:about |for )?(.+)",
        r"remove (?:the )?(?:fact|memory) (?:about |for )?(.+)",
        r"don't remember (.+)",
        r"clear (?:all )?(?:my )?(?:memories|facts)",
    ]
]

# ── Bare-declarative classification (no explicit "remember" trigger) ──
#
# A user can state a fact about themselves ("my editor is vim") or report a
# problem ("my screen is too bright") without ever saying "remember". The
# router never stores either silently — the user owns memory — but it can
# acknowledge and offer the right next step (store the preference / look into
# the complaint). These deterministic, high-precision signals decide WHICH:
# anything that is not clearly one or the other returns None so the caller
# falls through to the existing routing unchanged.

# "my X is Y" where Y reports a problem, not a stable value.
_COMPLAINT_TOKENS = frozenset([
    "slow", "broken", "frozen", "stuck", "laggy", "crashing", "crashed",
    "dead", "unresponsive", "overheating", "glitchy", "buggy", "lagging",
    "freezing", "failing", "failed", "hanging", "hangs", "noisy",
])
_COMPLAINT_PHRASES = (
    "not working", "won't ", "wont ", "won't.", "acting up", "out of",
    "keeps crashing", "keeps freezing", "keeps restarting", "too ",
)

_PREF_VERB_RE = re.compile(r"\bi\s+(?:prefer|like|use|want)\s+(.+)", re.IGNORECASE)
_MY_X_IS_Y_RE = re.compile(r"\bmy\s+(.+?)\s+(?:is|are)\s+(.+)", re.IGNORECASE)
# M7 leg 3: an imperative ADDRESSED TO THE ASSISTANT ("I want YOU to run it", "I
# want you- InterGen- to run the script") is a command, not a stored preference —
# the "want" verb captured it and the memory offer stole the turn twice (2026-07-08
# finding 2). The exclusion fires when the value after the preference verb is
# "you … to <verb>" (the assistant told to DO something): the object is the
# assistant, not a fact about the user. A genuine preference ("I want dark mode",
# "I prefer vim") does not begin by directing "you".
_IMPERATIVE_TO_ASSISTANT_RE = re.compile(
    r"^\s*you\b[\s\w,'’.-]*?\bto\s+\w+", re.IGNORECASE)

# An INTERROGATIVE turn — the user is ASKING, not stating. Measured defect: the
# preference verb in _PREF_VERB_RE matches just as happily inside a question as
# inside a statement, so "which shell do I use again?" was read as the
# declarative preference "I use <again?>" and the assistant offered to remember
# the literal word "again?" as the user's shell. Every recall question of that
# shape ("what do I use for editing again?", "remind me which shell I use
# again?") lost its answer the same way: the route to memory was RIGHT — the
# question is about a stored fact — but the parse turned a question into a
# statement, and what got stored was the tail of the sentence.
#
# Detection is deliberately two-signal and cheap: a terminal question mark, or an
# interrogative lead (a wh-word or a fronted auxiliary — the shapes English uses
# to open a question). A statement carrying neither is unaffected, so bare
# declaratives ("I use zsh", "my screen is too bright") classify exactly as
# before.
_INTERROGATIVE_LEAD_RE = re.compile(
    r"^\s*(?:what|which|who|whose|where|when|why|how|"
    r"do|does|did|is|are|was|were|am|can|could|should|would|will|"
    r"have|has|had|remind\s+me)\b",
    re.IGNORECASE)


def _is_question(message: str) -> bool:
    """True when the turn is asking rather than stating."""
    stripped = message.strip()
    if stripped.endswith("?"):
        return True
    return bool(_INTERROGATIVE_LEAD_RE.match(stripped))

# Affirmative / negative replies to a yes/no offer. Vocabulary broadened
# 2026-07-01 (offer/accept fix F4): common affirmatives like "absolutely" /
# "affirmative" / "make it so" previously did not match, so they lapsed the
# staged offer and fell into the bare-affirmative hole (now also guarded by F1).
# The alternation BODIES are shared so the prefix matchers (is_affirmative/
# is_negative, used to resolve a reply when an offer IS staged) and the BARE
# matchers (is_bare_*, used by the no-offer guard) can never drift apart.
_AFFIRMATIVE_BODY = (
    r"yes|yeah|yep|yup|sure|ok|okay|k|please|go ahead|go for it|"
    r"do it|do so|proceed|absolutely|affirmative|make it so|sounds good|"
    r"sounds great|that works|please do|yes please|will do|let'?s do it")
_NEGATIVE_BODY = r"no|nope|nah|don't|do not|not now|skip|leave it|no thanks"
_AFFIRMATIVE_RE = re.compile(r"^\s*(?:" + _AFFIRMATIVE_BODY + r")\b", re.IGNORECASE)
_NEGATIVE_RE = re.compile(r"^\s*(?:" + _NEGATIVE_BODY + r")\b", re.IGNORECASE)
# BARE affirmative/negative (F1 correctness fix, 2026-07-02): the reply is
# ENTIRELY an affirmative/negative — the vocab plus an optional politeness tail
# and trailing punctuation, with NOTHING actionable after it. The prefix matchers
# above are wrong for the no-offer guard: they fire on any turn that merely STARTS
# with a vocab word, so a real request ("please show me my disk usage", "ok so how
# do I install X", "no idea why my cpu is high", "go ahead and list my files") was
# dead-ended at the nothing-staged clarify AHEAD of every content route. The bare
# matcher requires a full match, so those content turns pass through to routing.
_POLITE_TAIL = (
    r"(?:[\s,.!]+(?:please|thanks?|thank\s+you|now|already|anyway|"
    r"sure|ok|okay|then|for\s+me))*")
_BARE_AFFIRMATIVE_RE = re.compile(
    r"^\s*(?:" + _AFFIRMATIVE_BODY + r")" + _POLITE_TAIL + r"\s*[.!?,]*\s*$",
    re.IGNORECASE)
_BARE_NEGATIVE_RE = re.compile(
    r"^\s*(?:" + _NEGATIVE_BODY + r")" + _POLITE_TAIL + r"\s*[.!?,]*\s*$",
    re.IGNORECASE)
# Separator between a leading affirmative/negative token and the real request in
# a PREFIXED reply ("Yes, <tail>" / "no thanks - but <tail>"). Stripped so the
# tail routes on its own merits when the router keeps an action offer armed
# (router M3(i)). Shares the vocab bodies above so the matchers can never drift.
_REPLY_PREFIX_SEP_RE = re.compile(r"^[\s,.;:!–—-]+")
# ACCEPTANCE-RESTATING tail (offer-consent execution integrity, decided
# 2026-07-24): the tail of a prefixed "yes" that merely RESTATES the acceptance
# ("Yes, please check" / "yes, go ahead" / "yes, do it") instead of asking for
# something new. Consent must not be conditioned on magic phrasing — "yes,
# please check" over a live check-offer IS the acceptance, and re-offering on
# it was a live-reproduced defect. Bounded allowlist by design: the tail must
# consist ENTIRELY of pro-verb / politeness vocabulary — one residual content
# word ("yes, check the OTHER printer too") fails the full match and keeps the
# current keep-armed-and-route behavior, so a new ask can never fire the staged
# action. Extend the vocabulary on evidence, never speculatively.
_ACCEPTANCE_RESTATE_TOKEN = (
    r"(?:please|thanks?|thank\s+you|go\s+ahead|go\s+for\s+it|go|proceed|"
    r"do\s+(?:it|that|so)|run\s+(?:it|that)|check(?:\s+(?:it|that))?|"
    r"look|verify|try\s+(?:it|that)|sure|ok|okay|now|then|for\s+me|"
    r"sounds\s+good|that\s+works|do|it|that)")
_ACCEPTANCE_RESTATE_RE = re.compile(
    r"^\s*" + _ACCEPTANCE_RESTATE_TOKEN +
    r"(?:[\s,.!]+" + _ACCEPTANCE_RESTATE_TOKEN + r")*\s*[.!?,]*\s*$",
    re.IGNORECASE)

# Gratitude / conversational-closure turns (2026-07-14). A turn that is
# ENTIRELY a thank-you and/or a "that's all" closer — optionally led by an
# affirmative/negative token ("ok thanks", "no thanks, that's all") and carrying
# a politeness tail. WHY this needs its own matcher: _POLITE_TAIL folds
# "thanks"/"thank you" into the bare affirmative/negative matchers, so an
# "ok thanks" closer already matches is_bare_affirmative and hit the no-offer
# guard's cold "nothing staged to confirm" clarify — wrong for a user who is
# simply closing the exchange. is_gratitude_or_closure lets the guard recognise
# the gratitude/closure and return a warm closure instead. Full-match (like the
# bare matchers) so a real request that merely CONTAINS a thank-you ("thanks,
# now show my disk usage") does NOT warm-close.
_GRATITUDE_BODY = (
    r"thanks?|thank\s+(?:you|u|ya)|thx|ty|much\s+appreciated|"
    r"(?:really\s+)?appreciate\s+(?:it|that|you|this)|cheers|many\s+thanks|"
    r"thanks\s+(?:a\s+lot|so\s+much|again)")
_CLOSURE_BODY = (
    r"that'?s\s+(?:all|it|everything|fine|good|helpful)|that'?ll\s+be\s+all|"
    r"nothing\s+else|no\s+more(?:\s+questions)?|we'?re\s+(?:good|done|set|"
    r"all\s+set)|i'?m\s+(?:good|done|all\s+set)|all\s+(?:good|set|done)|"
    r"that\s+(?:helps|helped|did\s+it)")
# A single "closer unit": gratitude, closure, or the affirmative/negative and
# politeness vocab that can accompany one. is_gratitude_or_closure requires the
# WHOLE turn to be one-or-more of these AND at least one genuine gratitude/
# closure phrase — so a lone "yes"/"ok" (no thanks) still falls to the cold
# clarify, while "ok thanks"/"no thanks, that's all" warm-close.
_CLOSER_ACCOMPANY = (
    r"please|now|then|anyway|really|so|much|again|great|awesome|perfect|good|"
    r"for\s+(?:that|it|the\s+help|your\s+help|everything|now)")
_CLOSER_UNIT = (
    r"(?:" + _GRATITUDE_BODY + r"|" + _CLOSURE_BODY + r"|"
    + _AFFIRMATIVE_BODY + r"|" + _NEGATIVE_BODY + r"|" + _CLOSER_ACCOMPANY + r")")
_GRATITUDE_OR_CLOSURE_HAS_RE = re.compile(
    r"\b(?:" + _GRATITUDE_BODY + r"|" + _CLOSURE_BODY + r")\b", re.IGNORECASE)
_GRATITUDE_OR_CLOSURE_FULL_RE = re.compile(
    r"^\s*" + _CLOSER_UNIT + r"(?:[\s,.;:!–—-]+" + _CLOSER_UNIT
    + r")*\s*[.!?,]*\s*$", re.IGNORECASE)


def _looks_like_complaint(value: str) -> bool:
    """True if a 'my X is Y' value reports a problem rather than a stable fact."""
    low = value.lower()
    if any(ph in low for ph in _COMPLAINT_PHRASES):
        return True
    words = re.findall(r"[a-z']+", low)
    return any(w in _COMPLAINT_TOKENS for w in words)


def _looks_like_stable_value(value: str) -> bool:
    """True if a value is a concrete fact/preference worth offering to store.

    A path, or a short (<=2 word) noun phrase that is neither a complaint nor a
    state/gerund. Deliberately conservative: a longer or gerund-ish value
    ('going well', 'running hot') is NOT stable → the caller abstains.
    """
    v = value.strip()
    if v.startswith("/") or v.startswith("~/"):
        return True
    words = v.split()
    if not words or len(words) > 2:
        return False
    if any(w.lower().endswith("ing") for w in words):
        return False
    return not _looks_like_complaint(v)


class MemoryManager:
    """User-controlled fact storage with transparency and forgetting."""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path) if db_path else _default_db_path()
        self._db_lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite database."""
        private_dir(self._db_path.parent)
        # sqlite creates the database file itself, through a plain open, so it
        # would land 0644. Pre-creating it owner-only means sqlite opens an
        # EXISTING file and an ordinary open never changes a mode — the store
        # this module's own docstring calls a per-user secret stays one.
        private_touch(self._db_path)
        with self._db_lock:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    fact_id TEXT PRIMARY KEY,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    source TEXT DEFAULT 'explicit',
                    confidence REAL DEFAULT 0.95,
                    created_at REAL NOT NULL,
                    updated_at REAL,
                    deleted INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_facts_key
                ON facts(key) WHERE deleted = 0
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_facts_category
                ON facts(category) WHERE deleted = 0
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    queries TEXT,
                    tools_used TEXT,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    turn_count INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            logger.info("Memory database initialized at %s", self._db_path)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    # ── Extraction ──

    @staticmethod
    def _shift_perspective(text: str) -> str:
        """Shift first-person to second-person for stored facts.

        'my backup drive' → 'your backup drive'
        """
        import re
        text = re.sub(r'\bmy\b', 'your', text, flags=re.IGNORECASE)
        text = re.sub(r'\bI am\b', 'you are', text, flags=re.IGNORECASE)
        text = re.sub(r'\bI\b', 'you', text, flags=re.IGNORECASE)
        text = re.sub(r'\bme\b', 'you', text, flags=re.IGNORECASE)
        return text

    def extract_and_store(self, message: str) -> list[Fact]:
        """Extract facts from user message and store them.

        Only extracts from explicit patterns — no inference, no LLM.
        Returns list of newly stored facts.
        """
        facts = []
        for pattern, extractor in _COMPILED_PATTERNS:
            match = pattern.search(message)
            if match:
                key, value = extractor(match)
                if key and value and len(key) < 200 and len(value) < 500:
                    key = self._shift_perspective(key)
                    fact = self.store(key, value)
                    if fact:
                        facts.append(fact)
                        logger.info("Extracted fact: %s = %s", key, value)
        return facts

    # ── CRUD ──

    def store(self, key: str, value: str,
              category: str = "general",
              source: str = "explicit",
              confidence: float = 0.95) -> Fact | None:
        """Store a fact. Updates existing fact if key matches."""
        now = time.time()
        fact_id = uuid.uuid4().hex[:16]

        with self._db_lock:
            conn = self._get_conn()

            # Check for existing fact with same key
            existing = conn.execute(
                "SELECT fact_id FROM facts WHERE key = ? AND deleted = 0",
                (key,)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE facts SET value = ?, updated_at = ?, confidence = ? "
                    "WHERE fact_id = ?",
                    (value, now, confidence, existing["fact_id"])
                )
                conn.commit()
                logger.info("Updated fact: %s = %s", key, value)
                return Fact(
                    fact_id=existing["fact_id"], key=key, value=value,
                    category=category, source=source, confidence=confidence,
                    created_at=now, updated_at=now,
                )

            conn.execute(
                "INSERT INTO facts (fact_id, key, value, category, source, "
                "confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fact_id, key, value, category, source, confidence, now)
            )
            conn.commit()

        return Fact(
            fact_id=fact_id, key=key, value=value,
            category=category, source=source, confidence=confidence,
            created_at=now,
        )

    def get(self, key: str) -> str | None:
        """Get a fact value by key."""
        with self._db_lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT value FROM facts WHERE key = ? AND deleted = 0 "
                "ORDER BY created_at DESC LIMIT 1",
                (key,)
            ).fetchone()
            return row["value"] if row else None

    def search(self, query: str) -> list[Fact]:
        """Search facts by text match on key or value."""
        with self._db_lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT * FROM facts WHERE deleted = 0 AND "
                "(key LIKE ? OR value LIKE ?) "
                "ORDER BY created_at DESC LIMIT 20",
                (f"%{query}%", f"%{query}%")
            ).fetchall()
            facts = [self._row_to_fact(r) for r in rows]
            # M1 (bullet 3): memory READ.
            glass.emit("decision", "memory_read", detail={
                "query": query, "hits": len(facts)})
            return facts

    def list_all(self) -> list[Fact]:
        """List all active facts. For user transparency."""
        with self._db_lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT * FROM facts WHERE deleted = 0 "
                "ORDER BY created_at DESC"
            ).fetchall()
            return [self._row_to_fact(r) for r in rows]

    def delete(self, fact_id: str) -> bool:
        """Soft delete a fact, and drop the copy any live index is holding."""
        with self._db_lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT key, value FROM facts WHERE fact_id = ? AND deleted = 0",
                (fact_id,)
            ).fetchone()
            conn.execute(
                "UPDATE facts SET deleted = 1, updated_at = ? "
                "WHERE fact_id = ?",
                (time.time(), fact_id)
            )
            conn.commit()
            logger.info("Deleted fact: %s", fact_id)
        # Outside the store lock: the indexes take their own, and a forget must
        # never be able to deadlock a turn that is ranking facts at that moment.
        if row is not None:
            forget_fact_vectors([fact_cache_text(row["key"], row["value"])])
        return True

    def forget_forms(self, subject: str) -> list[str]:
        """The forms of a forget subject that must be matched against the store.

        A forget arrives in the words the user typed — "my backup drive" — and
        the store holds what the EXTRACTOR wrote. Those are not the same string:
        :meth:`_shift_perspective` turns "my" into "your" on the way in, and the
        same sentence also lands under the bare noun. Matching only the user's
        literal words found neither, so nothing was deleted and InterGen replied
        that it had no such memory while still holding it.

        Three forms, in the order they are tried, each derived from what the
        store side actually does rather than guessed at:
          · the subject as the user said it;
          · the subject with the same perspective shift the store applied;
          · the subject with a leading possessive removed, which is the form the
            extractor's bare-noun row is keyed under.
        Duplicates are dropped so a subject that is already in one of these
        forms is not matched three times.

        The forms WIDEN what a forget matches, and that is the point — but only
        as far as the store side widened what it wrote. A subject naming nothing
        the user stored still matches nothing.
        """
        forms: list[str] = []
        for candidate in (subject,
                          self._shift_perspective(subject),
                          re.sub(r"^(?:my|your|our|the)\s+", "", subject,
                                 flags=re.IGNORECASE)):
            candidate = candidate.strip()
            if candidate and candidate not in forms:
                forms.append(candidate)
        return forms

    def delete_by_key(self, key: str) -> int:
        """Soft delete every fact the user's forget subject names, and drop the
        copies the running daemon is holding in memory.

        Soft, deliberately: whether a forgotten fact's bytes must leave the
        database file is a separate storage-contract question, scheduled for a
        later release and not settled here. What IS settled here is that the
        fact stops being remembered — no active row, nothing recalled, nothing
        left in any conversation's fact-vector cache, and a truthful reply.
        """
        forms = self.forget_forms(key)
        with self._db_lock:
            conn = self._get_conn()
            clause = " OR ".join(["key LIKE ? OR value LIKE ?"] * len(forms))
            match_params: list[Any] = []
            for form in forms:
                match_params.extend([f"%{form}%", f"%{form}%"])
            # Read the rows BEFORE hiding them: once the flag is set they are
            # not selectable by this clause any more, and their text is what
            # names the cached vectors that have to go with them.
            going = [fact_cache_text(r["key"], r["value"]) for r in conn.execute(
                f"SELECT key, value FROM facts WHERE deleted = 0 AND ({clause})",
                match_params
            ).fetchall()]
            cursor = conn.execute(
                "UPDATE facts SET deleted = 1, updated_at = ? "
                f"WHERE deleted = 0 AND ({clause})",
                [time.time(), *match_params]
            )
            conn.commit()
            count = cursor.rowcount
            if count:
                logger.info("Deleted %d facts matching '%s' (forms: %s)",
                            count, key, ", ".join(repr(f) for f in forms))
        # Outside the store lock: the indexes take their own, and a forget must
        # never be able to deadlock a turn that is ranking facts at that moment.
        cleared = forget_fact_vectors(going)
        # A deletion of the user's own data that the record does not mention
        # is a hole in a writer whose mandate is that there are none. The
        # subject is the user's own words, which the record already holds in
        # the turn's prompt; the fact VALUES are not written here.
        glass.emit("memory", "forget", detail={
            "subject": key, "forms": forms, "removed": count,
            "session_vectors_cleared": cleared,
            "physical": False})
        return count

    def clear_all(self) -> int:
        """Soft delete ALL facts. User-requested full reset.

        Every live index's fact-vector cache goes with them: no stored fact
        remains, so no cached fact vector is legitimate.
        """
        with self._db_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "UPDATE facts SET deleted = 1, updated_at = ?",
                (time.time(),)
            )
            conn.commit()
            count = cursor.rowcount
            logger.info("Cleared all facts (%d)", count)
        # Outside the store lock, for the reason delete_by_key states.
        cleared = forget_all_fact_vectors()
        glass.emit("memory", "forget", detail={
            "subject": "__ALL__", "forms": [], "removed": count,
            "session_vectors_cleared": cleared,
            "physical": False})
        return count

    def clear_sessions(self) -> int:
        """Hard-delete ALL session rows. Used by the test harness to reset
        session-continuity state between --repeat runs so each run is
        independent (clear_all preserves the sessions table by design, so
        without this a session test's history accumulates across repeats)."""
        with self._db_lock:
            conn = self._get_conn()
            cursor = conn.execute("DELETE FROM sessions")
            conn.commit()
            count = cursor.rowcount
            logger.info("Cleared all sessions (%d)", count)
            return count

    @property
    def count(self) -> int:
        """Count of active (non-deleted) facts."""
        with self._db_lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COUNT(*) as c FROM facts WHERE deleted = 0"
            ).fetchone()
            return row["c"]

    # ── Intent detection ──

    @staticmethod
    def is_remember_request(message: str) -> bool:
        """Check if the message is asking InterGen to remember something."""
        lower = message.lower()
        return any(lower.startswith(p) for p in [
            "remember", "save that", "store that", "note that",
            "don't forget", "keep in mind",
        ])

    @staticmethod
    def classify_declarative(message: str) -> tuple[str | None, str | None, str | None]:
        """Classify a bare declarative (no explicit 'remember' trigger).

        Returns (kind, key, value):
          'preference' — a stable fact/preference the user stated → offer to store
          'complaint'  — a problem the user reported → offer to assist
          'recall'     — a QUESTION about a preference → answer from the store
          None         — not obviously any → caller falls through unchanged

        Deterministic and high-precision by design: fires only when the shape is
        clearly one of the three. Nothing is stored here — this only decides
        which acknowledgement the router offers (the user owns memory).
        """
        msg = message.strip()

        # Explicit preference verb — "I prefer/like/use/want X" — unambiguous,
        # unless what follows is itself a complaint ("I want X to stop crashing").
        m = _PREF_VERB_RE.search(msg)
        if m:
            # ASKING about a preference is not STATING one. The verb matches
            # inside a question exactly as it does inside a statement, and the
            # tail it captured there was the rest of the question, not a value —
            # "which shell do I use again?" yielded the preference "again?". The
            # turn still belongs to memory (it is about a stored fact); it is a
            # recall, and the router answers it from the store instead of
            # offering to remember a fragment of the user's own question.
            if _is_question(msg):
                return ("recall", None, None)
            value = m.group(1).strip().rstrip(".!")
            # M7 leg 3: "I want YOU to run it" is an imperative to the assistant, not
            # a preference — let it fall through to the action/model routing rather
            # than offering to remember it.
            if _IMPERATIVE_TO_ASSISTANT_RE.match(value):
                return (None, None, None)
            if value and not _looks_like_complaint(value):
                return ("preference", "preference", value)
            return (None, None, None)

        # "my X is Y" — classify on the value.
        m = _MY_X_IS_Y_RE.search(msg)
        if m:
            key = m.group(1).strip()
            value = m.group(2).strip().rstrip(".!")
            if not key or not value or len(key) > 60:
                return (None, None, None)
            if _looks_like_complaint(value):
                return ("complaint", key, value)
            if _looks_like_stable_value(value):
                return ("preference", key, value)

        return (None, None, None)

    @staticmethod
    def is_affirmative(message: str) -> bool:
        """True if the message is an affirmative reply to a yes/no offer."""
        return bool(_AFFIRMATIVE_RE.match(message))

    @staticmethod
    def is_negative(message: str) -> bool:
        """True if the message is a negative reply to a yes/no offer."""
        return bool(_NEGATIVE_RE.match(message))

    @staticmethod
    def is_bare_affirmative(message: str) -> bool:
        """True if the message is ENTIRELY an affirmative (vocab + optional polite
        tail + punctuation, nothing actionable after). For the no-offer guard: a
        content turn that merely starts with 'yes'/'ok'/'please' must NOT count
        (that is is_affirmative's prefix job, correct only when an offer is staged).
        """
        return bool(_BARE_AFFIRMATIVE_RE.match(message))

    @staticmethod
    def is_bare_negative(message: str) -> bool:
        """True if the message is ENTIRELY a negative (see is_bare_affirmative)."""
        return bool(_BARE_NEGATIVE_RE.match(message))

    @staticmethod
    def is_gratitude_or_closure(message: str) -> bool:
        """True if the message is ENTIRELY a thank-you and/or conversational
        closer ("thanks", "ok thanks", "no thanks, that's all", "that helps,
        much appreciated"). Requires at least one genuine gratitude/closure
        phrase, so a lone "yes"/"ok" is NOT one (that stays with the no-offer
        clarify). Lets the no-offer guard return a warm closure instead of the
        cold "nothing staged to confirm" when the user is simply closing."""
        return bool(_GRATITUDE_OR_CLOSURE_HAS_RE.search(message)
                    and _GRATITUDE_OR_CLOSURE_FULL_RE.match(message))

    @staticmethod
    def is_acceptance_restating_tail(tail: str) -> bool:
        """True if the (already-stripped) tail of a prefixed "yes" merely
        RESTATES the acceptance — pro-verb/politeness vocabulary only, no new
        content ("please check", "go ahead", "do it"). Router M3(i) treats a
        prefixed yes with such a tail as the acceptance it is and EXECUTES the
        staged offer (offer-consent execution integrity, decided 2026-07-24:
        consent is not conditioned on magic phrasing). An empty tail counts —
        it is a bare yes by another route. Any residual content word fails the
        match, keeping the keep-armed-and-route path for genuine new asks."""
        if not tail or not tail.strip():
            return True
        return bool(_ACCEPTANCE_RESTATE_RE.match(tail))

    @staticmethod
    def strip_affirmative_prefix(message: str) -> str:
        """Return the substance after a leading affirmative token ("Yes, <tail>"
        -> "<tail>"); the whole message if it does not start with one; "" if it
        was a bare affirmative. Used by router M3(i) to route the tail of a
        prefixed "yes" cleanly while the action offer stays armed."""
        m = _AFFIRMATIVE_RE.match(message)
        if not m:
            return message.strip()
        return _REPLY_PREFIX_SEP_RE.sub("", message[m.end():]).strip()

    @staticmethod
    def strip_negative_prefix(message: str) -> str:
        """Return the substance after a leading negative token ("No, but <tail>"
        -> "but <tail>"); the whole message if it does not start with one; "" if
        it was a bare negative. Router M3(i) routes the tail of a prefixed "no"
        after clearing the offer."""
        m = _NEGATIVE_RE.match(message)
        if not m:
            return message.strip()
        return _REPLY_PREFIX_SEP_RE.sub("", message[m.end():]).strip()

    @staticmethod
    def is_transparency_request(message: str) -> bool:
        """Check if the user is asking what InterGen knows about them."""
        return any(p.search(message) for p in _TRANSPARENCY_PATTERNS)

    @staticmethod
    def is_forget_request(message: str) -> str | None:
        """Check if user is asking to forget something. Returns the subject or None."""
        for pattern in _FORGET_PATTERNS:
            match = pattern.search(message)
            if match:
                if "clear" in message.lower() and ("all" in message.lower()
                                                     or "memories" in message.lower()):
                    return "__ALL__"
                return match.group(1).strip() if match.lastindex else "__ALL__"
        return None

    # ── Response formatting ──

    def format_transparency_response(self) -> str:
        """Format all facts for user inspection."""
        facts = self.list_all()
        if not facts:
            return "I don't have any stored memories about you yet."

        lines = [f"I remember {len(facts)} thing{'s' if len(facts) != 1 else ''} about you:\n"]
        for fact in facts:
            lines.append(f"- **{fact.key}**: {fact.value}")
        return "\n".join(lines)

    def format_forget_response(self, subject: str) -> str:
        """Execute a forget request and return the response."""
        if subject == "__ALL__":
            count = self.clear_all()
            if count:
                return f"Done. I've cleared all {count} memories."
            return "I don't have any memories to clear."

        count = self.delete_by_key(subject)
        if count:
            return f"Done. I've forgotten {count} thing{'s' if count != 1 else ''} about '{subject}'."
        return f"I don't have any memories about '{subject}'."

    # ── Session awareness ──

    def start_session(self) -> str:
        """Start a new session. Returns session_id."""
        session_id = uuid.uuid4().hex[:16]
        with self._db_lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO sessions (session_id, topic, started_at, turn_count) "
                "VALUES (?, ?, ?, 0)",
                (session_id, "", time.time())
            )
            conn.commit()
        self._current_session_id = session_id
        self._session_queries: list[str] = []
        self._session_tools: list[str] = []
        logger.info("Session started: %s", session_id)
        return session_id

    def record_turn(self, query: str, tools_used: list[str] | None = None) -> None:
        """Record a turn in the current session for topic tracking."""
        if not hasattr(self, "_current_session_id"):
            return
        self._session_queries.append(query)
        if tools_used:
            self._session_tools.extend(tools_used)
        # M1 (bullet 3): memory WRITE (session turn record).
        glass.emit("decision", "memory_turn", detail={
            "query": query, "tools_used": tools_used or []})
        with self._db_lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE sessions SET turn_count = turn_count + 1 "
                "WHERE session_id = ?",
                (self._current_session_id,)
            )
            conn.commit()

    def end_session(self, topic_summary: str | None = None) -> None:
        """End the current session with an optional topic summary.

        If no summary provided, generates one from recorded queries.
        """
        if not hasattr(self, "_current_session_id"):
            return

        if topic_summary is None:
            topic_summary = self._auto_summarize_session()

        with self._db_lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE sessions SET topic = ?, queries = ?, tools_used = ?, "
                "ended_at = ? WHERE session_id = ?",
                (
                    topic_summary,
                    "\n".join(self._session_queries[-10:]),
                    ",".join(set(self._session_tools)),
                    time.time(),
                    self._current_session_id,
                )
            )
            conn.commit()
        logger.info("Session ended: %s — topic: %s",
                     self._current_session_id, topic_summary)

    def get_last_session(self) -> dict | None:
        """Get the most recent completed session for cross-session awareness."""
        with self._db_lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM sessions WHERE ended_at IS NOT NULL "
                "AND topic != '' ORDER BY ended_at DESC LIMIT 1"
            ).fetchone()
            if row:
                return {
                    "session_id": row["session_id"],
                    "topic": row["topic"],
                    "queries": row["queries"],
                    "tools_used": row["tools_used"],
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                    "turn_count": row["turn_count"],
                }
            return None

    def format_welcome_back(self) -> str | None:
        """Format a welcome-back message with context from last session.

        Returns None if no prior session exists.
        """
        last = self.get_last_session()
        if not last or not last["topic"]:
            return None

        elapsed = time.time() - last["ended_at"]
        if elapsed < 60:
            return None

        if elapsed < 3600:
            time_ago = f"{int(elapsed / 60)} minutes ago"
        elif elapsed < 86400:
            time_ago = f"{int(elapsed / 3600)} hours ago"
        else:
            days = int(elapsed / 86400)
            time_ago = f"{days} day{'s' if days != 1 else ''} ago"

        return (f"Welcome back. Last time ({time_ago}) you were "
                f"{last['topic']}. What can I help with?")

    def _auto_summarize_session(self) -> str:
        """Generate a topic summary from the session's recorded queries."""
        if not self._session_queries:
            return ""

        queries = self._session_queries[-5:]
        topics = set()
        for q in queries:
            lower = q.lower()
            if any(w in lower for w in ["disk", "storage", "space"]):
                topics.add("checking disk space")
            elif any(w in lower for w in ["memory", "ram"]):
                topics.add("checking memory usage")
            elif any(w in lower for w in ["service", "systemctl", "restart", "start", "stop"]):
                topics.add("managing services")
            elif any(w in lower for w in ["install", "package", "pkm"]):
                topics.add("managing packages")
            elif any(w in lower for w in ["network", "ip", "dns"]):
                topics.add("checking network")
            elif any(w in lower for w in ["file", "read", "config", "log"]):
                topics.add("working with files")
            elif any(w in lower for w in ["hostname", "kernel", "uptime", "system"]):
                topics.add("checking system info")

        if topics:
            return " and ".join(sorted(topics))
        return "general queries"

    # ── Internal ──

    @staticmethod
    def _row_to_fact(row: sqlite3.Row) -> Fact:
        return Fact(
            fact_id=row["fact_id"],
            key=row["key"],
            value=row["value"],
            category=row["category"],
            source=row["source"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted=bool(row["deleted"]),
        )


# ── M2b: retrieval-over-verbatim session memory ──────────────────────────────

@dataclass
class _IndexedTurn:
    """One completed exchange, embedded for relevance retrieval. `vector` is a
    1-D float32 numpy array; `user_input`/`response` are the VERBATIM turn."""
    turn_no: int
    vector: Any  # np.ndarray (dim,), float32
    user_input: str
    response: str


class SessionTurnIndex:
    """M2b Stage B/C — retrieval-over-verbatim session memory (MemGPT, adapted).

    The raw 20-message history window binds only the last ~10 exchanges; an
    antecedent older than that is simply gone (the truncation-lottery), and
    everything inside the window rides along whether relevant or not. This index
    closes the gap WITHOUT the fabrication risk of generative extraction: after
    each completed turn it embeds the verbatim exchange via the dedicated :8081
    embedder and keeps it in an in-memory, session-scoped list; at turn start the
    router asks for the single most relevant PAST exchange the window has already
    lost, and re-presents it VERBATIM as inert quoted context. Because the store
    only ever quotes what was actually said, it CANNOT fabricate memory by
    construction (design D1) — it is verified input, never model output.

    Security/behaviour invariants (design §1.1):
      * Verbatim only — no truncation of turn content anywhere (D2).
      * The retrieved excerpt is handed to the router for LEAST-TRUST, USER-role,
        inert-quoted placement; this class never emits instruction-shaped text (D3).
      * Every index/retrieve/inject/skip/degrade decision emits a glass event (D4).
      * Embedder-down degrades LOUD: retrieve returns None (router falls back to
        the raw window) and `degraded` is surfaced in Status — never a silent
        trust-nothing, never a crash (D5).
      * Indexing runs on ONE bounded worker (depth-1 slot, drop-oldest with a
        glass event) — never thread-per-turn, and the only added inference is a
        milliseconds embed on :8081, zero contention with the chat server (D6).
      * Relevance-selected + threshold-gated + dedup-per-window + capped — no
        standing full dump; a wrong retrieval's blast radius is one turn (D8).

    Not persisted across sessions (cross-session recall stays the explicit-fact
    store); ResetConversation clears it via clear().
    """

    def __init__(self, embedder, *, threshold: float = 0.60,
                 window_turns: int = 10):
        # embedder: Callable[[list[str]], "list[list[float]] | None"] | None —
        # the SAME nomic :8081 client the semantic matcher uses. A None return
        # (server down / malformed) is the degrade signal; this class also guards
        # the call itself so a transient raise degrades identically (never
        # propagates into a turn). None embedder = memory disabled, loudly.
        self._embedder = embedder
        self._threshold = threshold
        # A turn is "outside the raw window" (and thus a valid injection
        # candidate) once at least this many turns have occurred since it — kept
        # in lockstep with the router's raw-history window so injection fires ONLY
        # for material the window has lost, never duplicating what is already in
        # the prompt.
        self._window_turns = max(1, window_turns)
        self._turns: list[_IndexedTurn] = []
        self._surfaced: set[int] = set()   # dedup: never inject the same past turn twice
        self._turn_seq = 0                 # count of turns handed to index_turn()
        # Cache of explicit-fact embeddings, keyed by the verbatim "key: value"
        # text (fact_cache_text — the same string the router builds the ranker's
        # candidate list from). Facts change rarely and persist across
        # conversations, so their vectors are embedded once and reused (a changed
        # fact has new text -> a new key; stale entries are simply never
        # queried). NOT cleared on conversation reset — facts are cross-session,
        # and dropping them every reset would pay to embed them again.
        #
        # A FORGET IS THE EXCEPTION, and it is why this index registers itself
        # below. The cache holds the fact in the user's own words, as the
        # dictionary key; a fact the user has asked InterGen to forget must not
        # still be sitting here, in this conversation or in any other, so the
        # store reaches every live index through the module registry.
        self._fact_vecs: dict[str, Any] = {}
        self._degraded = False             # loud embedder-down flag (Status)
        # Whether the embedder has ever actually ANSWERED. Distinct from
        # _degraded, which only becomes True once an attempt has FAILED, and
        # from the index object merely existing.
        #
        # Without this there is no way to tell "the embedder is up" from "the
        # embedder has never been asked": both read as not-degraded, and the
        # user surface reported "session recall active" on the strength of an
        # index object having been constructed. On a machine where the embedder
        # never came up at all, that sentence was shown until the first recall
        # was attempted and failed — which is precisely the window in which a
        # user is deciding whether to trust it.
        self._verified = False
        self._lock = threading.Lock()
        # Bounded worker: ONE daemon thread drains a bounded FIFO queue. The
        # design specified a depth-1 drop-oldest slot; measured, that dropped 26
        # of 30 turns under a burst faster than the embed (which would break the
        # truncation-lottery recovery this exists for, and the rapid-fire battery),
        # so this uses a small bounded FIFO instead — still ONE bounded worker,
        # still off the hot path, but it preserves turns under normal + moderate
        # load and drops OLDEST (favouring recency) only past a pathological cap
        # that human-paced turns never approach. daemon=True so it never blocks
        # process exit.
        self._queue: deque = deque()
        self._cv = threading.Condition(self._lock)
        self._stopped = False
        self._worker = threading.Thread(target=self._drain, name="m2b-index",
                                        daemon=True)
        self._worker.start()
        # Last, so a half-built index is never handed a forget: everything the
        # drop methods touch exists by this line.
        _register_live_index(self)

    # Bounded backlog cap. Turns are human-paced (seconds apart) and the embed is
    # milliseconds, so the queue sits near-empty in practice; this only bounds a
    # pathological burst so the index can never grow without limit.
    _QUEUE_MAX = 256

    @property
    def degraded(self) -> bool:
        """True once an embed attempt found the embedder unreachable/malformed.
        Surfaced in daemon Status so a degraded memory path is never silent."""
        with self._lock:
            return self._degraded

    @property
    def verified(self) -> bool:
        """True once the embedder has actually answered at least once.

        This is the difference between "session recall works" and "session
        recall is configured". Only a MEASURED success sets it, so a status
        surface can say what it observed rather than what it hoped.
        """
        with self._lock:
            return self._verified

    def _embed_one(self, text: str):
        """Embed a single text -> 1-D float32 np.ndarray, or None (degrade).

        Mirrors semantic._embed's fail-to-None discipline so a down or malformed
        embedder degrades this whole path rather than raising into a turn."""
        if self._embedder is None:
            return None
        try:
            vectors = self._embedder([text])
        except Exception as e:  # transient conn error / malformed server response
            logger.warning("SessionTurnIndex: embedder raised (%s); degrading",
                           type(e).__name__)
            return None
        if not vectors:
            return None
        try:
            import numpy as np
            arr = np.asarray(vectors, dtype=np.float32)
        except (ValueError, TypeError) as e:
            logger.warning("SessionTurnIndex: malformed embedding shape (%s); "
                           "degrading", type(e).__name__)
            return None
        if arr.ndim != 2 or arr.shape[0] < 1 or arr.shape[1] < 1:
            return None
        return arr[0]

    def index_turn(self, user_input: str, response: str) -> None:
        """Stage B: queue a completed exchange for background embedding.

        Assigns the turn its monotonic number NOW (so ordering is stable even if
        the embed lands later), then appends it to the bounded FIFO worker queue
        (cap _QUEUE_MAX; only once the cap is reached is the OLDEST pending turn
        dropped — favouring recency — with a glass event, so a burst never piles
        up unboundedly). Returns immediately; the embed happens off the hot path.

        (The design's literal depth-1 slot dropped 26/30 turns under a rapid-fire
        burst, breaking both the out-of-window recovery the index exists for and
        the reset-enabled battery; the bounded FIFO is the evidence-grounded
        deviation recorded in the package.yml r33 changelog.)"""
        if self._embedder is None:
            return
        with self._cv:
            self._turn_seq += 1
            turn_no = self._turn_seq
            dropped = None
            if len(self._queue) >= self._QUEUE_MAX:
                dropped = self._queue.popleft()  # drop OLDEST — favour recency
            self._queue.append((turn_no, user_input, response))
            self._cv.notify()
        if dropped is not None:
            glass.emit("memory", "index_drop_oldest", detail={
                "dropped_turn_no": dropped[0], "queued_turn_no": turn_no,
                "queue_cap": self._QUEUE_MAX})
        glass.emit("memory", "index_enqueue", detail={"turn_no": turn_no})

    def _drain(self) -> None:
        """Bounded worker loop: embed the pending exchange and append it."""
        while True:
            with self._cv:
                while not self._queue and not self._stopped:
                    self._cv.wait()
                if self._stopped:
                    return
                turn_no, user_input, response = self._queue.popleft()
            # Embed OUTSIDE the lock (the :8081 round-trip must not block
            # index_turn / retrieve). Index user + response together so a turn is
            # retrievable by either side of the exchange.
            vec = self._embed_one(f"{user_input}\n{response}")
            with self._lock:
                if vec is None:
                    self._degraded = True
                    glass.emit("memory", "index_degraded", detail={
                        "turn_no": turn_no,
                        "reason": "embedder unavailable/malformed"})
                    continue
                was_degraded = self._degraded
                self._degraded = False
                # A success here is the only proof the embedder is actually up.
                self._verified = True
                self._turns.append(_IndexedTurn(
                    turn_no=turn_no, vector=vec,
                    user_input=user_input, response=response))
                indexed = len(self._turns)
            if was_degraded:
                glass.emit("memory", "recovered", detail={
                    "reason": "embed succeeded after a degraded window",
                    "stage": "index"})
            glass.emit("memory", "indexed", detail={
                "turn_no": turn_no, "indexed_total": indexed})

    def retrieve(self, query: str, query_vector=None):
        """Stage C: the single most relevant PAST exchange the raw window lost.

        Returns an _IndexedTurn (verbatim) above threshold and not yet surfaced
        this session, else None. `query_vector` (a 1-D float32 array) may be
        passed to REUSE the vector the semantic matcher already computed for this
        turn — making the marginal cost zero on that path; otherwise the query is
        embedded here. Embedder down / no candidates / below threshold all return
        None (the router falls back to the raw window), each glass-logged."""
        with self._lock:
            # Candidates: turns far enough back to have left the raw window, and
            # not already injected this session (dedup).
            cutoff = self._turn_seq - self._window_turns
            candidates = [t for t in self._turns
                          if t.turn_no <= cutoff and t.turn_no not in self._surfaced]
        if not candidates:
            glass.emit("memory", "skip", detail={"reason": "no_candidates_outside_window"})
            return None
        qv = query_vector
        if qv is None:
            qv = self._embed_one(query)
        if qv is None:
            with self._lock:
                self._degraded = True
            glass.emit("memory", "degraded", detail={
                "reason": "query embed unavailable", "stage": "retrieve"})
            return None
        # Self-healing: a successful embed is live proof the embedder is back, so
        # it CLEARS the flag here exactly as the indexer does on its own success
        # path. Without this the flag was write-only on this route — set on
        # failure, never reset — so one cold-start timeout during model warm-up
        # latched the Status banner for the life of the daemon while every
        # subsequent embed returned 200. Reported against r109. The direction is
        # truthful state, not quieter state: the next genuine failure sets it
        # again just as loudly.
        elif query_vector is None:
            self._note_embed_success("retrieve")
        try:
            import numpy as np
            q = np.asarray(qv, dtype=np.float32).reshape(-1)
            qn = float(np.linalg.norm(q))
            if qn == 0.0:
                glass.emit("memory", "skip", detail={"reason": "zero_query_vector"})
                return None
            best = None
            best_score = -1.0
            for t in candidates:
                v = t.vector
                vn = float(np.linalg.norm(v))
                if vn == 0.0:
                    continue
                score = float(np.dot(q, v) / (qn * vn))
                if score > best_score:
                    best_score, best = score, t
        except Exception as e:
            # Numpy math must never take down a turn — degrade to raw window.
            logger.warning("SessionTurnIndex.retrieve: scoring failed (%s); "
                           "degrading", type(e).__name__)
            glass.emit("memory", "degraded", detail={
                "reason": f"scoring_error:{type(e).__name__}", "stage": "retrieve"})
            return None
        if best is None or best_score < self._threshold:
            glass.emit("memory", "skip", detail={
                "top_score": round(best_score, 4),
                "threshold": self._threshold,
                "candidates": len(candidates)})
            return None
        with self._lock:
            self._surfaced.add(best.turn_no)
        glass.emit("memory", "inject", detail={
            "turn_no": best.turn_no, "score": round(best_score, 4),
            "threshold": self._threshold})
        return best

    def embed_query(self, query: str):
        """Embed the incoming query ONCE so both the turn-retrieve and the
        fact-retrieve can share the vector — one :8081 call per turn, not two
        (design §3 budget). Returns a 1-D float32 array, or None on embedder-down
        (sets the loud degraded flag + a glass event; the router then falls back
        to the raw window for both)."""
        qv = self._embed_one(query)
        if qv is None:
            with self._lock:
                self._degraded = True
            glass.emit("memory", "degraded", detail={
                "reason": "query embed unavailable", "stage": "embed_query"})
        else:
            # The per-turn embed runs on EVERY turn, so it is the route that
            # actually observes recovery first; clearing here is what makes
            # Status self-heal without waiting for a background index drain.
            self._note_embed_success("embed_query")
        return qv

    def _note_embed_success(self, stage: str) -> None:
        """Record a live successful embed, clearing the loud degraded flag.

        Only emits the recovery event on an actual transition, so a healthy
        daemon does not spam the glass log every turn.
        """
        with self._lock:
            was = self._degraded
            self._degraded = False
            # A success here is the only proof the embedder is actually up.
            self._verified = True
        if was:
            glass.emit("memory", "recovered", detail={
                "reason": "embed succeeded after a degraded window",
                "stage": stage})

    def retrieve_facts(self, query_vector, facts, *, max_facts: int = 2):
        """The `max_facts` explicitly-stored facts most relevant to the query,
        above threshold — relevance-selected + capped, never a standing dump (D8).

        `facts`: list of (fact_id, text) where text is the VERBATIM "key: value"
        the user stored, spelled by fact_cache_text so the cache, the candidate
        list and a forget all name a fact the same way. Fact vectors are embedded
        lazily and cached; the query vector is REUSED (no extra query embed). Returns list[str] of the verbatim
        fact texts (<= max_facts), or [] on embedder-down / nothing relevant."""
        if query_vector is None or not facts:
            return []
        try:
            import numpy as np
            q = np.asarray(query_vector, dtype=np.float32).reshape(-1)
            qn = float(np.linalg.norm(q))
            if qn == 0.0:
                return []
            scored = []
            best_all = None
            for _fid, text in facts:
                with self._lock:
                    v = self._fact_vecs.get(text)
                if v is None:
                    v = self._embed_one(text)
                    if v is None:
                        continue  # transient embed miss on this fact — stay robust
                    with self._lock:
                        self._fact_vecs[text] = v
                vn = float(np.linalg.norm(v))
                if vn == 0.0:
                    continue
                score = float(np.dot(q, v) / (qn * vn))
                if best_all is None or score > best_all:
                    best_all = score
                if score >= self._threshold:
                    scored.append((score, text))
        except Exception as e:
            logger.warning("SessionTurnIndex.retrieve_facts: scoring failed (%s); "
                           "degrading", type(e).__name__)
            glass.emit("memory", "facts_degraded", detail={"reason": type(e).__name__})
            return []
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen = [t for _, t in scored[:max(1, max_facts)]]
        if chosen:
            glass.emit("memory", "facts_inject", detail={
                "count": len(chosen), "top_score": round(scored[0][0], 4),
                "threshold": self._threshold})
        else:
            glass.emit("memory", "facts_skip", detail={
                "candidates": len(facts),
                "top_score": (round(best_all, 4) if best_all is not None else None),
                "threshold": self._threshold})
        return chosen

    def forget_facts(self, texts) -> int:
        """Drop the cached vectors of facts the user asked InterGen to forget.

        Named exactly, never by resemblance: `texts` are the "key: value"
        strings of the rows the store just removed, so a fact the user did NOT
        name keeps its vector and the next turn does not pay to embed it again.
        Returns the number of entries this index actually held."""
        dropped = 0
        with self._lock:
            for text in texts:
                if self._fact_vecs.pop(text, None) is not None:
                    dropped += 1
        return dropped

    def forget_all_facts(self) -> int:
        """Drop every cached fact vector (the user cleared all their memories).
        Returns the number of entries this index was holding."""
        with self._lock:
            count = len(self._fact_vecs)
            self._fact_vecs.clear()
        return count

    def clear(self) -> None:
        """Drop the whole session index + dedup set (ResetConversation). The
        worker thread stays alive across conversations; `degraded` resets and a
        fresh turn re-evaluates it.

        The fact-vector cache is deliberately KEPT: facts are cross-session, so
        ending a conversation is not a reason to pay to embed them again. What
        the user forgets leaves through forget_facts(), which is a different
        question from ending a conversation."""
        with self._lock:
            n = len(self._turns)
            self._turns.clear()
            self._surfaced.clear()
            self._turn_seq = 0
            self._queue.clear()
            self._degraded = False
            # A success here is the only proof the embedder is actually up.
            self._verified = True
        glass.emit("memory", "index_cleared", detail={"dropped": n})

    def stop(self) -> None:
        """Stop the worker thread (clean shutdown / tests)."""
        with self._cv:
            self._stopped = True
            self._cv.notify()
