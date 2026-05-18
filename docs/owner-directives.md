# Owner Directives — append-only log

This file is the canonical record of explicit directives issued by the project owner. Every entry is **append-only**. Entries are never edited, reordered, or deleted — superseding directives are added as NEW entries that reference the prior entry.

## Protocol

The owner issues a directive by prefixing a message with `OWNER DIRECTIVE:`.

When any coordinator (build-system, installed-system, Windows-host, or any future fleet member) sees that prefix in owner input or on the coordination bus, they MUST:

1. **Acknowledge immediately** in the same thread or message.
2. **Append the verbatim directive text** + UTC timestamp + the originating thread/context to this file as a new numbered entry.
3. **Cite this file as source-of-truth** in any future synthesis, matrix row, tracker entry, audit pass, design doc, or commit message that touches the directive's subject.
4. **Update any conflicting prior records** by adding a `SUPERSEDED-BY` annotation pointing to the directive entry. Do not silently rewrite prior records.

The recording in this file is the load-bearing artifact. A coordinator that fails to record breaks the trust contract. A coordinator that records but then writes contradicting "deferred" language elsewhere breaks the trust contract.

## What counts as a directive (vs a discussion)

The `OWNER DIRECTIVE:` prefix is the only signal. Without it, owner messages are interpreted in their conversational context (questions, requests, suggestions, authorizations). With it, the message is a binding ratification — to be recorded, never re-litigated.

## What coordinators MUST NOT do

- Write "DEFERRED", "post-v1.0", "v1.x", "out of scope for v1.0", "Phase 2", or equivalent scheduling language in any tracker, design doc, matrix row, research note, or commit message WITHOUT citing a specific entry in this file as the authorizing directive. If no such entry exists, frame as `PROPOSED-DEFERRAL — awaiting operator confirmation` and surface for input.
- Edit, reorder, or delete entries below. Supersession is an additive operation.
- Add entries on the owner's behalf without their explicit `OWNER DIRECTIVE:` prefix in the originating message.
- Treat coordinator-side "we'll get to it later" or "out of cycle scope" as equivalent to an owner ratification of deferral. They are not. They are operating notes; this file is owner state.

## Format

Each entry uses this shape:

```
## D-NNN — <one-line summary>

- **Issued:** <ISO 8601 UTC timestamp> by owner
- **Context:** <thread / conversation reference where the directive was given>
- **Verbatim:**

  > <verbatim text following the OWNER DIRECTIVE: prefix>

- **Supersedes:** <list of prior records this overrides — file paths + line refs, or "none">
- **Status:** ACTIVE (default) | SUPERSEDED-BY D-NNN
```

`D-NNN` numbering is monotonic. First directive is `D-001`. Numbers are assigned at append time and never reused.

## Entries

_(awaiting first entry — protocol scaffold pushed 2026-05-18 ~13:50 UTC; first owner directive will land here.)_
