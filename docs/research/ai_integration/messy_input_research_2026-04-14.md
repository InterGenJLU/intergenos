# InterGen Messy Input Research
**Date:** April 14, 2026
**Purpose:** How real users interact with AI agents — typos, fragments, ambiguity

---

## Sources

### LEA — Sentence Similarity Robustness to Typos
**URL:** https://arxiv.org/html/2307.02912
**Conference:** ACM SIGKDD 2023

**Key findings:**
- Transformer sentence encoders suffer up to 15% accuracy degradation on typos
- Misspelled words split into different sub-word tokens, shifting the entire embedding
- LEA (Lexical-aware Attention) module adds lexical similarity to cross-encoders
- Solution: augment training data with adversarial typo variants

### Making Sentence Embeddings Robust to User-Generated Content
**URL:** https://arxiv.org/html/2403.17220v1

**Key findings:**
- Non-standard words and standard counterparts do NOT have similar embeddings
- Performance drop due to semantic vectors not being robust to user-generated content
- Augmenting fine-tuning data with adversarial variants helps
- Structure-aware classifiers recommended

### Fuzzy Matching 101
**URL:** https://dataladder.com/fuzzy-matching-101/

**Key findings:**
- Levenshtein Distance: counts edits for typo detection
- Jaro-Winkler: better for short identifiers, handles transpositions
- Chain approaches: n-grams → distance metric → business rules
- Mix Levenshtein (misspellings) + Metaphone (sound-alikes)

### Intent Classification 2026 Techniques
**URL:** https://labelyourdata.com/articles/machine-learning/intent-classification

**Key findings:**
- Going from 500 to 5,000 training examples cuts errors from 15% to 2%
- Examples must reflect ACTUAL user language, not developer assumptions
- Hybrid approach: exact keyword → NLP engine (handles typos)
- Train on varied utterances including slang and typos

### Conversational Prompt Rewriting
**URL:** https://arxiv.org/html/2503.16789v1

**Key findings:**
- Proposed rewrites succeed better deeper into conversations
- Rewriter has more information about grounded goals and preferences
- Better chatbot responses from rewritten prompts

### AmbigChat — Clarification for Ambiguous QA
**URL:** https://dl.acm.org/doi/10.1145/3746059.3747686

### Curiosity by Design — LLM Asking Clarification
**URL:** https://arxiv.org/html/2507.21285v1

---

## Applied in InterGen

1. **Layer 0 normalization** — contraction expansion, 30+ typo fixes, fragment expansion
2. **Messy training examples** — added to all intent categories alongside clean examples
3. **Lowered thresholds** — 0.85 (from 0.88/0.90) for messy tolerance in embedding matching
4. **8 messy_input test conversations** — fragments, typos, terse, casual, caps
