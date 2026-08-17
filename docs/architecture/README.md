# InterGenOS Architecture Documents

This directory holds the canonical architecture design notes and RFCs for
InterGenOS subsystems. Each document is the source of truth for one
subsystem decision; the rationale and discussion live here so that
implementation files can stay focused on code.

## Index

### InterGen AI assistant

- **[intergen-provenance-gate-design.md](intergen-provenance-gate-design.md)**
  — InterGen Provenance Gate (RFC). The canonical design source for how
  InterGen tracks where a request came from. Defines the provenance taxonomy
  (`user_direct` / `user_implied` / `ingress_derived`), the dispatcher gate,
  the ingress-tool watermark, the review-modal UX, and the v1.0 versus later
  scope split. See also
  [intergen-tool-author-guide.md](intergen-tool-author-guide.md) for the
  developer-facing guide to declaring tools that compose with the gate.

- **[intergen-tool-author-guide.md](intergen-tool-author-guide.md)** —
  Developer guide for authoring InterGen tools. Covers risk-tier
  declaration, `source_of_request` handling, ingress-set membership
  criteria, spotlighting expectations, and integration with the
  injection-corpus test harness.

- **[intergen-structured-tool-returns-design.md](intergen-structured-tool-returns-design.md)**
  — The structured tool-result contract. Defines the `model_summary` return
  shape so a tool can hand the local model a compact, synthesis-ready summary
  instead of an oversized raw payload, alongside the ingress-tool rollout and
  the InterGen Sentinel trust-boundary wiring that scans and spotlights tool
  results.

- **[intergen-perceived-latency-design.md](intergen-perceived-latency-design.md)**
  — Perceived-latency architecture. Describes how InterGen manages the user's
  experience of wait time on local hardware — acknowledge instantly, reassure
  during the wait, and let the model own the final synthesis — so the
  assistant stays responsive even when a system call or model run is slow.

- **[sentinel-architecture.md](sentinel-architecture.md)** — InterGen Sentinel
  architecture. The security layer of the InterGen AI assistant: a pluggable,
  vendor-neutral content scanner, a chokepoint over every trust-boundary
  interaction, consent-first Phone-A-Friend (Frontier/Cloud Escalation), and
  the signed destructive-policy never-list.

- **[intergen-web-ui/websocket-protocol.md](intergen-web-ui/websocket-protocol.md)**
  — InterGen Web UI WebSocket protocol contract. The message contract between
  `intergen/web_server.py` and the browser/console frontends — every message
  type and payload shape, verified against the current implementation.

- **[m4-capability-grounding-data.md](m4-capability-grounding-data.md)** — M4
  capability-grounding data. The ground-truth data inputs (the introspected
  capability surface, the read-only state-question map, and a howto-corpus
  addition) that back InterGen's anti-fabrication "grounded claims" work.

### Build system and packaging

- **[reproducible-builds-design.md](reproducible-builds-design.md)** —
  Design scope for reproducible InterGenOS builds.

- **[silent-failure-detection.md](silent-failure-detection.md)** — Design
  note on detecting silent failures during the build pipeline.

- **[per-archive-sig-decision.md](per-archive-sig-decision.md)** — v1.0
  architecture decision on per-archive signatures.

- **[helper-manifest-spec-v1.md](helper-manifest-spec-v1.md)** — Helper
  package manifest specification, v1.

- **[helper-lib-abi-policy.md](helper-lib-abi-policy.md)** —
  `intergenos-helper-lib` ABI stability policy.

- **[intergen-icon-compiler-design.md](intergen-icon-compiler-design.md)** —
  InterGenOS Icon Compiler (IGIC) design. A deterministic build-time pipeline
  that compiles the icon theme from reusable primitives and per-icon recipes,
  with embedded build provenance. Pre-RC001-arc; not yet built.

## Cross-references

- **User-facing security defaults** —
  [`docs/users/security-defaults.md`](../users/security-defaults.md)
  composes the v1.0 SSH posture and the provenance gate into the
  user-facing security defaults documentation.

## Authoring conventions

- New architecture documents land as a single Markdown file in this
  directory. Add an index entry above when the document lands.
- Use the YYYY-MM-DD date prefix sparingly; prefer descriptive names that
  survive renaming as the design evolves (`reproducible-builds-design.md`,
  not `2026-04-15-build-notes.md`).
- Cross-link related documents with relative paths so the index renders
  on GitHub and `docs/` static-site builds without rewriting.
- Project directives are authoritative; architecture notes here implement
  and elaborate but do not override them.
