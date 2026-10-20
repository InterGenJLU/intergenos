"""A staged package action runs through its own tool, and an acceptance in
the person's own words is an acceptance.

The assistant session of 2026-10-19: "How do I update this system?" was
answered with the how-to and the offer to run it; the person's "yes" then ran
the offered line as a SHELL command — "sudo pkm sync && sudo pkm upgrade
--all" — and the safety tier blocked the sudo ("Privilege escalation isn't
really my thing"), while the package manager's own tool would have asked for
the authorization and done it. "How do I install steam" offered nothing: the
install how-tos carried no action, so "yes" had nothing to run. And "run that
install" over a standing offer lapsed it.

These tests hold: a staged package action is dispatched through
manage_packages with structured arguments, never a shell line; every install
how-to stages its action, so "how do I install steam" then "yes" installs; an
acceptance in the person's own words ("go ahead", "do it", "run that
install", "install it") over a live offer is the acceptance; "no" declines;
a prefixed yes with a new ask keeps the offer armed as before. The dispatch
is captured at the registry; the model is never called.
"""
from __future__ import annotations

from unittest import mock

import pytest

from intergen.tests.test_a_follow_up_that_asks_for_an_action_acts_on_it import (
    _router, capture)
from intergen.tests.test_compound_howto_routing import embedding_choice, entry


@pytest.fixture(params=["locked_floor", "native"])
def router(request):
    return _router(locked=request.param == "locked_floor")


INSTALL_STEAM = [("manage_packages", {"action": "install", "package": "steam"})]
UPDATE = [("manage_packages", {"action": "update"})]


def offer_steam(router):
    """"how do I install steam?" answered from the corpus, its action staged."""
    with embedding_choice(router, entry(router, "pkm-how-do-i-install-steam"), 0.9):
        answer = router.route("how do I install steam?")
    assert "sudo pkm install steam" in answer.text, answer.text
    assert router._conv.pending_action_offer is not None, answer.text
    return answer


def test_yes_to_the_update_offer_runs_the_package_tool(router):
    calls = capture(router)
    with embedding_choice(router, entry(router, "pkm-update-system"), 0.9):
        answer = router.route("How do I update this system?")
    assert "pkm upgrade" in answer.text, answer.text
    assert router._conv.pending_action_offer is not None
    result = router.route("yes")
    assert calls == UPDATE, result.text


def test_yes_to_the_install_how_to_installs(router):
    calls = capture(router)
    offer_steam(router)
    result = router.route("yes")
    assert calls == INSTALL_STEAM, result.text


@pytest.mark.parametrize("words", [
    "go ahead", "do it", "please do it", "run it", "yes, go ahead and do it",
    "run that install", "install it", "go ahead and install it",
])
def test_an_acceptance_in_the_persons_own_words_is_the_acceptance(router, words):
    calls = capture(router)
    offer_steam(router)
    result = router.route(words)
    assert calls == INSTALL_STEAM, (words, result.text)
    assert router._conv.pending_action_offer is None


def test_no_declines_and_nothing_runs(router):
    calls = capture(router)
    offer_steam(router)
    result = router.route("no")
    assert calls == [] and router._conv.pending_action_offer is None, result.text


def test_a_prefixed_yes_with_a_new_ask_keeps_the_offer_armed(router):
    calls = capture(router)
    offer_steam(router)
    router.route("yes, but first what time is it?")
    # The tail is answered on its own merits (the time, a read-only probe);
    # the staged install does not run and the offer stays armed, as before.
    assert all(name != "manage_packages" for name, _ in calls), calls
    assert router._conv.pending_action_offer is not None


def test_the_teachers_offer_runs_through_the_package_tool(router):
    calls = capture(router)
    with mock.patch.object(router._howto, "retrieve", return_value=(None, 0.0)):
        answer = router.route("how do I install neovim-qt?")
    assert "pkm install neovim-qt" in answer.text, answer.text
    assert router._conv.pending_action_offer is not None
    result = router.route("yes")
    assert calls == [("manage_packages", {"action": "install", "package": "neovim-qt"})], result.text


def test_every_install_how_to_stages_its_action(router):
    corpus = router._howto
    named = {e.id: e for e in corpus._entries}
    for ident, package in (("pkm-how-do-i-install-steam", "steam"),
                           ("pkm-how-do-i-install-google-chrome", "chrome"),
                           ("pkm-how-do-i-install-vscode", "vscode"),
                           ("pkm-how-do-i-install-spotify-on-linux", "spotify"),
                           ("pkm-how-do-i-install-discord-on-linux", "discord"),
                           ("pkm-how-do-i-install-zoom-on-linux", "zoom"),
                           ("pkm-how-do-i-install-vlc", "mpv")):
        action = named[ident].action
        assert action is not None, ident
        assert action.tool == "manage_packages", ident
        assert dict(action.arguments) == {"action": "install", "package": package}, ident
        assert action.command == f"sudo pkm install {package}", ident
    update = named["pkm-update-system"].action
    assert update is not None and dict(update.arguments) == {"action": "update"}
