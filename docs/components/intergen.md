# InterGen AI Assistant Architecture

This document details `InterGen`, the locally-hosted AI assistant embedded directly into InterGenOS. Operating exclusively on the local machine without cloud dependencies, it serves as a conversational interface for system administration, configuration, and coding.

## Core Design Principles
1. **Local Only**: All inference runs on the local hardware to guarantee privacy and adhere to the user-control posture. 
2. **Hardware-Tiered Constraints**: Models are selected automatically based on available RAM and compute.
3. **Safety First**: Every action is filtered through a rigorous safety classifier ensuring destructive actions are flagged.
4. **Predictability via Routing**: Complex interactions are structured into a priority-based routing chain rather than delegating the entire flow to an unpredictable LLM.

## Model Catalog & Hardware Tiers

InterGen is dynamically scaled according to the hardware it detects on the host. `llama_manager.py` loads models corresponding to these tiers (canonical catalog in `intergen/model_manager.py`):

*   **Tier 1 (2B - Basic)**: Qwen3.5-2B Q4_K_M (~1.5 GB). Used on systems with <8 GB RAM. Limited to basic semantic matching, system querying, and keyword extraction. Not authorized for complex code generation.
*   **Tier 2 (9B - Standard)**: Qwen3.5-9B Q4_K_M. Requires 16 GB+ RAM. The default daily driver capable of coding, system configuration, and reasoning.
*   **Tier 3 (35B - Advanced)**: Qwen3.5-35B-A3B Q4_K_M (MoE). Requires 32 GB+ RAM. Used for deep, multi-file codebase analysis and complex architectural reasoning.

## The Priority Router (`intergen/router.py`)

The core of the assistant is an 8-priority routing engine. Instead of sending every user prompt straight to an LLM, the router attempts to fulfill the request using the cheapest, most predictable method first.

*   **Priority 0 (Decomposition)**: Analyzes if the query is a compound request ("Update the system then restart the web server"). If so, it decomposes the prompt into sub-tasks and recursively routes them.
*   **Priority 1 (Keyword/Regex Match)**: Fast, hardcoded regex matches for common system commands (e.g., "What's my IP?", "Check disk space"). Dispatches directly to an internal tool without invoking the LLM.
*   **Priority 2 (Semantic Embedding Match)**: Uses lightweight semantic search against a pre-computed database of capabilities. If a high-confidence match is found for an internal tool, it dispatches.
*   **Priority 3 (LLM Tool Calling)**: The query is sent to the LLM (if Tier 2+) with a schema of available system tools. The LLM decides which tool to call and with what arguments.
*   **Priority 4 (LLM Free Response)**: Fallback. The LLM answers conversationally based on its internal knowledge and the context window.
*   *(Priorities 5-7 are reserved for background jobs, log summarization, and deferred tasks, preventing UI lockups during active chat).*

## Safety Classification

Every generated action resulting from the router passes through `intergen/safety.py`. The classifier categorizes the proposed action into one of three states:

1.  **`AUTO`**: Read-only queries or harmless operations (e.g., `ls`, `grep`, `systemctl status`). Executed without prompt.
2.  **`CONFIRM`**: State-changing operations (e.g., `systemctl restart`, `pkm install`, editing a config file). The assistant pauses and presents a confirmation dialogue to the user before executing.
3.  **`BLOCKED`**: Highly destructive or security-bypassing operations (e.g., `rm -rf /`, formatting root partitions). The assistant refuses to execute the command and returns an error explaining the violation of the user-control posture.

## D-Bus Integration (`intergen/dbus_daemon.py`)

InterGen exposes its capabilities to the desktop environment via a D-Bus service. This allows other applications (like a text editor, terminal, or system settings panel) to send IPC messages requesting completion, summarization, or semantic search.

The D-Bus exposure is strictly bounded. Only specific safe interfaces are exposed to prevent arbitrary code execution by local unprivileged applications via `dbus-send`.

## MCP Client integration

InterGen supports the Model Context Protocol (MCP) (`intergen/mcp_client.py`). This allows the assistant to connect to external MCP servers (running locally) to securely acquire new capabilities or query external data sources, while maintaining the boundary between the assistant's core runtime and the tool execution environment.

## Memory & State

InterGen maintains conversational context using `intergen/memory.py` (a rolling window of previous turns) and `intergen/state_cache.py` (for fast retrieval of recent queries to avoid re-computing identical prompts). Memory is serialized and persisted locally.
