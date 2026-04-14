"""Analyze and explain file contents using LLM.

Reads a file and uses the local LLM to explain, diagnose, or summarize
its contents. This is the "file comprehension" tool — it answers
questions ABOUT files, not just reads them.

Use cases:
  - "What does this config do?"
  - "Is there anything wrong with this log?"
  - "Explain this systemd unit"
  - "Summarize this script"
"""

from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

log = logging.getLogger(__name__)

MAX_FILE_SIZE = 65536  # 64 KB — files larger than this are too big for LLM context
LLM_ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"


class AnalyzeFileTool(BaseTool):
    """Read a file and explain its contents using the LLM."""

    @property
    def name(self) -> str:
        return "analyze_file"

    @property
    def description(self) -> str:
        return (
            "Read a file and explain or diagnose its contents. "
            "Can explain config files, diagnose logs, summarize scripts, "
            "or answer specific questions about a file's contents."
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to analyze.",
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "What to analyze — e.g., 'explain this config', "
                            "'is there anything wrong', 'summarize this'."
                        ),
                        "default": "Explain what this file does.",
                    },
                },
                "required": ["path"],
            },
            safety_tier=SafetyTier.AUTO,
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Read the file and analyze it with the LLM."""
        path_str = arguments.get("path", "")
        question = arguments.get("question", "Explain what this file does.")

        if not path_str:
            return ToolResult(
                call_id="", name=self.name,
                content="Error: no path provided", success=False,
            )

        path = Path(path_str).expanduser().resolve()

        if not path.exists():
            return ToolResult(
                call_id="", name=self.name,
                content=f"File not found: {path}", success=False,
            )

        if not path.is_file():
            return ToolResult(
                call_id="", name=self.name,
                content=f"Not a regular file: {path}", success=False,
            )

        try:
            size = path.stat().st_size
        except OSError as e:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Cannot stat file: {e}", success=False,
            )

        if size > MAX_FILE_SIZE:
            return ToolResult(
                call_id="", name=self.name,
                content=(
                    f"File too large for analysis ({size:,} bytes, "
                    f"max {MAX_FILE_SIZE:,}). Try read_file with a line range."
                ),
                success=False,
            )

        log.info("Analyzing %s (%d bytes): %s", path, size, question)

        try:
            content = path.read_text(errors="replace")
        except OSError as e:
            log.error("Cannot read %s: %s", path, e)
            return ToolResult(
                call_id="", name=self.name,
                content=f"Cannot read file: {e}", success=False,
            )

        # Detect file type for context
        file_type = self._detect_type(path, content)

        # Build analysis prompt
        prompt = (
            f"Analyze this {file_type} file ({path.name}).\n"
            f"User's question: {question}\n\n"
            f"File contents:\n```\n{content}\n```\n\n"
            f"Provide a clear, concise analysis. If the user asked a specific "
            f"question, answer it directly. If they asked for an explanation, "
            f"describe what the file does and note anything important."
        )

        # Call LLM for analysis
        analysis = self._call_llm(prompt)
        if analysis is None:
            # LLM unavailable — return the file with basic info
            return ToolResult(
                call_id="", name=self.name,
                content=(
                    f"File: {path} ({file_type}, {size:,} bytes)\n"
                    f"LLM unavailable for analysis. File contents:\n{content}"
                ),
                success=True,
            )

        return ToolResult(
            call_id="", name=self.name,
            content=analysis,
            success=True,
        )

    def _detect_type(self, path: Path, content: str) -> str:
        """Detect file type from extension and content."""
        ext = path.suffix.lower()
        name = path.name.lower()

        type_map = {
            ".conf": "configuration",
            ".cfg": "configuration",
            ".ini": "configuration",
            ".yml": "YAML configuration",
            ".yaml": "YAML configuration",
            ".json": "JSON",
            ".xml": "XML",
            ".toml": "TOML configuration",
            ".service": "systemd unit",
            ".timer": "systemd timer",
            ".socket": "systemd socket",
            ".mount": "systemd mount",
            ".py": "Python",
            ".sh": "shell script",
            ".bash": "Bash script",
            ".log": "log",
            ".rules": "rules",
            ".desktop": "desktop entry",
        }

        if ext in type_map:
            return type_map[ext]
        if name in ("makefile", "dockerfile", "vagrantfile"):
            return name
        if content.startswith("#!/"):
            return "script"
        if content.startswith("[Unit]") or content.startswith("[Service]"):
            return "systemd unit"
        return "text"

    def _call_llm(self, prompt: str) -> str | None:
        """Call the local LLM for analysis. Returns None if unavailable."""
        try:
            data = json.dumps({
                "model": "local",
                "messages": [
                    {"role": "system", "content": (
                        "You are InterGen, analyzing a file on InterGenOS. "
                        "Be concise and technical. No filler."
                    )},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 1024,
                "temperature": 0.3,
            }).encode()

            req = urllib.request.Request(
                LLM_ENDPOINT,
                data=data,
                headers={"Content-Type": "application/json"},
            )

            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())

            content = result["choices"][0]["message"].get("content", "")
            if content.strip():
                return content.strip()

            # Reasoning model may have put everything in reasoning_content
            reasoning = result["choices"][0]["message"].get("reasoning_content", "")
            if reasoning.strip():
                log.debug("LLM returned reasoning only — using as analysis")
                return reasoning.strip()

            return None
        except Exception as e:
            log.warning("LLM analysis failed: %s", e)
            return None
