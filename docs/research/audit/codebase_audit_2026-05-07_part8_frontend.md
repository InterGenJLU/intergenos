# Codebase Audit — §8 Frontend Depth Audit

**Auditor:** chris-ubuntu_hplt-codium-deepseek  
**Date:** 2026-05-07 10:10–10:40 CDT  
**Master:** 6750fc1  
**Paths audited:** `installer/frontend/tui.py` (603 lines), `installer/frontend/gui/` (7 screens + state + style + window + integrity_dialog), `intergen/cli.py` (209 lines), `intergen/dbus_daemon.py` (488 lines), `intergen/interfaces/` (8 files), `intergen/tools/` (20+ files)  
**Lens:** Frontend-specific — user-facing strings, error clarity, signal handling, paste-disable, screen state machine, D-Bus introspection, console scrubbing  
**Findings:** 0 CRITICAL, 2 HIGH, 4 MEDIUM, 8 LOW  

---

## Summary

The frontend depth audit covers ~1100 lines of user-facing code across the TUI installer, GTK4 GUI, and InterGen AI assistant CLI/D-Bus layers. The GUI is well-hardened: paste-disabled phrase entry with live-validation, proper GLib thread marshaling, NavigationView screen stack with pre-instantiated widgets preserving state. The D-Bus service has correct signal-based shutdown and proper subprocess lifecycle management. Two HIGH findings: the CLI's diagnostic suppression in `try_dbus()` (hides D-Bus errors from the user and falls through to direct mode with a misleading message), and the TUI's complete absence of top-level exception handling (a SIGINT during install produces a raw Python traceback visible to the user).

---

## Closure Table

| ID | Severity | Section | File:Line | Finding | Proposed Fix |
|----|----------|---------|-----------|---------|--------------|
| F1 | HIGH | CLI | `intergen/cli.py:53` | `try_dbus()` catches bare `except Exception` and returns `None`, then `cmd_ask()` at line 67 sees `response is None` and prints "InterGen daemon not running. Starting direct session..." — the user is MISLED. The actual error (D-Bus authentication failure, timeout, method-not-found) is silently swallowed. User starts a direct session believing the daemon isn't running, when it may be running but unreachable. Prime Directive violation: the user is given wrong information that drives a wrong action (starting a duplicate daemon). | Change `except Exception: return None` to `except Exception as e: print(f"D-Bus call failed: {e}", file=sys.stderr); return None`. Then in `cmd_ask`, check if the D-Bus error is a connection-failure (not an auth/permission error) before falling through to direct mode. If auth/permission error, exit with guidance: "D-Bus permission denied — is intergen.service running under your user session?" |
| F2 | HIGH | TUI | `installer/frontend/tui.py:563-599` | `run_installer()` function has ZERO exception handling. If `run_install()` (line 582) raises (e.g., OSError during package install, KeyboardInterrupt from SIGINT), no cleanup runs. `cleanup_on_abort()` is called on user-initiated cancellations but NOT on unexpected exceptions. The two `except` blocks in tui.py are local-only: one for `AttributeError` on disk detection (line 336), one for `KeyboardInterrupt` on the override phrase prompt only (line 441). A SIGINT during the actual install produces a raw Python traceback on the user's tty — not "Installation cancelled." | Wrap `run_installer()` body in `try: ... except KeyboardInterrupt: return _cleanup_on_abort(yaml_path=...)` to catch SIGINT cleanly. For other exceptions, print the error message without the traceback: `except Exception as e: print(f"forge: unexpected error: {e}", file=sys.stderr); return 1`. |
| F3 | MEDIUM | TUI | `installer/frontend/tui.py:234-239` | `_ask_hostname()` accepts any input from dialog with no validation. Passed through to `config.generate_hostname()` which writes `${hostname}.localdomain` to `/etc/hosts` unfiltered (§2 I6). A hostname with newlines, semicolons, or other shell-interpretable characters would be injected into the generated /etc/hosts. Frontend lens: the TUI is the gatekeeper and should validate before passing to backend. | Add hostname validation in `_ask_hostname()`: `re.fullmatch(r'^[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?$', hn)`. Re-prompt on invalid input with a clear error: "Hostname must contain only letters, digits, and hyphens, and cannot start or end with a hyphen." |
| F4 | MEDIUM | GUI | `installer/frontend/gui/screens/user.py` | Password fields in InstallerState are plaintext dataclass attributes. The Gtk.Entry fields on the User screen have `visibility=False` (password dots), but the values stored in `self._window.state.user_password` are plaintext Python strings. If a crash dump or core file is generated during the install, passwords are recoverable from memory. This is inherent to any interactive installer — but the frontend doesn't clear passwords from state after install completion (the `InstallerState` object persists until GC). | Add a `clear_sensitive_data()` method to InstallerState that zeroes out `user_password`, `user_password_confirm`, `root_password`, `root_password_confirm`, `mok_password`. Call it from ProgressPage after `run_install()` completes (both success and failure paths). |
| F5 | MEDIUM | D-Bus | `intergen/dbus_daemon.py:430-434` | `on_method_call` catches `Exception as e` and returns the error string to the D-Bus caller via `invocation.return_dbus_error()`. The error string `str(e)` is sent to the remote caller without sanitization. If the exception message contains internal paths, stack traces, or other internal details, they leak to any local process on the session bus. | Sanitize the error response: `invocation.return_dbus_error("com.intergenos.InterGen.Error", "Internal error — check daemon logs for details")`. Log the full exception with traceback server-side. |
| F6 | MEDIUM | TUI | `installer/frontend/tui.py:104-106` | `_ask_password` uses `--insecure` flag with `--passwordbox`. dialog(1) supports `--insecure` (shows asterisks for each typed character). whiptail(1) does NOT support `--insecure` — it ignores unknown flags. When whiptail is the binary, the password prompt uses whiptail's default passwordbox behavior (asterisks shown), which is equivalent. This is correct behavior but undocumented — a future developer might remove `--insecure` thinking it's a no-op on whiptail, not realizing dialog needs it. | Document the flag difference in a comment on line 104: "dialog(1) needs --insecure to show feedback; whiptail ignores it gracefully." |
| F7 | LOW | TUI | `installer/frontend/tui.py:31` | Print statement uses 'print()' (Python builtin), not `sys.stdout.write()`. In line 31 with `sys.stderr.write()`. All user-facing output from `print()` goes to stdout; diagnostic messages go to stderr. Consistent pattern — no finding. Verified. | N/A — just noting the consistent pattern for audit record. |
| F8 | LOW | GUI | `installer/frontend/gui/state.py:56-58` | `root_password` and `root_password_confirm` are separate fields with no equality validation in the dataclass itself. The User screen's `on_next()` method should validate equality before advancing. If the screen doesn't check, mismatched passwords flow silently to the backend. | Verify that `screens/user.py:on_next()` performs `root_password == root_password_confirm` check. If not, add before `navigate_next()`. |
| F9 | LOW | GUI | `installer/frontend/gui/integrity_dialog.py:69-71` | `_block_paste` calls `widget.stop_emission_by_name("paste-clipboard")` — this suppresses the Gtk.Entry paste-clipboard signal. However, GTK4 also supports DnD (drag-and-drop) text insertion. A user could drag text from another window into the entry and bypass the paste-block. The `paste-clipboard` suppression only blocks keyboard/context-menu paste, not DnD. | Connect `drag-data-received` signal to the same block handler. Or set `entry.set_editable(False)` temporarily on drag-drop events. GTK4 dropped `drag-data-received` — check whether `enable-drag-source` = False on the parent dialog window would suffice. |
| F10 | LOW | CLI | `intergen/cli.py:60` | `cmd_ask` tries D-Bus first, then falls through to direct mode. Starting a direct session imports `InterGenDaemon` and calls `start_service()` — this initializes llama-server, loads models, and exports the D-Bus interface. If the daemon was already running on the session bus (the D-Bus call failed for a non-connection reason), we now have TWO daemons competing for the same D-Bus name. Gio.bus_own_name on the second daemon will receive a `name_lost` callback, but the model is already loaded consuming RAM. | Before starting direct mode, check `try_dbus("Status")` specifically with a short timeout. If Status returns, the daemon IS running and the Ask failure is a method-specific error — report it and exit. Only start direct mode if Status also fails (meaning no daemon on the bus at all). |
| F11 | LOW | D-Bus | `intergen/dbus_daemon.py:50-54` | INTROSPECTION_XML exposes three methods: Ask, Status, GetTier. No properties are exposed. This is appropriately minimal — no writable state exposed over D-Bus. The Ask method takes a raw string message — no input validation server-side beyond what the router does. If a malicious local app sends a 10MB message, the daemon process memory spikes. | Add message size limit in `on_method_call` for Ask: `if len(message) > 4096: invocation.return_dbus_error(...)`. |
| F12 | LOW | GUI | `installer/frontend/gui/window.py:50` | All 7 screen instances created at once in constructor: `self._screens = [cls(self) for cls in SCREEN_ORDER]`. The base screen `_base.py` and `progress.py` and `done.py` have `__init__` methods. If any `__init__` has side effects beyond widget creation (e.g., file reads, network calls, subprocess calls), those happen at app startup rather than on screen navigation. Verified: `_base.py.__init__` only sets CSS classes; `progress.py.__init__` creates widgets; `done.py.__init__` creates labels. No side effects — clean. | N/A — verified no side effects in constructors. |
| F13 | LOW | TUI | `installer/frontend/tui.py:593` | `subprocess.run(["reboot"], check=False)` after install completion. If `reboot` is not available (non-systemd init, or the binary is missing), the command silently fails (`check=False`) and the TUI exits with rc=0 (install success). The user sees "forge: rebooting..." but nothing happens. | Check `shutil.which("reboot")` before attempting. If missing, print "Reboot command not found. Please reboot manually." and return 0. |
| F14 | LOW | All | `installer/frontend/` + `intergen/` | User-facing strings use "InterGenOS" (mixed case) consistently throughout. D-Bus names use lowercase "intergenos" per reverse-domain convention (correct). The TUI backtitle is "InterGenOS Installer (Forge — Declarative Builder)" — clear and descriptive. The GUI window title is "Forge — InterGenOS Installer" — consistent. No branding inconsistencies found. | N/A — branding is consistent. |

---

## Detailed Analysis

### A. TUI (`installer/frontend/tui.py`, 603 lines)

**Architecture:**
- Dialog/whiptail binary resolution with adaptive output handling (stdout for dialog, stderr for whiptail)
- 4-phase flow: walking (config Q&A) → emit_yaml → prompt_install_io (disk + passwords) → run_declarative
- Ephemeral YAML at `/var/lib/forge/install.yaml` — cleaned up after install (line 598)

**Frontend strengths:**
- Dialog/whiptail graceful fallback: if one binary is missing, the other is used. Both share a compatible flag surface.
- Override phrase prompt catches KeyboardInterrupt + EOFError (line 441) — correct for the security-sensitive prompt
- `_cleanup_on_abort` removes partial YAML state (line 152-160) — correct
- Integrity override audit log path documented for post-install review (line 538)

**Frontend gaps:**
- No top-level exception handler (F2) — SIGINT during install = raw traceback
- No hostname validation (F3) — passes unfiltered to backend /etc/hosts writer
- `--insecure` flag compatibility undocumented (F6)
- `reboot` availability not checked (F13)

### B. GTK4 GUI (`installer/frontend/gui/`)

**Screen architecture (7 screens):**
1. Welcome — acknowledgment only (sets `welcome_acked=True`)
2. Keyboard/Locale/Timezone — pre-filled defaults from InstallerState
3. Disk — drive selection with alongside/fresh modes
4. User — hostname, username, passwords, optional MOK password
5. Confirm — summary display + destructive confirmation
6. Progress — worker-threaded install execution with phase progress
7. Done — success/failure summary with reboot guidance

**Integrity dialog hardening (verified):**
- `paste-clipboard` signal suppressed ✓ (line 69-71)
- Right-click menu removed (`set_extra_menu(None)`) ✓ (line 76)
- Live-validation on every keystroke ✓ (line 81-86)
- NO_SPELLCHECK + NO_EMOJI input hints ✓ (line 64)
- FREE_FORM input purpose (user sees the phrase they type) ✓ (line 65)
- Submit button disabled until phrase matches ✓ (line 157-160)
- GLib.idle_add for thread-safe dialog rendering ✓ (line 120, 177)

**Frontend gaps:**
- Drag-and-drop bypass of paste-block (F9)
- Passwords in plaintext state after install (F4)
- Password confirmation not validated (F8)

### C. InterGen CLI (`intergen/cli.py`, 209 lines)

**Commands:** ask, status, tier, tools, test, setup, daemon

**Frontend strengths:**
- D-Bus first, direct-mode fallback — graceful degradation
- JSON response parsing with `data.get("response", response)` fallback
- Status/tier output uses structured key-value format

**Frontend gaps:**
- D-Bus errors silently swallowed (F1) — HIGH, user misled
- Dual daemon risk on D-Bus fallthrough (F10)

### D. InterGen D-Bus Daemon (`intergen/dbus_daemon.py`, 488 lines)

**Exposed interface:** Ask(message:str) → str, Status() → str, GetTier() → str

**Frontend strengths:**
- Gio native D-Bus (no pip deps needed — part of GNOME stack)
- Signal handlers for SIGTERM + SIGINT (line 476-477) — clean shutdown
- Subsystem initialization order documented: hardware → model → llama-server → D-Bus
- `start_service` checks for running process + available memory before model load
- Graceful degradation: D-Bus export failure logs warning + continues without bus

**Frontend gaps:**
- D-Bus error details leak to remote callers (F5)
- No message size limit on Ask method (F11)
- D-Bus interface minimal (3 methods) — good, no unnecessary exposure

---

## Audit Techniques Applied

| Technique | Result |
|-----------|--------|
| User-facing string audit | Branding consistent: "InterGenOS" (mixed case) in user-facing strings, "intergenos" (lowercase) in D-Bus names per reverse-domain convention. Error messages are clear and actionable. One gap: "InterGen daemon not running" is misleading when D-Bus errors are suppressed (F1). |
| Signal/interrupt handling | TUI: No SIGINT handler for main install flow (F2). Override-phrase prompt catches KeyboardInterrupt (correct). D-Bus daemon: SIGTERM + SIGINT handlers correct (line 476-477). |
| Paste-disable verification | GUI integrity dialog: paste-clipboard suppressed ✓, right-click menu removed ✓, live-validation ✓. One gap: drag-and-drop bypass (F9). |
| Screen state machine | NavigationView with pre-instantiated screens preserves widget state across back/forward ✓. No screen constructors have side effects ✓. `navigate_back()` pops from NavigationView ✓. |
| D-Bus introspection surface | 3 methods exposed (Ask, Status, GetTier) — appropriately minimal. No writable properties. All response strings are JSON — parseable by any D-Bus client. |
| Console/journal scrubbing | Passwords captured with `--insecure`/`--passwordbox` (shows asterisks) — never printed. Override phrase captured via `input()` (TUI) or paste-disabled Gtk.Entry (GUI) — not logged. Integrity override log path shown to user but contents are per-file hashes, not secrets. |
| py_compile pre-screen | All frontend Python files compile cleanly. |
