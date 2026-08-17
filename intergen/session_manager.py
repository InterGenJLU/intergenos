# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen Session Manager — conversation persistence.

Preceding-project pattern: one JSON file per session at
~/.local/share/intergen/sessions/<session_id>.json.
Auto-generates titles from first user message, auto-detects
category (system, files, troubleshooting, general), and
tracks is_live + timestamps.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intergen.interfaces.types import Message, MessageRole

logger = logging.getLogger(__name__)

SESSIONS_DIR = Path.home() / ".local" / "share" / "intergen" / "sessions"
MAX_MESSAGES_PER_SESSION = 200
MAX_TITLE_LENGTH = 80

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Category detection — keywords mapped to categories
CATEGORY_KEYWORDS: dict[str, str] = {
    "system": [
        "disk", "memory", "cpu", "ram", "storage", "hostname",
        "kernel", "uptime", "service", "systemctl", "daemon",
        "package", "pkm", "install", "upgrade", "update",
        "network", "ip", "firewall", "dns", "port",
        "gpu", "driver", "hardware", "usb",
    ],
    "files": [
        "file", "directory", "folder", "path", "read",
        "write", "edit", "config", "/etc/", "/var/",
        "copy", "move", "rename", "delete", "permission",
        "chmod", "chown", "symlink",
    ],
    "troubleshooting": [
        "error", "fail", "broken", "crash", "bug", "issue",
        "not working", "doesn't work", "can't", "debug",
        "diagnose", "fix", "repair", "restart", "reboot",
        "log", "traceback", "stuck", "frozen", "slow",
    ],
}


def _detect_category(text: str) -> str:
    """Auto-detect session category from message content."""
    lower = text.lower()
    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for kw in keywords if kw in lower)
    if not scores or max(scores.values()) == 0:
        return "general"
    return max(scores, key=lambda k: scores[k])


def _generate_title(text: str) -> str:
    """Generate a session title from the first user message.

    Takes first line or first 80 chars, strips whitespace.
    Removes leading question words for cleaner titles.
    """
    first_line = text.split("\n")[0].strip()
    if not first_line:
        return "Untitled"
    # Strip common question prefixes
    title = re.sub(r"^(what|how|can you|please|hey|hi|hello)\s+", "",
                   first_line, flags=re.IGNORECASE).strip()
    if not title:
        title = first_line
    if len(title) > MAX_TITLE_LENGTH:
        title = title[:MAX_TITLE_LENGTH - 3] + "..."
    return title[0].upper() + title[1:] if title else "Untitled"


class SessionManager:
    """Manages conversation sessions as JSON files.

    Sessions are stored at ~/.local/share/intergen/sessions/<id>.json.
    Each file is a JSON object with session metadata and message history.
    """

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self._dir = sessions_dir or SESSIONS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    # -- Create -------------------------------------------------------------

    def create(self, *,
               session_id: str | None = None,
               source_interface: str = "web",
               title: str = "",
               category: str = "general",
               ) -> dict[str, Any]:
        """Create a new session file and return its metadata."""
        sid = session_id or f"{source_interface}_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        session: dict[str, Any] = {
            "session_id": sid,
            "source_interface": source_interface,
            "title": title or "New Session",
            "category": category,
            "is_live": True,
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "model_tier": "medium",
            "message_count": 0,
        }
        self._write(sid, session)
        logger.info("Session created: %s (%s)", sid, source_interface)
        return session

    # -- Load / Save --------------------------------------------------------

    def load(self, session_id: str) -> dict[str, Any] | None:
        """Load a session from disk. Returns None if not found."""
        path = self._path_for(session_id)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            # Convert message dicts back to Message objects if needed
            if "messages" in data:
                data["messages"] = [
                    self._dict_to_message(m)
                    for m in data["messages"]
                ]
            return data
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load session %s: %s", session_id, e)
            return None

    def save(self, session_id: str,
             messages: list[Message],
             title: str = "",
             category: str = "",
             ) -> None:
        """Save session state to disk.

        If title/category are empty, they are auto-detected from messages.
        """
        existing = self.load(session_id) or self.create(
            session_id=session_id,
        )

        # Update metadata
        if title:
            existing["title"] = title
        elif messages and not existing.get("title", "").startswith("New"):
            # Keep existing title if it was already set
            pass
        elif messages:
            # Auto-generate title from first user message
            for m in messages:
                if (isinstance(m, dict) and m.get("role") == "user"):
                    existing["title"] = _generate_title(m["content"])
                    break
                elif hasattr(m, "role") and m.role == MessageRole.USER:
                    existing["title"] = _generate_title(m.content)
                    break

        if category:
            existing["category"] = category
        elif messages:
            # Auto-detect category from all user messages
            all_text = " ".join(
                m["content"] if isinstance(m, dict) else m.content
                for m in messages
                if (isinstance(m, dict) and m.get("role") == "user")
                or (hasattr(m, "role") and m.role == MessageRole.USER)
            )
            if all_text:
                existing["category"] = _detect_category(all_text)

        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        existing["is_live"] = True

        # Convert messages to dicts for JSON serialization
        existing["messages"] = [
            self._message_to_dict(m) for m in messages
        ][-MAX_MESSAGES_PER_SESSION:]
        existing["message_count"] = len(existing["messages"])

        self._write(session_id, existing)

    # -- List ---------------------------------------------------------------

    def list_sessions(self, source_interface: str = "") -> list[dict[str, Any]]:
        """List all sessions, most recently updated first.

        If source_interface is specified, filters to that interface.
        """
        sessions: list[dict[str, Any]] = []
        for path in sorted(self._dir.glob("*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(path) as f:
                    data = json.load(f)
                if source_interface and data.get("source_interface") != source_interface:
                    continue
                sessions.append({
                    "session_id": data.get("session_id", path.stem),
                    "title": data.get("title", "Untitled"),
                    "updated": data.get("updated_at", ""),
                    "category": data.get("category", "general"),
                    "is_live": data.get("is_live", False),
                    "message_count": data.get("message_count",
                                              len(data.get("messages", []))),
                })
            except (OSError, json.JSONDecodeError):
                continue
        return sessions

    # -- Delete -------------------------------------------------------------

    def delete(self, session_id: str) -> bool:
        """Delete a session file. Returns True if deleted."""
        path = self._path_for(session_id)
        try:
            if path.exists():
                path.unlink()
                logger.info("Session deleted: %s", session_id)
                return True
        except OSError as e:
            logger.warning("Failed to delete session %s: %s", session_id, e)
        return False

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not _SESSION_ID_RE.fullmatch(session_id):
            raise ValueError(
                f"Invalid session_id: {session_id!r}. "
                f"Must match {_SESSION_ID_RE.pattern}"
            )

    def _path_for(self, session_id: str) -> Path:
        self._validate_session_id(session_id)
        return self._dir / f"{session_id}.json"

    def _write(self, session_id: str, data: dict[str, Any]) -> None:
        path = self._path_for(session_id)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(path)

    @staticmethod
    def _message_to_dict(msg: Any) -> dict[str, Any]:
        if isinstance(msg, dict):
            return msg
        return {
            "role": msg.role.value if hasattr(msg, "role") else "system",
            "content": getattr(msg, "content", ""),
        }

    @staticmethod
    def _dict_to_message(d: dict[str, Any]) -> Message:
        role = MessageRole(d.get("role", "system"))
        return Message(
            role=role,
            content=d.get("content", ""),
        )