# InterGen Testing Methodology Research
**Date:** April 14, 2026
**Purpose:** Prior art on behavioral test suite design for AI agents

---

## Sources

### Anthropic — Demystifying Evals for AI Agents
**URL:** https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

**Key findings:**
- Three grader types: code-based (deterministic), model-based (rubric), human (gold standard)
- Grade OUTPUTS not PATHS — don't test "did it call run_command with hostname", test "did response contain the hostname"
- Path-based tests are brittle; output-based tests are robust
- Capability evals start at low pass rates, graduate to regression suite as they saturate
- Balance positive cases (should do X) and negative cases (should NOT do Y)
- Partial credit for multi-component tasks (2/3 correct = MIXED not FAIL)
- pass@k (one success in k tries) vs pass^k (all k succeed) — choose based on product needs
- Environment isolation: clean state per trial
- Regularly read actual transcripts to verify graders work correctly

### Google — Adversarial Testing for Generative AI
**URL:** https://developers.google.com/machine-learning/guides/adv-testing

**Key findings:**
- Adversarial datasets should NOT reflect real-world distribution — bias toward edge cases
- Explicitly adversarial: policy-violating language, tricks
- Implicitly adversarial: innocuous queries on sensitive topics
- Vary: query length, vocabulary breadth, formulation types (questions, direct, indirect)
- Cover: different topics, sensitive characteristics, global contexts
- Creative phrasing > obvious toxicity for effective testing

### AgentHarm Benchmark (ICLR 2025)
**URL:** https://proceedings.iclr.cc/paper_files/paper/2025/file/c493d23af93118975cdbc32cbe7323f5-Paper-Conference.pdf

**Key findings:**
- Red-teaming evaluation for adversarial/manipulative interaction patterns
- Out-of-Context attacks: irrelevant utterances
- Out-of-Scope attacks: exceed agent capabilities
- Prompt Injection attacks: override internal instructions

### CASE-Bench — Context-Aware Safety
**URL:** https://hasp-lab.github.io/pubs/sun2025case.pdf

### RefusalBench — AI Refusal Behavior
**URL:** (emergentmind.com/topics/refusalbench)
- Red-teaming, adversarial generation, multi-step moderation, fine-grained annotation

### Chatbot Testing Checklist 2025
**URL:** https://www.alphabin.co/blog/chatbot-testing-checklist
- 3-sigma edge cases provide ~99% confidence
- Test data should reflect diverse user personas, behaviors, demographics, goals
- Testing is continuous, not one-time

### AgentChangeBench
**URL:** https://arxiv.org/html/2510.18170
- Multi-dimensional evaluation for goal-shift robustness

---

## Recommended Test Taxonomy for InterGen

| Category | What It Tests | Target Count |
|----------|--------------|-------------|
| Happy path | Clean queries work | 15 |
| Lexical variation | Same intent, different words | 15 |
| Typos + fragments | Messy input tolerance | 10 |
| Verbose | Overly wordy queries | 5 |
| Indirect | Intent without action words | 8 |
| Wrong tool bait | Sounds like one tool, needs another | 8 |
| Refusals | Should decline gracefully | 8 |
| Adversarial/injection | Prompt injection, jailbreak | 10 |
| Multi-turn | Follow-ups and context | 8 |
| Ambiguous | Multiple valid interpretations | 5 |
| Compound false positives | "and" that isn't compound | 5 |
| Emotional/frustrated | Caps, profanity, urgency | 5 |
| Self-awareness | Identity, capabilities, limitations | 5 |
| Conversational | Social, non-task | 5 |
| Boundary testing | Max length, empty, unicode | 5 |
| **TOTAL** | | **~120** |

## Key Design Principles

1. **30% happy path, 70% weird stuff** — adversarial datasets should be biased toward failure modes
2. **Grade outputs, not paths** — test what the user sees, not internal tool routing
3. **Positive AND negative assertions** — "should contain hostname" AND "should NOT contain error"
4. **Partial credit** — MIXED grade for partially correct multi-part responses
5. **Real user language** — examples from actual user behavior, not developer imagination
6. **Continuous** — integrated into development loop, not a one-time pass
