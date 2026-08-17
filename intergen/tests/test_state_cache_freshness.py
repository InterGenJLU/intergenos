# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A health verdict is fresh, or it says how old it is.

Acceptability standard (decided 2026-07-25): an answer that would be
unacceptable from a careful human operator is unacceptable from the product. A
human who checked the services five minutes ago does not answer "nothing is
failing" — they say when they looked. Serving a STALE health verdict in the
present tense is the defect; identity and inventory reads (hostname, hardware
model) may stay cached, because a five-minute-old hostname is still the hostname.

Two independent mechanisms are pinned here.

1. A CLEARED CONDITION MUST BE REPRESENTABLE. The poller stored a result only
   when the command printed something. `systemctl --failed` prints NOTHING when
   nothing is failing, so a unit that failed and was then FIXED left its failure
   text in the cache permanently — every later health answer reported a resolved
   failure as current, and no amount of freshness checking would have caught it,
   because the entry was not stale in any way the cache could see: it was simply
   never overwritten. A successful poll now writes its result even when empty.

2. AGE MUST REACH THE ANSWER. The health blocks were read through the age-blind
   getter while only `load_average` was freshness-checked, so any other block
   could be arbitrarily old and was still rendered in the present tense. Blocks
   now carry their age when stale, and a never-populated key says so instead of
   rendering as a clean bill of health.
"""
import time
import unittest
from unittest import mock

from intergen import state_cache as sc
from intergen.state_cache import CachedValue, StateCache


def _entry(value, age=0.0, stale_after=sc._DYNAMIC_INTERVAL):
    return CachedValue(value=value, timestamp=time.monotonic() - age,
                       command="test", stale_after=stale_after)


class ClearedConditionTests(unittest.TestCase):
    """A poll that succeeds with EMPTY output is a real observation."""

    def test_successful_empty_poll_overwrites_a_stale_failure(self):
        cache = StateCache()
        cache._cache["failed_services"] = _entry("nginx.service loaded failed")
        completed = mock.Mock(stdout="", returncode=0)
        with mock.patch.object(StateCache, "_run_pipeline",
                               return_value=completed), \
             mock.patch.object(sc.os, "getloadavg", return_value=(0.0, 0, 0)), \
             mock.patch.object(sc.time, "sleep"):
            cache._poll_all({"failed_services":
                             sc._DYNAMIC_COMMANDS["failed_services"]},
                            sc._DYNAMIC_INTERVAL)
        self.assertEqual(
            cache.get("failed_services"), "",
            "a resolved failure must be cleared — otherwise the fixed unit is "
            "reported as still failing, forever")

    def test_a_failed_poll_does_not_erase_the_last_known_value(self):
        """Only a command that RAN may overwrite. A non-zero exit is not an
        observation of health, so the previous value stands and ages out."""
        cache = StateCache()
        cache._cache["failed_services"] = _entry("nginx.service loaded failed")
        completed = mock.Mock(stdout="", returncode=1)
        with mock.patch.object(StateCache, "_run_pipeline",
                               return_value=completed), \
             mock.patch.object(sc.os, "getloadavg", return_value=(0.0, 0, 0)), \
             mock.patch.object(sc.time, "sleep"):
            cache._poll_all({"failed_services":
                             sc._DYNAMIC_COMMANDS["failed_services"]},
                            sc._DYNAMIC_INTERVAL)
        self.assertEqual(cache.get("failed_services"),
                         "nginx.service loaded failed")

    def test_a_real_failure_is_still_stored(self):
        cache = StateCache()
        completed = mock.Mock(stdout="sshd.service loaded failed\n", returncode=0)
        with mock.patch.object(StateCache, "_run_pipeline",
                               return_value=completed), \
             mock.patch.object(sc.os, "getloadavg", return_value=(0.0, 0, 0)), \
             mock.patch.object(sc.time, "sleep"):
            cache._poll_all({"failed_services":
                             sc._DYNAMIC_COMMANDS["failed_services"]},
                            sc._DYNAMIC_INTERVAL)
        self.assertEqual(cache.get("failed_services"),
                         "sshd.service loaded failed")


class AgeReachesTheAnswerTests(unittest.TestCase):

    def _cache_with(self, failed_age):
        cache = StateCache()
        cache._cache["load_average"] = _entry("0.1 0.2 0.3 1/500 1")
        cache._cache["failed_services"] = _entry(
            "nginx.service loaded failed", age=failed_age)
        cache._cache["recent_errors"] = _entry("some error")
        return cache

    def test_a_stale_health_block_is_labelled_with_its_age(self):
        cache = self._cache_with(failed_age=600)
        with mock.patch.object(StateCache, "refresh_dynamic",
                               return_value=False):
            data = cache.get_system_map_data("is anything failing on this machine?")
        self.assertIn("STALE", data)
        self.assertIn("minutes ago", data)
        self.assertIn("nginx.service", data,
                      "the reading is still shown — it is dated, not dropped")

    def test_a_fresh_health_block_reads_in_the_present_tense(self):
        cache = self._cache_with(failed_age=0)
        with mock.patch.object(StateCache, "refresh_dynamic",
                               return_value=False):
            data = cache.get_system_map_data("is anything failing on this machine?")
        self.assertNotIn("STALE", data)
        self.assertIn("nginx.service", data)

    def test_a_never_populated_key_is_not_a_clean_bill_of_health(self):
        cache = StateCache()
        cache._cache["load_average"] = _entry("0.1 0.2 0.3 1/500 1")
        with mock.patch.object(StateCache, "refresh_dynamic",
                               return_value=False):
            data = cache.get_system_map_data("is anything failing on this machine?")
        self.assertIn("no reading available yet", data)
        self.assertNotIn("all units are healthy", data)

    def test_a_fresh_empty_reading_IS_a_clean_bill_of_health(self):
        """The true negative must survive — once the poller can store an empty
        result, an empty-and-fresh failed_services genuinely means healthy."""
        cache = StateCache()
        cache._cache["load_average"] = _entry("0.1 0.2 0.3 1/500 1")
        cache._cache["failed_services"] = _entry("")
        with mock.patch.object(StateCache, "refresh_dynamic",
                               return_value=False):
            data = cache.get_system_map_data("is anything failing on this machine?")
        self.assertIn("all units are healthy", data)
        self.assertNotIn("STALE", data)


class OneScanPerBurstTests(unittest.TestCase):
    """A health ask fires ONE fresh scan, not one per ask."""

    def test_a_burst_of_health_asks_costs_one_scan(self):
        cache = StateCache()
        calls = []
        with mock.patch.object(StateCache, "_poll_all",
                               side_effect=lambda *a, **k: calls.append(1)):
            results = [cache.refresh_dynamic() for _ in range(5)]
        self.assertEqual(len(calls), 1, "a burst must not storm the system")
        self.assertEqual(results, [True, False, False, False, False])

    def test_a_later_ask_after_the_floor_scans_again(self):
        cache = StateCache()
        calls = []
        with mock.patch.object(StateCache, "_poll_all",
                               side_effect=lambda *a, **k: calls.append(1)):
            cache.refresh_dynamic()
            cache._last_refresh -= (sc._REFRESH_MIN_INTERVAL + 1)
            cache.refresh_dynamic()
        self.assertEqual(len(calls), 2)

    def test_an_identity_ask_does_not_trigger_a_scan(self):
        """Identity/inventory stays cache-served — ratified design, and the
        instant-answer latency depends on it."""
        cache = StateCache()
        cache._cache["load_average"] = _entry("0.1 0.2 0.3 1/500 1")
        cache._cache["service_list"] = _entry("sshd.service running")
        with mock.patch.object(StateCache, "refresh_dynamic") as refresh:
            cache.get_system_map_data("what services are running")
        refresh.assert_not_called()


class ColdCacheGuardTests(unittest.TestCase):
    """The pre-existing cold-cache guard must survive unchanged."""

    def test_no_fresh_load_average_still_declines_entirely(self):
        cache = StateCache()
        cache._cache["failed_services"] = _entry("nginx.service loaded failed")
        self.assertIsNone(
            cache.get_system_map_data("is anything failing on this machine?"))


if __name__ == "__main__":
    unittest.main()
