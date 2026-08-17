"""Per-user phone-a-friend provider config persistence (the panel's write path).

The escalation:/providers: config sections are USER-OWNED and AI-immutable
(decision #5): only the human edits them — by hand or via the provider-config
panel. This module is that panel's persistence layer. It writes to the USER
override layer (~/.config/intergen/config.yml) — never /etc, so no root — and
the API key itself goes to the system keyring (cloud.http_adapter.store_secret),
NEVER into this file. Only the keyring id (derived from the provider name) is
persisted here.

Security posture: per-user file (0600), key in keyring only, reached via the
web server's Bearer-authenticated HTTP endpoints (NOT a tool), so the AI cannot
modify the provider/escalation config through its tool surface.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

_USER_CONFIG = Path.home() / ".config" / "intergen" / "config.yml"

# The adapters the factory knows (intergen/cloud/factory.py). Surfaced to the
# panel so the UI offers a closed set, and validated on write.
VALID_ADAPTERS = (
    "anthropic", "openai", "google", "microsoft", "deepseek", "xai", "custom",
)

# Per-adapter UI hints: friendly label + a SUGGESTED model (the user may enter
# any model the adapter's API accepts) + where to get a key. Only the Anthropic
# default is pinned — claude-fable-5, Anthropic's publicly-released frontier
# model WITH safety classifiers. We deliberately do NOT suggest claude-mythos-5
# (same capability, no safety classifiers, gated behind Project Glasswing): an
# un-classified frontier model contradicts a security-only OS. Other adapters
# leave the model blank (post-cutoff model ids vary; the user enters theirs).
PROVIDER_CATALOG = {
    "anthropic": {"label": "Anthropic — Claude Fable 5",
                  "default_model": "claude-fable-5",
                  "key_url": "https://platform.claude.com/"},
    "openai":    {"label": "OpenAI — ChatGPT", "default_model": "gpt-5.5-pro",
                  "key_url": "https://platform.openai.com/api-keys"},
    "google":    {"label": "Google — Gemini", "default_model": "gemini-3.1-pro-preview",
                  "key_url": "https://aistudio.google.com/apikey"},
    "microsoft": {"label": "Microsoft — MAI / Copilot", "default_model": "MAI-Thinking-1",
                  "key_url": "https://portal.azure.com/"},
    "deepseek":  {"label": "DeepSeek", "default_model": "deepseek-v4-pro",
                  "key_url": "https://platform.deepseek.com/api_keys"},
    "xai":       {"label": "xAI — Grok", "default_model": "grok-4.3",
                  "key_url": "https://console.x.ai/"},
    "custom":    {"label": "Custom (OpenAI-compatible)", "default_model": "",
                  "key_url": ""},
}


def keyring_id_for(name: str) -> str:
    """Derive a stable keyring id from a provider name (1:1, namespaced)."""
    return f"intergen-provider:{name}"


def _user_config_path() -> Path:
    # INTERGEN_USER_CONFIG overrides the path (tests; non-default installs).
    override = os.environ.get("INTERGEN_USER_CONFIG")
    return Path(override) if override else _USER_CONFIG


def _load() -> dict[str, Any]:
    path = _user_config_path()
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except Exception:  # noqa: BLE001 — a corrupt user file must not crash the panel
        return {}


def _save(cfg: dict[str, Any]) -> None:
    import yaml
    path = _user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)
        os.chmod(tmp, 0o600)        # user-only; the file names secrets' ids
        os.replace(tmp, path)       # atomic
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def list_providers() -> dict[str, Any]:
    """Current provider config for the panel (never includes any key)."""
    cfg = _load()
    esc = cfg.get("escalation") or {}
    providers = cfg.get("providers") or []
    # Defensive: never leak a key even if a hand-edit put one here.
    safe = [{k: v for k, v in p.items() if k != "api_key"} for p in providers]
    return {
        "providers": safe,
        "primary": esc.get("primary_provider"),
        "mode": esc.get("mode", "ask"),
        "available_adapters": list(VALID_ADAPTERS),
        "catalog": PROVIDER_CATALOG,
    }


def upsert_provider(name: str, adapter: str, model: str, *,
                    base_url: str | None = None,
                    max_tokens: int = 4096,
                    temperature: float = 0.7) -> dict[str, Any]:
    """Add or replace a provider (by name). Returns the persisted entry (no key).
    The caller stores the API key in the keyring separately (store_secret)."""
    name = (name or "").strip()
    adapter = (adapter or "").strip().lower()
    model = (model or "").strip()
    if not name:
        raise ValueError("provider name is required")
    if adapter not in VALID_ADAPTERS:
        raise ValueError(
            f"unknown adapter {adapter!r}; choose one of {', '.join(VALID_ADAPTERS)}")
    if not model:
        raise ValueError("model is required")

    entry: dict[str, Any] = {
        "name": name,
        "adapter": adapter,
        "model": model,
        "api_key_keyring_id": keyring_id_for(name),
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
    }
    if base_url:
        entry["base_url"] = base_url.strip()

    cfg = _load()
    providers = [p for p in (cfg.get("providers") or []) if p.get("name") != name]
    providers.append(entry)
    cfg["providers"] = providers
    _save(cfg)
    return entry


def remove_provider(name: str) -> bool:
    """Remove a provider by name; clear it as primary if it was. Returns True
    if one was removed. (The caller deletes its keyring secret separately.)"""
    cfg = _load()
    providers = cfg.get("providers") or []
    kept = [p for p in providers if p.get("name") != name]
    if len(kept) == len(providers):
        return False
    cfg["providers"] = kept
    esc = cfg.setdefault("escalation", {})
    if esc.get("primary_provider") == name:
        esc["primary_provider"] = None
    _save(cfg)
    return True


def set_primary(name: str | None) -> None:
    """Set (or clear, with None) the primary provider. Must name a configured one."""
    cfg = _load()
    providers = cfg.get("providers") or []
    if name is not None and not any(p.get("name") == name for p in providers):
        raise ValueError(f"no configured provider named {name!r}")
    esc = cfg.setdefault("escalation", {})
    esc["primary_provider"] = name
    esc.setdefault("mode", "ask")
    _save(cfg)
