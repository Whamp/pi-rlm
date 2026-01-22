# pi-rlm Development History

This document summarizes the development journey of pi-rlm, reconstructed from ~4.9 million characters of pi session history across 19 sessions.

## Project Genesis

**pi-rlm** adapts the [Recursive Language Model (RLM)](https://arxiv.org/abs/2512.24601) paradigm for use within the [Pi coding agent](https://github.com/mariozechner/pi-coding-agent). The goal: enable Pi to process and reason over extremely large contexts (codebases, documents, session histories) that exceed any single LLM's context window.

### Core Insight from the Paper

The RLM paper demonstrated that providing an LLM with:
1. A **Python REPL** with a `context` variable containing target content
2. An **`llm_query()` function** for recursive sub-model calls
3. **Iterative querying** until `FINAL()` is produced

...enables effective processing of virtually unlimited context sizes through recursive chunking, delegation, and aggregation.

---

## Architectural Decisions

### The "REPL + Pickle" Philosophy

**Key User Concern Raised:** Early proposals for a "Librarian" architecture that would lazy-load context were rejected because they "discard the benefit of having the python repl and pickle hold the whole context."

**Resolution:** The project adopted an "Augmented RLM" strategy—integrating codemap as helper functions *into* the existing REPL environment rather than replacing it. The agent remains the driver; the REPL remains the engine.

### Three Options for Recursion

| Option | Description | Selected? |
|--------|-------------|-----------|
| A: Raw Model | Sub-LM as direct API call, no tools | ❌ |
| B: Full Pi Agent | `llm_query()` spawns full pi subprocess with its own state | ✅ |
| C: Hybrid | Lightweight for simple, full agent for complex | ❌ |

**Decision:** Option B was chosen for supporting 10M-100M+ token contexts where full agent capabilities are needed in sub-calls.

### 8 Codemap Integration Proposals Evaluated

| Proposal | Status | Integration Path |
|----------|--------|------------------|
| The Librarian | Modified | Implemented as codemap bindings in REPL |
| Intelligent Routing | Adapted | Emergent behavior via `get_deps()` |
| Semantic Segmentation | Adopted | Implemented as `smart_chunk()` |
| Verification | Adopted | Workflow pattern using `find_refs()` |
| Vision (Graphs) | Deferred | `get_call_graph()` helper planned |
| The Zipper | Optional | Advanced optimization for manifest |
| Blast Radius | Workflow | Implementable via `git diff | get_deps` |
| Learning Codebase | Deferred | Read-only analysis prioritized |

---

## Implementation Phases

### Phase 1: Core `llm_query()` Infrastructure ✅
**Completed:** 2026-01-21

- `_spawn_sub_agent()` spawns full pi subprocess for sub-queries
- `_parse_pi_json_output()` extracts final text from `--mode json` output
- `_log_query()` appends to `llm_queries.jsonl` with timestamps
- Global concurrency semaphore limiting concurrent spawns to 5
- State version migration v2 → v3 with depth tracking fields

**Tests:** 22 unit tests passing

### Phase 2: Depth Tracking & Recursive State ✅
**Completed:** 2026-01-21

- `--max-depth N` argument (default: 3 per paper spec)
- `--preserve-recursive-state` flag for debugging
- Recursive directory structure: `depth-N/q_uuid/`
- Depth injection via system prompt: `RLM_REMAINING_DEPTH=N`
- Depth-0 fast fail with `depth_exceeded` status

**Tests:** 17 unit tests passing

### Phase 3: `llm_query_batch()` Implementation ✅
**Completed:** 2026-01-21

- Concurrent execution via ThreadPoolExecutor
- Shared global semaphore (5 max concurrent)
- Retry with exponential backoff (1s, 2s, 4s delays)
- Returns `(results, failures)` tuple
- Batch logging with `batch_id` for correlation

**Tests:** 21 unit tests passing

### Phase 4: Semantic Chunking - Markdown/Text ✅
**Completed:** 2026-01-21

- `smart_chunk()` with format auto-detection
- `_chunk_markdown()` splits at h2/h3 headers
- `_chunk_text()` paragraph-based fallback
- Enhanced manifest with `format`, `chunking_method`, `split_reason`, `boundaries`

**Format Detection:**
- `.md/.markdown/.mdx` → markdown
- `.py/.ts/.js/.rs/.go/...` → code
- `.json` → json
- 5+ headers in content → markdown (fallback)
- Everything else → text

**Tests:** 30 unit tests passing

### Phase 5: Finalization Signal ✅
**Completed:** 2026-01-21

- `set_final_answer(value)` - marks JSON-serializable value as final
- `has_final_answer()` - check if answer set
- `get_final_answer()` - retrieve the value
- `get-final-answer` CLI command for external retrieval
- Updated `status` to show final answer info

**Design Decision:** Used explicit function instead of paper's `FINAL()` tags—more reliable than output parsing, avoids the "brittle" tag detection the paper acknowledges.

**Tests:** 21 unit tests passing

### Phase 6: Semantic Chunking - Code (Codemap) ✅
**Completed:** 2026-01-21

- `_detect_codemap()` - finds codemap binary (env var → PATH → npx → None)
- `_chunk_code()` - splits at function/class boundaries via codemap
- `_extract_symbol_boundaries()` - parses codemap output
- Graceful fallback to text chunking when codemap unavailable

**Problem Encountered:** Codemap had Node.js version mismatch (`NODE_MODULE_VERSION 141 vs 127`). 

**Solution:** Implemented graceful fallback—codemap failure results in text-based chunking with `codemap_available: true, codemap_used: false` in manifest.

**Tests:** 30 unit tests passing

### Phase 7: Semantic Chunking - JSON ✅
**Completed:** 2026-01-21

- `_chunk_json_array()` - splits arrays into element groups
- `_chunk_json_object()` - splits by top-level keys
- Each chunk is re-serialized as valid JSON
- Manifest includes `element_range` for arrays, `key_range`/`keys` for objects

**Tests:** 46 unit tests passing

### Phase 8: Documentation & Integration Testing ✅
**Completed:** 2026-01-21

- Updated `SKILL.md` with comprehensive usage guide
- Created `TESTING-STRATEGY.md` with benchmark plans
- Added experience tests (needle-in-haystack, codebase analysis)
- Paper alignment review document (9/10 fidelity score)

**Tests:** 30 integration tests, 260 total tests passing

---

## Problems Encountered & Solutions

### 1. Handle System UX Friction
**Problem:** `grep()` returns full handle string (`$res1: Array(20) [...]`) but `count()`/`expand()` expected just handle name (`$res1`).

**Solution:** Added `_parse_handle()` helper that accepts both formats, enabling natural chaining.

### 2. Edit Tool Failures
**Problem:** The `edit` tool frequently failed with "Could not find exact text" due to HTML entities and whitespace differences.

**Solution:** Used bash `head`/`tail`/heredoc workarounds, or Python scripts via bash to perform text replacement.

### 3. Rate Limiting (429 Errors)
**Problem:** Cloud Code Assist API rate limits during development.

**Solution:** Switched between models (claude-opus → gemini-3-flash → gemini-3-pro → glm-4.7).

### 4. Pickle Serialization Bottleneck
**Problem:** Pickle serialization of entire context on every exec call. 500MB+ files problematic.

**Deferred Solution:** Store raw content in plain file or mmap, pickle only metadata—would make pickle O(1) vs O(n).

### 5. YAML Frontmatter Parsing
**Problem:** Special characters (`>`, `:`) in SKILL.md description caused YAML parser failures.

**Solution:** Quoted the description field properly.

---

## What Extends Beyond the Paper

| Feature | Paper | pi-rlm |
|---------|-------|--------|
| Format-aware chunking | Basic character splits | Markdown/Code/JSON-aware splitting |
| Handle system | Filtering via regex/code | Named handles ($res1), lazy evaluation, filter/map/sum operations |
| Answer finalization | `FINAL()` tags (brittle) | `set_final_answer()` function (reliable) |
| Batch execution | Sequential noted as limitation | ThreadPoolExecutor with retry |
| Chunk manifest | Not mentioned | Preview, hints, boundaries, line numbers |

---

## Final Statistics

- **rlm_repl.py:** ~1800 lines
- **Total tests:** 260 passed, 8 skipped
- **Sessions analyzed:** 19
- **Development time:** ~2 days intensive work
- **Paper alignment score:** 9/10 (deviations are justified engineering choices)

---

## Meta: This Document

This development history was created *using* pi-rlm to process its own session files—a test of the project's effectiveness. The 4.7MB of session history was chunked into 79 pieces, processed in parallel by rlm-subcall subagents, and synthesized into this narrative.
