# Audit Review — DS-workstation (Part 8: Frontend Depth)

**Reviewer:** DS-workstation (chris-ubuntu)  
**Date:** 2026-05-07  
**Section assigned:** §8 (Frontend depth audit) — second-pass reviewer  
**Audit source:** DS-v2, §8 frontend depth audit

---

## §8 Frontend Review

### Findings verdicts

- **F1 (HIGH): PRIORITIZE** — `intergen/cli.py:53` `except Exception: return None`. Confirmed: bare catch silently swallows ALL D-Bus errors (auth failure, timeout, method-not-found). User sees "InterGen daemon not running" even when it IS running but unreachable. Prime Directive violation: misled user takes wrong action (starts duplicate daemon). Proposed `except Exception as e: print(..., file=sys.stderr)` + "Status" health-check before direct-mode fallthrough is correct and minimal.

- **F2 (HIGH): PRIORITIZE** — `tui.py:563-599` no top-level exception handler. Confirmed: `run_installer()` has zero try/except. SIGINT during install = raw Python traceback. `cleanup_on_abort()` only fires on user-initiated cancellations, not crashes. The proposed `try: ... except KeyboardInterrupt: cleanup; except Exception: print(error)` is correct.

- **F3 (MEDIUM): PRIORITIZE** — Hostname validation missing. Confirmed: `_ask_hostname()` at line 234 passes unfiltered input to backend `/etc/hosts` writer. Frontend is the gatekeeper; backend writes `${hostname}.localdomain` without validation. A hostname with `\n` or `;` would inject into /etc/hosts format. The proposed regex `^[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?$` is the POSIX-hostname standard. I'm upgrading this to HIGH: the unfiltered-to-shell context makes this a correctness+security gap at the user-facing boundary.

- **F4 (MEDIUM): PRIORITIZE** — Password fields plaintext in state. Confirmed: `state.py:54-58` has 5 password fields as plain `str` attributes. No `clear_sensitive_data()`. The `to_install_io()` function is explicitly documented as excluding passwords "PRIME DIRECTIVE" (line 103-106), showing awareness of the concern — but clears only at serialization, not at the in-memory state object level. The proposed `clear_sensitive_data()` called after install completion is correct defense-in-depth. Note: passwords ARE required at install time (must be passed to `run_install`), so clearing after Use is the right timing.

- **F5 (MEDIUM): PRIORITIZE** — D-Bus error details leak. Confirmed: `dbus_daemon.py:438` returns `str(e)` as D-Bus error detail to ANY local process on the session bus. Server-side already logs the full exception (`log.error("D-Bus method call error: %s", e)`) — the leak is only on the RPC response. The proposed sanitization to "Internal error — check daemon logs for details" is correct.

- **F6 (MEDIUM): DEFER** — `--insecure` flag documentation. Confirmed: dialog(1) needs `--insecure` for asterisk feedback; whiptail(1) ignores it. The proposed comment on line 104 is correct but a documentation-only finding. Queue for polish pass.

- **F7 (LOW): DISAGREE** — Self-declares "no finding — just noting consistent pattern for audit record." Not a finding.

- **F8 (LOW): DEFER** — Password confirmation validation. Confirmed: `state.py:82-84` already validates `user_password == user_password_confirm` and `root_password == root_password_confirm` in `can_proceed()`. The audit says "If the screen doesn't check" — it DOES check. However, the check happens on state.validate(), not at on_next() time. The user CAN navigate past the User screen with mismatched passwords if the Confirm screen doesn't re-validate. Verify: Confirm screen calls `state.can_proceed()` before destructive action? If yes, this is covered. DEFER: validate the Confirm screen gating, not the User screen.

- **F9 (LOW): DEFER** — Drag-and-drop bypass of paste-block. Confirmed: only `paste-clipboard` signal suppressed. GTK4 DnD is a real concern. However, this is a LOW-severity finding for a niche attack vector (drag text from another window into the phrase-entry box during install). The proposed fix (`enable-drag-source = False` on parent) is one-line. Queue for security polish.

- **F10 (LOW): PRIORITIZE** — Dual daemon risk on D-Bus fallthrough. Confirmed: `cmd_ask` falls through to direct mode if D-Bus call returns None, without checking whether the daemon EXISTS but the method FAILED. The proposed "call Status first, only start direct mode if Status also fails" is correct. I'm upgrading to MEDIUM: starting a second daemon process that loads models into RAM is a resource-leak with user-observable impact (memory pressure, confused process state).

- **F11 (LOW): DEFER** — No message size limit on D-Bus Ask. Valid defense-in-depth but low risk — D-Bus is session-local, and a 10MB message from a malicious local app is limited to the daemon process memory. Queue for polish.

- **F12 (LOW): DISAGREE** — Self-declares "Verified: no side effects in constructors — clean." Not a finding.

- **F13 (LOW): DEFER** — Reboot command availability not checked. Valid but low impact — rc=0 means install succeeded, user just needs to reboot manually. Queue with B10 (chroot IGOS hardcode) for consistency.

- **F14 (LOW): DISAGREE** — Self-declares "Branding is consistent — no findings." Not a finding.

### Cross-cutting concerns SPOC may have missed

- **F1 scope:** The misled-user problem is worse than described. If the D-Bus call fails because the daemon IS running (e.g., auth failure, different user session), the CLI starts a SECOND daemon that loads llama-server and model weights into RAM. On a 16GB system with a 7GB model, that's a 14GB memory spike. User sees "starting direct session" but actually has two daemons competing for the same D-Bus name. This is both the F10 dual-daemon concern AND the F1 "user misled" concern combined — they compound each other.
- **F2 severity:** The missing SIGINT handler is the most user-visible flaw. A user who Ctrl-C's during install sees a raw Python traceback — this breaks the Prime Directive of "installer should never show internals to the user." I considered upgrading to CRITICAL but stopped short because (a) the install IS interrupted and (b) the cleanup-on-abort function exists and CAN be called — it just isn't.
- **F3 upgrade rationale:** Hostname → /etc/hosts is a shell/format injection surface. The frontend not validating is a gatekeeping failure. HIGH, not MEDIUM.
- **F10 upgrade rationale:** Dual daemon = model loaded twice = RAM wasted. MEDIUM, not LOW. Combined with F1's "user misled" makes a compound failure: user is told daemon isn't running, starts a duplicate, and now has 2x model memory consumption.
- **F8 partial-FP:** The audit says "If the screen doesn't check" — it DOES check (state.py:82-84). But the finding's spirit (validate before destructive action) is correct — just needs to be checked at the Confirm screen level, not the User screen level.

### Pattern analysis (DS-v2 audit methodology, second round)

Two false-positives in this round (F7, F12, F14 are self-declared non-findings — correct for the auditor to note). No *mistaken* FPs like §7's G1/G5. The auditor successfully applied py_compile pre-screen and the methodology-calibration note from §4 S1 reclassification. Quality improvement over §1-§7.

### Filter check

- **User-control posture:** OK — F2 (SIGINT traceback) is the only user-visible correctness gap; all other findings are defense-in-depth.
- **Anti-supply-chain posture:** OK — integrity dialog paste-block hardening is verified correct; DnD bypass (F9) is a valid low-severity gap.
- **Owner-approved baselines:** OK.

---

## Summary

| Verdict | Count | IDs |
|---------|-------|-----|
| PRIORITIZE | 7 | F1, F2, F3 (upgraded HIGH), F4, F5, F10 (upgraded MEDIUM) |
| DEFER | 4 | F6, F9, F11, F13 |
| DISAGREE | 3 | F7, F12, F14 (self-declared non-findings) |

**Reclassifications:**
- **F3 MEDIUM→HIGH:** Hostname injection surface at frontend boundary.
- **F10 LOW→MEDIUM:** Dual daemon = 2x model memory, compound failure with F1.
