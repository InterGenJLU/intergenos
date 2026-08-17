# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Wi-Fi carry (2026-07-11) — RED/GREEN for the backend copy step, the TUI
resolver, and the netprobe parser.

The feature: an active live-session Wi-Fi connection is offered (Confirm
screen / TUI ask, default ON) for carry onto the installed system, so first
boot arrives already connected. These tests pin the portability rules —
what carries, what is skipped and WHY — and the ask-only-when-it-matters
tri-state threading.
"""

import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from installer.backend.config import carry_wifi_connections  # noqa: E402
from installer.frontend.tui import _resolve_carry_wifi  # noqa: E402
from installer.frontend import netprobe  # noqa: E402

import tempfile  # noqa: E402


_PSK_PROFILE = """\
[connection]
id=HomeNet
uuid=11111111-2222-3333-4444-555555555555
type=wifi
interface-name=wlp2s0

[wifi]
mode=infrastructure
ssid=HomeNet

[wifi-security]
key-mgmt=wpa-psk
psk=correct horse battery staple

[ipv4]
method=auto
"""

_KEYRING_PROFILE = """\
[connection]
id=AgentNet
type=wifi

[wifi]
ssid=AgentNet

[wifi-security]
key-mgmt=wpa-psk
psk-flags=1
"""

_ENTERPRISE_PROFILE = """\
[connection]
id=CorpNet
type=wifi

[wifi]
ssid=CorpNet

[wifi-security]
key-mgmt=wpa-eap

[802-1x]
eap=peap;
identity=user@corp
ca-cert=/run/live/certs/corp-ca.pem
"""

_OPEN_PROFILE = """\
[connection]
id=CoffeeShop
type=802-11-wireless

[wifi]
ssid=CoffeeShop

[ipv4]
method=auto
"""

_WIRED_PROFILE = """\
[connection]
id=Wired1
type=ethernet

[ipv4]
method=auto
"""

# A profile created from the live user's session: NM writes a
# permissions= creator binding. The live user does not exist on the
# installed system, so the binding must be stripped at carry time or
# the profile is unusable by anyone (never autoconnects, never offered).
_LIVE_USER_BOUND_PROFILE = """\
[connection]
id=HomeNet
uuid=41840ae4-53bd-491c-a4d6-66d999384168
type=wifi
interface-name=wlo1
permissions=user:intergenos:;

[wifi]
mode=infrastructure
ssid=HomeNet

[wifi-security]
auth-alg=open
key-mgmt=wpa-psk
psk=correct horse battery staple

[ipv4]
method=auto
"""


class CarryWifiBackendTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.live = base / "live"
        self.target = base / "target"
        self.src = self.live / "etc/NetworkManager/system-connections"
        self.src.mkdir(parents=True)
        self.target.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        p = self.src / name
        p.write_text(content)
        os.chmod(p, 0o600)
        return p

    def _dst(self, name):
        return self.target / "etc/NetworkManager/system-connections" / name

    def test_system_stored_psk_carries_byte_verbatim(self):
        src = self._write("HomeNet.nmconnection", _PSK_PROFILE)
        result = carry_wifi_connections(self.target, live_root=self.live)
        self.assertEqual(result["carried"], ["HomeNet"])
        self.assertEqual(result["skipped"], {})
        self.assertEqual(result["normalized"], {})
        dst = self._dst("HomeNet.nmconnection")
        self.assertEqual(dst.read_bytes(), src.read_bytes())

    def test_live_user_permissions_binding_stripped(self):
        src = self._write("HomeNet.nmconnection", _LIVE_USER_BOUND_PROFILE)
        result = carry_wifi_connections(self.target, live_root=self.live)
        self.assertEqual(result["carried"], ["HomeNet"])
        self.assertEqual(result["skipped"], {})
        self.assertEqual(
            result["normalized"],
            {"HomeNet.nmconnection": "live-user-permissions-stripped"})
        dst_text = self._dst("HomeNet.nmconnection").read_text()
        self.assertNotIn("permissions=", dst_text)
        # Every other line rides byte-for-byte — the strip is surgical.
        expected = "".join(
            line for line in src.read_text().splitlines(True)
            if not line.strip().startswith("permissions="))
        self.assertEqual(dst_text, expected)
        # The secret and the identity survive.
        self.assertIn("psk=correct horse battery staple\n", dst_text)
        self.assertIn("ssid=HomeNet\n", dst_text)

    def test_carried_keyfile_is_0600_and_dir_0700(self):
        self._write("HomeNet.nmconnection", _PSK_PROFILE)
        carry_wifi_connections(self.target, live_root=self.live)
        dst = self._dst("HomeNet.nmconnection")
        self.assertEqual(stat.S_IMODE(dst.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(dst.parent.stat().st_mode), 0o700)

    def test_keyring_scoped_secret_is_skipped_loudly(self):
        self._write("AgentNet.nmconnection", _KEYRING_PROFILE)
        result = carry_wifi_connections(self.target, live_root=self.live)
        self.assertEqual(result["carried"], [])
        self.assertEqual(result["skipped"],
                         {"AgentNet.nmconnection": "secret-not-system-stored"})
        self.assertFalse(self._dst("AgentNet.nmconnection").exists())

    def test_enterprise_8021x_is_skipped_loudly(self):
        self._write("CorpNet.nmconnection", _ENTERPRISE_PROFILE)
        result = carry_wifi_connections(self.target, live_root=self.live)
        self.assertEqual(result["carried"], [])
        self.assertEqual(result["skipped"],
                         {"CorpNet.nmconnection":
                          "enterprise-8021x-not-portable-v1"})
        self.assertFalse(self._dst("CorpNet.nmconnection").exists())

    def test_open_network_carries_and_legacy_type_accepted(self):
        self._write("CoffeeShop.nmconnection", _OPEN_PROFILE)
        result = carry_wifi_connections(self.target, live_root=self.live)
        self.assertEqual(result["carried"], ["CoffeeShop"])
        self.assertTrue(self._dst("CoffeeShop.nmconnection").exists())

    def test_wired_profile_is_ignored_entirely(self):
        self._write("Wired1.nmconnection", _WIRED_PROFILE)
        result = carry_wifi_connections(self.target, live_root=self.live)
        self.assertEqual(result["carried"], [])
        self.assertEqual(result["skipped"], {})
        self.assertFalse(self._dst("Wired1.nmconnection").exists())

    def test_mixed_directory_partitions_correctly(self):
        self._write("HomeNet.nmconnection", _PSK_PROFILE)
        self._write("AgentNet.nmconnection", _KEYRING_PROFILE)
        self._write("CorpNet.nmconnection", _ENTERPRISE_PROFILE)
        self._write("CoffeeShop.nmconnection", _OPEN_PROFILE)
        self._write("Wired1.nmconnection", _WIRED_PROFILE)
        result = carry_wifi_connections(self.target, live_root=self.live)
        self.assertEqual(sorted(result["carried"]), ["CoffeeShop", "HomeNet"])
        self.assertEqual(set(result["skipped"]),
                         {"AgentNet.nmconnection", "CorpNet.nmconnection"})

    def test_missing_source_dir_is_a_clean_noop(self):
        result = carry_wifi_connections(self.target,
                                        live_root=self.live / "nope")
        self.assertEqual(result,
                         {"carried": [], "skipped": {}, "normalized": {}})
        self.assertFalse(
            (self.target / "etc/NetworkManager").exists())

    def test_unparseable_keyfile_skipped_not_fatal(self):
        self._write("Broken.nmconnection", "\x00\x01 not a keyfile [")
        self._write("HomeNet.nmconnection", _PSK_PROFILE)
        result = carry_wifi_connections(self.target, live_root=self.live)
        self.assertEqual(result["carried"], ["HomeNet"])
        self.assertIn("Broken.nmconnection", result["skipped"])


class ResolveCarryWifiTests(unittest.TestCase):
    """The TUI resolver — the ask-only-when-it-matters gate (pure)."""

    def test_no_active_wifi_returns_none_never_asks(self):
        ask = mock.Mock()
        self.assertIsNone(_resolve_carry_wifi([], ask))
        self.assertIsNone(_resolve_carry_wifi(None, ask))
        ask.assert_not_called()

    def test_active_wifi_asks_and_returns_choice(self):
        self.assertTrue(_resolve_carry_wifi(["HomeNet"], lambda: True))
        self.assertFalse(_resolve_carry_wifi(["HomeNet"], lambda: False))


class NetprobeParserTests(unittest.TestCase):
    def _run(self, returncode, stdout):
        proc = mock.Mock(returncode=returncode, stdout=stdout)
        with mock.patch("installer.frontend.netprobe.subprocess.run",
                        return_value=proc):
            return netprobe.active_wifi_names()

    def test_active_wifi_parsed_wired_filtered(self):
        out = "HomeNet:802-11-wireless\nWired1:802-3-ethernet\n"
        self.assertEqual(self._run(0, out), ["HomeNet"])

    def test_escaped_colon_in_name_unescaped(self):
        out = "Cafe\\: Upstairs:802-11-wireless\n"
        self.assertEqual(self._run(0, out), ["Cafe: Upstairs"])

    def test_nmcli_failure_returns_none(self):
        self.assertIsNone(self._run(10, ""))

    def test_nmcli_absent_returns_none(self):
        with mock.patch("installer.frontend.netprobe.subprocess.run",
                        side_effect=FileNotFoundError):
            self.assertIsNone(netprobe.active_wifi_names())

    def test_no_active_connections_returns_empty_list(self):
        self.assertEqual(self._run(0, ""), [])


class StateTriStateTests(unittest.TestCase):
    """to_install_io threads carry_wifi only when asked (GTK-free import)."""

    def _state(self):
        try:
            from installer.frontend.gui.state import InstallerState
        except Exception:
            self.skipTest("GUI state module needs GTK deps in this env")
        return InstallerState()

    def test_absent_when_never_asked(self):
        st = self._state()
        st.carry_wifi = None
        self.assertNotIn("carry_wifi", st.to_install_io())

    def test_present_when_asked(self):
        st = self._state()
        for choice in (True, False):
            st.carry_wifi = choice
            self.assertEqual(st.to_install_io().get("carry_wifi"), choice)


if __name__ == "__main__":
    unittest.main()
