# Forge end-to-end install-flow audit — 2026-05-16

**Scope.** Static code-audit of Forge's 7-screen GUI install flow plus the
backend orchestrator surface it depends on, as part of the Item 4
deliverable on dispatch thread `internal 2026-05-16 four-item brief`. The
dispatched ask was a VM click-through against the cycle-3 unsigned-test
ISO at `build/intergenos-1.0-dev1-smoke.unsigned-test.iso`; this audit
ships the achievable subset (static analysis) plus an explicit
convergence-gate note for the runtime portion.

**Why this isn't a full click-through.** Three constraints, listed
honestly:

1. **No virt tooling local on this bare-metal laptop.** No `virsh`,
   `qemu-system-x86_64`, `virt-install`, or `virt-viewer` in PATH;
   `libvirtd` inactive; no `/var/lib/libvirt/images/` directory. The
   InterGenOS distribution running on this laptop does not yet have a
   running pkm + mirror surface for me to `pkm install qemu libvirt`
   self-service (the mirror is what Item 1's design closes — but
   it's still design-pending-ratification, not deployed).
2. **The cycle-3 ISO would hit known unfixed bugs at step 2 of 7.**
   The portal-gtk env-loss surfaced in cycle 3's serial log freezes
   the disk-picker's file-chooser call at the 120s D-Bus activation
   timeout. The fix is in master at commit `fb96de5e` (the two-stage
   pkexec launcher) but is not in the cycle-3 ISO. So a click-through
   against this ISO would reproduce the cycle-3 stall without
   surfacing new information.
3. **the build-host agent's libvirt VM (`intergenos-iso-test`, shut-off) is
   the build-host agent-lane-locked during the build-host agent's pause-for-context.** Per the build-host agent's
   pause broadcast `2026-05-16T05:59:25Z`, the build-host agent's lane includes the
   build VM + chroot state; touching the build-host agent's VM definitions during
   pause crosses a lane boundary the dispatch did not authorize.

**The correct convergence shape.** A fresh rebuild that bundles
this branch's master tip (after `ea6fdad2` lands the Item 1/2/3
deliverables) plus the build-host agent's `iso_include` mechanism (gated on the
Item 2 classification list, now delivered at `docs/extra-tier-classification.md`)
is the right test target. Click-through against that rebuild
validates: the Item 3 portal-gtk fix; the Item 2 server-shape
exclusion; the build-host agent's preset-default-disable fix at `1218d70a`; the build-host agent's
setuid safety-net at `e452bbe9`. Without those landed in an ISO,
runtime click-through tests a known-broken state and adds no new
signal.

Until that rebuild lands, the achievable solo deliverable is this
static audit. Findings below are filed by severity. Severity
labelling is intentionally conservative (HIGH = blocks v1.0 ship;
MEDIUM = visible UX defect; LOW = polish or future-phase scope).

---

## HIGH severity

### F1 — Disk picker is text-entry, not a list of detected disks

**Where.** `installer/frontend/gui/screens/disk.py:25-51`.

**What.** The Disk screen renders a single `Gtk.Entry` with
placeholder `/dev/sda` and a confirm-destructive checkbox. The user
types the device path. No detection, no enumeration, no model + size
display.

**Why it's HIGH.** A user on a laptop with an NVMe drive who types
`/dev/sda` (the placeholder) without thinking — and there's an
NVMe-bay USB drive plugged in for backup, mounted as `/dev/sda` —
will overwrite the wrong device on confirm. Calamares, Anaconda,
Ubiquity, and Pop!_OS all show a structured list with device path +
model + size + filesystem + partition table; Forge's TUI uses
exactly this pattern (`disks.detect_disks()` → `prompt_install_io`
menu in `installer/frontend/tui.py`). The GUI is the one surface
that should be MORE forgiving than the TUI, not less.

**Backend already exists.** `installer/backend/disks.py:51`
`detect_disks()` returns `list[Disk]` with `path`, `name`,
`size_bytes`, `size_human`, `model`, `removable`, `partitions`.
The DiskPage just doesn't call it.

**Recommended fix.** Replace `_disk_entry` with a `Gtk.ListBox` of
detected disks. Each row shows `path | size_human | model |
removable-flag`. Selection drives `state.target_disk`. Keep the
text-entry as a fallback if `detect_disks()` returns empty (live
ISO with no writeable disks → user can override for manual
testing).

**Why it's HIGH not BLOCKER.** Cycle-3 ISO's smoke test in a VM
boots with a single attached virtual disk, so the typo-risk window
is narrow during dev validation. Real users on real hardware with
multiple disks are the failure mode. Fix should land before signed
ISO.

### F2 — Username has no format validation

**Where.** `installer/frontend/gui/screens/user.py:67-69`.

**What.** Username validation is `if not state.username: toast +
return False`. No check for:

- Reserved usernames (`root`, `daemon`, `nobody`, `systemd-*`, etc.).
  Creating a user collision corrupts `/etc/passwd` or fails partway
  through the user-creation phase.
- Shell-unsafe characters (`:`, `\n`, `$`, `\``). The downstream
  `useradd` call sanitizes some but not all; a `:` in the username
  is treated as a field separator by passwd-file rewrites further
  down the chain (`installer/backend/users.py` calls — pending
  verification).
- POSIX portability (`[a-z_][a-z0-9_-]*[$]?`). Linux is more
  permissive than POSIX but most user-management tools assume the
  POSIX shape.

**Recommended fix.** Add `validate_username()` to
`installer/backend/_validators.py` mirroring the existing
`validate_hostname()`. Call from `UserPage.on_next`. Same toast
pattern.

### F3 — `state.target_disk` is not normalized to `/dev/` prefix

**Where.** `installer/frontend/gui/screens/disk.py:58-67`.

**What.** `on_next` reads `self._disk_entry.get_text().strip()` and
stores it verbatim if non-empty. A user typing `sda` (no `/dev/`)
passes the "not empty" check, gets stored as `state.target_disk =
"sda"`. Downstream `disks.partition_disk_fresh()` or `parted` calls
will then fail or — worse, if there's a file called `sda` in the
current working directory — operate on the wrong path.

**Recommended fix.** Normalize: if path doesn't start with `/dev/`,
prepend it; if the resulting path doesn't exist as a block device,
toast an error.

---

## MEDIUM severity

### F4 — Progress screen's worker_thread is spawned in `on_load`, not gated against re-entry

**Where.** `installer/frontend/gui/screens/progress.py:80-117`.

**What.** `on_load` runs every time the page is loaded, including on
back→forward re-entry. It unconditionally spawns
`self._worker_thread = threading.Thread(...).start()`.

**Failure mode.** User clicks Install → progress page loads → install
starts. User clicks Back (the back_button is hidden — but it's
hidden via `.set_visible(False)`, not removed; the
window.navigate_back path can still arrive here via accelerator/key
shortcut or via the NavigationView's swipe gesture on touch devices).
On re-entry, on_load fires again, spawning a SECOND install thread
operating on the same target disk in parallel with the first.

**Recommended fix.** Gate the spawn behind
`if self._worker_thread is None and not state.install_started:`.
And `state.install_started` is already set to True at the top of
`on_load`, so the existing variable suffices — just check it before
spawning.

### F5 — NavigationView pop is async; `_screen_index` decrement is sync

**Where.** `installer/frontend/gui/window.py:65-70`.

**What.** `navigate_back` decrements `_screen_index` and calls
`self._nav_view.pop()`. The visual pop is async (next GTK event-loop
tick); the index decrement is sync. A user clicking Back twice
rapidly decrements the index twice, but the visual stack may have
only popped once.

**Failure mode.** Index = N-2, visual page = N-1. Next click on
"Next" advances from N-2 → N-1, which is the page the user is
ALREADY ON. Double-tap-back → next gives a confusing "nothing
happened" experience.

**Recommended fix.** Drive `_screen_index` from the
`NavigationView::popped` signal instead of pre-decrementing. Or
disable the Back button after click until the pop animation
completes.

### F6 — No way to cancel an in-flight install

**Where.** `installer/frontend/gui/screens/progress.py:69-76`.

**What.** Once `Install` is clicked, the user can only wait. There's
no Cancel button. If the user realizes they clicked Install with
the wrong disk path, the only recovery is hard-poweroff. The Back
button is `set_visible(False)` and Next is `set_sensitive(False)`
until completion.

**Why MEDIUM not HIGH.** The orchestrator's destructive partition
write happens early in PHASE_PARTITION. Once that phase is past,
"cancel" is moot — the disk is already partitioned, the user has to
restart from scratch anyway. Adding a Cancel that aborts gracefully
in early phases (validate, partition-preview) and shows a confirm
in late phases (chroot, packages) is the right shape.

**Recommended fix.** Add Cancel button on Progress. Wires to
`self._worker_thread.cancel = True` (orchestrator polls a flag at
phase boundaries) + sets the page back to Confirm. If the cancel
arrives after PHASE_PARTITION_WRITE, show a "Disk has already been
modified — cancel will leave the target in an indeterminate state.
Continue install? [Cancel anyway / Continue]" dialog.

### F7 — `from installer.backend._validators import validate_hostname` inside `on_next`

**Where.** `installer/frontend/gui/screens/user.py:71`.

**What.** Import inside hot path. Every press of Next on the User
screen re-resolves the import. Module-level imports are this
module's pattern elsewhere.

**Why MEDIUM not LOW.** Performance impact is trivial. The
real reason this is worth fixing: it hints that other validators
(`validate_username` for F2 above) may end up scattered as inline
imports too. Consistent module-level imports make the validator
surface auditable in one place.

**Recommended fix.** Move to module-level import alongside Gtk
import.

---

## LOW / informational

### F8 — Done page has no "Reboot now" button

**Where.** `installer/frontend/gui/screens/done.py`.

**What.** Final-screen instruction is "Reboot, remove the install
media, and (if EFI) follow the MokManager prompts." User has to
open a terminal or hit the physical power button.

**Recommended fix.** Add a "Reboot now" button on success path that
calls `systemctl reboot` (or `subprocess.run(["reboot"])` with
appropriate error capture). Failure path keeps the Quit button.

### F9 — No package-group selection screen

**Where.** `installer/frontend/gui/state.py:64-66`,
`installer/frontend/gui/screens/__init__.py:26-34`.

**What.** `state.package_groups` defaults to `["core", "base",
"desktop-gnome"]`. There's no screen to change it. The TUI walking
sequence (per `state.py:31-32` docstring) "proposes" them but
provides override. The GUI exposes no override path.

**Why LOW.** This is intentional future-phase scope per the
`__init__.py:9-11` comment: "Phase 6 scope: text entry... Real
disk-detection... lands in a later visual-polish phase." A package-
selection screen is on the same future-phase track.

### F10 — `state.package_groups` membership check vs validation_errors mismatch

**Where.** `installer/frontend/gui/state.py:248-252`.

**What.** `validation_errors()` checks `"core" not in
(self.package_groups or [])` and adds an error if absent. But
`build_install_yaml()` already force-includes `core` via
`set(self.package_groups) | {"core"}`. So a user could never see
this validation error in practice unless they bypass
`build_install_yaml` — but `validation_errors()` is called by
`ConfirmPage.on_next` BEFORE the yaml builder. The check is a soft
warning that surfaces only if someone constructs a custom state
flow that pre-validates without building the yaml.

**Recommended fix.** Either drop the check (it's defended elsewhere)
or move the force-include into `__post_init__` so `package_groups`
is invariant-with-core-always-present from construction time.

### F11 — `_toast` falls through to `print()` on headless smoke tests

**Where.** `installer/frontend/gui/screens/_base.py:109-120`.

**What.** When `window.toast_overlay is None`, `_toast` prints to
stdout. Useful for test paths; benign in production. Flagging for
future-phase consideration: a structured test-callback pattern
(record the toast string in a list the test inspects) would be
more useful than a printf.

---

## Items NOT findings — verified clean

- **Password capture + cleanup.** `state.clear_sensitive_data()` is
  called from BOTH success and failure paths in
  `progress.py:221, 266`. Username and hostname intentionally NOT
  cleared (per the doc comment) so the Done page can render them.
- **MOK enrollment is optional.** Empty `mok_password` is a no-op
  per `state.to_install_io()`:185 which conditionally omits the key.
- **Integrity verification is conditional.** ProgressPage builds a
  `VerifyConfig` only if `/install/intergenos-archive-manifest.txt`
  + `/install/intergenos-release-key.asc` both exist. Dev/test
  ISOs without those skip the phase cleanly.
- **Thread-marshal for GTK updates.** All worker→main-loop event
  hops use `GLib.idle_add`, including the orchestrator's
  `progress_callback` re-routing in `_on_progress_from_worker`. No
  cross-thread widget access.
- **Welcome screen ack flag.** `on_load` sets
  `state.welcome_acked = True` so the state object reflects "user
  saw the welcome page" — useful for resume/restart auditing even
  though no business logic gates on it today.
- **Confirm screen summary surfaces every captured field.** Disk,
  hostname, username, keymap, locale, timezone, package_groups,
  MOK enrollment status — all rendered in the summary label
  (`confirm.py:46-58`). User has one visible chance to spot a
  typo before committing.

---

## Convergence path (recommended next runtime test)

After the build-host agent re-orients from pause + the `iso_include` mechanism
lands + a fresh ISO is rebuilt with `current_master` (post-
`ea6fdad2`), run the full click-through against that ISO. At that
point all the known cycle-3 issues are fixed:

- Portal-gtk env-loss → fixed in `fb96de5e` (this lane).
- 40+ auto-enabled services → fixed in the build-host agent's `1218d70a`.
- pkm setuid/sticky safety net → the build-host agent's `e452bbe9`.
- ISO-vs-MIRROR classification → fixed by the build-host agent consuming this
  lane's `docs/extra-tier-classification.md`.

The click-through against THAT ISO will surface any genuinely-new
Forge bugs (F1-F11 above + whatever else manifests at runtime that
static analysis missed) and ratify shipping. That runtime test is
the v1.0 ship gate.

---

Last updated: 2026-05-16 against master `ea6fdad2`.
