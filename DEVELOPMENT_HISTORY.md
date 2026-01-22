# pi-rlm Development History

> This document was generated using pi-rlm itself to analyze 23MB of development session logs spanning 28 sessions over 2 days.

## Executive Summary

**pi-rlm** evolved from an exploration of codemap integration strategies into a focused implementation aligned with the RLM paper (arXiv:2512.24601). The project pivoted from ambitious code-graph navigation ideas to a practical, paper-aligned implementation of recursive LLM processing within a persistent Python REPL.

---

## Timeline Overview

| Date | Sessions | Focus |
|------|----------|-------|
| 2026-01-21 | 1-15 | Codemap exploration → Paper study → 8-phase implementation |
| 2026-01-22 | 16-28 | Testing, polish, documentation, merge to main |

---

## Phase 0: Codemap Integration Exploration

**Initial Goal**: Integrate [codemap](https://github.com/user/codemap) capabilities into pi-rlm for code-aware processing of large repositories.

### Paths Considered

Eight distinct strategies were documented in `devdocs/codemap/plans/`:

| Plan | Description | Status |
|------|-------------|--------|
| **Intelligent Routing** | Graph-based navigation following code dependencies | 📋 Deferred |
| **Semantic Segmentation** | Symbol-aware chunking using AST boundaries | ✅ Partially implemented |
| Blast Radius Analysis | Diff-focused processing for change impact | 📋 Deferred |
| Learning Codebase | Progressive knowledge building across sessions | 📋 Deferred |
| Vision Integration | Using vision models for diagram understanding | 📋 Deferred |
| Librarian Architecture | Index-based retrieval system | 📋 Deferred |
| Context Compression | Compressed intermediate representations | 📋 Deferred |
| Verification/Quality | Chunk quality validation | 📋 Deferred |

**Initial Recommendation**: Prioritize Intelligent Routing with Semantic Segmentation as a prerequisite data layer.

**Housekeeping**: "Structural Chunking" was identified as a duplicate of Semantic Segmentation and merged.

### Pivot Decision

After studying the RLM paper, the focus shifted from codemap integration to **paper alignment**. The codemap strategies were deferred to a future phase, with only the core semantic segmentation concept retained for smart chunking.

---

## Phase 1: RLM Paper Study

**Paper**: [arXiv:2512.24601 - Recursive Language Models](https://arxiv.org/abs/2512.24601)

### Key Paper Concepts Identified

1. **REPL Environment** - Full content held in `context` variable, never in LLM context
2. **llm_query()** - Function to spawn recursive sub-LLM calls (~500K char windows)
3. **Chunking Strategies** - Overlap-based splitting for continuity
4. **FINAL/FINAL_VAR** - Output format for returning computed results
5. **Depth Limits** - Prevent runaway recursion (paper notes Qwen3-Coder needed explicit warnings)

### Gap Analysis

Existing pi-rlm had a basic REPL but lacked:
- `llm_query()` for recursive calls
- Depth tracking/limits
- Finalization mechanism
- Smart (content-aware) chunking
- Batch processing for parallel analysis

---

## Phase 2: Architecture Decisions

### Critical Decision: llm_query() Implementation

Three options were evaluated:

| Option | Approach | Pros | Cons | Decision |
|--------|----------|------|------|----------|
| **A: pi CLI subprocess** | Shell out to `pi --mode json` | Reuses infrastructure, simple | 100-200ms overhead | ✅ **CHOSEN** |
| B: Direct API call | Call LLM API from Python | Faster, lower overhead | Duplicates logic, API key mgmt | ❌ Discarded |
| C: Unix socket IPC | Connect to running pi | Low latency, shared context | Pi doesn't expose this yet | ❌ Discarded |

**Rationale**: Option A was simplest. Process overhead is negligible compared to LLM API latency (1-10s). Batch processing with `llm_query_batch()` amortizes startup costs across concurrent calls.

### Recursion Strategy

| Option | Approach | Decision |
|--------|----------|----------|
| **Explicit depth** | Top-level sets `--max-depth`, propagated via env vars | ✅ **CHOSEN** |
| Implicit depth | Auto-detect from call stack | ❌ Discarded (hard to debug) |

**Default**: Max depth of 3 levels. Each recursive call gets isolated state in its own session directory.

---

## Phase 3: Implementation (8 Sub-Phases)

Implementation was structured into 8 phases, each scoped for ~100k-125k tokens of agent work:

### Phase Workflow
```
1. Read prior phase diff (git diff HEAD~1)
2. Implement features
3. Run validation steps (unit + integration tests)
4. Append to progress-notes.txt
5. Commit with structured message
```

### Implementation Phases

| Phase | Feature | Key Files Modified |
|-------|---------|-------------------|
| 1 | Core `llm_query()` with subprocess spawning | `rlm_repl.py` |
| 2 | Depth tracking with `remaining_depth` propagation | `rlm_repl.py` |
| 3 | Batch processing with `llm_query_batch()` | `rlm_repl.py` |
| 4 | Smart chunking - Markdown (header-aware) | `rlm_repl.py` |
| 5 | Smart chunking - Semantic analysis features | `rlm_repl.py` |
| 6 | Smart chunking - Code (codemap integration) | `rlm_repl.py` |
| 7 | Smart chunking - JSON (array/object boundaries) | `rlm_repl.py` |
| 8 | Finalization with `set_final_answer()` | `rlm_repl.py`, `SKILL.md` |

---

## Features Implemented

### Core RLM Capabilities
- **`llm_query(prompt)`** - Spawn pi subprocess for single LLM call
- **`llm_query_batch(prompts, concurrency=5)`** - Parallel execution with retry logic
- **Depth tracking** - `--max-depth` init arg, `remaining_depth` state propagation
- **Finalization** - `set_final_answer()`, `get-final-answer` CLI command

### Smart Chunking System
- **Format detection** - Extension-based, with content fallback
- **Markdown chunking** - Splits on level 2-3 headers, keeps sections together
- **Code chunking** - Uses codemap for symbol boundaries when available
- **JSON chunking** - Splits arrays/objects at element boundaries
- **Text fallback** - Paragraph-based splitting

### Handle System
- **Lazy stubs** - `$res1: Array(42)` representation
- **Expansion** - `expand(handle, limit=10)`
- **Operations** - `count()`, `filter_handle()`, `map_field()`, `sum_field()`

### Infrastructure
- **Session management** - Timestamped directories, `state.pkl` persistence
- **Manifest generation** - Previews, hints, line numbers per chunk
- **Logging** - JSONL query logs for debugging

---

## Paths Discarded

### Discarded During Implementation
| Path | Reason |
|------|--------|
| Direct API calls (Option B) | Too much code duplication |
| Unix socket IPC (Option C) | Not exposed by pi yet |
| Token/cost tracking | Deemed not important for this phase |

### Deferred to Future Work
| Path | Notes |
|------|-------|
| Intelligent Routing | Full codemap graph navigation - planned for future phase |
| Blast Radius Analysis | Diff-focused optimization |
| Learning Codebase | Cross-session knowledge persistence |
| Vision Integration | Diagram understanding |
| Librarian Architecture | Index-based retrieval |
| Context Compression | Advanced optimization |

### Merged/Eliminated
- **Structural Chunking** → merged into Semantic Segmentation (was duplicate)

---

## Post-Implementation

### Testing
- **260 unit tests** across 8 test files (phases 1-7 + integration)
- **6 experience tests** covering real-world workflows
- All tests passing at merge

### Documentation Created
- `SKILL.md` - Primary skill documentation (~300 lines)
- `HANDOFF-PAPER-REVIEW.md` - Paper alignment verification guide
- `TESTING-STRATEGY.md` - Test approach documentation
- 7 example Python scripts

### Bugs Fixed Post-Implementation
- Handle UX: `count()`/`expand()` now accept full handle string (not just `$res1`)
- Session loading errors after branch merge

### Remaining Polish Identified
- Session management friction
- Chunk boundary quality inspection tooling
- Handle ergonomics review (`$res1` syntax)

---

## Lessons Learned

1. **Pivot early** - Codemap integration was ambitious; paper alignment provided clearer scope
2. **Phase sizing matters** - ~100k token phases allowed complete implementation + testing per session
3. **Option A often wins** - Simple subprocess approach beat complex IPC alternatives
4. **Handle system complexity** - Lazy evaluation helpful but UX needs iteration
5. **Meta-testing works** - Using pi-rlm to analyze its own development proved the tool's effectiveness

---

## Current State

The project successfully implements the core RLM paper concepts:
- ✅ Persistent REPL with full content in memory
- ✅ Recursive LLM queries with depth limits
- ✅ Smart content-aware chunking
- ✅ Parallel batch processing
- ✅ Finalization mechanism

Future work remains for the more ambitious codemap integration strategies that were originally explored.

---

*Document generated: 2026-01-22*
*Source: 28 pi sessions, 23.7M characters analyzed using RLM skill*
