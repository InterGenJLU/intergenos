"""Tests for the read-through tool-result cache (intergen/tool_cache.py)."""
import unittest

from intergen.interfaces.types import ToolResult
from intergen.tool_cache import ToolCache, _MAX_ENTRIES


def _ok(name="manage_packages", content="824 packages", summary=None):
    return ToolResult(call_id="c", name=name, content=content,
                      success=True, model_summary=summary)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestToolCache(unittest.TestCase):
    def setUp(self):
        self.clk = _Clock()
        self.c = ToolCache(clock=self.clk)
        self.uid = 1000

    def test_miss_then_put_then_hit(self):
        args = {"action": "list"}
        self.assertIsNone(self.c.get("manage_packages", args, self.uid))
        r = _ok(summary="824 packages.")
        self.c.put("manage_packages", args, self.uid, r)
        hit = self.c.get("manage_packages", args, self.uid)
        # D-5: a hit is a SNAPSHOT copy, never the cached object itself, so the
        # chokepoint's in-place scan/spotlight can't mutate the cached entry.
        self.assertIsNot(hit, r)
        self.assertEqual(hit.content, r.content)
        self.assertEqual(hit.model_summary, "824 packages.")  # summary survives
        self.assertEqual(self.c.hits, 1)
        self.assertEqual(self.c.misses, 1)

    def test_d5_scan_mutation_of_a_hit_cannot_poison_the_cache(self):
        # D-5 trust-boundary defense-in-depth: the dispatch chokepoint mutates
        # the served result in place (withhold/wrap). A copy on put AND get must
        # keep those mutations off the cached entry, so the cache always holds
        # the RAW pre-scan result and a later hit is never the wrapped/withheld
        # form from an earlier serve.
        args = {"action": "list"}
        raw = _ok(content="824 packages", summary="824 packages.")
        self.c.put("manage_packages", args, self.uid, raw)

        # Simulate the chokepoint mutating the ORIGINAL after put (scan/wrap):
        raw.content = "<<UNTRUSTED>> 824 packages <<END>>"
        raw.model_summary = None

        # Simulate a BLOCK mutating the FIRST hit in place:
        hit1 = self.c.get("manage_packages", args, self.uid)
        hit1.content = "Sentinel withheld: injection"
        hit1.model_summary = None

        # A SECOND hit must still be the clean raw result, untouched by either.
        hit2 = self.c.get("manage_packages", args, self.uid)
        self.assertEqual(hit2.content, "824 packages")
        self.assertEqual(hit2.model_summary, "824 packages.")
        self.assertIsNot(hit2, hit1)

    def test_non_cacheable_tool_is_never_cached(self):
        # take_screenshot is not in the policy table.
        self.assertFalse(ToolCache.is_cacheable("take_screenshot", {}))
        self.c.put("take_screenshot", {}, self.uid, _ok("take_screenshot"))
        self.assertIsNone(self.c.get("take_screenshot", {}, self.uid))

    def test_failed_result_not_cached(self):
        bad = ToolResult(call_id="c", name="manage_packages",
                         content="error", success=False)
        self.c.put("manage_packages", {"action": "list"}, self.uid, bad)
        self.assertIsNone(self.c.get("manage_packages", {"action": "list"}, self.uid))

    def test_ttl_expiry(self):
        args = {"action": "status", "service": "dbus"}  # ttl 10s
        self.c.put("manage_services", args, self.uid, _ok("manage_services"))
        self.clk.t += 9
        self.assertIsNotNone(self.c.get("manage_services", args, self.uid))
        self.clk.t += 2  # now 11s > 10s ttl
        self.assertIsNone(self.c.get("manage_services", args, self.uid))

    def test_write_invalidates_related_reads(self):
        self.c.put("manage_packages", {"action": "list"}, self.uid, _ok())
        self.c.put("manage_packages", {"action": "info", "package": "bash"}, self.uid, _ok())
        # An install flushes ALL manage_packages reads.
        n = self.c.invalidate_for_write(
            "manage_packages", {"action": "install", "package": "vim"}, self.uid)
        self.assertEqual(n, 2)
        self.assertIsNone(self.c.get("manage_packages", {"action": "list"}, self.uid))

    def test_service_restart_invalidates_only_services(self):
        self.c.put("manage_services", {"action": "status", "service": "x"}, self.uid, _ok("manage_services"))
        self.c.put("manage_packages", {"action": "list"}, self.uid, _ok())
        n = self.c.invalidate_for_write(
            "manage_services", {"action": "restart", "service": "x"}, self.uid)
        self.assertEqual(n, 1)
        # package read survives a service restart
        self.assertIsNotNone(self.c.get("manage_packages", {"action": "list"}, self.uid))

    def test_read_only_call_does_not_invalidate(self):
        self.c.put("manage_packages", {"action": "list"}, self.uid, _ok())
        n = self.c.invalidate_for_write("manage_packages", {"action": "list"}, self.uid)
        self.assertEqual(n, 0)

    def test_per_uid_isolation(self):
        args = {"action": "list"}
        self.c.put("manage_packages", args, 1000, _ok(content="user-1000"))
        # A different uid does not see uid 1000's cached read.
        self.assertIsNone(self.c.get("manage_packages", args, 1001))
        # A write by uid 1001 does not flush uid 1000's entry.
        self.c.invalidate_for_write("manage_packages", {"action": "install"}, 1001)
        self.assertIsNotNone(self.c.get("manage_packages", args, 1000))

    def test_key_ignores_source_of_request(self):
        a1 = {"action": "list", "source_of_request": "user_direct"}
        a2 = {"action": "list", "source_of_request": "ingress_derived"}
        self.c.put("manage_packages", a1, self.uid, _ok())
        self.assertIsNotNone(self.c.get("manage_packages", a2, self.uid))  # same key

    def test_lru_eviction_bounds_size(self):
        for i in range(_MAX_ENTRIES + 20):
            self.c.put("read_file", {"path": f"/f{i}"}, self.uid, _ok("read_file"))
        # Oldest evicted; size capped.
        self.assertLessEqual(len(self.c._store), _MAX_ENTRIES)
        self.assertIsNone(self.c.get("read_file", {"path": "/f0"}, self.uid))
        self.assertIsNotNone(self.c.get(
            "read_file", {"path": f"/f{_MAX_ENTRIES + 19}"}, self.uid))


if __name__ == "__main__":
    unittest.main()
