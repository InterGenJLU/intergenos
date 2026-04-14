"""Hardware detection and model management interfaces."""

from __future__ import annotations
from abc import ABC, abstractmethod

from intergen.interfaces.types import HardwareTier, HardwareTierLevel, ModelInfo, ServerHealth


class HardwareDetectorInterface(ABC):
    """Detects system hardware and assigns an LLM tier."""

    @abstractmethod
    def detect(self) -> HardwareTier:
        """Probe hardware and return tier assignment.

        Reads: /proc/meminfo, lspci, /sys/class/drm/*/device/vendor
        """

    @abstractmethod
    def get_tier(self) -> HardwareTier:
        """Return cached tier (calls detect() on first access)."""


class ModelManagerInterface(ABC):
    """Downloads, verifies, and selects LLM models."""

    @abstractmethod
    def download_model(self, model: ModelInfo, *,
                       progress_callback: callable | None = None) -> bool:
        """Download model from Hugging Face.

        Args:
            model: Model info with repo_id and filename.
            progress_callback: Called with (bytes_downloaded, total_bytes).

        Returns:
            True if download + SHA256 verification succeeded.
        """

    @abstractmethod
    def get_model_for_tier(self, tier: HardwareTierLevel) -> ModelInfo:
        """Return the recommended model for a hardware tier."""

    @abstractmethod
    def list_downloaded(self) -> list[ModelInfo]:
        """List all downloaded and verified models."""

    @abstractmethod
    def verify_model(self, model: ModelInfo) -> bool:
        """Verify SHA256 hash of a downloaded model."""

    @abstractmethod
    def get_embedding_model(self) -> ModelInfo:
        """Return info for the embedding model (nomic-embed-text-v1.5)."""


class LlamaManagerInterface(ABC):
    """Manages the llama-server subprocess lifecycle."""

    @abstractmethod
    def start(self, model_path: str, *,
              port: int = 8080,
              context_size: int = 8192,
              gpu_layers: int = 999,
              jinja: bool = True) -> bool:
        """Start llama-server with the given model.

        Args:
            model_path: Path to .gguf model file.
            port: HTTP port for the server.
            context_size: Context window size.
            gpu_layers: Number of layers to offload to GPU.
            jinja: Enable --jinja flag for tool calling support.

        Returns:
            True if server started and health check passed.
        """

    @abstractmethod
    def stop(self) -> None:
        """Stop the llama-server subprocess."""

    @abstractmethod
    def restart(self) -> bool:
        """Stop and restart with the same configuration."""

    @abstractmethod
    def health(self) -> ServerHealth:
        """Check server health via GET /health endpoint."""

    @abstractmethod
    def get_endpoint(self) -> str:
        """Return the server's chat completions endpoint URL."""

    @abstractmethod
    def is_running(self) -> bool:
        """Return True if the server process is alive."""
