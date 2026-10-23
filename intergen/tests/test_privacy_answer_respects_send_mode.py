# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The routed privacy answer reads the manager's current send refusal."""

from unittest import mock

import pytest

from intergen.escalation import EscalationManager
from intergen.tests.test_privacy_answer_from_the_machine import _routed


@pytest.mark.parametrize("locked", [True, False])
@pytest.mark.parametrize("mode, refused", [
    ("never", True), ("off", True),
    ("ask", False), ("fallback", False), ("auto", False),
])
def test_the_privacy_answer_matches_the_send_gate(locked, mode, refused):
    adapter = mock.Mock(side_effect=AssertionError("unexpected provider call"))
    manager = EscalationManager.from_config(
        {"mode": mode},
        [{"name": "example-cloud", "adapter": "openai", "model": "example",
          "api_key_keyring_id": "unused-test-key"}],
        adapter_factory=adapter,
    )
    router = _routed(locked=locked)
    router._escalation = manager
    result = router.route("is my data private?")
    assert result.source == "direct_answer"
    if refused:
        assert ("Phone-A-Friend is off here (escalation.mode), so nothing is "
                "sent to a cloud model.") in result.text
        assert "only after you approve" not in result.text
        assert "sends a question to example-cloud" not in result.text
    else:
        assert "Phone-A-Friend sends a question to example-cloud" in result.text
        assert "only after you approve that one send" in result.text
        assert "Phone-A-Friend is off here" not in result.text
    adapter.assert_not_called()
