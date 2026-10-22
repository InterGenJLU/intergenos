# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Compound teaching never promises an action whose offer slot was cleared."""
from unittest import mock

from intergen.tests.test_compound_howto_routing import COMPOUND, entry, router


def test_compound_teaching_has_no_offer_that_a_later_clause_clears(router):
    answer = router.route(COMPOUND)
    update_command = entry(router, "pkm-update-system").action.command
    assert update_command not in answer.text
    assert router._pending_action_offer is None
    assert "pkm sync" in answer.text and "pkm upgrade" in answer.text
    assert "pkm list" in answer.text


def test_package_instruction_fallback_in_a_compound_does_not_stage_an_offer(router):
    # pkm's resolver stood in: the fallback teaches only a name the package
    # index resolves, and this index holds the one name the case asks for.
    resolver = mock.Mock()
    resolver.return_value.get_package.side_effect = (
        lambda name: {"name": name} if name == "widget-editor" else None)
    with mock.patch.object(router, "_stage_action_offer_or_handoff",
                           wraps=router._stage_action_offer_or_handoff) as offer, \
            mock.patch("pkm.repo.RepoManager", resolver):
        answer = router.route(
            "how do I install widget-editor and how do I update my system")
    assert "pkm install widget-editor" in answer.text
    assert "pkm upgrade" in answer.text
    assert router._pending_action_offer is None
    offer.assert_not_called()
    router._tools.execute.assert_not_called()
