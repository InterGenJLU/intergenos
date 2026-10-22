# InterGen AI Assistant Architecture

This document describes InterGen, the local AI assistant built into InterGenOS. InterGen's model runs on the local machine and the assistant sends nothing in the background (the cases where something does leave the machine are listed under What Leaves the Machine below); it serves as a conversational interface for system administration, configuration, and coding.

## Core Design Principles

1. **Local-first.** Inference always runs on local hardware, and nothing leaves the machine in the background: a web search the person asks for or accepts, and a cloud provider the person sets up and approves each send to, are the exceptions. This gives you a system you understand, can modify, and can trust.
2. **Hardware-tiered.** InterGen detects the host's GPU and selects an appropriately sized model automatically.
3. **Security is not first. It is only.** Every proposed action passes through a safety classifier, and destructive or security-bypassing operations are refused outright.
4. **Predictable routing.** Requests flow through a deterministic, priority-ordered routing chain that resolves common queries cheaply and predictably instead of handing the entire interaction to the model.

## Model Catalog and Hardware Tiers

InterGen scales to the hardware it detects on the host. `intergen/hardware.py` probes the GPU and assigns a tier; `intergen/model_manager.py` holds the canonical model catalog and downloads, verifies, and selects the model for that tier; `intergen/llama_manager.py` manages the `llama-server` subprocess (from llama.cpp) that actually serves the selected model over a local HTTP API.

**Tier assignment keys on discrete-GPU presence and video memory only.** System RAM is never an input. A machine without a discrete GPU serves the smallest model regardless of how much RAM it has, because the larger models on a CPU-only host are too slow to be usable (roughly 50 seconds per query for the 9B, against roughly 17 seconds for the 2B). Unknown or unreadable GPU capability always fails *down* to the smaller model, never up. A GPU is treated as discrete only when it reports at least 3 GB of dedicated video memory — integrated graphics carve a small buffer out of system RAM and are correctly treated as CPU-only.

The shipping tiers, evaluated top-down, are:

*   **Tier 1 (InternVL3.5-2B, the universal floor)**: InternVL3.5-2B Q4_K_M (~1.3 GB), a vision-language model. This is the universal floor — it runs on every install. Selected whenever there is no discrete GPU, and whenever a discrete GPU's video memory is too small for a higher tier or cannot be read. Handles semantic matching, system queries, log summaries, and keyword extraction, and — via its paired vision projector — can read a screenshot or image. Not used for complex code generation.
*   **Tier 2 (Qwen3.5-9B, Standard)**: Qwen3.5-9B Q4_K_M (~5.6 GB). Selected on a discrete GPU with at least 7 GiB of video memory — enough to hold the model, its vision projector, and the working buffers resident. The default daily driver, capable of coding, system configuration, and reasoning.
*   **Tier 3 (Qwen3.5-35B-A3B, Advanced)**: Qwen3.5-35B-A3B Q4_K_M, a Mixture-of-Experts model (~22 GB), selected on a discrete GPU with at least roughly 22 GB of video memory so the model and its buffers stay resident on the card — deep, multi-file codebase analysis and complex architectural reasoning. The 35B is pinned in the shipped, signed model manifest along with its paired vision projector, so a machine that clears the video-memory gate runs it.

A small embedding model (nomic-embed-text, Apache-2.0) is downloaded alongside every tier's model to power the semantic-matching layer of the router.

## Vision (Screenshots and Images)

Every tier's model ships a paired vision projector (`mmproj`) that runs locally, so InterGen can look at an image or a screenshot of your screen without sending anything off the machine. The `take_screenshot` tool captures the screen on request. Because a screen capture can expose sensitive on-screen content, it is a **CONFIRM**-gated action — the capture runs through the desktop's own screenshot dialog, where you choose what is captured and press its button; no other process can answer that dialog for you. Every picture — a capture, or an image you attach — is **untrusted input** (the same ingress class as a downloaded file or a web result) and meets a gate before the model's turn: the local vision model transcribes the picture's text under a fixed instruction, the transcription runs through InterGen Sentinel's scanner, a picture carrying an instruction aimed at the assistant, a destructive command, a download piped into a shell or a "paste this" lure is refused with its reason (the model never sees it), and an admitted picture enters the turn behind a marker that tells the model the picture's words are not your request. The model layer refuses any picture the gate did not admit, so no code path can put an unchecked picture in front of it.

## The Priority Router (`intergen/router.py`)

At the core of the assistant is a priority-ordered routing chain. Rather than sending every prompt straight to the model, the router tries to satisfy each request with the cheapest, most predictable method first and only escalates when it has to.

*   **Priority 0 (Decomposition)**: Detects compound requests ("update the system, then restart the web server"). When found, the router splits the prompt into sub-tasks and routes each one in turn.
*   **Priority 1 (Keyword/Regex Match)**: Fast pattern matches for common system commands (for example, "what's my IP?" or "check disk space"). These dispatch directly to a built-in tool without invoking the model.
*   **Priority 2 (Semantic Embedding Match)**: Lightweight embedding search against a pre-computed catalog of capabilities. A high-confidence match dispatches to the corresponding built-in tool.
*   **Priority 3 (LLM Tool Calling)**: On the Tier 2 model only — the one lane whose native dispatch ships unlocked; the Tier 1 and Tier 3 models run locked down, where only the code-owned matches above dispatch a tool — the query is sent to the local model together with a schema of available system tools. The model selects a tool and its arguments, and the router executes the call through the safety classifier and synthesizes the result. A web search the model selects runs only when the person asked for one on that turn or accepted the offer; otherwise it is held and offered.
*   **Priority 4 (LLM Free Response)**: The fallback. The model answers conversationally from its own knowledge and the conversation context.

## Safety Classification

Every action the router proposes passes through the classifier in `intergen/safety.py`, which sorts the operation into one of three tiers:

1.  **`AUTO`**: Read-only or harmless operations (for example `ls`, `grep`, `systemctl status --no-pager`). Executed immediately, without a prompt.
2.  **`CONFIRM`**: State-changing operations (for example `systemctl restart`, `pkm install`, or editing a config file). The assistant pauses and asks you to approve the action before it runs.
3.  **`BLOCKED`**: Destructive or security-bypassing operations (for example `rm -rf /` or reformatting the root partition). The assistant refuses outright and explains why the command was rejected.

This classification is what keeps the user in control: nothing that changes the system runs without explicit approval, and the most dangerous commands cannot run at all.

## D-Bus Integration (`intergen/dbus_daemon.py`)

InterGen exposes its capabilities to the GNOME desktop through a D-Bus service (`com.intergenos.InterGen` on the session bus), so other applications in the session can ask it a question (`Ask`, answered through the same router and safety classifier as the panel), make a Phone-A-Friend request behind the same show-before-send review (`Escalate`), and read or steer the service (`Status`, `GetTier`, `ResetConversation`, `PauseForGame`, `ResumeAfterGame`).

The D-Bus surface is deliberately narrow. Only these seven methods are exposed, which prevents a local unprivileged application from driving arbitrary code execution through `dbus-send`.

## MCP Client Integration

InterGen is a Model Context Protocol (MCP) client (`intergen/mcp_client.py`). It can connect to MCP servers running locally to acquire new capabilities or query additional data sources, while preserving the boundary between the assistant's core runtime and the tool-execution environment.

## Memory and State

InterGen keeps conversational context with `intergen/memory.py`, which manages a user-controlled store of persistent facts plus a rolling window of recent turns, and `intergen/state_cache.py`, which caches recent query results so identical prompts are not recomputed. You stay in control of what InterGen remembers: facts are added and removed by explicit request, and stored data is fully inspectable. All of it is serialized and persisted locally.

## InterGen Sentinel

InterGen Sentinel is the security scanner that guards InterGen's interactions with the outside world. It inspects content crossing two boundaries: data returned from web search and from external and MCP tools (ingress), and arguments the model composed for an external or MCP tool before they are handed over (egress). Both surfaces are scanned by default; turning scanning off requires the human-authenticated path, and the scan policy is in the protected configuration set that the assistant itself can never edit.

Sentinel runs in two local stages: a fast rules pass on every scanned item (the baseline floor), and a deeper read by a small local classifier — the Tier 1 InternVL3.5-2B model on its own local server, when that model is on disk — that a flagged item is passed to, or every item when the scan depth is set to deep. The source tree also carries a cloud-scanner adapter, but this release does not connect it: a deep-scanner setting other than the local classifier leaves the rules floor in charge, so scanning always happens on the machine.

## Phone-A-Friend (Frontier/Cloud Escalation)

Phone-A-Friend (Frontier/Cloud Escalation) is an optional, consent-first path for handing a request to a more capable frontier model in the cloud when the local assistant cannot satisfy it. It is off by default: no provider is configured out of the box, and the feature only acts when you have explicitly set one up.

When a request looks beyond the local model, InterGen can offer to escalate; the `escalation.mode` setting decides when it offers (`ask`, the default, and `auto` on the same signals, `fallback` only when the local answer fails its quality check, `never` not at all). Nothing is sent until the person asks — the "Ask my frontier model" button, `intergen ask-frontier`, or the bus's `Escalate` — and approves the exact text and the provider's name in a show-before-send dialog; the approved text is sent as shown. Six provider families can be configured (Anthropic, Google, Microsoft, OpenAI, xAI and DeepSeek) plus a custom endpoint; the API key is read from the system keyring at call time, never stored in the configuration, and is refused over any connection that is not TLS, except to the local machine. This preserves InterGen's local-first posture: cloud assistance is available when you ask for it, never imposed.

## What Leaves the Machine

The model and every routing stage run locally, and nothing is sent in the background. The network paths, each started by the person:

*   **Model setup** (`intergen/setup.py`, `intergen/model_manager.py`): the tier's model, its vision projector and the embedding model, from the InterGenOS mirror with Hugging Face as the fallback source, each verified against its pin in the signed manifest before use.
*   **Web search** (`intergen/tools/web_search.py`): the search words to DuckDuckGo, or to Serper first when `SERPER_API_KEY` is set in the service's environment (DuckDuckGo only if Serper fails or returns nothing); then the first result's page, fetched from its own site — public addresses only, at most 128 KiB, 8 seconds and three redirects. The tool registry runs a search only on the person's word: an explicit ask, or an accepted offer.
*   **Network commands** (`intergen/safety.py`): `ping`, `dig`, `nslookup`, `host` and `traceroute` are AUTO-tier and reach the host they name; `curl` and `wget` are CONFIRM-tier. The IP-address answer (`intergen/router.py`, the code-owned handler) reads the internal address with `ifconfig` and sends one DNS query to Cloudflare's public resolver for the public IPv4 address; the IPv6 lookup runs only after the person accepts the offer.
*   **Phone-A-Friend** (`intergen/escalation.py`, `intergen/cloud/`): only with a configured provider, only on the person's request, and only after the show-before-send approval described above.
