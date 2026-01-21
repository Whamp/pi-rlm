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
│ • Chunk text          │     │ • Extracts relevant info          │
│ • Grep/search         │     │ • Returns structured JSON         │
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
    ├── state.pkl           # Persistent REPL state
    └── chunks/
        ├── manifest.json   # Chunk metadata
        ├── chunk_0000.txt
        ├── chunk_0001.txt
        └── ...
```

## Manifest Format

The `manifest.json` provides chunk location data for precise navigation:

```json
{
  "session": "auth-module-spec-20260120-155234",
  "context_file": "auth-module-spec.txt",
  "total_chars": 1500000,
  "chunk_size": 200000,
  "overlap": 0,
  "chunks": [
    {
      "id": "chunk_0000",
      "file": "chunk_0000.txt",
      "start_char": 0,
      "end_char": 200000,
      "start_line": 1,
      "end_line": 4523
    }
  ]
}
```

## REPL Commands

```bash
# Initialize with context file
python3 ~/skills/rlm/scripts/rlm_repl.py init path/to/file.txt

# Check status
python3 ~/skills/rlm/scripts/rlm_repl.py --state .pi/rlm_state/<session>/state.pkl status

# Execute code
python3 ~/skills/rlm/scripts/rlm_repl.py --state ... exec -c "print(len(content))"

# Peek at content
python3 ~/skills/rlm/scripts/rlm_repl.py --state ... exec -c "print(peek(0, 3000))"

# Write chunks
python3 ~/skills/rlm/scripts/rlm_repl.py --state ... exec -c "paths = write_chunks('.pi/rlm_state/<session>/chunks')"
```

## Helper Functions

Available in the REPL environment:

| Function | Description |
|----------|-------------|
| `peek(start, end)` | View slice of content |
| `grep(pattern, max_matches=20)` | Search with context window |
| `chunk_indices(size, overlap)` | Get chunk boundaries |
| `write_chunks(out_dir, size, overlap)` | Materialize chunks to disk |
| `add_buffer(text)` | Accumulate subagent results |

## Performance Notes

| File Size | Performance |
|-----------|-------------|
| 1-50MB | ✓ Works well |
| 50-100MB | ⚠ Slower but functional |
| 500MB+ | ✗ Consider splitting first |

The bottleneck is pickle serialization of the full content on each `exec` call.
