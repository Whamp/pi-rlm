# Pi Adaptation Plan

Adapting the Claude Code RLM implementation for the pi coding agent.

## Overview

The RLM (Recursive Language Model) pattern from [arXiv:2512.24601](https://arxiv.org/abs/2512.24601):

1. **Root LLM** — pi main session orchestrates the overall task
2. **Sub-LLM** (`rlm-subcall` subagent) — processes individual chunks via `subagent` tool
3. **Persistent Python REPL** (`rlm_repl.py`) — external environment for state/chunking

**Primary use case:** Analyzing extremely large files (textbooks, massive docs, log dumps) that far exceed typical LLM context windows (~200k tokens).

## Target Structure

```
~/skills/rlm/
├── README.md                   # Overview, architecture, usage docs
├── SKILL.md                    # Skill definition + workflow procedure
└── scripts/
    └── rlm_repl.py             # Persistent Python REPL

~/dotfiles/pi/agent/agents/
└── rlm-subcall.md              # Sub-LLM agent definition
```

## Key Differences: Claude Code vs Pi

| Concept | Claude Code | Pi Equivalent |
|---------|-------------|---------------|
| Project context | `CLAUDE.md` | Not needed — skill is self-contained |
| Skills location | `.claude/skills/*/SKILL.md` | `~/skills/**/SKILL.md` |
| Subagents location | `.claude/agents/*.md` | `~/dotfiles/pi/agent/agents/*.md` |
| Subagent invocation | "Use the rlm-subcall subagent..." | `{ "agent": "rlm-subcall", "task": "..." }` via subagent tool |
| Skill command | `/rlm` | `/skill:rlm` (auto-registered) |
| Model format | `model: haiku` | `model: google-antigravity/gemini-3-flash` |
| State directory | `.claude/rlm_state/` | `.pi/rlm_state/<session>/` |

## File Purposes

| File | Purpose |
|------|---------|
| `README.md` | Human documentation — what is RLM, architecture, usage |
| `SKILL.md` | Agent instructions — step-by-step procedure the LLM follows |
| `rlm_repl.py` | Tool — persistent state management, chunking, helpers |
| `rlm-subcall.md` | Agent — chunk-level extraction, returns structured JSON |

## Session Path Format

```
.pi/rlm_state/
└── <context-name>-YYYYMMDD-HHMMSS/
    ├── state.pkl                # persistent REPL state
    └── chunks/                  # materialized chunk files
        ├── manifest.json        # chunk metadata
        ├── chunk_0000.txt
        ├── chunk_0001.txt
        └── ...
```

**Name derivation from context filename:**
- Extract basename, remove extension
- Sanitize (lowercase, replace non-alphanumeric with hyphens, truncate to ~30 chars)
- Append timestamp
- Example: `auth-module-spec.txt` → `auth-module-spec-20260120-155234`

## Changes to Original Implementation

### 1. SKILL.md

- Update all paths from `.claude/` to `.pi/`
- Update script path to `~/skills/rlm/scripts/rlm_repl.py`
- Update subagent invocation to use pi's subagent tool syntax:
  ```json
  { "agent": "rlm-subcall", "task": "Query: <user query>\nChunk file: <path>" }
  ```
- Instruct agent to read `manifest.json` after chunking

### 2. rlm_repl.py

- Remove `DEFAULT_STATE_PATH` constant — session path is now dynamic
- `init` command:
  - Auto-generates session directory from context filename + timestamp
  - Creates `.pi/rlm_state/<session>/` structure
  - Outputs session path for subsequent commands
- `write_chunks` enhancement:
  - Emits `manifest.json` alongside chunk files
  - Manifest includes: session name, context file, chunk metadata (id, file, start_char, end_char, start_line, end_line)
- All commands accept `--state` to specify session state path

### 3. rlm-subcall.md

```yaml
---
name: rlm-subcall
description: Sub-LLM for RLM chunk extraction. Given a chunk file and query, extracts relevant info as JSON.
tools: read
model: google-antigravity/gemini-3-flash
---
```

**Tools decision:** `read` only. The agent's task is pure extraction — no side effects needed. Keeping it minimal reduces prompt overhead and keeps sub-LLM calls fast.

**No required-skills needed.** The agent has a deliberately narrow task:
- Read the chunk file it's given
- Extract info relevant to the query
- Return structured JSON

## Chunk Manifest Format

`write_chunks` emits `manifest.json`:

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
    },
    {
      "id": "chunk_0001",
      "file": "chunk_0001.txt",
      "start_char": 200000,
      "end_char": 400000,
      "start_line": 4524,
      "end_line": 8901
    }
  ]
}
```

**Use cases:**
- Main agent asks "which chunk has line 5000?" — lookup instead of guessing
- After subagent reports evidence at a position, main agent knows which chunk
- Debugging: understand chunk coverage

## Python REPL Analysis

### Strengths

- **Zero dependencies** — pure stdlib Python, works anywhere
- **Persistent state** — survives across tool calls via pickle
- **Tailored helpers** — `peek`, `grep`, `chunk_indices`, `write_chunks`, `add_buffer`
- **Arbitrary code execution** — agent can write custom logic via `exec()`
- **Buffer system** — accumulate subagent results for synthesis
- **Output truncation** — prevents flooding agent context

### Weaknesses (Accepted)

- **Single context only** — user can concat files externally if needed
- **Character-based chunking** — semantic chunking deferred (character-based is standard RLM approach)
- **No session listing** — `ls .pi/rlm_state/` works
- **No cleanup command** — `rm -rf` works

### Performance Characteristics

**Bottleneck:** Pickle serialization of entire content on every `exec` call.

| File Size | Performance |
|-----------|-------------|
| 1-50MB (typical textbook) | ✓ Fine |
| 50-100MB (large reference) | ⚠ Slower but works |
| 500MB+ | ✗ Problematic |

**Why Python over Rust/Go?**

The `exec()` feature allows agents to write arbitrary Python logic — this flexibility is more valuable than raw speed. The bottleneck is architectural (pickle serialization), not language speed.

## Future Enhancements (Deferred)

### Content-Separation Optimization

For very large files (500MB+), separate content storage from pickle:
- Store raw content in plain file or mmap
- Pickle only stores `{context_path, metadata, buffers, globals}` (tiny)
- Load content on-demand
- Would make pickle load/save O(1) instead of O(n)

### Other Ideas

- Session listing command
- Auto-clean old sessions
- Multi-file context loading
- Semantic chunking (by headings, functions, etc.)
- Parallel chunk processing with multiple subagents

## Implementation Checklist

- [ ] Create `~/skills/rlm/README.md`
- [ ] Create `~/skills/rlm/SKILL.md`
- [ ] Create `~/skills/rlm/scripts/rlm_repl.py` with:
  - [ ] Dynamic session path from context filename
  - [ ] Chunk manifest generation
- [ ] Create `~/dotfiles/pi/agent/agents/rlm-subcall.md`
- [ ] Test with sample large context file
- [ ] Add `.pi/rlm_state/` to `.gitignore` template

## Reference Files

- Original implementation: `/home/will/tools/claude_code_RLM/`
- Pi subagent extension: `~/.pi/agent/extensions/subagent/`
- Pi skills docs: `/home/will/.local/share/mise/installs/node/22.21.1/lib/node_modules/@mariozechner/pi-coding-agent/docs/skills.md`
- Pi agent template: `~/.pi/agent/agents/TEMPLATE.md`
