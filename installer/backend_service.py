# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Forge installer D-Bus backend service.

Architecture B (2026-05-25): Forge's GTK4 GUI runs as the unprivileged
`intergenos` user. The actual install — partitioning, filesystem creation,
package extraction, chroot configuration, bootloader install — runs in
this separate root-privileged backend service, dispatched via the system
D-Bus. Reason: Wayland deliberately denies pkexec'd-root processes the
input-grab capability needed by GTK popovers (Adw.ComboRow,
Gtk.DropDown, Adw.Toast, popovermenu, dialogs). Running the GUI as user
sidesteps the policy entirely while keeping privileged work auditable
behind a small, well-defined D-Bus interface.

Bus name        : org.intergenos.ForgeInstaller1
Object path     : /org/intergenos/ForgeInstaller1
Interface       : org.intergenos.ForgeInstaller1
Bus type        : SYSTEM
Activation      : on-demand via D-Bus activation (no always-running daemon),
                  and only on the install medium: the unit starts only when
                  the medium's trust set or its dev marker is present, and
                  this service refuses every method call (and refuses to
                  start) where neither is (integrity.on_install_media). An
                  installed system has neither, so nothing here acts there.
Caller policy   : the bus policy admits root, the administrators' group and,
                  on the medium, the live account (a policy the live overlay
                  writes at boot). Each acting method (StartInstall,
                  CancelInstall, AckIntegrityOverride) then asks polkit, per
                  call, whether its caller may perform the action
                  org.intergenos.forge.install: an administrator's password
                  by default; the medium's own rule grants the live account
                  at its local, active session. GetStatus is a read. A dry
                  run is refused: it is a preview a client makes without
                  this service.

State model — at most ONE concurrent install ever. StartInstall returns
an opaque install_id (UUID4 string); subsequent CancelInstall /
AckIntegrityOverride calls reference it. The service refuses a second
StartInstall while an install is in progress.

Integrity-warning flow (Phase 4 audit-protected override):
    1. Backend's verify phase finds a hash mismatch.
    2. Backend emits IntegrityWarning(install_id, package, expected, actual).
    3. GUI shows the paste-disabled phrase entry.
    4. GUI calls AckIntegrityOverride(install_id, package, phrase).
    5. Backend's ack thread looks up the expected phrase, compares, returns
       (bool accepted). The install worker thread is blocked on this
       result; on accepted=True it continues, on False it aborts.

Cancel flow:
    1. GUI calls CancelInstall(install_id).
    2. Backend sets the worker's cancel_event.
    3. Worker observes at next phase boundary, aborts cleanly, emits Complete
       with cancelled=True.
"""

import logging
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from gi.repository import Gio, GLib

from installer.backend import install as backend_install
from installer.backend import integrity as backend_integrity


LOG = logging.getLogger("forge.backend_service")

BUS_NAME = "org.intergenos.ForgeInstaller1"
OBJECT_PATH = "/org/intergenos/ForgeInstaller1"
INTERFACE = "org.intergenos.ForgeInstaller1"

DEFAULT_ARCHIVE_DIR = "/var/lib/igos/archives"
# get_group_packages() in installer/backend/packages.py expects this dir
# to have TIER-SUBDIR layout: <packages_dir>/<tier>/<pkg-name>/.
# /var/lib/igos/packages is the pkm package-DB directory which has FLAT
# layout (<pkg-name>-<version>/) — wrong shape for tier-aware group
# resolution, every group resolves to zero archives + install fails with
# "archive resolution error". The correct path is the installer-hooks
# tree shipped by the Forge package's do_install — that's the tier-aware
# layout already on disk. forge-tui.service passes this explicitly via
# `--packages /usr/share/intergenos/installer-hooks`; the GUI backend
# service unit `forge-installer-backend.service` doesn't pass any flag
# so it uses this default. Operator-flagged 2026-05-26 ~07:47 CDT after
# install attempt #3 surfaced the issue once the D-Bus shape bugs were
# clear of the way.
DEFAULT_PACKAGES_DIR = "/usr/share/intergenos/installer-hooks"
DEFAULT_YAML_DIR = "/var/lib/forge"

# The polkit action every acting method asks for, per call
# (packages/desktop/forge/polkit/org.intergenos.forge.policy).
POLKIT_ACTION = "org.intergenos.forge.install"
ACTING_METHODS = frozenset(("StartInstall", "CancelInstall", "AckIntegrityOverride"))
ERROR_NOT_ON_MEDIUM = "org.intergenos.ForgeInstaller1.Error.NotOnInstallMedium"
ERROR_NOT_AUTHORIZED = "org.intergenos.ForgeInstaller1.Error.NotAuthorized"
ERROR_DRY_RUN_REFUSED = "org.intergenos.ForgeInstaller1.Error.DryRunRefused"

INTROSPECTION_XML = """
<node>
  <interface name="org.intergenos.ForgeInstaller1">
    <method name="StartInstall">
      <arg name="yaml_content"   type="s"    direction="in"/>
      <arg name="install_io"     type="a{sv}" direction="in"/>
      <arg name="dry_run"        type="b"    direction="in"/>
      <arg name="install_id"     type="s"    direction="out"/>
    </method>
    <method name="CancelInstall">
      <arg name="install_id"     type="s"    direction="in"/>
    </method>
    <method name="AckIntegrityOverride">
      <arg name="install_id"     type="s"    direction="in"/>
      <arg name="package"        type="s"    direction="in"/>
      <arg name="override_phrase" type="s"   direction="in"/>
      <arg name="accepted"       type="b"    direction="out"/>
    </method>
    <method name="GetStatus">
      <arg name="install_id"     type="s"    direction="out"/>
      <arg name="busy"           type="b"    direction="out"/>
    </method>
    <signal name="Progress">
      <arg name="install_id"     type="s"/>
      <arg name="phase"          type="s"/>
      <arg name="current"        type="i"/>
      <arg name="total"          type="i"/>
      <arg name="message"        type="s"/>
    </signal>
    <signal name="IntegrityWarning">
      <arg name="install_id"     type="s"/>
      <arg name="package"        type="s"/>
      <arg name="expected_sha"   type="s"/>
      <arg name="actual_sha"     type="s"/>
    </signal>
    <signal name="Complete">
      <arg name="install_id"     type="s"/>
      <arg name="result"         type="a{sv}"/>
    </signal>
  </interface>
</node>
"""

# Idle timeout (seconds) after install completion; the service exits so
# systemd / D-Bus activation re-spawns it fresh next time. Short window
# is intentional: a stale long-lived root daemon is exactly what we're
# trying NOT to be.
IDLE_TIMEOUT_AFTER_COMPLETE = 30


class _IntegrityAckBridge:
    """Bridges the backend's synchronous ack_callback(package) -> bool to
    the D-Bus async signal + method-call ack flow.

    The backend's PHASE_VERIFY worker calls our `ack_callback`, which
    blocks on a queue.Queue waiting for the GUI's AckIntegrityOverride
    method-call handler to push the result. Per-package queues so multiple
    warnings (rare but possible) don't interleave.
    """

    def __init__(self):
        self._queues: Dict[str, "queue.Queue[bool]"] = {}
        self._lock = threading.Lock()

    def get_queue(self, package: str) -> "queue.Queue[bool]":
        with self._lock:
            q = self._queues.get(package)
            if q is None:
                q = queue.Queue(maxsize=1)
                self._queues[package] = q
            return q

    def submit(self, package: str, accepted: bool) -> None:
        q = self.get_queue(package)
        # Drain any stale residue then push the new value.
        while not q.empty():
            try:
                q.get_nowait()
            except queue.Empty:
                break
        q.put(accepted, block=False)

    def wait(self, package: str, timeout_seconds: float = 600.0) -> bool:
        q = self.get_queue(package)
        try:
            return q.get(timeout=timeout_seconds)
        except queue.Empty:
            return False


class PolkitAuthority:
    """Asks polkit, per call, whether the sender of a method call may perform
    an action: CheckAuthorization on the system bus with the caller's unique
    bus name as the subject, so polkit resolves that caller's process and
    user from the bus itself rather than from a pid this service read.
    User interaction is allowed, so an authentication agent can prompt; the
    call is asynchronous, so this service keeps serving while a prompt is
    open, and it waits as long as the person takes."""

    NAME = "org.freedesktop.PolicyKit1"
    PATH = "/org/freedesktop/PolicyKit1/Authority"
    INTERFACE = "org.freedesktop.PolicyKit1.Authority"
    ALLOW_USER_INTERACTION = 0x1
    TIMEOUT_MS = GLib.MAXINT32

    def __init__(self, bus):
        self._bus = bus

    def check(self, sender: str, action_id: str, on_result) -> None:
        """Calls on_result(authorized, reason) once polkit has decided; an
        error from polkit or the bus is a refusal, never a grant."""
        subject = ("system-bus-name", {"name": GLib.Variant("s", sender)})
        args = GLib.Variant("((sa{sv})sa{ss}us)", (
            subject, action_id, {}, self.ALLOW_USER_INTERACTION, "",
        ))

        def _done(bus, res, _data=None):
            try:
                reply = bus.call_finish(res)
            except GLib.Error as exc:
                on_result(False, f"polkit could not decide: {exc.message}")
                return
            (authorized, _challenge, _details), = reply.unpack()
            on_result(bool(authorized),
                      None if authorized else "polkit did not authorize it")

        self._bus.call(
            self.NAME, self.PATH, self.INTERFACE, "CheckAuthorization", args,
            GLib.VariantType.new("((bba{ss}))"), Gio.DBusCallFlags.NONE,
            self.TIMEOUT_MS, None, _done, None,
        )


class ForgeInstallerService:
    """D-Bus service implementation. One instance per process."""

    def __init__(self, archive_dir: str = DEFAULT_ARCHIVE_DIR,
                 packages_dir: str = DEFAULT_PACKAGES_DIR,
                 medium_root: Path = Path("/"), authority=None):
        self._archive_dir = archive_dir
        self._packages_dir = packages_dir
        # The root the install-medium check reads (integrity.on_install_media)
        # and the medium's trust set is read from; a scratch root in tests.
        self._medium_root = Path(medium_root)
        # Asks polkit per acting call; made on the bus connection in run().
        self._authority = authority

        self._loop: Optional[GLib.MainLoop] = None
        self._bus: Optional[Gio.DBusConnection] = None
        self._reg_id: Optional[int] = None
        self._owner_id: Optional[int] = None

        self._worker_thread: Optional[threading.Thread] = None
        self._worker_install_id: Optional[str] = None
        self._cancel_event = threading.Event()
        self._ack_bridge = _IntegrityAckBridge()
        self._lock = threading.Lock()
        self._idle_exit_source: Optional[int] = None

    # ------------------------------------------------------------------
    # D-Bus lifecycle
    # ------------------------------------------------------------------
    def run(self) -> int:
        self._loop = GLib.MainLoop()
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as exc:
            LOG.error("could not connect to system bus: %s", exc.message)
            return 2
        if self._authority is None:
            self._authority = PolkitAuthority(self._bus)

        node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        interface_info = node_info.interfaces[0]

        try:
            self._reg_id = self._bus.register_object(
                OBJECT_PATH,
                interface_info,
                self._on_method_call,
                None,
                None,
            )
        except GLib.Error as exc:
            LOG.error("register_object failed: %s", exc.message)
            return 3

        self._owner_id = Gio.bus_own_name_on_connection(
            self._bus,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            None,
            self._on_name_lost,
        )

        LOG.info("forge backend service ready on system bus")
        self._loop.run()
        return 0

    def _on_name_lost(self, _conn, _name):
        LOG.error("lost bus name; shutting down")
        if self._loop is not None:
            self._loop.quit()

    # ------------------------------------------------------------------
    # Method dispatch
    # ------------------------------------------------------------------
    def _on_method_call(self, _conn, sender, _path, _iface, method, params,
                        invocation):
        # Cancel any pending idle-exit; an active call means we're not idle.
        if self._idle_exit_source is not None:
            GLib.source_remove(self._idle_exit_source)
            self._idle_exit_source = None

        # Off the install medium nothing here acts: every method is refused
        # before its arguments are read (the unit does not start there either).
        if not backend_integrity.on_install_media(self._medium_root):
            LOG.warning("%s from %s refused: this is not the install medium",
                        method, sender)
            invocation.return_dbus_error(
                ERROR_NOT_ON_MEDIUM,
                "the Forge installer backend acts only on the install medium; "
                "this system carries neither the medium's trust set nor its "
                "dev marker under /install",
            )
            return

        # A dry run is a preview the client makes; this service never runs
        # one, so nothing it starts can act as root under the name of one.
        if method == "StartInstall" and bool(params.unpack()[2]):
            LOG.warning("StartInstall from %s refused: a dry run", sender)
            invocation.return_dbus_error(
                ERROR_DRY_RUN_REFUSED,
                "the installer backend runs no dry run; a dry run is a "
                "preview the client makes without it",
            )
            return

        if method in ACTING_METHODS:
            self._authority.check(
                sender, POLKIT_ACTION,
                lambda authorized, reason: self._after_authorization(
                    authorized, reason, sender, method, params, invocation),
            )
            return
        self._dispatch(sender, method, params, invocation)

    def _after_authorization(self, authorized, reason, sender, method, params,
                             invocation):
        if not authorized:
            LOG.warning("%s from %s refused: %s (%s)", method, sender,
                        reason or "not authorized", POLKIT_ACTION)
            invocation.return_dbus_error(
                ERROR_NOT_AUTHORIZED,
                f"{method} needs the {POLKIT_ACTION} authorization: "
                f"{reason or 'not authorized'}",
            )
            return
        self._dispatch(sender, method, params, invocation)

    def _dispatch(self, sender, method, params, invocation):
        try:
            if method == "StartInstall":
                yaml_content, install_io_variant, dry_run = params.unpack()
                install_id = self._handle_start_install(
                    sender, yaml_content, install_io_variant, bool(dry_run),
                )
                invocation.return_value(GLib.Variant("(s)", (install_id,)))
            elif method == "CancelInstall":
                install_id, = params.unpack()
                self._handle_cancel_install(install_id)
                invocation.return_value(None)
            elif method == "AckIntegrityOverride":
                install_id, package, phrase = params.unpack()
                accepted = self._handle_ack_integrity(install_id, package, phrase)
                invocation.return_value(GLib.Variant("(b)", (accepted,)))
            elif method == "GetStatus":
                with self._lock:
                    iid = self._worker_install_id or ""
                    busy = self._worker_thread is not None and self._worker_thread.is_alive()
                invocation.return_value(GLib.Variant("(sb)", (iid, busy)))
            else:
                invocation.return_dbus_error(
                    "org.freedesktop.DBus.Error.UnknownMethod",
                    f"Unknown method {method}",
                )
        except Exception as exc:
            LOG.exception("method %s failed", method)
            invocation.return_dbus_error(
                "org.intergenos.ForgeInstaller1.Error.InternalError",
                f"{type(exc).__name__}: {exc}",
            )

    # ------------------------------------------------------------------
    # StartInstall
    # ------------------------------------------------------------------
    def _handle_start_install(self, sender: str, yaml_content: str,
                              install_io: Dict[str, Any],
                              dry_run: bool) -> str:
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                raise RuntimeError(
                    "an install is already in progress "
                    f"(install_id={self._worker_install_id})"
                )

            install_id = uuid.uuid4().hex
            self._worker_install_id = install_id
            self._cancel_event = threading.Event()

            yaml_path = Path(DEFAULT_YAML_DIR) / "install.yaml"
            yaml_path.parent.mkdir(parents=True, exist_ok=True)
            yaml_path.write_text(yaml_content, encoding="utf-8")
            os.chmod(yaml_path, 0o600)

            LOG.info(
                "StartInstall id=%s sender=%s dry_run=%s yaml=%s",
                install_id, sender, dry_run, yaml_path,
            )

            self._worker_thread = threading.Thread(
                target=self._install_worker,
                args=(install_id, yaml_path, install_io, dry_run),
                daemon=True,
                name=f"forge-install-{install_id[:8]}",
            )
            self._worker_thread.start()

        return install_id

    def _install_worker(self, install_id: str, yaml_path: Path,
                        install_io: Dict[str, Any], dry_run: bool) -> None:
        try:
            verify_config = self._build_verify_config(install_id, dry_run)
        except backend_integrity.ReleaseMediaIntegrityError as exc:
            # Fail-closed: real release media missing its trust set. Emit a
            # terminal failure (mirrors the run_install except below) and stop
            # BEFORE run_install — no disk write happens.
            LOG.error("install refused (install-integrity): %s", exc)
            self._emit_complete(install_id, success=False, cancelled=False,
                                error=f"install-integrity: {exc}")
            self._mark_worker_done()
            return

        kwargs: Dict[str, Any] = {
            "yaml_path": str(yaml_path),
            "install_io": install_io,
            "archive_dir": self._archive_dir,
            "packages_dir": self._packages_dir,
            "progress_callback": (
                lambda phase, cur, total, msg: self._emit_progress(
                    install_id, phase, cur, total, msg,
                )
            ),
            "dry_run": dry_run,
            "cancel_event": self._cancel_event,
            # Plumb the D-Bus install_id through so the verbose forensic
            # trace (FORGE_DEBUG_VERBOSE=1) names its log file by this id,
            # making install logs cross-correlatable with D-Bus signals.
            "install_id": install_id,
        }
        if verify_config is not None:
            kwargs["verify_config"] = verify_config

        try:
            result = backend_install.run_install(**kwargs)
        except Exception as exc:
            LOG.exception("install worker raised")
            self._emit_complete(install_id, success=False, cancelled=False,
                                error=f"{type(exc).__name__}: {exc}")
            self._mark_worker_done()
            return

        result_dict = self._result_to_dict(result)
        self._emit_complete_dict(install_id, result_dict)
        self._mark_worker_done()

    def _result_to_dict(self, result) -> Dict[str, Any]:
        failed_packages = list(getattr(result, "failed_packages", []) or [])
        return {
            "success": bool(getattr(result, "success", False)),
            "cancelled": bool(getattr(result, "cancelled", False)),
            "error_message": str(getattr(result, "error_message", "") or ""),
            "phase_completed": str(getattr(result, "phase_completed", "") or ""),
            "package_fail_count": int(getattr(result, "package_fail_count", 0) or 0),
            "integrity_overrides_granted": int(
                getattr(result, "integrity_overrides_granted", 0) or 0,
            ),
            "integrity_aborted_at": str(
                getattr(result, "integrity_aborted_at", "") or "",
            ),
            "failed_packages": failed_packages,
            # The recovery key the disk phase generated, when the person
            # asked for one: carried to the GUI for the one time it is
            # shown (its Done page), and nowhere else. Empty when none.
            # It must cross this boundary in the completion payload: the
            # GUI runs in another process and has no other path to the
            # install result.
            "luks_recovery_key": str(
                getattr(result, "luks_recovery_key", "") or "",
            ),
        }

    def _mark_worker_done(self):
        with self._lock:
            self._worker_thread = None
        # Schedule idle-exit. If another StartInstall comes in before timeout,
        # _on_method_call cancels the source.
        self._idle_exit_source = GLib.timeout_add_seconds(
            IDLE_TIMEOUT_AFTER_COMPLETE, self._on_idle_exit,
        )

    def _on_idle_exit(self):
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                # Race: a new install started during the idle window.
                self._idle_exit_source = None
                return False
        LOG.info("idle timeout reached; exiting (D-Bus activation respawns)")
        if self._loop is not None:
            self._loop.quit()
        return False

    # ------------------------------------------------------------------
    # CancelInstall
    # ------------------------------------------------------------------
    def _handle_cancel_install(self, install_id: str) -> None:
        with self._lock:
            if self._worker_install_id != install_id:
                LOG.warning("CancelInstall: unknown install_id %s", install_id)
                return
            LOG.info("CancelInstall: id=%s", install_id)
            self._cancel_event.set()

    # ------------------------------------------------------------------
    # Integrity ack
    # ------------------------------------------------------------------
    def _build_verify_config(self, install_id: str, dry_run: bool):
        """Mirror tui._build_verify_config_if_present + gui progress.py
        equivalent, but with the warning/ack callbacks routed through
        D-Bus signals + method-call rendezvous."""
        if dry_run:
            return None

        medium = self._medium_root
        manifest_path = medium / "install/intergenos-archive-manifest.txt"
        pubkey_path = medium / "install/intergenos-release-key.asc"
        audit_log_path = Path("/var/log/igos-integrity-override.log")
        # Shared fail-closed policy (install-integrity §4C). On real release
        # media missing its trust set this RAISES ReleaseMediaIntegrityError;
        # the worker (caller) converts that into a terminal failure event
        # before any disk write. "skip-dev" => explicit dev marker => None.
        decision = backend_integrity.install_media_trust_decision(
            manifest_path, pubkey_path,
            medium / "install/IGOS_DEV_ALLOW_UNVERIFIED",
        )
        if decision == "skip-dev":
            return None

        def warning_cb(package: str, expected_sha: str, actual_sha: str):
            self._emit_integrity_warning(install_id, package, expected_sha, actual_sha)

        def ack_cb(package: str) -> bool:
            # Block this worker-thread call until GUI submits an answer or
            # 10 min elapses (in which case we default to reject).
            return self._ack_bridge.wait(package, timeout_seconds=600.0)

        return backend_install.VerifyConfig(
            manifest_path=manifest_path,
            public_key_path=pubkey_path,
            audit_log_path=audit_log_path,
            warning_callback=warning_cb,
            ack_callback=ack_cb,
        )

    def _handle_ack_integrity(self, install_id: str, package: str,
                              phrase: str) -> bool:
        with self._lock:
            if self._worker_install_id != install_id:
                LOG.warning(
                    "AckIntegrityOverride: unknown install_id %s", install_id,
                )
                return False

        expected = backend_integrity.expected_override_phrase(package)
        accepted = (phrase == expected)
        self._ack_bridge.submit(package, accepted)
        LOG.info(
            "AckIntegrityOverride: id=%s pkg=%s accepted=%s",
            install_id, package, accepted,
        )
        return accepted

    # ------------------------------------------------------------------
    # Signal emit
    # ------------------------------------------------------------------
    def _emit_progress(self, install_id: str, phase: str, current: int,
                       total: int, message: str):
        # Tee every progress update to journal so forensics survive a
        # backend crash / GUI exit / D-Bus disconnect. The install
        # pipeline only calls progress_callback (not LOG.info) at phase
        # boundaries; without this mirror, the journal stays empty for
        # the entire install (10+ minutes of work) — surfaced 2026-05-26
        # during install attempt #7 when the only forensic trail was
        # "StartInstall accepted" → "idle timeout reached" with nothing
        # in between. Phase/current/total/message format mirrors the
        # D-Bus Progress signal so journal greppers and GUI log-pane
        # observers see the same sequence.
        LOG.info(
            "Progress install=%s phase=%s %d/%d %s",
            install_id, phase, int(current), int(total), str(message or ""),
        )
        if self._bus is None:
            return
        self._bus.emit_signal(
            None, OBJECT_PATH, INTERFACE, "Progress",
            GLib.Variant("(ssiis)", (
                install_id, str(phase), int(current), int(total),
                str(message or ""),
            )),
        )

    def _emit_integrity_warning(self, install_id: str, package: str,
                                expected: str, actual: str):
        if self._bus is None:
            return
        self._bus.emit_signal(
            None, OBJECT_PATH, INTERFACE, "IntegrityWarning",
            GLib.Variant("(ssss)", (install_id, package, expected, actual)),
        )

    def _emit_complete(self, install_id: str, success: bool, cancelled: bool,
                       error: str = ""):
        self._emit_complete_dict(install_id, {
            "success": success,
            "cancelled": cancelled,
            "error_message": error,
            "phase_completed": "",
            "package_fail_count": 0,
            "integrity_overrides_granted": 0,
            "integrity_aborted_at": "",
            "failed_packages": [],
            "luks_recovery_key": "",
        })

    def _emit_complete_dict(self, install_id: str, result: Dict[str, Any]):
        if self._bus is None:
            return

        def _v_str(s):
            return GLib.Variant("s", str(s or ""))

        def _v_bool(b):
            return GLib.Variant("b", bool(b))

        def _v_int(i):
            return GLib.Variant("i", int(i or 0))

        failed = result.get("failed_packages") or []
        failed_variant = GLib.Variant("a(ss)", [
            (str(n), str(m)) for n, m in failed
        ])

        # Dict-of-variants SHAPE (Python dict, values are GLib.Variants).
        # The outer Variant("(sa{sv})", ...) constructor wraps the a{sv}
        # sub-field itself; pre-wrapping the dict in a Variant("a{sv}", ...)
        # makes the outer constructor try to iterate the wrapped Variant
        # as a Python iterable, hitting Variant.__getitem__(0) which
        # raises "Must be string, not int" then KeyError: 0 (the actual
        # crash signature seen on 2026-05-26 install attempt). Same bug
        # shape as dbus_client.py:166 fixed at 91c4bfd7 — pre-wrap-then-
        # consume-as-dict — but on the emission side instead of the call
        # side. `failed_variant` STAYS pre-wrapped because it's a VALUE
        # inside the dict (the `v` in `a{sv}` is the variant tag).
        dict_payload = {
            "success": _v_bool(result.get("success", False)),
            "cancelled": _v_bool(result.get("cancelled", False)),
            "error_message": _v_str(result.get("error_message", "")),
            "phase_completed": _v_str(result.get("phase_completed", "")),
            "package_fail_count": _v_int(result.get("package_fail_count", 0)),
            "integrity_overrides_granted": _v_int(result.get(
                "integrity_overrides_granted", 0,
            )),
            "integrity_aborted_at": _v_str(result.get("integrity_aborted_at", "")),
            "failed_packages": failed_variant,
            # Never logged here or anywhere in this service: the only
            # reader is the GUI's completion handler. Who may receive this
            # signal at all is set by the shipped bus policy
            # (installer/data/org.intergenos.ForgeInstaller1.conf): root
            # and the administrators' group; every other connection is
            # denied receipt of the service's messages.
            "luks_recovery_key": _v_str(result.get("luks_recovery_key", "")),
        }

        self._bus.emit_signal(
            None, OBJECT_PATH, INTERFACE, "Complete",
            GLib.Variant("(sa{sv})", (install_id, dict_payload)),
        )


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    LOG.info("forge backend service starting (uid=%d)", os.geteuid())

    if os.geteuid() != 0:
        LOG.error("forge backend service must run as root (uid 0); refusing")
        return 1

    # The unit's start condition is the same check; this covers a start that
    # does not go through the unit.
    if not backend_integrity.on_install_media():
        LOG.error("forge backend service runs only on the install medium "
                  "(neither its trust set nor its dev marker is under "
                  "/install); refusing to start")
        return 1

    service = ForgeInstallerService()
    return service.run()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
