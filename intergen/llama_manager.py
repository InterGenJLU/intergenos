# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Llama server manager — subprocess lifecycle for llama-server.

Manages the llama-server process (from llama.cpp) that serves the local
LLM via an OpenAI-compatible HTTP API. Handles startup, health checks,
auto-restart on crash, and graceful shutdown.

Default endpoint: http://localhost:8080/v1/chat/completions
Health check:     http://localhost:8080/health
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import logging
import os
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from intergen import glass
from intergen.interfaces.hardware import LlamaManagerInterface
from intergen.interfaces.types import ServerHealth, StartFailure

log = logging.getLogger(__name__)

MAX_RESTART_ATTEMPTS = 3

# The start failures that are the ENGINE BINARY'S OWN, and so are worth
# retrying on a different engine rather than on the same one.
#
# Deliberately narrow. Only failures where the server never came up at all
# qualify, because those are the ones a different build could plausibly survive:
# an absent or unlaunchable binary, and a server that never became healthy —
# which is where a HIP build segfaulting at model load on an unsupported GPU
# architecture lands.
#
# Everything else is left out on purpose. MODEL_FILE_ABSENT and PORT_IN_USE are
# not the engine's doing and no other engine would fare better. The integrity
# failures mean a capability the signed manifest declared was not honoured,
# which reads as tamper and must stay conspicuous rather than being retried
# past. OFFLOAD_FAILED already has its own ratified answer — fall to the 2B
# floor loudly — and adding a second, different response to it here would make
# which one happens depend on call order.
_ENGINE_LEVEL_FAILURES = frozenset({
    StartFailure.BINARY_ABSENT,
    StartFailure.SPAWN_ERROR,
    StartFailure.UNHEALTHY,
})

# The shipped llama_server.gpu_layers default. It is a word rather than a number
# so the config reads as an intent ("use what this box has") instead of a magic
# constant the user is expected to decode.
AUTO_GPU_LAYERS = "auto"
# llama.cpp's "put every layer on the accelerator" value.
OFFLOAD_ALL_LAYERS = 999


def resolve_gpu_layers(value, tier_level: int | None = None, *,
                       plan=None) -> int:
    """The effective ``--n-gpu-layers`` for a configured value.

    The contract (decided 2026-07-31; fit clause decided 2026-08-24):

    * an explicit ``int`` is honoured VERBATIM — ``0`` pins the processor, any
      other number is used as written. The user's pin is the last word, on every
      machine;
    * anything else, including the shipped ``"auto"``, takes the offload plan
      the caller measured: whether the RESOLVED MODEL FITS THE DETECTED VIDEO
      MEMORY decides how many layers go on the card
      (:func:`intergen.gpu_offload.plan_offload`);
    * with no plan — a caller that has not measured anything — every layer is
      offloaded. Unknown is not the same as absent, and serving on the processor
      because nothing was measured is the silent failure this contract replaced.

    A string of digits is accepted as the integer it plainly is, so a value
    quoted in YAML behaves the way it reads.

    ``tier_level`` no longer decides anything and is kept only because callers
    pass it. Until 2026-08-24 a tier-1 machine resolved to 0 layers here, which
    meant a discrete card between the 3072 MB discreteness floor and the 7168 MB
    second-tier floor was detected, reported, and then never used. The tier
    chooses which model is served; the card's measured capacity decides how much
    of it is offloaded.
    """
    if isinstance(value, bool):
        # bool is an int subclass; treat it as "not a layer count" rather than
        # letting `true`/`false` read as a pin.
        pass
    elif isinstance(value, int):
        return value
    elif isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            pass
    if plan is not None:
        return int(plan.layers)
    return OFFLOAD_ALL_LAYERS

HEALTH_CHECK_TIMEOUT = 5      # seconds per health check request
# Tokens held back from an embedding input's budget for whatever the model adds
# around it (a beginning-of-sequence marker and the like). Small on purpose: it
# costs a few tokens of a long document and removes an off-by-a-token refusal.
EMBED_TOKEN_MARGIN = 8
# How long embed() will wait for a server that is up but not yet ready. Short on
# purpose: this runs on the request path, and a caller that cannot have an
# embedding right now degrades to keyword matching in milliseconds rather than
# holding a user's turn. The LONG wait for a model to load belongs to the code
# that starts the server (start() already waits out the model-load budget), not
# to every caller that asks for a vector.
EMBED_READY_GRACE_S = 2.0
# Texts per HTTP request to the embedding server.
#
# MEASURED, not chosen for tidiness. A seat machine's journal shows the wiki
# index asking for 32 passages of about 140 words in ONE request and the client
# giving up at its 30-second timeout — on every daemon start, with the whole
# index falling back to keyword matching afterwards. The failure is expensive on
# both sides: the client stops waiting while the server keeps working through
# the batch, so the server's single slot is still busy when the first user turn
# arrives.
#
# Two rates bracket the number. On a desktop with the model warm, 2116 passages
# of ~123 tokens embed at 50 ms each, so 32 of them take about 1.6 s. On the
# laptop whose journal carries the failure, the same 32 did not finish in 30 s
# while the chat model was still loading beside them — at least 0.94 s per
# passage, roughly nineteen times slower. A request therefore has to be sized
# for the machine having its worst minute, not its best: at 8 texts the slow
# case lands near 7.5 s, four times inside the timeout, while the fast case
# pays four extra round trips of a few milliseconds each.
#
# Callers that ask for more are split across several requests; their own
# batching and budgets are untouched.
EMBED_TEXTS_PER_REQUEST = 8
STARTUP_TIMEOUT = 60           # seconds: the FLOOR of the model-load budget (startup_budget_seconds)
STARTUP_POLL_INTERVAL = 1.0    # seconds between health polls during startup

# How much of a dead child's stderr is recorded, and WHICH END of it.
#
# This used to be `[:500]`, and 500 characters is less than llama.cpp's startup
# banner: a device list, build_info and system_info run past it before the server
# has said anything about what went wrong. So the recorded error was always
# banner and the reason was always in the part that was thrown away. Measured on
# a dual-GPU workstation 2026-08-26: the journal line for a failed 35B start is
# exactly 500 characters and ends mid-token at "8 | CPU : SS", and a real failing
# launch (a bad --model path) prints 2165 bytes of which every line naming the
# cause sits in the 1665 that were dropped. Two and a half hours of a dead
# assistant could not be diagnosed from what was kept.
#
# The cap is not removed, because a runaway child must not be able to flood the
# journal; it is raised far above any real failure's size and taken from the END,
# because llama.cpp prints the banner first and the reason last. When the text is
# longer than the cap the elision is stated in the record itself rather than
# leaving a reader to wonder whether the beginning was ever there.
FAILURE_STDERR_MAX_CHARS = 16384


def _failure_tail(stderr: str) -> str:
    """The part of a failed launch's stderr worth recording — the END."""
    if len(stderr) <= FAILURE_STDERR_MAX_CHARS:
        return stderr
    dropped = len(stderr) - FAILURE_STDERR_MAX_CHARS
    return (f"[{dropped} earlier characters elided; the last "
            f"{FAILURE_STDERR_MAX_CHARS} follow]\n"
            + stderr[-FAILURE_STDERR_MAX_CHARS:])
SHUTDOWN_TIMEOUT = 10          # seconds to wait for graceful shutdown
REAP_GRACE = 3                 # seconds a stale/orphaned own-server gets on SIGTERM before SIGKILL

# How long a SIGKILLed model server can take to actually leave the machine.
#
# MEASURED, not estimated. A killed llama-server does NOT always die promptly:
# its exit runs the GPU driver's file-close path, and a driver stuck in an
# uninterruptible RPC wait holds the whole exit there — the process stays alive,
# and so does its listening socket. Two measurements bracket it, both on the
# same box (a laptop with a discrete NVIDIA card on the open-source driver):
#
#   * healthy case, reproduced three times on a quiet machine: the child was
#     gone and the port accepted a bind within 0.25s of the SIGKILL;
#   * the pathological case, from the incident this budget exists for: the port
#     was STILL refusing a bind on the probe 98 seconds after the kill, and had
#     released by a manual restart 4 minutes later. So the real teardown ran to
#     at least 98s, and the wait that was refusing it lasted 11.5s.
#
# The consequence of a wait shorter than the teardown is not a slow recovery,
# it is NO recovery: every restart attempt burned its budget, declared the port
# lost, and the machine sat with no language model until a person intervened.
# Refusal is the correct verdict for a port that is genuinely gone — but it is
# not a verdict any wait can honestly reach in a twentieth of the time the
# release actually takes. 150s covers the measured floor with room and still
# bounds the wait, so the caller always gets an answer.
GPU_TEARDOWN_BUDGET_S = 150.0

# Boot-path bind-race recovery — give chat-engine bring-up the recover-then-retry
# discipline the embedding server already gets from the watchdog. After a
# stale/orphaned server is reaped its LISTEN socket is gone, but TIME_WAIT
# sockets left by its served connections can linger on the port and make the
# kernel refuse a fresh bind() for a few seconds — invisible to the LISTEN-only
# _port_has_listener check. start() used to relaunch immediately, the child lost
# the bind() and exited, and (unlike the embed server, which the watchdog
# retries) nothing re-attempted the chat engine, so the model stayed SILENTLY
# unserved until a manual restart. Wait, bounded, for the port to actually
# accept a bind before launching; on exhaustion start() fails LOUD and the
# watchdog / next start retries — never a silent stall.
#
# The BUDGET is the number that matters here, not the attempt count: the wait
# has to outlast the teardown above, so the schedule is derived from
# GPU_TEARDOWN_BUDGET_S rather than a hand-picked number of tries. A free port
# still binds on the first probe with no delay at all — the budget only costs
# anything when the port is genuinely still held.
PORT_BIND_BASE_DELAY = 0.5     # seconds; exponential backoff between bind probes
PORT_BIND_MAX_DELAY = 10.0     # seconds; backoff cap


def _bind_backoff_schedule() -> list[float]:
    """The backoff delays whose sum covers GPU_TEARDOWN_BUDGET_S.

    Exponential from PORT_BIND_BASE_DELAY, capped at PORT_BIND_MAX_DELAY. The
    cap keeps the probe responsive early (a port that frees in a second is not
    waited on for ten) and keeps the log readable late (one line per probe).
    """
    delays: list[float] = []
    total = 0.0
    delay = PORT_BIND_BASE_DELAY
    while total < GPU_TEARDOWN_BUDGET_S:
        delays.append(delay)
        total += delay
        delay = min(delay * 2, PORT_BIND_MAX_DELAY)
    return delays


# Model-load budget. STARTUP_TIMEOUT was a flat 60 seconds for every model, and
# a flat number is a constant that a bigger model outgrows SILENTLY: the load
# simply fails to finish inside it, the server is killed as unhealthy, and
# nothing in the failure says "it needed more time".
#
# The floor stays 60s, so no model that fits today loses anything. On top of it
# the budget adds an allowance for the bytes actually being loaded — model plus
# vision projector. The 2B-with-projector pair on the box this was measured on
# (1.9 GB total) reached /health in 3.77-6.53s across three runs, which is
# roughly 3.4 s/GB with a warm page cache. The allowance is set an order of
# magnitude above that, at 30 s/GB, so it still holds when the cache is cold and
# the file is coming off a disk reading at ~40 MB/s. A 20 GB model therefore
# gets ~11 minutes rather than one flat minute.
STARTUP_SECONDS_PER_GB = 30.0
_BYTES_PER_GB = float(1024 ** 3)


def startup_budget_seconds(total_model_bytes: int | float) -> float:
    """Seconds to allow a model to load and answer /health.

    ``total_model_bytes`` is every byte the launch has to read — the model file
    plus the vision projector when there is one. Returns at least
    STARTUP_TIMEOUT, so this can only ever grant more time than the fixed budget
    it replaced, never less.
    """
    try:
        size = max(0.0, float(total_model_bytes))
    except (TypeError, ValueError):
        size = 0.0
    return STARTUP_TIMEOUT + STARTUP_SECONDS_PER_GB * (size / _BYTES_PER_GB)


def _file_size_or_zero(path: str | None) -> int:
    """Bytes at ``path``; 0 when it is unset or unreadable.

    A size that cannot be read must not raise on the launch path — it costs the
    budget its per-gigabyte term and falls back to the floor, which is exactly
    the behaviour that shipped before.
    """
    if not path:
        return 0
    try:
        return os.path.getsize(path)
    except OSError:
        return 0

# PI-Z27 facet (ii) — PREVENTION: make an orphaned llama-server impossible.
# subprocess.Popen's default leaves the child's survival to either the daemon's
# own stop() running or the unit's cgroup KillMode reaching it — and NEITHER
# holds on the abnormal teardown paths (daemon crash/SIGKILL, D-Bus deactivation
# racing the cgroup kill, or a GDM DynamicUser greeter session's user@<uid>
# slice tearing down). On those paths the child reparents to init and survives
# holding its port (the uid-60584 orphan observed on the Zephyrus, 2026-07-07).
# PR_SET_PDEATHSIG asks the KERNEL to signal the child the instant its parent
# dies — prevention that does not depend on any shutdown code running or on the
# unit's KillMode, complementing the unit-side lifecycle fix (r24).
_PR_SET_PDEATHSIG = 1  # linux/prctl.h


def _load_prctl():
    """Pre-resolve libc.prctl at import (never inside the post-fork child, which
    must do no dlopen/symbol lookup). Returns the callable, or None off Linux /
    if libc is unavailable — in which case the preexec is a no-op and the reap
    path is the sole (still-correct) orphan defense."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6",
                           use_errno=True)
        libc.prctl.restype = ctypes.c_int
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                               ctypes.c_ulong, ctypes.c_ulong]
        return libc.prctl
    except (OSError, AttributeError):
        return None


_PRCTL = _load_prctl()


def _die_with_parent() -> None:
    """preexec_fn (runs in the child between fork and exec): ask the kernel to
    SIGKILL this child the instant its parent — the InterGen daemon — dies, so a
    daemon crash / SIGKILL / greeter-session teardown can never leave an
    orphaned llama-server holding its port (PI-Z27 facet ii). Kept minimal — a
    single pre-resolved libc.prctl call, no allocation-heavy or lock-taking work
    — because preexec_fn runs post-fork in the multithreaded daemon. Caveat:
    PDEATHSIG is delivered on the death of the parent THREAD that spawned us; the
    daemon starts the server from a long-lived thread (init / the watchdog), and
    the verified-ownership reap recovers any orphan this does not prevent."""
    if _PRCTL is not None:
        _PRCTL(_PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0)

# llama.cpp prints the offload summary to stderr during model load, e.g.
# "load_tensors: offloaded 33/33 layers to GPU". The count is the authoritative
# signal that GPU acceleration actually engaged (a card too small to hold the
# model, or a CPU-only fallback, reports 0/N or emits no such line at all). The
# final match wins (llama.cpp may print per-device lines before the summary).
_OFFLOAD_RE = re.compile(
    r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers?\s+to\s+GPU", re.IGNORECASE
)


@dataclass
class ServerConfig:
    """Configuration snapshot for restart."""
    model_path: str
    port: int
    context_size: int
    gpu_layers: int
    parallel: int
    jinja: bool
    reasoning: str
    embedding: bool = False  # AI-12: serve /embedding (embedding-only instance)
    cache_reuse: int = 256   # llama-server --cache-reuse: min chunk for prefix KV reuse
    cacheable: bool = False  # backbone supports prefix-cache (declared capability)
    mmproj_path: str | None = None  # vision projector → --mmproj (declared has_vision)
    chat_template_file: str | None = None  # tool-capable template → --chat-template-file
    has_vision: bool = False  # model declares vision → mmproj REQUIRED + /props vision asserted
    expect_tools: bool = False  # chat server → assert /props advertises tool support
    expect_offload: bool = False  # discrete/GPU tier → assert the model actually offloaded to the GPU, else OFFLOAD_FAILED (fall to the 2B floor); FALSE for the CPU-pinned embedder and the 2B floor
    device: str | None = None  # ggml device pin (e.g. "Vulkan1") — multi-GPU boxes pin the serving model to ONE card so the other stays free (judge/eval co-residency); None = llama.cpp default (all devices)
    server_path: str | None = None  # the engine's server binary (from select_serving_engine) — the binary that ENUMERATED devices must be the binary that LAUNCHES (device names are backend-local); None = engine-aware _find_server()


class LlamaManager(LlamaManagerInterface):
    """Manages the llama-server subprocess lifecycle."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._config: ServerConfig | None = None
        self._start_time: float = 0.0
        self._restart_count: int = 0
        self._requests_served: int = 0
        self._last_error: str | None = None
        self._last_failure: StartFailure = StartFailure.NONE
        self._startup_stderr: str = ""  # child's captured model-load banner (drained once after /health)
        # Seconds this launch may take to load and answer /health. start()
        # re-derives it from the model bytes; the floor is what a caller gets
        # if it never launched anything.
        self._startup_budget: float = float(STARTUP_TIMEOUT)
        self._stderr_thread: threading.Thread | None = None  # runtime-stderr -> journal pump
        # PI-Z26 requested-vs-actual offload gate: the last start's serving reality,
        # surfaced in Status so a silent CPU fallback is impossible to miss.
        self._offload_requested: int = 0        # gpu_layers asked for (999 = all)
        self._offloaded_layers: int | None = None   # what llama-server actually put on GPU
        self._total_layers: int | None = None       # the model's layer count
        self._serving_backend: str = "unknown"       # Vulkan / CUDA / CPU / …
        # embed()-while-down is logged ONCE per down-episode (reset on a
        # successful start): before first start it is the by-design
        # first-boot/onboarding window (server deliberately held down), and
        # repeating it at ERROR each call was pure alarm fatigue.
        self._embed_notrunning_logged: bool = False
        # READY is not RUNNING. Popen returns the instant the child exists, and
        # is_running() has been true from that moment — so embed() used to post
        # into a server that had not finished loading its model and sat for the
        # whole request timeout. An installed machine showed three of those, 30
        # seconds each, at every daemon start. This event is set only when
        # /health has answered ok for OUR child, and cleared on every launch and
        # every stop.
        self._ready: threading.Event = threading.Event()
        # One line per not-ready episode, like the not-running one above.
        self._embed_notready_logged: bool = False

    def start(self, model_path: str, *,
              port: int = 8080,
              context_size: int = 16384,
              gpu_layers: int = 999,
              parallel: int = 1,
              jinja: bool = True,
              reasoning: str = "off",
              embedding: bool = False,
              cache_reuse: int = 256,
              cacheable: bool = False,
              mmproj_path: str | None = None,
              chat_template_file: str | None = None,
              has_vision: bool = False,
              expect_tools: bool = False,
              expect_offload: bool = False,
              device: str | None = None,
              server_path: str | None = None) -> bool:
        """Start llama-server with the given model.

        device: a ggml device name (e.g. "Vulkan1" from --list-devices) pins
        the instance to ONE accelerator — on a multi-GPU box the serving model
        takes its assigned card and leaves the other free (judge/eval
        co-residency). Ignored for CPU-pinned instances (gpu_layers=0 always
        wins with --device none). None = llama.cpp's default device set.

        server_path: the engine's server binary, when the caller already
        resolved the engine (select_serving_engine) — required whenever a
        device pin came from that engine's own --list-devices, because device
        names are backend-local ("CUDA0" is not "Vulkan0") and the enumerating
        binary must be the launching binary. Set-but-absent REFUSES to start
        (BINARY_ABSENT) — a resolved engine choice is never silently
        substituted with a different engine. None = engine-aware
        _find_server().

        AI-12: pass embedding=True for an embedding-only instance — adds
        --embedding so the server exposes POST /embedding. Pair with
        gpu_layers=0 to pin it to CPU (the small nomic-embed model is
        ~80-140MB) and a distinct port so it runs alongside the chat server.

        cacheable / mmproj_path are DECLARED model capabilities (sourced from
        the signed models-manifest, WC's one-capability-descriptor): the launch
        builder asserts what the model declares instead of guessing per-flag.
          - cacheable=True emits --cache-reuse; FALSE (default) omits it, so a
            non-cacheable backbone (e.g. Qwen3.5 Gated DeltaNet, which llama.cpp
            cannot prefix-cache) can never get the flag and can never be bricked
            by an upstream llama.cpp that turns the current soft-ignore into a
            hard-fail-at-load. Standard-attention (GQA) backbones declare True.
          - mmproj_path set emits --mmproj <path> for native vision/OCR.
          - chat_template_file set emits --chat-template-file <path> (a
            tool-capable template); fails loud if set-but-absent so a missing
            template can never silently fall back to a toolless one.

        has_vision / expect_tools are launch-time INTEGRITY assertions (the
        launch-integrity layer):
          - has_vision=True REQUIRES a verified mmproj_path — a declared vision
            model whose projector is unset/absent is a partially-provisioned
            model that would serve SILENTLY text-only; refuse it rather than lie
            about being able to see. After health it also asserts /props
            advertises modalities.vision (the projector really loaded).
          - expect_tools=True asserts /props advertises
            chat_template_caps.supports_tools after the server is healthy — a
            toolless template leaves /health green but reopens the 0/33
            fabrication hole; this turns that silent regression into a loud boot
            failure.
        Each failure records a STRUCTURAL StartFailure reason-code (see
        last_failure) so the daemon classifies a declared-but-unhonored
        capability as a conspicuous integrity failure, not the benign no-model
        degrade — never by string-matching the error text.
        """
        self._last_failure = StartFailure.NONE
        # Verify the model file exists
        if not Path(model_path).exists():
            self._last_error = f"Model file not found: {model_path}"
            self._last_failure = StartFailure.MODEL_FILE_ABSENT
            log.error(self._last_error)
            return False

        # Find llama-server binary. An explicit server_path is a resolved
        # engine choice: honoured verbatim, and refused loudly when absent —
        # falling back to a DIFFERENT engine's binary would launch a server
        # whose device namespace does not match the device pin computed
        # against this one.
        if server_path is not None:
            import os
            if not (os.path.isfile(server_path) and os.access(server_path, os.X_OK)):
                self._last_error = (
                    f"resolved engine server binary absent: {server_path!r}")
                self._last_failure = StartFailure.BINARY_ABSENT
                log.error(self._last_error)
                return False
        else:
            server_path = self._find_server()
            if server_path is None:
                self._last_error = "llama-server binary not found"
                self._last_failure = StartFailure.BINARY_ABSENT
                log.error(self._last_error)
                return False

        # Stop existing server if running
        if self.is_running():
            log.info("Stopping existing llama-server before starting new one")
            self.stop()

        # Build command.
        # WHY the chat-server flag defaults (M6 leg-3b — the conversational serving
        # profile for a SINGLE-USER embodiment box, not a multi-tenant endpoint):
        #   --n-gpu-layers 999 : offload EVERY layer to the dGPU. 999 is the
        #       "all layers" sentinel — the 9B Q4_K_M weights (~5.8 GB) + KV fit
        #       inside the 16 GB dGPU with headroom, so a partial offload would
        #       only add a PCIe hop per token for no memory reason. (The embedding
        #       instance overrides this to 0 at its call site to stay CPU-resident
        #       beside the GPU chat server.)
        #   --parallel 1       : ONE decode slot. A single-user box serves one
        #       conversation at a time, so the whole KV budget backs that one
        #       sequence (longest context, best cache-reuse) instead of being
        #       sliced across idle parallel slots.
        #   --reasoning off    : suppress chain-of-thought token emission. Chat
        #       turns want the answer, not a visible CoT preamble — off spends no
        #       decode budget on reasoning tokens, lowering TTFT and total latency
        #       (the fast-path/conversational budgets in latency_budgets.py assume
        #       this). Tool-call correctness comes from the tool template below,
        #       not from CoT.
        cmd = [
            server_path,
            "--model", model_path,
            "--port", str(port),
            "--ctx-size", str(context_size),
            "--n-gpu-layers", str(gpu_layers),
            "--parallel", str(parallel),
            "--reasoning", reasoning,
        ]
        if gpu_layers == 0:
            # F24 (2026-07-21): a CPU-resident instance must never initialize an
            # accelerator device. llama.cpp's Vulkan backend enumerates + touches
            # the device even at -ngl 0; on a broken/unstable ICD (NVK on GA104:
            # vk::DeviceLostError during init, the nouveau GSP fault class) the
            # server ABORTS -> health timeout -> engine down with a healthy model
            # on disk. --device none skips device init entirely; GPU instances
            # (gpu_layers > 0) are unaffected and stay under the earn-gate's
            # audition. Live-proven on the NVK box: crash -> healthy in 2s.
            cmd += ["--device", "none"]
        elif device:
            # Multi-GPU device pin: serve on exactly the assigned card (ggml
            # device name from --list-devices). Without it llama.cpp's Vulkan
            # backend splits layers across EVERY visible device — including the
            # card reserved for the judge/eval instance on dual-GPU boxes.
            # elif, not if: a CPU-pinned instance's --device none is supreme.
            cmd += ["--device", device]
        if cacheable and cache_reuse > 0:
            # Reuse the cached KV for the longest common prefix across requests
            # (via KV shifting) — the ~437-tok system prompt is identical every
            # turn, so without this each new user message re-prefills the whole
            # prefix (~6-10s cold TTFT on the 2B CPU floor = the per-turn
            # "fall asleep" latency). With it, a turn prefills only its new
            # tokens -> first-token toward the ~150ms warm floor. Gated on the
            # DECLARED cacheable capability: a non-cacheable (DeltaNet) backbone
            # never gets the flag (no-op today, brick-risk under a stricter
            # upstream llama.cpp).
            cmd += ["--cache-reuse", str(cache_reuse)]
        if has_vision and not mmproj_path:
            # The model DECLARES vision (signed manifest) but no projector path
            # was resolved — a partially-provisioned vision model (GGUF verified,
            # projector failed/absent). a8492d9b's fail-loud only fires when
            # mmproj_path is PASSED-but-absent; this closes the gap where
            # has_vision=True + mmproj_path=None would otherwise launch SILENTLY
            # text-only, the exact trust gap (the OS believes it can see the
            # screen and quietly cannot). has_vision is a LAUNCH-time assertion,
            # not only a download-time one — refuse.
            self._last_error = (
                "model declares vision (has_vision) but no mmproj projector "
                "was provided — refusing to launch silently text-only"
            )
            self._last_failure = StartFailure.MMPROJ_MISSING
            log.error(self._last_error)
            return False
        if mmproj_path:
            # Native vision/OCR: the paired projector (declared has_vision).
            # Fail loud if declared but absent — a silently-text-only server is
            # the same trust gap class as a missing model.
            if not Path(mmproj_path).exists():
                self._last_error = f"mmproj file not found: {mmproj_path}"
                self._last_failure = StartFailure.MMPROJ_MISSING
                log.error(self._last_error)
                return False
            cmd += ["--mmproj", mmproj_path]
        if chat_template_file:
            # Tool-capable chat template (e.g. the Qwen-Hermes template that
            # makes llama-server inject our tool schemas AND parse the model's
            # tool_calls back) — THE fix that flips InternVL from 0/33 toolless
            # fabrication to 122/122 dispatch. Fail loud if configured but
            # absent: a missing template silently falls back to the GGUF's
            # embedded (toolless) template, reopening the fabrication hole — the
            # same trust-gap class as a missing model. (The /props supports_tools
            # guard is the runtime backstop that asserts the served capability.)
            if not Path(chat_template_file).exists():
                self._last_error = (
                    f"chat template file not found: {chat_template_file}"
                )
                self._last_failure = StartFailure.CHAT_TEMPLATE_MISSING
                log.error(self._last_error)
                return False
            cmd += ["--chat-template-file", chat_template_file]
        if jinja:
            # --jinja selects llama-server's Jinja chat-template engine (over the
            # legacy built-in formatter). It is the PREREQUISITE for the tool
            # dialect: the tool-capable --chat-template-file above is a Jinja
            # template, and only the Jinja path renders our injected tool schemas
            # into the prompt and parses the model's tool_calls back out. Without
            # --jinja the tool template is inert and the model falls back to
            # toolless (fabricating) output — the same hole the template guards.
            cmd.append("--jinja")
        if embedding:
            # Embedding-only instance: expose POST /embedding. (Pair with
            # gpu_layers=0 at the call site to keep it CPU-resident.)
            cmd.append("--embedding")
            # An embedding request is not streamed: llama.cpp processes the
            # whole input in ONE physical batch and refuses anything longer
            # with "input (N tokens) is too large to process. increase the
            # physical batch size". The default physical batch is 512 while
            # this instance's context is larger, so every input between the two
            # failed with HTTP 500 — measured on an installed machine on four
            # ordinary questions in three days, and reproduced here against the
            # real engine binary. Size the physical batch to the context, which
            # is the largest input the server can hold at all.
            #
            # Both flags are passed on purpose. The engine clamps the LOGICAL
            # batch down to the physical one for an embedding server and says so
            # in its own startup log; passing both keeps the argv honest about
            # what the server will run with instead of leaving a 2048/512 pair
            # for a reader to reconcile.
            cmd += ["--batch-size", str(context_size),
                    "--ubatch-size", str(context_size)]

        # Save config for restart
        self._config = ServerConfig(
            model_path=model_path,
            port=port,
            context_size=context_size,
            gpu_layers=gpu_layers,
            parallel=parallel,
            jinja=jinja,
            reasoning=reasoning,
            embedding=embedding,
            cache_reuse=cache_reuse,
            cacheable=cacheable,
            mmproj_path=mmproj_path,
            chat_template_file=chat_template_file,
            has_vision=has_vision,
            expect_tools=expect_tools,
            expect_offload=expect_offload,
            device=device,
            server_path=server_path,
        )

        # Pre-launch bind-ownership check. If the target port is ALREADY held by
        # another process, our child cannot bind it and will exit — but the
        # foreign server answers GET /health, which _wait_for_healthy would
        # otherwise misread as OUR success (the mask-not-verify defect that hid
        # the embedding-down collision: the GDM greeter session runs its own
        # InterGen daemon, which binds 8080/8081 first at cold boot before the
        # real user logs in). Refuse loudly with a structural reason instead. The
        # watchdog retries start() later; once the greeter session tears down and
        # frees the port this check passes and the bind succeeds. _config is
        # already set above, so a watchdog restart() after this failure can retry.
        if self._port_has_listener(port):
            # The port is already held. Before refusing, try to recover it from
            # OUR OWN stale/orphaned llama-server (PI-Z27 facet i): a crash-looped
            # child we lost the handle to, or an orphan a prior daemon incarnation
            # left behind — the case that stranded the watchdog ("3 strikes ->
            # giving up -> manual restart"). _reap_stale_owner VERIFIES ownership
            # (exe + our model + our port — never assumed from the port) and
            # kill-reaps ONLY our own stale/orphaned child; a genuinely foreign
            # holder, or a LIVE peer daemon's server (the GDM-greeter cold-boot
            # collision), is refused and left untouched. If it frees the port we
            # fall through and bind; otherwise refuse as before.
            if not self._reap_stale_owner(port, model_path):
                self._last_error = (
                    f"port {port} is already held by another process; refusing "
                    f"to launch — a foreign server on the port must never count "
                    f"as ours"
                )
                self._last_failure = StartFailure.PORT_IN_USE
                log.error(self._last_error)
                return False
            log.info("recovered port %d from our own stale llama-server; "
                     "launching", port)

        # Confirm the port will actually accept a bind before launching. Even
        # once _reap_stale_owner has freed the LISTEN socket, TIME_WAIT sockets
        # from the reaped server's served connections can linger on the port and
        # make the kernel refuse a fresh bind() for a few seconds — the
        # LISTEN-only checks above cannot see them, which is the boot-path race
        # that relaunched the chat engine too early, lost the bind, and left the
        # model silently unserved with no retry. Wait, bounded, for the port to
        # bind; fail LOUD if it never does (never a silent unserved state).
        if not self._await_port_bindable(port):
            self._last_error = (
                f"port {port} did not become bindable within "
                f"{sum(_bind_backoff_schedule()):.1f}s — a prior socket has "
                f"not released; refusing to launch into a lost bind"
            )
            self._last_failure = StartFailure.PORT_IN_USE
            log.error(self._last_error)
            return False

        # The model-load budget for THIS launch, from the bytes this launch has
        # to read. Computed here, where the paths are known, so _wait_for_healthy
        # measures against what is actually loading instead of a fixed number
        # that a larger model would outgrow without saying so.
        self._startup_budget = startup_budget_seconds(
            _file_size_or_zero(model_path) + _file_size_or_zero(mmproj_path))
        log.info("model-load budget for this launch: %.0fs",
                 self._startup_budget)

        log.info("Starting llama-server: %s", " ".join(cmd))

        # Not ready until /health says so for this launch.
        self._ready.clear()

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # PI-Z27 facet (ii): the child dies with the daemon (kernel-
                # enforced), so it can never orphan and hold the port after an
                # abnormal daemon teardown.
                preexec_fn=_die_with_parent,
            )
            self._start_time = time.time()

            # Wait for server to become healthy
            if self._wait_for_healthy(port):
                # The server has loaded its model and answered /health as our
                # own child: callers waiting on readiness are released here.
                self._ready.set()
                self._embed_notready_logged = False
                # New up-episode: the next embed()-while-down deserves its own
                # single non-DEBUG line.
                self._embed_notrunning_logged = False
                # Capture the child's startup banner ONCE — the model-load lines
                # llama.cpp writes to stderr before /health answers, including the
                # GPU offload summary — and echo it to our logger so it reaches
                # the daemon journal. The child's stderr is a captured PIPE, so
                # without this the banner never lands in the journal: the item-5
                # live offload validation on the discrete card reads the layer
                # count from there, and a start that only speaks on stderr would
                # otherwise leave no journal trace. Drained once here;
                # _verify_gpu_offload reuses this capture (a second drain reads
                # an already-empty pipe).
                self._startup_stderr = self._read_startup_stderr()
                if self._startup_stderr.strip():
                    log.info("llama-server startup banner (port %d):\n%s",
                             port, self._startup_stderr.strip())
                # The drain above captured the PRE-health banner only. After
                # /health nothing reads the child's stderr pipe again, so RUNTIME
                # stderr — a crash-loop's death lines (e.g. a SIGSYS from a
                # seccomp-blocked sched_setaffinity, PI-Z10) — accumulates unread
                # and, under the unit's PrivateTmp, only ever reached a torn-down
                # /tmp capture, never the journal (this is what forced a GPU
                # diagnosis through /tmp logs). Start a pump that streams every
                # subsequent stderr line to the daemon logger, so runtime failures
                # land in `journalctl -u intergen`. It also drains the pipe so a
                # spewing child never blocks on a full stderr buffer.
                self._start_stderr_pump()
                # PI-Z26 requested-vs-actual offload gate. Record the SERVING
                # REALITY (backend + offloaded/total layers) from the load banner
                # for every start — GPU or CPU — and, when GPU acceleration was
                # requested (gpu_layers>0) but the model did NOT fully offload,
                # WARN loudly + glass-log the mismatch. This is independent of the
                # expect_offload fatal gate below (which only fires on the discrete
                # tier): two independent layers (a systemd sandbox hiding the DRM
                # nodes — PI-Z28 — and a VRAM shortfall) already produced silent
                # CPU serving, so the reality is now always surfaced, never inferred.
                self._record_offload(port, gpu_layers, expect_offload)
                # Served-capability guard: /health only proves the PROCESS is
                # up; it does NOT prove the tool-capable template or the vision
                # projector actually loaded. A toolless template (the 0/33
                # fabrication hole) or a dropped projector (silent text-only)
                # leaves /health green but /props missing the capability. Assert
                # the declared capabilities are actually advertised; fail-loud
                # (with a structural reason-code) if not.
                if not self._verify_served_capabilities(
                        port, expect_tools=expect_tools,
                        expect_vision=bool(mmproj_path)):
                    # _verify_served_capabilities set _last_error + _last_failure
                    self.stop()
                    return False
                # Offload checked-gate: /health + /props prove the server is up
                # and tool-capable, but NOT that the model reached the GPU. On a
                # discrete tier we launched the big model EXPECTING acceleration;
                # if it silently fell back to CPU (0 layers offloaded, or the
                # offload line is unreadable) the 9B/35B is unusably slow, so
                # fail LOUD with OFFLOAD_FAILED and let the daemon fall to the 2B
                # floor rather than serve an unusably-slow model. Only fires when
                # GPU acceleration was expected (expect_offload), so the 2B floor
                # and the CPU-pinned embedder are unaffected.
                if expect_offload and not self._verify_gpu_offload():
                    # _verify_gpu_offload set _last_error + _last_failure
                    self.stop()
                    return False
                # Reset the restart counter ONLY on a fully successful start —
                # healthy AND declared capabilities verified. Resetting right
                # after Popen (pre-health/pre-capability) would defeat restart()'s
                # MAX_RESTART_ATTEMPTS cap for post-spawn failures (UNHEALTHY /
                # TOOLS|VISION_NOT_ADVERTISED): a model that spawns then fails the
                # guard every cycle would reset to 0 each time and never
                # accumulate, so llama_manager's own cap could never fire — the
                # bound would rest solely on the watchdog's monotonic counter.
                self._restart_count = 0
                log.info("llama-server started successfully on port %d", port)
                return True

            # Server didn't become healthy — kill it
            self._last_error = "Server failed to become healthy within timeout"
            self._last_failure = StartFailure.UNHEALTHY
            log.error(self._last_error)
            self.stop()
            return False

        except OSError as e:
            self._last_error = f"Failed to start llama-server: {e}"
            self._last_failure = StartFailure.SPAWN_ERROR
            log.error(self._last_error)
            return False

    def stop(self) -> None:
        """Stop the llama-server subprocess gracefully.

        This function ALWAYS RETURNS. It used to be able to raise, and that was
        the first failure of the incident this recovery work exists for: the
        child was wedged in an uninterruptible kernel teardown, the post-SIGKILL
        ``wait(timeout=5)`` raised subprocess.TimeoutExpired, and nothing caught
        it — the handler below catches OSError, which TimeoutExpired is not. The
        exception escaped stop(), escaped restart(), and reached the watchdog as
        "Restart action failed: Command '[...]' timed out after 5 seconds". The
        restart attempt was spent without a single launch being attempted, and
        the message pointed at a launch timeout that does not exist anywhere in
        this file.

        Two things follow from that, and both are load-bearing:
          * a child that will not die is a REPORTED condition, never an
            exception — the caller's job is to go on and start a server, and
            the next start()'s ownership-verified reap path is what deals with
            a survivor still holding the port;
          * the wait after the SIGKILL is sized against a real teardown
            (GPU_TEARDOWN_BUDGET_S), because a five-second wait against a
            98-second teardown never learned anything before declaring failure.
        """
        if self._process is None:
            return

        self._ready.clear()
        log.info("Stopping llama-server (PID %d)", self._process.pid)
        pid = self._process.pid

        # Try SIGTERM first (graceful)
        try:
            self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=SHUTDOWN_TIMEOUT)
                log.info("llama-server stopped gracefully")
            except subprocess.TimeoutExpired:
                # Force kill
                log.warning("Graceful shutdown timed out, sending SIGKILL")
                self._process.kill()
                try:
                    self._process.wait(timeout=GPU_TEARDOWN_BUDGET_S)
                    log.info("llama-server (PID %d) exited after SIGKILL", pid)
                except subprocess.TimeoutExpired:
                    # SIGKILL cannot be refused by a process, so reaching here
                    # means the KERNEL is still tearing the process down — the
                    # GPU-driver close path is the case measured on this
                    # hardware. Say so, name the pid, and return: the port it
                    # still holds is recovered by the next start()'s
                    # ownership-verified reap, and a restart that never happens
                    # is a worse outcome than a restart that has to reap.
                    log.error(
                        "llama-server (PID %d) did not exit within %.0fs of "
                        "SIGKILL — the kernel is still tearing it down (a GPU "
                        "driver close path does this). Releasing our handle; "
                        "the next start verifies ownership and reaps it.",
                        pid, GPU_TEARDOWN_BUDGET_S)
        except OSError as e:
            log.warning("Error stopping llama-server: %s", e)
        finally:
            # Join the stderr pump: the child is dead, so its stderr is at EOF and
            # the pump returns promptly (bounded so a wedged pipe can't hang stop()).
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=SHUTDOWN_TIMEOUT)
                self._stderr_thread = None
            # Close stdout/stderr pipes to avoid ResourceWarning
            if self._process is not None:
                if self._process.stdout:
                    self._process.stdout.close()
                if self._process.stderr:
                    self._process.stderr.close()
            self._process = None

    def start_saved_config(self) -> bool:
        """Launch again with the configuration the last successful start used.

        This is the ONE place a stored ServerConfig is mapped back onto start()
        keyword arguments. restart() calls it, and so does any caller that
        deliberately stopped the server and now wants the SAME server back — the
        pause/resume path, where the stop was a user-requested handover of the
        accelerator to another program rather than a fault.

        It does NOT stop anything first (the caller owns that) and it does NOT
        spend the restart budget: the budget exists to stop a genuinely broken
        server from being relaunched forever, and a deliberate pause is not a
        failure. Without this separation a user who launched a few games in one
        session would exhaust MAX_RESTART_ATTEMPTS and InterGen would refuse to
        come back for the rest of the session.

        A single mapping site is also what keeps a newly added start() argument
        from being silently dropped on the relaunch path — the exact defect
        recorded on expect_offload, which a second hand-maintained argument list
        had omitted so the offload gate never fired on a watchdog restart.
        """
        if self._config is None:
            self._last_error = "No previous configuration to start with"
            log.error(self._last_error)
            return False

        return self.start(
            self._config.model_path,
            port=self._config.port,
            context_size=self._config.context_size,
            gpu_layers=self._config.gpu_layers,
            parallel=self._config.parallel,
            jinja=self._config.jinja,
            reasoning=self._config.reasoning,
            embedding=self._config.embedding,
            cache_reuse=self._config.cache_reuse,
            cacheable=self._config.cacheable,
            mmproj_path=self._config.mmproj_path,
            chat_template_file=self._config.chat_template_file,
            has_vision=self._config.has_vision,
            expect_tools=self._config.expect_tools,
            # PI-Z26: a restart must re-verify GPU offload too — a server that
            # silently CPU-falls-back after a restart is the same defect as at
            # first start (previously expect_offload was dropped on restart, so
            # the fatal offload gate never fired on the watchdog's restarts).
            expect_offload=self._config.expect_offload,
            device=self._config.device,
            # The device pin was computed against THIS engine binary's device
            # namespace — a restart must relaunch the same binary.
            server_path=self._config.server_path,
        )

    def _advance_engine(self) -> bool:
        """Move to the next engine on the preference ladder. True if it moved.

        WHY THIS EXISTS. A restart relaunches the SAME binary, because the
        device pin was computed in that binary's device namespace. That is
        right when the server died for a reason the binary is not responsible
        for. It is exactly wrong when the binary itself is what cannot run
        here: the HIP build carries device code only for the architectures it
        was compiled for and SEGFAULTS at model load on anything else, so
        relaunching it three times spends the whole restart budget on an engine
        that was never going to start, the watchdog gives up, and the assistant
        goes silent on a machine that had a working Vulkan engine installed the
        entire time.

        So a start failure that looks like the engine's own failure drops to
        the next rung instead. Returns False when there is no next rung, and
        the caller then fails loudly — a ladder with nothing below it is the end
        of the line, not a reason to loop.
        """
        if self._config is None:
            return False
        try:
            from intergen.serving_device import (next_engine_after,
                                                 select_serving_engine)
        except Exception as e:                        # pragma: no cover
            log.warning("engine ladder unavailable (%s)", e)
            return False

        current = self._config.server_path
        current_engine = None
        try:
            from intergen.serving_device import ENGINE_SERVER_PATHS
            for engine, path in ENGINE_SERVER_PATHS.items():
                if current and path == current:
                    current_engine = engine
                    break
        except Exception:                             # pragma: no cover
            pass

        nxt = next_engine_after(current_engine)
        if nxt is None:
            log.error("no engine left below %s — the preference ladder is "
                      "exhausted", current_engine or current or "the current "
                      "engine")
            return False
        engine, path = nxt
        if path == current:
            return False
        log.warning("engine %s failed to start; dropping to %s (%s)",
                    current_engine or current or "unknown", engine, path)
        # The device pin belongs to the OLD binary's device namespace — device
        # names are backend-local, so carrying "Vulkan1" onto a different
        # engine would pin to whatever that name happens to mean there, or to
        # nothing. Clear it and let the new engine's own selection run.
        self._config = replace(self._config, server_path=path, device=None)
        return True

    # The gaps between the attempts retry_transient_start makes, in seconds.
    # Short enough that a daemon start is not visibly held open, long enough for
    # the case this exists for: a device released by the process that just
    # stopped, which is free within a couple of seconds. Two gaps for three
    # attempts, seven seconds in total.
    TRANSIENT_RETRY_BACKOFF_S = (2.0, 5.0, 10.0)

    def retry_transient_start(self, *, attempts: int = 3,
                              sleep=time.sleep) -> bool:
        """Start from the saved configuration, retrying a TRANSIENT failure.

        WHY THIS EXISTS. A chat model server that failed once used to be down for
        the life of the daemon: the caller dropped the manager, which also removed
        the watchdog that could have recovered it, and nothing tried again.
        Measured on a dual-GPU workstation 2026-08-26 — a start eleven seconds
        after a model re-drive released the same card recorded UNHEALTHY and the
        assistant answered nothing for two and a half hours, while the identical
        command run by hand later loaded and served normally.

        Only a failure the taxonomy calls transient is retried (StartFailure.
        is_transient); anything else returns immediately so an absent model file
        or an integrity failure degrades honestly instead of spending the budget.
        `sleep` is injected so a test can assert the back-off without waiting it
        out.
        """
        for attempt in range(1, max(1, attempts) + 1):
            if self.start_saved_config():
                if attempt > 1:
                    log.info("llama-server started on attempt %d/%d — the first "
                             "failure was transient", attempt, attempts)
                return True
            failure = self._last_failure
            if not failure.is_transient:
                log.info("start failed with %s, which a retry cannot fix — "
                         "not retrying", failure.name)
                return False
            if attempt >= attempts:
                break
            gap = self.TRANSIENT_RETRY_BACKOFF_S[
                min(attempt - 1, len(self.TRANSIENT_RETRY_BACKOFF_S) - 1)]
            log.warning("llama-server start attempt %d/%d failed with %s (%s); "
                        "retrying in %.0fs", attempt, attempts, failure.name,
                        self._last_error, gap)
            sleep(gap)
        log.error("llama-server did not start after %d attempts; last failure "
                  "%s: %s", attempts, self._last_failure.name, self._last_error)
        return False

    def restart(self) -> bool:
        """Stop and restart with the same configuration."""
        if self._config is None:
            self._last_error = "No previous configuration to restart with"
            log.error(self._last_error)
            return False

        self._restart_count += 1
        if self._restart_count > MAX_RESTART_ATTEMPTS:
            self._last_error = (
                f"Max restart attempts ({MAX_RESTART_ATTEMPTS}) exceeded"
            )
            log.error(self._last_error)
            return False

        log.info("Restarting llama-server (attempt %d/%d)",
                 self._restart_count, MAX_RESTART_ATTEMPTS)
        self.stop()

        if self.start_saved_config():
            return True

        # The relaunch failed. If the failure is the engine's own — it never
        # came up at all — try the next engine rather than spending the rest of
        # the budget on a binary that cannot run here.
        if self._last_failure in _ENGINE_LEVEL_FAILURES and self._advance_engine():
            log.info("retrying the restart on the next engine")
            return self.start_saved_config()
        return False

    def health(self) -> ServerHealth:
        """Check server health via GET /health endpoint."""
        if not self.is_running():
            return ServerHealth(
                running=False,
                model_loaded=False,
                last_error=self._last_error,
            )

        port = self._config.port if self._config else 8080

        try:
            req = urllib.request.Request(f"http://localhost:{port}/health")
            with urllib.request.urlopen(req, timeout=HEALTH_CHECK_TIMEOUT) as resp:
                data = json.loads(resp.read())

            uptime = time.time() - self._start_time if self._start_time else 0.0
            status = data.get("status", "")

            return ServerHealth(
                running=True,
                model_loaded=status == "ok",
                uptime_seconds=round(uptime, 1),
                requests_served=self._requests_served,
                last_error=None if status == "ok" else f"status: {status}",
            )
        except Exception as e:
            return ServerHealth(
                running=True,  # process is alive, but health check failed
                model_loaded=False,
                uptime_seconds=time.time() - self._start_time if self._start_time else 0.0,
                last_error=f"Health check failed: {e}",
            )

    @property
    def last_failure(self) -> StartFailure:
        """Structured reason the most recent start() failed (StartFailure.NONE
        when it succeeded or has not run). The daemon classifies an integrity
        failure on this rather than by string-matching last_error."""
        return self._last_failure

    @property
    def last_error(self) -> str | None:
        """Human-readable detail of the most recent failure (pairs with
        last_failure for the daemon's conspicuous integrity-failure message)."""
        return self._last_error

    @property
    def pid(self) -> int | None:
        """OS PID of the running llama-server subprocess, or None if stopped."""
        return self._process.pid if self._process else None

    @property
    def context_size(self) -> int:
        """Configured context window in tokens, or 0 if never started."""
        return self._config.context_size if self._config else 0

    @property
    def model_name(self) -> str:
        """Friendly model name derived from the served GGUF path."""
        if self._config and self._config.model_path:
            return Path(self._config.model_path).stem
        return "—"

    def get_endpoint(self) -> str:
        """Return the server's chat completions endpoint URL."""
        port = self._config.port if self._config else 8080
        return f"http://localhost:{port}/v1/chat/completions"

    def get_embedding_endpoint(self) -> str:
        """Return the server's OpenAI-compatible embeddings endpoint URL."""
        port = self._config.port if self._config else 8080
        return f"http://localhost:{port}/v1/embeddings"

    def is_ready(self) -> bool:
        """True when this launch has answered /health as our own child.

        RUNNING means a process exists; READY means it has loaded its model and
        will answer. The two are minutes apart for a cold model file, and the
        gap is where the blind request timeouts lived.
        """
        return self._ready.is_set()

    def _mark_ready(self) -> None:
        """Declare readiness without a launch (the health wait's own callers and
        tests that drive embed() with a mocked transport)."""
        self._ready.set()

    def _await_ready(self, budget: float) -> bool:
        """Wait up to ``budget`` seconds for readiness. Never longer."""
        if self._ready.is_set():
            return True
        if budget <= 0:
            return False
        return self._ready.wait(timeout=budget)

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """Block until this server is ready, or the timeout passes.

        For a caller that legitimately has to wait out a model load — the
        daemon's own startup and self-heal paths, which start the server and
        then want its vectors — as opposed to embed(), which is on the request
        path and degrades in milliseconds instead.
        """
        budget = self._startup_budget if timeout is None else timeout
        return self._await_ready(budget)

    def _post_json(self, path: str, obj: dict, timeout: float):
        """POST a JSON body to one of this server's endpoints; None on failure."""
        port = self._config.port if self._config else 8080
        req = urllib.request.Request(
            f"http://localhost:{port}{path}",
            data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:  # noqa: BLE001 — degrade, never crash a caller
            log.debug("%s request failed: %s", path, e)
            return None

    def _fit_to_context(self, texts: list[str], timeout: float) -> list[str]:
        """Shorten any input this server cannot process, and say so.

        The server refuses an input longer than its physical batch, which this
        manager sizes to the context. Anything longer has to be shortened by
        SOMEONE: doing it here, deliberately and in the log, is the difference
        between a shortened answer and an HTTP 500 that drops retrieval to
        keyword-only without telling the user anything.

        The cheap pre-filter is exact rather than a guess: a token is at least
        one character, so a text shorter than the context IN CHARACTERS cannot
        exceed it in tokens and is sent untouched. Only a text at or above that
        length pays for a /tokenize round trip, and the server's own tokenizer —
        not an estimate — decides. A small margin is left for whatever special
        tokens the model adds.
        """
        limit = self._config.context_size if self._config else 0
        if limit <= 0:
            return texts
        budget = max(1, limit - EMBED_TOKEN_MARGIN)
        out: list[str] = []
        for text in texts:
            if len(text) < limit:
                out.append(text)
                continue
            tok = self._post_json("/tokenize", {"content": text}, timeout)
            tokens = tok.get("tokens") if isinstance(tok, dict) else None
            if not isinstance(tokens, list) or len(tokens) <= budget:
                # Either it fits, or the server would not tell us — send it as
                # the author wrote it and let the server answer for itself.
                out.append(text)
                continue
            back = self._post_json("/detokenize", {"tokens": tokens[:budget]},
                                   timeout)
            shortened = back.get("content") if isinstance(back, dict) else None
            if not isinstance(shortened, str) or not shortened:
                # Cannot shorten on token boundaries; fall back to a character
                # cut at the budget, which is under the limit by construction.
                shortened = text[:budget]
            log.warning(
                "embed(): an input of %d tokens is longer than this embedding "
                "server's %d-token context — shortened to %d tokens (the tail "
                "was dropped) so the request is answered instead of refused",
                len(tokens), limit, budget)
            out.append(shortened)
        return out

    def embed(self, texts: list[str], *,
              timeout: float = 30.0,
              ready_timeout: float | None = None) -> list[list[float]] | None:
        """Embed a batch of texts via the local llama-server /v1/embeddings.

        AI-12: replaces the sentence-transformers/torch/huggingface stack — the
        same nomic-embed-text-v1.5 model, served as GGUF by an --embedding
        llama-server instance, reached over stdlib urllib (no PyPI/SDK, the
        same transport llm.py + health() already use). Returns one vector per
        input text (order preserved), or None on failure so the caller can
        degrade rather than crash.
        """
        if not texts:
            return []
        if not self.is_running():
            # By-design state, not an anomaly: callers degrade on None and the
            # semantic layer retains pending intents for recovery. One line per
            # down-episode; INFO before the first start (the deliberate
            # first-boot/onboarding hold), WARNING when a previously-started
            # server is down; repeats at DEBUG.
            if not self._embed_notrunning_logged:
                self._embed_notrunning_logged = True
                if not self._start_time:
                    log.info(
                        "embed() called before the embedding server has "
                        "started (expected during the first-boot/onboarding "
                        "window) — embeddings degrade until it is up; "
                        "repeats logged at DEBUG")
                else:
                    log.warning(
                        "embed() called but the embedding server is not "
                        "running — embeddings degrade; repeats logged at "
                        "DEBUG")
            else:
                log.debug("embed() called but embedding server is not running")
            return None

        # A running server that has not finished loading answers nothing: the
        # request simply sits until it times out. Wait on readiness instead,
        # bounded by this launch's own model-load budget, and degrade with one
        # line if it does not arrive.
        budget = EMBED_READY_GRACE_S if ready_timeout is None else ready_timeout
        if not self._await_ready(budget):
            if not self._embed_notready_logged:
                self._embed_notready_logged = True
                log.warning(
                    "embed() skipped: the embedding server process is up but "
                    "has not answered /health yet (waited %.1fs) — embeddings "
                    "degrade until it is ready; repeats logged at DEBUG",
                    budget)
            else:
                log.debug("embed() skipped: embedding server not ready yet")
            return None

        texts = self._fit_to_context(texts, timeout)

        # One request per bounded slice, so no single request can outlive the
        # timeout while the server is still working on it.
        if len(texts) > EMBED_TEXTS_PER_REQUEST:
            log.info("embed(): %d texts sent as %d requests of at most %d, so "
                     "no single request outruns its %.0fs timeout",
                     len(texts),
                     -(-len(texts) // EMBED_TEXTS_PER_REQUEST),
                     EMBED_TEXTS_PER_REQUEST, timeout)
            out: list[list[float]] = []
            for i in range(0, len(texts), EMBED_TEXTS_PER_REQUEST):
                part = self._embed_one_request(
                    texts[i:i + EMBED_TEXTS_PER_REQUEST], timeout)
                if part is None:
                    # Partial results are not returned: a caller that asked for
                    # N vectors and gets fewer cannot line them up with its own
                    # inputs. Degrade whole, as before.
                    return None
                out.extend(part)
            return out
        return self._embed_one_request(texts, timeout)

    def _embed_one_request(self, texts: list[str],
                           timeout: float) -> list[list[float]] | None:
        """One POST to /v1/embeddings. Vectors in input order, or None."""
        payload = json.dumps({"input": texts, "model": "embedding"}).encode()
        req = urllib.request.Request(
            self.get_embedding_endpoint(),
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except Exception as e:  # noqa: BLE001 — network/parse; degrade, don't crash
            log.error("embed() request failed: %s", e)
            return None

        # OpenAI-compatible shape: {"data": [{"embedding": [...], "index": i}, ...]}.
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            log.error("embed() got %d rows for %d inputs",
                      len(rows) if isinstance(rows, list) else -1, len(texts))
            return None
        # Sort by index so vector order matches input order regardless of
        # server-side reordering, then extract the embedding vectors.
        try:
            ordered = sorted(rows, key=lambda r: r.get("index", 0))
            return [r["embedding"] for r in ordered]
        except Exception as e:  # noqa: BLE001 — untrusted response shape; degrade, don't crash
            # The response shape is not trusted (version mismatch / error envelope
            # / proxy), so any malformed row — a missing key (KeyError), a non-dict
            # row whose .get/.__getitem__ is absent (AttributeError/TypeError), etc.
            # — degrades to None rather than crashing the caller, matching the
            # network handler above. The row-count guard already caught the gross
            # shape errors; this is the per-row net.
            log.error("embed() malformed embedding rows: %s", e)
            return None

    def is_running(self) -> bool:
        """Return True if the server process is alive."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def _find_server(self) -> str | None:
        """Find the llama-server binary — engine-aware.

        The engine selector's choice (the per-vendor preference table over the
        engine builds actually present: CUDA /opt/llama-cpp-cuda, HIP
        /opt/rocm, Vulkan /usr/bin) is consulted first, so a box that carries
        a preferred engine variant launches THAT server rather than whichever
        binary the PATH finds. A caller that resolved the engine itself passes
        ``server_path`` to :meth:`start` instead and never reaches this
        fallback chain.
        """
        import os
        import shutil
        try:
            from intergen.serving_device import select_serving_engine
            engine, path = select_serving_engine()
            if os.path.isfile(path) and os.access(path, os.X_OK):
                log.info("engine selector chose %s (%s)", engine, path)
                return path
        except Exception as e:
            log.warning("engine selection unavailable (%s); falling back to "
                        "path search", e)

        # Generic lookup — the pre-engine-selection behavior, kept as the
        # fail-safe floor.
        path = shutil.which("llama-server")
        if path:
            return path

        # Check common build locations
        candidates = [
            "/usr/local/bin/llama-server",
            "/usr/bin/llama-server",
            Path.home() / "llama.cpp" / "build" / "bin" / "llama-server",
            Path.home() / "builds" / "llama.cpp" / "build" / "bin" / "llama-server",
        ]
        for candidate in candidates:
            p = Path(candidate)
            if p.exists() and p.is_file():
                return str(p)

        return None

    @staticmethod
    def _listening_socket_inodes(port: int) -> set[str]:
        """Socket inodes in LISTEN state bound to ``port`` (IPv4 + IPv6).

        Read from /proc/net/tcp{,6} — stdlib + procfs only, no privilege and no
        psutil. The local-address column is hex ``IP:PORT``; LISTEN state is
        ``0A``. Used to detect a foreign holder (pre-launch) and to prove
        bind-ownership (post-health) so a stranger answering on the port can
        never be mistaken for our own child.
        """
        inodes: set[str] = set()
        hexport = f"{port:04X}"
        for path in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(path) as f:
                    next(f, None)  # skip header
                    for line in f:
                        parts = line.split()
                        if len(parts) < 10 or parts[3] != "0A":  # 0A == LISTEN
                            continue
                        if parts[1].rsplit(":", 1)[-1].upper() == hexport:
                            inodes.add(parts[9])
            except OSError:
                continue
        return inodes

    @staticmethod
    def _pid_socket_inodes(pid: int) -> set[str]:
        """Socket inodes held by ``pid`` (from /proc/<pid>/fd symlinks)."""
        inodes: set[str] = set()
        fd_dir = f"/proc/{pid}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    target = os.readlink(f"{fd_dir}/{fd}")
                except OSError:
                    continue
                if target.startswith("socket:["):
                    inodes.add(target[len("socket:["):-1])
        except OSError:
            pass
        return inodes

    def _port_has_listener(self, port: int) -> bool:
        """True if ANY process already listens on ``port`` (pre-launch guard)."""
        return bool(self._listening_socket_inodes(port))

    def _pid_owns_port(self, pid: int, port: int) -> bool:
        """True if the LISTEN socket on ``port`` is held by ``pid`` — proves the
        server answering on the port is OUR child, not a foreign holder."""
        return bool(
            self._listening_socket_inodes(port) & self._pid_socket_inodes(pid)
        )

    def owns_port(self) -> bool:
        """True if OUR running child owns the LISTEN socket on its configured port.

        The periodic-probe analogue of the startup bind-ownership gate in
        _wait_for_healthy: GET /health proves SOMETHING answers on the port, not
        that it is our child. A foreign holder (the GDM greeter session's own
        InterGen daemon, on the fixed-port cold-boot collision) answers /health
        green — without this the watchdog's PERIODIC health probe would read that
        foreign green as healthy and never rebind our server, leaving the startup
        check ownership-aware but the periodic check not. Fails closed (procfs
        unreadable → not owned); the watchdog's 2-consecutive-failure requirement
        de-bounces a one-off /proc read race so a genuinely-healthy child is not
        needlessly restarted.
        """
        if not self.is_running():
            return False
        pid = self._process.pid if self._process else None
        port = self._config.port if self._config else 8080
        return pid is not None and self._pid_owns_port(pid, port)

    # --- PI-Z27 facet (i): verified-ownership reap of our own stale server -----
    #
    # The pre-launch guard above refuses ANY port listener. That is right for a
    # foreign holder but strands us when the holder is OUR OWN llama-server whose
    # handle we lost — a crash-looped child, or (after a daemon restart, when
    # self._process is None) an orphan the prior incarnation left behind. Without
    # a reap path, start() refuses forever and the watchdog exhausts its 3
    # restarts. These helpers identify a holder as ours by VERIFIED ownership
    # (kernel-resolved exe + our exact model + our exact port — the security
    # posture: verify, never assume from the port) and reap ONLY our own; a foreign
    # holder, or a LIVE peer daemon's in-use server, is refused and never killed.
    # A different-uid holder (a DynamicUser greeter orphan) is unreadable AND
    # unkillable from an unprivileged daemon, so it correctly reads as foreign
    # here and is handled by PREVENTION (_die_with_parent), not by this reap.

    def _pids_listening_on_port(self, port: int) -> set[int]:
        """PIDs (readable by us — our own uid) holding a LISTEN socket on
        ``port``. Inverts the port->socket-inode map against each process's held
        socket inodes; a cross-uid holder's /proc/<pid>/fd is unreadable so it
        does not appear (correct — we could neither inspect nor kill it)."""
        target = self._listening_socket_inodes(port)
        if not target:
            return set()
        pids: set[int] = set()
        try:
            entries = os.listdir("/proc")
        except OSError:
            return pids
        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            if self._pid_socket_inodes(pid) & target:
                pids.add(pid)
        return pids

    @staticmethod
    def _proc_cmdline(pid: int) -> list[str]:
        """argv of ``pid`` from /proc/<pid>/cmdline (NUL-separated), or []."""
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read()
        except OSError:
            return []
        return [a.decode(errors="replace") for a in raw.split(b"\0") if a]

    @staticmethod
    def _proc_exe_name(pid: int) -> str | None:
        """basename of the kernel-resolved /proc/<pid>/exe (cannot be spoofed by
        argv rewriting), or None if unreadable (different uid / process gone)."""
        try:
            return os.path.basename(os.readlink(f"/proc/{pid}/exe"))
        except OSError:
            return None

    @staticmethod
    def _proc_ppid(pid: int) -> int | None:
        """Parent PID from /proc/<pid>/status (PPid:), or None if unreadable."""
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        return int(line.split()[1])
        except (OSError, ValueError, IndexError):
            return None
        return None

    @staticmethod
    def _argv_has_opt(argv: list[str], opt: str, val: str) -> bool:
        """True if argv contains ``opt`` immediately followed by ``val`` — the
        space-separated form the launch builder emits (['--port', '8080'])."""
        for i in range(len(argv) - 1):
            if argv[i] == opt and argv[i + 1] == val:
                return True
        return False

    def _is_our_llama_server(self, pid: int, port: int,
                             model_path: str) -> bool:
        """VERIFY (never assume from the port) that ``pid`` is one of OUR own
        llama-server processes serving ``model_path`` on ``port``.

        Identity signal: the kernel-resolved exe basename is ``llama-server``
        (unspoofable) OR argv[0]'s basename is ``llama-server`` (covers an
        unreadable exe; an impostor rewriting argv to our binary is, at worst,
        killed — never an information exposure, and it is squatting our port
        under our name). AND argv must carry ``--port <port>`` and
        ``--model <model_path>``, so we only ever reap the holder of THIS port
        serving OUR model — never some other llama-server. A genuinely foreign
        holder (nginx, a stranger) matches none of this and is refused, never
        killed (the fail-safe: an unverifiable holder is treated as foreign)."""
        cmd = self._proc_cmdline(pid)
        if not cmd:
            return False
        exe = self._proc_exe_name(pid)
        argv0 = os.path.basename(cmd[0])
        if exe != "llama-server" and argv0 != "llama-server":
            return False
        return (self._argv_has_opt(cmd, "--port", str(port))
                and self._argv_has_opt(cmd, "--model", model_path))

    def _has_live_peer_daemon_parent(self, pid: int) -> bool:
        """True iff ``pid``'s parent is a LIVE InterGen daemon that is NOT us —
        i.e. the holder is another daemon's in-use server (the cold-boot GDM
        greeter collision: the greeter session's own daemon holds 8080 and is
        still up). Such a holder is REFUSED and waited out, never reaped — killing
        a live peer's server would start a port fight (the behavior the existing
        pre-launch refusal was built to preserve). Everything else — parent is us
        (our own lost-handle child), or init / systemd / dead (an orphan whose
        managing daemon is gone) — is reapable."""
        ppid = self._proc_ppid(pid)
        if ppid is None or ppid <= 1:
            return False                      # reparented to init -> orphan
        if ppid == os.getpid():
            return False                      # OUR own child (lost handle) -> reap
        pcmd = self._proc_cmdline(ppid)
        if not pcmd:
            return False                      # parent already dead -> orphan
        joined = " ".join(pcmd)
        return ("intergen" in joined
                and ("python" in joined or "/usr/bin/intergen" in joined))

    def _await_port_bindable(self, port: int) -> bool:
        """Poll until (127.0.0.1, ``port``) actually accepts a bind, or the
        bounded retry budget is spent.

        The probe MUST predict the child's bind, which means it must use the
        child's socket options. It sets SO_REUSEADDR because **llama-server sets
        it** (its HTTP server does so when it creates the listening socket), and
        the probe is only useful insofar as it is faithful to that.

        This was measured, not assumed. The probe previously omitted SO_REUSEADDR
        on the stated premise that a bare bind was "exactly llama-server's own
        bind". That premise was wrong, and it inverted the probe's purpose: a
        just-stopped server leaves its served connections in TIME_WAIT on the
        serving port for the kernel's 60s timeout, a bare bind() refuses while
        any of them remain, and llama-server itself binds straight over them in
        ~0.1s. So the probe was refusing to start a child that would have started
        fine — burning the whole retry budget and reporting the port unusable on
        a restart the child could have served immediately.

        SO_REUSEADDR narrows this to exactly the lingering-TIME_WAIT case and
        does NOT weaken the gate: it never permits a bind while another socket is
        actively LISTENING on the same address and port (that would require
        SO_REUSEPORT, which is deliberately not set). A genuine port owner is
        still refused here, and _port_has_listener / _reap_stale_owner still own
        that case.

        THE WAIT IS SIZED AGAINST A REAL TEARDOWN. It began as ~11.5s, chosen
        against a settle window of about 12 seconds. That is the right size for
        a TIME_WAIT drain and far too small for the other thing that holds this
        port: a SIGKILLed server whose exit is stuck in the GPU driver's close
        path, measured on this hardware still holding the port 98 seconds after
        the kill. The schedule now covers GPU_TEARDOWN_BUDGET_S. It costs a
        healthy start nothing — a free port binds on the first probe, with no
        sleep at all — and it is still BOUNDED, so the caller always gets an
        answer and the watchdog thread always reaches its next health check.

        True as soon as the port binds; False once the budget is spent (the
        caller fails LOUD)."""
        schedule = _bind_backoff_schedule()
        attempts = len(schedule) + 1
        for attempt in range(1, attempts + 1):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Match the child's bind semantics; see the docstring for why this
            # is required for the probe to predict anything at all.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as e:
                if attempt == attempts:
                    log.error("port %d still not bindable after %d attempts "
                              "spanning %.1fs (%s) — a prior socket has not "
                              "released", port, attempt, sum(schedule), e)
                    return False
                delay = schedule[attempt - 1]
                log.warning("port %d not yet bindable (attempt %d/%d: %s) — "
                            "waiting %.1fs for the socket to release", port,
                            attempt, attempts, e, delay)
                time.sleep(delay)
                continue
            finally:
                probe.close()
            if attempt > 1:
                log.info("port %d became bindable on attempt %d", port, attempt)
            return True
        return False

    def _reap_stale_owner(self, port: int, model_path: str) -> bool:
        """Recover ``port`` from OUR OWN stale/orphaned llama-server (PI-Z27
        facet i). Returns True iff the port is now free to bind; False iff a
        holder must be REFUSED (a genuinely foreign process, or a live peer
        daemon's server — neither is ever killed). Called only when
        _port_has_listener(port) is already True."""
        holders = self._pids_listening_on_port(port)
        if not holders:
            return True                       # raced free between check and here
        ours: list[int] = []
        for pid in holders:
            if self._is_our_llama_server(pid, port, model_path):
                ours.append(pid)
            else:
                log.error(
                    "port %d is held by a process that is NOT our llama-server "
                    "(pid %d, exe=%s) — refusing to launch and refusing to kill "
                    "a foreign holder", port, pid, self._proc_exe_name(pid))
                return False
        for pid in ours:
            if self._has_live_peer_daemon_parent(pid):
                log.warning(
                    "port %d is held by a LIVE peer InterGen daemon's "
                    "llama-server (pid %d) — refusing; the watchdog retries once "
                    "the peer (e.g. a GDM greeter session) frees it", port, pid)
                return False
            log.warning(
                "reaping our own STALE/ORPHANED llama-server (pid %d) holding "
                "port %d — its managing daemon is gone (or we lost its handle); "
                "killing it and taking over the port", pid, port)
            self._kill_pid(pid)
        if self._port_has_listener(port):
            log.error("reaped our stale server(s) on port %d but the port is "
                      "still held — refusing", port)
            return False
        return True

    def _kill_pid(self, pid: int) -> None:
        """SIGTERM then (after REAP_GRACE) SIGKILL a pid we hold no Popen handle
        for (a lost-handle own child or an orphan). Best-effort: a permission /
        already-gone race just ends the attempt (the caller re-checks the port).
        Reaps the zombie if it turns out to be our direct child."""
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return                            # already gone / not permitted
        deadline = time.time() + REAP_GRACE
        while time.time() < deadline:
            if not self._pid_alive(pid):
                break
            time.sleep(0.1)
        if self._pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            os.waitpid(pid, os.WNOHANG)       # reap if it was our child
        except OSError:
            pass                              # ChildProcessError etc.: not ours

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """True if ``pid`` exists and is not a zombie (state != 'Z'). The state
        is the first field after the closing ')' in /proc/<pid>/stat, so the
        rsplit is robust to a comm containing spaces or parentheses."""
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[-1].split()
        except OSError:
            return False
        return bool(fields) and fields[0] != "Z"

    def _verify_served_capabilities(self, port: int, *,
                                    expect_tools: bool,
                                    expect_vision: bool) -> bool:
        """Assert the running server actually ADVERTISES the declared capabilities.

        /health proves the process is up; it does NOT prove the tool-capable
        chat template or the vision projector loaded. GET /props and check the
        served capability flags (shape confirmed on the running InternVL server):
          - chat_template_caps.supports_tools — false means a toolless template
            loaded (the 0/33 fabrication hole the chat-template-file fix closes);
          - modalities.vision — false means the projector did not load, so the
            model would answer image turns blind.
        A miss fails the start with a structural integrity reason-code. If /props
        itself cannot be read we FAIL rather than assume served (fail-loud: an
        unverifiable capability is treated as absent, never as present).
        """
        if not (expect_tools or expect_vision):
            return True
        try:
            req = urllib.request.Request(f"http://localhost:{port}/props")
            with urllib.request.urlopen(req, timeout=HEALTH_CHECK_TIMEOUT) as resp:
                data = json.loads(resp.read())
        except Exception as e:  # noqa: BLE001 — any /props read failure = can't confirm → fail-loud
            self._last_error = (
                f"could not read /props to verify served capabilities: {e}"
            )
            # Attribute the reason to the primary expectation; tools take
            # precedence (the chat server's core contract).
            self._last_failure = (
                StartFailure.TOOLS_NOT_ADVERTISED if expect_tools
                else StartFailure.VISION_NOT_ADVERTISED
            )
            log.error(self._last_error)
            return False

        if expect_tools:
            caps = data.get("chat_template_caps") or {}
            if not caps.get("supports_tools"):
                self._last_error = (
                    "served chat template does not advertise tool support "
                    "(/props chat_template_caps.supports_tools is false) — a "
                    "toolless template loaded; tool dispatch would silently "
                    "fabricate"
                )
                self._last_failure = StartFailure.TOOLS_NOT_ADVERTISED
                log.error(self._last_error)
                return False
        if expect_vision:
            mods = data.get("modalities") or {}
            if not mods.get("vision"):
                self._last_error = (
                    "served model does not advertise vision (/props "
                    "modalities.vision is false) — the projector did not load; "
                    "image turns would answer blind"
                )
                self._last_failure = StartFailure.VISION_NOT_ADVERTISED
                log.error(self._last_error)
                return False
        return True

    @staticmethod
    def _parse_offload(load_text: str) -> tuple[int | None, int | None]:
        """Extract (offloaded, total) layers from the final llama.cpp
        ``offloaded N/M layers to GPU`` summary line, or (None, None) if absent
        (a pure-CPU run prints none). Pure/text-only — unit-testable without a GPU.
        """
        if not load_text:
            return (None, None)
        last = None
        for last in _OFFLOAD_RE.finditer(load_text):
            pass
        if last is None:
            return (None, None)
        return (int(last.group(1)), int(last.group(2)))

    @staticmethod
    def _parse_offloaded_layers(load_text: str) -> int | None:
        """The N in the final ``offloaded N/M layers to GPU`` line, or None.
        Retained for the fatal offload gate; delegates to _parse_offload."""
        return LlamaManager._parse_offload(load_text)[0]

    @staticmethod
    def _parse_backend(load_text: str, offloaded: int | None) -> str:
        """Name the serving backend from the load banner. The AUTHORITATIVE signal
        is the offloaded count: a banner may report a Vulkan device was FOUND yet
        still fall back to CPU (0 layers), so 0/None offload => 'CPU' regardless of
        what device was probed. A positive offload => the accelerator named in the
        banner (Vulkan/CUDA/…)."""
        if not offloaded:  # None or 0 => nothing reached the GPU
            return "CPU"
        low = (load_text or "").lower()
        for name, key in (("Vulkan", "vulkan"), ("CUDA", "cuda"),
                          ("ROCm", "rocm"), ("HIP", "hip"), ("Metal", "metal"),
                          ("SYCL", "sycl")):
            if key in low:
                return name
        return "GPU"

    def _record_offload(self, port: int, gpu_layers: int,
                        expect_offload: bool) -> None:
        """PI-Z26: capture the serving reality (backend + offloaded/total) from the
        load banner and, when GPU acceleration was requested but not fully
        delivered, WARN loudly + glass-log the mismatch. Runs on every start so a
        silent CPU fallback is always conspicuous — independent of the fatal
        expect_offload gate (which only fires on the discrete tier)."""
        off, tot = self._parse_offload(self._startup_stderr)
        self._offload_requested = gpu_layers
        self._offloaded_layers = off
        self._total_layers = tot
        self._serving_backend = self._parse_backend(self._startup_stderr, off)
        fully = self._fully_offloaded(gpu_layers, off, tot)
        if gpu_layers > 0 and not fully:
            log.warning(
                "GPU OFFLOAD MISMATCH (port %d): requested all layers "
                "(--n-gpu-layers %d) but llama-server offloaded %s/%s — serving "
                "backend is %s, NOT GPU-accelerated. Likely the daemon cannot see "
                "the DRM nodes (PI-Z28 sandbox) or VRAM is short. The model will "
                "run on CPU (unusably slow for the big tiers).",
                port, gpu_layers, off, tot, self._serving_backend)
        detail = self.describe_offload(requested_layers=gpu_layers, offloaded=off,
                                       total=tot,
                                       backend=self._serving_backend)
        detail.update({"port": port, "expect_offload": expect_offload})
        glass.emit("engine", "offload_check", detail=detail)

    @staticmethod
    def _fully_offloaded(gpu_layers: int, offloaded: int | None,
                         total: int | None) -> bool:
        """True only when EVERY layer actually reached the graphics card.

        Corrected 2026-08-24. This used to answer True whenever zero layers were
        requested, on the reasoning that processor-only serving had got what it
        asked for. The field it feeds is read by a person diagnosing a slow
        machine, and it told them the card was in use while nothing was on it —
        the observability half of the offload defect. A deliberate
        processor-only configuration now has its OWN field
        (``cpu_only_by_request``) and no longer borrows this one.
        """
        return (offloaded is not None and total is not None
                and total > 0 and offloaded > 0 and offloaded >= total)

    @staticmethod
    def describe_offload(*, requested_layers: int, offloaded: int | None,
                         total: int | None, backend: str | None) -> dict:
        """The offload half of the health surface, as one record.

        One function builds it so the live report, the start-time record and any
        gate all read the same fields decided the same way.
        """
        requested = int(requested_layers or 0)
        return {
            "backend": backend,
            "requested_layers": requested_layers,
            "offloaded_layers": offloaded,
            "total_layers": total,
            "fully_offloaded": LlamaManager._fully_offloaded(
                requested, offloaded, total),
            "cpu_only_by_request": requested <= 0,
        }

    def offload_report(self) -> dict:
        """PI-Z26: the serving backend + requested/actual GPU offload, for Status.
        Makes a silent CPU fallback queryable (D-Bus Status) as well as logged."""
        return self.describe_offload(
            requested_layers=self._offload_requested,
            offloaded=self._offloaded_layers,
            total=self._total_layers,
            backend=self._serving_backend)

    @staticmethod
    def _offload_satisfied(offloaded_layers: int | None) -> bool:
        """Whether real GPU offload was confirmed.

        None (no offload line / unreadable) => NOT satisfied: an unverifiable
        claim is treated as unmet, the same fail-loud posture as the served-
        capability guard. 0 layers => pure CPU fallback => not satisfied. Any
        positive offload => GPU acceleration engaged.
        """
        return offloaded_layers is not None and offloaded_layers > 0

    def _read_startup_stderr(self) -> str:
        """Best-effort read (zero-timeout; never blocks the running server) of
        the child's buffered startup stderr.

        llama.cpp writes the model-load banner (including the offload summary) to
        stderr before the server answers /health, so by the time we get here the
        banner is sitting in the pipe buffer. Read what is available with a
        zero-timeout select so the running server is never stalled; never raise —
        an unreadable pipe returns "" so the offload check fails safe to the
        floor rather than crashing the launch. The live read is validated on the
        RTX 3070 at lane item 5.
        """
        proc = self._process
        if proc is None or proc.stderr is None:
            return ""
        try:
            fd = proc.stderr.fileno()
            chunks: list[bytes] = []
            # Drain whatever is buffered; stop as soon as no data is ready.
            while True:
                ready, _, _ = select.select([fd], [], [], 0)
                if not ready:
                    break
                data = os.read(fd, 65536)
                if not data:
                    break
                chunks.append(data)
            return b"".join(chunks).decode(errors="replace")
        except (OSError, ValueError, TypeError):
            # TypeError: a stderr object whose fileno() yields a non-integer
            # (seen with test doubles; conceivable with exotic file-likes).
            # The documented contract is NEVER raise — fail safe to "".
            return ""

    def _start_stderr_pump(self) -> None:
        """Start the runtime-stderr -> journal pump for the running child.

        Called once, on the healthy-start path, AFTER the startup banner is
        drained. From here the pump owns the child's stderr: it streams every
        subsequent line to the daemon logger (so runtime failures reach the
        journal, not just a PrivateTmp /tmp capture) and keeps the pipe drained
        so a spewing child cannot block on a full stderr buffer. The thread is a
        daemon and ends on EOF when the child exits; stop() joins it.
        """
        proc = self._process
        if proc is None or proc.stderr is None:
            return
        t = threading.Thread(target=self._pump_stderr, args=(proc,),
                              name="llama-stderr-pump", daemon=True)
        self._stderr_thread = t
        t.start()

    def _pump_stderr(self, proc: subprocess.Popen) -> None:
        """Read the child's stderr to EOF, echoing each line at INFO.

        Uses os.read on the raw fd (consistent with the startup drain, which
        already consumed the buffered banner via os.read — mixing that with a
        buffered readline would strand bytes). Splits on newlines so each llama
        line is one journal record. NEVER raises: a closed pipe on shutdown, or a
        test double whose fileno() is not a real fd, just ends the pump — the
        child lifecycle must not depend on the log pump surviving.
        """
        try:
            fd = proc.stderr.fileno()
        except (OSError, ValueError, TypeError, AttributeError):
            return
        buf = b""
        while True:
            try:
                data = os.read(fd, 65536)
            except (OSError, ValueError):
                break                      # pipe closed (shutdown) — normal exit
            if not data:
                break                      # EOF: the child exited
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode(errors="replace").rstrip()
                if text:
                    log.info("llama-server[%s] %s", proc.pid, text)
        tail = buf.decode(errors="replace").rstrip()
        if tail:                            # a final unterminated line at exit
            log.info("llama-server[%s] %s", proc.pid, tail)

    def _verify_gpu_offload(self) -> bool:
        """Assert the model actually offloaded to the GPU (discrete tier only).

        Reads the llama.cpp load banner and confirms a positive offloaded-layer
        count. On failure records OFFLOAD_FAILED + a loud error so the daemon can
        fall to the 2B floor instead of serving an unusably-slow CPU-bound big
        model. Fails safe: an unreadable/absent offload line counts as NOT
        offloaded.
        """
        offloaded = self._parse_offloaded_layers(self._startup_stderr)
        if not self._offload_satisfied(offloaded):
            self._last_error = (
                f"GPU offload not confirmed (offloaded={offloaded}) — the "
                f"discrete tier expected GPU acceleration but the model did not "
                f"offload to the GPU; falling to the 2B floor rather than serve "
                f"an unusably-slow CPU-bound model"
            )
            self._last_failure = StartFailure.OFFLOAD_FAILED
            log.error(self._last_error)
            return False
        log.info("GPU offload confirmed: %d layers offloaded", offloaded)
        return True

    def _wait_for_healthy(self, port: int) -> bool:
        """Poll the health endpoint until the server is ready.

        The deadline is the budget start() derived from the model being loaded,
        not a fixed constant — see startup_budget_seconds. It falls back to the
        floor for any caller that reaches here without a launch of its own.
        """
        deadline = time.time() + (self._startup_budget or STARTUP_TIMEOUT)

        while time.time() < deadline:
            # Check if process died
            if self._process and self._process.poll() is not None:
                stderr = ""
                if self._process.stderr:
                    stderr = self._process.stderr.read().decode(errors="replace")
                self._last_error = (
                    f"Server exited with code {self._process.returncode}: "
                    f"{_failure_tail(stderr)}"
                )
                log.error(self._last_error)
                return False

            try:
                req = urllib.request.Request(f"http://localhost:{port}/health")
                with urllib.request.urlopen(req, timeout=HEALTH_CHECK_TIMEOUT) as resp:
                    data = json.loads(resp.read())
                    if data.get("status") == "ok":
                        # Bind-ownership: a healthy /health answer proves SOMETHING
                        # on the port is up, NOT that it is OUR child. A foreign
                        # holder (the GDM greeter's daemon) answers ok in the
                        # window before our child's bind-failure-exit is observed —
                        # that race is the false-positive. Confirm our own child
                        # owns the listening socket before declaring ready. If not,
                        # keep polling: our child exits on its bind failure and the
                        # process-death check at the top of the loop returns False.
                        pid = self._process.pid if self._process else None
                        if pid is not None and self._pid_owns_port(pid, port):
                            return True
                        log.debug("port %d answers /health but is not owned by "
                                  "our child (pid %s) — not ready", port, pid)
            except Exception:
                pass  # Server not ready yet

            time.sleep(STARTUP_POLL_INTERVAL)

        return False
