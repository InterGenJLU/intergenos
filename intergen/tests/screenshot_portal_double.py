# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A screenshot portal on a private message bus, for the cases that drive the
real capture code without a compositor.

A capture through the portal is D-Bus from end to end except for the dialog:
the request the tool sends, the handle it subscribes to before sending it, the
Response signal that answers it, the Close that ends a request nobody answered,
and the bus's own rule about who may answer. Each of those is real here — a
dbus-daemon of the case's own, the GDBus connections the tool uses, and the
portal's interfaces as the installed descriptions define them, so a request
whose arguments do not match the installed interface is refused by GDBus
exactly as the real portal's would be. Only the dialog is stood in for: the
double answers a request itself, the way the portal does once a person has
pressed the dialog's capture button, or stays silent, the way a dialog nobody
answers does.

The bus is private. Its configuration lists no service directories, so nothing
is activated on it, and nothing here reaches the session bus of the machine
running the suite.
"""

from __future__ import annotations

import selectors
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from intergen.tools import take_screenshot as ts  # noqa: E402

INTERFACE_DIR = Path("/usr/share/dbus-1/interfaces")
SCREENSHOT_XML = INTERFACE_DIR / "org.freedesktop.portal.Screenshot.xml"
REQUEST_XML = INTERFACE_DIR / "org.freedesktop.portal.Request.xml"

_BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC
 "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:dir={directory}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""

_START_TIMEOUT_S = 10.0


def unavailable_reason() -> str | None:
    """Why the double cannot run on this machine, or None when it can."""
    if shutil.which("dbus-daemon") is None:
        return "dbus-daemon is not installed, so no private bus can be started"
    for xml in (SCREENSHOT_XML, REQUEST_XML):
        if not xml.is_file():
            return f"the portal's interface description {xml} is not installed"
    return None


def _interface(xml: Path, name: str) -> Gio.DBusInterfaceInfo:
    info = Gio.DBusNodeInfo.new_for_xml(xml.read_text()).lookup_interface(name)
    if info is None:
        raise RuntimeError(f"{xml} does not describe {name}")
    return info


class PrivateBus:
    """A dbus-daemon of the case's own, listening in a temporary directory."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="intergen-portal-bus-")
        config = Path(self._tmp.name) / "bus.conf"
        config.write_text(_BUS_CONFIG.format(directory=self._tmp.name))
        self._proc = subprocess.Popen(
            ["dbus-daemon", f"--config-file={config}", "--nofork",
             "--print-address=1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        selector = selectors.DefaultSelector()
        selector.register(self._proc.stdout, selectors.EVENT_READ)
        ready = selector.select(timeout=_START_TIMEOUT_S)
        selector.close()
        if not ready:
            self.close()
            raise RuntimeError("the private bus printed no address in "
                               f"{_START_TIMEOUT_S:g} s")
        self.address = self._proc.stdout.readline().strip()
        if not self.address:
            stderr = self._proc.stderr.read() if self._proc.poll() is not None \
                else ""
            self.close()
            raise RuntimeError(f"the private bus did not start: {stderr}")

    def connect(self) -> Gio.DBusConnection:
        return Gio.DBusConnection.new_for_address_sync(
            self.address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            self._proc.wait(timeout=_START_TIMEOUT_S)
        for stream in (self._proc.stdout, self._proc.stderr):
            if stream is not None:
                stream.close()
        self._tmp.cleanup()


@dataclass
class Answer:
    """How the double answers the next request.

    ``code`` and ``uri`` are the Response the portal sends; ``silent`` sends
    none, as a dialog nobody answers does. ``forged_uri`` first has a peer that
    does NOT hold the portal's name send a Response of its own to the same
    handle. ``close_fails`` makes the request refuse a Close.
    """

    code: int = 0
    uri: str | None = None
    silent: bool = False
    forged_uri: str | None = None
    close_fails: bool = False


class PortalDouble:
    """Holds org.freedesktop.portal.Desktop on a private bus and answers
    Screenshot requests the way the portal does.

    It runs on a thread of its own with its own main context, as the portal
    runs in a process of its own; the tool under test runs on the case's
    thread. Every request is recorded with the sender and the options it
    carried, and every Close with the handle it named.
    """

    def __init__(self, bus: PrivateBus) -> None:
        self._bus = bus
        self._screenshot = _interface(SCREENSHOT_XML, ts.PORTAL_SCREENSHOT_IFACE)
        self._request = _interface(REQUEST_XML, ts.PORTAL_REQUEST_IFACE)
        self._ready = threading.Event()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="screenshot-portal-double")
        self._registrations: dict[str, int] = {}
        self.answer = Answer()
        self.requests: list[dict] = []
        self.closed: list[str] = []

    # -- the case's side ----------------------------------------------------
    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(_START_TIMEOUT_S):
            raise RuntimeError("the portal double did not come up")
        if self._failure is not None:
            raise RuntimeError(f"the portal double failed to start: "
                               f"{self._failure}")

    def reset(self, answer: Answer | None = None) -> None:
        self.answer = answer or Answer()
        self.requests.clear()
        self.closed.clear()

    def stop(self) -> None:
        def quit_loop(*_args) -> bool:
            self._loop.quit()
            return False

        source = GLib.idle_source_new()
        source.set_callback(quit_loop)
        source.attach(self._context)
        self._thread.join(_START_TIMEOUT_S)

    # -- the portal's side --------------------------------------------------
    def _run(self) -> None:
        self._context = GLib.MainContext.new()
        self._context.push_thread_default()
        try:
            self._conn = self._bus.connect()
            # A peer on the same bus that does not hold the portal's name.
            self._forger = self._bus.connect()
            self._conn.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "RequestName",
                GLib.Variant("(su)", (ts.PORTAL_BUS_NAME, 4)),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None)
            self._conn.register_object_with_closures2(
                ts.PORTAL_OBJ_PATH, self._screenshot, self._on_screenshot,
                None, None)
            self._loop = GLib.MainLoop.new(self._context, False)
        except BaseException as exc:  # reported to start(), never swallowed
            self._failure = exc
            self._ready.set()
            self._context.pop_thread_default()
            return
        self._ready.set()
        self._loop.run()
        for registration in self._registrations.values():
            self._conn.unregister_object(registration)
        self._forger.close_sync(None)
        self._conn.close_sync(None)
        self._context.pop_thread_default()

    def _on_screenshot(self, conn, sender, _path, _iface, _method, params,
                       invocation) -> None:
        parent_window, options = params.unpack()
        token = options.get("handle_token", "")
        handle = (f"{ts.PORTAL_REQUEST_PATH_PREFIX}/"
                  f"{sender.lstrip(':').replace('.', '_')}/{token}")
        self.requests.append({"sender": sender, "parent_window": parent_window,
                              "options": options, "handle": handle})
        self._registrations[handle] = conn.register_object_with_closures2(
            handle, self._request, self._on_request, None, None)
        invocation.return_value(GLib.Variant("(o)", (handle,)))
        answer = self.answer
        if answer.silent:
            return

        def respond(*_args) -> bool:
            if answer.forged_uri is not None:
                self._send_response(self._forger, sender, handle, 0,
                                    answer.forged_uri)
                # The bus handles one connection's messages in order, so once
                # this round trip returns, the forged Response is already on
                # its way to the tool, ahead of the portal's own.
                self._forger.call_sync(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "GetId", None,
                    GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, -1, None)
            self._send_response(self._conn, sender, handle, answer.code,
                                answer.uri)
            return False

        # After the method's reply is on the wire, as the portal answers only
        # once the dialog it opened has been dealt with.
        source = GLib.timeout_source_new(10)
        source.set_callback(respond)
        source.attach(self._context)

    @staticmethod
    def _send_response(conn, destination, handle, code, uri) -> None:
        results = {} if uri is None else {"uri": GLib.Variant("s", uri)}
        conn.emit_signal(destination, handle, ts.PORTAL_REQUEST_IFACE,
                         "Response", GLib.Variant("(ua{sv})", (code, results)))
        conn.flush_sync(None)

    def _on_request(self, conn, _sender, path, _iface, method, _params,
                    invocation) -> None:
        if method != "Close":
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        self.closed.append(path)
        if self.answer.close_fails:
            invocation.return_dbus_error(
                "org.freedesktop.portal.Error.Failed",
                "the request could not be closed")
            return
        registration = self._registrations.pop(path, None)
        if registration is not None:
            conn.unregister_object(registration)
        invocation.return_value(None)
