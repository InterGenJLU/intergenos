# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Durable off-peak capture queue (spec §5).

When a capture's estimated change size exceeds the threshold, it is not run
immediately — a small JSON intent is written to a durable on-disk spool and
drained in the off-peak window. The spool lives in the always-on LOCAL store,
so it survives reboot: the sentinel re-drives it on start.

Two distinct behaviours, both honest:
  * A large change held for off-peak is VISIBLE — queue_status() reports
    "N changes queued — will back up after <working-hours>", surfaced in the
    GUI and `chronicle status`.
  * An ABSENT target is a QUIET skip that leaves the intent queued for
    catch-up on reattach — quiet by design, not a silent failure of a change
    the user made (spec §5).

An intent is {id, layer, scope, trigger_time, reason, estimate}. The id and the
spool filename are derived deterministically from the intent's content + trigger
time (no randomness needed), and sort by trigger time.
"""

import hashlib
import json
import os
from pathlib import Path

from . import paths as _paths


def _intent_id(intent):
    basis = "|".join(str(intent.get(k, "")) for k in
                     ("layer", "scope", "trigger_time", "reason", "estimate"))
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
    return f"{int(intent.get('trigger_time', 0)):020d}-{digest}"


class Queue:
    """The durable capture-intent spool for a local store root."""

    def __init__(self, local_root):
        self.dir = _paths.queue_dir(local_root)

    def _path(self, intent_id):
        return self.dir / f"{intent_id}.json"

    def enqueue(self, intent):
        """Write a durable intent (idempotent by content). Returns the id."""
        intent = dict(intent)
        intent.setdefault("id", _intent_id(intent))
        self.dir.mkdir(parents=True, exist_ok=True)
        dest = self._path(intent["id"])
        # Atomic write so a crash mid-enqueue never leaves a half-written intent.
        fd, tmp = _mkstemp_in(self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(intent, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest)
        except BaseException:
            _silent_unlink(tmp)
            raise
        return intent["id"]

    def list(self):
        """All queued intents, sorted by id (i.e. by trigger time)."""
        if not self.dir.exists():
            return []
        out = []
        for p in sorted(self.dir.iterdir()):
            if p.is_file() and p.suffix == ".json" and not p.name.startswith(".tmp-"):
                try:
                    out.append(json.loads(p.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
        return out

    def get(self, intent_id):
        p = self._path(intent_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def remove(self, intent_id):
        """Remove a drained intent. Returns True if it existed."""
        p = self._path(intent_id)
        try:
            p.unlink()
            return True
        except FileNotFoundError:
            return False

    def count(self):
        return len(self.list())

    def status_summary(self, work_window_text=None):
        """A human line for `chronicle status` / the GUI. Empty when idle."""
        n = self.count()
        if n == 0:
            return ""
        when = (f"will back up after {work_window_text}"
                if work_window_text else "will back up in the off-peak window")
        noun = "change" if n == 1 else "changes"
        return f"{n} {noun} queued — {when}."

    def drain(self, run_intent):
        """Drain the queue by calling run_intent(intent) for each entry.

        run_intent returns True on a successful capture (the intent is removed)
        or False to leave it queued (e.g. the target is still absent — the
        quiet-skip catch-up path). Returns (drained, remaining) counts.
        """
        drained = 0
        for intent in self.list():
            ok = run_intent(intent)
            if ok:
                self.remove(intent["id"])
                drained += 1
        return drained, self.count()


def _mkstemp_in(directory):
    import tempfile
    Path(directory).mkdir(parents=True, exist_ok=True)
    return tempfile.mkstemp(prefix=".tmp-", dir=str(directory))


def _silent_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass
