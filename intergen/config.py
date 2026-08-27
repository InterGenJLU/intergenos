# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen configuration — loads from YAML with user overrides.

Configuration hierarchy:
  1. /etc/intergen/config.yml (system defaults)
  2. ~/.config/intergen/config.yml (user overrides)
  3. Environment variables (INTERGEN_* prefix)

Supports dotted key access: config.get("llm.temperature")
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

from intergen.private_state import (
    PrivateFileHandler,
    PrivateRotatingFileHandler,
    private_dir,
)

logger = logging.getLogger(__name__)

_SYSTEM_CONFIG = Path("/etc/intergen/config.yml")
_USER_CONFIG = Path.home() / ".config" / "intergen" / "config.yml"

_DEFAULTS = {
    "llm": {
        "endpoint": "http://127.0.0.1:8080/v1/chat/completions",
        "temperature": 0.6,
        "top_p": 0.8,
        "top_k": 20,
        "max_tokens": 4096,
        "tool_calling": True,
        "presence_penalty": 1.5,
        # M6 LEG 3b — chat KV-cache size (feeds llama-server --ctx-size). NOT a
        # per-turn latency lever (it caps the KV cache; actual prefill = what a turn
        # uses, ~500 tok system prompt + window + memory + tool output). 16384 gives
        # ample headroom for tool-output-heavy turns without VRAM pressure on the
        # 16 GB dGPU (the 9B Q4 serves ~5.9 GB; a 16k-token KV cache fits with room).
        "context_size": 16384,
        "request_timeout": 120,
    },
    # Sentinel scan policy (design plan §5). always-on by default for the
    # external/MCP + ingress surfaces; "off" requires the human-auth path and is
    # itself in the AI-immutable protected set (decision #5 — the AI can never
    # edit sentinel:/escalation:/providers:; only the human does, hand-edit or
    # authenticated GUI). Enforcement of that immutability converges with the
    # destructive-policy never-list subsystem at the same dispatch chokepoint.
    "sentinel": {
        "scan": {
            "mcp": "always",            # always | enabled | off
            "ingress_tools": "always",  # always | enabled | off
            "depth": "baseline",        # baseline (rules floor) | deep (+local-qwen)
            "deep_scanner": "local-qwen",  # local-qwen | cloud:<provider>
            "qwen_model": "InternVL3.5-2B",  # catalog name of the deep-scan classifier
                                             # (an already-pinned model; small/quantized).
                                             # NOTE: key name retained for compat; the
                                             # Tier-1 model is now InternVL3.5-2B.
        },
        "cloud_scanner": {
            "enabled": False,           # opt-in
            "provider": None,           # NO default provider
        },
    },
    "escalation": {
        "mode": "ask",                  # never | fallback | ask | auto (default ask)
        "primary_provider": None,       # NO default provider
    },
    # Cloud provider configs (phone-a-friend + cloud scanner). Each entry:
    # adapter + model + api_key_keyring_id (key lives in the Keyring, NEVER here)
    # + optional base_url. Empty by default — local-only ships ready.
    "providers": [],
    "models": {
        "path": "/var/lib/intergen/models",
        "embedding_model": "nomic-ai/nomic-embed-text-v1.5",
        "embedding_device": "cpu",
    },
    "llama_server": {
        "port": 8080,
        # "auto" resolves to the detected hardware tier's serving posture:
        # Tier-1 hardware serves over CPU only (its model tier is sized for
        # CPU serving — the GPU is not an inference device there), higher
        # tiers offload every layer with nothing to prove first. An explicit
        # INTEGER is honoured verbatim and is the last word on every tier:
        # 0 pins the CPU, 999 is full offload stated outright, any other
        # number is passed to llama-server as written. Resolved by
        # intergen.llama_manager.resolve_gpu_layers.
        "gpu_layers": "auto",
        # "auto" pins the serving model to the most-capable DISCRETE card's
        # ggml device (multi-GPU boxes serve on one card and leave the other
        # free for eval/judge co-residency; single-GPU boxes get their own
        # card; no discrete card = no pin). An explicit ggml device name
        # (e.g. "Vulkan1" from `llama-server --list-devices`) is an operator
        # pin, SUPREME over the selector — same user-control contract as
        # gpu_layers above.
        "device": "auto",
        "jinja": True,
        # Override the model's embedded chat template with a tool-capable one.
        # InternVL3.5 GGUFs ship a toolless ChatML template; this Qwen-Hermes
        # tool template makes llama-server inject tool schemas + parse tool
        # calls. Empty/None = use the model's embedded template. Installed by
        # the intergen package build (see packages/ai/intergen/build.sh).
        "chat_template_file": "/usr/share/intergen/internvl-tool-template.jinja",
    },
    "logging": {
        "level": "INFO",
        "file": "/var/log/intergen/intergen.log",
        "event_log": "/var/log/intergen/events.jsonl",
        "mcp_audit": "/var/log/intergen/mcp-audit.log",
        "max_file_size_mb": 50,
        "backup_count": 5,
    },
    "security": {
        "mcp_config": "/etc/intergen/mcp.yml",
        "mcp_permissions": "/etc/intergen/mcp.d",
        "schema_pins": "/var/lib/intergen/mcp-pins",
    },
    "data": {
        "path": "/var/lib/intergen/data",
    },
    # Dispatch lockdown (dispatch_policy / the tier resolver). The model never
    # decides a tool on the 2B; this section is the operator-only manual override
    # that sits on top of the resolver.
    "dispatch": {
        # Manual tier override. null = use hardware detection. An int 1/2/3 (or
        # "TIER_N") forces that tier; the resolver STILL fails closed — a forced
        # bigger tier whose native-dispatch logic lane is not shipped in this
        # build falls back to the locked 2B floor (you cannot run code that isn't
        # there). Operator-only.
        "tier_override": None,
    },
}


class Config:
    """Hierarchical configuration with dotted key access."""

    def __init__(self, config_path: str | Path | None = None):
        # deepcopy, NOT dict(): dict(_DEFAULTS) is a SHALLOW copy that shares
        # every nested section dict (llm, security, ...) with the module-level
        # _DEFAULTS and across all Config instances, so a set()/_deep_merge into
        # one instance mutated the shared defaults (including security sections)
        # and leaked into every other instance.
        self._data = copy.deepcopy(_DEFAULTS)
        self._load_yaml(_SYSTEM_CONFIG)
        self._load_yaml(_USER_CONFIG)
        if config_path:
            self._load_yaml(Path(config_path))
        self._load_env_overrides()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value by dotted key path.

        Example: config.get("llm.temperature") -> 0.6
        """
        parts = key.split(".")
        node = self._data
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, key: str, value: Any) -> None:
        """Set a value by dotted key path."""
        parts = key.split(".")
        node = self._data
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    def get_section(self, section: str) -> dict:
        """Get an entire configuration section."""
        return dict(self._data.get(section, {}))

    def _load_yaml(self, path: Path) -> None:
        """Load and merge a YAML config file."""
        if not path.exists():
            return
        try:
            import yaml
            with open(path) as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                self._deep_merge(self._data, data)
                logger.debug("Loaded config from %s", path)
        except ImportError:
            logger.debug("PyYAML not available, skipping %s", path)
        except Exception as e:
            logger.warning("Failed to load config %s: %s", path, e)

    def _load_env_overrides(self) -> None:
        """Load INTERGEN_* environment variables from a fixed allow-list.

        Replaces the original blanket-loop pattern that walked every
        environment variable starting with INTERGEN_ and set the
        config key without validation. The blanket pattern accepted
        ANY config key including dangerous ones (llm.endpoint,
        logging.file, memory.db_path) — a prompt-injection chain
        that gained file-write to ~/.profile or ~/.config/systemd/
        user/intergen.service.d/ could persistently redirect every
        LLM call, log file, or memory database via env var overrides.

        Allow-list scope (each key is type-checked + range-clamped):

          INTERGEN_LOG_LEVEL          -> logging.level         (enum)
          INTERGEN_LLM_TEMPERATURE    -> llm.temperature       (float 0.0-2.0)
          INTERGEN_LLM_TOP_P          -> llm.top_p             (float 0.0-1.0)
          INTERGEN_LLM_TOP_K          -> llm.top_k             (int   1-1000)
          INTERGEN_LLM_MAX_TOKENS     -> llm.max_tokens        (int   1-65535)
          INTERGEN_LLM_PRESENCE_PENALTY -> llm.presence_penalty (float -2.0-2.0)
          INTERGEN_LLM_REQUEST_TIMEOUT -> llm.request_timeout  (int   1-3600)
          INTERGEN_TRACE              -> trace.enabled         (bool)

        All eight are non-weaponizable tunables: enum values bounded
        by the validation set, numeric values clamped to ranges that
        cannot produce a DoS or redirect attack, and INTERGEN_TRACE is
        a pure on/off observability switch (the request-scoped decision
        tracer) that captures only metadata — decision booleans, scores,
        ids, token counts — never raw content. Path-style overrides
        (endpoint, file, db_path) are NOT exposed; the YAML config
        file is the only path-aware configuration layer.

        DELIBERATELY NOT on the allow-list: INTERGEN_TRACE_CONTENT. It
        gates raw prompt / tool-arg / output capture, so blessing it
        through the sanctioned env boundary would sanction a content
        tap. Even with the tracer's root-refusal guard + credential
        redaction, the conservative security-only posture is to
        leave content capture an explicitly-unsanctioned dev-only
        os.environ path. INTERGEN_TRACE (metadata) is sanctioned; the
        content tap is not. (Posture decision, 2026-06-23.)

        Any other INTERGEN_* env var is logged at WARNING and refused.
        The three-tier config doctrine (YAML system / YAML user /
        env runtime) is preserved; only the env-var tier is narrowed.

        INTERGEN_MODEL_PATH is separately consumed by dbus_daemon.py
        through a dedicated security-gated path (ModelManager.verify
        _arbitrary_path) — not through this allow-list.
        """
        allowed = {
            "INTERGEN_LOG_LEVEL": (
                "logging.level",
                self._validate_log_level,
            ),
            "INTERGEN_LLM_TEMPERATURE": (
                "llm.temperature",
                lambda v: self._clamp_float(v, 0.0, 2.0),
            ),
            "INTERGEN_LLM_TOP_P": (
                "llm.top_p",
                lambda v: self._clamp_float(v, 0.0, 1.0),
            ),
            "INTERGEN_LLM_TOP_K": (
                "llm.top_k",
                lambda v: self._clamp_int(v, 1, 1000),
            ),
            "INTERGEN_LLM_MAX_TOKENS": (
                "llm.max_tokens",
                lambda v: self._clamp_int(v, 1, 65535),
            ),
            "INTERGEN_LLM_PRESENCE_PENALTY": (
                "llm.presence_penalty",
                lambda v: self._clamp_float(v, -2.0, 2.0),
            ),
            "INTERGEN_LLM_REQUEST_TIMEOUT": (
                "llm.request_timeout",
                lambda v: self._clamp_int(v, 1, 3600),
            ),
            # Pure on/off observability switch for the request-scoped
            # decision tracer (intergen/trace.py). Metadata-only, safe by
            # construction — see the docstring for why INTERGEN_TRACE is
            # sanctioned but INTERGEN_TRACE_CONTENT deliberately is not.
            "INTERGEN_TRACE": (
                "trace.enabled",
                self._validate_bool,
            ),
        }

        for env_var, raw_value in os.environ.items():
            if not env_var.startswith("INTERGEN_"):
                continue
            # INTERGEN_MODEL_PATH is handled by dbus_daemon.py via its
            # own dedicated security gate; not this allow-list. Skip
            # silently rather than warning, since this is its own
            # documented contract.
            if env_var == "INTERGEN_MODEL_PATH":
                continue
            if env_var not in allowed:
                logger.warning(
                    "Refusing %s: not on the env-var allow-list. "
                    "Only safe-by-construction tunables are accepted "
                    "via env vars; edit the YAML config (/etc/intergen/"
                    "config.yml or ~/.config/intergen/config.yml) for "
                    "other settings.",
                    env_var,
                )
                continue

            config_key, validator = allowed[env_var]
            try:
                validated = validator(raw_value)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Refusing %s=%r: validation failed (%s). Keeping "
                    "the config-file value for %s.",
                    env_var, raw_value, exc, config_key,
                )
                continue

            self.set(config_key, validated)
            logger.debug("Env override: %s = %s", config_key, validated)

    @staticmethod
    def _validate_log_level(value: str) -> str:
        """Accept only canonical logging level names."""
        canonical = value.strip().upper()
        if canonical not in ("DEBUG", "INFO", "WARNING", "WARN",
                             "ERROR", "CRITICAL"):
            raise ValueError(
                f"log level must be one of DEBUG/INFO/WARNING/"
                f"ERROR/CRITICAL, got {value!r}"
            )
        # Normalize the WARN alias to WARNING.
        return "WARNING" if canonical == "WARN" else canonical

    @staticmethod
    def _validate_bool(value: str) -> bool:
        """Accept common truthy/falsey env-var spellings → bool.

        Truthy: 1/true/yes/on (case-insensitive). Anything else → False.
        Mirrors the conservative env-var convention (an unset or junk
        value disables, never accidentally enables).
        """
        return value.strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _clamp_float(value: str, low: float, high: float) -> float:
        f = float(value)
        if f < low or f > high:
            raise ValueError(f"value {f} out of range [{low}, {high}]")
        return f

    @staticmethod
    def _clamp_int(value: str, low: int, high: int) -> int:
        i = int(value)
        if i < low or i > high:
            raise ValueError(f"value {i} out of range [{low}, {high}]")
        return i

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> None:
        """Recursively merge override into base."""
        for key, value in override.items():
            if (key in base and isinstance(base[key], dict)
                    and isinstance(value, dict)):
                Config._deep_merge(base[key], value)
            else:
                base[key] = value

    def to_dict(self) -> dict:
        """Return the full configuration as a dict."""
        return dict(self._data)

    def setup_logging(self) -> None:
        """Configure Python logging based on config settings."""
        level_name = self.get("logging.level", "INFO")
        level = getattr(logging, level_name.upper(), logging.INFO)

        log_file = self.get("logging.file")
        handlers = [logging.StreamHandler()]
        # The file that will actually receive records, as opposed to the one
        # the config asked for. The two differ on every non-root run — see the
        # closing log line for why that mattered.
        effective_path: Path | None = None

        if log_file:
            log_path = Path(log_file)
            # G3-7: intergen runs as a `--user` systemd service, but the default
            # log path is the root-owned /var/log/intergen, which a user process
            # cannot create or write — so it ALWAYS failed and fell back with a
            # scary "Cannot write to …" WARNING on every start. For a non-root
            # process, resolve logs under the user's XDG state dir up front (the
            # correct home for a per-user service's logs) — no doomed attempt, no
            # warning. A root/system deployment keeps the configured /var/log path.
            if os.geteuid() != 0 and str(log_path).startswith(("/var/", "/usr/")):
                state_home = Path(os.environ.get(
                    "XDG_STATE_HOME", Path.home() / ".local" / "state"))
                log_path = state_home / "intergen" / log_path.name
            try:
                private_dir(log_path.parent)
                max_bytes = self.get("logging.max_file_size_mb", 50) * 1024 * 1024
                backup_count = self.get("logging.backup_count", 5)
                # The daemon log records web-search queries, so it is owner-only.
                # The private handler covers the ROLLOVERS too: the stock
                # RotatingFileHandler opens each fresh file through plain open(),
                # which would leave every backup world-readable even if the live
                # file were tightened once at startup.
                file_handler = PrivateRotatingFileHandler(
                    log_path, maxBytes=max_bytes, backupCount=backup_count
                )
                file_handler.setFormatter(logging.Formatter(
                    "%(asctime)s %(name)s %(levelname)s %(message)s"
                ))
                handlers.append(file_handler)
                effective_path = log_path
            except (PermissionError, OSError):
                fallback = (Path.home() / ".local" / "state" / "intergen"
                            / log_path.name)
                private_dir(fallback.parent)
                handlers.append(PrivateFileHandler(fallback))
                effective_path = fallback
                logger.info("Logging to user state dir: %s", fallback)

        logging.basicConfig(
            level=level,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
            handlers=handlers,
            force=True,
        )
        # Name the file records will actually reach. This line used to print
        # the CONFIGURED path, which for any non-root process is not where
        # anything is written: the redirect above sends a user process's log to
        # the XDG state dir because /var/log/intergen is root-owned. Every
        # `intergen` command therefore announced /var/log/intergen/intergen.log
        # while writing to ~/.local/state/intergen/intergen.log, so anyone who
        # followed the named file was reading one that nothing writes — a
        # diagnostic pointing away from its own output.
        logger.info("Logging configured: level=%s, file=%s", level_name,
                    str(effective_path) if effective_path else "stderr only")
