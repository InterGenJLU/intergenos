# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""DNS guidance distinguishes available client tools from installed state."""

from intergen.tests.howto_content_helpers import answer


def test_dns_client_tools_are_available_through_bind_utils():
    text = answer("net-why-can-t-i-resolve-domain-names")
    assert "bind-utils" in text
    for tool in ("dig", "host", "nslookup"):
        assert tool in text
    assert "getent hosts" in text and "nmcli device show" in text
    assert "does not ship" not in text
    assert "no dig/nslookup" not in text
