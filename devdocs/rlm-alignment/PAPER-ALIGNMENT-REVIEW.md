# RLM Implementation Review: Paper Alignment Analysis

**Paper**: "Recursive Language Models" - [arxiv.org/abs/2512.24601](https://arxiv.org/abs/2512.24601)  
**Implementation**: `skills/rlm/` in `pi-rlm` repository  
**Branch**: `feat/codemap-integration`  
**Review Date**: January 21, 2026

---

## 1. Alignment Summary

**Overall Assessment**: ✅ **Strongly Aligned**

The implementation faithfully captures the core RLM architecture from the paper. The key insight—offloading context to a REPL environment while enabling recursive sub-LM calls—is correctly implemented. The implementation extends the paper's concepts with practical enhancements for real-world usage in coding agents.

**Fidelity Score**: 9/10

The 1-point deduction reflects minor deviations that are justified engineering choices rather than conceptual misalignments.

---

## 2. Feature Matrix

| Paper Concept | Implementation | Status | Notes |
|--------------|---------------|--------|-------|
| **Context offloading to REPL** | `init` command loads content into `context['content']` variable | ✅ Full | Context stored as Python variable, accessible via `peek()`, `grep()`, etc. |
| **Recursive sub-LM calls** | `llm_query(prompt)` spawns `pi` subprocess | ✅ Full | Uses `_spawn_sub_agent()` with configurable model |
| **Bounded recursion depth** | `--max-depth N`, `remaining_depth` tracking | ✅ Full | Depth decrements on each spawn; returns error at limit |
| **Persistent REPL state** | `state.pkl` persists between invocations | ✅ Full | Pickle-based state with automatic migration |
| **Code execution feedback** | `exec` command runs arbitrary Python | ✅ Full | Captures stdout/stderr, updates state |
| **Final answer signaling** | `set_final_answer(value)`, `FINAL()` tags | ✅ Full | JSON-serializable values, CLI retrieval |
| **Parallel sub-calls** | `llm_query_batch(prompts, concurrency=5)` | ✅ Full | ThreadPoolExecutor with retry + backoff |
| **Content chunking** | `smart_chunk()` with format detection | ✅ Extended | Exceeds paper with markdown/JSON/code-aware chunking |

---

## 3. Detailed Concept Verification

### 3.1 Recursive Decomposition (Core Paper Concept)

**Paper's Architecture**:
> "A recursive language model is a thin wrapper around an LM that can spawn (recursive) LM calls for intermediate computation... Under the hood, a RLM provides only the query to the LM (which we call the root LM), and allows this LM to interact with an environment, which stores the (potentially huge) context."

**Implementation Analysis**:

```
Location: skills/rlm/scripts/rlm_repl.py

_spawn_sub_agent() (line 1398):
- Spawns full `pi` subprocess with isolated state
- Passes remaining_depth via --append-system-prompt
- Creates sub-session directories: depth-{level}/{query_id}/

llm_query() (line 2082):
- User-facing wrapper that calls _spawn_sub_agent()
- Respects global concurrency semaphore (5 concurrent)
- Handles preserve_recursive_state for debugging
```

**Verification**: ✅ **Aligned**

The architecture matches the paper's Figure 3 (REPL environment diagram). The key elements are present:
- Root LM interacts with context via REPL commands
- Sub-LM calls are isolated and return text responses
- Depth tracking prevents infinite recursion

**Recursion Depth Diagram from SKILL.md**:
```
        Root LM (depth=3)
             │
  ┌──────────┴──────────┐
  │                     │
llm_query(A)        llm_query(B)
Sub-LM (depth=2)    Sub-LM (depth=2)
  │                     │
llm_query(C)        llm_query(D)
Sub-LM (depth=1)    Sub-LM (depth=1)
  │                     │
[ERROR: depth limit]  [ERROR: depth limit]
```

This matches the paper's description: "We only consider a recursive depth of 1" for experiments, but the framework supports configurable depth.

---

### 3.2 Token Efficiency (Handle System)

**Paper's Approach**:
> "Filtering input information using code execution based on model priors... The context window of the root LM is rarely clogged — because it never directly sees the entire context, its input context grows slowly."

**Implementation Analysis**:

```
Location: skills/rlm/scripts/rlm_repl.py (lines 1757-1930)

grep(pattern) → Returns handle stub "$res1: Array(47) [preview...]"
count("$res1") → 47 (no data expansion)
expand("$res1", limit=5) → Materialize only 5 items
filter_handle("$res1", "timeout") → New handle with subset
```

**Handle Workflow** (from implementation):
```python
# grep() returns a stub, not raw data
result = grep("ERROR")           # "$res1: Array(47) [preview...]"
print(f"Found {count(result)} errors")  # Uses handle directly
for item in expand(result, limit=5):    # Materialize only what's needed
    print(item['snippet'])
```

**Verification**: ✅ **Extended Beyond Paper**

The paper describes filtering via regex/code, but doesn't formalize a handle system. The implementation adds:
- Named handle references (`$res1`, `$res2`, etc.)
- Lazy evaluation (data stays in memory, not in LM context)
- Handle operations: `filter_handle()`, `map_field()`, `sum_field()`

This is a **practical extension** that enhances token efficiency.

---

### 3.3 Smart Chunking

**Paper's Approach**:
> "Chunking and recursively sub-calling LMs. RLMs defer essentially unbounded-length reasoning chains to sub-(R)LM calls. The choice of decomposition can greatly affect task performance."

The paper mentions uniform chunking and keyword searches as observed strategies.

**Implementation Analysis**:

```
Location: skills/rlm/scripts/rlm_repl.py

_smart_chunk_impl() (line 1212) - Auto-detects format and routes to:
├── _chunk_markdown() (line 608) - Splits at header boundaries (##, ###)
├── _chunk_text() (line 764) - Splits at paragraph breaks
├── _chunk_code() (line 355) - Splits at function/class boundaries (via codemap)
├── _chunk_json_array() (line 860) - Splits at element boundaries
└── _chunk_json_object() (line 1012) - Splits at key boundaries
```

**Verification**: ✅ **Extended Beyond Paper**

The paper's experiments used basic chunking strategies. The implementation adds:
- Format auto-detection (markdown, code, JSON, text)
- Semantic boundary detection (headers, functions, JSON elements)
- Manifest generation with chunk metadata
- Configurable target/min/max sizes

This is a **significant enhancement** that improves real-world applicability.

---

### 3.4 Answer Finalization

**Paper's Approach**:
> "When it is done, it outputs a final answer with FINAL(…) tags or it can choose to use a string in the code execution environment with FINAL_VAR(…)."

**Implementation Analysis**:

```
Location: skills/rlm/scripts/rlm_repl.py

set_final_answer(value) (line 2169):
- Validates JSON-serializability
- Stores in state["final_answer"] with timestamp
- Retrieved via `get-final-answer` CLI command

has_final_answer() (line 2197):
- Boolean check for external tooling

get_final_answer() (line 2203):
- Returns the stored value
```

**CLI Retrieval**:
```bash
python rlm_repl.py --state <path> get-final-answer
# Returns: {"summary": "...", "count": 42}
```

**Verification**: ✅ **Aligned with Practical Adaptation**

The paper uses `FINAL()` and `FINAL_VAR()` tags parsed from model output. The implementation uses a function-based approach (`set_final_answer()`) that:
- Is more explicit (no parsing ambiguity)
- Enforces JSON-serializability
- Integrates with the REPL's state persistence

This is a **justified deviation** that improves reliability.

---

### 3.5 Batch Execution

**Paper's Approach**:
> "RLMs without asynchronous LM calls are slow. We implemented all sub-LM queries naively as blocking / sequential calls."

The paper acknowledges this limitation and suggests async as future work.

**Implementation Analysis**:

```
Location: skills/rlm/scripts/rlm_repl.py

llm_query_batch() (line 2119):
- Uses ThreadPoolExecutor for parallelism
- Global semaphore limits to 5 concurrent (line 69)
- Exponential backoff retry (max_retries=3)

_llm_query_batch_impl() (line 1530):
- Core implementation with retry logic
- Tracks failures per-index with details
```

**Verification**: ✅ **Extends Paper's Vision**

The paper identified async as a limitation. This implementation:
- Adds concurrent execution (bounded to 5)
- Adds retry with exponential backoff
- Returns (results, failures) tuple for error handling

This directly addresses the paper's acknowledged limitation.

---

## 4. Deviations

| Deviation | Paper | Implementation | Justification |
|-----------|-------|----------------|---------------|
| **Final answer mechanism** | `FINAL()` tags parsed from output | `set_final_answer()` function | More reliable than output parsing; avoids the "brittle" tag detection the paper mentions |
| **Sub-agent spawning** | Single model context | Full `pi` subprocess | Enables full agent capabilities in sub-calls; practical for coding agent integration |
| **Handle system** | Not formalized | `$resN` with lazy expansion | Token efficiency enhancement not in paper |
| **Chunking sophistication** | Basic uniform/keyword | Format-aware semantic chunking | Practical enhancement for real codebases |

All deviations are **justified engineering choices** that improve practical utility without contradicting paper concepts.

---

## 5. Gaps

| Paper Feature | Status | Notes |
|--------------|--------|-------|
| **Deeper recursion (depth > 1)** | ✅ Implemented | Configurable via `--max-depth N` |
| **Async sub-calls** | ✅ Partial | ThreadPoolExecutor provides concurrency, but not fully async |
| **Variable passing to sub-LMs** | ❌ Not implemented | Paper shows storing results in variables; impl passes via prompt text |
| **REPL history visualization** | ❌ Not implemented | Paper references a trajectory visualizer |

**Variable Passing Gap**:
The paper shows examples like:
```python
answer6 = llm_query("Find info in: " + context[chunk6])  # Returns to root's namespace
```

The implementation only returns text responses. Sub-agents cannot directly modify parent namespace variables. This is a minor gap since results can be parsed from returned text.

---

## 6. Extensions Beyond Paper

| Extension | Description | Value |
|-----------|-------------|-------|
| **Format detection** | Auto-detects markdown, code, JSON, text | High - Enables smart chunking |
| **Codemap integration** | Uses AST-based code chunking when available | High - Preserves function boundaries |
| **JSON-aware chunking** | Splits arrays at elements, objects at keys | Medium - Handles config/data files |
| **Manifest generation** | Chunk metadata with boundaries, previews, hints | High - Enables intelligent chunk selection |
| **Query logging** | `queries.jsonl` tracks all sub-agent calls | Medium - Debugging and analysis |
| **State migration** | Auto-upgrades state schema across versions | Medium - Backward compatibility |
| **Handle operations** | `filter_handle()`, `map_field()`, `sum_field()` | High - Rich data manipulation |

---

## 7. Recommendations

### 7.1 Consider Implementing

1. **Variable bridging**: Allow sub-agents to write to named result slots the parent can read
   ```python
   result = llm_query("Analyze this", result_var="analysis_1")
   # analysis_1 now available in namespace
   ```

2. **Trajectory export**: Add `export-trajectory` command for debugging/visualization

3. **Prefix caching hint**: When spawning sub-agents with shared context, consider caching prefixes

### 7.2 Documentation Improvements

1. Add paper citation to SKILL.md header
2. Include comparison table of paper vs implementation features
3. Add "RLM Patterns" section showing grep→chunk→map→reduce workflows

### 7.3 Testing Suggestions

1. Add benchmark comparison test vs paper metrics (OOLONG, BrowseComp+)
2. Test recursive depth > 2 scenarios
3. Profile memory usage with large handle sets

---

## 8. Conclusion

The `pi-rlm` implementation is a **faithful and enhanced version** of the RLM paper concepts. It correctly captures:

- ✅ Context offloading to REPL environment
- ✅ Recursive sub-LM calls with depth bounding
- ✅ Token-efficient context exploration
- ✅ Answer finalization for external retrieval
- ✅ Parallel batch execution (extending paper's limitation)

The implementation extends the paper with practical features (smart chunking, handle system, codemap integration) that make it suitable for real-world coding agent workflows. The deviations from the paper are justified engineering choices that improve reliability and usability.

**Recommendation**: This implementation is ready for integration. The core RLM architecture is solid, and the extensions add genuine value for the pi coding agent use case.

---

*Reviewed by: Paper Alignment Review Agent*  
*Test Status: 260 passed, 8 skipped*
