# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A service change is proposed only for a unit the service manager knows.

THE DEFECT, read on the tree in intergen/router.py. The service keyword pattern
claims a sentence that starts with start, stop, restart, enable or disable, and
the argument builder took the next word as the service: "start over" became a
privileged proposal to start a service named "over", held for the person's
approval, and "restart everything" one for a service named "everything".
Nothing in either sentence names a service. Two more of the same shape: the
builder lowercased the word, so "restart NetworkManager" proposed
"networkmanager", a name the case-sensitive manager does not have, and it kept a
full stop, so "Restart sshd." proposed "sshd.".

THE RULE these cells hold the router to: a change (start, stop, restart, enable,
disable) is proposed only when the service manager knows the unit, and the
proposal names the unit as the manager does; otherwise nothing is proposed, the
reason is recorded in words on the decision trace, and the request goes on to
the model's turn as an ordinary request. A manager that cannot be read is a
failure, recorded in words, and nothing is proposed on it.

THE CONTROLS beside the cells are green at both ends: a unit the manager knows
is still proposed; "stop it" with nothing named still asks which service; a
status question is answered for any name and the manager is not asked about it.

Every case builds the router the way the daemon builds it. A proposal is read
where it is made: the tool registry's execute is stood in, records every call
it is handed and runs none. This machine's service manager is never asked: for
the length of a turn `subprocess.run` is a manager of the case's own, which
answers `systemctl show` and `systemctl list-unit-files` from a fixed set of
unit files, records every question and refuses any other command. The model is
stubbed at both entry points and counted, and every other way out of the
process is recorded and refused.
"""
from __future__ import annotations

import socket
import subprocess
import unittest
from unittest import mock

from intergen.dispatch_policy import DispatchMode, resolve_dispatch_for_model
from intergen.interfaces.semantic import MatchResult
from intergen.interfaces.types import HardwareTierLevel, ToolResult
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry
from intergen.tools.manage_services import _SERVICE_ALIASES

SYSTEMCTL = "/usr/bin/systemctl"
UNIT_FILES = ("NetworkManager.service", "bluetooth.service", "sshd.service")
WHICH_SERVICE = "Which service did you mean?"


class _BoundaryReached(BaseException):
    """Deliberately NOT an Exception: the model client's ``except Exception``
    paths would swallow an ordinary one and the case would walk on with the
    request already made."""


class _Resp:
    """A completion carrying only what the free-form path reads."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.quality_passed = True
        self.escalated = False
        self.local = True
        self.model = "stub"
        self.tokens_prompt = 0
        self.tokens_completion = 0
        self.semantic_flags = []


class _Manager:
    """A service manager of the case's own, standing in for ``subprocess.run``.

    It answers the two questions the router may put — the load state of one
    name, and the list of service unit files — from ``unit_files``, records
    each, and refuses every other command. A failure is chosen per case.
    """

    def __init__(self, unit_files=UNIT_FILES, *, show_raises=None,
                 show_rc=0, show_out=None, list_rc=0, list_out=None) -> None:
        self.unit_files = tuple(unit_files)
        self.show_raises = show_raises
        self.show_rc = show_rc
        self.show_out = show_out
        self.list_rc = list_rc
        self.list_out = list_out
        self.asked: list[list[str]] = []
        self.refused: list[str] = []

    def run(self, args, *a, **kw):
        argv = [str(x) for x in args] if not isinstance(args, str) else [args]
        if argv[:2] == [SYSTEMCTL, "show"]:
            self.asked.append(argv)
            if self.show_raises is not None:
                raise self.show_raises
            name = argv[-1]
            unit = name if name.endswith(".service") else name + ".service"
            out = self.show_out
            if out is None:
                out = ("loaded" if unit in self.unit_files else "not-found") + "\n"
            return subprocess.CompletedProcess(argv, self.show_rc, out, "")
        if argv[:2] == [SYSTEMCTL, "list-unit-files"]:
            self.asked.append(argv)
            out = self.list_out
            if out is None:
                out = "".join("%s enabled enabled\n" % u for u in self.unit_files)
            return subprocess.CompletedProcess(
                argv, self.list_rc, out, "" if self.list_rc == 0 else "failed")
        self.refused.append(" ".join(argv))
        raise OSError("a command this case's manager does not answer, refused")

    def names_asked(self) -> list[str]:
        return [argv[-1] for argv in self.asked if argv[1] == "show"]


class _Case(unittest.TestCase):
    """The router, the case's manager and one turn, as every case uses them."""

    def _router(self, tier: HardwareTierLevel = HardwareTierLevel.TIER_2,
                matcher: SemanticMatcher | None = None) -> ConversationRouter:
        """A router built the way the daemon builds it: tools discovered, intents
        registered, the dispatch posture asked of the product's resolver."""
        from intergen.intents import register_all_intents
        reg = ToolRegistry()
        reg.discover_tools()
        self.dispatched: list[tuple[str, dict]] = []

        def _execute(call, **kw):
            # The registry is where a call becomes a proposal: record it, run
            # nothing, and answer as a refusal so the turn carries on.
            self.dispatched.append((call.name, dict(call.arguments or {})))
            return ToolResult(call_id=call.call_id, name=call.name,
                              content="not run by this case", success=False,
                              executed=False)

        reg.execute = _execute
        if matcher is None:
            matcher = SemanticMatcher(embedder=None)
            register_all_intents(matcher)
        locked = resolve_dispatch_for_model(
            tier, detected_tier=tier).dispatch_mode is DispatchMode.LOCKED_DOWN
        r = ConversationRouter(
            tool_registry=reg, semantic_matcher=matcher, llm=LLMRouter(config=None),
            lock_dispatch=locked, hardware_tier=tier, memory=None)
        self.model_calls: list[str] = []

        def _chat(messages, **kw):
            self.model_calls.append("chat")
            return _Resp("(a model's answer)")

        def _stream_with_tools(messages, *, tools=(), **kw):
            self.model_calls.append("stream_with_tools")
            yield "(a model's answer)"

        r._llm.chat = _chat
        r._llm.stream_with_tools = _stream_with_tools
        return r

    def _turn(self, request: str, manager: _Manager | None = None,
              call=None):
        """One turn through route() (or ``call``) on a router built before it,
        this case's manager standing in for every command and every other way
        out of the process recorded and refused for its length."""
        route = call if call is not None else self._router().route
        self.manager = manager if manager is not None else _Manager()
        self.processes: list[str] = []
        self.connections: list[str] = []
        self.urls: list[str] = []
        self.glass: list[tuple[str, str, dict]] = []

        def _popen(args, *a, **kw):
            self.processes.append(args if isinstance(args, str)
                                  else " ".join(map(str, args)))
            raise OSError("a child process, refused by this case")

        def _connect(sock, address, *a, **kw):
            self.connections.append(repr(address))
            raise OSError("a connection, refused by this case")

        def _urlopen(req, *a, **kw):
            self.urls.append(getattr(req, "full_url", repr(req)))
            raise _BoundaryReached(self.urls[-1])

        def _emit(phase, event, *, detail=None, **kw):
            self.glass.append((phase, event, dict(detail or {})))

        with mock.patch("subprocess.run", self.manager.run), \
                mock.patch("subprocess.Popen", _popen), \
                mock.patch.object(socket.socket, "connect", _connect), \
                mock.patch("urllib.request.urlopen", _urlopen), \
                mock.patch("intergen.glass.emit", _emit):
            return route(request)

    def _service_calls(self) -> list[dict]:
        """Every service call the registry was handed this turn."""
        return [args for name, args in self.dispatched
                if name == "manage_services"]

    def _declined(self, event: str) -> list[dict]:
        return [d for (p, e, d) in self.glass if (p, e) == ("decision", event)]

    def assertNothingLeft(self, request: str) -> None:
        self.assertEqual(
            (self.manager.refused, self.processes, self.connections, self.urls),
            ([], [], [], []), "%r ran or reached something" % request)

    def assertNotProposed(self, request: str, *, reason: str,
                          words: str, manager: _Manager | None = None) -> None:
        """No service change proposed; the decline recorded with its reason
        and in words; the request answered by the model's turn."""
        result = self._turn(request, manager)
        self.assertEqual(
            self._service_calls(), [],
            "%r was proposed as a service change: %r"
            % (request, self._service_calls()))
        declined = [d for d in self._declined("dispatch_declined")
                    if d.get("tool") == "manage_services"]
        self.assertEqual(
            [(d.get("reason"), d.get("path")) for d in declined],
            [(reason, "code_owned_intent")],
            "the decline of %r was not recorded once with its reason: %r"
            % (request, declined))
        self.assertIn(words, declined[0].get("why", ""),
                      "the decline of %r does not say why in words: %r"
                      % (request, declined[0]))
        self.assertEqual(
            [d.get("reason") for d in self._declined("keyword_dispatch_declined")],
            [reason],
            "the keyword route's own decline of %r does not carry the reason"
            % request)
        self.assertTrue(result.handled, "%r went unanswered" % request)
        self.assertNotEqual(result.source, "keyword",
                            "%r was answered by the keyword route" % request)
        self.assertTrue(self.model_calls,
                        "%r did not reach the model's turn" % request)
        self.assertEqual(self.processes, [],
                         "%r started a process" % request)


# ── the cells: red before the correction ────────────────────────────────────

class ASentenceThatNamesNoUnitIsNotProposed(_Case):
    """A word the manager does not know is no service, and nothing is proposed."""

    def test_start_over(self) -> None:
        self.assertNotProposed(
            "start over", reason="service_unit_not_known",
            words="the service manager knows no unit named 'over'")
        self.assertEqual(self.manager.names_asked(), ["over"])
        self.assertNothingLeft("start over")

    def test_restart_everything(self) -> None:
        self.assertNotProposed(
            "restart everything", reason="service_unit_not_known",
            words="the service manager knows no unit named 'everything'")
        self.assertNothingLeft("restart everything")

    def test_restart_a_unit_this_machine_does_not_have(self) -> None:
        self.assertNotProposed(
            "restart frobnicatord", reason="service_unit_not_known",
            words="the service manager knows no unit named 'frobnicatord'")
        self.assertNothingLeft("restart frobnicatord")


class TheProposalNamesTheUnitAsTheManagerDoes(_Case):
    """The name proposed is the manager's, not a lowercased or punctuated word."""

    def assertProposes(self, request: str, action: str, service: str) -> None:
        self._turn(request)
        self.assertEqual(self._service_calls(),
                         [{"action": action, "service": service}],
                         "%r did not propose %s %s" % (request, action, service))
        self.assertNothingLeft(request)

    def test_restart_NetworkManager(self) -> None:
        self.assertProposes("restart NetworkManager", "restart", "NetworkManager")

    def test_restart_networkmanager_typed_in_lower_case(self) -> None:
        self.assertProposes("restart networkmanager", "restart", "NetworkManager")

    def test_restart_sshd_with_a_full_stop(self) -> None:
        self.assertProposes("Restart sshd.", "restart", "sshd")


class AManagerThatCannotBeReadIsAFailureNotAnAnswer(_Case):
    """A read that fails proposes nothing and says what failed."""

    def test_a_manager_that_cannot_be_asked(self) -> None:
        self.assertNotProposed(
            "restart sshd", reason="service_manager_unreadable",
            words="the service manager could not be asked about 'sshd'",
            manager=_Manager(show_raises=FileNotFoundError(2, "No such file")))

    def test_a_manager_that_answers_with_nothing(self) -> None:
        self.assertNotProposed(
            "restart sshd", reason="service_manager_unreadable",
            words="show exited 0 without a load state for 'sshd'",
            manager=_Manager(show_out=""))

    def test_a_unit_file_listing_that_fails(self) -> None:
        self.assertNotProposed(
            "restart networkmanager", reason="service_manager_unreadable",
            words="list-unit-files exited 1",
            manager=_Manager(list_rc=1, list_out=""))


class TheSemanticRouteProposesNothingForIt(_Case):
    """The argument builder is shared: a semantic match of the same sentence to
    the service intent proposes nothing either."""

    def test_start_over_matched_by_meaning(self) -> None:
        matcher = SemanticMatcher(embedder=None)
        matcher._match_embeddings = lambda q: MatchResult(
            intent_id="manage_services", score=0.9, layer="embedding",
            tool_name="manage_services")
        r = self._router(matcher=matcher)
        result = self._turn("start over", call=r._try_semantic_match)
        self.assertEqual(self._service_calls(), [],
                         "the semantic route proposed %r"
                         % self._service_calls())
        self.assertFalse(result.handled)
        self.assertNothingLeft("start over")


# ── the controls: green at both ends ────────────────────────────────────────

class AUnitTheManagerKnowsIsStillProposed(_Case):

    def assertProposed(self, request: str, action: str) -> dict:
        result = self._turn(request)
        calls = self._service_calls()
        self.assertEqual([c.get("action") for c in calls], [action],
                         "%r was not proposed as a %s: %r"
                         % (request, action, calls))
        self.assertEqual(result.source, "keyword",
                         "%r left the keyword route" % request)
        self.assertEqual(self.model_calls, [], "%r reached the model" % request)
        self.assertNothingLeft(request)
        return calls[0]

    def test_start_sshd(self) -> None:
        self.assertEqual(self.assertProposed("start sshd", "start")["service"],
                         "sshd")

    def test_start_the_ssh_service(self) -> None:
        service = self.assertProposed("start the ssh service", "start")["service"]
        self.assertEqual(_SERVICE_ALIASES.get(service, service), "sshd",
                         "the proposal does not name the unit sshd")

    def test_restart_NetworkManager_is_proposed(self) -> None:
        self.assertProposed("restart NetworkManager", "restart")


class NothingNamedStillAsksWhichOne(_Case):

    def test_stop_it(self) -> None:
        result = self._turn("stop it")
        self.assertEqual(self._service_calls(), [])
        self.assertIn(WHICH_SERVICE, result.text)
        self.assertEqual(self.manager.asked, [],
                         "the manager was asked about a referent")
        self.assertNothingLeft("stop it")


class AStatusQuestionIsNotAskedAbout(_Case):

    def test_is_over_running(self) -> None:
        self._turn("is over running")
        self.assertEqual(self._service_calls(),
                         [{"action": "status", "service": "over"}])
        self.assertEqual(self.manager.asked, [],
                         "the manager was asked about a status question")


# ── the reader itself (the function is new: no reading at the base) ────────

class TheReaderAsksTheManagerAndNothingElse(unittest.TestCase):

    def _read(self, name: str, manager: _Manager):
        from intergen.router import read_service_unit
        self.manager = manager
        with mock.patch("subprocess.run", manager.run):
            return read_service_unit(name)

    def test_a_loaded_unit_is_known_by_its_own_name(self) -> None:
        m = _Manager()
        self.assertEqual(self._read("sshd", m), ("known", "sshd", ""))
        self.assertEqual(m.asked, [[SYSTEMCTL, "show", "--property=LoadState",
                                    "--value", "--", "sshd"]])

    def test_a_unit_known_under_another_case_is_named_as_the_manager_names_it(self) -> None:
        m = _Manager()
        self.assertEqual(self._read("networkmanager", m),
                         ("known", "NetworkManager", ""))
        self.assertEqual([a[1] for a in m.asked], ["show", "list-unit-files"])
        self.assertEqual(self._read("networkmanager.service", _Manager()),
                         ("known", "NetworkManager.service", ""))

    def test_a_name_the_manager_does_not_know(self) -> None:
        self.assertEqual(self._read("over", _Manager()),
                         ("unknown", "",
                          "the service manager knows no unit named 'over'"))

    def test_two_units_that_differ_only_in_case_are_no_answer(self) -> None:
        state, unit, words = self._read(
            "foo", _Manager(("Foo.service", "FOO.service")))
        self.assertEqual((state, unit), ("unknown", ""))
        self.assertIn("more than one unit", words)

    def test_a_glob_or_an_empty_name_asks_nothing(self) -> None:
        for name, words in (("over?", "'over?' is not a unit name"),
                            ("sshd*", "'sshd*' is not a unit name"),
                            ("", "no service was named")):
            m = _Manager()
            self.assertEqual(self._read(name, m), ("unknown", "", words))
            self.assertEqual(m.asked, [], "%r was put to the manager" % name)

    def test_a_show_that_fails_is_unreadable(self) -> None:
        for manager, words in (
                (_Manager(show_rc=1, show_out=""), "show exited 1"),
                (_Manager(show_out=""), "show exited 0 without a load state"),
                (_Manager(show_raises=FileNotFoundError(2, "No such file")),
                 "could not be asked"),
                (_Manager(show_raises=subprocess.TimeoutExpired(SYSTEMCTL, 5)),
                 "could not be asked")):
            state, unit, said = self._read("sshd", manager)
            self.assertEqual((state, unit), ("unreadable", ""), said)
            self.assertIn(words, said)

    def test_a_listing_that_fails_or_is_empty_is_unreadable(self) -> None:
        for manager, words in (
                (_Manager(list_rc=1, list_out=""), "exited 1 with 0 unit files"),
                (_Manager(list_out=""), "exited 0 with 0 unit files")):
            state, unit, said = self._read("networkmanager", manager)
            self.assertEqual((state, unit), ("unreadable", ""), said)
            self.assertIn(words, said)


if __name__ == "__main__":
    unittest.main()
