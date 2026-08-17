# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""D-Bus client for the Forge installer backend service.

Architecture B (2026-05-25): the GUI runs as an unprivileged user and
calls into the root-privileged install backend over the system bus.
This module wraps that with a small client class so screens don't have
to deal with Gio.DBusConnection / GLib.Variant directly.

Signal callbacks fire on the GLib main loop, so widget updates inside
them are safe (no GLib.idle_add re-marshaling needed).
"""

from typing import Any, Callable, Dict, Optional

from gi.repository import Gio, GLib


BUS_NAME = "org.intergenos.ForgeInstaller1"
OBJECT_PATH = "/org/intergenos/ForgeInstaller1"
INTERFACE = "org.intergenos.ForgeInstaller1"


def _install_io_dict_of_variants(install_io: Dict[str, Any]) -> Dict[str, GLib.Variant]:
    """Build the dict-of-variants payload for a D-Bus a{sv} signature.

    Returns a Python `dict` whose VALUES are GLib.Variants (not native
    types). This is the shape the outer `Variant("(...a{sv}...)", ...)`
    constructor expects when composing a tuple containing an a{sv}
    field: the dict is treated as an a{sv} sub-payload and each
    value-variant carries its own type tag.
    """
    out: Dict[str, GLib.Variant] = {}
    for k, v in install_io.items():
        if isinstance(v, bool):
            out[k] = GLib.Variant("b", v)
        elif isinstance(v, int):
            out[k] = GLib.Variant("i", v)
        elif isinstance(v, str):
            out[k] = GLib.Variant("s", v)
        elif v is None:
            out[k] = GLib.Variant("s", "")
        else:
            # Best-effort string coercion for unexpected types so the
            # call doesn't reject; backend will see the stringified form.
            out[k] = GLib.Variant("s", str(v))
    return out


def _install_io_to_variant(install_io: Dict[str, Any]) -> GLib.Variant:
    """Standalone-Variant convenience wrapper around
    `_install_io_dict_of_variants` for callers that want the wrapped
    `a{sv}` form (vs. the dict-of-variants shape needed when composing
    into a larger tuple signature). The StartInstall caller uses the
    dict-shape helper directly because the outer tuple constructor
    wraps the a{sv} field itself."""
    return GLib.Variant("a{sv}", _install_io_dict_of_variants(install_io))


def _result_dict_from_variant(variant: GLib.Variant) -> Dict[str, Any]:
    """Inverse of _emit_complete_dict — turn the a{sv} payload back into
    a Python dict with native types."""
    raw = variant.unpack()
    return {
        "success": bool(raw.get("success", False)),
        "cancelled": bool(raw.get("cancelled", False)),
        "error_message": str(raw.get("error_message", "") or ""),
        "phase_completed": str(raw.get("phase_completed", "") or ""),
        "package_fail_count": int(raw.get("package_fail_count", 0) or 0),
        "integrity_overrides_granted": int(
            raw.get("integrity_overrides_granted", 0) or 0,
        ),
        "integrity_aborted_at": str(raw.get("integrity_aborted_at", "") or ""),
        "failed_packages": list(raw.get("failed_packages", []) or []),
    }


class ForgeInstallerClient:
    """Thin wrapper around Gio.DBusConnection to the install backend."""

    def __init__(self):
        self._bus: Optional[Gio.DBusConnection] = None
        self._progress_cb: Optional[Callable[[str, str, int, int, str], None]] = None
        self._integrity_cb: Optional[Callable[[str, str, str, str], None]] = None
        self._complete_cb: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self._subscription_ids: list[int] = []

    def connect(self) -> None:
        if self._bus is None:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    # ------------------------------------------------------------------
    # Signal subscriptions
    # ------------------------------------------------------------------
    def subscribe(
        self,
        on_progress: Optional[Callable[[str, str, int, int, str], None]] = None,
        on_integrity_warning: Optional[Callable[[str, str, str, str], None]] = None,
        on_complete: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.connect()
        self._progress_cb = on_progress
        self._integrity_cb = on_integrity_warning
        self._complete_cb = on_complete

        for sig in self._subscription_ids:
            self._bus.signal_unsubscribe(sig)
        self._subscription_ids.clear()

        if on_progress is not None:
            sid = self._bus.signal_subscribe(
                None, INTERFACE, "Progress", OBJECT_PATH, None,
                Gio.DBusSignalFlags.NONE, self._dispatch_progress,
            )
            self._subscription_ids.append(sid)

        if on_integrity_warning is not None:
            sid = self._bus.signal_subscribe(
                None, INTERFACE, "IntegrityWarning", OBJECT_PATH, None,
                Gio.DBusSignalFlags.NONE, self._dispatch_integrity,
            )
            self._subscription_ids.append(sid)

        if on_complete is not None:
            sid = self._bus.signal_subscribe(
                None, INTERFACE, "Complete", OBJECT_PATH, None,
                Gio.DBusSignalFlags.NONE, self._dispatch_complete,
            )
            self._subscription_ids.append(sid)

    def _dispatch_progress(self, _conn, _sender, _path, _iface, _signal, params):
        if self._progress_cb is None:
            return
        install_id, phase, current, total, message = params.unpack()
        self._progress_cb(install_id, phase, int(current), int(total), message)

    def _dispatch_integrity(self, _conn, _sender, _path, _iface, _signal, params):
        if self._integrity_cb is None:
            return
        install_id, package, expected, actual = params.unpack()
        self._integrity_cb(install_id, package, expected, actual)

    def _dispatch_complete(self, _conn, _sender, _path, _iface, _signal, params):
        if self._complete_cb is None:
            return
        install_id, payload = params.unpack()
        # payload is already a Python dict at this point; rewrap as Variant
        # for the canonicalizer (which expects a Variant).
        v = GLib.Variant("a{sv}", {
            k: (
                GLib.Variant("b", v)
                if isinstance(v, bool)
                else GLib.Variant("i", v)
                if isinstance(v, int)
                else GLib.Variant("a(ss)", [(str(a), str(b)) for a, b in v])
                if isinstance(v, list)
                else GLib.Variant("s", str(v) if v is not None else "")
            )
            for k, v in payload.items()
        })
        self._complete_cb(install_id, _result_dict_from_variant(v))

    # ------------------------------------------------------------------
    # Method calls
    # ------------------------------------------------------------------
    def start_install(
        self,
        yaml_content: str,
        install_io: Dict[str, Any],
        dry_run: bool = False,
    ) -> str:
        """Synchronous StartInstall — returns the backend's install_id.

        The actual install runs asynchronously in the backend; subscribe
        to Progress and Complete signals beforehand to observe it.
        """
        self.connect()
        # The outer Variant("(sa{sv}b)", ...) constructor wraps the
        # a{sv} sub-field itself, so we pass the dict-of-variants
        # SHAPE (not a pre-wrapped Variant nor a .unpack()-ed dict-of-
        # native-types). Earlier code wrapped + unpacked which fed
        # native-typed values to the constructor — that's what produced
        # "TypeError: argument value: Expected GLib.Variant, got str"
        # at runtime on install (2026-05-26 install attempt).
        result = self._bus.call_sync(
            BUS_NAME, OBJECT_PATH, INTERFACE, "StartInstall",
            GLib.Variant("(sa{sv}b)", (
                yaml_content,
                _install_io_dict_of_variants(install_io),
                bool(dry_run),
            )),
            GLib.VariantType.new("(s)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        return result.unpack()[0]

    def cancel_install(self, install_id: str) -> None:
        self.connect()
        self._bus.call_sync(
            BUS_NAME, OBJECT_PATH, INTERFACE, "CancelInstall",
            GLib.Variant("(s)", (install_id,)),
            None, Gio.DBusCallFlags.NONE, -1, None,
        )

    def ack_integrity_override(
        self, install_id: str, package: str, override_phrase: str,
    ) -> bool:
        self.connect()
        result = self._bus.call_sync(
            BUS_NAME, OBJECT_PATH, INTERFACE, "AckIntegrityOverride",
            GLib.Variant("(sss)", (install_id, package, override_phrase)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        return result.unpack()[0]

    def get_status(self) -> tuple[str, bool]:
        self.connect()
        result = self._bus.call_sync(
            BUS_NAME, OBJECT_PATH, INTERFACE, "GetStatus",
            None,
            GLib.VariantType.new("(sb)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        install_id, busy = result.unpack()
        return install_id, bool(busy)
