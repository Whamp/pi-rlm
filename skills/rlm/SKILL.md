---
name: rlm
description: Process files too large to fit in context (>100KB, >2000 lines). MUST USE when reading large logs, documentation, transcripts, codebases, or data dumps. Chunks content, delegates to subagents, synthesizes results. Triggers: large file, big document, massive log, full codebase, entire repo, long transcript, huge context, won't fit in context, too long to read, context window exceeded.
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
   ```bash
   python3 ~/skills/rlm/scripts/rlm_repl.py --state <session_state_path> exec <<'PY'
   session_dir = state_path.parent  # available in env
   chunks_dir = str(session_dir / 'chunks')
   paths = write_chunks(chunks_dir, size=200000, overlap=0)
   print(f"Wrote {len(paths)} chunks")
   print(paths[:5])
   PY
   ```

   After chunking, **read the manifest** to understand chunk coverage:
   ```bash
   cat <session_dir>/chunks/manifest.json
   ```
   
   The manifest now includes:
   - `preview`: First few lines of each chunk
   - `hints`: Content analysis (e.g., `likely_code`, `section_headers`, `density`)
   
   Use hints to skip irrelevant chunks or craft better prompts.

4. **Subcall loop — use parallel mode**
   
   **Always use parallel invocation** for speed. The `rlm-subcall` agent has `full-output: true` configured, so parallel mode returns complete results (not truncated previews).

   ```json
   {
     "tasks": [
       {"agent": "rlm-subcall", "task": "Query: <user query>\nChunk file: <absolute path to chunk_0000.txt>"},
       {"agent": "rlm-subcall", "task": "Query: <user query>\nChunk file: <absolute path to chunk_0001.txt>"},
       ...
     ]
   }
   ```

   The subagents will read their entire chunk (they exist to burn context) and return structured JSON.

   Optionally accumulate results using `add_buffer()`:
   ```bash
   python3 ~/skills/rlm/scripts/rlm_repl.py --state <session_state_path> exec -c "add_buffer('<subagent result>')"
   ```

5. **Synthesis**
   - Once enough evidence is collected, synthesize the final answer in the main conversation.
   - Use the manifest to cite specific locations (line numbers, character positions).
   - Optionally invoke rlm-subcall once more to merge collected buffers into a coherent draft.

## REPL Helpers Reference

### Content Exploration
| Function | Returns | Description |
|----------|---------|-------------|
| `peek(start, end)` | `str` | View a slice of raw content |
| `grep(pattern, max_matches=20, window=120)` | `str` (handle) | Regex search, returns handle stub |
| `grep_raw(pattern, ...)` | `list[dict]` | Same as grep but returns raw data |
| `chunk_indices(size, overlap)` | `list[tuple]` | Get chunk boundary positions |
| `write_chunks(out_dir, size, overlap)` | `list[str]` | Write chunks to disk with manifest |
| `add_buffer(text)` | `None` | Accumulate text for later synthesis |

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

### Handle Workflow Example
```python
# Search returns handle, not data
print(grep("ERROR"))             # "$res1: Array(47) [preview...]"

# Chain with last_handle()
map_field(last_handle(), "line_num")
print(expand(last_handle()))     # [10, 45, 89, ...]

# Or use handle names directly
result = grep("ERROR")           # "$res1: Array(47) [...]"
print(count("$res1"))            # 47

# Filter server-side
filter_handle("$res1", "timeout")
print(f"Timeout errors: {count(last_handle())}")
```

## Guardrails

- **Do not paste large raw chunks into the main chat context.**
- Use handles to avoid context bloat during exploration.
- Use the REPL to locate exact excerpts; quote only what you need.
- Subagents cannot spawn other subagents. Any orchestration stays in the main conversation.
- Keep scratch/state files under `.pi/rlm_state/`.
- Always use absolute paths when invoking subagents.
