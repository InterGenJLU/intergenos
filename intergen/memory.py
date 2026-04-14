"""InterGen memory manager — user-controlled persistent fact storage.

Ported from JARVIS core/memory_manager.py (Phases 1 + 6 only).
Deliberately simple: explicit pattern extraction, text search,
full user transparency, soft deletes. No FAISS, no batch LLM
extraction, no proactive surfacing.

The user controls what InterGen remembers:
  "Remember that my backup drive is /dev/sdb1"
  "What do you know about me?"
  "Forget about my backup drive"

PRIME DIRECTIVE: the user owns the memory. Transparent, inspectable,
deletable. No silent profiling.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "/var/lib/intergen/data/memory.db"


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


class MemoryManager:
    """User-controlled fact storage with transparency and forgetting."""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path or _DEFAULT_DB_PATH)
        self._db_lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite database."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
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
            return [self._row_to_fact(r) for r in rows]

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
        """Soft delete a fact."""
        with self._db_lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE facts SET deleted = 1, updated_at = ? "
                "WHERE fact_id = ?",
                (time.time(), fact_id)
            )
            conn.commit()
            logger.info("Deleted fact: %s", fact_id)
            return True

    def delete_by_key(self, key: str) -> int:
        """Soft delete all facts matching a key pattern."""
        with self._db_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "UPDATE facts SET deleted = 1, updated_at = ? "
                "WHERE deleted = 0 AND (key LIKE ? OR value LIKE ?)",
                (time.time(), f"%{key}%", f"%{key}%")
            )
            conn.commit()
            count = cursor.rowcount
            if count:
                logger.info("Deleted %d facts matching '%s'", count, key)
            return count

    def clear_all(self) -> int:
        """Soft delete ALL facts. User-requested full reset."""
        with self._db_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "UPDATE facts SET deleted = 1, updated_at = ?",
                (time.time(),)
            )
            conn.commit()
            count = cursor.rowcount
            logger.info("Cleared all facts (%d)", count)
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
