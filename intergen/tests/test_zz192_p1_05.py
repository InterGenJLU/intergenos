# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Item 192, P1-05 — the shell screenshot surface must authorise its caller.

intergen/panel/extension/extension.js publishes com.intergenos.InterGenShell on
the user's SESSION bus (extension.js:346) because the compositor is the one
place a full-frame grab is permitted on Wayland. The file says so itself, and
that comment is the promise this case holds it to:

    The one place a full-frame grab is permitted is inside the compositor
    process itself -- where this extension runs. So the extension exports its
    own tiny D-Bus surface with a single Screenshot method that drives the
    in-process Shell.Screenshot API and writes the PNG to a caller-supplied
    absolute path. The tool calls this method.

"The tool calls this method" is the whole of the access model, and the code
keeps no part of it:

  * _onShellMethodCall declares `sender` and never reads it, and nothing else
    on the path looks at who called. So EVERY peer on the user's session bus
    -- not only the InterGen daemon -- gets the full-frame grab that
    org.gnome.Shell.Screenshot answers AccessDenied for out-of-process, and
    gets it with none of the SafetyTier.CONFIRM consent that the daemon's own
    take_screenshot tool carries (intergen/tools/take_screenshot.py).
  * The same unauthenticated call names any absolute path it likes: the
    handler tests only GLib.path_is_absolute, _shellCaptureToFile replaces
    that path with PNG bytes, and the failure branch deletes it outright.

The surface is registered FIRST and unconditionally in enable(), ahead of the
readiness gate, so it is live for as long as the extension is enabled.

There is no Python-side unit for the GJS surface, so these pins read the
extension source the way ThePageShowsTheReasonTests in
test_the_kept_answer_says_why_it_differs_from_the_stream.py reads web/app.js.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_EXT_JS = (Path(__file__).resolve().parents[1]
           / "panel" / "extension" / "extension.js")

# The two in-args the surface ships today. A caller-supplied shared secret
# would have to arrive as a THIRD in-arg, so anything beyond these two counts
# as an authorisation token for the purposes of this case.
_SHIPPED_IN_ARGS = ("filename", "include_cursor")

# Any of these, anywhere on the capture path, would constrain the destination
# to somewhere the service owns rather than to whatever the caller names.
_DESTINATION_CONSTRAINTS = (
    "get_tmp_dir", "get_user_runtime_dir", "get_user_cache_dir",
    "XDG_RUNTIME_DIR", "startsWith", "has_prefix", "build_filenamev",
    "path_get_dirname", "dirname",
)

# Forms a caller check can take: reading the D-Bus sender, or asking the bus
# who is on the other end of the connection.
_CALLER_CHECKS = (
    "sender", "GetConnectionCredentials", "GetConnectionUnixProcessID",
    "GetConnectionUnixUser",
)


def _read_source() -> str:
    if not _EXT_JS.is_file():
        raise AssertionError(f"extension source not found at {_EXT_JS}")
    return _EXT_JS.read_text(encoding="utf-8")


def _match(text: str, start: int, opener: str, closer: str) -> tuple[int, int]:
    """Index of `opener` at/after `start` and of its matching `closer`."""
    i = text.index(opener, start)
    begin, depth = i, 0
    while True:
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                return begin, i
        i += 1


def _signature_and_body(src: str, name: str) -> tuple[str, str]:
    """The parameter list and the body of a top-level `function name(...)`."""
    start = src.index(f"function {name}(")
    p_open, p_close = _match(src, start, "(", ")")
    b_open, b_close = _match(src, p_close, "{", "}")
    return src[p_open + 1:p_close], src[b_open + 1:b_close]


def _iface_in_args(src: str) -> list[str]:
    xml = src.split("const SHELL_DBUS_IFACE = `", 1)[1].split("`", 1)[0]
    return re.findall(r'<arg[^>]*direction="in"[^>]*name="([^"]+)"', xml)


class TheSurfaceIsWhereThisCaseSaysItIs(unittest.TestCase):
    """Preconditions. These are GREEN: they prove the two red pins below are
    red about the real handler and not about a source parse that missed."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read_source()

    def test_the_session_bus_surface_is_registered(self):
        self.assertIn("com.intergenos.InterGenShell", self.src)
        self.assertIn("Gio.DBus.session.register_object", self.src)
        self.assertIn("Gio.bus_own_name_on_connection", self.src)

    def test_the_handler_body_was_extracted_whole(self):
        sig, body = _signature_and_body(self.src, "_onShellMethodCall")
        self.assertIn("sender", sig)               # it is a declared parameter
        self.assertIn("Unknown method", body)      # first statement reached
        self.assertIn("_shellCaptureToFile(", body)
        self.assertIn("_screenshotErrorReturn(invocation, msg)", body)  # last
        self.assertNotIn("INTERGEN_BUS", body)     # did not run past the end

    def test_the_interface_declares_the_two_in_args_this_case_assumes(self):
        self.assertEqual(_iface_in_args(self.src), list(_SHIPPED_IN_ARGS))


class TheScreenshotSurfaceAuthorisesItsCaller(unittest.TestCase):
    """The behaviour the comment promises. RED at this snapshot."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read_source()
        sig, cls.body = _signature_and_body(cls.src, "_onShellMethodCall")
        # `sender` in the parameter list is a declaration, not a check, so the
        # search for a caller check runs over the file with that list removed.
        cls.src_minus_sig = cls.src.replace(sig, "", 1)
        _cap_sig, cls.capture_body = _signature_and_body(
            cls.src, "_shellCaptureToFile")

    def test_the_screenshot_method_authorises_its_caller(self):
        """A full-frame grab the platform withholds from out-of-process
        clients must not be handed to any peer that asks for it."""
        checks_caller = [c for c in _CALLER_CHECKS if c in self.src_minus_sig]
        takes_secret = [a for a in _iface_in_args(self.src)
                        if a not in _SHIPPED_IN_ARGS]
        self.assertTrue(
            checks_caller or takes_secret,
            "com.intergenos.InterGenShell.Screenshot is published on the "
            "user's session bus and answers ANY caller: _onShellMethodCall "
            "declares `sender` and never reads it, nothing asks the bus who "
            "the peer is, and the interface carries no shared secret (in-args "
            f"{_iface_in_args(self.src)}). The comment above the surface says "
            "'The tool calls this method' and gives the grab as the one thing "
            "Wayland permits only inside the compositor -- yet any process on "
            "the session bus now gets the capture that "
            "org.gnome.Shell.Screenshot answers AccessDenied for, without the "
            "SafetyTier.CONFIRM consent the daemon's own take_screenshot tool "
            f"carries. Expected a check from {list(_CALLER_CHECKS)} on the "
            "path, or an authorisation in-arg; found neither.",
        )

    def test_the_capture_destination_is_not_any_path_the_caller_names(self):
        """The unauthenticated call must not be able to aim the write at an
        arbitrary file: _shellCaptureToFile replaces the named path, and the
        failure branch deletes it."""
        path_code = self.body + self.capture_body
        constrains = [t for t in _DESTINATION_CONSTRAINTS if t in path_code]
        names_no_path = "filename" not in _iface_in_args(self.src)
        self.assertTrue(
            constrains or names_no_path,
            "the only test applied to the caller's filename is "
            "GLib.path_is_absolute, so Screenshot('/home/<user>/.bashrc', "
            "true) is accepted: _shellCaptureToFile opens it with "
            "file.replace(null, false, FileCreateFlags.NONE, null) and the "
            "catch branch calls delete(null) on it, so the named file is "
            "replaced with PNG bytes or removed either way, with no prompt "
            "and no record. Expected the destination to be constrained to a "
            f"directory the service owns (one of {list(_DESTINATION_CONSTRAINTS)}) "
            "or the filename not to be a caller argument at all; found "
            "neither.",
        )


if __name__ == "__main__":
    unittest.main()
