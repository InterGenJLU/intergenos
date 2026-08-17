# InterGen Web UI — WebSocket Protocol Contract

**Status:** Implemented (InterGenOS 1.0-dev)
**Purpose:** Define the message contract between `intergen/web_server.py`
and the browser/console frontends. Every message type and payload shape
documented here is verified against the current `intergen/web_server.py`
implementation so the frontend has a single source of truth. Fields not
yet wired end-to-end are called out explicitly rather than presented as
shipped behavior.

---

## Connection

```
Browser: ws://localhost:8089/ws?token=<hex_token>&source_interface=<web|console>

Server → {type: "connected",
          client_id: "...",
          source_interface: "web",
          timestamp: 1737...,
          system_status: {...}     // §System Status — only when governance is wired
         }
```

The auth token is generated at `intergen setup` time and written to
`~/.config/intergen/web-token`. The launch script injects it into the
browser URL. The token is validated once at WebSocket handshake — there
is no per-message auth. An invalid or missing token returns an
`auth_failed` error at the HTTP layer before the WebSocket upgrade.

---

## Message Reference

### §1 — Client → Server

The server dispatches on `type` via a handler map in
`WebServer._dispatch_loop`. The current handled types:

| Type | Fields | Purpose |
|---|---|---|
| `message` | `{content: str}` | Send a chat message to InterGen |
| `gate_decision` | `{tool_call_id: str, decision: "allow"\|"allow_conversation"\|"deny"}` | Respond to a provenance gate prompt |
| `switch_model` | `{tier: "small"\|"medium"\|"large"}` | Request a model tier preference change |
| `slash_command` | `{command: str}` | Execute a slash command (see §12) |
| `frontier_escalate` | `{content: str}` | Phone-a-friend: send content to the configured frontier model, with a show-before-send consent modal on the server side |
| `switch_session` | `{session_id: str}` | Load a different conversation session |
| `new_session` | `{}` | Start a new empty session |
| `list_sessions` | `{}` | Request the session list (also pushed proactively after session changes) |
| `request_health` | `{}` | Request a full health report |
| `request_governance` | `{}` | Request the commandments + governance status |
| `request_metrics` | `{}` | Request the metrics dashboard payload |

An unrecognized `type` returns an `unknown_type` error (see §11).

### §2 — Core Response Flow (streaming)

```
→ {type: "stream_start", turn_id: "uuid",
     source: "llm_tools",           // RouteResult.source, e.g. "llm_tools" / "llm_freeform" / "system_map"
     model_name: "local"}           // resolved from the llama-server endpoint; "local" when not otherwise named

→ {type: "stream_token", turn_id: "uuid", token: "H"}
→ {type: "stream_token", turn_id: "uuid", token: "el"}
  ... (more tokens) ...

→ {type: "stream_end", turn_id: "uuid",
     full_response: "Hello! ...",
     source: "llm_tools",
     used_llm: true,
     escalated: false,
     confidence: 0.94,               // last routing confidence, or null if the turn didn't reach semantic routing
     escalation_offer: null,         // advisory string offering a frontier-model escalation, or null
     stats: {total_ms: 4520, tokens: 847, tool_calls_count: 1}
   }
```

Any tool results from the turn are sent as separate `tool_executed`
messages (§5) after `stream_end`, not embedded in it.

If a turn commits to the tool path, a `tool_ack` (hop-1, perceived-latency
filler — see
[intergen-perceived-latency-design.md](../intergen-perceived-latency-design.md))
fires immediately after `stream_start`:

```
→ {type: "tool_ack", turn_id: "uuid", text: "One moment..."}
```

If a tool call takes longer than the slow-lane threshold, a `tool_progress`
(hop-2) nudge fires while the call is still running:

```
→ {type: "tool_progress", turn_id: "uuid", text: "Still working on that..."}
```

If a tool call requires a provenance-gate hold, the stream pauses instead
and a `gate_prompt` (§3) is sent; the caller responds with `gate_decision`,
the server replies with `gate_resolved`, and streaming resumes.

### §3 — Provenance Gate

Constructed once, from a single call site
(`WebServer._handle_tool_call_with_gate` → `gate_prompt_msg`):

```json
{
  "type": "gate_prompt",
  "turn_id": "uuid",
  "tool_call_id": "uuid",
  "action": "{\"action\": \"upgrade\", \"names\": [\"firefox\"]}",
  "tool_name": "manage_packages",
  "provenance": {
    "classification": "ingress_derived"
  },
  "governance_check": null,
  "risk_tier": "user_scope_state_changing",
  "blocked_by": "provenance_gate"
}
```

Notes on current behavior:

- `action` is `json.dumps(tool_call.arguments)` truncated to 200
  characters — a raw args dump, not a rendered command string.
- `provenance.classification` is the tool call's declared/effective
  `source_of_request` (see
  [intergen-provenance-gate-design.md](../intergen-provenance-gate-design.md)
  §3), falling back to `user_direct` if absent.
- `governance_check` is currently always `null` on this path. The
  `GovernanceEngine.evaluate()` call that runs before the prompt IS used
  to decide a non-overridable hard-deny (`hash_integrity` /
  `owner_only` — see §4), but its per-gate `checks` list is not yet
  threaded into the `gate_prompt` payload. Do not build a frontend that
  assumes `governance_check` is populated for review-modal prompts today.
- `risk_tier` is a fixed string on this code path (`"user_scope_state_changing"`)
  rather than the dynamically computed tier for the specific call. A
  hard-deny (privileged, non-overridable) never reaches this prompt at
  all — it resolves straight to a `gate_resolved` deny (§2/§7 below).

Read-only tool calls are auto-approved server-side with no `gate_prompt`
at all (the "FREE" path), except for tools in the ingress set (see the
provenance-gate design), whose *result* can carry injection content even
though the call itself is read-only.

### §4 — Governance Check (internal shape, not yet on the wire for gates)

`GovernanceEngine.evaluate()` (`intergen/governance.py`) returns a
`GovernanceDecision` with a `checks: list[GovernanceCheck]` field, each
entry shaped `{gate_name, passed, reason, detail}`. This is the internal
representation the doc's `governance_check` object below described; as
noted in §3, it currently drives only the hard-deny decision and is not
serialized into `gate_prompt`. The illustrative shape, if/when wired:

```json
{
  "governance_check": {
    "checks": [
      {"gate_name": "hash_integrity", "passed": true},
      {"gate_name": "owner_only",     "passed": true, "reason": "Not an owner-only action"},
      {"gate_name": "cooldown",       "passed": false,
        "reason": "4 manage_packages actions in 22 minutes."}
    ],
    "blocked_by": "cooldown"
  }
}
```

The governance dashboard (§9) and the `/tier` slash command (§12) already
expose the equivalent data via `governance_report` and `tier_status`.

### §5 — Tool Execution Visibility

Sent once per executed tool, after `stream_end`:

```json
{
  "type": "tool_executed",
  "tool_name": "manage_packages",
  "success": true,
  "summary": "142 packages installed. Sample: firefox, openssl, curl, ... Full list shown to the user.",
  "full_output": "<the complete tool content, only present when it adds something beyond summary>"
}
```

`summary` is the tool's `model_summary` when set (see
[intergen-structured-tool-returns-design.md](../intergen-structured-tool-returns-design.md)),
otherwise the first 256 characters of `content`. `full_output` is included
only when the full payload is longer than what `summary` already shows,
so a frontend can offer a "show full output" expander.

### §6 — `gate_resolved`

Sent after a gate decision is reached (user response, timeout, or
non-overridable governance deny):

```json
{"type": "gate_resolved", "tool_call_id": "uuid", "decision": "allow_conversation"}
{"type": "gate_resolved", "tool_call_id": "uuid", "decision": "deny", "reason": "Gate prompt timed out after 5 minutes."}
```

The gate timeout is a fixed 5 minutes; on timeout the decision resolves
to `deny` with a `reason` field.

### §7 — System Status

Pushed on connect (inside `connected`, only when governance is wired) and
periodically every `SYSTEM_STATS_INTERVAL` (60s):

```json
{
  "type": "system_status",
  "uptime_seconds": 38400,
  "connections": {"total": 3, "web": 2, "console": 1},
  "engine": {"ready": true, "model_present": true},
  "context_size": 40960,
  "model_name": "Qwen3.5-9B-intergen-round3-Q4_K_M.gguf",
  "governance": {"autonomy_tier": 2, "autonomy_tier_name": "PROPOSE", "hash_verified": true, "active_cooldowns": 1},
  "metrics": {"...": "..."}
}
```

`engine.ready` reflects whether `llama_manager` reports the inference
server running; `model_present` reflects whether a `.gguf` file exists in
the model directory, so the client can distinguish "run `intergen setup`"
from "model downloaded, server still starting." `context_size` and
`model_name` are `0`/`""` when no inference server is up. `governance`
and `metrics` keys are present only when those subsystems are wired into
the server.

Actual shipped model tiers (`intergen/model_manager.py:MODEL_CATALOG`):
TIER_1 `InternVL3.5-2B` (vision, ~1.2 GB), TIER_2 `Qwen3.5-9B` (~5.5 GB),
TIER_3 `Qwen3.5-35B-A3B` (~21 GB). `model_name` reflects the actual loaded
GGUF filename, not a marketing name.

### §8 — Health Report

Returned on `request_health` or the `/health`/`/status` slash commands.
When a `HealthAggregator` is wired, `report = health_agg.collect()` is
returned with `type` and `timestamp` added — see `intergen/health.py` for
the full layer/check shape. The fallback (aggregator unavailable) shape:

```json
{
  "type": "health_report",
  "timestamp": "2026-07-11T10:31:00-0500",
  "layers": [
    {"name": "Web Server", "checks": [
      {"name": "Web server", "status": "green", "summary": "Running on 127.0.0.1:8089"},
      {"name": "Connections", "status": "green", "summary": "3 active"}
    ]},
    {"name": "Governance", "checks": [
      {"name": "Governance hash", "status": "green", "summary": "Verified"},
      {"name": "Autonomy tier", "status": "green", "summary": "PROPOSE"}
    ]}
  ],
  "summary": {"green": 4, "yellow": 0, "red": 0}
}
```

### §9 — Governance Dashboard

Returned on `request_governance` or the `/governance` slash command.
Renders the commandments (`GovernanceEngine.get_commandments()`) plus
current tier information:

```json
{
  "type": "governance_report",
  "timestamp": "2026-07-11T10:31:00-0500",
  "autonomy_tier": 2,
  "autonomy_tier_name": "PROPOSE",
  "hash_verified": true,
  "hash_path": "/var/lib/intergen/governance/governance.sha256",
  "active_cooldowns": 1,
  "commandments": [
    {"num": 1, "title": "Serve the system above all else.",
     "enforcement": "prompt_anchored",
     "text": "Every action must serve the stability, security, and usability..."}
  ]
}
```

This payload does not currently include a per-decision audit trail; that
data is available separately via `intergen tool-log` (see
[intergen-tool-author-guide.md](../intergen-tool-author-guide.md) §10).

### §10 — Session Model

Sessions are persisted via `SessionManager`
(`~/.local/share/intergen/sessions/` — see `intergen/session_manager.py`
for the exact path resolution). Pushed proactively after any session
change (`switch_session`, `new_session`, and mid-turn autosave), and on
`list_sessions`:

```json
{
  "type": "session_list",
  "sessions": [
    {"session_id": "uuid", "...": "SessionManager.list_sessions() entry shape"}
  ],
  "current_session_id": "uuid"
}
```

Related messages: `session_created` (sent by `/new`), `session_switched`
(sent by `switch_session`, includes `session_id` + `message_count`), and
`buffer_cleared` (sent by `/clear`).

### §11 — Error States

Errors are constructed by `_make_error(code, message, detail=None)` and
sent as `{"type": "error", "code": ..., "message": ..., "detail": ...}`.
The codes actually in use:

```
auth_failed           — invalid/missing token (HTTP-layer, pre-upgrade)
busy                   — a turn is already in progress on this connection
empty_command          — slash_command sent with an empty command
empty_message          — message/frontier_escalate sent with empty content
internal_error         — unhandled server-side exception
invalid_decision       — gate_decision with a decision outside allow/allow_conversation/deny
invalid_json           — message payload was not valid JSON
invalid_model_tier     — switch_model with a tier outside small/medium/large
invalid_session_id     — switch_session with a malformed session_id
llm_unavailable        — LLM router not initialized
missing_field          — a required field was absent from the message
router_unavailable     — the router dependency is not wired
unknown_command        — slash_command with an unrecognized command
unknown_type           — a message type not in the handler map
```

### §12 — Slash Commands

Handled in `WebServer._handle_slash_command`:

| Command | Purpose |
|---|---|
| `/new` | Start a new session (sends `session_created`) |
| `/clear` | Clear the in-memory conversation buffer (sends `buffer_cleared`) |
| `/health` or `/status` | Request a full health report |
| `/governance` | Request the commandments + tier status |
| `/metrics` | Request the metrics dashboard payload |
| `/tier` | Request current governance tier status (sends `tier_status`) |

An unrecognized slash command returns `unknown_command`. Model-tier
switching is a dedicated `switch_model` message type (§1), not a slash
command.

### §13 — Control-message responses

Three of the §1 client requests get a dedicated server reply that is not
part of the streaming flow. Each shape is verified against
`intergen/web_server.py`.

**`frontier_response`** — reply to `frontier_escalate`
(`_handle_frontier_escalate`). An empty `frontier_escalate` returns an
`empty_message` error (§11) instead. If no frontier provider is configured
the reply is unsent; otherwise the server runs a show-before-send consent
modal, and the reply carries the frontier model's text (or a cancel/error
line) with `sent` reflecting whether anything actually left the machine:

```json
{"type": "frontier_response", "sent": false,
 "content": "No frontier model is configured. Add a provider to ~/.config/intergen/ (the human-only config)."}
{"type": "frontier_response", "sent": true, "content": "…frontier model reply…", "provider": "anthropic"}
```

`provider` is the primary provider name when `sent` is true, else `null`.
On decline/error `sent` is `false` and `content` carries the reason.

**`model_changed`** — reply to `switch_model` (`_handle_switch_model`):

```json
{"type": "model_changed", "tier": "medium"}
```

This acknowledges a stored tier *preference* on the connection; it does not
mean a different GGUF is loaded. An actual model swap requires llama-server
to reload a different GGUF (tens of seconds) and is driven separately by the
model manager. An out-of-range tier returns `invalid_model_tier` (§11).

**`metrics_report`** — reply to `request_metrics` or the `/metrics` slash
command (`_handle_request_metrics`). Merges the metrics collector's status
with a governance snapshot and the live connection count:

```json
{
  "type": "metrics_report",
  "counters": {"requests": 0, "llm_calls": 0, "escalations": 0, "route_keyword": 0},
  "governance": {"autonomy_tier": 2, "hash_verified": true, "active_cooldowns": 0},
  "connections": 3
}
```

The keys inside come from `MetricsCollector.get_status()` (see
`intergen/metrics.py`); `governance` is present only when the governance
subsystem is wired.

### §14 — HTTP metrics endpoints (outside the WebSocket contract)

The Performance/Usage panels also read three **Bearer-authed HTTP GET**
routes registered in `WebServer._setup_routes` — these are REST endpoints,
not WebSocket messages, though they share the type-tagged envelope. They are
listed here so the surface is complete:

| Route | `type` | Payload keys |
|---|---|---|
| `GET /api/metrics/performance` | `metrics_performance` | `latency`, `counts`, `tokens`, `cache`, `model_perf` |
| `GET /api/metrics/usage` | `metrics_usage` | `requests`, `escalations`, `llm_calls`, `query_types`, `tool_counts` |
| `GET /api/metrics/realtime` | `metrics_realtime` | `connections`, `server_uptime`, `governance`, plus `MetricsCollector.get_status()` fields |

All three return `503` until the engine is ready and require the same Bearer
token as the other HTTP endpoints. The WebSocket `metrics_report` (§13) and
these routes draw from the same `MetricsCollector`; the WebSocket path is the
one the live dashboard subscribes to.
