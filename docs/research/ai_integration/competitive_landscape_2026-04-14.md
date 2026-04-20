# InterGen Competitive Landscape Analysis
**Date:** April 14, 2026
**Purpose:** Where InterGen sits among existing AI assistants

---

## Sources

### Newelle — GNOME AI Assistant
- **v1.0:** https://www.phoronix.com/news/GNOME-AI-Assistant-1.0
- **v1.2 (llama.cpp + tools):** https://www.phoronix.com/news/GNOME-AI-Newelle-1.2
- GNOME-native GTK app, Flatpak distribution
- Supports: Google Gemini, OpenAI, Groq, local LLMs (Ollama, llama.cpp)
- Features: voice chat, MCP support, long-term memory, document reading, extensions
- Gaps: no hardware-aware tiering, no safety classification, no system-deep integration

### ManageLM — AI Server Management
- **URL:** https://www.managelm.com/
- 31 skills, 230+ operations, enterprise compliance (CIS, SOC 2, HIPAA)
- Local LLM via Ollama, zero inbound ports, command allowlisting in CODE
- Kernel sandbox (Landlock/seccomp), secrets hidden from AI
- Free for 10 agents, Pro/Enterprise for unlimited
- Gaps: server-focused (not desktop), web dashboard (not panel integrated)

### OpenJarvis — Stanford Research Framework
- **URL:** https://scalingintelligence.stanford.edu/blogs/openjarvis/
- Apache 2.0, five-primitive architecture
- Learning loop that improves from local trace data
- 88.7% of single-turn queries handled locally
- Backends: Ollama, vLLM, SGLang, llama.cpp
- Gaps: research framework (not end-user product), no OS integration

### Other Evaluated
- **PyGPT** (pygpt.net) — Desktop chat, Qt app, Ollama support
- **Leon AI** (github.com/leon-ai/leon) — Skills-based, API-first
- **OpenClaw** — Self-hosted autonomous agent, messaging platforms
- **Mycroft** — Voice assistant, Raspberry Pi focus, open-source

---

## InterGen's Unique Differentiators
1. Ships WITH the OS (not bolt-on)
2. Hardware-aware tiering (auto model selection)
3. Sub-10ms routing via 4-layer semantic matching + template synthesis
4. Tier-aware compound query decomposition
5. Glasswing MCP security (schema pinning, injection scanning)
6. "Phone a Friend" with user-chosen provider
7. PRIME DIRECTIVE as design filter

## What Others Have That We Should Consider
1. Long-term memory — IMPLEMENTED (user-controlled)
2. File comprehension — IMPLEMENTED (analyze_file tool)
3. Learning from traces — DEFERRED (complex, PRIME DIRECTIVE concerns)
4. Voice — SKIPPED (VOQR covers this)
5. 230 operations — SELECTIVE (add based on user need, not feature parity)
