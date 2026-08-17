# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Chronicle's protection-status state model (decided 2026-07-30).

The Overview window renders one of six distinguishable protection states —
the honest replacement for the binary socket-exists probe that left the
status group a bare heading whenever the engine was unreachable:

  PROTECTED     engine reachable, captures exist
  NO_CAPTURES   engine reachable, nothing captured yet
  TARGET_ABSENT engine reachable, a target is configured but not attached
  SERVICE_DOWN  socket absent, or present but refusing / timing out
  UNAUTHORIZED  engine reachable, authorization refused by polkit
  NO_ACCESS     engine running, but this account may not open its socket

SERVICE_DOWN, UNAUTHORIZED and NO_ACCESS are different states with different
remedies (start the service; retry so polkit can raise its authentication
dialog; be added to the chronicle group); collapsing any of them into another
would tell one class of user to start a service that is already running, or to
answer a dialog that will never appear.

NO_ACCESS arrived with the socket's group gate (2026-08-04). Before it the
socket was mode 0666 and every local process could connect, so "the socket
refused me" could only mean the engine was gone; now it can also mean the
account is outside the group that may open it, and a permission denial that
rendered as "the service isn't running" would send the user to a Start button
that cannot fix anything.

Headless by design: no GTK import, so the engine test suite exercises the
classification and the copy table directly; the GUI consumes both. The
verdict must always be derived from the same `status` payload the detail
rows render — never from a second, differently-timed probe — so the headline
and the detail cannot disagree.
"""

# The six states.
PROTECTED = "protected"
NO_CAPTURES = "no_captures"
TARGET_ABSENT = "target_absent"
SERVICE_DOWN = "service_down"
UNAUTHORIZED = "unauthorized"
NO_ACCESS = "no_access"

# The user-facing copy, one string per key. The verdict names the user's
# situation, never a component — "Backups are paused", not "chronicled is
# inactive"; the daemon's name lives only behind the Technical-details
# expander.
COPY = {
    "header.name": "Chronicle",
    "verdict.protected": "Protected — last capture {when}",
    "verdict.no_captures": "Not protected yet — nothing has been captured",
    "verdict.target_absent": "Paused — the backup drive is not attached",
    "verdict.service_down":
        "Backups are paused — the Chronicle service isn't running",
    "verdict.unauthorized":
        "Waiting for permission — Chronicle needs an administrator to allow "
        "backups",
    "verdict.no_access":
        "This account cannot use Chronicle — it is not allowed to reach the "
        "backup service",
    "banner.service_down":
        "Backups are paused — the Chronicle service isn't running.",
    "banner.no_access":
        "This account cannot use Chronicle. Backups are still running; this "
        "account is not allowed to see or change them.",
    "banner.button": "Start",
    "card.meaning": "Nothing is being captured right now",
    "card.existing": "Safe — restoring needs the service running too",
    "action.start": "Start backups",
    "action.capture": "Capture now",
    "action.choose_drive": "Choose a drive",
    "action.allow": "Allow…",
    "expander.technical": "Technical details",
    "tooltip.capture_disabled":
        "The Chronicle service must be running to capture",
    # The remedy is an administrator action on the account, so the window
    # states it plainly instead of offering a button that would fail.
    "no_access.remedy":
        "An administrator can allow it by adding this account to the "
        "\"chronicle\" group. The change takes effect at the next login.",
}

VERDICT_KEY = {
    PROTECTED: "verdict.protected",
    NO_CAPTURES: "verdict.no_captures",
    TARGET_ABSENT: "verdict.target_absent",
    SERVICE_DOWN: "verdict.service_down",
    UNAUTHORIZED: "verdict.unauthorized",
    NO_ACCESS: "verdict.no_access",
}

# Verdict tone -> the theme's semantic style class (success #10b981,
# warning #f59e0b, error #ef4444 — existing tokens, no new colour).
TONE = {
    PROTECTED: "success",
    NO_CAPTURES: "warning",
    TARGET_ABSENT: "warning",
    SERVICE_DOWN: "error",
    UNAUTHORIZED: "warning",
    NO_ACCESS: "error",
}

# The short state tag shown on the verdict row's right edge.
TAG = {
    PROTECTED: "PROTECTED",
    NO_CAPTURES: "NOT YET",
    TARGET_ABSENT: "PAUSED",
    SERVICE_DOWN: "STOPPED",
    UNAUTHORIZED: "WAITING",
    NO_ACCESS: "NOT ALLOWED",
}

# The engine's authorization-refused reason string starts with this marker
# (api.authorize_verb); tests/chronicle lock the two together so neither can
# drift without the other.
NOT_AUTHORIZED_MARKER = "not authorized"

# The group that may open the engine socket (api.ENGINE_SOCKET_GROUP). Named
# here too so the copy above and the engine agree; tests lock them together.
ENGINE_GROUP = "chronicle"

# The unit the service-down Start action manages, and the equivalent command
# line shown behind the Technical-details expander.
SERVICE_UNIT = "chronicled.service"
START_ARGV = ["systemctl", "start", SERVICE_UNIT]


def is_unauthorized(error_message):
    """True when an engine error string is the polkit authorization refusal."""
    return str(error_message or "").startswith(NOT_AUTHORIZED_MARKER)


def classify(status):
    """Classify a successful `status` payload into PROTECTED / NO_CAPTURES /
    TARGET_ABSENT. A configured-but-absent target outranks the capture
    history — captures cannot currently reach it. "No external target" is a
    supported configuration, not a defect: the always-on local history still
    runs, so it classifies by capture history alone."""
    target = status.get("target")
    if target and not status.get("target_present"):
        return TARGET_ABSENT
    if status.get("last_capture"):
        return PROTECTED
    return NO_CAPTURES


def latest_capture_epoch(status):
    """The newest wall-clock across the per-layer last_capture map, or None."""
    values = [w for w in (status.get("last_capture") or {}).values() if w]
    return max(values) if values else None
