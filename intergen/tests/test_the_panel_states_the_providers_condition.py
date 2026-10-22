# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The panel's Providers tab states when a question goes to a provider.

Its description said "Nothing leaves this machine without your explicit
consent at send time", and with no provider configured the list said
"InterGen runs fully local until you add one below." A web search the person
asks for sends its words to the search service, a network check reaches the
host it names and the IP answer asks a public resolver, with or without a
provider, so both sentences denied an egress the assistant performs. Both now
say what the tab is about: a question goes to a provider only once one is set
up, and only with the person's approval of the exact content at send time.

The texts are read from the page's own files, without a browser.
"""

from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"


def test_the_description_states_the_provider_condition():
    html = " ".join((WEB / "dashboard.html").read_text(encoding="utf-8").split())
    assert "Nothing leaves this machine" not in html
    assert ("A question goes to the provider only with your explicit consent at "
            "send time (you review the exact outbound content first).") in html


def test_the_empty_list_states_the_provider_condition():
    js = (WEB / "dashboard.js").read_text(encoding="utf-8")
    assert "runs fully local" not in js
    assert ("No frontier provider configured. A question goes to a frontier "
            "model only once you add one below, and each send waits for your "
            "approval.") in js
