"""InterGen configuration — loads from YAML with user overrides.

Configuration hierarchy:
  1. /etc/intergen/config.yml (system defaults)
  2. ~/.config/intergen/config.yml (user overrides)
  3. Environment variables (INTERGEN_* prefix)

Supports dotted key access: config.get("llm.temperature")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

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
        "context_size": 8192,
    },
    "escalation": {
        "mode": "ask",
    },
    "models": {
        "path": "/var/lib/intergen/models",
        "embedding_model": "nomic-ai/nomic-embed-text-v1.5",
        "embedding_device": "cpu",
    },
    "llama_server": {
        "port": 8080,
        "gpu_layers": 999,
        "jinja": True,
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
}


class Config:
    """Hierarchical configuration with dotted key access."""

    def __init__(self, config_path: str | Path | None = None):
        self._data = dict(_DEFAULTS)
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
        """Load INTERGEN_* environment variables as overrides.

        INTERGEN_LLM_TEMPERATURE=0.5 -> llm.temperature = 0.5
        """
        prefix = "INTERGEN_"
        for key, value in os.environ.items():
            if not key.startswith(prefix):
                continue
            config_key = key[len(prefix):].lower().replace("_", ".", 1)
            # Try numeric conversion
            try:
                if "." in value:
                    value = float(value)
                else:
                    value = int(value)
            except ValueError:
                if value.lower() in ("true", "false"):
                    value = value.lower() == "true"
            self.set(config_key, value)
            logger.debug("Env override: %s = %s", config_key, value)

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

        if log_file:
            log_path = Path(log_file)
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                from logging.handlers import RotatingFileHandler
                max_bytes = self.get("logging.max_file_size_mb", 50) * 1024 * 1024
                backup_count = self.get("logging.backup_count", 5)
                file_handler = RotatingFileHandler(
                    log_path, maxBytes=max_bytes, backupCount=backup_count
                )
                file_handler.setFormatter(logging.Formatter(
                    "%(asctime)s %(name)s %(levelname)s %(message)s"
                ))
                handlers.append(file_handler)
            except PermissionError:
                fallback = Path.home() / ".local" / "share" / "intergen" / "intergen.log"
                fallback.parent.mkdir(parents=True, exist_ok=True)
                handlers.append(logging.FileHandler(fallback))
                logger.warning("Cannot write to %s, using %s", log_path, fallback)

        logging.basicConfig(
            level=level,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
            handlers=handlers,
            force=True,
        )
        logger.info("Logging configured: level=%s, file=%s", level_name, log_file)
