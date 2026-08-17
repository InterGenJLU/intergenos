# InterGenOS Firstboot Animation — v7 Arc Closure

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Status:** Q4 HARD-GATE CLOSED — operator visual sign-off achieved on real hardware, end-to-end, twice within the same engagement.

**Date:** 2026-05-21

**Closure trigger:** chain-vs-phase matrix Q4 hard-gate ([chain-vs-phase-matrix.md](chain-vs-phase-matrix.md)) requires operator visual sign-off as the canonical closure trigger. That trigger was satisfied at two retest moments within the same engagement, the second of which validated the complete cinematic arc end-to-end including the welcomer payoff.

---

## Closure verdicts (verbatim, with expletives redacted)

**1) v7.3 retest — ~08:30Z host clock (2026-05-21)**

> OH MY GOD THAT'S [REDACTED] PERFECT!!!! THAT WAS AMAZING!!!! [REDACTED] THAT IS THE COOLEST THING I THINK I'VE EVER HAD THE PLEASURE OF BEING ASSOCIATED WITH!!!! I'm NOT kidding — that was truly incredible. SO perfect. I'm awestruck at the moment. You're going to have to give me a moment...

This verdict ratified Q4 closure on the JS + St.DrawingArea + Clutter.Timeline rendering stack at IGOS-workstation-deploy-time. Q5 design-locked properties (sweep count, total duration, sweep rate, ECG curve shape, text content, font choice, fade-easing) all validated visually.

**2) Full cinematic arc with welcomer reveal — ~14:Z (2026-05-21)**

> PHENOMENAL!!!! WHOLE ARC VALIDATED!!!! TRULY EPIC!!!!

This second verdict, after `intergen-welcome` was installed on the workstation for the full cinematic arc retest, validated the complete v7 firstboot animation arc end-to-end on real hardware: Bug-1 actor-binding fix + Pattern X1 LAYER 1 + LAYER 2 overview suppression + v7.3 fade-in/fade-out cinematic arc + welcomer-revealed-by-fade-out payoff — all in a single login retest.

---

## The arc — v1 through v7.3

The firstboot animation arc spans five-plus weeks of iteration. Each layer surfaced a deeper sub-layer of modern-GNOME-on-systemd integration on real hardware.

| Iter | Mechanism | Failure mode addressed by the subsequent layer |
|---|---|---|
| v1 | XDG `.desktop` autostart | Silently dropped by GNOME 49 + systemd 259 |
| v2 | systemd user unit | Bug 1 GTK init swallowed rc=0 + Bug 2 wrong target anchor |
| v3 | GTK init-failure propagation + `gnome-session-initialized.target` anchor | Partial-pass; Mutter-fullscreen + activities-overview-chrome issues |
| v4 | wlr-layer-shell OVERLAY via `gtk4-layer-shell` | OVERLAY doesn't defeat gnome-shell compositor-drawn UI |
| v5 | `ext-session-lock-v1` (`Gtk4SessionLock`) | EMPIRICALLY INVALID — Mutter does NOT implement; protocol is wlroots-only |
| v6 | gnome-shell extension (`intergen-firstboot` + `intergen-no-overview`) | First-retest CRASH on `Clutter.Timeline` actor-binding + cascading `/tmp/.X11-unix` corruption |
| v7 | Bug-1 actor-binding fix + Pattern X1 declarative (`sessionMode.hasOverview=false` + `_stateAdjustment.value=HIDDEN`) | Animation fired smooth; ~250ms residual activities-overview flash |
| v7.1 | Array reorder (`intergen-no-overview` FIRST in enabled-extensions) | Flash persisted — load-order alone insufficient against `systemBackground.loaded` async race |
| v7.2 | LAYER 2 method-replacement (`Main.overview.runStartupAnimation` + `Main.overview.show` as no-ops during startup) | Overview suppression confirmed; ~500ms residual desktop-visible moment surfaced as upstream `uiGroup-ease` (not overview-related) |
| v7.3 | Design pivot — compose with upstream `uiGroup-ease` rather than fight it; 2s fade-in (ease-out-quad) + ECG unchanged + 2s fade-out (ease-out-quad) | **OPERATOR VISUAL SIGN-OFF — Q4 hard-gate closed** |
| post-v7.3 | `intergen-welcome` installed on workstation; cinematic arc retest with welcomer-revealed-by-fade-out payoff | **OPERATOR VISUAL SIGN-OFF — full arc validated** |

Total commit chain landed on master between `91d65db0` and `87e26740`:

| SHA | Subject |
|---|---|
| `91d65db0` | `fix(intergen-firstboot): bind Clutter.Timeline to overlay actor + monkey-patch Main.overview.show during animation -- defeats v6 first-retest failure modes` |
| `69bb9635` | `fix(intergen-firstboot): remove dead Main.overview.show monkey-patch; keep Clutter.Timeline actor-binding (v7 commit 1 of 4)` |
| `eb0d97d2` | `feat(intergen-no-overview): upgrade Mechanism A reactive-hide to Pattern X1 declarative sessionMode flip (v7 commit 2 of 4)` |
| `8eef1676` | `feat(intergenos-default-settings): suppress GNOME built-in welcome dialog via welcome-dialog-last-shown-version='9999' (v7 commit 3 of 4)` |
| `ba46ddfa` | `fix(scripts/create-image.sh): remove redundant /etc/tmpfiles.d/x11.conf block shadowing upstream systemd config (v7 commit 4 of 4)` |
| `08035803` | `fix(intergenos-default-settings): reorder enabled-extensions to put intergen-no-overview FIRST (v7.1 race-fix for overview-at-startup brief-flash)` |
| `a4b4dc09` | `fix(intergen-no-overview): add LAYER 2 method-replacement (runStartupAnimation + show no-op) as race-loss safety net (v7.2 monkey-patch)` |
| `87e26740` | `feat(intergen-firstboot): add fade-in + fade-out design beats composing with upstream uiGroup-ease (v7.3 -- "device waking up on login")` |

---

## The design pivot at v7.3 — "compose with upstream"

The architectural insight that closed the arc arrived ~08:Z 2026-05-21. After v7.2 confirmed overview suppression but surfaced a residual ~500ms upstream `uiGroup-ease` desktop-visible moment, the impulse was to fight harder — monkey-patch `_startupAnimationSession`, override `_prepareStartupAnimation`, etc. Framing (verbatim):

> we may be able to use this to our advantage... creating a black screen FADE IN effect to the start of the animation (maybe 2 seconds worth of fade in)... ensuring the welcomer is set on the desktop by the time the animation is complete, so fading out reveals it... this would make our animation even MORE impactful in my opinion — to me it makes it seem like something just 'woke up' on login.

This is the canonical pattern: **don't fight upstream behavior; compose with it as a deliberate UX beat**. The brief desktop-visible moment between gnome-shell `startup-complete` and the firstboot overlay engaging was upstream-canonical GNOME `uiGroup-ease` "fade in the desktop." Fighting it would have been increasingly invasive. Composing with it turned the moment into the first beat of a "device waking up on login" cinematic arc. This is now a standing design principle for the project.

### v7.3 phase chain (87e26740)

| Phase | Duration | Easing | Effect |
|---|---|---|---|
| 0 (upstream `uiGroup-ease`) | ~500ms | upstream-canonical | Brief desktop glimpse — design beat: "device woke up" |
| 1 (NEW fade-in) | 2000ms | ease-out-quad | Overlay opacity 0 → 255 — overlay materializes over desktop |
| 2 (ECG, unchanged Q5-locked) | 22443ms | Q5 properties locked | ECG sweep + greeting text |
| 3 (NEW fade-out) | 2000ms | ease-out-quad | Overlay opacity 255 → 0 — welcomer revealed beneath |
| **Total cinematic arc** | **~26.4s** | — | end-to-end |

---

## Research contributions

A multi-vantage research process delivered Pattern X1 ratification and caught failure modes that single-vantage analysis would have missed. This is now a 2nd successful application of the model (1st was v2-v6 sub-decision walk).

- **Research worker A** — GNOME Shell 49.x upstream source dive (startup + overview lifecycle)
- **Research worker B** — Production extension monkey-patch pattern catalog (5 extensions surveyed)
- **Research worker C** — Clutter best-practices for animation (Timeline + DrawingArea + chrome)
- **4-lane peer-review** — cross-distro + canonical-citation + sessionMode + gschema audit
- **Live-runtime extraction** — GNOME 49.4 gresource extract from `libshell-17.so` (150 JS files); discovered `startInOverview` API REMOVED in 49.4; corrected v7 implementation
- **Repo-state peer-review** at 11:37:37Z — APPROVE-clean on v7 4-commit series with 6 observations
- **Deploy execution** at 11:41:17Z — 7-step deploy with sha256 verification at every step; fired T-0 `gdm` restart at operator GO
- **D3 investigation** — `/etc/tmpfiles.d/x11-unix.conf` removed; gdm-greeter DynamicUser=yes UID 60578 hypothesis surfaced + documented

The architectural decisions, design language, persistence-through-iterations, and the v7.3 design pivot were maintainer-directed throughout.

---

## The 3 held observations — DECIDED (per Item #4 Option A)

The v7 peer-review at 11:37:37Z was APPROVE-clean with 6 observations total (1 MINOR + 3 MICRO + 2 affirmative). Three of the six observations were specifically held for decision. Decided 2026-05-21 ~14:Z via a cleanup-batch walkthrough.

**Observation 4a — Mid-session toggle behavior change (MINOR)**
Pattern X1's early-exit guard (`!_startingUp` → no-op) replaces Mechanism A's mid-session `hide()` call. Same observable outcome (overview opens normally mid-session) but different mechanism. **Reviewer lean: accept-as-pattern.** Rationale: deliberate design choice; canonical dash-to-dock convention; consistent with D4 single-owner architecture; safer (less intercepted code path mid-session). **DECIDED: accept-as-pattern.**

**Observation 4b — Commit 4 (ba46ddfa) wording reconciliation (MICRO)**
Commit message body framing around "redundant block" could be parsed two ways on first scan. **Reviewer lean: leave-as-is.** Rationale: empirically reconcilable on close reading; rewording would require force-push of already-pushed history (D-009 commit hygiene). **DECIDED: leave-as-is.**

**Observation 4c — Ubuntu '100' precedent unverified (MICRO)**
v7 c3/4 commit (`8eef1676`) sets `welcome-dialog-last-shown-version='9999'` and cites Ubuntu's `'100'` precedent. The Ubuntu citation was from research notes, not verified against Ubuntu source. **Reviewer lean: accept-as-cited.** Rationale: provenance-citation for industry pattern; our `'9999'` functions regardless of Ubuntu's specific value; non-load-bearing reference. **DECIDED: accept-as-cited.**

---

## Workstation deployment notes — items #5 + #7

The IGOS workstation is NOT repo-canonical. It is the result of a first-completed-build months ago that has been manually modified over time. Two state divergences identified during the cleanup-batch walkthrough; both are workstation-specific contaminations that a future clean ISO build will resolve canonically. No workstation-side action taken.

### pkm-notifier deployment gap (Item #5)

**Repo state:** package fully defined at [packages/desktop/intergen-pkm-notifier/](../../../packages/desktop/intergen-pkm-notifier/); asset at [assets/intergen-pkm-notifier/pkm-notifier@intergenos.org/](../../../assets/intergen-pkm-notifier/pkm-notifier@intergenos.org/); default-enabled in repo-canonical [config/gsettings/91_intergenos-extensions.gschema.override](../../../config/gsettings/91_intergenos-extensions.gschema.override) (9th UUID in `enabled-extensions`).

**Workstation state:** `pkm-notifier@intergenos.org` UUID present in workstation gschema-override (90-DASH legacy prefix) `enabled-extensions` array, but extension files are NOT installed at `/usr/share/gnome-shell/extensions/pkm-notifier@intergenos.org/`. The user-level effective gsettings shows a 6-item list that does NOT include pkm-notifier — so the workstation's effective state has the UUID enabled-by-default but masked out by user override.

**Latent risk:** if anyone on this workstation runs `gsettings reset org.gnome.shell enabled-extensions`, they inherit the gschema default; gnome-shell then tries to load an extension whose files don't exist → silent extension-load-failure in journal, no crash. Severity: low.

**Resolution path:** a future clean ISO build will install `intergen-pkm-notifier` via `pkm install`; the gap will not exist on a clean build.

### intergen-welcome manual-install state (Item #7)

`intergen-welcome` was installed on the workstation on 2026-05-21 via manual file-copy of the `do_install()` steps from [packages/desktop/intergen-welcome/build.sh](../../../packages/desktop/intergen-welcome/build.sh) — to enable the full cinematic arc retest. Same install path the eventual ISO would produce (`/usr/libexec/intergen-welcome/`, `/usr/bin/intergen-welcome` wrapper, `/etc/xdg/autostart/intergen-welcome.desktop`, etc.) but pkm metadata is absent (no installation recorded in pkm's package database; no upgrade or clean-uninstall path tracked).

**Resolution path:** a future clean ISO build will install `intergen-welcome` via `pkm install`, producing the canonical state with pkm metadata; the manual-install state will be superseded.

---

## Workstation runtime state at closure

| Artifact | Path | sha256 (truncated) | Notes |
|---|---|---|---|
| intergen-firstboot extension.js (v7.3) | `/usr/share/gnome-shell/extensions/intergen-firstboot@intergenos.org/extension.js` | `5cab9320…` | matches repo source |
| intergen-no-overview extension.js (v7.2) | `/usr/share/gnome-shell/extensions/intergen-no-overview@intergenos.org/extension.js` | `eba7df0e…` | matches repo source |
| intergen-welcome | `/usr/libexec/intergen-welcome/intergen-welcome.py` | `fcce0bf4…` | matches repo source; manual-install (Item #7) |
| GNOME Shell version | — | — | 49.4 |
| Sentinel state post-retest | `~/.local/share/intergen/firstboot-animation-done` | — | WRITTEN — animation ran to completion |
| Welcomer done-marker post-retest | `~/.config/intergen-welcome/done` | — | WRITTEN — wizard ran to completion |
| `/tmp/.X11-unix` state | `/tmp/.X11-unix` | — | `drwxrwxrwt root:root 1777` (healthy at closure) |

Full sha256 anchors are preserved in the engagement session record.

---

## Outstanding standing item — `/tmp/.X11-unix` permanent fix

Per cleanup-batch walkthrough Item #2 Option D ratification, the permanent fix for the `/tmp/.X11-unix` gdm-greeter-vs-systemd-tmpfiles race is on standing-investigation status with explicit operator direction:

> the current install of InterGenOS on the laptop is NOT reflective of our current repository code base. It was created from our very first completed build- and manually installed- with numerous manual code changes done on the fly. My recommendation is that we note what we've observed to this point, and ONLY take action on it as needed, and also compare it against the second bare-metal install target once that's completed. My concern is that this condition only exists due to the manual ownership and permission changes that were done at various intervals to this point, and that we may inadvertently create our own footgun attempting to 'remedy' a potentially non-existent problem.

Investigation report durable anchor: `workstation:~/tmp/research_d3_investigation_report.md` (on the IGOS workstation).

Capture-and-compare discipline: compare against a clean reference build before acting on a divergence observed only on a long-lived manually-modified workstation.

---

## Provenance

This closure record was authored 2026-05-21 by the InterGenOS maintainers following the cleanup-batch walkthrough at the same date. The walkthrough surfaced 7 items in the canonical decision-surface format (plain-English issue + options filtered through the user-control design principle + security review + recommendation + reasoning + comparable-distro perspective); Options were decided for all 7. This doc absorbs Items #4, #5, and #7 in addition to its primary purpose (Item #6 Option C closure record).

Q4 HARD-GATE CLOSED status was recorded at the same time.

Verbatim quotes appear in this doc with expletives redacted, decided 2026-05-21 ~14:Z during the cleanup-batch walkthrough. Unredacted verbatims are preserved in the session record.
