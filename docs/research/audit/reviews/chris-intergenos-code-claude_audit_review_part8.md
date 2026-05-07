# Audit Review — §8 Frontend Depth

**Reviewer:** InterGenOS bare-metal contributor
**Date:** 2026-05-07 ~16:00Z UTC
**Master:** c160038
**Lens:** bare-metal install + frontend UX (the install-time TUI + GUI flows actually exercised on this host)
**Sections reviewed:** §8 Frontend Depth (14 findings F1-F14)

---

## §8 Frontend Review

### Findings verdicts

- **F1 (HIGH, intergen/cli.py:53)**: **PRIORITIZE** — confirmed accurate. `try_dbus()` bare `except Exception: return None` (line 53) collapses every failure mode (D-Bus auth denied / connection refused / timeout / method-not-found / typed-arg mismatch) into the same `None` sentinel, and `cmd_ask:67` then prints "InterGen daemon not running" when the daemon may very well be running but unreachable. User-control violation: user is given wrong information that drives wrong action. Auditor's fix shape is right; severity HIGH appropriate. Pairs tightly with F10 — fix together.
- **F2 (HIGH, installer/frontend/tui.py:563-599)**: **PRIORITIZE** — confirmed accurate by trace. `run_installer()` has zero exception handling at the outer entry point. Only two narrow except blocks exist in tui.py: `AttributeError` on disks.list_candidates (line 336) and `(EOFError, KeyboardInterrupt)` on the override-phrase prompt (line 441). A SIGINT during `run_declarative()` (line 582) raises straight through to the user's tty. Real bare-metal UX failure: user hits Ctrl-C mid-install, sees a Python traceback, doesn't know if disk was touched. Severity HIGH appropriate — Prime-Directive-class user confusion. Auditor's fix shape (wrap with two-tier KeyboardInterrupt + Exception) matches the canonical install-time UX pattern.
- **F3 (MEDIUM → HIGH, installer/frontend/tui.py:234-239)**: **PRIORITIZE + agree with severity upgrade.** `_ask_hostname()` accepts any input from dialog/whiptail with no validation. Hostname flows: TUI → `walking()` → `emit_yaml()` → `/var/lib/forge/install.yaml` → `validate_install_inputs` (which only checks PRESENCE, not contents) → `config.generate_hostname` writing /etc/hosts. Pairs exactly with §2 I6 backend finding. The frontend is the gatekeeper for malicious yaml or compromised TUI input (custom kickstart-style scenarios). HIGH severity correct: yaml-driven hostname injection at the install boundary is a real attack vector. Recommend bundling with §2 I6 in single fix-wave commit using a shared regex validator (e.g., `installer/backend/_validators.py:hostname_re`) consumed by both frontend and backend.
- **F4 (MEDIUM, installer/frontend/gui/screens/user.py)**: **PRIORITIZE** — confirmed at state.py:54-58 (passwords as plaintext dataclass attributes) + state.py has no `clear_sensitive_data` method. Realistic blast radius: process crash with core dump + attacker access to core file recovers passwords. Cheap fix (one method + one call from ProgressPage post-install both success and failure paths). Defense-in-depth aligns with the broader credential-hygiene pattern already established in `users.run_chroot_stdin` (passwords via stdin not argv to keep them out of process table).
- **F5 (MEDIUM, intergen/dbus_daemon.py:430-434)**: **PRIORITIZE** — confirmed accurate. `invocation.return_dbus_error("com.intergenos.InterGen.Error", str(e))` sends raw exception text back to any local D-Bus caller. Internal paths, file paths, partial stack info all leak to other local processes on the session bus. D-Bus session bus is per-user, so threat model is "trusted user processes" — but a buggy user app reading `last_error` shouldn't see internal daemon paths. Auditor's sanitization fix is correct shape; pair with F11 as a "D-Bus hardening" commit.
- **F6 (MEDIUM, installer/frontend/tui.py:104-106)**: **PRIORITIZE** — code IS correct on both binaries today (dialog needs `--insecure`; whiptail ignores unknown flags gracefully) but the flag-difference is undocumented and a future maintainer might remove `--insecure` thinking it's a no-op. The fix is a 2-line comment. Per the don't-defer-fix-it posture, doc-only fixes that cost near-zero are fix-wave material. Reclassify auditor's MEDIUM down to LOW if SPOC consolidation wants severity-tightening (the immediate behavior is correct; the risk is purely future-maintenance).
- **F7 (LOW, installer/frontend/tui.py:31)**: **DISAGREE** — auditor's own description ends "Consistent pattern — no finding. Verified." This is a verified-clean audit-record entry, not a defect. No fix-wave action.
- **F8 (LOW, installer/frontend/gui/state.py:56-58)**: **DISAGREE** — verified clean by trace. `screens/user.py:on_next` (lines 70-75) explicitly validates both password pairs with toast feedback and returns False on mismatch. AND `state.is_ready_for_install` (lines 81-84) double-checks. AND `state.validation_errors` (lines 218-223) triple-checks. Three-layer defense in place. Auditor's "verify-and-confirm" fix shape is essentially a no-op — the validation already exists. Note: this is a DISAGREE delta vs the workstation-reviewer's PRIORITIZE; recommend coordinator arbitrate by re-reading user.py:58-80.
- **F9 (LOW, installer/frontend/gui/integrity_dialog.py:69-71)**: **PRIORITIZE** — the integrity-override flow is designed around forcing the user to MANUALLY TYPE the override phrase, not paste/drag it. That's the entire point of paste-clipboard suppression + right-click menu removal + live-validation. Drag-and-drop bypass undermines the design intent. Cheap fix (a few lines connecting drag-data-received OR setting drag-source disable on parent). Per the don't-defer-fix-it posture, security-design completion of the trust-anchor verification flow is fix-wave-priority.
- **F10 (LOW → MEDIUM, intergen/cli.py:60)**: **PRIORITIZE + agree with severity upgrade.** Compound failure mode with F1 documented correctly: F1 misleads user about why direct mode starts; F10 then loads model again in direct daemon while the original daemon may still be on the bus (Gio.bus_own_name's name_lost callback fires AFTER model load). Two-fold consequence: misled user perception (F1) + 2x model RAM consumption (F10) on dual-daemon hosts. Severity MEDIUM appropriate when compounded. Auditor's fix (probe Status with short timeout before fallthrough) is the right shape; pair with F1 fix in a single CLI-resilience commit.
- **F11 (LOW, intergen/dbus_daemon.py:50-54)**: **PRIORITIZE** — D-Bus session bus accepts message of arbitrary size. `parameters.unpack()` copies the entire string into Python memory. Threat model is weak (session bus = same-user processes which already have broad access) but a buggy user app sending a 10MB message → daemon RAM spike → potentially OOM-killed. Fix is one line (`if len(message) > 4096: invocation.return_dbus_error(...)`). Per don't-defer-fix-it, trivial-cost defense is in scope.
- **F12 (LOW, installer/frontend/gui/window.py:50)**: **DISAGREE** — auditor's own description ends "N/A — verified no side effects in constructors." This is a verified-clean audit-record entry, not a defect. No fix-wave action.
- **F13 (LOW, installer/frontend/tui.py:593)**: **PRIORITIZE** — confirmed UX failure mode at install-complete. `subprocess.run(["reboot"], check=False)` after `print("forge: rebooting...")` — if `reboot` is missing (build skew, non-systemd init), user sees the rebooting message, prompt returns, nothing happens, no error. Realistic blast radius is small on a properly-built InterGenOS install (reboot is in core tier) but defense-in-depth for unexpected build skew is cheap. 3-line fix (`shutil.which` + fallback message + return 0). Per don't-defer-fix-it, in scope.
- **F14 (LOW, installer/frontend/ + intergen/)**: **DISAGREE** — auditor's own description ends "N/A — branding is consistent." This is a verified-clean audit-record entry, not a defect. No fix-wave action.

### Cross-cutting concerns SPOC may have missed

- **F1 + F10 compound failure**: explicitly documented by both auditor and DS-workstation. Same observation here. Recommend single CLI-resilience commit covering both: (1) `try_dbus` returns structured `(ok, value, error)` instead of `None`-on-error; (2) `cmd_ask` checks `try_dbus("Status")` before falling through to direct mode; (3) error message reflects actual failure category (auth/connection/method) instead of always "daemon not running."
- **F3 + §2 I6 are paired findings — share a regex validator**: F3 is the frontend (TUI) gap; §2 I6 is the backend (config.generate_hostname) gap. Recommend introducing `installer/backend/_validators.py` with a `validate_hostname(hn) -> Optional[str]` returning None-on-valid or error-message-on-invalid. Both `_ask_hostname` (re-prompt loop) and `validate_install_inputs` (reject yaml) consume the same validator. Single shared truth for hostname grammar; closes both gaps in one commit.
- **F4 sensitive-data clearing — also applies to TUI locals**: `prompt_install_io` (tui.py:326) holds `root_pw`, `user_pw`, `mok_pw` as local variables. Less risk than the GUI's persistent-dataclass attributes (locals freed on function exit) but still process-memory-resident through the install. Worth a parallel mention: TUI's pattern is OK because the function returns and the locals are GC'd, but the dict it returns (line 397-403) is then held by `run_installer` for the duration of `run_declarative` (the entire install). Same blast-radius window as GUI. Recommend extending the F4 fix to clear the install_io dict's password fields after run_install completes in `run_declarative`.
- **F5 + F11 are both D-Bus hardening**: bundle as one commit. F5 sanitizes outgoing error strings; F11 caps incoming message size. Both touch `on_method_call` in `_export_dbus`. Single coherent intent ("D-Bus public-surface hardening") under one commit message.
- **F2 + F13 are TUI outer-entry-point UX fixes**: bundle. F2 wraps `run_installer` for SIGINT/exception cleanup; F13 adds reboot-binary availability check before invocation. Both touch the TUI's terminal user experience at the install-complete boundary.
- **Auditor scope-bounded the GUI screens audit to architectural depth**: the audit covered `state.py`, `window.py`, `integrity_dialog.py` line-by-line but the 7 individual screen files (welcome, keyboard_locale, disk, user, confirm, progress, done) got architectural treatment only. Worth confirming with the auditor: were screen `on_next`/`on_load` flows traced for input validation gaps (analogous to F8's `password equality` check), or was the audit primarily on the orchestrator/state-builder layer? If light on screen-internal validation, may merit a follow-up pass post-fix-wave.
- **F2 fix exposes a SIGINT-cleanup question for the GUI too**: GTK4's main-loop interrupt handling differs from CLI SIGINT. The GUI worker thread runs `run_install` synchronously inside ProgressPage. If the worker thread is interrupted (user closes window mid-install), the same partial-state cleanup question applies. Out of §8's surface for now — but worth flagging so the F2 fix considers the GUI counterpart. Recommend SPOC tag a parallel F2-GUI sub-finding.

### Filter check

- **User-control posture ("user controls / understands their machine"):** Strongly engaged on F1, F2, F13. F1 actively misleads users into wrong action (start-direct-session when daemon may be running) — most direct violation in §8. F2 raw-traceback-on-Ctrl-C breaks user confidence and obscures whether disk was touched. F13 silent-reboot-failure leaves user confused about install completion. PRIORITIZE on all three matches the user-control posture.
- **Anti-supply-chain / Mythos ("security-only alignment"):** F9 (DnD bypass of integrity dialog) directly touches the trust-anchor verification flow — closing it serves Mythos posture. F5 (D-Bus error leak) and F11 (size limit) are local-IPC hardening; defense-in-depth aligned. F4 (password residue in memory) is credential-hygiene; less Mythos-direct but still in posture. PRIORITIZE on all four.
- **Owner-approved baselines:** OK across all findings. None touch text-only-no-voice (Rule 4), Forge-not-Calamares (no third-party scaffolding), or signing-key custody. F3 hostname injection touches the install-boundary attack surface but stays inside Forge-native code. All proposed fixes preserve existing architectural decisions.

---

## Verdict Summary

**§8 Frontend Depth:** 10 PRIORITIZE / 0 DEFER / 4 DISAGREE
- PRIORITIZE: F1, F2, F3, F4, F5, F6, F9, F10, F11, F13
- DISAGREE: F7, F8, F12, F14 (all four are verified-clean audit-record entries per the auditor's own descriptions)

**Severity revisions confirmed:**
- F3 MEDIUM → HIGH (matches DS-workstation's recommendation; hostname injection at install boundary)
- F10 LOW → MEDIUM (matches DS-workstation's recommendation; F1+F10 compound failure)

**DEFER-zero rationale:** Per the don't-defer-fix-it posture (owner-signaled essentially everything in scope unless externally blocked), every legitimate finding here is fixable in this fix-wave at low cost. Even the doc-only F6 is a 2-line comment add; the trivial fixes (F11 size cap, F13 reboot check) are 1-3 lines each. None of the legitimate findings are externally-gated or repo-architectural.

**Fix-wave packaging recommendations (5 coherent groups):**
- Group 1 (CLI resilience): F1 + F10 → `intergen/cli.py` `try_dbus` structured-return + Status-probe-before-fallthrough
- Group 2 (D-Bus hardening): F5 + F11 → `intergen/dbus_daemon.py:on_method_call` error sanitization + size cap
- Group 3 (TUI outer-resilience): F2 + F13 → `installer/frontend/tui.py:run_installer` exception wrapper + reboot availability check
- Group 4 (validation defense, paired with §2 I6): F3 → `installer/backend/_validators.py` shared `validate_hostname` consumed by both TUI prompt and `validate_install_inputs`
- Group 5 (GUI security-design completion): F4 + F9 → `state.py:clear_sensitive_data` method called from ProgressPage post-install + `integrity_dialog.py` DnD-bypass closure

Plus standalone: F6 (1-line comment add) — minor, can fold into any of the above as opportunistic doc-fix.

**Delta vs the workstation-reviewer's verdicts (for coordinator arbitration):**
- F6: I PRIORITIZE (cheap doc-only fix); DS-workstation DEFER. Per don't-defer-fix-it, I lean PRIORITIZE; SPOC's call.
- F8: I DISAGREE (verified-clean — three-layer validation already in place at user.py:70-75 + state.py:81-84 + state.py:218-223); DS-workstation PRIORITIZE. Recommend SPOC re-read user.py:58-80 to arbitrate.
- F9: I PRIORITIZE (security-design completion); DS-workstation DEFER. Both reasonable; my lean is toward closing the design loophole given low cost.
- F11: I PRIORITIZE (1-line size-cap fix); DS-workstation DEFER. Per don't-defer-fix-it.
- F13: I PRIORITIZE (UX correctness, 3-line fix); DS-workstation DEFER. Per don't-defer-fix-it.

The auditor's §8 work was thorough and well-calibrated for frontend depth. The four verified-clean audit-record entries (F7, F8 not-actually-clean per my read, F12, F14) are appropriately documented as such. F1 + F2 + F3 surfaced as HIGH-class findings at the right severity calibration.

— Reviewer
