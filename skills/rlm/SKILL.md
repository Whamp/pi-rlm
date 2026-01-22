---
name: rlm
description: Process files too large to fit in context (>100KB, >2000 lines). MUST USE when reading large logs, documentation, transcripts, codebases, or data dumps. Chunks content, delegates to subagents, synthesizes results. Triggers - large file, big document, massive log, full codebase, entire repo, long transcript, huge context, won't fit in context, too long to read, context window exceeded.
---

# rlm (Recursive Language Model workflow)

Use this Skill when:
- The user provides (or references) a very large context file (docs, logs, transcripts, scraped webpages) that won't fit comfortably in chat context.
- You need to iteratively inspect, search, chunk, and extract information from that context.
- You can delegate chunk-level analysis to a subagent.

## Mental model

- Main pi conversation = the root LM.
- Persistent Python REPL (`rlm_repl.py`) = the external environment.
- Subagent `rlm-subcall` = the sub-LM used like `llm_query`.

## Core Features (RLM Paper Alignment)

This implementation aligns with the [RLM paper](https://arxiv.org/abs/2512.24601) patterns:

| Feature | Function | Description |
|---------|----------|-------------|
| **Inline LLM queries** | `llm_query(prompt)` | Sub-LLM calls from within Python code |
| **Batch execution** | `llm_query_batch(prompts)` | Parallel sub-LLM invocation (max 5 concurrent) |
| **Recursive depth** | `--max-depth N` | Sub-LLMs can spawn their own sub-LLMs |
| **Smart chunking** | `smart_chunk(out_dir)` | Content-aware splitting (markdown/code/JSON) |
| **Answer finalization** | `set_final_answer(value)` | Mark results for external retrieval |

## How to run

### Inputs

This Skill reads `$ARGUMENTS`. Accept these patterns:
- `context=<path>` (required): path to the file containing the large context.
- `query=<question>` (required): what the user wants.
- Optional: `chunk_chars=<int>` (default ~200000) and `overlap_chars=<int>` (default 0).

If the user didn't supply arguments, ask for:
1) the context file path, and
2) the query.

### Step-by-step procedure

1. **Initialize the REPL state**
   ```bash
   python3 ~/skills/rlm/scripts/rlm_repl.py init <context_path>
   ```
   The output will show the session path (e.g., `.pi/rlm_state/myfile-20260120-155234/state.pkl`).
   Store this path mentally — use it for all subsequent `--state` arguments.

   **Available init options:**
   ```bash
   python3 ~/skills/rlm/scripts/rlm_repl.py init <path> \
       --max-depth 5 \                    # Set recursion limit (default: 3)
       --preserve-recursive-state         # Keep sub-session directories for debugging
   ```

   Check status:
   ```bash
   python3 ~/skills/rlm/scripts/rlm_repl.py --state <session_state_path> status
   ```

2. **Scout the content (optional but recommended)**
   
   Use the handle-based search to explore without flooding your context:
   ```bash
   python3 ~/skills/rlm/scripts/rlm_repl.py --state <session_state_path> exec -c "
   # grep() returns a handle stub, not raw data
   result = grep('ERROR')
   print(result)  # e.g., '\$res1: Array(47) [preview...]'
   
   # Inspect without expanding
   print(f'Found {count(\"\$res1\")} matches')
   
   # Expand only what you need
   for item in expand('\$res1', limit=5):
       print(f\"Line {item['line_num']}: {item['match']}\")
   "
   ```

3. **Materialize chunks as files** (so subagents can read them)

   **Option A: Basic chunking (character-based)**
   ```bash
   python3 ~/skills/rlm/scripts/rlm_repl.py --state <session_state_path> exec <<'PY'
   session_dir = state_path.parent  # available in env
   chunks_dir = str(session_dir / 'chunks')
   paths = write_chunks(chunks_dir, size=200000, overlap=0)
   print(f"Wrote {len(paths)} chunks")
   print(paths[:5])
   PY
   ```

   **Option B: Smart chunking (content-aware) ⭐ RECOMMENDED**
   ```bash
   python3 ~/skills/rlm/scripts/rlm_repl.py --state <session_state_path> exec <<'PY'
   session_dir = state_path.parent
   paths = smart_chunk(
       str(session_dir / 'chunks'),
       target_size=200000,    # Target chars per chunk (soft limit)
       min_size=50000,        # Minimum chunk size
       max_size=400000,       # Maximum chunk size (hard limit)
   )
   print(f"Created {len(paths)} chunks")
   PY
   ```

   Smart chunking auto-detects format and splits at natural boundaries:
   - **Markdown**: Splits at header boundaries (## and ###)
   - **Code**: Splits at function/class boundaries (requires codemap)
   - **JSON**: Splits arrays into element groups, objects by keys
   - **Text**: Splits at paragraph breaks

   After chunking, **read the manifest** to understand chunk coverage:
   ```bash
   cat <session_dir>/chunks/manifest.json
   ```
   
   The manifest includes:
   - `format`: Detected content type (markdown, code, json, text)
   - `chunking_method`: Method used (smart_markdown, smart_code, smart_json, smart_text)
   - `preview`: First few lines of each chunk
   - `hints`: Content analysis (e.g., `likely_code`, `section_headers`, `density`)
   - `boundaries`: Header/symbol boundaries for each chunk
   
   Use hints to skip irrelevant chunks or craft better prompts.

4. **Query sub-LLMs** (multiple options)

   **Option A: Use subagent delegation (parallel mode)**
   
   Use `rlm-subcall` agent with parallel invocation for speed:

   ```json
   {
     "tasks": [
       {"agent": "rlm-subcall", "task": "Query: <user query>\nChunk file: <absolute path to chunk_0000.txt>"},
       {"agent": "rlm-subcall", "task": "Query: <user query>\nChunk file: <absolute path to chunk_0001.txt>"},
       ...
     ]
   }
   ```

   **Option B: Use inline `llm_query()` (single query)**
   ```bash
   python3 ~/skills/rlm/scripts/rlm_repl.py --state <session_state_path> exec -c "
   chunk_path = session_dir / 'chunks' / 'chunk_0000.txt'
   result = llm_query(f'Summarize: {chunk_path.read_text()[:50000]}')
   print(result)
   add_buffer(result)
   "
   ```

   **Option C: Use `llm_query_batch()` (parallel queries)**
   ```bash
   python3 ~/skills/rlm/scripts/rlm_repl.py --state <session_state_path> exec <<'PY'
   # Read all chunks
   chunk_dir = session_dir / 'chunks'
   chunk_files = sorted(chunk_dir.glob('chunk_*.txt'))
   
   # Build prompts
   prompts = []
   for chunk_file in chunk_files:
       content = chunk_file.read_text()[:50000]
       prompts.append(f"Find all TODOs in this code:\n{content}")
   
   # Run in parallel (max 5 concurrent)
   results, failures = llm_query_batch(
       prompts,
       concurrency=5,    # Max concurrent queries
       max_retries=3,    # Retry failures with exponential backoff
   )
   
   print(f"Got {len(results)} results, {len(failures)} failures")
   for i, result in enumerate(results):
       if "[ERROR:" not in result:
           add_buffer(f"Chunk {i}: {result}")
   PY
   ```

5. **Set final answer** (for external retrieval)
   ```bash
   python3 ~/skills/rlm/scripts/rlm_repl.py --state <session_state_path> exec -c "
   # Compile results
   final_result = {
       'summary': 'Found 15 issues across 4 files',
       'issues': [...]  # Must be JSON-serializable
   }
   set_final_answer(final_result)
   "

   # Retrieve from CLI
   python3 ~/skills/rlm/scripts/rlm_repl.py --state <session_state_path> get-final-answer
   ```

6. **Synthesis**
   - Once enough evidence is collected, synthesize the final answer in the main conversation.
   - Use the manifest to cite specific locations (line numbers, character positions).
   - Optionally invoke rlm-subcall once more to merge collected buffers into a coherent draft.

## Recursive Depth Diagram

```
                    Root LM (depth=3)
                         │
        ┌────────────────┴────────────────┐
        │                                 │
   llm_query(A)                    llm_query(B)
   Sub-LM (depth=2)                Sub-LM (depth=2)
        │                                 │
   llm_query(C)                    llm_query(D)
   Sub-LM (depth=1)                Sub-LM (depth=1)
        │                                 │
   llm_query(E)                    llm_query(F)
   [ERROR: depth limit]            [ERROR: depth limit]
```

- Each level decrements `remaining_depth` by 1
- At depth 0, queries return error without spawning
- Use `--max-depth N` at init to adjust the limit
- Use `--preserve-recursive-state` to keep sub-session directories for debugging

## REPL Helpers Reference

### Content Exploration
| Function | Returns | Description |
|----------|---------|-------------|
| `peek(start, end)` | `str` | View a slice of raw content |
| `grep(pattern, max_matches=20, window=120)` | `str` (handle) | Regex search, returns handle stub |
| `grep_raw(pattern, ...)` | `list[dict]` | Same as grep but returns raw data |
| `chunk_indices(size, overlap)` | `list[tuple]` | Get chunk boundary positions |
| `write_chunks(out_dir, size, overlap)` | `list[str]` | Write chunks to disk with manifest |
| `smart_chunk(out_dir, target, min, max)` | `list[str]` | Content-aware chunking ⭐ |
| `add_buffer(text)` | `None` | Accumulate text for later synthesis |

### LLM Query Functions
| Function | Returns | Description |
|----------|---------|-------------|
| `llm_query(prompt, cleanup=True)` | `str` | Single sub-LLM query |
| `llm_query_batch(prompts, concurrency=5, max_retries=3)` | `(list, dict)` | Parallel queries with retry |

### Answer Finalization
| Function | Returns | Description |
|----------|---------|-------------|
| `set_final_answer(value)` | `None` | Mark JSON-serializable value as final |
| `has_final_answer()` | `bool` | Check if final answer is set |
| `get_final_answer()` | `Any` | Retrieve the final answer value |

### Handle System (Token-Efficient)
| Function | Returns | Description |
|----------|---------|-------------|
| `handles()` | `str` | List all active handles |
| `last_handle()` | `str` | Get name of most recent handle (for chaining) |
| `expand(handle, limit=10, offset=0)` | `list` | Materialize handle data |
| `count(handle)` | `int` | Count items without expanding |
| `delete_handle(handle)` | `str` | Free memory |
| `filter_handle(handle, pattern_or_fn)` | `str` (handle) | Filter and return new handle |
| `map_field(handle, field)` | `str` (handle) | Extract field from each item |
| `sum_field(handle, field)` | `float` | Sum numeric field values |

**Note**: All handle functions accept both handle names (`$res1`) and full stubs (`$res1: Array(47) [...]`).

### Handle Workflow Example
```python
# Search returns handle stub
result = grep("ERROR")           # "$res1: Array(47) [preview...]"

# Use result directly - no need for last_handle()!
print(f"Found {count(result)} errors")  # 47
for item in expand(result, limit=5):
    print(item['snippet'])

# Or use handle names directly
print(count("$res1"))            # 47

# Filter and chain
filtered = filter_handle(result, "timeout")
print(f"Timeout errors: {count(filtered)}")

# Map to extract specific fields
line_nums = map_field(result, "line_num")
print(expand(line_nums))         # [10, 45, 89, ...]
```

## CLI Commands Reference

```bash
# Initialize a session
python3 rlm_repl.py init <context_path> [--max-depth N] [--preserve-recursive-state]

# Check session status
python3 rlm_repl.py --state <path> status

# Execute Python code
python3 rlm_repl.py --state <path> exec -c "code..."
python3 rlm_repl.py --state <path> exec < script.py

# Reset session (clear buffers, handles)
python3 rlm_repl.py --state <path> reset

# Export accumulated buffers
python3 rlm_repl.py --state <path> export-buffers

# Get final answer as JSON
python3 rlm_repl.py --state <path> get-final-answer
```

## Guardrails

- **Do not paste large raw chunks into the main chat context.**
- Use handles to avoid context bloat during exploration.
- Use the REPL to locate exact excerpts; quote only what you need.
- Subagents cannot spawn other subagents. Any orchestration stays in the main conversation.
- Keep scratch/state files under `.pi/rlm_state/`.
- Always use absolute paths when invoking subagents.
- `llm_query_batch()` is limited to 5 concurrent queries (global semaphore).
- `set_final_answer()` only accepts JSON-serializable values.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `RLM_STATE_DIR` | Override default state directory (`.pi/rlm_state`) |
| `RLM_REMAINING_DEPTH` | Set by parent LLM for sub-agents |
| `RLM_CODEMAP_PATH` | Explicit path to codemap binary for code chunking |
