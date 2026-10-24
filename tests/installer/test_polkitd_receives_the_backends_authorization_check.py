# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""polkitd can receive the installer backend's authorization check.

The backend asks polkit, per acting call, with CheckAuthorization on the
system bus (backend_service.PolkitAuthority), on the same connection that owns
the backend's bus name. The backend's policy denies receiving that name's
messages in its default context, and polkitd runs as its own account, neither
root nor in wheel. Without a rule of its own the bus refuses polkitd the call,
polkit never answers, and every install on the medium is refused before
anything is written ("polkit could not decide: ... Rejected receive message").

dbus-daemon decides a receive by the receiving connection's rules in this
order: the default context, then the connection's groups, then its user, then
the mandatory context; the last rule that matches the message wins
(dbus-daemon(1), the <policy> element). These tests apply that order to the
shipped policy file and the system bus's default receive rules, for messages
the backend really sends. They read the files; the running bus is proven by an
install on the medium.
"""

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from installer import backend_service

REPO = Path(__file__).resolve().parents[2]
CONF = (Path(backend_service.__file__).resolve().parent
        / "data" / f"{backend_service.BUS_NAME}.conf")

# The receive rules of the system bus's default context as dbus ships them in
# system.conf: every message type may be received unless a later rule says not.
SYSTEM_DEFAULTS = """<busconfig><policy context="default">
  <allow receive_type="method_call"/>
  <allow receive_type="method_return"/>
  <allow receive_type="error"/>
  <allow receive_type="signal"/>
</policy></busconfig>"""

# The authorization check as backend_service.PolkitAuthority sends it.
CHECK = {
    "receive_sender": backend_service.BUS_NAME,
    "receive_type": "method_call",
    "receive_path": backend_service.PolkitAuthority.PATH,
    "receive_interface": backend_service.PolkitAuthority.INTERFACE,
    "receive_member": "CheckAuthorization",
}

# The signal that carries an install's result, and an encrypted install's
# recovery key.
COMPLETE = {
    "receive_sender": backend_service.BUS_NAME,
    "receive_type": "signal",
    "receive_path": backend_service.OBJECT_PATH,
    "receive_interface": backend_service.BUS_NAME,
    "receive_member": "Complete",
}


def _rules_for(user, groups):
    """The rules dbus-daemon applies to a connection of `user` in `groups`,
    in the order it applies them."""
    policies = (ET.fromstring(SYSTEM_DEFAULTS).findall("policy")
                + ET.parse(CONF).getroot().findall("policy"))
    order = {"default": [], "group": [], "user": [], "mandatory": []}
    for policy in policies:
        if policy.get("context") in ("default", "mandatory"):
            order[policy.get("context")].append(policy)
        elif policy.get("group") is not None:
            if policy.get("group") in groups:
                order["group"].append(policy)
        elif policy.get("user") == user:
            order["user"].append(policy)
    for kind in ("default", "group", "user", "mandatory"):
        for policy in order[kind]:
            yield from policy


def may_receive(user, groups, message):
    decision = False
    for rule in _rules_for(user, groups):
        attrs = rule.attrib
        if not attrs or not all(name.startswith("receive_") for name in attrs):
            continue  # not a receive rule
        if all(message.get(name) == value for name, value in attrs.items()):
            decision = rule.tag == "allow"
    return decision


class PolkitdReceivesTheAuthorizationCheck(unittest.TestCase):

    def test_polkitd_may_receive_the_backends_authorization_check(self):
        self.assertTrue(may_receive("polkitd", {"polkitd"}, CHECK))

    def test_polkitd_may_not_receive_the_backends_signals(self):
        self.assertFalse(may_receive("polkitd", {"polkitd"}, COMPLETE))

    def test_another_system_account_receives_nothing_from_the_backend(self):
        self.assertFalse(may_receive("nobody", {"nobody"}, CHECK))
        self.assertFalse(may_receive("nobody", {"nobody"}, COMPLETE))

    def test_root_and_an_administrator_still_receive_the_backends_signals(self):
        self.assertTrue(may_receive("root", {"root"}, COMPLETE))
        self.assertTrue(may_receive("someone", {"someone", "wheel"}, COMPLETE))

    def test_the_account_named_is_the_one_polkit_runs_as(self):
        # polkit's meson option polkitd_user defaults to "polkitd"; the
        # recipe must not configure another account under the policy.
        build = (REPO / "packages/core/polkit/build.sh").read_text(encoding="utf-8")
        self.assertNotIn("polkitd_user", build)


if __name__ == "__main__":
    unittest.main()
