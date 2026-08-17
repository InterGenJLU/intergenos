# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""PI-Z27 — the watchdog own-orphan reap + the parent-death orphan-prevention.

Two facets, both on REAL process/port fixtures (no mocked /proc):

  (i)  RECOVERY — when our own llama-server crash-loops or a prior daemon
       incarnation leaves an orphan holding the port, start() must VERIFY the
       holder is ours (kernel exe + our model + our port — never assumed from
       the port) and kill-reap it, then relaunch. A crashed/wedged own child
       previously stranded the watchdog ("3 strikes -> giving up").
  (ii) PREVENTION — the spawned child gets PR_SET_PDEATHSIG=SIGKILL so it dies
       with the daemon and can never orphan (the uid-60584 DynamicUser greeter
       orphan observed on the Zephyrus, 2026-07-07).

FAIL-SAFE (both directions): a genuinely FOREIGN port-holder is NEVER killed,
and a LIVE peer daemon's server (the GDM-greeter cold-boot collision) is NEVER
killed — only refused and waited out.

RED/GREEN: with the llama_manager fix stashed, the reap tests fail (start()
refuses PORT_IN_USE and the holder is never recovered) and the preexec test
fails (Popen carries no preexec_fn); with it applied they pass.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import unittest
from unittest import mock

from intergen.interfaces.types import StartFailure
from intergen.llama_manager import LlamaManager, _die_with_parent


# A child that binds the --port it is given (SO_REUSEADDR) and then sleeps — a
# real LISTEN socket on a real pid, the fixture the reap logic inspects.
_BIND_AND_SLEEP = (
    "import socket,sys,time\n"
    "p=int(sys.argv[sys.argv.index('--port')+1])\n"
    "s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
    "s.bind(('127.0.0.1',p));s.listen(8)\n"
    "sys.stderr.write('bound\\n');sys.stderr.flush()\n"
    "time.sleep(300)\n"
)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class _RealHolderMixin:
    """Spawns/cleans up real port-holding subprocesses with controllable argv."""

    def setUp(self) -> None:
        self.mgr = LlamaManager()
        self.model = "/models/intergen-9b.gguf"
        self.port = _free_port()
        self._procs: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        for p in self._procs:
            try:
                os.kill(p.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                p.wait(timeout=2)
            except Exception:
                pass

    def _spawn(self, *, argv0: str, model: str | None = None,
               port: int | None = None) -> subprocess.Popen:
        """A real subprocess (direct child of this test → PPID == our pid) that
        binds `port` and presents `argv0` as argv[0] and our model/port flags."""
        model = self.model if model is None else model
        port = self.port if port is None else port
        p = subprocess.Popen(
            [argv0, "-c", _BIND_AND_SLEEP, "--model", model, "--port", str(port)],
            executable=sys.executable,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._procs.append(p)
        self._wait_listen(port)
        return p

    def _wait_listen(self, port: int, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.mgr._port_has_listener(port):
                return
            time.sleep(0.05)
        self.fail(f"fixture never began listening on port {port}")


class IdentityTests(_RealHolderMixin, unittest.TestCase):
    """VERIFY ownership — never assume from the port."""

    def test_pids_listening_finds_our_holder(self):
        p = self._spawn(argv0="/opt/llama.cpp/llama-server")
        self.assertIn(p.pid, self.mgr._pids_listening_on_port(self.port))

    def test_is_our_llama_server_true_for_matching_signature(self):
        p = self._spawn(argv0="/opt/llama.cpp/llama-server")
        self.assertTrue(
            self.mgr._is_our_llama_server(p.pid, self.port, self.model))

    def test_is_our_llama_server_false_for_foreign_binary(self):
        # A stranger on the port (nginx) — different exe AND argv0.
        p = self._spawn(argv0="/usr/sbin/nginx")
        self.assertFalse(
            self.mgr._is_our_llama_server(p.pid, self.port, self.model))

    def test_is_our_llama_server_false_for_wrong_model(self):
        # Our binary, but serving a DIFFERENT model — not the instance we own.
        p = self._spawn(argv0="/opt/llama.cpp/llama-server",
                        model="/models/some-other.gguf")
        self.assertFalse(
            self.mgr._is_our_llama_server(p.pid, self.port, self.model))

    def test_is_our_llama_server_false_for_wrong_port(self):
        p = self._spawn(argv0="/opt/llama.cpp/llama-server")
        self.assertFalse(
            self.mgr._is_our_llama_server(p.pid, self.port + 1, self.model))


class ReapTests(_RealHolderMixin, unittest.TestCase):

    def test_reap_recovers_port_from_our_own_stale_server(self):
        """GREEN facet (i): a verified own holder is reaped and the port frees."""
        p = self._spawn(argv0="/opt/llama.cpp/llama-server")
        self.assertTrue(self.mgr._reap_stale_owner(self.port, self.model))
        self.assertFalse(self.mgr._port_has_listener(self.port))
        self.assertFalse(_pid_alive(p.pid), "our stale server must be reaped")

    def test_reap_never_kills_foreign_holder(self):
        """FAIL-SAFE: a genuinely foreign holder is refused, NOT killed."""
        p = self._spawn(argv0="/usr/sbin/nginx")
        self.assertFalse(self.mgr._reap_stale_owner(self.port, self.model))
        self.assertTrue(_pid_alive(p.pid), "a foreign holder must never be killed")
        self.assertTrue(self.mgr._port_has_listener(self.port))

    def test_reap_never_kills_llama_server_of_wrong_model(self):
        """FAIL-SAFE: our binary but a DIFFERENT model instance is not ours."""
        p = self._spawn(argv0="/opt/llama.cpp/llama-server",
                        model="/models/some-other.gguf")
        self.assertFalse(self.mgr._reap_stale_owner(self.port, self.model))
        self.assertTrue(_pid_alive(p.pid))


class LivePeerGuardTests(_RealHolderMixin, unittest.TestCase):
    """A LIVE peer daemon's in-use server (greeter cold-boot collision) is
    refused and waited out — never reaped."""

    def test_reap_refuses_holder_of_a_live_peer_daemon(self):
        # An intermediate process whose cmdline reads as a live intergen daemon
        # (argv0 contains 'python' and 'intergen') spawns the port-holder, then
        # stays alive — so the holder's parent is a LIVE peer, not us and not
        # init. The reap must REFUSE and leave both processes alive.
        # The intermediate's argv0 is a fake name, so its own sys.executable is
        # unreliable — interpolate the real interpreter for the grandchild spawn.
        parent_code = (
            "import subprocess,time\n"
            f"b=subprocess.Popen(['/opt/llama.cpp/llama-server','-c',{_BIND_AND_SLEEP!r},"
            f"'--model',{self.model!r},'--port',{str(self.port)!r}],"
            f"executable={sys.executable!r})\n"
            "print(b.pid,flush=True)\n"
            "time.sleep(300)\n"
        )
        parent = subprocess.Popen(
            ["python3-intergen-daemon", "-c", parent_code],
            executable=sys.executable, stdout=subprocess.PIPE,
        )
        self._procs.append(parent)
        holder_pid = int(parent.stdout.readline().strip())
        self.addCleanup(lambda: os.kill(holder_pid, signal.SIGKILL)
                        if _pid_alive(holder_pid) else None)
        self._wait_listen(self.port)

        # Sanity: the holder IS identified as our llama-server by signature...
        self.assertTrue(
            self.mgr._is_our_llama_server(holder_pid, self.port, self.model))
        # ...but its parent is a LIVE peer daemon, so reap must refuse + not kill.
        self.assertTrue(self.mgr._has_live_peer_daemon_parent(holder_pid))
        self.assertFalse(self.mgr._reap_stale_owner(self.port, self.model))
        self.assertTrue(_pid_alive(holder_pid),
                        "a live peer's server must never be reaped")
        self.assertTrue(_pid_alive(parent.pid))


class StartIntegrationTests(_RealHolderMixin, unittest.TestCase):
    """start()'s port branch: reap our own stale holder then launch; refuse a
    foreign one. The launch tail (Popen/health/caps) is mocked so the test needs
    no real llama-server binary — the port branch runs for real."""

    def _run_start(self, model_file: str) -> tuple[bool, dict]:
        captured: dict = {}

        def _fake_popen(cmd, *a, **k):
            captured["cmd"] = cmd
            captured["kwargs"] = k
            return mock.MagicMock()

        with mock.patch.object(self.mgr, "_find_server",
                               return_value="/usr/bin/llama-server"), \
                mock.patch.object(self.mgr, "is_running", return_value=False), \
                mock.patch.object(self.mgr, "_wait_for_healthy",
                                  return_value=True), \
                mock.patch.object(self.mgr, "_verify_served_capabilities",
                                  return_value=True), \
                mock.patch.object(self.mgr, "_record_offload"), \
                mock.patch.object(self.mgr, "_read_startup_stderr",
                                  return_value=""), \
                mock.patch.object(self.mgr, "_start_stderr_pump"), \
                mock.patch("intergen.llama_manager.subprocess.Popen",
                           side_effect=_fake_popen):
            ok = self.mgr.start(model_file, port=self.port)
        return ok, captured

    def test_start_reaps_stale_owner_then_launches(self):
        """RED/GREEN facet (i): our own stale server holds the port; start()
        reaps it and proceeds to launch (Popen invoked). Pre-fix: PORT_IN_USE."""
        model_file = self._real_model()
        holder = self._spawn(argv0="/opt/llama.cpp/llama-server",
                             model=model_file)
        ok, captured = self._run_start(model_file)
        self.assertTrue(ok)
        self.assertIn("cmd", captured, "launch must proceed after the reap")
        self.assertFalse(_pid_alive(holder.pid))
        self.assertEqual(self.mgr.last_failure, StartFailure.NONE)

    def test_start_refuses_and_preserves_foreign_holder(self):
        """FAIL-SAFE via start(): a foreign holder -> PORT_IN_USE, never killed,
        no launch."""
        model_file = self._real_model()
        holder = self._spawn(argv0="/usr/sbin/nginx", model=model_file)
        ok, captured = self._run_start(model_file)
        self.assertFalse(ok)
        self.assertEqual(self.mgr.last_failure, StartFailure.PORT_IN_USE)
        self.assertNotIn("cmd", captured, "must not launch over a foreign holder")
        self.assertTrue(_pid_alive(holder.pid))

    def test_start_passes_pdeathsig_preexec(self):
        """PREVENTION facet (ii): the spawn carries the parent-death preexec so
        the child can never orphan."""
        model_file = self._real_model()  # free port, nothing to reap
        _, captured = self._run_start(model_file)
        self.assertIs(captured.get("kwargs", {}).get("preexec_fn"),
                      _die_with_parent)

    def _real_model(self) -> str:
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.unlink(f.name))
        return f.name


class ParentDeathPreventionTests(unittest.TestCase):
    """Real kernel test of PR_SET_PDEATHSIG: a child launched with the preexec
    dies when its parent dies — the mechanism that makes an orphan impossible."""

    @unittest.skipUnless(sys.platform.startswith("linux"), "PDEATHSIG is Linux")
    def test_child_dies_when_parent_dies(self):
        # Intermediate parent A spawns grandchild B with _die_with_parent as its
        # preexec; A prints B.pid then sleeps. We SIGKILL A and assert B dies on
        # its own (nobody signals B directly) — proving PDEATHSIG fired.
        gchild = (
            "import socket,sys,time\n"
            "s=socket.socket();s.bind(('127.0.0.1',0));s.listen(1)\n"
            "time.sleep(120)\n"
        )
        parent_code = (
            "import subprocess,sys\n"
            "from intergen.llama_manager import _die_with_parent\n"
            f"b=subprocess.Popen([sys.executable,'-c',{gchild!r}],"
            "preexec_fn=_die_with_parent)\n"
            "print(b.pid,flush=True)\n"
            "import time;time.sleep(120)\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
             env.get("PYTHONPATH", "")])
        a = subprocess.Popen([sys.executable, "-c", parent_code],
                             stdout=subprocess.PIPE, env=env)
        try:
            b_pid = int(a.stdout.readline().strip())
            self.assertTrue(_pid_alive(b_pid))
            os.kill(a.pid, signal.SIGKILL)
            a.wait(timeout=5)
            # B must die on its own within a short window (PDEATHSIG delivery).
            deadline = time.time() + 5
            while time.time() < deadline and _pid_alive(b_pid):
                time.sleep(0.05)
            self.assertFalse(_pid_alive(b_pid),
                             "child must die with its parent (PR_SET_PDEATHSIG)")
        finally:
            try:
                os.kill(a.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                if 'b_pid' in dir():
                    os.kill(b_pid, signal.SIGKILL)
            except (OSError, NameError):
                pass


if __name__ == "__main__":
    unittest.main()
