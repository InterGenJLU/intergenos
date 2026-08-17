<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2015-2016, 2026 InterGenJLU -->

# InterGen Sentinel — Architecture

**Status:** Shipped (2026-05-30).
**Scope:** the security layer of the InterGen AI assistant — content scanning, an
all-MCP-interaction chokepoint, consent-first frontier escalation ("Phone-A-Friend
(Frontier/Cloud Escalation)"), and the OpenPGP-signed destructive-policy never-list.

This document describes how Sentinel is built. For the user-facing "what it does and
how to configure it" view, see [`docs/users/intergen.md`](../users/intergen.md) and the
component overview in [`docs/components/intergen.md`](../components/intergen.md).

---

## 1. What Sentinel is

Sentinel is the consolidation of a security-aligned design philosophy: InterGen must
defend the user *and the machine* whenever the local AI assistant touches a trust
boundary — an MCP server, a web fetch, a file write, or an outbound request to a frontier
model. It has four cooperating parts:

1. **A pluggable, vendor-neutral scanner engine** (`intergen/scanner/`) that inspects
   content crossing a trust boundary and returns ALLOW / FLAG / BLOCK.
2. **A single dispatch chokepoint** (`ToolRegistry.execute`) through which *every* tool
   and MCP interaction passes, so scanning is structural rather than per-call opt-in.
3. **Consent-first "Phone-A-Friend"** (`intergen/escalation.py`) — the assistant may
   *offer* to reach the user's configured frontier model, and sends nothing without
   explicit consent.
4. **The destructive-policy never-list** (`intergen/destructive_policy.py`) — an
   OpenPGP-signed manifest of paths the AI may never destroy, enforced at the same
   chokepoint.

### Design principles

- Sentinel assumes adversaries with superhuman vulnerability-discovery capability.
  Ambiguity defaults toward deny: dispositions are severity-ordered
  (`ALLOW < FLAG < BLOCK`) and **most-severe-wins**; any scanner that errors **fails
  CLOSED to FLAG**, never to a silent ALLOW.
- **Vendor-neutral.** No provider is privileged. The cloud substrate (`intergen/cloud/`)
  speaks raw HTTPS to any of six built-in providers or a user-defined custom endpoint.
- **NO-PYPI.** The cloud adapters are hand-written `aiohttp`/raw-HTTP — no vendor SDKs
  (SLSA-L3 supply-chain posture; the NO-PYPI ban is project-canonical).
- **Composition, not bolt-on.** The scanner slots into the existing
  `ToolRegistry.execute` chokepoint *alongside* the provenance gate and the
  spotlight — the trust-boundary defenses layer, they do not replace one another.

---

## 2. Part 1 — the scanner engine

### Core interface (`intergen/interfaces/scanner.py`)

A `Scanner` inspects **one piece of content travelling one direction** across a trust
boundary and returns a verdict.

- **`ScanDirection`** — `EGRESS` (arguments leaving the machine toward an external/MCP
  surface; exfiltration risk) or `INGRESS` (content arriving before it re-enters the LLM
  context; injection risk).
- **`ScanDisposition`** — `ALLOW` / `FLAG` / `BLOCK`, severity-ordered. `FLAG` means
  "suspicious → hold for a human review modal"; `BLOCK` means "high-confidence malicious
  → hard refuse / withhold." `most_severe(a, b)` merges multiple verdicts default-deny.
- **`ScanContext`** — metadata carried alongside the content: `surface`
  (`"mcp:<server>/<tool>"` | `"web_search"` | `"file:<path>"`), `direction`,
  `tool_name`, and an optional `trust_tier` (a provenance/MCP-trust label the chokepoint
  supplies when it has one — the engine does not require it).
- **`ScanVerdict`** — `disposition`, `reason`, `score` (0..1 confidence), `scanner`
  (which scanner produced it), and `categories`.
- **`Scanner` (ABC)** — `name`, `is_local` (true if the scan runs fully on-device, no
  network), and `scan(content, ctx) -> ScanVerdict`.

### The scan chain (`ScannerPolicy`, `intergen/scanner/policy.py`)

`ScannerPolicy` composes an always-on deterministic floor with an optional deeper
scanner. The composition rule:

1. **`LocalRulesScanner` always runs first** (the deterministic floor).
2. A **`BLOCK` from the floor short-circuits** — refuse without spending the deeper scan.
3. **`ALLOW` at `depth=baseline` passes through.**
4. **`FLAG`, or any scan at `depth=deep`, escalates** to the configured deeper scanner.
5. **Most-severe disposition wins.** A scanner that raises **fails CLOSED to `FLAG`**
   (we could not confirm the content safe → hold for a human), logged, never a silent
   ALLOW.

`ScanDepth` is `BASELINE` (floor only; escalate on FLAG) or `DEEP` (always escalate).
The default depth is config-set so the chokepoint's 2-arg `scan(content, ctx)` honours
the configured posture. The deeper scanner is optional and attaches at runtime via
`set_deep_scanner()` — until one is configured, a `FLAG` from the floor stands and is
handed to the human modal at the chokepoint, which is the correct safe behaviour.

### The three scanners

| Scanner | `is_local` | Role |
|---|---|---|
| `LocalRulesScanner` (`local_rules.py`) | yes | Always-on deterministic floor — pattern/heuristic rules, no model, no network. The minimum guarantee on every interaction. |
| `LocalQwenScanner` (`local_qwen.py`) | yes | On-device llama.cpp Qwen classifier with on-demand keep-alive. The default deeper scanner; richer judgement than the rules floor, still no network. |
| `CloudScanner` (`cloud_scanner.py`) | no | Opt-in deep tier — wraps a vendor-neutral cloud adapter (`intergen/cloud/`) for the strongest scan. Off unless the user opts in. |

---

## 3. Part 2 — the all-interaction chokepoint

Every tool call and MCP interaction is dispatched through **`ToolRegistry.execute`**
(`intergen/tool_registry.py`). Sentinel wires the `ScannerPolicy` in there so scanning
is structural:

- `ToolRegistry.__init__(scanner_policy=...)` holds the policy.
- **Egress scan** before arguments leave toward the surface (`ScanDirection.EGRESS`).
- **Ingress scan** of returned content before it re-enters the LLM context
  (`ScanDirection.INGRESS`).
- A `BLOCK` refuses/withholds; a `FLAG` is raised to a human review modal; `ALLOW`
  proceeds.

The scan composes with the existing provenance gate and the spotlight at the
same chokepoint — three layered defenses on the one path everything must traverse.

---

## 4. Part 3 — Phone-A-Friend (consent-first escalation)

`EscalationManager` (`intergen/escalation.py`) implements the Phone-A-Friend
(Frontier/Cloud Escalation) feature. It is distinct from the quality FALLBACK in `llm.py`
(which auto-escalates after the local model fails twice): Phone-A-Friend is **consent-first
assistance** — the assistant recognises a task is multi-step / sensitive / "slightly
outside my scope" and **offers** to reach the user's configured frontier model; on
consent it routes via the vendor-neutral cloud substrate.

- **Modes** (`EscalationMode`, config `escalation.mode`, default **ASK**): `NEVER`
  (offline; never offer or send) · `FALLBACK` (auto-escalate only on local quality-gate
  failure) · `ASK` (offer on recognition; user consents before anything is sent) ·
  `AUTO` (decide by confidence, no prompt).
- **Hybrid recognition:** `should_escalate()` is the heuristic half (local
  confidence + multi-step signal + query type + an explicit "ask <FRONTIER AI>"); a
  user-invoked affordance — the GUI "Ask my frontier model" button with CLI parity —
  bypasses the heuristic and calls `escalate()` directly (the user already asked).
- **Show-before-send.** The consent modal (`intergen/consent_modal.py`) displays the
  FULL outbound payload (scrollable) before any send, so consent is informed.
- **Egress safety — scan-on-derivation:** the *initial* egress the user
  explicitly authorized (the consented offer) is trusted-at-source and not auto-scanned;
  **every subsequent egress** in the flow (derived / agentic / not individually
  consented) **is egress-scanned through the same `ScannerPolicy`** as the tool
  chokepoint — a `BLOCK` refuses the send, so secrets are never shipped to the cloud.
- **No default provider.** With none configured, escalation cannot run (offers degrade
  to a "configure a provider" note); InterGen ships local-only and ready.

---

## 5. The destructive-policy never-list

The OpenPGP-signed manifest `intergen/data/destructive-policy-manifest.json`
(installed to `/usr/share/intergen/`) enumerates the paths InterGen's
AI may **never** perform a destructive operation on — **no config option, ever**
(anti-self-tamper + system survival + credential/boot integrity). On an installed system
that file is an ordinary root-owned file on the root filesystem, and its integrity rests
on two things: its detached OpenPGP signature, verified against the pinned operator
fingerprint before the manifest is trusted, and root ownership, which keeps the
unprivileged assistant session from writing it. dm-verity seals the live ISO and install
media; the installed root filesystem is not a dm-verity device.
Everything *not* on the list is fair game under per-capability opt-in + per-action
human consent. The manifest's `system_ai` category also covers `~/.config/intergen/` and
`/var/lib/intergen/`, so enforcing it delivers the Sentinel AI-IMMUTABLE
config protection (sentinel / escalation / providers) in the same pass.

Three composing pieces:

1. **The pure matcher** (`intergen/destructive_policy.py`) — given an already-loaded
   manifest dict, decides whether a candidate path is protected. No I/O, no signature
   logic.
2. **The signature-verifying loader** — verifies the detached `.asc` against the
   operator key over the exact bytes read (closing a verify-then-parse TOCTOU) before
   trusting the JSON.
3. **The dispatch-chokepoint enforcement** — consults the matcher inside
   `write_file._classify_path` (matcher PRIMARY, with a canonicalized
   `AI_IMMUTABLE_PREFIXES` interim floor as defense-in-depth).

**Trust anchor.** The manifest is trusted ONLY when its detached signature verifies to
the operator's OpenPGP primary-key fingerprint
`OPERATOR_FINGERPRINT = 5597A3E0587B253006D0DD7B8C50826182083050`
(`intergen/destructive_policy.py`). A manifest that does not verify to this key is not
trusted — the loader fails closed to the interim floor.

**Match semantics** (from the manifest's `match_rules`):

- `expand_user` — a leading `~` in a candidate or manifest pattern expands.
- `resolve_symlinks` — the candidate is fully resolved (symlinks + `..` collapsed)
  **before** matching. This is the security-critical step: a symlink/`..` detour cannot
  smuggle a write past a prefix entry.
- `prefix` — `candidate == prefix.rstrip('/')` or `candidate.startswith(prefix)`;
  manifest prefixes keep their trailing slash so `/boot/` matches `/boot` and
  `/boot/grub` but not `/booty`.
- `exact` / `glob` — string equality / `fnmatch` against the resolved candidate.
- `default_on_ambiguity = block` — a candidate that cannot be normalized (resolve
  raised) is treated as PROTECTED (fail closed), never waved through.

A `ProtectedMatch` (category, rule, pattern, candidate) is surfaced in both the refusal
and the audit record.

---

## 6. The vendor-neutral cloud substrate

`intergen/cloud/` is the raw-HTTP substrate the CloudScanner and Phone-A-Friend both ride
on. It is vendor-neutral and SDK-free:

- **Six built-in providers** — `anthropic`, `openai`, `google`, `microsoft`, `deepseek`,
  `xai` — plus a `custom` adapter for any OpenAI-compatible endpoint.
- `factory.create_adapter(...)` selects the adapter from `ProviderConfig`; all share the
  `http_adapter` base (raw HTTPS, no vendor SDK — NO-PYPI).
- The API key is read from the keyring per-call (never cached in process), and a request
  is refused over a non-TLS transport.

---

## 7. Code map

| Concern | Location |
|---|---|
| Scanner interface (ABC, types) | `intergen/interfaces/scanner.py` |
| Scanner engine (3 scanners + policy) | `intergen/scanner/` |
| Chokepoint scan-wiring | `intergen/tool_registry.py` (`ToolRegistry.execute`) |
| Phone-A-Friend escalation | `intergen/escalation.py` |
| Consent modal (show-before-send) | `intergen/consent_modal.py` |
| Cloud substrate (6 providers + custom) | `intergen/cloud/` |
| Destructive-policy matcher + loader | `intergen/destructive_policy.py` |
| Signed never-list manifest | `intergen/data/destructive-policy-manifest.json` (+ `.asc`) |

---

## 8. Build & verification status

The full Sentinel + Phone-A-Friend design shipped on 2026-05-30: scanner engine, cloud
substrate, the LocalQwen and Cloud scanners, scan-policy configuration, chokepoint
scan-wiring, Phone-A-Friend core plus its UI and CLI wiring, the destructive-policy
matcher and signature-verifying loader, chokepoint enforcement, signed-manifest install,
and llama.cpp embeddings. The destructive-policy never-list has been verified
end-to-end: it loads, verifies against the pinned operator key, and blocks protected
paths in practice.
