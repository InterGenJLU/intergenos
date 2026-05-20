# Firstboot Architecture Rewrite — Chain vs Phase Sub-Decision Matrix

**Status:** OPERATOR-RATIFIED 2026-05-20 ~19:35Z-20:00Z. All 6 sub-decisions resolved as **Option A** in chat walkthrough. Implementation may proceed under the constraints + quality gates captured in this matrix and in the `project_first_login_animation_flow` PRIMARY-SOURCE memory.

**Author:** InterGenOS-coordinator — drafted as research-only matrix 2026-05-20 ~19:15Z; promoted to canonical research location after operator ratification.

**Scope:** the InterGenOS-coordinator's research-only matrix served as the decision frame for the build-system coordinator's operator chat walkthrough of the 6 sub-decisions named in the firstboot architecture rewrite Tier-2 substantial dispatch.

---

## Operator-ratified verdicts (added 2026-05-20 post-walkthrough)

All 6 open questions at the end of this matrix resolved as **Option A** per operator chat walkthrough 2026-05-20 ~19:35Z-20:00Z:

| # | Question | Verdict |
|---|---|---|
| Q1 | Chain vs Phase | **A — CHAIN** (two autostart files; welcomer untouched) |
| Q2 | Sequencing mechanism | **A — `X-GNOME-Autostart-Phase=Initialization`** + filename-sort belt-and-suspenders |
| Q3 | GTK4 binding language | **A — Python** (smoothness QA hard gate applies) |
| Q4 | Asset reuse from C | **A — port the math** (back-to-C fallback if testing fails) |
| Q5 | Animation duration | **A — preserve 7-sweep** (design LOCKED; mechanism-only rewrite) |
| Q6 | Existing C disposition | **A — KEEP everything** (additive rewrite; deletion is post-testing cleanup) |

**Smoothness quality bar (operator-verbatim Q3+Q4):** *"I spent a WEEK getting rid of tearing/odd glyphs/timing/etc - and it was FINALLY smooth as glass. It needs to STAY that way if it's moving to Python"* + *"if testing doesn't pan out- the entire thing is going back to C."*

**Design boundary (operator-verbatim Q5):** *"We're NOT changing this. Don't ask me another question that indicates you want to change anything about it."* — sweep count, timing, sweep rate, visual character, text content, font choice, fade-easing curves are LOCKED. Rewrite scope is mechanism (DRM/KMS → GTK4/Wayland) only.

See `project_first_login_animation_flow` memory for the full operator-verbatim quotes + the SMOOTHNESS QUALITY BAR section.

---

## Context (current in-tree state)

**Two separate first-boot artifacts exist that are easily confused:**

1. **`intergenos-first-boot-greeter.service`** (password greeter at tty1) — defined at `installer/data/intergenos-first-boot-greeter.service`; hardened at `bb43a724` for F-049 closure 2026-05-20. Runs at `multi-user.target` BEFORE GDM. Prompts root + user passwords via `/usr/libexec/intergenos/first-boot-greeter` bash script. **NOT IN SCOPE of this rewrite.** Documented at `docs/first-boot-greeter.md`.

2. **`intergen-firstboot.service`** (DRM boot animation) — defined at `assets/intergen-firstboot-drm/intergen-firstboot.service`. Runs at `multi-user.target` BEFORE GDM. Claims tty1 (`Conflicts=getty@tty1.service`). Renders ECG pulse animation via DRM/KMS dumb buffers + libfreetype text. C source at `assets/intergen-firstboot-drm/firstboot-drm.c` (404 lines). **THIS IS THE REWRITE TARGET.**

3. **`intergen-welcome.desktop`** (welcome wizard XDG autostart) — defined at `packages/desktop/intergen-welcome/build.sh` (1000-line GTK4/libadwaita Python at `assets/intergen-welcome/intergen-welcome.py`). Installs system-wide XDG autostart at `/etc/xdg/autostart/intergen-welcome.desktop`. Per-user done-marker at `~/.config/intergen-welcome/done`. 7-page wizard: Welcome / Appearance / Extensions / Keyboard Shortcuts / Meet InterGen / Community / All Set. **EXISTING; the rewrite must sequence with this.**

**SPOC dispatch sub-decision verbatim:** "REWRITE binary as GTK4-Wayland fullscreen window + SEQUENCE with intergen-welcome (chain-vs-phase sub-decision)".

**Current sequence (pre-rewrite):**
- `multi-user.target` → `intergen-firstboot.service` (DRM animation at tty1) → exits
- → `intergenos-first-boot-greeter.service` (password greeter at tty1) → exits → flag written
- → `getty@tty1` OR `graphical.target` → GDM Wayland login
- → User logs in → `intergen-welcome.desktop` autostart fires → GTK4 welcome wizard

**Target sequence (post-rewrite):**
- `multi-user.target` → `intergenos-first-boot-greeter.service` (password greeter at tty1; unchanged) → exits → flag written
- → `graphical.target` → GDM Wayland login
- → User logs in → **NEW firstboot animation (GTK4-Wayland fullscreen)** → exits
- → `intergen-welcome.desktop` autostart fires → GTK4 welcome wizard

---

## Decision: Chain vs Phase

### Option A — CHAIN (separate XDG autostart .desktop files, each done-marker-gated)

**Shape:**
- New `/etc/xdg/autostart/intergen-firstboot.desktop` autostart entry runs `intergen-firstboot` binary (the GTK4-Wayland rewrite)
- Existing `/etc/xdg/autostart/intergen-welcome.desktop` autostart entry runs `intergen-welcome` wrapper (the existing wizard)
- Both fire on GDM Wayland login; sequencing via one of:
  - **Sort-by-name** (filename-alphabetical): `intergen-firstboot.desktop` sorts BEFORE `intergen-welcome.desktop` — relies on lexical ordering
  - **`X-GNOME-Autostart-Phase`** field: `intergen-firstboot.desktop` declares `X-GNOME-Autostart-Phase=Initialization` (fires earlier) while `intergen-welcome.desktop` stays at default Applications phase
  - **`X-GNOME-Autostart-After`** field: `intergen-welcome.desktop` declares `X-GNOME-Autostart-After=intergen-firstboot.desktop` — explicit ordering
- Per-user done-markers gate each independently:
  - `~/.config/intergen-firstboot/done` (NEW)
  - `~/.config/intergen-welcome/done` (EXISTING)

**Process model:** TWO processes, sequential (first exits, second starts). Each is a separate codebase.

**Failure isolation:** If animation crashes, welcomer still fires (autostart entries are independent). If welcomer crashes, animation already completed.

**Code shape:**
- Animation binary = minimal GTK4-Wayland fullscreen GtkApplication with a GtkDrawingArea or GtkSnapshot for the ECG pulse + Pango for text. Single-purpose canvas. ~100-200 lines C or ~50-100 lines Python.
- Welcomer = existing 1000-line GTK4/libadwaita Python wizard, unchanged.

### Option B — PHASE (single application orchestrating animation then welcomer)

**Shape:**
- One `/etc/xdg/autostart/intergen-firstboot.desktop` autostart entry (which replaces `intergen-welcome.desktop` OR runs in parallel with it where welcomer skips on its own done-marker)
- Single binary that runs:
  - Phase 1: animation (GTK4-Wayland fullscreen GtkDrawingArea/GtkSnapshot)
  - Phase 2: welcomer (GTK4-Wayland normal-window GtkBox-of-pages or AdwLeaflet/AdwNavigationSplitView)
- Single per-user done-marker: `~/.config/intergen-firstboot-welcome/done` (NEW; replaces both prior markers)

**Process model:** ONE process, internal phase transition. Single codebase.

**Failure isolation:** If animation crashes mid-phase-1, welcomer phase doesn't fire. If welcomer crashes mid-phase-2, animation already completed.

**Code shape:**
- Either rewrite the existing 1000-line welcomer to be Phase 2 of a single app (adds animation Phase 1 prefix)
- Or wrap the existing welcomer's `WelcomeApp.run()` inside a parent app that does animation first then `present()`s the welcomer windows
- Either way: SINGLE codebase with two visually-very-different rendering modes (canvas-fullscreen vs forms-windowed)

---

## Trade-off matrix

| Criterion | Option A (Chain) | Option B (Phase) |
|---|---|---|
| **Code separation** | Strong — animation has its own minimal codebase; welcomer unchanged | Weak — single codebase mixes canvas-fullscreen + forms-windowed render modes |
| **Sequence reliability** | Depends on autostart ordering mechanism — Sort-by-name is fragile; `X-GNOME-Autostart-After` is explicit but newer | Inherently reliable — single process serializes its own phases |
| **Race-condition risk** | EXISTS if naive sort-by-name fails — both windows could open simultaneously and stack on the same display | NONE — single process owns the single window |
| **Failure isolation** | Higher — animation crash does not block welcomer | Lower — animation crash blocks welcomer |
| **Welcomer changes required** | NONE — existing `intergen-welcome` package unchanged | MAJOR — welcomer becomes Phase 2 of a parent app; pages refactor; done-marker rename |
| **Per-component testability** | HIGH — animation and welcomer testable independently | LOW — must drive the parent app to reach welcomer pages |
| **Per-user done-marker complexity** | 2 markers; clean separation | 1 marker; but migration from existing `intergen-welcome/done` requires care for existing-user upgrade scenarios |
| **GTK4 binding language** | Different per component (animation could be C, Python, Rust; welcomer is Python) | Same — both phases in one binding (must be the welcomer's binding, i.e. Python) |
| **Alignment with current welcomer architecture** | HIGH — welcomer keeps its 1000-line Python untouched | LOW — welcomer must absorb the animation phase |
| **D-009 item 5 disguise-pattern risk** | LOW — two-features-two-units is honestly two | MEDIUM — single-binary-two-phases can grow into hidden coupling if not strictly compartmentalized |
| **Operator review surface** | Each component reviewable independently | Reviewer must hold both phases in mind simultaneously |
| **Existing user upgrade story** | Both done-markers checked independently; new firstboot fires for users without `intergen-firstboot/done` marker | Done-marker migration: existing `intergen-welcome/done` users must be treated as having ALSO completed the new firstboot phase (since they passed through the equivalent stage); migration script or in-app check required |

---

## Recommended sequencing mechanism for Option A (if chosen)

If operator chooses Option A (Chain), the cleanest sequencing approach is:

- `intergen-firstboot.desktop` declares `X-GNOME-Autostart-Phase=Initialization` (fires as early as possible after session start, BEFORE Applications phase)
- `intergen-welcome.desktop` stays at default (Applications phase; fires AFTER Initialization completes)
- Filename-sort fallback also works in our favor: `intergen-firstboot.desktop` < `intergen-welcome.desktop` lexically

This avoids the race-condition risk by leveraging GNOME's autostart phase ordering, which is explicit and documented.

For belt-and-suspenders the new `intergen-firstboot` binary should `gtk_window_present_with_time` with a current-monotonic timestamp before doing any drawing, then `gtk_main_iteration` until the window is mapped — guarantees the fullscreen is visible before pulse rendering starts.

---

## Recommendation (for operator decision)

**OPTION A (CHAIN) recommended** for these reasons:

1. **Honest decomposition.** Two visually-distinct features (single-shot canvas animation + multi-page form wizard) are honestly two features. Forcing them into one binary respects the dispatch text but creates hidden coupling that bites the next refactor.

2. **Welcomer is already production.** The 1000-line `intergen-welcome.py` is a tested, working GTK4/libadwaita app at packages/desktop/intergen-welcome. Phase B requires rewriting it to be a phase of a parent app — substantial work for no security gain.

3. **D-009 item 5 disguise-pattern enumeration** treats "single binary with two phases that LOOK like two features" as a risk class to be eliminated, not embraced.

4. **Failure isolation is a security posture** (Holy Grail rules 4 + 9 + 10) — if the animation crashes (uncommon but possible on weird GPUs / Wayland compositor edge cases), the welcomer MUST still fire so the user reaches a usable system.

5. **Per-component testability** — animation can be smoke-tested via `intergen-firstboot --once` against a Wayland session without depending on welcomer code; welcomer can be smoke-tested independently as it is today.

**Operator override paths considered:**

- If operator prefers Option B for UX continuity (smooth visual transition from animation to first welcomer page without window-close + window-open flicker), the migration done-marker concern is solvable but requires care.
- If operator prefers a hybrid (e.g. animation in a separate XDG autostart but welcomer extended to absorb the "Hello." / "Shall we get started?" text-transition phase from the existing DRM binary as its own first page), that splits the difference and is worth surfacing for review.

---

## Original open questions (RESOLVED-AT-WALKTHROUGH 2026-05-20)

The 6 questions below are the original scope-prep open questions that the operator-walkthrough on 2026-05-20 ~19:35Z-20:00Z resolved. See [Operator-ratified verdicts](#operator-ratified-verdicts-added-2026-05-20-post-walkthrough) table at top of document for the ratified outcomes. Preserved here as scope-prep historical-context for future readers tracing the decision provenance.

1. **Chain vs Phase verdict?** Default recommendation = Chain (Option A); operator override paths enumerated above.
2. **Sequencing mechanism?** If Chain: `X-GNOME-Autostart-Phase=Initialization` + filename-sort belt-and-suspenders (recommended) OR `X-GNOME-Autostart-After` (newer; explicit but less broadly supported)?
3. **GTK4 binding language for animation binary?** Existing welcomer is Python; animation could be Python (consistency) OR C (closer to current DRM binary; smaller runtime footprint; needs gtk4-c bindings or pkg-config gtk4)?
4. **Asset reuse from current C/DRM binary?** Current `pulse.c` and `text.c` are shared between `intergen-firstboot/` and `intergen-firstboot-drm/`. The new GTK4-Wayland binary can either port the math directly (rewrite the render to cairo or GtkSnapshot) or call into the existing pulse-math via cgo/cffi/ctypes. Recommend port-the-math (lifecycle cleaner; ~50 lines of cairo).
5. **Animation duration?** Current DRM binary defaults to "7-sweep sequence then exit" (per firstboot-drm.c usage comment). Preserve duration or shorten for GTK4-Wayland session-start context where ECG pulse < welcomer-first-page-visible is the goal?
6. **Existing intergen-firstboot binary disposition?** SPOC dispatch says DELETE `assets/intergen-firstboot-drm/intergen-firstboot.service`. Should the C source at `assets/intergen-firstboot-drm/firstboot-drm.c` ALSO be deleted, or kept for archival/reference value? The non-drm `assets/intergen-firstboot/firstboot.c` (SDL2 prototype, 489 lines) — same question.

---

## Cross-references

- Dispatch context: SPOC T0-7 bus directive 2026-05-20T18:29:14Z
- Primary-source greenlight: `[[project_first_login_animation_flow]]` memory
- Review discipline: `[[feedback_primary_source_review_before_save]]` memory
- Existing welcomer architecture: `packages/desktop/intergen-welcome/build.sh` + `assets/intergen-welcome/intergen-welcome.py`
- Current DRM animation: `assets/intergen-firstboot-drm/firstboot-drm.c` + `intergen-firstboot.service`
- Password greeter (NOT IN SCOPE — different artifact): `docs/first-boot-greeter.md` + `installer/data/intergenos-first-boot-greeter.service` (hardened at `bb43a724`)
- Owner directives: D-007 (minimal-trust SSH posture; relevant for understanding the overall first-boot security model) + D-009 (universal development checklist; especially item 5 disguise-pattern enumeration)

---

**Next step:** matrix is OPERATOR-RATIFIED + closed as a research artifact; the Python rewrite work proceeds per operator-decision-queue verdicts on (a) implementation ownership (InterGenOS-coordinator Option 1 recommended at bus 19:57:36Z), (b) test-plan-first sequencing per the smoothness QA hard gate, and (c) hardware-testing protocols on the reference DRM hardware before the Python binary supersedes the existing C/DRM implementation. The rewrite implementation IS subject to D-009 8-item checklist + peer-review-primary on the SHA when it lands.
