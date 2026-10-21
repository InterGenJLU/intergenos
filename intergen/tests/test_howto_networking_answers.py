# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Networking guidance matches what the installed system ships: the DNS client
tools it offers, and the firewall's own turn-on and inspection steps."""

from intergen.howto import HowtoCorpus
from intergen.tests.howto_content_helpers import DATA, answer, commands, entry


def test_dns_client_tools_are_available_through_bind_utils():
    text = answer("net-why-can-t-i-resolve-domain-names")
    assert "bind-utils" in text
    for tool in ("dig", "host", "nslookup"):
        assert tool in text
    assert "getent hosts" in text and "nmcli device show" in text
    assert "does not ship" not in text
    assert "no dig/nslookup" not in text


def test_the_turn_on_question_reaches_the_firewall_entry():
    found, score = HowtoCorpus(data_dir=DATA).retrieve("how do I turn on the firewall?")
    assert found is not None and found.id == "net-firewall", score


def test_the_firewall_entry_carries_the_turn_on_question():
    assert "how do I turn on the firewall" in entry("net-firewall").triggers


def test_the_firewall_answer_gives_the_turn_on_step():
    # nftables.service loads /etc/nftables.conf and its system preset enables
    # it; the answer to "how do I turn on the firewall?" gives that step, keeps
    # the inspection step, and names only paths the release ships.
    text = answer("net-firewall")
    steps = commands(text)
    assert ["sudo", "systemctl", "enable", "--now", "nftables.service"] in steps
    assert ["systemctl", "is-active", "nftables.service"] in steps
    assert ["sudo", "nft", "list", "ruleset"] in steps
    assert "/etc/nftables.conf" in text
    assert "/etc/intergenos-firewall-defaults" not in text
