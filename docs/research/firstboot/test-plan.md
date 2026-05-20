# Firstboot Python Rewrite — Test Plan

**Status:** OPERATOR-RATIFIED 2026-05-20 ~20:30Z. All 5 open questions resolved via chat walkthrough. Python authoring proceeds against this plan.

**Author:** InterGenOS build-system coordinator — 2026-05-20 ~20:Z, post-walkthrough.

**Scope:** the test plan that gates the Python rewrite's closure claim per the SMOOTHNESS QUALITY BAR section in `project_first_login_animation_flow` PRIMARY-SOURCE memory + the Q3/Q4 operator-direct quality gate.

**Background:** the operator spent a week tuning the original C/DRM animation (`assets/intergen-firstboot-drm/firstboot-drm.c`) to be smooth-as-glass — eliminating tearing, odd glyphs, and timing hiccups. The Python rewrite must inherit that smoothness verbatim, or the rewrite reverts to C entirely. This test plan defines how we verify equivalence.

---

## Cross-references

- `project_first_login_animation_flow` PRIMARY-SOURCE memory (SMOOTHNESS QUALITY BAR section)
- `docs/research/firstboot/chain-vs-phase-matrix.md` (operator-ratified architectural decisions)
- `assets/intergen-firstboot-drm/firstboot-drm.c` (the C reference for smoothness comparison)
- Owner-directive D-009 universal development checklist (item 7 — completion claims gated on per-item verification + summary + peer-review)

---

## §1. Test environment

**Primary reference hardware: IGOS laptop (192.168.1.192).** This is the machine where the operator-tuned smoothness was achieved on the C/DRM version. Reference smoothness is whatever the laptop's existing C version delivers right now. VM testing is NOT a substitute — Wayland compositor behavior on real GPU differs from llvmpipe / virtio-gpu paths.
- Hardware: HP 14-dq laptop
- GPU: Intel integrated graphics (i915 driver class)
- OS: InterGenOS bare-metal
- Display server: GNOME on Wayland
- Has the C/DRM reference binary built locally for side-by-side smoothness A/B (the binary was built by the operator during the original tuning week and lives on the laptop; the C/DRM has never been wrapped in a shipping package per audit row D-001 — see `docs/audit/2026-05-18-design-decisions-matrix.md` line 730)

**Secondary AMD-GPU sanity check: DS-v2 host (192.168.1.218).**
- Hardware: HP Laptop 17-ak0xx
- GPU: AMD Radeon R5/R6/R7 Wani APU (amdgpu driver class)
- OS: Ubuntu 24.04.4 LTS (Noble Numbat)
- Display server: GNOME on Wayland (Ubuntu install)
- **Verification mechanism: manual file drop, NOT a `.deb` package.** Copy the new Python binary + the new XDG autostart `.desktop` file directly onto the host (suggested paths: `/usr/local/bin/intergen-firstboot` + `/etc/xdg/autostart/intergen-firstboot.desktop`). Logout + login via GDM. Observe smoothness on the AMD/amdgpu stack.
- Purpose: catch any GPU-driver-specific behavior that would not surface on Intel-only testing. AMD verification is a sanity check on the Wayland-stack-portability assumption — NOT an authoritative smoothness baseline (the AMD host has no C reference to A/B against).

**Reference C version:** `assets/intergen-firstboot-drm/firstboot-drm.c` as currently shipping. Kept in-tree per Q6 verdict as the fallback path. Lives on the IGOS laptop for side-by-side comparison.

**Side-by-side comparison:** Each Python iteration is tested on the IGOS laptop, visually compared against the C reference running on the same laptop. If the operator (the only authoritative judge of "smooth as glass") can A/B them and sees no visible regression, smoothness equivalence on Intel is met. Then drop the same Python files on the DS-v2 host + observe — confirms the rewrite ports across GPU vendors.

---

## §2. Pass criteria (smoothness equivalence to C reference)

The Python implementation passes if **all** of the following are met on the IGOS laptop:

1. **No visible tearing.** Operator visual inspection across 3+ animation cycles. Wayland's frame-callback protocol structurally prevents tearing at the protocol layer, so a regression here would indicate a serious implementation bug.
2. **No glyph rendering artifacts** — no missing characters, no malformed glyphs, no kerning weirdness, no subpixel-AA shifts visible vs the C reference.
3. **Sweep timing matches C reference.** Operator visual A/B. Quantitative cross-check via frame timestamps in GtkFrameClock if a numeric regression is suspected, but the load-bearing test is operator visual.
4. **Steady 60fps (≥58 sustained, no frame drops below 50fps during any sweep).** Measured via `frame_clock.get_fps()` instrumentation OR equivalent. Reference target: the C version's effective frame rate on the same hardware.
5. **Fade transitions are smooth** (no stepping, no banding visible) — both into the animation and out at the end.
6. **Total animation duration is bit-equivalent to the C reference** — 7-sweep total wall-clock matches within ±50ms.
7. **Operator visual sign-off after observing the animation 3+ times** on a freshly-installed user account on the reference laptop. The operator is the authoritative judge — *"smooth as glass"* is the criterion, and only the operator can attest to it because the operator did the original tuning.

---

## §3. Fail criteria (any one of these triggers revert-to-C per Q4 directive)

**The load-bearing criterion is operator-visual sign-off (per ratified §3 verdict).** Quantitative metrics (frame rates, glyph diffs) are supportive evidence but the authoritative judgment is operator's eyes on the IGOS laptop.

Triggers:
- Operator-direct verdict: *"this doesn't look right"* or *"nope, back to C we go"* — no further analysis required, revert is greenlit immediately.
- Any pass criterion §2 (1-7) cannot be brought to parity with the C reference after the operator-direct sign-off cycle.
- Implementation reveals an architectural blocker that GTK4/Wayland fundamentally cannot solve (e.g. compositor-side latency that no amount of code-level work can mitigate on the reference hardware).
- AMD-host secondary check surfaces smoothness regressions that are categorically different from the Intel host — indicating GPU-driver-specific behavior that the rewrite cannot cleanly abstract over.

---

## §4. Test cases (functional, not smoothness — smoothness is operator visual)

Functional behavior the rewrite must demonstrate, independent of smoothness:

1. **Fresh-install + first user login** — animation fires automatically (the systemd user unit `intergen-firstboot.service` is activated by `graphical-session.target` at session start), runs to completion, exits cleanly. Welcomer's auto-generated systemd user unit (`app-intergen\x2dwelcome@autostart.service`, created by `systemd-xdg-autostart-generator` from the welcomer's `/etc/xdg/autostart/intergen-welcome.desktop` entry) then fires, deterministically AFTER the firstboot unit per the firstboot unit's `Before=` clause.
2. **Done-marker semantics** — animation fires exactly once per user on first login. On second login by the same user, animation does NOT fire (done-marker at `~/.local/share/intergen/firstboot-animation-done` already present).
3. **Multi-user** — each user account sees the animation on THEIR first login. User A's done-marker does not suppress user B's animation.
4. **Welcomer chain integrity** — animation exits cleanly, welcomer's autostart fires after. No race where both windows are visible simultaneously. No race where welcomer fires BEFORE animation completes.
5. **Failure resilience** — if the animation binary crashes mid-sweep (kill -9, segfault, GTK error), the welcomer's autostart still fires (this is the failure-isolation guarantee from Q1 CHAIN verdict).
6. **Compositor edge-cases** — animation displays correctly on the laptop's actual GPU + Mutter version. If Mutter is restarted (gnome-shell --replace) DURING the animation, the animation either resumes cleanly or exits and lets welcomer fire (no hang).
7. **Reboot persistence** — animation does NOT fire on reboot for a user who has already seen it (done-marker persists across reboots).

---

## §5. Test sequence per iteration

For each Python iteration:

1. Build the Python package on the build chroot (or directly on the laptop for fast iteration).
2. Deploy to the IGOS laptop: install the new package (a `pkm install` runs `post_install()` for `systemctl --global enable intergen-firstboot.service`, which creates the install-time symlink at `/etc/systemd/user/graphical-session.target.wants/intergen-firstboot.service`; for manual file-drop deployment, copy the files into place and run that `systemctl` invocation manually afterward). Verify with `systemctl --user status intergen-firstboot.service` that the unit is enabled and in the `inactive (dead)` state (per-user systemd instances pick up the new unit at user-login time; no `daemon-reload` is required since `systemctl --global daemon-reload` is not a valid scope per `5466b17c`). Ensure the existing C/DRM systemd unit at `assets/intergen-firstboot-drm/intergen-firstboot.service` (source-of-truth path only; per audit row D-001 it was never wrapped in a shipping package) is NOT enabled (per Q6 + the not-deployed posture).
3. Reset done-markers: `rm -f ~/.local/share/intergen/firstboot-animation-done ~/.config/intergen-welcome/done` for the test user.
4. Logout. Login.
5. Operator observes the animation. Records: tearing? glyphs? timing? jank? overall smoothness vs C reference?
6. Run functional test cases §4 (1-7) to confirm no regressions.
7. Repeat steps 3-6 with a different test user to confirm multi-user isolation.
8. If pass: declare iteration successful + operator sign-off + lock the implementation.
9. If fail: capture specific failure modes + iterate on the implementation + return to step 1.

---

## §6. Iteration approach + revert trigger

**No iteration count set per operator-direct ratification.** We iterate until the operator either signs off on smoothness equivalence OR declares revert with *"nope, back to C we go"* (operator-verbatim 2026-05-20 ~20:Z). The decision-authority on convergence belongs entirely to the operator; the build-system coordinator's job is to keep producing iterations the operator can observe and judge.

**Iteration pacing:** each iteration produces a buildable Python package + a clean deploy to the IGOS laptop + the AMD-host file-drop + operator-visual observation cycle. Operator's feedback on each iteration directly informs the next.

---

## §7. Revert path (if testing fails)

Plain operational note — no ceremony required. If the operator calls revert:

1. Remove the Python package + the new XDG autostart file from the build pipeline.
2. The C/DRM source at `assets/intergen-firstboot-drm/` remains in tree per Q6 verdict; no deletion needed. **Note on revert-state shipping posture:** the C/DRM has never been wrapped in a shipping package per audit row D-001 (`built but never shipped (no package)`); the systemd unit at `assets/intergen-firstboot-drm/intergen-firstboot.service` is a source-of-truth file, not a deployed unit. The immediate revert-state therefore has no shipping firstboot animation. If the operator wants a shipping firstboot animation under the revert, that requires separately authoring a proper C/DRM package (`packages/desktop/intergen-firstboot/build.sh` + `package.yml`) which deploys the unit to `/etc/systemd/system/intergen-firstboot.service` and the binary to `/usr/bin/intergen-firstboot` — that packaging work is its own work-stream and is not part of this revert path.
3. Move the Python source files out of tree to an archive location (the `build-output/` or `_archive/` area; not kept live in tree per ratified §5 verdict). Git history retains the full implementation if archaeology is ever needed.
4. The flow-intent question (operator wants animation post-login, but C can only render pre-compositor) remains open — a different approach would be needed to deliver post-login while staying in C. That decision belongs to operator + happens at a separate time.

Single commit lands the revert; no audit row required (the lesson is captured in the matrix + this plan).

---

## §8. Not covered in this verification

The following are intentionally addressed elsewhere; this plan focuses only on smoothness-equivalence verification of the rewrite:

- **Smoothness on hardware OTHER than the reference laptop.** The smoothness tuning was done on this specific hardware; we cannot test smoothness equivalence on every possible Wayland-capable GPU stack within an initial ship. Broader hardware coverage is a subsequent-release work-stream.
- **Performance benchmarking** (CPU usage, memory footprint, GPU utilization). The smoothness gate is operator-visual + frame-rate-numeric; resource usage is not a closure criterion here.
- **Accessibility** (high-contrast mode, screen reader, reduced-motion). Important but addressed in a subsequent-release work-stream after the initial smoothness-equivalence verification lands.

---

## §9. Operator-ratified verdicts (resolved 2026-05-20 ~20:30Z)

| # | Question | Verdict |
|---|---|---|
| 1 | Test environment | **Both targets** — IGOS laptop primary + DS-v2 host AMD-GPU sanity check via manual file-drop |
| 2 | Fail criteria load-bearing | **Operator-visual sign-off** (quantitative metrics are supporting evidence only) |
| 3 | Iteration count | **No count** — iterate until operator signs off OR calls revert |
| 4 | Revert path | **Plain operational note**, no ceremony |
| 5 | Python source disposition post-revert | **Archive out of tree** — git history is the long-term retention |

---

**Next step:** Python authoring proceeds against this plan.
