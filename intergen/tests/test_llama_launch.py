# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""LlamaManager launch-command construction — declared-capability flags.

Covers the InternVL-swap Ph2 launch capabilities: --cache-reuse is gated on
the DECLARED `cacheable` capability (so a non-cacheable DeltaNet backbone never
gets the flag — the c497290f guard), and --mmproj is emitted from the declared
`mmproj_path` (native vision), failing loud if declared-but-absent. The cmd is
built in start(); the subprocess + health-wait are mocked so these run anywhere.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from intergen.interfaces.types import StartFailure
from intergen.llama_manager import LlamaManager, ServerConfig


def _free_port() -> int:
    """A genuinely free ephemeral port for each test, so every REAL port guard
    (listener probe, foreign-holder reclaim, bind-retry) runs and PASSES on any
    box — including one whose resident daemon legitimately holds 8080. Decided
    2026-07-24: the suite must never assume exclusive ownership of the
    production port; 22 tests failed (and burned the bind-retry budget) on a
    serving box, and the prior per-class _port_has_listener mocks only covered
    one of the three probe stages."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LaunchCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = LlamaManager()
        # A real file so Path(model_path).exists() passes without patching.
        self._model = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        self._model.close()
        self.model_path = self._model.name

    def tearDown(self) -> None:
        Path(self.model_path).unlink(missing_ok=True)

    def _start(self, **kw) -> list[str]:
        """Run start() with the subprocess + health-wait mocked; return the
        argv list that would have been exec'd (or [] if start returned False)."""
        captured: dict = {}

        def _fake_popen(cmd, *a, **k):
            captured["cmd"] = cmd
            return mock.MagicMock()

        kw.setdefault("port", _free_port())
        with mock.patch.object(self.m, "_find_server",
                               return_value="/fake/llama-server"), \
                mock.patch.object(self.m, "is_running", return_value=False), \
                mock.patch.object(self.m, "_wait_for_healthy",
                                  return_value=True), \
                mock.patch.object(self.m, "_verify_served_capabilities",
                                  return_value=True), \
                mock.patch("intergen.llama_manager.subprocess.Popen",
                           side_effect=_fake_popen):
            ok = self.m.start(self.model_path, **kw)
        self.last_ok = ok
        return captured.get("cmd", [])

    def test_default_omits_cache_reuse(self):
        # cacheable defaults False (assert declared capability) — no flag.
        cmd = self._start()
        self.assertTrue(self.last_ok)
        self.assertNotIn("--cache-reuse", cmd)

    def test_non_cacheable_omits_cache_reuse(self):
        # Explicit non-cacheable (e.g. Qwen3.5 DeltaNet) — never gets the flag.
        cmd = self._start(cacheable=False, cache_reuse=256)
        self.assertNotIn("--cache-reuse", cmd)

    def test_cacheable_emits_cache_reuse(self):
        cmd = self._start(cacheable=True, cache_reuse=256)
        self.assertIn("--cache-reuse", cmd)
        self.assertEqual(cmd[cmd.index("--cache-reuse") + 1], "256")

    def test_cacheable_but_zero_reuse_omits(self):
        cmd = self._start(cacheable=True, cache_reuse=0)
        self.assertNotIn("--cache-reuse", cmd)

    def test_mmproj_emitted_when_present(self):
        with tempfile.NamedTemporaryFile(suffix=".gguf") as mmproj:
            cmd = self._start(mmproj_path=mmproj.name)
            self.assertIn("--mmproj", cmd)
            self.assertEqual(cmd[cmd.index("--mmproj") + 1], mmproj.name)

    def test_no_mmproj_by_default(self):
        cmd = self._start()
        self.assertNotIn("--mmproj", cmd)

    def test_mmproj_declared_but_absent_fails_loud(self):
        cmd = self._start(mmproj_path="/no/such/projector.gguf")
        self.assertEqual(cmd, [])          # start returned before Popen
        self.assertFalse(self.last_ok)
        self.assertIn("mmproj", (self.m._last_error or "").lower())

    def test_chat_template_emitted_when_present(self):
        with tempfile.NamedTemporaryFile(suffix=".jinja") as tpl:
            cmd = self._start(chat_template_file=tpl.name)
            self.assertIn("--chat-template-file", cmd)
            self.assertEqual(
                cmd[cmd.index("--chat-template-file") + 1], tpl.name)

    def test_no_chat_template_by_default(self):
        cmd = self._start()
        self.assertNotIn("--chat-template-file", cmd)

    def test_chat_template_missing_fails_loud(self):
        # A configured-but-absent template would silently fall back to the
        # GGUF's toolless template (the 0/33 fabrication hole) — fail loud.
        cmd = self._start(chat_template_file="/no/such/template.jinja")
        self.assertEqual(cmd, [])
        self.assertFalse(self.last_ok)
        self.assertIn("template", (self.m._last_error or "").lower())

    def test_restart_preserves_capabilities(self):
        c = ServerConfig(
            model_path="m", port=8080, context_size=16384, gpu_layers=999,
            parallel=1, jinja=True, reasoning="off",
            cacheable=True, mmproj_path="/x/mmproj.gguf",
            chat_template_file="/x/tpl.jinja",
            has_vision=True, expect_tools=True,
        )
        self.assertTrue(c.cacheable)
        self.assertEqual(c.mmproj_path, "/x/mmproj.gguf")
        self.assertEqual(c.chat_template_file, "/x/tpl.jinja")
        self.assertTrue(c.has_vision)
        self.assertTrue(c.expect_tools)


class _ManagerBase(unittest.TestCase):
    """Shared real-temp-GGUF + start()-mocking base for the integrity tests."""

    def setUp(self) -> None:
        self.m = LlamaManager()
        self.port = _free_port()
        self._model = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        self._model.close()
        self.model_path = self._model.name
        self._mmproj = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        self._mmproj.close()
        self.mmproj_path = self._mmproj.name

    def tearDown(self) -> None:
        Path(self.model_path).unlink(missing_ok=True)
        Path(self.mmproj_path).unlink(missing_ok=True)

    def _start_with_props(self, *, props=None, props_exc=None, **kw) -> bool:
        """start() with subprocess + /health mocked and the REAL /props guard
        running against a mocked GET /props (returns `props`, or raises
        `props_exc`). _verify_served_capabilities is NOT mocked here."""
        if props_exc is not None:
            urlopen = mock.MagicMock(side_effect=props_exc)
        else:
            resp = mock.MagicMock()
            resp.read.return_value = json.dumps(props or {}).encode()
            cm = mock.MagicMock()
            cm.__enter__.return_value = resp
            cm.__exit__.return_value = False
            urlopen = mock.MagicMock(return_value=cm)
        self._urlopen = urlopen
        with mock.patch.object(self.m, "_find_server",
                               return_value="/fake/llama-server"), \
                mock.patch.object(self.m, "is_running", return_value=False), \
                mock.patch.object(self.m, "_wait_for_healthy",
                                  return_value=True), \
                mock.patch.object(self.m, "stop"), \
                mock.patch("intergen.llama_manager.subprocess.Popen",
                           return_value=mock.MagicMock()), \
                mock.patch("intergen.llama_manager.urllib.request.urlopen",
                           urlopen):
            kw.setdefault("port", self.port)
            return self.m.start(self.model_path, **kw)


class StartFailureReasonTests(_ManagerBase):
    """Every fail path records the right STRUCTURAL StartFailure reason-code so
    the daemon classifies integrity vs benign without string-matching."""

    def test_model_absent_reason(self):
        self.assertFalse(self.m.start("/no/such/model.gguf"))
        self.assertEqual(self.m.last_failure, StartFailure.MODEL_FILE_ABSENT)

    def test_binary_absent_reason(self):
        with mock.patch.object(self.m, "_find_server", return_value=None):
            self.assertFalse(self.m.start(self.model_path))
        self.assertEqual(self.m.last_failure, StartFailure.BINARY_ABSENT)

    def test_chat_template_missing_reason(self):
        with mock.patch.object(self.m, "_find_server",
                               return_value="/fake/llama-server"), \
                mock.patch.object(self.m, "is_running", return_value=False):
            ok = self.m.start(self.model_path,
                              chat_template_file="/no/such/tpl.jinja")
        self.assertFalse(ok)
        self.assertEqual(self.m.last_failure,
                         StartFailure.CHAT_TEMPLATE_MISSING)

    def test_unhealthy_reason(self):
        with mock.patch.object(self.m, "_find_server",
                               return_value="/fake/llama-server"), \
                mock.patch.object(self.m, "is_running", return_value=False), \
                mock.patch.object(self.m, "_wait_for_healthy",
                                  return_value=False), \
                mock.patch.object(self.m, "stop"), \
                mock.patch("intergen.llama_manager.subprocess.Popen",
                           return_value=mock.MagicMock()):
            ok = self.m.start(self.model_path, port=self.port)
        self.assertFalse(ok)
        self.assertEqual(self.m.last_failure, StartFailure.UNHEALTHY)

    def test_success_resets_failure(self):
        # A prior failure leaves a code set; a subsequent clean start clears it.
        self.m._last_failure = StartFailure.UNHEALTHY
        ok = self._start_with_props(
            props={"chat_template_caps": {"supports_tools": True}},
            expect_tools=True)
        self.assertTrue(ok)
        self.assertEqual(self.m.last_failure, StartFailure.NONE)


class HasVisionLaunchTests(_ManagerBase):
    """has_vision is a LAUNCH-time assertion: a declared-vision model with no
    verified projector must refuse rather than serve silently text-only."""

    def test_has_vision_without_projector_fails_loud(self):
        # GGUF verified but mmproj_path None (partial provision) — must refuse.
        with mock.patch.object(self.m, "_find_server",
                               return_value="/fake/llama-server"), \
                mock.patch.object(self.m, "is_running", return_value=False):
            ok = self.m.start(self.model_path, has_vision=True,
                              mmproj_path=None)
        self.assertFalse(ok)
        self.assertEqual(self.m.last_failure, StartFailure.MMPROJ_MISSING)
        self.assertIn("vision", (self.m.last_error or "").lower())

    def test_has_vision_with_absent_projector_fails_loud(self):
        with mock.patch.object(self.m, "_find_server",
                               return_value="/fake/llama-server"), \
                mock.patch.object(self.m, "is_running", return_value=False):
            ok = self.m.start(self.model_path, has_vision=True,
                              mmproj_path="/no/such/projector.gguf")
        self.assertFalse(ok)
        self.assertEqual(self.m.last_failure, StartFailure.MMPROJ_MISSING)

    def test_has_vision_with_valid_projector_and_advertised_starts(self):
        ok = self._start_with_props(
            props={"chat_template_caps": {"supports_tools": True},
                   "modalities": {"vision": True}},
            has_vision=True, mmproj_path=self.mmproj_path, expect_tools=True)
        self.assertTrue(ok)
        self.assertEqual(self.m.last_failure, StartFailure.NONE)


class ServedCapabilityGuardTests(_ManagerBase):
    """After /health, /props must ADVERTISE the declared capabilities — a
    toolless template or an unloaded projector leaves /health green."""

    def test_tools_advertised_starts(self):
        ok = self._start_with_props(
            props={"chat_template_caps": {"supports_tools": True}},
            expect_tools=True)
        self.assertTrue(ok)

    def test_toolless_template_fails(self):
        ok = self._start_with_props(
            props={"chat_template_caps": {"supports_tools": False}},
            expect_tools=True)
        self.assertFalse(ok)
        self.assertEqual(self.m.last_failure,
                         StartFailure.TOOLS_NOT_ADVERTISED)

    def test_missing_caps_block_fails(self):
        # /props with no chat_template_caps at all → treated as unadvertised.
        ok = self._start_with_props(props={}, expect_tools=True)
        self.assertFalse(ok)
        self.assertEqual(self.m.last_failure,
                         StartFailure.TOOLS_NOT_ADVERTISED)

    def test_props_unreadable_fails_loud(self):
        # Can't read /props → fail-loud (an unverifiable capability is absent).
        import urllib.error
        ok = self._start_with_props(
            props_exc=urllib.error.URLError("refused"), expect_tools=True)
        self.assertFalse(ok)
        self.assertEqual(self.m.last_failure,
                         StartFailure.TOOLS_NOT_ADVERTISED)

    def test_no_expectations_skips_props(self):
        # No tools/vision expectation → guard returns early, /props not fetched.
        ok = self._start_with_props(props={}, expect_tools=False)
        self.assertTrue(ok)
        self.assertFalse(self._urlopen.called)

    def test_vision_not_advertised_fails(self):
        # Projector passed + tools fine, but /props says no vision = it didn't
        # load → refuse (image turns would answer blind).
        ok = self._start_with_props(
            props={"chat_template_caps": {"supports_tools": True},
                   "modalities": {"vision": False}},
            has_vision=True, mmproj_path=self.mmproj_path, expect_tools=True)
        self.assertFalse(ok)
        self.assertEqual(self.m.last_failure,
                         StartFailure.VISION_NOT_ADVERTISED)


class RestartCapTests(_ManagerBase):
    """_restart_count resets ONLY on a fully successful start (healthy AND
    declared capabilities verified). A pre-health reset (right after Popen)
    would defeat restart()'s MAX_RESTART_ATTEMPTS cap for post-spawn failures —
    a model that spawns then fails the guard every cycle would never accumulate."""

    def test_counter_held_on_capability_fail(self):
        self.m._restart_count = 2
        ok = self._start_with_props(
            props={"chat_template_caps": {"supports_tools": False}},
            expect_tools=True)
        self.assertFalse(ok)
        self.assertEqual(self.m._restart_count, 2)  # spawn succeeded, NOT reset

    def test_counter_held_on_unhealthy(self):
        self.m._restart_count = 2
        with mock.patch.object(self.m, "_find_server",
                               return_value="/fake/llama-server"), \
                mock.patch.object(self.m, "is_running", return_value=False), \
                mock.patch.object(self.m, "_wait_for_healthy",
                                  return_value=False), \
                mock.patch.object(self.m, "stop"), \
                mock.patch("intergen.llama_manager.subprocess.Popen",
                           return_value=mock.MagicMock()):
            ok = self.m.start(self.model_path)
        self.assertFalse(ok)
        self.assertEqual(self.m._restart_count, 2)  # post-spawn fail, NOT reset

    def test_counter_reset_on_full_success(self):
        self.m._restart_count = 2
        ok = self._start_with_props(
            props={"chat_template_caps": {"supports_tools": True}},
            expect_tools=True)
        self.assertTrue(ok)
        self.assertEqual(self.m._restart_count, 0)  # reset only on full success

    def test_restart_caps_when_start_keeps_failing(self):
        # The watchdog drives restart() repeatedly; restart() increments the
        # counter, caps at MAX_RESTART_ATTEMPTS, then stops calling start().
        self.m._config = ServerConfig(
            model_path=self.model_path, port=self.port, context_size=16384,
            gpu_layers=999, parallel=1, jinja=True, reasoning="off")
        with mock.patch.object(self.m, "start", return_value=False) as start, \
                mock.patch.object(self.m, "stop"):
            results = [self.m.restart() for _ in range(5)]
        self.assertEqual(results, [False] * 5)
        # start attempted only while under the cap (3), then refused.
        self.assertEqual(start.call_count, 3)


class StartFailureClassificationTests(unittest.TestCase):
    """The is_integrity split the daemon keys its conspicuous state on."""

    def test_integrity_members(self):
        for f in (StartFailure.MMPROJ_MISSING,
                  StartFailure.CHAT_TEMPLATE_MISSING,
                  StartFailure.TOOLS_NOT_ADVERTISED,
                  StartFailure.VISION_NOT_ADVERTISED):
            self.assertTrue(f.is_integrity, f.name)

    def test_operational_members(self):
        for f in (StartFailure.NONE,
                  StartFailure.MODEL_FILE_ABSENT,
                  StartFailure.BINARY_ABSENT,
                  StartFailure.SPAWN_ERROR,
                  StartFailure.UNHEALTHY,
                  StartFailure.PORT_IN_USE,
                  StartFailure.OFFLOAD_FAILED):
            self.assertFalse(f.is_integrity, f.name)


class OffloadCheckedGateTests(_ManagerBase):
    """9B lane item 1b — the launch-time offload checked-gate. On a discrete
    tier we launch the big model EXPECTING GPU acceleration; /health + /props
    prove the process is up and tool-capable but NOT that the model reached the
    GPU. If it silently fell back to CPU (0 layers offloaded, or the offload
    line is unreadable) the gate must fail loud with OFFLOAD_FAILED so the daemon
    falls to the 2B floor. Only fires when expect_offload is set, so the 2B floor
    and the CPU-pinned embedder are untouched."""

    _TOOLS_PROPS = {"chat_template_caps": {"supports_tools": True}}

    def _start_with_offload(self, banner: str, **kw) -> bool:
        # Run the REAL offload gate against an injected llama.cpp load banner;
        # /props returns tool support so only the offload gate is under test.
        # The base's per-test free port lets every REAL port guard run and pass
        # (the old _port_has_listener mock covered one of three probe stages).
        with mock.patch.object(self.m, "_read_startup_stderr",
                               return_value=banner):
            return self._start_with_props(props=self._TOOLS_PROPS,
                                          expect_tools=True, **kw)

    def test_offload_zero_fails_loud(self):
        # WEDGE: expected GPU acceleration, but the model offloaded 0 layers —
        # a silent CPU fallback. Pre-fix there is no offload gate so start()
        # returns True; the gate makes it fail loud with OFFLOAD_FAILED.
        ok = self._start_with_offload(
            "load_tensors: offloaded 0/33 layers to GPU", expect_offload=True)
        self.assertFalse(ok)
        self.assertEqual(self.m.last_failure, StartFailure.OFFLOAD_FAILED)
        self.assertIn("offload", (self.m.last_error or "").lower())

    def test_offload_confirmed_starts(self):
        ok = self._start_with_offload(
            "load_tensors: offloaded 33/33 layers to GPU", expect_offload=True)
        self.assertTrue(ok)
        self.assertEqual(self.m.last_failure, StartFailure.NONE)

    def test_offload_unreadable_fails_safe(self):
        # No offload line at all (unreadable pipe / CPU-only build) — an
        # unverifiable claim is treated as unmet: fail loud to the floor.
        ok = self._start_with_offload("", expect_offload=True)
        self.assertFalse(ok)
        self.assertEqual(self.m.last_failure, StartFailure.OFFLOAD_FAILED)

    def test_no_expect_offload_ignores_cpu(self):
        # The 2B floor / embedder run without expect_offload; a 0-layer banner
        # must NOT trip the gate.
        ok = self._start_with_offload(
            "load_tensors: offloaded 0/33 layers to GPU", expect_offload=False)
        self.assertTrue(ok)
        self.assertEqual(self.m.last_failure, StartFailure.NONE)

    def test_parse_offloaded_layers_forms(self):
        p = LlamaManager._parse_offloaded_layers
        self.assertEqual(p("load_tensors: offloaded 33/33 layers to GPU"), 33)
        self.assertEqual(p("offloaded 0/33 layers to GPU"), 0)
        self.assertEqual(p("offloaded 20 / 33 layer to GPU"), 20)
        # The final summary wins over an earlier partial line.
        self.assertEqual(
            p("offloaded 10/33 layers to GPU\noffloaded 33/33 layers to GPU"), 33)
        self.assertIsNone(p("llama_model_loader: loaded meta data"))
        self.assertIsNone(p(""))

    def test_offload_satisfied_decision(self):
        s = LlamaManager._offload_satisfied
        self.assertTrue(s(33))
        self.assertTrue(s(1))
        self.assertFalse(s(0))
        self.assertFalse(s(None))


class OffloadReportTests(unittest.TestCase):
    """PI-Z26 — the requested-vs-actual offload gate's pure functions: parse both
    (offloaded,total), name the backend, decide fully-offloaded, and the Status
    report. Text/logic only — no GPU, no server, no port."""

    def test_parse_offload_pair(self):
        p = LlamaManager._parse_offload
        self.assertEqual(p("load_tensors: offloaded 33/33 layers to GPU"), (33, 33))
        self.assertEqual(p("offloaded 0/33 layers to GPU"), (0, 33))
        self.assertEqual(p("offloaded 10 / 33 layer to GPU"), (10, 33))
        # final summary wins
        self.assertEqual(p("offloaded 10/33 layers to GPU\n"
                           "offloaded 33/33 layers to GPU"), (33, 33))
        self.assertEqual(p("no offload line here"), (None, None))
        self.assertEqual(p(""), (None, None))

    def test_parse_backend_authoritative_on_count(self):
        b = LlamaManager._parse_backend
        vk = "ggml_vulkan: Found 1 Vulkan devices: RTX 3070 Ti"
        # a Vulkan device was FOUND but 0 layers offloaded => CPU (count wins)
        self.assertEqual(b(vk, 0), "CPU")
        self.assertEqual(b(vk, None), "CPU")
        self.assertEqual(b(vk, 33), "Vulkan")
        self.assertEqual(b("using CUDA0", 33), "CUDA")
        self.assertEqual(b("some backend", 33), "GPU")   # positive but unnamed

    def test_fully_offloaded(self):
        f = LlamaManager._fully_offloaded
        self.assertTrue(f(999, 33, 33))          # all layers on GPU
        self.assertFalse(f(999, 10, 33))         # partial => mismatch
        self.assertFalse(f(999, 0, 33))          # CPU fallback
        self.assertFalse(f(999, None, None))     # unreadable => unmet
        self.assertTrue(f(0, None, None))        # CPU-by-design (embedder/2B)

    def test_offload_report_shape(self):
        m = LlamaManager()
        m._offload_requested = 999
        m._offloaded_layers = 33
        m._total_layers = 33
        m._serving_backend = "Vulkan"
        r = m.offload_report()
        self.assertEqual(r, {"backend": "Vulkan", "requested_layers": 999,
                             "offloaded_layers": 33, "total_layers": 33,
                             "fully_offloaded": True})
        # a silent CPU fallback is conspicuous in the report
        m._offloaded_layers = None
        m._serving_backend = "CPU"
        r = m.offload_report()
        self.assertEqual(r["backend"], "CPU")
        self.assertFalse(r["fully_offloaded"])

    def test_record_offload_warns_and_glass_on_mismatch(self):
        # A GPU request that landed on CPU must WARN loudly + glass-log the
        # mismatch, and populate the report — independent of expect_offload.
        import intergen.glass as glassmod
        m = LlamaManager()
        m._startup_stderr = "ggml_vulkan: Found 1 Vulkan devices\n" \
                            "load_tensors: offloaded 0/33 layers to GPU"
        events = []
        with mock.patch.object(glassmod, "emit",
                               side_effect=lambda *a, **k: events.append((a, k))), \
                self.assertLogs("intergen.llama_manager", level="WARNING") as cm:
            m._record_offload(port=8080, gpu_layers=999, expect_offload=False)
        self.assertEqual(m._serving_backend, "CPU")
        self.assertFalse(m.offload_report()["fully_offloaded"])
        self.assertTrue(any("OFFLOAD MISMATCH" in line for line in cm.output))
        self.assertTrue(events and events[0][0][:2] == ("engine", "offload_check"))

    def test_record_offload_clean_no_warn(self):
        import intergen.glass as glassmod
        m = LlamaManager()
        m._startup_stderr = "using Vulkan0\nload_tensors: offloaded 33/33 layers to GPU"
        with mock.patch.object(glassmod, "emit") as em:
            m._record_offload(port=8080, gpu_layers=999, expect_offload=True)
        self.assertEqual(m._serving_backend, "Vulkan")
        self.assertTrue(m.offload_report()["fully_offloaded"])
        # glass fires either way (observable); the detail says fully_offloaded
        self.assertTrue(em.called)
        self.assertTrue(em.call_args.kwargs["detail"]["fully_offloaded"])


class StartupBannerTests(unittest.TestCase):
    """The child's model-load banner (offload summary etc.) must be echoed to our
    logger so it lands in the daemon journal — the item-5 live offload validation
    reads the layer count from there, and the child's stderr is a captured pipe
    that otherwise never reaches the journal. Host-independent: each test uses a
    free ephemeral port, so the real port guards run and pass beside a resident
    daemon."""

    def setUp(self) -> None:
        self.m = LlamaManager()
        self.port = _free_port()
        self._model = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        self._model.close()
        self.model_path = self._model.name

    def tearDown(self) -> None:
        Path(self.model_path).unlink(missing_ok=True)

    def _start_healthy(self, banner: str) -> bool:
        with mock.patch.object(self.m, "_find_server",
                               return_value="/fake/llama-server"), \
                mock.patch.object(self.m, "is_running", return_value=False), \
                mock.patch.object(self.m, "_wait_for_healthy", return_value=True), \
                mock.patch.object(self.m, "_verify_served_capabilities",
                                  return_value=True), \
                mock.patch.object(self.m, "_read_startup_stderr",
                                  return_value=banner), \
                mock.patch("intergen.llama_manager.subprocess.Popen",
                           return_value=mock.MagicMock()):
            return self.m.start(self.model_path, port=self.port)

    def test_startup_banner_echoed_to_journal(self):
        banner = ("llm_load_tensors: offloading 33 repeating layers to GPU\n"
                  "llm_load_tensors: offloaded 33/33 layers to GPU")
        with self.assertLogs("intergen.llama_manager", level="INFO") as cm:
            ok = self._start_healthy(banner)
        self.assertTrue(ok)
        self.assertTrue(
            any("offloaded 33/33 layers to GPU" in line for line in cm.output),
            f"startup banner not echoed to the logger: {cm.output}")
        self.assertEqual(self.m._startup_stderr, banner)

    def test_blank_banner_not_logged(self):
        # An empty/whitespace capture must not emit a noise line, but the healthy
        # start still succeeds and the (empty) capture is recorded.
        with self.assertLogs("intergen.llama_manager", level="INFO") as cm:
            ok = self._start_healthy("   \n")
        self.assertTrue(ok)
        self.assertFalse(any("startup banner" in line for line in cm.output), cm.output)
        self.assertEqual(self.m._startup_stderr, "   \n")


class RuntimeStderrPumpTests(unittest.TestCase):
    """RUNTIME stderr (written AFTER /health) must stream to the daemon journal.

    The one-time startup drain captures only the pre-health banner; a crash-loop's
    later stderr (e.g. a SIGSYS from a seccomp-blocked syscall, PI-Z10) is written
    after health and, without a pump, is never read again — under the unit's
    PrivateTmp it only reached a torn-down /tmp capture, which is what forced a GPU
    diagnosis through /tmp logs instead of `journalctl -u intergen`. This drives a
    REAL pipe so the pump thread is exercised end to end. RED on the pre-pump tree.
    """

    def setUp(self) -> None:
        self.m = LlamaManager()
        self.port = _free_port()
        self._model = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        self._model.close()
        self.model_path = self._model.name

    def tearDown(self) -> None:
        if self.m._stderr_thread is not None:
            self.m._stderr_thread.join(timeout=2)
        Path(self.model_path).unlink(missing_ok=True)

    def test_runtime_stderr_streamed_to_journal(self):
        r_fd, w_fd = os.pipe()
        proc = mock.MagicMock()
        proc.stderr = os.fdopen(r_fd, "rb", 0)  # real fd -> the pump reads it
        proc.pid = 4321
        proc.poll.return_value = None            # still alive after health
        with self.assertLogs("intergen.llama_manager", level="INFO") as cm:
            with mock.patch.object(self.m, "_find_server",
                                   return_value="/fake/llama-server"), \
                    mock.patch.object(self.m, "is_running", return_value=False), \
                    mock.patch.object(self.m, "_wait_for_healthy", return_value=True), \
                    mock.patch.object(self.m, "_verify_served_capabilities",
                                      return_value=True), \
                    mock.patch.object(self.m, "_read_startup_stderr", return_value=""), \
                    mock.patch("intergen.llama_manager.subprocess.Popen",
                               return_value=proc):
                ok = self.m.start(self.model_path, port=self.port)
            self.assertTrue(ok)
            # A RUNTIME stderr line, emitted after the healthy start.
            os.write(w_fd, b"SIGSYS: bad system call (sched_setaffinity)\n")
            os.close(w_fd)                       # EOF ends the pump thread
            deadline = time.time() + 3
            while (time.time() < deadline
                   and not any("SIGSYS" in ln for ln in cm.output)):
                time.sleep(0.02)
        self.assertTrue(
            any("SIGSYS" in ln for ln in cm.output),
            f"runtime stderr not streamed to the journal: {cm.output}")

    def test_pump_survives_a_non_fd_stderr(self):
        # A test double whose fileno() is not a real fd must NOT crash the child
        # lifecycle — the pump fails safe (never raises) and start() still succeeds.
        proc = mock.MagicMock()          # proc.stderr.fileno() returns a MagicMock
        proc.pid = 5555
        proc.poll.return_value = None
        with mock.patch.object(self.m, "_find_server",
                               return_value="/fake/llama-server"), \
                mock.patch.object(self.m, "is_running", return_value=False), \
                mock.patch.object(self.m, "_wait_for_healthy", return_value=True), \
                mock.patch.object(self.m, "_verify_served_capabilities",
                                  return_value=True), \
                mock.patch.object(self.m, "_read_startup_stderr", return_value=""), \
                mock.patch("intergen.llama_manager.subprocess.Popen",
                           return_value=proc):
            ok = self.m.start(self.model_path, port=self.port)
        self.assertTrue(ok)
        if self.m._stderr_thread is not None:
            self.m._stderr_thread.join(timeout=2)
            self.assertFalse(self.m._stderr_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
