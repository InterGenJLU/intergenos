# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen CLI — interact with the AI assistant from the terminal.

Usage:
  intergen ask "What packages are installed?"
  intergen status
  intergen tier
  intergen tools
  intergen tool-log [--clear|--json|--count] [--limit N]

This connects via D-Bus to the running InterGen daemon. If the daemon
isn't running, it starts a direct session (useful for development).

`intergen tool-log` reads the per-user D-008 RFC §9 dispatch audit log
at $XDG_STATE_HOME/intergen/tool-dispatch.jsonl. The log is the user's
own record of what InterGen's dispatcher decided + what the user
approved or denied via the review modal. `--clear` is the user-data
wipe path per the Q5 provisional default (30-day logrotate retention
canonical; --clear is the explicit user-initiated wipe).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from intergen.private_state import (
    cache_dir_path,
    private_dir,
    private_write_text,
)

# Perceived-latency: the synchronous CLI `ask` blocks on the daemon's .Ask
# (10-20s on the 2B). Show a client-side hop-1 acknowledgment immediately and a
# hop-2 "still working" nudge if it runs long — to STDERR, and only when
# interactive, so piped/scripted output stays clean. (The .Ask method is
# one-shot with no progress channel, so the filler is client-side here.)
_CLI_SLOW_THRESHOLD_S = 5.0


class _AskFillers:
    """Print hop-1 immediately + a one-shot hop-2 nudge if .Ask runs long.
    A no-op unless stderr is a TTY and the filler asset is available."""

    def __init__(self) -> None:
        self._enabled = sys.stderr.isatty()
        self._picker = None
        self._stop = threading.Event()
        self._timer: threading.Thread | None = None
        if self._enabled:
            try:
                from intergen.voice import FillerPicker
                p = FillerPicker()
                self._picker = p if p.available else None
            except Exception:
                self._picker = None

    def __enter__(self):
        if self._picker:
            print(self._picker.hop1(), file=sys.stderr, flush=True)
            self._timer = threading.Thread(target=self._nudge, daemon=True)
            self._timer.start()
        return self

    def _nudge(self) -> None:
        if self._stop.wait(_CLI_SLOW_THRESHOLD_S):
            return  # completed before the threshold
        if self._picker and not self._stop.is_set():
            print(self._picker.hop2(), file=sys.stderr, flush=True)

    def __exit__(self, *exc) -> None:
        self._stop.set()


def print_usage() -> None:
    print("Usage: intergen <command> [args]")
    print()
    print("Commands:")
    print("  ask <message>    Ask InterGen a question")
    print("  ask-frontier <message>")
    print("                   Ask your configured frontier model (phone-a-friend).")
    print("                   Shows the outbound content for your approval before")
    print("                   anything leaves the machine.")
    print("  last [--raw]     Show the last answer; --raw shows the original")
    print("                   output it was summarised from (verifiable source)")
    print("  reset            Clear the conversation — start fresh")
    print("  status           Show daemon status")
    print("  tier             Show hardware tier info")
    print("  tools            List available tools")
    # These option lists are what the handlers below actually read. They said
    # otherwise until 2026-08-26: all four of tool-log's options were printed
    # under `glass`, which accepts none of them, and glass's own --tail and
    # --turn were not printed at all. Found by generating the capability surface
    # from the code and comparing it with what the tool says about itself. If a
    # handler gains an option, add it here in the same change.
    print("  tool-log         Show the D-008 dispatch audit log")
    print("                     --clear       wipe the log (user-data delete)")
    print("                     --json        emit raw JSONL")
    print("                     --count       print record count and exit")
    print("                     --limit N     show last N records (default 50)")
    print("  glass            Reconstruct turns from the M1 glass trace")
    print("                     --turn ID     the full causal chain for one turn")
    print("                     --tail N      the last N rows (default 40)")
    print("                     --json        emit raw JSONL")
    print("  test             Run self-test (hardware + tools)")
    print("  setup            Download and verify LLM model")
    print("                     --tier=N      force a hardware tier "
          "(default: auto-detect)")
    print("                     --yes, -y     answer yes to every prompt")
    print("                     --show-offer  report what this machine can run "
          "and exit;")
    print("                                   changes nothing and installs "
          "nothing")
    print("  daemon           Start the InterGen daemon")
    print("  console          Start the InterGen terminal console")
    print("  panel            Start the InterGen GTK4 panel window")
    print("  --version        Show the InterGen version and, where a")
    print("                   Qwen-family model is on this machine, the")
    print("                   attribution its license requires")
    print()
    print("InterGen — AI assistant for InterGenOS")


INTERGEN_BUS_NAME = "com.intergenos.InterGen"
INTERGEN_OBJ_PATH = "/com/intergenos/InterGen"

# Ask/Escalate run the local LLM, which can take well over the old 5s on a cold
# prompt-ingest (the daemon's own LLM timeout is 300s). Racing it with a short
# client timeout was the G3-6 bug: the call timed out, the CLI mis-read that as
# "daemon not running", and started a competing direct daemon that just exits as
# a duplicate. Wait at least as long as the daemon would.
ASK_TIMEOUT_MS = 320000  # 320s — daemon LLM timeout (300s) + margin


def daemon_has_owner() -> bool:
    """Definitively report whether the InterGen daemon owns its bus name.

    This is answered by the dbus-daemon itself (org.freedesktop.DBus), so it
    returns INSTANTLY even while the InterGen daemon's single-threaded GLib main
    loop is busy doing inference. Probing an InterGen method (the old Status
    disambiguation) does NOT have this property — it blocks behind the in-flight
    Ask and times out, which falsely looks like the daemon is gone (G3-6)."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        result = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (INTERGEN_BUS_NAME,)),
            GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE,
            2000,
        )
        return bool(result.unpack()[0])
    except Exception:
        return False


def try_dbus(method: str, *args: str, timeout_ms: int = 5000) -> str | None:
    """Try to call a method on the InterGen D-Bus service.

    timeout_ms defaults to 5s for the cheap informational methods; callers that
    drive the LLM (Ask/Escalate) pass ASK_TIMEOUT_MS so a slow-but-healthy
    daemon is not mistaken for a dead one."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        result = bus.call_sync(
            INTERGEN_BUS_NAME,
            INTERGEN_OBJ_PATH,
            INTERGEN_BUS_NAME,
            method,
            GLib.Variant("(s)", args) if args else None,
            GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE,
            timeout_ms,
        )
        return result.unpack()[0]
    except Exception:
        return None


def _last_answer_path() -> Path:
    """Cache file holding the last CLI answer + its raw original.

    XDG_CACHE_HOME (transient, regenerable) — a convenience cache for
    `intergen last`, not durable state, so it lives under cache/, not state/.

    The directory comes from private_state so that this path, the trees the
    one-time permission pass walks and the trees the fresh-home permission gate
    judges cannot drift apart: they are the same resolution, called once."""
    return cache_dir_path() / "last-answer.json"


def _hint(text: str) -> None:
    """Dim one-line affordance to STDERR (keeps piped STDOUT clean), TTY only."""
    if sys.stderr.isatty():
        print(f"\033[2m  {text}\033[0m", file=sys.stderr, flush=True)


def _deliver_answer(data: dict) -> None:
    """Print the answer, cache it for `intergen last`, and — when a distinct raw
    original exists behind the summary — point the user at it.

    The always-verifiable-original affordance: the normalised prose is one step
    from the ground truth it was derived from. The hint is shown ONLY after the
    raw has durably landed on disk, so the promise is never a lie (fail-closed)."""
    response = data.get("response", "")
    print(response)
    full = data.get("full_output") or ""
    # Persist for `intergen last [--raw]`. Best-effort + atomic: a cache-write
    # failure must never break the answer, and we advertise the raw only if it
    # actually landed.
    cached_raw = False
    try:
        path = _last_answer_path()
        # Owner-only, and the mode is set at creation: this file holds the
        # answer AND the raw model output behind it, which is the same class of
        # material as a session transcript. The temporary file is the one that
        # is created, so it is the one that has to be private; the rename keeps
        # the inode and therefore the mode.
        private_dir(path.parent)
        tmp = path.with_suffix(".json.tmp")
        private_write_text(
            tmp, json.dumps({"response": response, "full_output": full}))
        tmp.replace(path)
        cached_raw = bool(full)
    except Exception:
        cached_raw = False
    # Hint only when a raw original exists that adds something beyond the prose
    # the user just read, and it is retrievable.
    if cached_raw and full.strip() and full.strip() != response.strip():
        _hint("original output available — run: intergen last --raw")


def cmd_ask(message: str) -> None:
    """Ask InterGen a question.

    Liveness is decided by NameHasOwner (instant, daemon-busy-proof), NOT by a
    method probe: while the daemon is doing inference its single-threaded main
    loop cannot answer a second call, so the old "Ask failed → Status probe →
    direct fallback" path mis-read a healthy-but-busy daemon as gone and started
    a competing direct session (G3-6). When the daemon owns the name we ALWAYS
    talk to it (with a generous timeout) and never spawn a second instance.
    """
    if daemon_has_owner():
        with _AskFillers():
            response = try_dbus("Ask", message, timeout_ms=ASK_TIMEOUT_MS)
        if response is not None:
            data = json.loads(response)
            _deliver_answer(data)
            return
        # The daemon owns the name but the call did not complete even within the
        # LLM timeout — surface the symptom; do NOT start a competing daemon.
        print("InterGen is running but the request did not complete in time "
              "(it may still be loading the model — try again in a moment).",
              file=sys.stderr)
        print("Check the daemon logs for details:", file=sys.stderr)
        print("  journalctl --user -u intergen -n 50", file=sys.stderr)
        sys.exit(2)

    # No daemon owns the bus name → genuinely down. Safe to start direct mode.
    print("InterGen daemon not running. Starting direct session...")
    from intergen.dbus_daemon import InterGenDaemon
    daemon = InterGenDaemon()
    daemon.start_service()
    response = daemon.ask(message)
    data = json.loads(response)
    _deliver_answer(data)


def cmd_last(args: list[str]) -> None:
    """Show the last CLI answer, or its raw original with --raw.

    The verifiable-original companion to `ask`: the normalised prose the user
    saw is one step from the ground truth it was derived from."""
    path = _last_answer_path()
    try:
        data = json.loads(path.read_text())
    except Exception:
        print('No recent InterGen answer is cached yet — ask something first: '
              'intergen ask "..."', file=sys.stderr)
        sys.exit(1)
    if "--raw" in args:
        full = (data.get("full_output") or "").rstrip("\n")
        if full.strip():
            print(full)
            return
        # No richer raw than the answer itself — the prose IS the ground truth.
        _hint("the last answer had no separate raw output — it was a direct "
              "answer, not summarised tool output.")
        print(data.get("response", ""))
    else:
        print(data.get("response", ""))


def cmd_reset() -> None:
    """Clear the daemon's conversation memory — start a fresh conversation.

    Surfaces the ResetConversation D-Bus verb to the terminal: drops the
    router's rolling history and any standing grounding offer so the next turn
    starts clean. Named `reset` to mirror the method 1:1 — no hidden behaviour."""
    if not daemon_has_owner():
        # Direct sessions are per-invocation and hold no cross-turn state, so
        # there is nothing persistent to clear.
        print("InterGen daemon is not running — there is no ongoing "
              "conversation to reset.")
        return
    response = try_dbus("ResetConversation")
    if response is None:
        print("Reset request did not complete (the daemon may be busy) — try "
              "again in a moment.", file=sys.stderr)
        sys.exit(2)
    try:
        ok = bool(json.loads(response).get("reset", False))
    except Exception:
        ok = False
    if ok:
        print("Conversation reset — InterGen has cleared this session's memory.")
    else:
        print("InterGen could not reset the conversation.", file=sys.stderr)
        sys.exit(2)


def cmd_ask_frontier(message: str) -> None:
    """Phone-a-friend: ask the configured frontier model (CLI parity for the GUI
    'Ask my frontier model' button — decision #4's user-invoked affordance).

    This is the genuine initial human-authorized hop: the daemon's Escalate method
    shows a show-before-send consent modal (the user sees the exact outbound content
    + provider and must approve) before anything leaves the machine, then escalates
    with user_consented=True. Same D-Bus-first / direct-fallback structure as `ask`.
    """
    print("Requesting your frontier model — approve the send in the consent dialog…")
    if daemon_has_owner():
        response = try_dbus("Escalate", message, timeout_ms=ASK_TIMEOUT_MS)
        if response is not None:
            data = json.loads(response)
            print(data.get("response", response))
            return
        print("InterGen is running but the Escalate call did not complete in "
              "time.", file=sys.stderr)
        print("Check the daemon logs for details:", file=sys.stderr)
        print("  journalctl --user -u intergen -n 50", file=sys.stderr)
        sys.exit(2)

    print("InterGen daemon not running. Starting direct session...")
    from intergen.dbus_daemon import InterGenDaemon
    daemon = InterGenDaemon()
    daemon.start_service()
    response = daemon.escalate(message)
    data = json.loads(response)
    print(data.get("response", response))


def cmd_status() -> None:
    """Show daemon status.

    Whether a daemon was already on the bus is established BEFORE the Status
    call, because the Status call can create the very state it reports: the
    service is D-Bus activatable, so on a machine with nothing running the
    call itself starts the assistant — a model server, the hardware probe, the
    lot. That is a legitimate mechanism and it is left alone. What is not
    legitimate is doing it in silence: the user asked what the state was, and
    got back the state their question created, with nothing saying so. The
    NameHasOwner probe is answered by the bus daemon itself and returns
    instantly, so this costs one cheap call and buys the disclosure below.
    """
    ran_before_call = daemon_has_owner()
    response = try_dbus("Status")
    if response:
        status = json.loads(response)
        if not ran_before_call:
            status["activated_by_this_call"] = True
    elif daemon_has_owner():
        # Daemon owns the name but Status didn't answer in time (busy doing
        # inference). Don't start a competing direct daemon — report what we
        # can know for certain.
        print("InterGen Status")
        print("=" * 40)
        if not ran_before_call:
            # Same disclosure as the answered path below, for the case where
            # the activation succeeded but the freshly started daemon was too
            # busy starting up to answer within the timeout.
            print("  Started:    the assistant was NOT running when this "
                  "status call began — the call started it (D-Bus activation)")
        print("  Running:    True (daemon is busy — status call timed out)")
        print("  Tip:        retry in a moment, or: "
              "journalctl --user -u intergen -n 50")
        return
    else:
        # No daemon on the bus. This used to start a whole daemon just to ask
        # it how it was doing — which meant a read-only status command detected
        # hardware, sha256-hashed the entire model file, and started a model
        # server, on a machine whose whole complaint was that no daemon was
        # running. The hash alone reads every byte of a file that is tens of
        # gigabytes on a large-model machine.
        #
        # Status now REPORTS the down state instead of trying to leave it.
        # Nothing about load-time verification changes: the model is still
        # hashed against its pin before it is ever loaded, in the daemon's own
        # startup and in the env-override path. What is removed is a hash on a
        # path that loads nothing.
        status = offline_status()

    print_status(status)


def offline_status() -> dict:
    """A status payload for a machine with no daemon running.

    Cheap and read-only by construction: it reports what can be established by
    looking, and says plainly what it did not check. It must not hash the model
    (the file is read in full and nothing is loaded), start a model server, or
    claim the bus name.
    """
    status: dict = {
        "running": False,
        "version": "0.1.0",
        "daemon_down": True,
        "requests_handled": 0,
        "last_error": None,
    }
    # Hardware tier — reads /proc and the GPU probes, no model involved.
    detected = None
    try:
        from intergen.hardware import HardwareDetector
        tier = HardwareDetector().detect()
        detected = tier
        status["tier"] = {
            "level": tier.tier.value,
            "ram_gb": tier.ram_gb,
            "gpu_vendor": tier.gpu_vendor,
            "gpu_model": tier.gpu_model,
            "recommended_model": tier.recommended_model,
            "recommended_quant": tier.recommended_quant,
            "estimated_model_size_gb": tier.estimated_model_size_gb,
        }
    except Exception as exc:  # noqa: BLE001 — a probe failure must not take status down
        status["tier_error"] = type(exc).__name__

    # The model FILE: whether it is there and how big, from the directory entry.
    # Not its hash — that is the load-time gate's job, and this path loads
    # nothing. Reporting presence-without-integrity is only honest because the
    # renderer says so in as many words.
    # WHICH model file is reported has to be the one this machine would
    # actually serve. It used to be resolved by asking ModelManager for a
    # method it does not have (`get_default_model`), so the lookup always fell
    # through to the floor tier — the smallest model in the catalog. On a
    # machine that serves something else, every line printed about the model
    # was true about a file the daemon would never load: a box serving a large
    # model was told the small one was "NOT on this machine", which is a true
    # sentence and a useless one.
    #
    # The daemon's own selection, in the daemon's own order, is used instead:
    #   1. INTERGEN_MODEL_PATH, when set — the daemon honours that override
    #      first, so a status call that ignored it would describe a different
    #      file than the one that would be loaded. (The daemon pin-verifies it
    #      before loading; this path loads nothing and hashes nothing, so it
    #      reports the file's presence and says the integrity was not checked,
    #      exactly as for every other selection route.)
    #   2. model_manager.resolve_for_detected(tier) — the ONE shared
    #      resolution path used by both `intergen setup` and the daemon, so
    #      what status names is what a start would load.
    #   3. The floor tier, only when the two above resolve nothing, and
    #      labelled as the fallback it is.
    try:
        from intergen.model_manager import ModelManager
        from pathlib import Path as _Path
        mm = ModelManager()
        info = None
        selected_by = ""
        env_override = os.environ.get("INTERGEN_MODEL_PATH")
        path = None
        name = None
        if env_override:
            path = _Path(env_override)
            name = path.stem
            selected_by = "the INTERGEN_MODEL_PATH override"
        else:
            if detected is not None:
                info = mm.resolve_for_detected(detected)
                if info is not None:
                    selected_by = "this machine's detected hardware"
            if info is None:
                from intergen.dispatch_policy import FLOOR_TIER
                info = mm.get_model_for_tier(FLOOR_TIER)
                selected_by = ("the floor tier — the model this machine would "
                               "serve could not be resolved")
            if info is not None:
                name = info.name
                path = _Path(info.local_path) if info.local_path else None
                if path is None or not path.exists():
                    path = mm._model_dir / info.filename  # noqa: SLF001
        if path is not None:
            present = path.exists()
            status["model_file"] = {
                "name": name,
                "path": str(path),
                "present": present,
                "size_bytes": path.stat().st_size if present else 0,
                # Stated as a field, not just in prose, so anything READING this
                # payload cannot mistake presence for verification.
                "integrity_checked": False,
                # WHY this file and not another one — the difference between a
                # true statement and a useful one on a machine holding several.
                "selected_by": selected_by,
            }
    except Exception as exc:  # noqa: BLE001 — same reason as above
        status["model_file_error"] = type(exc).__name__
    return status


def print_status(status: dict) -> None:
    """Render a status payload. Pure over the dict — no bus, no daemon.

    Split out of cmd_status so the rendering can be exercised directly against
    a payload. What this surface SAYS is a correctness property in its own
    right — the difference between "session recall active" and "wired, not yet
    verified" is the difference between a user trusting recall and knowing not
    to — and a renderer reachable only through a live D-Bus call could not be
    tested for it.
    """
    print("InterGen Status")
    print("=" * 40)
    # A status call on a machine with nothing running STARTS the assistant, by
    # way of the service's D-Bus activation. The state below is then partly the
    # answer to the question and partly the consequence of asking it, and the
    # user is told which — one line, on the same never-silent principle as the
    # posture banners below. The activation itself is unchanged.
    if status.get("activated_by_this_call"):
        print("  Started:    the assistant was NOT running when this status "
              "call began — the call started it (D-Bus activation)")
    # F1: banner the launch-time test-review autopilot LOUDLY whenever active, so
    # a non-interactive consent posture is never silent. None in production.
    autopilot = status.get("review_autopilot")
    if autopilot:
        print("  *** TEST-REVIEW AUTOPILOT ACTIVE"
              f" ({autopilot}) ***")
        print("  Consent dispatches are auto-answered without a human.")
        print("  Test-only — never a production posture.")
        print("-" * 40)
    # Same never-silent discipline for the eval-consent deny-and-record
    # responder: an unattended-baseline consent posture is bannered, with the
    # denial count so far, so nobody reads a run's refusals as user decisions.
    ec = status.get("eval_consent") or {}
    if ec.get("armed"):
        print("  *** EVAL-CONSENT RESPONDER ARMED (deny-and-record) ***")
        print("  Every consent gate is answered with an immediate DENY,")
        print(f"  and recorded ({ec.get('denials', 0)} so far this session).")
        print("  Unattended-baseline only — never a production posture.")
        print("-" * 40)
    print(f"  Running:    {status.get('running', False)}")
    print(f"  Version:    {status.get('version', 'unknown')}")

    tier = status.get("tier")
    if tier:
        print(f"  Tier:       {tier.get('level', '?')}")
        print(f"  RAM:        {tier.get('ram_gb', '?')} GB")
        print(f"  GPU:        {tier.get('gpu_vendor', 'none')}")

    # "Model:" is the LOADED engine, not the hardware recommendation. PI-Z25:
    # status rendered tier.recommended_model (e.g. a Tier-3 35B) as Model:,
    # contradicting the model actually running (the box pins the 9B via the dev
    # override). Show the loaded model; keep the recommendation as its own line.
    model_loaded = status.get("model")
    if model_loaded:
        print(f"  Model:      {model_loaded}")
    if tier and tier.get("recommended_model"):
        rec = f"{tier.get('recommended_model')} {tier.get('recommended_quant', '')}".strip()
        print(f"  Recommended: {rec}")

    # Offload (PI-Z26): whether the loaded model is actually served on the GPU —
    # the backend + how many layers are offloaded. The daemon records this every
    # start; rendering it here makes "is my GPU being used" a first-party grounded
    # answer, not a claim the user can only check via nvidia-smi. Shown if present.
    offload = status.get("offload")
    if isinstance(offload, dict) and offload:
        backend = offload.get("backend", "?")
        n = offload.get("offloaded_layers")
        total = offload.get("total_layers")
        layers = (f"{n}/{total}" if n is not None and total is not None
                  else str(n) if n is not None else "?")
        if offload.get("fully_offloaded"):
            where = "on GPU"
        elif (n or 0) > 0:
            where = "partial GPU"
        else:
            where = "on CPU"
        print(f"  Offload:    {backend}, {layers} layers {where}")

    # M2b (design D5): the session-memory INDEX's serving reality (distinct from
    # the persistent Fact store shown under Components). enabled = the :8081
    # embedder is wired, so an out-of-window antecedent can be recalled and
    # re-injected; degraded = that embedder went unreachable/malformed at runtime,
    # so recall fell back to the raw history window. Per D5 ("nothing silent at the
    # user surface"), a degraded index is rendered LOUD -- not a quiet flag -- the
    # same class of serving-reality alarm as an Offload CPU fallback.
    mem = status.get("memory_index")
    if isinstance(mem, dict) and mem:
        if mem.get("degraded"):
            print("  *** MEMORY DEGRADED -- session recall is OFFLINE ***")
            print("      the :8081 embedder is unreachable; answers use the raw")
            print("      history window only, so an older antecedent may be lost.")
        elif mem.get("verified"):
            print("  Memory:     session recall active (:8081 index)")
        elif mem.get("enabled"):
            # Wired but never yet observed to work. Saying "active" here would
            # be reporting configuration as if it were behaviour: an embedder
            # that has never come up has not failed either, so nothing has set
            # the degraded flag, and the old wording called that active.
            print("  Memory:     session recall wired, NOT YET VERIFIED")
            print("      the :8081 embedder has not answered yet, so whether")
            print("      recall works on this machine is not known.")
        else:
            print("  Memory:     disabled (no embedder wired)")

    # The daemon-down report. Everything above it that could be established by
    # looking has been; this says what was NOT established, so "the file is
    # there" is never read as "the file is the one it should be".
    if status.get("daemon_down"):
        print()
        print("  The InterGen daemon is not running, so this is what can be")
        print("  established without starting one:")
        mf = status.get("model_file") or {}
        if mf:
            if mf.get("present"):
                # Decimal GB, matching the tier table's displayed sizes — a
                # binary GiB labelled "GB" here showed the same file 0.4 GB
                # smaller than the tier table.
                size_gb = mf.get("size_bytes", 0) / 1e9
                print(f"  Model file: {mf.get('name', '?')} "
                      f"({size_gb:.1f} GB on disk)")
            else:
                print(f"  Model file: {mf.get('name', '?')} is NOT on this "
                      f"machine")
                print(f"      expected at {mf.get('path', '?')}")
            # WHICH model this is a statement about. On a machine holding more
            # than one, naming the file without naming why it was picked is
            # how a true line ends up describing the wrong model.
            if mf.get("selected_by"):
                print(f"      selected by {mf['selected_by']}")
            print("  Integrity:  NOT checked here. The model is verified "
                  "against its")
            print("      pin when it is loaded; a status call loads nothing, so "
                  "it")
            print("      does not read the file to hash it.")
        elif status.get("model_file_error"):
            print(f"  Model file: could not be read "
                  f"({status['model_file_error']})")
        print("  Start it:   systemctl --user start intergen")
        print("  Logs:       journalctl --user -u intergen -n 50")

    print(f"  Requests:   {status.get('requests_handled', 0)}")
    print(f"  Last Error: {status.get('last_error', 'none')}")

    components = status.get("components", {})
    if components:
        print()
        print("Components:")
        for name, ready in components.items():
            marker = "+" if ready else "-"
            print(f"  [{marker}] {name}")


def cmd_tier() -> None:
    """Show hardware tier info."""
    response = try_dbus("GetTier")
    if response:
        tier = json.loads(response)
    else:
        from intergen.hardware import HardwareDetector
        detector = HardwareDetector()
        t = detector.detect()
        tier = {
            "level": t.tier.value,
            "ram_gb": t.ram_gb,
            "gpu_vendor": t.gpu_vendor,
            "gpu_model": t.gpu_model,
            "recommended_model": t.recommended_model,
            "recommended_quant": t.recommended_quant,
            "estimated_model_size_gb": t.estimated_model_size_gb,
        }

    print("Hardware Tier")
    print("=" * 40)
    print(f"  Level:      Tier {tier.get('level', '?')}")
    print(f"  RAM:        {tier.get('ram_gb', '?')} GB")
    print(f"  GPU:        {tier.get('gpu_vendor', 'none')} ({tier.get('gpu_model', '')})")
    # PI-Z25: this is the hardware tier's RECOMMENDATION, not the running engine
    # — label it as such (cmd_status carries the loaded-model line).
    print(f"  Recommended: {tier.get('recommended_model', '?')} {tier.get('recommended_quant', '')}")
    print(f"  Model Size: ~{tier.get('estimated_model_size_gb', '?')} GB")


def cmd_tools() -> None:
    """List available tools."""
    from intergen.tools.run_command import RunCommandTool
    from intergen.tools.read_file import ReadFileTool
    from intergen.tools.write_file import WriteFileTool
    from intergen.tools.manage_packages import ManagePackagesTool
    from intergen.tools.manage_services import ManageServicesTool
    from intergen.tools.web_search import WebSearchTool
    from intergen.tools.open_application import OpenApplicationTool

    tools = [
        RunCommandTool(), ReadFileTool(), WriteFileTool(),
        ManagePackagesTool(), ManageServicesTool(),
        WebSearchTool(), OpenApplicationTool(),
    ]

    print("InterGen Tools")
    print("=" * 40)
    for tool in tools:
        safety = tool.schema.safety_tier.value
        print(f"  {tool.name:25s} [{safety:7s}]  {tool.description[:50]}")


def cmd_tool_log(args: list[str]) -> None:
    """Show or wipe the D-008 RFC §9 dispatch audit log.

    Flags:
      --clear      truncate the log (user-data-wipe path per Q5 default).
      --json       emit raw JSONL lines instead of human-readable rendering
                   (suitable for piping into jq).
      --count      print the record count and exit.
      --limit N    show only the last N records (default 50).
    """
    from intergen.audit_log import (
        clear_log, default_log_path, read_records, record_count,
    )

    if "--clear" in args:
        path = default_log_path()
        if not path.exists():
            print(f"Audit log already empty: {path}")
            return
        existing = record_count()
        ok = clear_log()
        if ok:
            print(f"Cleared {existing} record(s) from {path}")
        else:
            print(f"Failed to clear {path} (see logs)", file=sys.stderr)
            sys.exit(1)
        return

    if "--count" in args:
        print(record_count())
        return

    limit = 50
    if "--limit" in args:
        try:
            limit = int(args[args.index("--limit") + 1])
        except (IndexError, ValueError):
            print("--limit requires an integer argument", file=sys.stderr)
            sys.exit(1)
        if limit < 1:
            print("--limit must be >= 1", file=sys.stderr)
            sys.exit(1)

    records = list(read_records())
    if not records:
        print(f"Audit log empty: {default_log_path()}")
        return

    if "--json" in args:
        # Raw JSONL pass-through for jq/grep pipelines.
        for r in records[-limit:]:
            print(json.dumps(r, separators=(",", ":")))
        return

    # Human-readable rendering: one block per record.
    shown = records[-limit:]
    print(f"InterGen dispatch audit log — {len(shown)} of {len(records)} record(s)")
    print(f"  Path: {default_log_path()}")
    print()
    for r in shown:
        kind = r.get("kind", "tool_dispatch")
        if kind == "governance_decision":
            event_type = r.get("event_type", "?")
            ts = r.get("timestamp", "?")
            summary = r.get("result_summary", "")
            details = r.get("details")
            print(f"  [{ts}] [GOV:{event_type}] {summary}")
            if details:
                for key, val in sorted(details.items()):
                    print(f"    {key}: {val}")
            print()
            continue

        ts = r.get("timestamp", "?")
        name = r.get("tool_name", "?")
        outcome = r.get("user_decision", "?")
        declared = r.get("declared_provenance", "?")
        effective = r.get("effective_provenance", "?")
        exit_code = r.get("exit_code", "?")
        source = r.get("source_attribution", "")
        excerpt = r.get("excerpt", "")
        ingress = r.get("ingress_tools_this_turn", []) or []

        prov_marker = ""
        if declared != effective:
            prov_marker = f"  (escalated from {declared} -> {effective} per RFC §5.1)"

        print(f"  [{ts}] {name}  outcome={outcome}  exit={exit_code}")
        print(f"    provenance:   {effective}{prov_marker}")
        if ingress:
            print(f"    ingress tools (this turn): {', '.join(ingress)}")
        if source:
            print(f"    source:       {source}")
        if excerpt:
            snippet = excerpt[:200] + ("..." if len(excerpt) > 200 else "")
            print(f"    excerpt:      {snippet}")
        args_summary = r.get("arguments", {})
        if args_summary:
            print(f"    arguments:    {args_summary}")
        result = r.get("result_summary", "")
        if result:
            snippet = result[:160] + ("..." if len(result) > 160 else "")
            print(f"    result:       {snippet}")
        print()


def cmd_test() -> None:
    """Run self-test."""
    import unittest
    from intergen.tests import test_tools
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(test_tools)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def _glass_summary(r: dict) -> str:
    """A one-line summary of a glass row for the human reader."""
    d = r.get("detail") or {}
    ev = r.get("event")
    if ev == "turn_start":
        return f"user: {str(d.get('user_msg',''))[:70]!r}"
    if ev == "decided":
        return (f"source={d.get('source')} qtype={d.get('query_type')} "
                f"score={d.get('semantic_score')}"
                + (" [streamed]" if d.get("streamed") else ""))
    if ev in ("offer_stage", "offer_consume", "offer_decline", "offer_lapse"):
        return f"cmd={d.get('command')!r} slot={d.get('slot','action')}"
    if ev == "decompose":
        return (f"compound={d.get('is_compound')} "
                f"signals={d.get('matched_signals')} "
                f"needs={d.get('needs_decomposition')}")
    if ev == "history_write":
        return f"buffer len={d.get('len_after')}"
    if ev == "assembled":
        return (f"variant={d.get('system_variant')} "
                f"history_msgs={d.get('history_msg_count')} "
                f"msgs={len(d.get('messages',[]))}")
    if ev in ("first_token", "complete"):
        return (f"ttft/total via dur_ms; tokens={d.get('completion_tok_count')} "
                f"text={str(d.get('text',''))[:50]!r}")
    if ev == "final":
        return f"iface={d.get('iface')} text={str(d.get('text',''))[:60]!r}"
    return json.dumps(d)[:80]


def cmd_glass(args: list[str]) -> None:
    """Reconstruct a turn from the glass trace (M1 Glass Pipeline).

    `intergen glass`                list turns (newest last)
    `intergen glass --turn <id>`    the full ordered causal chain for one turn
    `intergen glass --tail [N]`     the last N rows (default 40)
    `intergen glass --json`         raw JSONL
    """
    from intergen import glass
    rows = list(glass.read_rows())
    path = glass.default_glass_path()
    if not rows:
        print(f"No glass trace yet at {path}")
        if not glass.glass_enabled():
            print("(glass is DISABLED — INTERGEN_GLASS=0)")
        return
    if "--json" in args:
        for r in rows:
            print(json.dumps(r))
        return
    if "--tail" in args:
        i = args.index("--tail")
        n = 40
        if i + 1 < len(args) and args[i + 1].isdigit():
            n = int(args[i + 1])
        for r in rows[-n:]:
            print(f"{r.get('turn_id')} {r['phase']}/{r['event']} "
                  f"{_glass_summary(r)}")
        return
    if "--turn" in args:
        i = args.index("--turn")
        turn = args[i + 1] if i + 1 < len(args) else None
        chain = [r for r in rows if r.get("turn_id") == turn]
        if not chain:
            print(f"No rows for turn {turn}")
            return
        print(f"=== turn {turn} — {len(chain)} rows "
              f"(iface={chain[0].get('iface')}) ===")
        for r in chain:
            t = r.get("t_rel_ms")
            tstr = f"+{t:.0f}ms" if isinstance(t, (int, float)) else "-"
            dur = r.get("dur_ms")
            dstr = f" ({dur:.0f}ms)" if isinstance(dur, (int, float)) else ""
            print(f"[{tstr:>9}]{dstr:>9} {r['phase']}/{r['event']:<14} "
                  f"{_glass_summary(r)}")
        return
    from collections import OrderedDict
    turns: "OrderedDict[str, list]" = OrderedDict()
    for r in rows:
        turns.setdefault(r.get("turn_id"), []).append(r)
    print(f"{len(turns)} turns in {path} (newest last):")
    for tid, rs in turns.items():
        first = next((x.get("detail", {}).get("user_msg")
                      for x in rs if x.get("event") == "turn_start"), "")
        print(f"  {tid}  iface={rs[0].get('iface')}  rows={len(rs)}  "
              f"{str(first)[:60]}")
    print("\nReconstruct one:  intergen glass --turn <id>")


# Tongyi Qianwen License section 4 requires an attribution wherever a
# Qwen-family model powers the assistant, and docs/legal/payload-licenses.md
# states that `intergen --version` renders it. It did not: `--version` was not
# a command at all — it reached the final `else` in main() and printed
# "Unknown command: --version" before exiting 1.
QWEN_ATTRIBUTION = "Powered by Qwen"


def qwen_models_present() -> list[str]:
    """Names of the Qwen-family models whose files are on this machine.

    Cheap and read-only by construction, the same property `intergen status`
    had to be given: it reads the download manifest and the directory entries
    that manifest names. It loads no model, hashes no model file and starts no
    daemon.

    Any failure yields an empty list, and the caller then prints no
    attribution. That direction is deliberate. An attribution is a factual
    claim about what is running, so an unreadable manifest must produce
    silence rather than a guess — and on a Tier-1 box, which serves
    InternVL3.5-2B, "Powered by Qwen" would simply be false.
    """
    try:
        from intergen.model_manager import ModelManager
        names: list[str] = []
        for info in ModelManager().list_downloaded():
            name = (getattr(info, "name", "") or "").strip()
            # The paired projector rides the manifest as "<model> (mmproj)".
            # It is the same model for attribution purposes, so it must not
            # appear as a second name on the line.
            base = name.split(" (mmproj)")[0]
            if base.lower().startswith("qwen") and base not in names:
                names.append(base)
        return names
    except Exception:  # noqa: BLE001 — printing a version must never fail
        return []


def cmd_version() -> None:
    """`intergen --version` — the running package version, plus the model
    attribution when one is owed."""
    # Read through the module so the printed value is the package's own
    # version at call time, not a second copy typed into this file that would
    # drift the first time the release is bumped.
    import intergen
    print(f"InterGen {intergen.__version__}")
    present = qwen_models_present()
    if present:
        print(f"{QWEN_ATTRIBUTION} — {', '.join(present)}, used under the "
              f"Tongyi Qianwen License.")


def main() -> None:
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    command = sys.argv[1].lower()

    # Initialise logging so CLI/setup errors persist to a file (rotating log
    # under /var/log/intergen, or ~/.local/share/intergen/intergen.log when
    # unprivileged) instead of vanishing with the terminal. The CLI previously
    # configured no handler at all, so `intergen setup` failures were unlogged.
    # Best-effort — logging setup must never block a command.
    try:
        from intergen.config import Config
        Config().setup_logging()
    except Exception:  # noqa: BLE001 — diagnostics must not break the CLI
        pass

    if command == "ask":
        if len(sys.argv) < 3:
            print("Usage: intergen ask <message>")
            sys.exit(1)
        cmd_ask(" ".join(sys.argv[2:]))
    elif command == "ask-frontier":
        if len(sys.argv) < 3:
            print("Usage: intergen ask-frontier <message>")
            sys.exit(1)
        cmd_ask_frontier(" ".join(sys.argv[2:]))
    elif command == "last":
        cmd_last(sys.argv[2:])
    elif command == "reset":
        cmd_reset()
    elif command == "status":
        cmd_status()
    elif command == "tier":
        cmd_tier()
    elif command == "tools":
        cmd_tools()
    elif command == "tool-log":
        cmd_tool_log(sys.argv[2:])
    elif command == "glass":
        cmd_glass(sys.argv[2:])
    elif command == "test":
        cmd_test()
    elif command == "setup":
        from intergen.setup import report_offer, run_setup
        # Read-only query of what this box can run (the Welcomer asks for this
        # before offering the model choice). Never installs anything.
        if "--show-offer" in sys.argv:
            sys.exit(report_offer())
        # Forward --tier=N (same form setup.main() accepts) — the `intergen
        # setup` subcommand previously dropped it, so `intergen setup --tier=1`
        # silently fell back to auto-detect.
        tier_override = None
        for arg in sys.argv:
            if arg.startswith("--tier="):
                try:
                    tier_override = int(arg.split("=")[1])
                except ValueError:
                    pass
        # Propagate run_setup's verdict to the EXIT CODE. Without this the
        # `intergen setup` subcommand exited 0 regardless of outcome, so a
        # fail-closed setup (e.g. the model download could not complete because
        # the machine was offline — WiFi not yet connected) was reported to the
        # caller as SUCCESS. The Welcomer keys "InterGen is ready" off this
        # process's rc==0, so it showed a false "ready" over an empty model
        # store — a fail-closed integrity step must never surface as success.
        # setup.py:main() already does this; the subcommand must too.
        if not run_setup(auto_yes="--yes" in sys.argv or "-y" in sys.argv,
                         tier_override=tier_override):
            sys.exit(1)
    elif command == "daemon":
        from intergen.dbus_daemon import main as daemon_main
        # Pass only the arguments AFTER the subcommand: the daemon's argparse
        # must never see the word "daemon" itself (unit ExecStart is
        # `/usr/bin/intergen daemon [--eval-consent-deny]`).
        daemon_main(sys.argv[2:])
    elif command == "console":
        from intergen.console.shell import main as console_main
        console_main()
    elif command == "panel":
        from intergen.panel import main as panel_main
        panel_main()
    elif command == "--version":
        cmd_version()
    elif command in ("help", "--help", "-h"):
        print_usage()
    else:
        print(f"Unknown command: {command}")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
