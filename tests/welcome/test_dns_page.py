"""Unit tests for the Welcomer's Finding Websites (name-server) page.

Covers the four ruled behaviours of that page, as far as they can be reached
without a display:

  (a) it SHOWS the name server in use — the resolver-state reader and the
      sentences built from it;
  (b) it defaults to changing NOTHING — the selection derived from the
      machine's own state is what the page opens on;
  (c) the choices, and the rule that a privacy-branded name server is never
      offered without encryption — asserted on the argument the privileged
      helper is given;
  (d) the failure that surfaces the page is told apart from other network
      failures — asserted through the shared classifier the page loads.

Also covers the privileged helper's address validation, which is run directly
through its `check-address` verb. That verb writes nothing and needs no
privilege, so these tests never touch this machine's own resolver
configuration — and neither does anything else in this file. The resolver
states below are supplied as text, exactly as `resolvectl` prints them.

GTK widget construction (build_dns_page) needs a display and is exercised by
the local render proof, not here — these tests are pure and run headless, the
same rule the ready-state tests follow.
"""

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib  # noqa: E402,F401

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"
PRIVHELPER = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome-privhelper"

_spec = importlib.util.spec_from_file_location("intergen_welcome", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)


def _completed(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def _runner(stdout="", returncode=0, stderr="", record=None):
    """A subprocess runner that answers with a fixed result and, optionally,
    records the argument vector it was given."""
    def run(argv, **kwargs):
        if record is not None:
            record.append(list(argv))
        return _completed(stdout, returncode, stderr)
    return run


# A resolver state as `resolvectl status --json=short` prints it: the global
# scope with no servers of its own, one interface carrying the default route
# and the servers its network handed out, and a second interface with nothing.
NETWORK_PROVIDED = json.dumps([
    {"dnssec": "allow-downgrade", "dnsOverTLS": "no", "llmnr": "no",
     "mDNS": "no", "resolvConfMode": "stub", "fallbackServers": []},
    {"ifname": "eno2", "ifindex": 2, "defaultRoute": True,
     "dnsOverTLS": "no", "dnssec": "allow-downgrade",
     "currentServer": {"addressString": "192.0.2.1"},
     "servers": [{"addressString": "192.0.2.1"}]},
    {"ifname": "wlo1", "ifindex": 3, "defaultRoute": False,
     "dnsOverTLS": "no", "dnssec": "allow-downgrade"},
])

# The same machine after the page applied the Cloudflare choice: the global
# scope now carries the provider's servers with their certificate names, and
# reports encryption on.
CLOUDFLARE_ENCRYPTED = json.dumps([
    {"dnsOverTLS": "yes", "dnssec": "allow-downgrade", "resolvConfMode": "stub",
     "servers": [
         {"addressString": "1.1.1.1", "name": "cloudflare-dns.com"},
         {"addressString": "1.0.0.1", "name": "cloudflare-dns.com"},
         {"addressString": "2606:4700:4700::1111", "name": "cloudflare-dns.com"},
         {"addressString": "2606:4700:4700::1001", "name": "cloudflare-dns.com"},
     ]},
    {"ifname": "eno2", "ifindex": 2, "defaultRoute": True, "dnsOverTLS": "no",
     "servers": [{"addressString": "192.0.2.1"}]},
])

# Cloudflare on a machine with no working IPv6: only the two IPv4 addresses
# are reported. Still Cloudflare — recognising it requires a subset test, not
# an equality test.
CLOUDFLARE_IPV4_ONLY = json.dumps([
    {"dnsOverTLS": "yes", "resolvConfMode": "stub",
     "servers": [{"addressString": "1.1.1.1"},
                 {"addressString": "1.0.0.1"}]},
])

QUAD9_ENCRYPTED = json.dumps([
    {"dnsOverTLS": "yes", "resolvConfMode": "stub",
     "servers": [{"addressString": "9.9.9.9"},
                 {"addressString": "149.112.112.112"}]},
])

CUSTOM_CLEARTEXT = json.dumps([
    {"dnsOverTLS": "no", "resolvConfMode": "stub",
     "servers": [{"addressString": "192.0.2.53"}]},
])

NOTHING_CONFIGURED = json.dumps([
    {"dnsOverTLS": "no", "resolvConfMode": "stub"},
    {"ifname": "eno2", "ifindex": 2, "defaultRoute": True, "dnsOverTLS": "no"},
])


class TestSharedClassifierIsLoaded(unittest.TestCase):
    """The Welcomer must be using the SAME module `intergen setup` uses, not a
    private copy of the logic — that is the whole reason it is loaded from a
    file rather than reimplemented here."""

    def test_module_loaded_from_the_intergen_tree(self):
        self.assertTrue(welcome.netdiag.__file__.endswith("net_diagnostics.py"))

    def test_module_exposes_the_causes_the_page_uses(self):
        for name in ("NAME_RESOLUTION", "NO_LINK", "NO_ROUTE", "REACHABLE",
                     "UNKNOWN", "MODEL_SOURCE_HOSTS"):
            self.assertTrue(hasattr(welcome.netdiag, name), name)

    def _record_probe_hosts(self, **kwargs):
        called = {}
        original = welcome.netdiag.probe_hosts
        try:
            def fake(hosts, **_kwargs):
                called["hosts"] = tuple(hosts)
                return "sentinel"
            welcome.netdiag.probe_hosts = fake
            self.assertEqual(welcome._probe_download_sources(**kwargs),
                             "sentinel")
        finally:
            welcome.netdiag.probe_hosts = original
        return called["hosts"]

    def test_probe_delegates_to_the_shared_module(self):
        self.assertEqual(self._record_probe_hosts(),
                         tuple(welcome.netdiag.MODEL_SOURCE_HOSTS))

    def test_the_startup_check_contacts_the_mirror_and_nothing_else(self):
        # It runs on every launch, whether or not anything is being set up,
        # and its question is answered by one host. Reaching a third party
        # unasked on a question that does not need them is not something to do
        # because it happened to be the existing list.
        hosts = self._record_probe_hosts(hosts=welcome._STARTUP_PROBE_HOSTS)
        self.assertEqual(hosts, ("repo.intergenos.org",))
        self.assertNotIn("huggingface.co", hosts)


class TestResolverState(unittest.TestCase):
    """(a) reading what the machine is actually doing."""

    def _state(self, stdout, returncode=0, managed=False):
        return welcome._resolver_state(
            run=_runner(stdout=stdout, returncode=returncode),
            dropin_exists=lambda: managed)

    def test_network_provided_state_is_parsed(self):
        state = self._state(NETWORK_PROVIDED)
        self.assertTrue(state["read"])
        self.assertIsNone(state["error"])
        scopes = [e["scope"] for e in state["entries"]]
        self.assertEqual(scopes, ["global", "link", "link"])
        link = state["entries"][1]
        self.assertEqual(link["ifname"], "eno2")
        self.assertTrue(link["default_route"])
        self.assertEqual([s["address"] for s in link["servers"]],
                         ["192.0.2.1"])
        self.assertEqual(link["current"], "192.0.2.1")

    def test_certificate_names_are_kept(self):
        state = self._state(CLOUDFLARE_ENCRYPTED)
        self.assertEqual(state["entries"][0]["servers"][0]["name"],
                         "cloudflare-dns.com")

    def test_nonzero_exit_is_reported_as_unread(self):
        # Never smoothed into a guess: a page about what the machine is doing
        # is worthless if it invents the answer when it cannot find out.
        state = welcome._resolver_state(
            run=_runner(returncode=1, stderr="boom"),
            dropin_exists=lambda: False)
        self.assertFalse(state["read"])
        self.assertEqual(state["error"], "boom")

    def test_unparseable_output_is_reported_as_unread(self):
        state = self._state("not json at all")
        self.assertFalse(state["read"])
        self.assertIn("unreadable", state["error"])

    def test_unexpected_shape_is_reported_as_unread(self):
        state = self._state('{"not": "a list"}')
        self.assertFalse(state["read"])
        self.assertIn("shape", state["error"])

    def test_missing_resolvectl_is_reported_as_unread(self):
        def explode(argv, **kwargs):
            raise FileNotFoundError("resolvectl")

        state = welcome._resolver_state(run=explode,
                                        dropin_exists=lambda: False)
        self.assertFalse(state["read"])
        self.assertIn("resolvectl", state["error"])

    def test_managed_flag_follows_the_dropin(self):
        self.assertTrue(self._state(CLOUDFLARE_ENCRYPTED, managed=True)["managed"])
        self.assertFalse(self._state(CLOUDFLARE_ENCRYPTED)["managed"])


class TestEffectiveResolver(unittest.TestCase):
    def _effective(self, stdout, managed=False):
        return welcome._effective_resolver(welcome._resolver_state(
            run=_runner(stdout=stdout), dropin_exists=lambda: managed))

    def test_network_servers_are_the_answer_when_nothing_was_chosen(self):
        effective = self._effective(NETWORK_PROVIDED)
        self.assertTrue(effective["known"])
        self.assertEqual(effective["origin"], "network")
        self.assertEqual(effective["ifname"], "eno2")
        self.assertEqual([s["address"] for s in effective["servers"]],
                         ["192.0.2.1"])

    def test_our_dropin_makes_the_global_servers_the_answer(self):
        effective = self._effective(CLOUDFLARE_ENCRYPTED, managed=True)
        self.assertEqual(effective["origin"], "chosen-here")
        self.assertEqual(effective["over_tls"], "yes")
        self.assertIn("1.1.1.1",
                      [s["address"] for s in effective["servers"]])

    def test_an_interface_with_servers_outranks_a_machine_wide_setting(self):
        # systemd-resolved consults the machine-wide set only when no
        # interface has servers of its own. Reporting it the other way round
        # would name servers the machine is not using, which is worse than
        # saying nothing. The page's own file is the one thing that overrides
        # this, because the file that writes those servers also writes the
        # rule that makes them win.
        effective = self._effective(CLOUDFLARE_ENCRYPTED, managed=False)
        self.assertEqual(effective["origin"], "network")
        self.assertEqual([s["address"] for s in effective["servers"]],
                         ["192.0.2.1"])

    def test_a_machine_wide_setting_that_is_not_used_is_still_reported(self):
        effective = self._effective(CLOUDFLARE_ENCRYPTED, managed=False)
        self.assertIn("1.1.1.1",
                      [s["address"] for s in effective["also_global"]])

    def test_the_unused_machine_wide_setting_is_named_in_the_panel(self):
        _servers, origin, _encryption = welcome._describe_current(
            self._effective(CLOUDFLARE_ENCRYPTED, managed=False))
        self.assertIn("1.1.1.1", origin)
        self.assertIn("not what these lookups use", origin)

    def test_machine_wide_servers_are_the_answer_when_no_interface_has_any(self):
        state = json.dumps([
            {"dnsOverTLS": "no", "servers": [{"addressString": "192.0.2.53"}]},
            {"ifname": "eno2", "ifindex": 2, "defaultRoute": True,
             "dnsOverTLS": "no"},
        ])
        effective = self._effective(state, managed=False)
        self.assertEqual(effective["origin"], "system-wide")
        self.assertEqual([s["address"] for s in effective["servers"]],
                         ["192.0.2.53"])

    def test_nothing_configured_is_reported_as_such(self):
        effective = self._effective(NOTHING_CONFIGURED)
        self.assertFalse(effective["known"])
        self.assertEqual(effective["origin"], "none")

    def test_unreadable_state_is_not_dressed_up_as_an_answer(self):
        effective = welcome._effective_resolver(
            welcome._resolver_state(run=_runner(returncode=1),
                                    dropin_exists=lambda: False))
        self.assertFalse(effective["known"])
        self.assertEqual(effective["origin"], "unreadable")


class TestSelectionFromState(unittest.TestCase):
    """(b) the page opens describing the machine, so doing nothing changes
    nothing."""

    def _selection(self, stdout, managed=False):
        return welcome._selection_from_state(welcome._resolver_state(
            run=_runner(stdout=stdout), dropin_exists=lambda: managed))

    def test_untouched_machine_preselects_network_default(self):
        selection, addresses, encrypted = self._selection(NETWORK_PROVIDED)
        self.assertEqual(selection, "network")
        self.assertEqual(addresses, ["192.0.2.1"])
        self.assertFalse(encrypted)

    def test_cloudflare_is_recognised(self):
        selection, _addresses, encrypted = self._selection(
            CLOUDFLARE_ENCRYPTED, managed=True)
        self.assertEqual(selection, "cloudflare")
        self.assertTrue(encrypted)

    def test_cloudflare_recognised_with_only_its_ipv4_addresses(self):
        # A machine with no IPv6 route legitimately shows two of the four.
        # Recognition is a subset test for exactly this reason; equality would
        # mislabel it as a custom choice and offer to "change" it to what it
        # already is.
        selection, _addresses, _encrypted = self._selection(
            CLOUDFLARE_IPV4_ONLY, managed=True)
        self.assertEqual(selection, "cloudflare")

    def test_quad9_is_recognised(self):
        selection, _addresses, _encrypted = self._selection(
            QUAD9_ENCRYPTED, managed=True)
        self.assertEqual(selection, "quad9")

    def test_an_address_of_our_own_is_custom(self):
        selection, addresses, encrypted = self._selection(
            CUSTOM_CLEARTEXT, managed=True)
        self.assertEqual(selection, "custom")
        self.assertEqual(addresses, ["192.0.2.53"])
        self.assertFalse(encrypted)

    def test_unreadable_state_selects_nothing(self):
        selection, addresses, _encrypted = welcome._selection_from_state(
            welcome._resolver_state(run=_runner(returncode=1),
                                    dropin_exists=lambda: False))
        self.assertEqual(selection, "unknown")
        self.assertEqual(addresses, [])


class TestDescribeCurrent(unittest.TestCase):
    """(a)/(c) the sentences the panel shows — including the one that refuses
    to let an unencrypted lookup look protected."""

    def _describe(self, stdout, managed=False):
        return welcome._describe_current(welcome._effective_resolver(
            welcome._resolver_state(run=_runner(stdout=stdout),
                                    dropin_exists=lambda: managed)))

    def test_cleartext_is_named_as_readable(self):
        _servers, _origin, encryption = self._describe(NETWORK_PROVIDED)
        lowered = encryption.lower()
        self.assertIn("plain text", lowered)
        self.assertIn("can see every name", lowered)

    def test_encrypted_is_named_as_encrypted(self):
        _servers, _origin, encryption = self._describe(
            CLOUDFLARE_ENCRYPTED, managed=True)
        self.assertIn("encrypted", encryption.lower())

    def test_opportunistic_is_treated_as_readable(self):
        # The mode that encrypts when it can and silently does not when it
        # cannot is the appearance of protection. The panel says so.
        state = json.dumps([{"dnsOverTLS": "opportunistic",
                             "servers": [{"addressString": "1.1.1.1"}]}])
        _servers, _origin, encryption = self._describe(state, managed=True)
        self.assertIn("treat", encryption.lower())
        self.assertIn("readable", encryption.lower())

    def test_the_servers_in_use_are_listed(self):
        servers, _origin, _encryption = self._describe(NETWORK_PROVIDED)
        self.assertIn("192.0.2.1", servers)

    def test_origin_names_the_interface_for_a_network_provided_server(self):
        _servers, origin, _encryption = self._describe(NETWORK_PROVIDED)
        self.assertIn("eno2", origin)

    def test_unreadable_state_says_so(self):
        servers, origin, encryption = self._describe_unreadable()
        self.assertIn("could not be read", servers.lower())
        self.assertIn("not describing your machine", origin.lower())
        self.assertIn("could not be read", encryption.lower())

    def _describe_unreadable(self):
        return welcome._describe_current(welcome._effective_resolver(
            welcome._resolver_state(run=_runner(returncode=1),
                                    dropin_exists=lambda: False)))


class TestApplyResolver(unittest.TestCase):
    """(c) what the page asks the privileged helper to do. Asserted on the
    argument vector, with the runner injected — no privileged call is made and
    this machine's resolver is not touched."""

    def _argv(self, *args, **kwargs):
        record = []
        ok, _message = welcome._apply_resolver(
            *args, run=_runner(record=record), **kwargs)
        self.assertTrue(ok)
        self.assertEqual(len(record), 1)
        return record[0]

    def test_network_default_removes_the_choice(self):
        argv = self._argv("network")
        self.assertEqual(argv[:2], ["pkexec", str(welcome.PRIVHELPER)])
        self.assertEqual(argv[2], "dns-use-network-default")

    def test_cloudflare_verb(self):
        self.assertEqual(self._argv("cloudflare")[2], "dns-use-cloudflare")

    def test_quad9_verb(self):
        self.assertEqual(self._argv("quad9")[2], "dns-use-quad9")

    def test_a_named_provider_carries_no_cleartext_option(self):
        # The ruled behaviour: a privacy-branded name server is never queried
        # in the clear, so there is no argument that could ask for it. The
        # verb itself is the whole instruction.
        for provider in ("cloudflare", "quad9"):
            argv = self._argv(provider)
            self.assertEqual(len(argv), 3)
            self.assertNotIn("cleartext", argv)

    def test_custom_encrypted(self):
        argv = self._argv("custom", ["192.0.2.53"], True)
        self.assertEqual(argv[2:], ["dns-use-custom", "encrypted", "192.0.2.53"])

    def test_custom_cleartext_is_said_explicitly(self):
        # Cleartext for an address the user typed is allowed, and it is named
        # in the request rather than being the absence of a flag.
        argv = self._argv("custom", ["192.0.2.53"], False)
        self.assertEqual(argv[2:], ["dns-use-custom", "cleartext", "192.0.2.53"])

    def test_custom_passes_every_address(self):
        argv = self._argv("custom", ["192.0.2.53", "192.0.2.54"], True)
        self.assertEqual(argv[-2:], ["192.0.2.53", "192.0.2.54"])

    def test_unknown_selection_is_refused_without_running_anything(self):
        record = []
        ok, message = welcome._apply_resolver(
            "something-else", run=_runner(record=record))
        self.assertFalse(ok)
        self.assertEqual(record, [])
        self.assertIn("unknown selection", message)

    def test_a_dismissed_password_prompt_says_nothing_changed(self):
        """The promise is unchanged; the words are the shared ones now.

        This page used to answer pkexec 126 and 127 with a single sentence of
        its own. Both codes now go through the mapping every privileged path in
        the application shares, so a closed prompt and a refused authentication
        are told apart here too. The assertion below still pins the promise the
        page has always made — the user is told nothing was changed — and adds
        the part that is new: the sentence says WHICH of the two happened.
        """
        ok, message = welcome._apply_resolver(
            "cloudflare", run=_runner(returncode=126))
        self.assertFalse(ok)
        self.assertIn("nothing was changed", message.lower())
        self.assertIn("closed", message.lower())

    def test_a_refused_authentication_is_told_apart_from_a_dismissal(self):
        dismissed = welcome._apply_resolver(
            "cloudflare", run=_runner(returncode=126))[1]
        refused = welcome._apply_resolver(
            "cloudflare", run=_runner(returncode=127))[1]
        self.assertIn("nothing was changed", refused.lower())
        self.assertNotEqual(dismissed, refused)

    def test_helper_error_text_is_passed_through(self):
        ok, message = welcome._apply_resolver(
            "cloudflare", run=_runner(returncode=3, stderr="refused"))
        self.assertFalse(ok)
        self.assertEqual(message, "refused")

    def test_a_failure_to_launch_is_reported_not_swallowed(self):
        def explode(argv, **kwargs):
            raise OSError("no pkexec")

        ok, message = welcome._apply_resolver("cloudflare", run=explode)
        self.assertFalse(ok)
        self.assertIn("no pkexec", message)


class TestAddressValidation(unittest.TestCase):
    """The privileged boundary's own parser, run through the helper's
    validate-only verb. This is the check that decides what may be written
    into a configuration file as root, so it is tested as the helper actually
    runs it rather than against a copy of the rules."""

    def _check(self, address):
        return subprocess.run(
            ["bash", str(PRIVHELPER), "check-address", address],
            capture_output=True, text=True, timeout=30).returncode

    def test_accepts_ipv4(self):
        for address in ("1.1.1.1", "9.9.9.9", "0.0.0.0", "255.255.255.255",
                        "192.0.2.10", "192.0.2.53"):
            self.assertEqual(self._check(address), 0, address)

    def test_accepts_ipv6(self):
        for address in ("::1", "::", "2606:4700:4700::1111", "2620:fe::fe",
                        "fe80::1", "2001:0db8:0000:0000:0000:0000:0000:0001",
                        "::ffff:192.0.2.1", "64:ff9b::1.2.3.4"):
            self.assertEqual(self._check(address), 0, address)

    def test_refuses_a_hostname(self):
        # A name server has to be given as an address: the machine cannot look
        # up a name until it has a name server that works.
        for address in ("example.com", "dns.quad9.net", "localhost"):
            self.assertEqual(self._check(address), 2, address)

    def test_refuses_malformed_ipv4(self):
        for address in ("1.1.1", "1.1.1.1.1", "256.1.1.1", "1.1.1.256",
                        "-1.1.1.1", "0x1.1.1.1", "..."):
            self.assertEqual(self._check(address), 2, address)

    def test_refuses_leading_zero_octets(self):
        # "010" is ten to one reader and eight to another. An address that
        # means two things has no place in a file that decides where lookups
        # go.
        for address in ("01.1.1.1", "1.01.1.1", "1.1.1.010"):
            self.assertEqual(self._check(address), 2, address)

    def test_refuses_malformed_ipv6(self):
        for address in ("gggg::1", "1:2:3:4:5:6:7:8:9", "1::2::3", "12345::1",
                        "1:2:3:4:5:6:7", ":1:2:3:4:5:6:7", "1:2:3:4:5:6:7:",
                        "::1.2.3", "1.2.3.4::", ":", "::1.2.3.4.5"):
            self.assertEqual(self._check(address), 2, address)

    def test_refuses_a_zone_identifier(self):
        self.assertEqual(self._check("fe80::1%eth0"), 2)

    def test_refuses_anything_that_could_add_a_second_setting(self):
        # The containment that matters: an accepted value cannot carry a
        # newline, an equals sign or a section header, so it cannot turn one
        # configuration line into two.
        for address in ("1.1.1.1\nDNSSEC=no", "1.1.1.1=x", "[Resolve]",
                        "1.1.1.1 1.0.0.1", "1.1.1.1;reboot",
                        "1.1.1.1#cloudflare-dns.com", "$(id)", "`id`"):
            self.assertEqual(self._check(address), 2, address)

    def test_refuses_an_empty_address(self):
        self.assertEqual(self._check(""), 2)


class TestHelperArgumentGates(unittest.TestCase):
    """The helper refuses a malformed request BEFORE it would do anything, so
    a bad call never reaches the point of writing a file."""

    def _run(self, *args):
        return subprocess.run(
            ["bash", str(PRIVHELPER), *args],
            capture_output=True, text=True, timeout=30).returncode

    def test_no_verb_is_a_usage_error(self):
        self.assertEqual(self._run(), 2)

    def test_unknown_verb_is_a_usage_error(self):
        self.assertEqual(self._run("dns-use-something"), 2)

    def test_custom_without_a_mode_is_refused(self):
        self.assertEqual(self._run("dns-use-custom", "1.1.1.1"), 2)

    def test_custom_with_an_invented_mode_is_refused(self):
        self.assertEqual(self._run("dns-use-custom", "maybe", "1.1.1.1"), 2)

    def test_custom_with_no_address_is_refused(self):
        self.assertEqual(self._run("dns-use-custom", "encrypted"), 2)

    def test_custom_with_too_many_addresses_is_refused(self):
        self.assertEqual(
            self._run("dns-use-custom", "encrypted",
                      "1.1.1.1", "1.0.0.1", "8.8.8.8", "9.9.9.9"), 2)

    def test_custom_with_a_bad_address_is_refused_with_its_own_code(self):
        # Exit 3, not 2: a refused address is a different answer from a
        # malformed command line, and the page says different things about
        # them.
        self.assertEqual(
            self._run("dns-use-custom", "encrypted", "example.com"), 3)

    def test_one_bad_address_among_good_ones_refuses_the_whole_request(self):
        self.assertEqual(
            self._run("dns-use-custom", "encrypted", "1.1.1.1", "not-an-ip"), 3)

    def test_the_usage_text_names_every_verb(self):
        proc = subprocess.run(["bash", str(PRIVHELPER)],
                              capture_output=True, text=True, timeout=30)
        for verb in ("dns-use-network-default", "dns-use-cloudflare",
                     "dns-use-quad9", "dns-use-custom", "check-address"):
            self.assertIn(verb, proc.stderr)


class TestAddressIsValidWrapper(unittest.TestCase):
    """The page's pre-check runs the helper's own parser rather than a second
    one, so what the interface accepts and what the boundary enforces cannot
    drift apart."""

    def test_it_calls_the_helper(self):
        record = []
        welcome._address_is_valid("1.1.1.1", run=_runner(record=record))
        self.assertEqual(record[0],
                         [str(welcome.PRIVHELPER), "check-address", "1.1.1.1"])

    def test_it_never_uses_pkexec(self):
        # Checking an address asks for no privilege, so it must not raise a
        # password prompt while the user is still typing.
        record = []
        welcome._address_is_valid("1.1.1.1", run=_runner(record=record))
        self.assertNotIn("pkexec", record[0])

    def test_nonzero_exit_is_invalid(self):
        self.assertFalse(welcome._address_is_valid(
            "example.com", run=_runner(returncode=2)))

    def test_a_failure_to_run_the_check_is_invalid(self):
        def explode(argv, **kwargs):
            raise OSError("missing helper")

        self.assertFalse(welcome._address_is_valid("1.1.1.1", run=explode))

    def test_the_real_helper_agrees(self):
        # Not mocked: the wrapper and the shipped parser, end to end.
        self.assertTrue(welcome._address_is_valid("1.1.1.1"))
        self.assertFalse(welcome._address_is_valid("example.com"))


class TestChoiceCopy(unittest.TestCase):
    """(c) every option says who can see the lookups afterwards, and the two
    privacy-branded options say encryption is not optional."""

    def _detail(self, choice_id):
        for cid, _label, detail in welcome._DNS_CHOICES:
            if cid == choice_id:
                return detail
        raise AssertionError(f"no such choice {choice_id}")

    def test_all_four_choices_are_offered(self):
        self.assertEqual([c[0] for c in welcome._DNS_CHOICES],
                         ["network", "cloudflare", "quad9", "custom"])

    def test_named_providers_say_encryption_cannot_be_switched_off(self):
        for choice_id in ("cloudflare", "quad9"):
            self.assertIn("cannot be switched off",
                          self._detail(choice_id).lower())

    def test_named_providers_say_who_can_still_see_the_lookups(self):
        self.assertIn("cloudflare itself can see",
                      self._detail("cloudflare").lower())
        self.assertIn("quad9 itself can see", self._detail("quad9").lower())

    def test_network_default_says_it_changes_nothing(self):
        self.assertIn("unless you change it", self._detail("network").lower())

    def test_custom_encryption_note_names_the_failure_mode(self):
        lowered = welcome._ENCRYPT_CUSTOM_DETAIL.lower()
        self.assertIn("fail rather than quietly falling back", lowered)
        self.assertIn("plain text", lowered)


class GroupTitleInsetTests(unittest.TestCase):
    """The card border of a boxed group is drawn around its header too, so a
    group title with no start inset renders on the border itself.

    What these tests can prove without a display is that the stylesheet still
    carries the inset and that the value still matches the row-title start
    position it was derived from. The position itself was measured on the live
    display (group title x=1 without the rule, x=33 with it, exactly the row
    titles' x; the three other shapes carrying the same class did not move).
    A text assertion cannot re-measure that, but it does stop the rule being
    dropped without anyone noticing.
    """

    #: The row titles inside a boxed group start 32px from the card's left
    #: edge on GTK 4.20.3 / libadwaita 1.8.4. Measured, not chosen.
    ROW_TITLE_INSET_PX = 32

    def test_the_group_title_carries_a_start_inset(self):
        self.assertIn("box.labels", welcome.CUSTOM_CSS,
                      "the group-header labels box is no longer targeted, so "
                      "group titles render on the card border")

    def test_the_inset_matches_the_measured_row_title_start(self):
        self.assertIn(
            f".transparent-group box.labels {{ margin-left: "
            f"{self.ROW_TITLE_INSET_PX}px; }}",
            welcome.CUSTOM_CSS,
            "the group-title inset no longer matches the measured row-title "
            "start position")

    def test_the_inset_does_not_reach_the_rows(self):
        # `box.labels` is the group header's own labels box. Rows use
        # `box.title` for the same job, so the rule cannot indent row content.
        self.assertNotIn(".transparent-group box.title", welcome.CUSTOM_CSS)


if __name__ == "__main__":
    unittest.main()
