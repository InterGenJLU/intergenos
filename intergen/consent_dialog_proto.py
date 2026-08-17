# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Wire protocol shared by the consent-dialog daemon helper and its GTK binary.

This module is the ONE source of truth for the contract between
`intergen.consent_dialog` (daemon side — spawns the dialog) and
`intergen.consent_dialog_gtk` (the standalone branded GTK4 renderer). It is
deliberately dependency-free (stdlib-only, in fact import-free) so the binary
can stay minimal per the security review's §5.3 "does ONE thing" constraint and
the daemon helper can import it without pulling GTK in.

The design follows the branded-consent-dialog security red-team
(`docs/research/branding/consent-dialog/`):

- **Transport (§A / invariant transport):** the daemon hands the binary a SINGLE
  JSON object on **stdin** — never argv. The payload (and any tool arguments,
  which can themselves carry a secret) are JSON *string values*, so a real parser
  reads them as one opaque field; attacker bytes inside them can never be
  re-interpreted as protocol (no line-scanning, no field-confusion). argv carries
  nothing variable.

- **Decision channel (§F / invariant 11 — affirmative-only):** the decision is the
  process **exit code**. The binary initialises its result to DENY and sets an
  affirmative code ONLY inside an explicit Send/Allow handler. Window-close, Esc,
  falling off the main loop, a crash, an unrecognised code → all map to deny/cancel.

- **Pre-render watchdog (§F / invariant 11 — bounded wait):** the binary prints
  `RENDERED_MARKER` to stdout the instant its window is on screen, then blocks.
  The daemon fails FAST (→ zenity fallback) if that marker never arrives, but
  waits patiently (up to the post-render deadline) once it has — distinguishing
  "hung/attacked before render" from "window up, human thinking".

- **Never truncate the egress payload (§E):** a payload larger than
  `MAX_PAYLOAD_BYTES` is refused (fail-closed), never shown truncated.
"""

from __future__ import annotations

# ── Modes ────────────────────────────────────────────────────────────────────
MODE_REVIEW = "review"    # tool-call review gate (Allow once / Allow conversation / Deny)
MODE_CONSENT = "consent"  # phone-a-friend show-before-send egress (Send / Cancel)

# ── Decision channel: exit codes (§F / invariant 11) ─────────────────────────
# Affirmative codes are the ONLY non-deny outcomes. They are distinct non-zero
# values so that a clean exit(0), a fall-off-main, exit(1), a crash, or any
# unmapped code can never be mistaken for consent — fail-closed by construction.
EXIT_DENY = 1                       # the default; also any non-affirmative outcome
EXIT_REVIEW_ALLOW_ONCE = 10         # review mode: "Allow once"
EXIT_REVIEW_ALLOW_CONVERSATION = 11  # review mode: "Allow this conversation"
EXIT_CONSENT_SEND = 10              # consent mode: "Send"
# Reserved: the binary uses os._exit(EXIT_RENDER_FAILED) if it cannot even
# initialise GTK / parse its input — i.e. it never showed the user anything, so
# the daemon should fall back to zenity rather than treat it as a user decision.
EXIT_RENDER_FAILED = 70

# ── Pre-render watchdog signal ───────────────────────────────────────────────
# Printed to stdout (its own line, flushed) the instant the window is mapped.
# SOH-wrapped so it cannot collide with anything in normal output.
RENDERED_MARKER = "\x01INTERGEN_CONSENT_DIALOG_RENDERED\x01"

# Max seconds to wait for RENDERED_MARKER before declaring a render failure and
# falling back to zenity (a hung/attacked binary fails fast here).
PRE_RENDER_TIMEOUT_SECONDS = 20.0

# After the window is on screen, how long to wait for the human's decision before
# treating silence as deny/cancel. Aligned with review_modal's RFC §7.2 one-hour
# implicit-deny so the branded path and the fallback expire identically.
POST_RENDER_DEADLINE_SECONDS = 3600.0

# ── Payload bound (§E — never truncate; too-big = fail-closed) ────────────────
# Generous vs any real consent payload, but bounded so a pathological size cannot
# wedge the renderer. Over this → the daemon denies before spawn AND the binary
# refuses (defense in depth, §F.6). 1 MiB.
MAX_PAYLOAD_BYTES = 1024 * 1024

# ── Scrubbed child environment (§C / invariant 10) ───────────────────────────
# ALLOWLIST, not denylist: the child inherits ONLY these vars (when present in
# the daemon env), which structurally drops the entire code-injection family
# (LD_PRELOAD, LD_LIBRARY_PATH, GTK_MODULES, GTK_PATH, GIO_MODULE_DIR,
# GIO_EXTRA_MODULES, GTK_IM_MODULE, GSETTINGS_BACKEND, PYTHONPATH, …) because
# they are simply not on the list. Kept minimal: the display/session vars GTK
# needs to draw, plus HOME/USER/locale for settings + a FIXED safe PATH.
ENV_ALLOWLIST = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "DBUS_SESSION_BUS_ADDRESS",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_MESSAGES",
)

# A fixed PATH for the child — never inherited (an inherited PATH can point exec
# lookups at attacker dirs). The binary needs no external programs, but GTK/gio
# may; keep it to the standard system locations only.
SAFE_PATH = "/usr/bin:/bin"

# ── JSON schema keys (the single stdin object) ───────────────────────────────
# All values are daemon-supplied TRUSTED metadata EXCEPT `payload`, `arguments`,
# `excerpt`, and `reasoning`, which are untrusted CONTENT shown inert and
# visually sanitised. Chrome (title, provider/destination, provenance, reason,
# button labels) is built ONLY from trusted keys — no content byte touches it
# (§D / invariant 9).
K_MODE = "mode"
K_PROVENANCE = "provenance"      # review: trusted gate classification → badge
K_TOOL = "tool"                  # review: trusted tool name → chrome
K_HEADLINE = "headline"          # review: plain-language action headline — a
                                 # DETERMINISTIC per-(tool,action) template built
                                 # daemon-side (never model-generated). It embeds
                                 # untrusted arg values (package/service/command/
                                 # path), so the binary sanitises it for display
                                 # like any content field; empty ("") when no
                                 # template matched → binary falls closed to the
                                 # verbatim `tool: args` monospace form.
K_REASON = "reason"              # trusted gate/system reason → chrome
K_SOURCE = "source"              # review: trusted source attribution → chrome
K_NEEDS_PKEXEC = "needs_pkexec"  # review: trusted bool → chrome row
K_RISK_TIER = "risk_tier"        # review: trusted ToolRiskTier value → the
                                 # "what does this do" half of the risk copy.
                                 # Absent/unknown renders as the severe case.
K_PROVIDER = "provider"          # consent: trusted egress destination → pill
K_ARGUMENTS = "arguments"        # review: CONTENT (LLM-emitted; may carry secret)
K_EXCERPT = "excerpt"            # review: CONTENT (ingress-derived)
K_REASONING = "reasoning"        # review: CONTENT (LLM-emitted)
K_PAYLOAD = "payload"            # consent: CONTENT — the verbatim outbound bytes
