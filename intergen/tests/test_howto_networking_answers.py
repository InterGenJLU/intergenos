# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Networking guidance matches what the installed system ships: the DNS client
tools it offers, and the firewall's own turn-on and inspection steps."""

import re

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
    enable = ["sudo", "systemctl", "enable", "--now", "nftables.service"]
    assert enable in steps
    assert ["systemctl", "is-active", "nftables.service"] in steps
    assert ["sudo", "nft", "list", "ruleset"] in steps
    assert "/etc/nftables.conf" in text
    assert "/etc/intergenos-firewall-defaults" not in text
    # Masking nftables.service is the opt-out the firewall defaults name, and
    # enable --now refuses a masked unit: the turn-on step says how to tell
    # (is-enabled prints "masked") and unmasks first, in the same block as
    # the enable that follows it.
    unmask = ["sudo", "systemctl", "unmask", "nftables.service"]
    blocks = [commands(f"```\n{body}```") for body in re.findall(r"```[^\n]*\n(.*?)```", text, re.S)]
    assert any(block[i:i + 2] == [unmask, enable] for block in blocks for i in range(len(block))), blocks
    assert "`systemctl is-enabled nftables.service` says `masked`" in text
