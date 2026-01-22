# RLM Examples

These examples demonstrate the RLM (Recursive Language Model) workflow using the **3-level escalation ladder**.

## The Escalation Ladder

| Level | Tool | Use When | Context Cost |
|-------|------|----------|--------------|
| **1** | REPL (`grep`, `peek`, etc.) | Pattern matching, structure extraction, aggregation | Only `print()` output |
| **2** | `llm_query()` / `llm_query_batch()` | Semantic reasoning, classification, summarization | Only `print()` output |
| **3** | Subagent (`rlm-subcall`) | Final synthesis, complex multi-step reasoning | ~5KB per subagent |

**Default to Level 1.** Only escalate when you need semantic reasoning.

## Running Examples

All examples should be run from the `skills/rlm` directory:

```bash
cd skills/rlm
python3 examples/01_basic_workflow.py
```

## Examples

### 01_basic_workflow.py — Core REPL Workflow

Demonstrates Level 1 operations:
- Initialize a session with content
- Use `grep()` to find patterns  
- Use `expand()` to materialize results
- Write chunks to disk
- Set and retrieve final answer

### 02_smart_chunking.py — Content-Aware Chunking

Shows how `smart_chunk()` handles different formats:
- **Markdown**: Splits at header boundaries
- **JSON arrays**: Splits at element boundaries
- **JSON objects**: Splits by top-level keys
- **Plain text**: Splits at paragraph breaks

### 03_handle_system.py — Token-Efficient Exploration

Demonstrates the handle system for exploring large content without loading it all:
- `grep()` returns handles, not raw data
- `count()` counts without expanding
- `expand()` materializes only what you need
- `filter_handle()` for server-side filtering
- `map_field()` for field extraction

### 04_depth_configuration.py — Recursion Depth Settings

Shows depth configuration (mostly an implementation detail):
- `--max-depth N` for controlling recursion limit
- `--preserve-recursive-state` for debugging
- Depth affects `llm_query()` behavior

### 05_finalization.py — Answer Finalization

Demonstrates the finalization signal:
- `set_final_answer()` for marking results
- `has_final_answer()` and `get_final_answer()` helpers
- `get-final-answer` CLI command for external retrieval

### 06_llm_query.py — LLM Queries (Level 2) ⭐

Demonstrates semantic reasoning with LLM calls:
- `llm_query()` for single classification/interpretation
- `llm_query_batch()` for parallel processing
- `add_buffer()` for accumulating results
- `export-buffers` CLI for exporting to files
- Complete workflow: grep → llm_query → buffer → synthesize

**Requires the `pi` CLI to be available for LLM calls.**

### 07_subagent_synthesis.py — Subagent Integration (Level 3) ⭐

Demonstrates the subagent pattern for complex synthesis:
- Chunking content for parallel subagent processing
- Subagent invocation pattern (conceptual)
- Processing subagent results
- Exporting buffers for final synthesis
- Setting structured final answers

## Key Concepts

### Token Efficiency

The handle system (`grep()`, `expand()`, etc.) lets you explore large content without loading everything into context. Results are stored in the REPL and materialized only when needed.

### Content-Aware Chunking

`smart_chunk()` automatically detects content format and splits at natural boundaries:
- Markdown: Headers
- Code: Functions/classes (if codemap available)
- JSON: Array elements or object keys
- Text: Paragraphs

### The Buffer Pattern

Use `add_buffer()` to accumulate findings during analysis:
```python
for item in grep_raw('ERROR'):
    result = llm_query(f"Classify: {item['snippet']}")
    add_buffer(result)

# Later: export for synthesis
python3 rlm_repl.py --state <path> export-buffers findings.txt
```

### Decision Tree

```
Is this a structural query? (find X, count Y, parse JSON)
    → YES → Level 1: REPL
    → NO ↓

Do I need LLM judgment? (classify, summarize, interpret)
    → YES → Level 2: llm_query() in REPL
    → NO → Level 1: REPL

Am I synthesizing final results?
    → YES → Level 3: Subagent (protects main context)
```
