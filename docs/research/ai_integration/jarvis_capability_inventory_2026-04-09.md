# JARVIS AI Assistant — Complete Capability Inventory for InterGen Porting

**Date:** 2026-04-09
**Source:** Full codebase analysis of /home/christopher/jarvis/

## Executive Summary

JARVIS is 66,000+ lines of production Python with a 4-layer semantic matching pipeline,
18-priority conversation router, 14 auto-discovered LLM tools, self-managing memory
(MemGPT pattern), and 100% tool calling accuracy across 1,200+ trials.

**70% is portable to InterGen.** The core architecture (router, LLM, skills, tool calling)
is universally useful. The 30% to remove is personal awareness (calendar, reminders,
news, faces, voice enrollment).

## Porting Decision Summary

### YES — Port to InterGen
- Conversation Router (simplified, system priorities)
- LLM Router (local-first + fallback, tool calling)
- Skill System (YAML metadata + Python handlers)
- Tool Registry (auto-discovery, semantic pruning)
- Semantic Matcher (pre-computed embeddings, 4-layer pipeline)
- Console Frontend (text REPL)
- Structured Event Logging
- Metrics Tracker
- Health Check + Watchdog
- Developer Tools (git, shell, process management)
- Filesystem skill
- System Info skill
- Quality Gates (empty/gibberish/echo detection)

### MAYBE — Adapt if needed
- Task Planner (simplify, remove voice interrupts)
- Context Window (keep token budgeting, remove proactive surfacing)
- Web UI (remote admin access is valuable)
- Memory → rename to "Knowledge Base" (FAISS for config/log search)

### NO — Remove entirely
- Conversational Awareness Layer (6 phases of personal briefings)
- Presence Detector (face detection/recognition)
- Speaker ID (voice enrollment)
- Reminder Manager
- News Manager
- Google Calendar / CalDAV
- Vision system (webcam, InsightFace)
- App Launcher
- Desktop Manager
- Web Navigation (Playwright)
- Honorific system
- Social Introductions skill

## Estimated Effort: 18-26 days (3-4 weeks)
