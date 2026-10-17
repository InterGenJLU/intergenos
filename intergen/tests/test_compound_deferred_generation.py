# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An instructional compound leaves model work to the streaming caller."""
from unittest import mock
import pytest
from intergen.interfaces.types import RouteResult
from intergen.tests.test_compound_howto_routing import router
@pytest.mark.parametrize('query',[
 'how do I check for updates and how do I calibrate the frobnicator',
 'how do I update the system?',
 'how do I calibrate the frobnicator',
])
def test_decide_only_never_generates(router,query):
 with mock.patch.object(router,'_try_llm_freeform',return_value=RouteResult(text='Generated explanation.',source='llm_freeform',handled=True,used_llm=True)) as generation, mock.patch.object(router,'_try_llm_tools',return_value=RouteResult(text='Generated explanation.',source='llm_tools',handled=True,used_llm=True)) as tools:
  result=router.route(query,decide_only=True)
 assert generation.call_count+tools.call_count==0
 router._tools.execute.assert_not_called()
def test_generation_instrument_detects_normal_generation(router):
 with mock.patch.object(router,'_try_llm_freeform',return_value=RouteResult(text='Generated explanation.',source='llm_freeform',handled=True,used_llm=True)) as generation, mock.patch.object(router,'_try_llm_tools',return_value=RouteResult(text='Generated explanation.',source='llm_tools',handled=True,used_llm=True)) as tools:
  result=router.route('how do I calibrate the frobnicator',decide_only=False)
 assert generation.call_count+tools.call_count==1
 assert result.used_llm


@pytest.mark.parametrize("query", [
    "how do I calibrate the frobnicator and how do I check for updates",
    "how do I check for updates and calibrate the frobnicator",
    "how do I calibrate the frobnicator and how do I tune the fluxometer",
])
def test_uncovered_compound_returns_a_generation_decision(router, query):
    with mock.patch.object(router, "_try_llm_freeform") as generation, \
            mock.patch.object(router, "_try_llm_tools") as tools:
        result = router.route(query, decide_only=True)
    generation.assert_not_called()
    tools.assert_not_called()
    assert result.source == "llm_freeform"
    assert not result.handled
    assert not result.used_llm
    assert result.text == ""
    router._tools.execute.assert_not_called()


def test_covered_compound_keeps_both_answers_during_route_decision(router):
    with mock.patch.object(router, "_try_llm_freeform") as generation, \
            mock.patch.object(router, "_try_llm_tools") as tools:
        result = router.route(
            "how do I check for updates and how do I list installed packages",
            decide_only=True)
    generation.assert_not_called()
    tools.assert_not_called()
    assert result.handled and result.source == "decomposed"
    assert "pkm upgrade" in result.text and "pkm list" in result.text
    assert not result.used_llm
    router._tools.execute.assert_not_called()


def test_normal_compound_still_combines_the_generated_explanation(router):
    answer = RouteResult(text="Generated explanation.", source="llm_freeform",
                         handled=True, used_llm=True)
    with mock.patch.object(router, "_try_llm_freeform", return_value=answer) as generation:
        result = router.route(
            "how do I check for updates and how do I calibrate the frobnicator")
    generation.assert_called_once_with("how do I calibrate the frobnicator")
    assert result.handled and result.source == "decomposed"
    assert result.used_llm
    assert "pkm upgrade" in result.text and "Generated explanation." in result.text
    router._tools.execute.assert_not_called()
