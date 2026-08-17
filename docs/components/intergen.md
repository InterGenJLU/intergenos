# InterGen AI Assistant Architecture

This document describes InterGen, the local AI assistant built into InterGenOS. InterGen runs entirely on the local machine, with no cloud dependency by default, and serves as a conversational interface for system administration, configuration, and coding.

## Core Design Principles

1. **Local-first.** Inference runs on local hardware by default, so your data stays on your machine. This gives you a system you understand, can modify, and can trust.
2. **Hardware-tiered.** InterGen detects the host's GPU and selects an appropriately sized model automatically.
3. **Security is not first. It is only.** Every proposed action passes through a safety classifier, and destructive or security-bypassing operations are refused outright.
4. **Predictable routing.** Requests flow through a deterministic, priority-ordered routing chain that resolves common queries cheaply and predictably instead of handing the entire interaction to the model.

## Model Catalog and Hardware Tiers

InterGen scales to the hardware it detects on the host. `intergen/hardware.py` probes the GPU and assigns a tier; `intergen/model_manager.py` holds the canonical model catalog and downloads, verifies, and selects the model for that tier; `intergen/llama_manager.py` manages the `llama-server` subprocess (from llama.cpp) that actually serves the selected model over a local HTTP API.

**Tier assignment keys on discrete-GPU presence and video memory only.** System RAM is never an input. A machine without a discrete GPU serves the smallest model regardless of how much RAM it has, because the larger models on a CPU-only host are too slow to be usable (roughly 50 seconds per query for the 9B, against roughly 17 seconds for the 2B). Unknown or unreadable GPU capability always fails *down* to the smaller model, never up. A GPU is treated as discrete only when it reports at least 3 GB of dedicated video memory — integrated graphics carve a small buffer out of system RAM and are correctly treated as CPU-only.

The shipping tiers, evaluated top-down, are:

*   **Tier 1 (InternVL3.5-2B, the universal floor)**: InternVL3.5-2B Q4_K_M (~1.2 GB), a vision-language model. This is the universal floor — it runs on every install. Selected whenever there is no discrete GPU, and whenever a discrete GPU's video memory is too small for a higher tier or cannot be read. Handles semantic matching, system queries, log summaries, and keyword extraction, and — via its paired vision projector — can read a screenshot or image. Not used for complex code generation.
*   **Tier 2 (Qwen3.5-9B, Standard)**: Qwen3.5-9B Q4_K_M (~5.5 GB). Selected on a discrete GPU with at least 7 GiB of video memory — enough to hold the model, its vision projector, and the working buffers resident. The default daily driver, capable of coding, system configuration, and reasoning.
*   **Tier 3 (Qwen3.5-35B-A3B, Advanced)**: Qwen3.5-35B-A3B Q4_K_M, a Mixture-of-Experts model (~21 GB), selected on a discrete GPU with at least roughly 22 GB of video memory so the model and its buffers stay resident on the card — deep, multi-file codebase analysis and complex architectural reasoning. The 35B is pinned in the shipped, signed model manifest along with its paired vision projector, so a machine that clears the video-memory gate runs it.

A small embedding model (nomic-embed-text, Apache-2.0) ships alongside every tier to power the semantic-matching layer of the router.

## Vision (Screenshots and Images)

Every tier's model ships a paired vision projector (`mmproj`) that runs locally, so InterGen can look at an image or a screenshot of your screen without sending anything off the machine. The `take_screenshot` tool captures the screen on request. Because a screen capture can expose sensitive on-screen content, it is a **CONFIRM**-gated action — InterGen surfaces a "Take a screenshot" consent prompt before capturing. A captured image is treated as **untrusted input** (the same ingress class as a downloaded file or a web result), so its content passes through InterGen Sentinel's ingress discipline instead of being trusted as if you had typed it.

## The Priority Router (`intergen/router.py`)

At the core of the assistant is a priority-ordered routing chain. Rather than sending every prompt straight to the model, the router tries to satisfy each request with the cheapest, most predictable method first and only escalates when it has to.

*   **Priority 0 (Decomposition)**: Detects compound requests ("update the system, then restart the web server"). When found, the router splits the prompt into sub-tasks and routes each one in turn.
*   **Priority 1 (Keyword/Regex Match)**: Fast pattern matches for common system commands (for example, "what's my IP?" or "check disk space"). These dispatch directly to a built-in tool without invoking the model.
*   **Priority 2 (Semantic Embedding Match)**: Lightweight embedding search against a pre-computed catalog of capabilities. A high-confidence match dispatches to the corresponding built-in tool.
*   **Priority 3 (LLM Tool Calling)**: For Tier 2 and above, the query is sent to the model together with a schema of available system tools. The model selects a tool and its arguments, and the router executes the call and synthesizes the result.
*   **Priority 4 (LLM Free Response)**: The fallback. The model answers conversationally from its own knowledge and the conversation context.

## Safety Classification

Every action the router proposes passes through the classifier in `intergen/safety.py`, which sorts the operation into one of three tiers:

1.  **`AUTO`**: Read-only or harmless operations (for example `ls`, `grep`, `systemctl status`). Executed immediately, without a prompt.
2.  **`CONFIRM`**: State-changing operations (for example `systemctl restart`, `pkm install`, or editing a config file). The assistant pauses and asks you to approve the action before it runs.
3.  **`BLOCKED`**: Destructive or security-bypassing operations (for example `rm -rf /` or reformatting the root partition). The assistant refuses outright and explains why the command was rejected.

This classification is what keeps the user in control: nothing that changes the system runs without explicit approval, and the most dangerous commands cannot run at all.

## D-Bus Integration (`intergen/dbus_daemon.py`)

InterGen exposes its capabilities to the GNOME desktop through a D-Bus service, so other applications (a text editor, a terminal, or a system settings panel) can request completion, summarization, or semantic search over IPC.

The D-Bus surface is deliberately narrow. Only a small set of vetted interfaces is exposed, which prevents a local unprivileged application from driving arbitrary code execution through `dbus-send`.

## MCP Client Integration

InterGen is a Model Context Protocol (MCP) client (`intergen/mcp_client.py`). It can connect to MCP servers running locally to acquire new capabilities or query additional data sources, while preserving the boundary between the assistant's core runtime and the tool-execution environment.

## Memory and State

InterGen keeps conversational context with `intergen/memory.py`, which manages a user-controlled store of persistent facts plus a rolling window of recent turns, and `intergen/state_cache.py`, which caches recent query results so identical prompts are not recomputed. You stay in control of what InterGen remembers: facts are added and removed by explicit request, and stored data is fully inspectable. All of it is serialized and persisted locally.

## InterGen Sentinel

InterGen Sentinel is the security scanner that guards InterGen's interactions with the outside world. It inspects content crossing two boundaries: data returned from external and MCP tools (ingress) and content about to be sent off-device (egress). Both surfaces are scanned by default; turning scanning off requires the human-authenticated path, and the scan policy is in the protected configuration set that the assistant itself can never edit.

Sentinel is pluggable. The default configuration runs two stages, both local: a fast local-rules pass (the baseline floor) and an optional deep pass backed by a small local Qwen classifier. For deeper analysis you may opt in to a cloud scanner backed by one of six providers: Claude (Anthropic), Gemini (Google), Copilot (Microsoft), ChatGPT (OpenAI), Grok (xAI), or DeepSeek. No cloud provider is configured by default, so the default install scans entirely on-device.

## Phone-A-Friend (Frontier/Cloud Escalation)

Phone-A-Friend (Frontier/Cloud Escalation) is an optional, consent-first path for handing a request to a more capable frontier model in the cloud when the local assistant cannot satisfy it. It is off by default: no provider is configured out of the box, and the feature only acts when you have explicitly set one up.

When a request exceeds local capability, InterGen offers to escalate and asks before reaching out. The same six providers available to Sentinel can be configured here, with API keys stored in the system keyring rather than in plain configuration. Every outbound payload is scanned by Sentinel's egress policy first, so a blocked send keeps sensitive content from leaving the machine. This preserves InterGen's local-first posture: cloud assistance is available when you ask for it, never imposed.
