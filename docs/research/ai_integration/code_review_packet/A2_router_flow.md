# router.py Walkthrough — ConversationRouter

**File:** `intergen/router.py` (868 lines)
**Ported from:** JARVIS `core/conversation_router.py` (3,782 lines)
**Reduction:** 79% — removed voice, multi-user, conversation windows, task planner

---

## Class: ConversationRouter

### Constructor (lines 41-65)

Takes injected dependencies — no hard-coded singletons:
- `tool_registry`: discovers and executes tools
- `semantic_matcher`: 4-layer intent matching
- `llm`: LLM router (local + cloud)
- `event_logger`, `metrics`: observability
- `hardware_tier`: determines compound decomposition thresholds
- `memory`: session tracking and fact storage
- `state_cache`: single-value system state lookups

Conversation history is capped at 20 exchanges (`_max_history`). The history is cleared between test conversations to prevent contamination (handled by the test runner, not the router itself).

### route() — The Main Entry Point (lines 67-172)

This is the priority chain. Every user query enters here and exits as a `RouteResult`.

**Step 1: Classify query type** (line 72)
```python
self._current_query_type = self._classify_query_type(user_input)
```
Runs BEFORE any routing. The classification result is used in two places:
1. Adaptive prompt selection (when building LLM messages)
2. P3 routing decision (diagnostic queries forced to tool path)

**Step 2: Safety pre-check** (lines 93-100)
```python
_SAFETY_TRIGGERS = ("format", "delete", "remove", "wipe", ...)
has_safety_trigger = any(t in lower_input_raw for t in _SAFETY_TRIGGERS)
```
This flag prevents the cache from answering safety-sensitive queries. Without it, "format my disk" could match a cached "format" response from a previous harmless query.

**Step 3: Compound detection** (lines 103-108)
P0 in the priority chain. Uses `decomposer.py` to detect multi-part queries ("check disk and show memory"). If decomposition is needed, each sub-query is routed independently through P1-P4.

**Step 4: Smart cache** (lines 112-124)
```python
if self._state_cache and not has_safety_trigger:
    cached = self._state_cache.lookup_for_query(user_input)
    if cached and "\n" not in cached.strip():
```
Three guards:
1. Safety trigger → skip cache entirely
2. Multi-line output → skip (needs LLM formatting)
3. No match → fall through

When cache hits, response goes through `_template_synthesis()` for natural language formatting.

**Step 5: Self-awareness templates** (lines 127-132)
37 identity patterns with fallthrough aliases. "Who are you?" → "What are you?" → hardcoded response. No LLM call, 0ms.

**Step 6: Memory operations** (lines 135-139)
Handles "remember that...", "what were we working on?", "forget about...". Explicit storage only — no passive fact extraction (per PRIME DIRECTIVE: user controls what's stored).

**Step 7: P1 keyword match** (lines 142-145)
Deterministic regex/keyword matching via semantic matcher Layer 1. Maps known phrases to tool calls. Template synthesis first, LLM fallback for complex output.

**Step 8: P2 semantic match** (lines 148-154)
Embedding similarity via nomic-embed-text-v1.5. Threshold 0.85 (higher than JARVIS's 0.55-0.85 because system commands are dangerous — false positives could execute destructive operations).

**Step 9: P3 LLM tool calling** (lines 158-165)
```python
use_tools = (
    p2_match.score >= 0.7
    or self._current_query_type == "diagnostic"
)
```
This is the critical routing decision. Two paths into P3:
1. Semantic score ≥ 0.7 (probable tool match, but below P2's 0.85 threshold)
2. Diagnostic query type (ALWAYS, regardless of score)

The diagnostic override was added in R20 (commit 72c041c) after audit showed diagnostic queries falling through to P4 and fabricating system data.

**Step 10: P4 freeform** (lines 169-172)
Last resort. LLM generates a response without tools. Quality-gated: empty, repetitive, or echo responses trigger retry, then cloud escalation.

**Known issue:** P4 has no grounding mechanism. The LLM can fabricate system data from training data. Partially mitigated by routing diagnostic queries to P3 instead.

---

### _classify_query_type() (lines 761-787)

The adaptive query classifier. Uses three keyword sets — no LLM call, 0ms.

**Priority order matters:**
1. Safety triggers checked first (most critical)
2. Identity keywords second
3. Ultra-short queries (≤2 words) default to identity
4. Diagnostic keywords third
5. Everything else → general

The ≤2 word rule (line 781) was added because short/ambiguous queries like "?" or "help" were getting the general modifier, causing the LLM to respond with "I am InterGenOS" instead of "I'm InterGen." With the identity modifier, the LLM consistently self-identifies correctly.

**Design decision:** Safety and identity keywords are stored as `frozenset` class attributes (lines 743-759) for O(1) lookup. Diagnostic keywords use substring matching (`if kw in lower`) because some are multi-word ("running out", "can't reach").

---

### _try_self_awareness() (lines 174-265)

Static method — no instance state needed. 37 identity patterns organized as a dict with `None` values as aliases:

```python
"who are you": None,  # falls through to "what are you"
```

Matching order: exact match first, then longest-substring match (prevents "are you" from matching before "can you write code").

**Design decision:** All identity responses are hardcoded, not LLM-generated. This guarantees consistency — InterGen always identifies itself the same way, regardless of LLM temperature or context.

---

### _try_llm_tools() — The Agentic Loop (lines 427-494)

This method implements the tool-calling → synthesis pipeline:

1. Build messages with adaptive system prompt
2. Stream LLM response with tool schemas
3. If LLM makes a tool call → execute tool
4. Send tool result back to LLM via `continue_after_tool_call()`
5. If synthesis succeeds → return synthesized response
6. If synthesis fails → fall back to template synthesis

**The stream_with_tools constraint** (handled in llm.py): Only `[system, user]` messages are sent to the LLM for tool calling. Conversation history is stripped. This prevents Qwen3.5's "pattern addiction" — copying tool-calling patterns from previous turns.

**Fallback chain:**
```python
if synthesis:
    response_text = synthesis.text
else:
    if collected_text:
        response_text = self._llm._strip_filler("".join(collected_text))
    else:
        response_text = self._synthesize_tool_result(...)
```
Three levels of fallback ensure a response is always produced.

---

### _template_synthesis() (lines 588-644)

Static method that maps query patterns to natural language templates. Returns `None` when no template matches, triggering LLM fallback.

**Single-line templates** (lines 601-609): Simple lookups — "hostname" → "Your hostname is {output}."

**Multi-line templates** (lines 612-643): Parse and summarize system output. `_summarize_disk()` extracts usage percentages from `df -h` output. `_summarize_memory()` extracts total/used/available from `free -h`.

**Design decision:** Templates always include raw data alongside summaries:
```python
return f"{summary}\n\n```\n{out}\n```"
```
This satisfies two needs: quick-glance summary + full data for verification. The user is in control — they can see exactly what the system reported.

---

### _natural_language_to_command() (lines 679-724)

Static method mapping 30+ natural language phrases to shell commands. Used by P1 keyword matching to extract the `run_command` argument without needing LLM inference.

**Design decision:** The map is intentionally conservative — only read-only commands. "disk space" → `df -h`, never `rm` or `dd`. Write/destructive commands require P3 (LLM tool calling) where safety classification applies.

---

### _record() (lines 815-853)

Observability hook called after every successful route. Logs:
- Route source (cache, keyword, semantic, llm_tools, llm_freeform)
- Latency
- Query type (from adaptive classifier)
- Tool usage
- Token counts
- Escalation status

The `query_type` in metadata was added to enable per-category performance analysis — essential for identifying which query types cause the most failures.

---

## Key Design Patterns

1. **Template-first, LLM-fallback:** Every path tries template synthesis before invoking the LLM. This minimizes hallucination risk and latency.

2. **Safety at every layer:** Safety triggers block cache (line 100), safety modifier steers LLM (adaptive prompting), safety classification gates tool execution (tool_registry.py), and the grader catches any safety failures that slip through.

3. **Fail-safe routing:** The priority chain ensures every query gets answered. Even if P1-P3 all miss, P4 catches it. Even if P4 produces garbage, the quality gate retries and can escalate to cloud.

4. **Explicit over implicit:** Memory storage is explicit ("remember that..."). Query classification is keyword-based, not inferred. Identity responses are hardcoded. The user always knows what InterGen is doing and why.
