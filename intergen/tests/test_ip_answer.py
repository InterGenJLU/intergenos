# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The "what's my IP" answer — internal + external, IPv4 auto / IPv6 gated.

Decided privacy split: the IPv4 answer auto-fires (the user
asked, so the resolver query IS the request), but IPv6 is gated behind an explicit
offer because a global v6 (SLAAC/EUI-64) can pin a device. The external value is
THIRD-PARTY display-only data — composed into the answer string, never re-executed
(no command-substitution; the handler runs only FIXED, code-owned commands).

These are deterministic unit tests (no embedder, no daemon): detection, the parse
helpers (incl the captured-dig quoted-TXT strip), and the handler composition /
graceful-fail / IPv6-gating with the command runner stubbed.
"""

from __future__ import annotations

import unittest

from intergen.router import (
    ConversationRouter,
    _is_ip_query,
    _parse_internal_ip,
    _strip_dig_txt,
)

# ifconfig, OLD net-tools/inetutils format (as seen on a development machine).
_IFCONFIG_OLD = """\
eno1      Link encap:Ethernet  HWaddr 80:E8:2C:C6:FD:03
          inet addr:192.168.1.241  Bcast:192.168.1.255  Mask:255.255.255.0
          inet6 addr: 2601:abc:def::1234/64 Scope:Global
          inet6 addr: fe80::82e8:2cff:fec6:fd03/64 Scope:Link
lo        Link encap:Local Loopback
          inet addr:127.0.0.1  Mask:255.0.0.0
          inet6 addr: ::1/128 Scope:Host
"""
# ifconfig, NEW format.
_IFCONFIG_NEW = """\
eno1: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 192.168.1.50  netmask 255.255.255.0  broadcast 192.168.1.255
        inet6 2601:abc::5  prefixlen 64  scopeid 0x0<global>
        inet6 fe80::1  prefixlen 64  scopeid 0x20<link>
lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536
        inet 127.0.0.1  netmask 255.0.0.0
        inet6 ::1  prefixlen 128  scopeid 0x10<host>
"""


class IpQueryDetection(unittest.TestCase):
    def test_positive_ip_asks(self):
        for q in ("what's my ip", "whats my ip address", "show me my ip",
                  "what ip do i have", "what ip am i on", "my ip addr"):
            self.assertTrue(_is_ip_query(q), q)

    def test_negatives_not_ip_asks(self):
        for q in ("grep ip in the log", "what do i have", "zip the files",
                  "show me my recipe", "unzip this", "how much ram do i have"):
            self.assertFalse(_is_ip_query(q), q)

    def test_instructional_is_not_auto_answer(self):
        # "how do I find my ip" should TEACH (explain gate), not auto-answer.
        for q in ("how do i find my ip", "how can i see my ip address",
                  "what's the command for my ip"):
            self.assertFalse(_is_ip_query(q), q)

    def test_face_coverage_broadened_phrasings(self):
        # FACE coverage 2026-07-01: the both-IPs handler already answers these;
        # the selector now reaches them (adjective between my/your and "ip",
        # bare scope + ip, and behind-a-NAT).
        for q in ("current ip", "whats my local ip", "whats my external ip",
                  "whats my public ip", "am i behind a nat", "my public ip",
                  "internal ip", "what's my external ip"):
            self.assertTrue(_is_ip_query(q), q)

    def test_face_coverage_false_friends_still_miss(self):
        # The broaden must NOT trip the over-match traps (WC SET-2).
        for q in ("whats a good ip camera", "how do i zip files",
                  "search the log for ip", "what do i have installed",
                  "how do i skip this", "how do i find my external ip"):
            self.assertFalse(_is_ip_query(q), q)

    def test_definitional_ip_asks_teach_not_personal(self):
        # WC over-match guard: "what IS an ip" definitional forms must NOT hit the
        # personal-ip handler — they teach. The indefinite article / define /
        # used-for / stand-for frame marks the definitional ask.
        for q in ("what is an ip address", "whats an ip address",
                  "what is a public ip", "define ip address",
                  "what is an ip address used for", "what does ip stand for"):
            self.assertFalse(_is_ip_query(q), q)

    def test_personal_ip_still_catches_despite_definitional_guard(self):
        # my/your (or bare scope) with no article stays a personal ask.
        for q in ("what is my public ip", "whats my static ip",
                  "what is my ip address", "what's my ip address",
                  "current ip", "whats my local ip"):
            self.assertTrue(_is_ip_query(q), q)


class ParseHelpers(unittest.TestCase):
    def test_internal_ipv4_excludes_loopback_both_formats(self):
        self.assertEqual(_parse_internal_ip(_IFCONFIG_OLD, v6=False), "192.168.1.241")
        self.assertEqual(_parse_internal_ip(_IFCONFIG_NEW, v6=False), "192.168.1.50")

    def test_internal_ipv6_global_excludes_linklocal_and_loopback(self):
        self.assertEqual(_parse_internal_ip(_IFCONFIG_OLD, v6=True), "2601:abc:def::1234")
        self.assertEqual(_parse_internal_ip(_IFCONFIG_NEW, v6=True), "2601:abc::5")

    def test_no_address_returns_none(self):
        self.assertIsNone(_parse_internal_ip("no addresses here", v6=False))
        self.assertIsNone(_parse_internal_ip("inet addr:127.0.0.1", v6=False))
        self.assertIsNone(_parse_internal_ip("inet6 addr: fe80::1/64", v6=True))

    def test_dig_txt_quote_strip(self):
        self.assertEqual(_strip_dig_txt('"203.0.113.5"\n'), "203.0.113.5")
        self.assertEqual(_strip_dig_txt('"2606:4700::1234"'), "2606:4700::1234")

    def test_dig_empty_or_none_returns_none(self):
        self.assertIsNone(_strip_dig_txt(""))
        self.assertIsNone(_strip_dig_txt(None))
        self.assertIsNone(_strip_dig_txt("   \n"))


def _router(ifconfig_out, dig4=None, dig6=None):
    r = ConversationRouter.__new__(ConversationRouter)
    r._pending_ipv6_offer = None
    r._record = lambda *a, **k: None
    def fake_run(cmd):
        if "ifconfig" in cmd:
            return ifconfig_out
        if cmd.startswith("dig -6"):
            return dig6
        if cmd.startswith("dig -4"):
            return dig4
        return None
    r._run_fixed_command = fake_run
    return r


class Ipv4AutoAnswer(unittest.TestCase):
    def test_internal_and_external_present(self):
        r = _router(_IFCONFIG_OLD, dig4='"8.8.8.8"')
        res = r._answer_ip_query("what's my ip", 0.0)
        self.assertIn("internal IPv4 is 192.168.1.241", res.text)
        self.assertIn("external IPv4 is being reported as 8.8.8.8 (via Cloudflare)",
                      res.text)
        # IPv6 is OFFERED, never auto-revealed.
        self.assertIn("Want your IPv6 too?", res.text)
        self.assertNotIn("2601:", res.text)        # no v6 leaked into the auto answer
        self.assertEqual(r._pending_ipv6_offer, "what's my ip")

    def test_graceful_fail_external_unavailable(self):
        # dig absent / resolver unreachable -> still answer internal, never hang/error.
        r = _router(_IFCONFIG_OLD, dig4=None)
        res = r._answer_ip_query("what's my ip", 0.0)
        self.assertIn("internal IPv4 is 192.168.1.241", res.text)
        self.assertIn("external IPv4 is unavailable", res.text)
        self.assertTrue(res.handled)

    def test_reachable_but_empty_external_v4_is_unavailable(self):
        # The resolver is REACHABLE but returns no answer, so run_command hands
        # back its "(no output)" placeholder (or other non-address text). That must
        # never be echoed as an address — the shape gate treats it as absent and the
        # graceful-unavailable branch fires instead. (WC dual-address wedge.)
        for empty in ("(no output)", "not a real ip", "192.168.1.999"):
            r = _router(_IFCONFIG_OLD, dig4=empty)
            res = r._answer_ip_query("what's my ip", 0.0)
            self.assertIn("external IPv4 is unavailable", res.text, empty)
            self.assertNotIn("(no output)", res.text, empty)
            self.assertNotIn("reported as", res.text, empty)
        # A genuine GLOBAL IPv4 still composes normally (no false negative).
        r = _router(_IFCONFIG_OLD, dig4='"8.8.8.8"')
        self.assertIn("reported as 8.8.8.8",
                      r._answer_ip_query("what's my ip", 0.0).text)

    def test_wrong_family_external_v4_is_unavailable(self):
        # An IPv6 value on the v4 path is not a valid external IPv4 -> absent.
        r = _router(_IFCONFIG_OLD, dig4='"2606:4700::1234"')
        res = r._answer_ip_query("what's my ip", 0.0)
        self.assertIn("external IPv4 is unavailable", res.text)

    def test_non_global_external_v4_is_unavailable(self):
        # A misconfigured/captive/hostile resolver can answer with a loopback,
        # private, or link-local address of the right family. It is a valid IPv4
        # but NOT the user's public IP — presenting it as such states a false value
        # as fact, so the global-ness gate treats it as absent. (WC in-class residual.)
        for nonglobal in ("192.168.1.50", "127.0.0.1", "169.254.10.1", "10.0.0.5"):
            r = _router(_IFCONFIG_OLD, dig4=f'"{nonglobal}"')
            res = r._answer_ip_query("what's my ip", 0.0)
            self.assertIn("external IPv4 is unavailable", res.text, nonglobal)
            self.assertNotIn(nonglobal, res.text, nonglobal)
            self.assertNotIn("reported as", res.text, nonglobal)


class Ipv6Gated(unittest.TestCase):
    def test_ipv6_does_not_auto_fire(self):
        r = _router(_IFCONFIG_OLD, dig4='"8.8.8.8"')
        res = r._answer_ip_query("what's my ip", 0.0)
        self.assertNotIn("IPv6 is", res.text)      # only the offer, not the v6 answer

    def test_ipv6_on_accept(self):
        r = _router(_IFCONFIG_OLD, dig4='"8.8.8.8"', dig6='"2606:4700::abcd"')
        r._answer_ip_query("what's my ip", 0.0)    # sets the offer
        res = r._resolve_pending_ipv6_offer("yes", 0.0)
        self.assertIsNotNone(res)
        self.assertIn("internal IPv6 is 2601:abc:def::1234", res.text)
        self.assertIn("external IPv6 is being reported as 2606:4700::abcd", res.text)
        self.assertIsNone(r._pending_ipv6_offer)   # offer consumed

    def test_ipv6_decline(self):
        r = _router(_IFCONFIG_OLD)
        r._answer_ip_query("what's my ip", 0.0)
        res = r._resolve_pending_ipv6_offer("no", 0.0)
        self.assertIsNotNone(res)
        self.assertIn("leave IPv6 out", res.text)

    def test_ipv6_offer_lapses_on_unrelated_reply(self):
        r = _router(_IFCONFIG_OLD)
        r._answer_ip_query("what's my ip", 0.0)
        # an unrelated reply lapses the offer (returns None -> caller routes normally)
        self.assertIsNone(r._resolve_pending_ipv6_offer("what time is it", 0.0))
        self.assertIsNone(r._pending_ipv6_offer)

    def test_ipv6_no_connectivity_message(self):
        r = _router(_IFCONFIG_NEW.replace("2601:abc::5", "fe80::99"), dig6=None)
        r._answer_ip_query("what's my ip", 0.0)
        res = r._resolve_pending_ipv6_offer("yes", 0.0)
        self.assertIn("No IPv6 connectivity", res.text)

    def test_reachable_but_empty_external_v6_is_unavailable(self):
        # v6 path: a reachable-but-empty external lookup yields the "(no output)"
        # placeholder; the shape gate treats it as absent. With no global internal
        # v6 either, the clean "No IPv6 connectivity" message fires — not "(no output)".
        r = _router(_IFCONFIG_NEW.replace("2601:abc::5", "fe80::99"), dig6="(no output)")
        r._answer_ip_query("what's my ip", 0.0)
        res = r._resolve_pending_ipv6_offer("yes", 0.0)
        self.assertIn("No IPv6 connectivity", res.text)
        self.assertNotIn("(no output)", res.text)

    def test_reachable_but_empty_external_v6_with_internal_present(self):
        # Internal global v6 present but external reachable-but-empty -> the external
        # half reads "unavailable", never the "(no output)" placeholder.
        r = _router(_IFCONFIG_OLD, dig6="(no output)")
        r._answer_ip_query("what's my ip", 0.0)
        res = r._resolve_pending_ipv6_offer("yes", 0.0)
        self.assertIn("internal IPv6 is 2601:abc:def::1234", res.text)
        self.assertIn("external IPv6 is unavailable", res.text)
        self.assertNotIn("(no output)", res.text)

    def test_non_global_external_v6_is_unavailable(self):
        # A ULA / link-local / loopback IPv6 is a valid v6 but not a global one;
        # it must not be presented as the user's public IPv6. Internal global v6
        # present, external non-global -> the external half reads "unavailable".
        for nonglobal in ('"fd00::1234"', '"fe80::abcd"', '"::1"'):
            r = _router(_IFCONFIG_OLD, dig6=nonglobal)
            r._answer_ip_query("what's my ip", 0.0)
            res = r._resolve_pending_ipv6_offer("yes", 0.0)
            self.assertIn("internal IPv6 is 2601:abc:def::1234", res.text, nonglobal)
            self.assertIn("external IPv6 is unavailable", res.text, nonglobal)


if __name__ == "__main__":
    unittest.main()
