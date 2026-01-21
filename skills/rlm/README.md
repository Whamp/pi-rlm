# RLM (Recursive Language Model) Skill

A pi skill for processing extremely large context files that exceed typical LLM context windows (~200k tokens).

## Overview

This skill implements the RLM pattern from [arXiv:2512.24601](https://arxiv.org/abs/2512.24601). The approach breaks down large documents into manageable chunks, processes each with a specialized sub-LLM, then synthesizes results in the main agent.

**Primary use case:** Analyzing textbooks, massive documentation, log dumps, scraped webpages, or any context too large to paste into chat.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Pi Main Session                          │
│                        (Root LLM / Orchestrator)                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
            ┌──────────────┴──────────────┐
            │                             │
            ▼                             ▼
┌───────────────────────┐     ┌───────────────────────────────────┐
│   rlm_repl.py         │     │        rlm-subcall                 │
│   (Persistent REPL)   │     │        (Sub-LLM Agent)             │
├───────────────────────┤     ├───────────────────────────────────┤
│ • Load large context  │     │ • Reads individual chunks         │
│ • Handle-based search │     │ • Extracts relevant info          │
│ • Chunk with hints    │     │ • Returns structured JSON         │
│ • Store state         │     │ • Fast (gemini-3-flash)           │
│ • Accumulate results  │     │                                   │
└───────────────────────┘     └───────────────────────────────────┘
            │                             │
            └──────────────┬──────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   .pi/rlm_state/       │
              │   <session>/           │
              ├────────────────────────┤
              │ • state.pkl            │
              │ • chunks/              │
              │   ├─ manifest.json     │
              │   ├─ chunk_0000.txt    │
              │   └─ ...               │
              └────────────────────────┘
```

## Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `SKILL.md` | `~/skills/rlm/SKILL.md` | Agent instructions and workflow |
| `rlm_repl.py` | `~/skills/rlm/scripts/rlm_repl.py` | Persistent Python REPL for state |
| `rlm-subcall` | `~/.pi/agent/agents/rlm-subcall.md` | Sub-LLM for chunk extraction |

## Usage

Invoke the skill with:
```
/skill:rlm context=path/to/large-file.txt query="What patterns appear in this document?"
```

Or just start with `/skill:rlm` and the agent will prompt for the context file and query.

## Session Structure

Each RLM session creates a timestamped directory:

```
.pi/rlm_state/
└── auth-module-spec-20260120-155234/
    ├── state.pkl           # Persistent REPL state (includes handles)
    └── chunks/
        ├── manifest.json   # Chunk metadata with previews and hints
        ├── chunk_0000.txt
        ├── chunk_0001.txt
        └── ...
```

## Manifest Format

The `manifest.json` provides chunk location data plus content hints:

```json
{
  "session": "auth-module-spec-20260120-155234",
  "context_file": "auth-module-spec.txt",
  "total_chars": 1500000,
  "total_lines": 35420,
  "chunk_size": 200000,
  "overlap": 0,
  "chunk_count": 8,
  "chunks": [
    {
      "id": "chunk_0000",
      "file": "chunk_0000.txt",
      "start_char": 0,
      "end_char": 200000,
      "start_line": 1,
      "end_line": 4523,
      "preview": "# Auth Module Specification\n\nThis document covers...",
      "hints": {
        "section_headers": ["# Auth Module Specification", "## Overview"],
        "likely_code": false,
        "density": "normal"
      }
    }
  ]
}
```

**Hint fields:**
- `section_headers`: Markdown headers found in chunk (up to 5)
- `likely_code`: True if chunk has high code character density
- `has_code_blocks`: True if markdown code fences present
- `likely_json`: True if chunk appears to be JSON
- `density`: "dense", "normal", or "sparse" based on non-empty line ratio

## REPL Commands

```bash
# Initialize with context file
python3 ~/skills/rlm/scripts/rlm_repl.py init path/to/file.txt

# Check status (shows handles count)
python3 ~/skills/rlm/scripts/rlm_repl.py --state .pi/rlm_state/<session>/state.pkl status --show-vars

# Execute code
python3 ~/skills/rlm/scripts/rlm_repl.py --state ... exec -c "print(len(content))"

# Peek at content
python3 ~/skills/rlm/scripts/rlm_repl.py --state ... exec -c "print(peek(0, 3000))"

# Write chunks
python3 ~/skills/rlm/scripts/rlm_repl.py --state ... exec -c "paths = write_chunks('.pi/rlm_state/<session>/chunks')"
```

## Helper Functions

### Content Exploration

| Function | Returns | Description |
|----------|---------|-------------|
| `peek(start, end)` | `str` | View slice of content |
| `grep(pattern, max_matches=20)` | `str` (handle) | Search with context window, returns handle stub |
| `grep_raw(pattern, ...)` | `list[dict]` | Same as grep but returns raw data |
| `chunk_indices(size, overlap)` | `list[tuple]` | Get chunk boundaries |
| `write_chunks(out_dir, size, overlap)` | `list[str]` | Materialize chunks to disk with manifest |
| `add_buffer(text)` | `None` | Accumulate subagent results |

### Handle System

The handle system provides **~80-97% token savings** during exploration. Search results are stored server-side; only compact stubs are returned.

| Function | Returns | Description |
|----------|---------|-------------|
| `handles()` | `str` | List all active handles |
| `last_handle()` | `str` | Get name of most recent handle (for chaining) |
| `expand(handle, limit=10, offset=0)` | `list` | Materialize handle data |
| `count(handle)` | `int` | Count items without expanding |
| `delete_handle(handle)` | `str` | Free memory |
| `filter_handle(handle, pattern)` | `str` (handle) | Filter by regex, return new handle |
| `map_field(handle, field)` | `str` (handle) | Extract single field from each item |
| `sum_field(handle, field)` | `float` | Sum numeric field values |

### Handle Example

```python
# grep() returns a handle stub, not raw data
print(grep("ERROR"))
# Output: "$res1: Array(47) [Line 234: ERROR: connection refused...]"

# Chain with last_handle()
map_field(last_handle(), "line_num")
print(expand(last_handle()))     # [234, 456, 789, ...]

# Or use explicit handle names
print(count("$res1"))            # 47

# Filter server-side
filter_handle("$res1", "timeout")
for item in expand(last_handle(), limit=3):
    print(f"Line {item['line_num']}: {item['snippet'][:60]}")
```

## Performance Notes

| File Size | Performance |
|-----------|-------------|
| 1-50MB | ✓ Works well |
| 50-100MB | ⚠ Slower but functional |
| 500MB+ | ✗ Consider splitting first |

The bottleneck is pickle serialization of the full content on each `exec` call.
