"""InterGen perceived-latency voice — the filler picker (design artifact 3).

Loads the operator-tunable filler pools (intergen/data/voice/fillers.json ->
/usr/share/intergen/voice/fillers.json) and serves two kinds of line:

  hop-1 ack       — fired the instant a turn commits to the slow LLM/tool path,
                    before the result exists. Asserts NOTHING about the outcome,
                    so it composes with success, a gate prompt, or a refusal.
  hop-2 progress  — fired when a tool call crosses the slow-lane threshold.
                    Implies delivery; a {what} slot is filled with a clean
                    noun-phrase for the call when one is available.

Selection is random with NO repeat within the last N picks (N from the asset's
no_repeat_window), via a small per-pool ring buffer. The asset is data so the
voice is tunable without a code change.

See docs/architecture/intergen-perceived-latency-design.md.
"""
from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path

logger = logging.getLogger(__name__)

# Resolution order: explicit env override -> shipped system path -> in-repo dev
# copy. The first that exists and parses wins.
_ENV_PATH = "INTERGEN_VOICE_FILLERS"
_SYSTEM_PATH = "/usr/share/intergen/voice/fillers.json"
_REPO_PATH = Path(__file__).resolve().parent / "data" / "voice" / "fillers.json"


def _candidate_paths() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get(_ENV_PATH)
    if env:
        out.append(Path(env))
    out.append(Path(_SYSTEM_PATH))
    out.append(_REPO_PATH)
    return out


# Clean noun-phrase for the hop-2 {what} slot, per (tool, action). Kept small and
# explicit; an unmapped call returns None and the picker uses the generic pool.
def describe_subject(tool: str, action: str | None = None,
                     service: str | None = None) -> str | None:
    if tool == "manage_packages":
        return {
            "list": "the package list",
            "search": "the search results",
            "info": "the package details",
            "verify": "the package check",
        }.get((action or "").lower())
    if tool == "manage_services":
        if (action or "").lower() in ("status", "is-active", "is-enabled",
                                      "is-failed", "show"):
            return f"the {service} service" if service else "the service status"
        if (action or "").lower() in ("list-units", "list-unit-files"):
            return "the service list"
        return None
    return {
        "read_file": "the file",
        "analyze_file": "the analysis",
        "web_search": "the search results",
        "take_screenshot": "the screenshot",
    }.get(tool)


class _Pool:
    """One named pool with no-repeat-within-window selection."""

    def __init__(self, lines: list[str], window: int) -> None:
        self._lines = list(lines)
        self._window = max(0, min(window, max(0, len(self._lines) - 1)))
        self._recent: list[int] = []

    def __len__(self) -> int:
        return len(self._lines)

    def pick(self) -> str:
        if not self._lines:
            return ""
        eligible = [i for i in range(len(self._lines)) if i not in self._recent]
        if not eligible:  # window >= pool size (shouldn't happen) — reset
            eligible = list(range(len(self._lines)))
        idx = random.choice(eligible)
        self._recent.append(idx)
        if len(self._recent) > self._window:
            self._recent.pop(0)
        return self._lines[idx]


class FillerPicker:
    """Loads the filler pools and serves hop-1 / hop-2 lines."""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        data = self._load(path)
        self._window = int(data.get("no_repeat_window", 5))
        self._slot = data.get("templated_slot", "what")
        self._hop1 = _Pool(data.get("hop1_ack", []), self._window)
        hop2 = data.get("hop2_progress", {}) or {}
        self._hop2_generic = _Pool(hop2.get("generic", []), self._window)
        self._hop2_templated = _Pool(hop2.get("templated", []), self._window)
        # Offer-run pools (F6): {command}-templated offers to run a taught command,
        # keyed on the command's safety tier so the reassurance is honest — the
        # confirm pool keeps the "you'll confirm first" promise (the modal fires),
        # the readonly pool drops it (an AUTO command runs immediately on yes).
        offer = data.get("offer_run", {}) or {}
        self._offer_confirm = _Pool(offer.get("confirm", []), self._window)
        self._offer_readonly = _Pool(offer.get("readonly", []), self._window)
        logger.info(
            "Voice fillers loaded: hop1=%d, hop2 generic=%d templated=%d, "
            "offer confirm=%d readonly=%d",
            len(self._hop1), len(self._hop2_generic), len(self._hop2_templated),
            len(self._offer_confirm), len(self._offer_readonly))

    @staticmethod
    def _load(path: str | os.PathLike | None) -> dict:
        candidates = [Path(path)] if path else _candidate_paths()
        for p in candidates:
            try:
                with open(p) as f:
                    return json.load(f)
            except FileNotFoundError:
                continue
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("Voice fillers at %s unreadable: %s", p, e)
                continue
        logger.warning("No voice fillers found (tried %s); fillers disabled.",
                       ", ".join(str(p) for p in candidates))
        return {}

    @property
    def available(self) -> bool:
        return len(self._hop1) > 0

    def hop1(self) -> str:
        """An instant acknowledgment. Asserts nothing about the outcome."""
        return self._hop1.pick()

    def hop2(self, tool: str | None = None, action: str | None = None,
             service: str | None = None) -> str:
        """A still-working nudge. Uses a {what}-templated line when a clean
        subject phrase is available for the call, else the generic pool."""
        subject = describe_subject(tool, action, service) if tool else None
        if subject and len(self._hop2_templated):
            line = self._hop2_templated.pick()
            return line.replace("{" + self._slot + "}", subject)
        return self._hop2_generic.pick()

    def offer(self, command: str, *, readonly: bool = False) -> str:
        """A {command}-templated offer to run a taught command (F6). readonly
        picks the AUTO pool (runs immediately on yes — no confirm-first promise);
        otherwise the confirm pool (a mutating command fires the confirm modal).
        The per-pool no-repeat window keeps back-to-back offers from reading
        identical. Returns '' when the chosen pool is empty so the caller can fall
        back to a canonical template."""
        pool = self._offer_readonly if readonly else self._offer_confirm
        line = pool.pick()
        if not line:
            return ""
        return line.replace("{command}", command)
