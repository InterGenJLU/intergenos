"""A follow-up that asks for an action acts on it.

The assistant session of 2026-10-19 on a laptop with the locked floor (52
turns): "can you install that for me?", "run that for me", "I need YOU to
install that package FOR me", "run that install", "can YOU do it for me?" —
fourteen turns referred to something the conversation had already named, and
each was routed past every code-owned route to the model's turn, which on the
locked floor can only talk. The person asked for one install fourteen times.

These tests hold the shapes of that session. A follow-up that asks for an
action resolves what it refers to from the conversation (the offer that just
lapsed, the last package either side named, the last command the assistant
showed, the person's own previous ask) and the resolved text re-enters the
code-owned ladder — the same keyword routes, the same gate — or, with nothing
to resolve, ONE question is asked and its answer is understood. The dispatch is
captured at the registry, never run; the model is never called.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from intergen.howto import HowtoCorpus
from intergen.interfaces.types import HardwareTierLevel, ToolResult
from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import (ConversationRouter, _package_action_teach,
                             depends_on_an_earlier_turn)
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

CORPUS = Path(__file__).resolve().parents[1] / "data" / "howto"

LPSTAT_HOWTO = ("To see the printers this machine knows about, run "
                "`lpstat -p -d` in a terminal. If a printer is listed you can "
                "print to it from any application.")
STEAM_HOWTO = ("Steam is available through the **steam download-helper "
               "package**:\n```\nsudo pkm install steam\n```\nThe helper "
               "package comes from the InterGenOS repository.")
CAPABILITY_QUIZ = ("Yes — I can install, remove, and update software packages "
                   "— you'll get a confirmation prompt before I make any "
                   "change. Just ask.")
TALKED_ABOUT_UPDATING = ("Okay, let's get this system updated. To do that, we "
                         "need to:\n\n1. Sync the package index.\n2. Upgrade "
                         "all installed packages.")


def _router(*, locked: bool) -> ConversationRouter:
    registry = ToolRegistry()
    registry.discover_tools()
    registry.execute = mock.Mock(side_effect=AssertionError("unexpected dispatch"))
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    llm = LLMRouter(config=None)
    llm.chat = mock.Mock(side_effect=AssertionError("unexpected model call"))
    llm.stream_with_tools = mock.Mock(
        side_effect=AssertionError("unexpected model call"))
    with mock.patch.dict(os.environ, {"INTERGEN_HOWTO_DIR": str(CORPUS)}):
        router = ConversationRouter(
            tool_registry=registry, semantic_matcher=matcher, llm=llm,
            hardware_tier=HardwareTierLevel.TIER_2, lock_dispatch=locked)
    router._howto = HowtoCorpus(embedder=None, data_dir=CORPUS)
    router._current_query_type = "general"
    return router


@pytest.fixture(params=["locked_floor", "native"])
def router(request):
    """Both floors: the locked one the session ran on, and the native one."""
    return _router(locked=request.param == "locked_floor")


def capture(router):
    """Every dispatch the ladder makes, captured at the registry and answered
    with a plain success — the gate and the tool are not under test here."""
    calls = []

    def execute(call, **_kwargs):
        calls.append((call.name, dict(call.arguments)))
        return ToolResult(call_id=call.call_id, name=call.name, content="ok",
                          success=True)

    router._tools.execute = mock.Mock(side_effect=execute)
    router._template_synthesis = mock.Mock(return_value="done")
    return calls


def said(router, person: str, assistant: str) -> None:
    router._append_history(person, assistant)


INSTALL_STEAM = [("manage_packages", {"action": "install", "package": "steam"})]
RUN_LPSTAT = [("run_command", {"command": "lpstat -p -d"})]


def test_install_that_resolves_to_the_package_the_person_named(router):
    calls = capture(router)
    said(router, "How do I install steam on this machine?", STEAM_HOWTO)
    result = router.route("can you install that for me please?")
    assert calls == INSTALL_STEAM, result.text


def test_install_that_package_after_the_person_named_it(router):
    calls = capture(router)
    said(router, "can YOU install the steam package FOR me?", CAPABILITY_QUIZ)
    result = router.route("I need YOU to install that package FOR me")
    assert calls == INSTALL_STEAM, result.text


def test_run_that_runs_the_command_the_assistant_showed(router):
    calls = capture(router)
    said(router, "is there a printer on this network?", LPSTAT_HOWTO)
    result = router.route("Can you run that for me?")
    assert calls == RUN_LPSTAT, result.text


def test_you_did_not_run_it_runs_it(router):
    calls = capture(router)
    said(router, "is there a printer on this network?", LPSTAT_HOWTO)
    said(router, "I need YOU to run that command FOR me",
         "You can run the command `lpstat -p -d` in the terminal.")
    result = router.route("you didn't run it- you just told me how to")
    assert calls == RUN_LPSTAT, result.text


def test_are_you_going_to_install_it_is_the_request(router):
    calls = capture(router)
    said(router, "can YOU install the steam package FOR me?", CAPABILITY_QUIZ)
    result = router.route("so, are you going to install it?")
    assert calls == INSTALL_STEAM, result.text


def test_you_cannot_install_it_for_me_is_the_request(router):
    calls = capture(router)
    said(router, "can YOU install the steam package FOR me?", CAPABILITY_QUIZ)
    result = router.route("you can't install it FOR me?")
    assert calls == INSTALL_STEAM, result.text


def test_still_not_installing_it_is_the_request(router):
    calls = capture(router)
    said(router, "I need YOU to install that package FOR me",
         "Here's how you can do it:\n```bash\npkm install steam\n```")
    result = router.route("you're still not installing it for me, why not?")
    assert calls == INSTALL_STEAM, result.text


def test_run_that_install_resolves_to_the_install_shown(router):
    calls = capture(router)
    said(router, "sudo pkm install steam", STEAM_HOWTO)
    said(router, "No, not to install steam- I don't even know how to open the "
                 "terminal- THAT is what I need you to tell me.",
         "Open a terminal from the Activities overview.")
    result = router.route("run that install")
    assert calls == INSTALL_STEAM, result.text


def test_do_it_for_me_does_what_was_asked_last_turn(router):
    calls = capture(router)
    said(router, "please update this system", TALKED_ABOUT_UPDATING)
    result = router.route("can YOU do it for me?")
    assert calls == [("manage_packages", {"action": "update"})], result.text


def test_with_nothing_to_resolve_one_question_is_asked_and_its_answer_understood(router):
    calls = capture(router)
    said(router, "hello", "Hi — what can I do for you?")
    first = router.route("can you install that for me please?")
    assert calls == [], first.text
    assert "which package" in first.text.lower(), first.text
    said(router, "can you install that for me please?", first.text)
    result = router.route("steam")
    assert calls == INSTALL_STEAM, result.text


def test_a_capability_frame_with_a_named_target_dispatches(router):
    calls = capture(router)
    result = router.route("can YOU install the steam package FOR me?")
    assert calls == INSTALL_STEAM, result.text


def test_a_bare_capability_question_is_still_answered_not_dispatched(router):
    calls = capture(router)
    result = router.route("can you install software?")
    assert result.source == "capability_question", result.text
    assert calls == []


def test_a_typed_command_is_acted_on(router):
    calls = capture(router)
    result = router.route("sudo pkm install steam")
    assert calls == INSTALL_STEAM, result.text


def test_a_typed_command_inside_a_sentence_is_acted_on(router):
    calls = capture(router)
    result = router.route("I want YOU to run the `sudo pkm install steam` "
                          "command FOR me to install the steam package")
    assert calls == INSTALL_STEAM, result.text


def test_remove_the_x_package_names_x(router):
    calls = capture(router)
    router.route("remove the steam package")
    assert calls == [("manage_packages", {"action": "remove", "package": "steam"})]
    calls.clear()
    router.route("install the steam package for me")
    assert calls == INSTALL_STEAM


def test_the_teacher_honours_negation_and_strips_punctuation():
    assert _package_action_teach(
        "no, not to install steam- i don't even know how to open the terminal"
    ) is None
    assert _package_action_teach("how do i install steam-")[1] == "steam"
    assert _package_action_teach("how do i install steam")[0] == "pkm install steam"


def test_this_system_names_the_machine():
    for fresh in ("update this system", "please update this system",
                  "is this machine up to date?"):
        assert not depends_on_an_earlier_turn(fresh), fresh
    for follow_up in ("run that for me?", "install that package",
                      "what about it?", "are those installed?"):
        assert depends_on_an_earlier_turn(follow_up), follow_up


def test_a_follow_up_that_asks_for_no_action_keeps_its_path(router):
    calls = capture(router)
    said(router, "what is a kernel?", "The kernel is the core of the system.")
    result = router.route("what about it?", decide_only=True)
    assert calls == []
    # The model's turn on either floor (the native one may offer it tools).
    assert result.source in ("llm_freeform", "llm_tools"), result.source
    assert not result.handled
