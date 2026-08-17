# Branded consent dialog — design proposal

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Status:** DESIGN APPROVED (2026-06-28) · SECURITY RED-TEAM DONE (2026-06-28, conditional) · IMPLEMENTED — the branded GTK dialog is the primary review surface (`intergen/consent_dialog.py`, routed from `intergen/review_modal.py`); the zenity fallback feeds its body over stdin and carries the §7.2 one-hour unanswered-dialog bound (2026-07-19)
**Owner:** design + visual language · **Security review:** internal red-team · **Scope:** InterGen consent surfaces
**Blocks:** the InternVL-03 ISO build (FACE bar — the current dialog "reads generic")
**Mockup:** [`mockup-v2.png`](mockup-v2.png) (rendered from [`mockup.html`](mockup.html) with the real `intergen/web/style.css` tokens + the ECG mark)

---

## 1. Problem

InterGen has two desktop consent surfaces, both rendered today by **zenity**:

- **Tool-call review gate** — `intergen/review_modal.py` (Allow once / Allow this conversation / Deny)
- **Phone-a-friend egress consent** — `intergen/consent_modal.py` (show-before-send payload; Send / Cancel)

zenity is a stock system-dialog generator: it accepts text + button labels and returns the OS default chrome. It **cannot** carry a brand mark, the InterGen palette, a provenance badge, the ✓/✗ governance rows, or color-semantic buttons. So these two surfaces — the *only* ones a user sees when InterGen asks permission — read as generic system dialogs and fall below the FACE bar.

Meanwhile the InterGen **web UI** (`intergen/web/`, now on master/dev) already has a beautifully designed gate modal (`app.js` `buildGateCard`, `style.css` `.gate-btn`), and the **GTK4 panel** (`intergen/panel/`) is a WebKitGTK window rendering that same web UI. The desktop consent path never received that design — purely because of its rendering technology (zenity), not intent.

## 2. Decision (visual)

Replace the zenity render with a small **standalone, branded GTK4 dialog binary** that the daemon shells out to in place of zenity. It reproduces the web gate-modal's design language as **native GTK4 + GTK CSS**, sharing the web UI's design tokens (`--accent #0099FF`, `--success`, `--destructive`, `--card`, `--void`, radii, Inter / JetBrains Mono) so the desktop dialogs read as one family with the panel and dashboard. The approved look is `mockup-v2.png`:

- GTK4 window with a branded **headerbar**: the glowing ECG **InterGen mark** + the **Inter**_Gen_ wordmark + a mode label (Review / Confirm send).
- **Review gate:** provenance badge (`user direct` etc.), ✓/✗ governance-check rows (why it was held), three color-semantic buttons (Allow once = success outline, Allow this conversation = accent fill, Not now = destructive outline).
- **Egress consent:** destination pill (`↗ DeepSeek · api.deepseek.com`), a read-only "Exactly what will be sent" payload view, an amber secret-in-payload warning, Cancel / Send.

## 3. Why native GTK4, NOT WebKitGTK (the security-only lens)

The panel renders the web UI through a WebKit engine. We deliberately do **not** reuse that engine for the consent surfaces. The consent dialog is the human-authorized **egress seam**: per `consent_modal.py`'s own design note, that hop is *not* egress-scanned, so its entire safety rests on the human truly seeing the exact payload — which contains conversation content that can be **ingress-derived (attacker-influenced)**. Rendering partially-adversary-controlled content through a full JS-capable browser engine, in the one dialog whose integrity is load-bearing for egress safety, is the wrong trade. We keep the payload **inert plain text** (the way zenity `--text-info` shows it today) and reproduce the *look* in native widgets. The panel/dashboard stay WebKit — they are not the egress decision point.

## 4. Security invariants — MUST NOT MOVE (red-team-affirmed + extended)

The visual change is a render swap only. Invariants 1–7 stay exactly as proven on `.218`; 8–11 were
**added by the security red-team** (2026-06-28) — two of them (8 visual-integrity, 10 env-scrub) are gaps
this proposal originally missed, and under the security-only lens they outrank the cohesion gain:

1. **zenity is KEPT as the fallback** — it is NOT removed. The GTK4 binary becomes the *primary* render path; if it is missing or fails, the path falls back to zenity, then to the libnotify dead-end, exactly as today. (The prior rogue session removed every zenity call — that is explicitly forbidden.)
2. **`_session_active` / `_ensure_display_env` self-heal unchanged** — the proven DISPLAY/WAYLAND_DISPLAY recovery that passed the live `.218` cold-boot proof (e0a020c2) is untouched.
3. **Subprocess isolation unchanged** — the daemon shells out to a separate, short-lived process; the dialog never runs in the daemon's address space.
4. **Inert payload** — the outbound payload is shown as plain text in a non-interactive view; no markup/markdown/HTML rendering, no mnemonic/markup interpretation of payload content.
5. **Fail-closed** — Esc, window close, binary-missing, crash, and timeout all resolve to **Deny / Cancel** (and the existing 1-hour implicit-deny for the review gate). Nothing is ever sent on ambiguity.
6. **Consent semantics unchanged** — Allow once / Allow this conversation / Deny for review; Send / Cancel for egress; the per-conversation trust state (RFC §7.2) is unchanged.
7. **Loud path-logging preserved** — the INFO/WARNING consent-path log lines (52897593 / 6fa2d693) that gave the security review its SSH-observable boot-class proof are preserved (logged *before* the blocking modal call).
8. **Visual integrity (SHOWN == SENT *visually*)** — the payload and any rendered destination string must defeat Unicode spoofing that breaks show-before-send with zero code execution: bidirectional control (right-to-left override / isolates — Trojan-Source class) that visually reorders shown vs. sent bytes, and zero-width / homoglyph / confusables that hide a secret from a human skim. Render bidi-control, zero-width, and non-printing characters **visibly** (escape/badge, e.g. `<U+202E>`) or strip-and-flag. Monospace alone does not neutralize bidi reordering.
9. **Chrome from trusted metadata only** — NO payload byte may influence any chrome element (title, provenance badge, ✓/✗ rows, button labels/semantics, and especially the **destination pill**). The destination shown MUST come from the same config value the egress code actually sends to, passed as trusted daemon metadata — never from anything the LLM/payload can influence. SHOWN == SENT applies to the DESTINATION, not just the payload.
10. **Scrubbed child environment** — subprocess isolation (#3) is pierced from the inside by env that injects code into the child: dynamic-linker preload, GTK module path, GIO module dirs. The daemon spawns the binary with a **minimal scrubbed env** — only DISPLAY / WAYLAND_DISPLAY / XAUTHORITY / XDG_RUNTIME_DIR (the self-heal set) — explicitly dropping the preload / toolkit-module / module-path family, with no-new-privs set. The binary needs display + stdin only (no network, no child processes, no filesystem writes).
11. **Affirmative-only decision + bounded wait** — the decision channel (exit code) is initialized to DENY; ALLOW is set **only** inside the explicit Send/Allow handler. Window-close, Esc, quit signal, crash, unrecognized exit → deny. Both modal calls today have **no `subprocess` timeout** — a hung first-party renderer wedges the daemon thread forever; add a generous absolute deadline → Cancel, ideally a watchdog keyed to the invariant-#7 pre-render log line (hang-before-render fails fast; window-up-human-thinking still waits).

## 5. Red-team verdict (security review, 2026-06-28) — SHAPE APPROVED, conditional

The review affirmed the architecture and all 7 original
invariants; approval is conditional on the MUSTs below (each answers an original open question).

- **A · Transport** — **stdin, with injection-proof framing.** argv carries only fixed literal flags (`mode=review|consent`); ALL variable bytes (payload *and* `call.arguments`, which can itself bear a secret) go on stdin. Temp file **rejected** (touches disk). The real risk is framing: a line-oriented `KEY=VALUE` stream lets attacker payload bytes forge a metadata field or a decision token (field-confusion) — so the payload is **one opaque, length-delimited field a real parser reads** (JSON string value or explicit length-prefix), never line-scanned.
- **B · Visual integrity** — invariant #8 above (the biggest gap).
- **C · Env-scrub** — invariant #10 above.
- **D · Chrome/destination trust** — invariant #9 above. Residual named honestly: once users trust the branded chrome as a security signal, a malicious app can draw the same chrome (zenity shares this) — render modal/focused/on-top; branding does not remove it.
- **E · Render inertness** — **`GtkTextView` (editable=false, cursor-visible=false, monospace), buffer via `set_text()`** for the payload (no Pango-markup path; handles arbitrary length). `GtkLabel` only for short FIXED chrome with `use_markup=false` + `use_underline=false`. **Never truncate the egress payload** to fit the widget → too-large = Cancel (fail-closed), never truncate-and-show. (review_modal's 400-char *excerpt* truncation is review CONTEXT, not the egress payload — that distinction stays.)
- **F · Fail-closed** — invariant #11 + the full enumeration (binary absent → zenity → libnotify deny; non-zero/crash/unknown-exit → deny; malformed/oversized/framing-attack → child rejects AND daemon validates size/framing before spawn).
- **§5.3 attack-surface** — net surface GROWS (we add the binary, keep zenity); acceptable **only if** the binary does ONE thing (framed stdin in → inert render → decision via exit code), reads no config/network/payload-derived behavior, **stdin parser is fuzzed**, pair-reviewed.
- **§5.5 secret-highlight** — aid not gate. Detector emits **offset ranges only** over the verbatim payload; shown and sent are both the ORIGINAL bytes. Highlight via **GtkTextBuffer tags on ranges, NOT Pango `<span>` markup** (markup reintroduces the surface §E bans). Detector local/deterministic/no-network; display 100% independent of detector output; absence of the amber warning must never read as "all clear" — the standing "review ALL of it" copy stays regardless.

## 5b. Grounded pre-existing findings in current code — the swap MUST close these

1. **Live info-leak (review gate):** `review_modal._prompt_review_zenity` passes the body — including the ingress **excerpt** and `call.arguments` — to zenity on **argv** (`f"--text={text}"`, review_modal.py:220), so it is readable via `/proc/<pid>/cmdline` by other local UIDs *today*. (`consent_modal` already uses stdin.) The swap moves BOTH surfaces to stdin and closes this leak — it must not be carried forward.
2. **Indefinite-hang hole:** both `_prompt_review_zenity` and `_prompt_consent_zenity` call `subprocess.run` with **no `timeout=`**. Tolerable for stock zenity; not for new first-party code an attacker can wedge — invariant #11's deadline/watchdog closes it.

## 6. Packaging / build

The new binary ships in the InterGen package (desktop tier); it depends on GTK4 (already present for the panel). It reconciles into the tree at the **InternVL-03 ISO build** (GB002 base + auto-skip-built), same as the other `.218` hand-deploys. zenity stays in the desktop tier as the fallback.

## 7. Sequence

1. ✅ Design approved (operator).
2. ✅ **Security red-team** — SHAPE APPROVED, conditional (2026-06-28); §4 invariants 8–11 + §5 MUSTs + §5b findings folded in.
3. ▶ **Implement** — standalone single-purpose GTK4 binary + swap the render call in `review_modal.py` / `consent_modal.py`; all 11 §4 invariants + the §A–F MUSTs held; §5b leaks closed. Pair-review. ← next
4. Tests: fuzz the stdin parser; fail-closed matrix; inert-render incl. bidi/zero-width spoof cases; fallback-to-zenity; env-scrub; never-truncate; path-logging.
5. **Fresh adversarial red-team pass** on the actual binary + the full fail-closed matrix before done.
6. Prove on the InternVL-03 build.
