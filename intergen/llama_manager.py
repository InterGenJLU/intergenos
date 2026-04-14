"""Llama server manager — subprocess lifecycle for llama-server.

Manages the llama-server process (from llama.cpp) that serves the local
LLM via an OpenAI-compatible HTTP API. Handles startup, health checks,
auto-restart on crash, and graceful shutdown.

Default endpoint: http://localhost:8080/v1/chat/completions
Health check:     http://localhost:8080/health
"""

from __future__ import annotations

import json
import logging
import signal
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from intergen.interfaces.hardware import LlamaManagerInterface
from intergen.interfaces.types import ServerHealth

log = logging.getLogger(__name__)

MAX_RESTART_ATTEMPTS = 3
HEALTH_CHECK_TIMEOUT = 5      # seconds per health check request
STARTUP_TIMEOUT = 60           # seconds to wait for server to become healthy
STARTUP_POLL_INTERVAL = 1.0    # seconds between health polls during startup
SHUTDOWN_TIMEOUT = 10          # seconds to wait for graceful shutdown


@dataclass
class ServerConfig:
    """Configuration snapshot for restart."""
    model_path: str
    port: int
    context_size: int
    gpu_layers: int
    jinja: bool


class LlamaManager(LlamaManagerInterface):
    """Manages the llama-server subprocess lifecycle."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._config: ServerConfig | None = None
        self._start_time: float = 0.0
        self._restart_count: int = 0
        self._requests_served: int = 0
        self._last_error: str | None = None

    def start(self, model_path: str, *,
              port: int = 8080,
              context_size: int = 8192,
              gpu_layers: int = 999,
              jinja: bool = True) -> bool:
        """Start llama-server with the given model."""
        # Verify the model file exists
        if not Path(model_path).exists():
            self._last_error = f"Model file not found: {model_path}"
            log.error(self._last_error)
            return False

        # Find llama-server binary
        server_path = self._find_server()
        if server_path is None:
            self._last_error = "llama-server binary not found"
            log.error(self._last_error)
            return False

        # Stop existing server if running
        if self.is_running():
            log.info("Stopping existing llama-server before starting new one")
            self.stop()

        # Build command
        cmd = [
            server_path,
            "--model", model_path,
            "--port", str(port),
            "--ctx-size", str(context_size),
            "--n-gpu-layers", str(gpu_layers),
        ]
        if jinja:
            cmd.append("--jinja")

        # Save config for restart
        self._config = ServerConfig(
            model_path=model_path,
            port=port,
            context_size=context_size,
            gpu_layers=gpu_layers,
            jinja=jinja,
        )

        log.info("Starting llama-server: %s", " ".join(cmd))

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._start_time = time.time()
            self._restart_count = 0

            # Wait for server to become healthy
            if self._wait_for_healthy(port):
                log.info("llama-server started successfully on port %d", port)
                return True

            # Server didn't become healthy — kill it
            self._last_error = "Server failed to become healthy within timeout"
            log.error(self._last_error)
            self.stop()
            return False

        except OSError as e:
            self._last_error = f"Failed to start llama-server: {e}"
            log.error(self._last_error)
            return False

    def stop(self) -> None:
        """Stop the llama-server subprocess gracefully."""
        if self._process is None:
            return

        log.info("Stopping llama-server (PID %d)", self._process.pid)

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
                self._process.wait(timeout=5)
        except OSError as e:
            log.warning("Error stopping llama-server: %s", e)
        finally:
            # Close stdout/stderr pipes to avoid ResourceWarning
            if self._process is not None:
                if self._process.stdout:
                    self._process.stdout.close()
                if self._process.stderr:
                    self._process.stderr.close()
            self._process = None

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

        return self.start(
            self._config.model_path,
            port=self._config.port,
            context_size=self._config.context_size,
            gpu_layers=self._config.gpu_layers,
            jinja=self._config.jinja,
        )

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

    def get_endpoint(self) -> str:
        """Return the server's chat completions endpoint URL."""
        port = self._config.port if self._config else 8080
        return f"http://localhost:{port}/v1/chat/completions"

    def is_running(self) -> bool:
        """Return True if the server process is alive."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def _find_server(self) -> str | None:
        """Find the llama-server binary."""
        import shutil
        # Check standard locations
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

    def _wait_for_healthy(self, port: int) -> bool:
        """Poll the health endpoint until the server is ready."""
        deadline = time.time() + STARTUP_TIMEOUT

        while time.time() < deadline:
            # Check if process died
            if self._process and self._process.poll() is not None:
                stderr = ""
                if self._process.stderr:
                    stderr = self._process.stderr.read().decode(errors="replace")[:500]
                self._last_error = f"Server exited with code {self._process.returncode}: {stderr}"
                log.error(self._last_error)
                return False

            try:
                req = urllib.request.Request(f"http://localhost:{port}/health")
                with urllib.request.urlopen(req, timeout=HEALTH_CHECK_TIMEOUT) as resp:
                    data = json.loads(resp.read())
                    if data.get("status") == "ok":
                        return True
            except Exception:
                pass  # Server not ready yet

            time.sleep(STARTUP_POLL_INTERVAL)

        return False
