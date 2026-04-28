# InterGen AI Assistant — Integration Plan

## Context

InterGenOS VISION.md defines a tiered local AI assistant but no implementation exists yet. The owner read Anthropic's __PROJECT_Sentinel__ announcement (Claude Mythos Preview for cybersecurity) and wants AI integration prioritized. This plan maps out every package, dependency, and application component needed to go from zero to a working AI assistant in the InterGenOS build.

---

## Key Architecture Decisions

### 1. Replace Vosk with whisper.cpp for all STT tiers
Vosk requires Kaldi (32GB RAM, 100GB disk, hours to compile). whisper.cpp is from the same team as llama.cpp, builds with cmake in minutes, and has better accuracy (WER ~6% vs Vosk ~15%). Update VISION.md accordingly.

### 2. Let Piper bundle its own ONNX Runtime
Building ONNX Runtime from source is impractical. Piper's cmake FetchContent handles it. We pre-download the dependency tarballs for offline builds.

### 3. New "ai" tier between desktop and extra
Keeps AI packages separate from desktop infrastructure. Optional — systems built without it produce a standard GNOME desktop.

### 4. Kokoro TTS (Tier 3) is Python + ONNX, not a C++ build
Installed via pip helper, not built from source. Same pattern as claude-code-helper.

### 5. Sentinel is a pure Python module (always installed, runtime-gated)
Anthropic SDK via pip. No C/C++ build. Package is always included in the ai tier. Functionality activates at runtime only when internet + API key are configured.

---

## Package Dependency Chain

```
Already exists (desktop tier):
  cmake, gcc, python, dbus, pipewire, pulseaudio, alsa,
  vulkan-headers, vulkan-loader, spirv-tools, systemd

New packages (ai tier), build order:

  1. espeak-ng          [cmake, C, no AI deps]
  2. llama-cpp          [cmake, C++, no AI deps]      ← can parallel with 1,3
  3. whisper-cpp        [cmake, C++, no AI deps]      ← can parallel with 1,2
  4. piper-tts          [cmake, C++, depends on espeak-ng]
  5. intergen           [custom, Python app, depends on 2+3+4]
  6. intergen-glasswing [custom, Python, depends on 5] ← included
  7. intergen-gnome-ext [custom, GJS, depends on 5]   ← included
```

---

## Package Specifications

### 1. espeak-ng
- **Version:** 1.52.0
- **Style:** cmake
- **Source:** `github.com/espeak-ng/espeak-ng` release tarball
- **Deps:** cmake (existing)
- **Flags:** `-DUSE_ASYNC=OFF -DBUILD_SHARED_LIBS=ON`
- **Purpose:** Phonemizer for Piper TTS (text → phonemes)

### 2. llama-cpp
- **Version:** latest tagged release (e.g., b5678)
- **Style:** cmake
- **Source:** `github.com/ggml-org/llama.cpp` release tarball
- **Deps:** cmake (existing); optional vulkan-headers (existing, for GPU)
- **Key flags:** `-DLLAMA_BUILD_SERVER=ON -DBUILD_SHARED_LIBS=ON`
- **Outputs:** `llama-server` (HTTP API), `llama-cli`, `libllama.so`, `libggml.so`
- **GPU:** Vulkan auto-detected at build time (headers already in desktop tier)

### 3. whisper-cpp
- **Version:** latest tagged release
- **Style:** cmake
- **Source:** `github.com/ggml-org/whisper.cpp` release tarball
- **Deps:** cmake (existing)
- **Key flags:** `-DWHISPER_SDL2=OFF -DBUILD_SHARED_LIBS=ON`
- **Outputs:** `libwhisper.so`, `whisper-cli`

### 4. piper-tts
- **Version:** 1.4.2
- **Style:** custom (cmake with FetchContent)
- **Source:** `github.com/OHF-Voice/piper1-gpl` release tarball
- **Deps:** cmake (existing), espeak-ng
- **Build note:** Pre-download FetchContent deps (onnxruntime, fmt, spdlog, piper-phonemize) into `$IGOS_SOURCES`; build with `FETCHCONTENT_FULLY_DISCONNECTED=ON`
- **Outputs:** `piper` binary

### 5. intergen (the application)
- **Version:** 0.1.0
- **Style:** custom
- **Source:** [] (lives in InterGenOS repo, like claude-code-helper)
- **Deps:** llama-cpp, whisper-cpp, piper-tts, python (existing)
- **Installs:**
  - `/usr/lib/intergen/` — Python package
  - `/usr/bin/intergen` — CLI entry point
  - `/usr/share/dbus-1/services/com.intergenos.InterGen.service`
  - `/usr/lib/systemd/user/intergen.service`
  - `/etc/intergen/config.yml`
  - `/usr/share/intergen/prompts/` — system prompt templates
  - `/usr/share/intergen/models.yml` — model manifest with URLs + checksums

### 6. intergen-glasswing (included in standard build)
- **Style:** custom, source: []
- **Deps:** intergen, python
- **Installs:** Helper that pip-installs `anthropic` SDK + glasswing module
- **Commands:** `intergen scan`, `intergen harden`, `intergen audit`
- **Note:** Included in the ai tier build. Functionality requires internet + API key at runtime, but the package and tooling are always present. Users without an API key simply see "configure API key to enable security scanning" messaging.

### 7. intergen-gnome-extension (included in standard build)
- **Style:** custom, source: []
- **Deps:** intergen, gnome-shell
- **Installs:** GNOME Shell extension at `/usr/share/gnome-shell/extensions/intergen@intergenos.com/`
- **Features:** Panel indicator, popup chat, quick actions
- **Note:** Included in the ai tier build. This is the primary user-facing interface for InterGen on the desktop.

---

## InterGen Application Architecture

```
GNOME Shell Extension (panel indicator + chat popup)
        │ D-Bus: com.intergenos.InterGen
        ▼
InterGen Daemon (Python, systemd user service)
  ├── Hardware Detector — reads /proc/cpuinfo, /proc/meminfo, /sys/class/drm/
  ├── LLM Interface    — manages llama-server subprocess (HTTP localhost:8080)
  ├── STT Interface    — whisper.cpp via ctypes/subprocess + PipeWire capture
  ├── TTS Interface    — piper subprocess (stdin text → stdout PCM → pw-play)
  ├── CLI Interface    — intergen ask/chat/explain/log/status/model
  ├── D-Bus Service    — Ask(), StartListening(), GetStatus(), ExplainCommand()
  ├── System Integration — journalctl, config reader, pkm queries
  └── [Optional] Sentinel — Anthropic API for security scanning
```

**Tier detection logic:**
- < 8GB RAM → Tier 1
- 8-15GB RAM, no discrete GPU → Tier 2
- 16GB+ RAM with discrete GPU → Tier 3

**Model selection per tier:**

| Tier | LLM | STT | TTS |
|------|-----|-----|-----|
| 1 | Qwen3-0.6B Q4_K_M (~397MB) | whisper-tiny.en (~75MB) | Piper lessac-medium (~63MB) |
| 2 | Qwen3-1.7B Q4_K_M (~1.3GB) | whisper-base.en (~142MB) | Piper lessac-medium (~63MB) |
| 3 | Qwen3-8B+ Q4_K_M (~4.9GB+) | whisper-medium (~1.5GB) | Kokoro v1.0 (~325MB) |

---

## Model Management

Models stored at `/var/lib/intergen/models/{llm,stt,tts}/`. Downloaded post-install via:

```bash
intergen model download --auto     # download for detected tier
intergen model download <name>     # specific model
intergen model list                # show available
```

**Download sources:** Hugging Face Hub (primary), InterGenOS VPS mirror (fallback).

**Manifest:** `/usr/share/intergen/models.yml` with URLs, sizes, SHA256 checksums.

**First boot:** InterGen daemon detects no models → GNOME notification → user triggers download. No internet required during install.

---

## Build System Changes

### Files to modify:
- `igos-build/parser.py:93` — add `"ai"` to `VALID_TIERS` set
- `igos-build/graph.py:115` — add `"ai": 3.5` to `tier_priority` dict
- `scripts/build-intergenos.sh:53-65` — add `ai` phase between `desktop` and `image`

### New files:
- `packages/ai/espeak-ng/{package.yml,build.sh}`
- `packages/ai/llama-cpp/{package.yml,build.sh}`
- `packages/ai/whisper-cpp/{package.yml,build.sh}`
- `packages/ai/piper-tts/{package.yml,build.sh}`
- `packages/ai/intergen/{package.yml,build.sh}` + Python source
- `packages/ai/intergen-glasswing/{package.yml,build.sh}`
- `packages/ai/intergen-gnome-extension/{package.yml,build.sh}`
- `scripts/chroot-build-ai.sh` (or use existing `chroot-build-tier.sh --tier ai`)

---

## Implementation Phases

### Phase 1: Build infrastructure + C++ packages
- Add "ai" tier to parser/graph/build-intergenos.sh
- Create espeak-ng, llama-cpp, whisper-cpp package templates
- Test building all three in build VM

### Phase 2: Piper TTS
- Create piper-tts package template
- Handle FetchContent pre-download for offline builds
- Test: `echo "Hello from InterGenOS" | piper --model lessac-medium.onnx`

### Phase 3: InterGen core application (text mode)
- Hardware detection, LLM interface, CLI, model manager
- Create intergen package template
- Test: `intergen ask "what is Linux?"` → get response from Qwen3-0.6B

### Phase 4: Voice pipeline
- STT + TTS integration, PipeWire audio capture
- D-Bus service
- Test: speak → transcribe → LLM → speak response

### Phase 5: GNOME integration + Sentinel
- Shell extension (panel indicator, popup chat)
- First-boot model download notification
- Anthropic API client for security scanning (`intergen scan`, `intergen harden`, `intergen audit`)
- API key configuration flow in CLI and GNOME extension settings

### Phase 6: Tier 3 enhancements
- Vulkan GPU acceleration for llama.cpp
- Kokoro TTS for Tier 3

---

## Pre-requisites Before Starting

1. **Bare metal boot must work** — AI needs PipeWire audio working (SOF firmware for Ice Lake on HP laptop)
2. **Build VM must be functional** — need to compile the AI tier after desktop
3. **Pre-download Piper FetchContent deps** — add onnxruntime, fmt, spdlog tarballs to source mirror

---

## Verification

After Phase 3 (minimum viable product):
1. Boot InterGenOS (VM or bare metal)
2. Run `intergen model download --auto`
3. Run `intergen ask "explain the /etc/fstab file on this system"`
4. Verify response is coherent and references actual system content
5. Run `intergen status` — shows detected tier, loaded model, memory usage
