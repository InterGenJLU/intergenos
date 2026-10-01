# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""No read-only command reaches the assistant's bus name in silence.

The neighbouring file test_the_command_line_starts_no_daemon_of_its_own.py
pins, in its own words, that "with no service on the bus, the command exits
non-zero, names the state of the user service and the command that starts it,
and constructs no assistant at all". Every case in that file drives `ask` or
`ask-frontier`, and each proves the last clause by replacing
intergen.dbus_daemon.InterGenDaemon with a raiser — a guard that can only see
an assistant built INSIDE this process.

That is not the only way the command line constructs one. The package installs
/usr/share/dbus-1/services/com.intergenos.InterGen.service (packages/ai/
intergen/build.sh), so the bus name is activatable: Name=com.intergenos.InterGen,
Exec=/usr/bin/intergen daemon, SystemdService=intergen.service. cli.try_dbus
calls with Gio.DBusCallFlags.NONE — auto-start allowed — so on a machine with
nothing owning the name, ANY try_dbus call starts the service: a model server,
the hardware probe, the lot. cmd_status says exactly this in its own docstring,
and handles it: it reads ownership BEFORE the call and prints one line saying
the call started the assistant. "What is not legitimate is doing it in
silence."

What is pinned here is the weakest form of that rule that still holds the
promise above — for each read-only subcommand, on a machine with nothing on the
bus, EITHER the command reaches for the bus name not at all, OR its output says
the call started the assistant. Passing it costs a correct command nothing:
`ask` and `reset` take the first branch, `status` takes the second.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from intergen import cli

# The sentence cmd_status prints when its own call did the starting. Either
# half is accepted as a disclosure; a command that says this in its own words
# passes too, which is why both forms are matched rather than one exact line.
DISCLOSURES = ("(D-Bus activation)", "the call started it")

# The read-only subcommands of `intergen`: the ones a person runs to find out
# what the machine is, which is exactly what they run when nothing is up.
READ_ONLY = (["status"], ["tier"], ["tools"], ["last"], ["--version"])

_STATUS = json.dumps({"running": True, "version": "0.1.0"})
_TIER = json.dumps({
    "level": 3, "ram_gb": 64.0, "gpu_vendor": "nvidia",
    "gpu_model": "a discrete card", "recommended_model": "Big Serving Model",
    "recommended_quant": "Q4_K_M", "estimated_model_size_gb": 20.0,
})


class _BuiltHere(AssertionError):
    """Raised if a command builds an assistant inside this process."""


def _run(argv_tail):
    """Run one subcommand on a machine with nothing owning the bus name.

    Returns (bus methods called, whether ownership was read before the first
    of them, everything the command printed).

    try_dbus is replaced by a recorder that ANSWERS the informational methods,
    because that is what a real activation produces: the bus starts the
    service and the freshly started service replies. Answering also keeps the
    case hermetic — no probe of this machine's hardware, no bus, no service.
    """
    events: list[str] = []
    out, err = io.StringIO(), io.StringIO()

    def _owner():
        events.append("owner-probe")
        return False

    def _call(method, *_args, **_kwargs):
        events.append(f"bus:{method}")
        return {"Status": _STATUS, "GetTier": _TIER}.get(method)

    def _refuse(*_a, **_k):
        raise _BuiltHere("an assistant was constructed in this process")

    import intergen.dbus_daemon as daemon_mod
    probe = mock.Mock(stdout="inactive", stderr="", returncode=0)
    with mock.patch.object(cli, "daemon_has_owner", _owner), \
            mock.patch.object(cli, "try_dbus", _call), \
            mock.patch.object(daemon_mod, "InterGenDaemon", _refuse), \
            mock.patch.object(cli.subprocess, "run", return_value=probe), \
            mock.patch("intergen.package_record.PKM_DB",
                       Path("/nonexistent/pkm.db")), \
            mock.patch.object(cli.sys, "argv", ["intergen"] + argv_tail):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                cli.main()
            except SystemExit:
                pass
    calls = [e.split(":", 1)[1] for e in events if e.startswith("bus:")]
    probed_first = bool(events) and events[0] == "owner-probe"
    return calls, probed_first, out.getvalue() + err.getvalue()


def _discloses(text: str) -> bool:
    return any(phrase in text for phrase in DISCLOSURES)


class NoSilentActivationTests(unittest.TestCase):

    def test_no_read_only_subcommand_activates_the_service_in_silence(self):
        for argv in READ_ONLY:
            with self.subTest(command=" ".join(argv)):
                calls, _probed, text = _run(list(argv))
                if not calls:
                    continue  # never reached for the bus name: nothing to say
                self.assertTrue(
                    _discloses(text),
                    f"`intergen {' '.join(argv)}` called {calls} on a machine "
                    "where nothing owned com.intergenos.InterGen. The name is "
                    "D-Bus activatable, so that call starts the service, and "
                    "the command printed no line saying so:\n" + text)

    def test_the_tier_command_does_not_reach_the_bus_in_silence(self):
        """The same property, named, for the command that breaks it."""
        calls, probed_first, text = _run(["tier"])
        self.assertTrue(
            _discloses(text),
            "`intergen tier` called " + str(calls) + " with nothing owning "
            "the bus name, which starts the service by D-Bus activation, and "
            "disclosed nothing. It cannot disclose: ownership was "
            + ("read first" if probed_first else "never read at all")
            + ", so the command does not know whether the tier it prints came "
            "from an assistant its own question started. What it printed:\n"
            + text)

    def test_the_status_command_shows_what_the_disclosure_costs(self):
        """The control: the sibling command passes the assertion above.

        Same machine, same recorder, same question shape — so a failure of the
        two cases above is about the command under test, not about the rule
        being unmeetable.
        """
        calls, probed_first, text = _run(["status"])
        self.assertEqual(calls, ["Status"])
        self.assertTrue(probed_first, "status reads ownership before it calls")
        self.assertTrue(_discloses(text), text)


if __name__ == "__main__":
    unittest.main()
