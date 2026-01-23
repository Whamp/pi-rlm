# pi-rlm

An RLM (Recursive Language Model) skill for [pi](https://github.com/mariozechner/pi-coding-agent), enabling processing of extremely large context files that exceed typical LLM context windows.

Based on the RLM pattern from [arXiv:2512.24601](https://arxiv.org/abs/2512.24601).

Originally a fork of this claude code minimal implementation [claude_code_RLM](https://github.com/brainqub3/claude_code_RLM)

## Prerequisites

This skill requires the **subagent tool** for pi. The subagent tool is included with pi as a bundled extension.

> **Note:** The subagent tool spawns separate pi processes to handle delegated tasks. Each sub-agent runs in an isolated context, which is essential for the RLM pattern where chunk processing must not pollute the main context.

## What is RLM?

The Recursive Language Model pattern breaks down large documents into manageable chunks, processes each with a specialized sub-LLM, then synthesizes results. This allows you to analyze textbooks, massive documentation, log dumps, or any context too large to paste into chat.

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Pi Main Session (Root LLM)                           │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
          ┌───────────────────────┴───────────────────────┐
          ▼                                               ▼
┌─────────────────────────┐                 ┌─────────────────────────────────┐
│   Agent-Driven Mode     │                 │      Autonomous Mode            │
│   (/skill:rlm)          │                 │      (rlm-autonomous)           │
│                         │                 │                                 │
│ • Agent drives REPL     │                 │ • Subagent drives REPL          │
│ • Sees each iteration   │                 │ • Runs complete loop internally │
│ • Can adapt approach    │                 │ • Returns only final answer     │
│ • Uses main context     │                 │ • Isolates main context         │
└───────────┬─────────────┘                 └───────────────┬─────────────────┘
            │                                               │
            ▼                                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          rlm_repl.py (Persistent REPL)                      │
│  • Load large context  • Search/grep  • Chunk text  • Accumulate results   │
└─────────────────────────────────────────┬───────────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Sub-LLM Queries (llm_query / rlm-subcall)                │
│           • Semantic analysis  • ~500K char capacity  • Parallel batching  │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Installation

### Option 1: Symlink (recommended for development)

```bash
# Clone the repo
git clone https://github.com/Whamp/pi-rlm.git ~/projects/pi-rlm

# Symlink the skill (includes the read_chunk extension)
ln -s ~/projects/pi-rlm/skills/rlm ~/skills/rlm

# Symlink the agents
ln -s ~/projects/pi-rlm/agents/rlm-subcall.md ~/.pi/agent/agents/rlm-subcall.md
ln -s ~/projects/pi-rlm/agents/rlm-autonomous.md ~/.pi/agent/agents/rlm-autonomous.md
```

### Option 2: Copy files

```bash
# Clone the repo
git clone https://github.com/Whamp/pi-rlm.git /tmp/pi-rlm

# Copy the skill (includes the read_chunk extension)
cp -r /tmp/pi-rlm/skills/rlm ~/skills/

# Copy the agents
cp /tmp/pi-rlm/agents/rlm-subcall.md ~/.pi/agent/agents/
cp /tmp/pi-rlm/agents/rlm-autonomous.md ~/.pi/agent/agents/
```

> **Note:** The `read_chunk` tool (used by the rlm-subcall agent to read large chunks without truncation) is bundled with the skill at `skills/rlm/extensions/rlm_tools.ts`. The agent automatically loads this extension when spawned—no manual extension installation required.

## Usage Modes

pi-rlm provides two modes for different context sizes:

### Agent-Driven Mode (Medium-Large Files)

For files where you want the main agent to steer the analysis:

```
/skill:rlm context=path/to/large-file.txt query="What patterns appear in this document?"
```

The main agent drives the REPL, sees intermediate results, and can adapt its approach. Best for files up to ~10MB where interactive exploration adds value.

### Autonomous Mode (Massive Files)

For very large files where you want complete context isolation:

```json
{
  "agent": "rlm-autonomous",
  "task": "File: /path/to/huge-log.txt\nQuery: Find all security errors and classify by severity"
}
```

The subagent handles the entire analysis loop internally. The main agent only sees the final answer. Best for files >10MB or when you need to analyze many large files in one session.

| Mode | Context Cost | Agent Control | Best For |
|------|--------------|---------------|----------|
| Agent-driven (`/skill:rlm`) | Proportional to iterations | Full steering | <10MB, interactive exploration |
| Autonomous (`rlm-autonomous`) | Fixed (~task + answer) | None during analysis | >10MB, batch processing |

## How It Works

1. **Initialize**: Load the large context file into a persistent Python REPL
2. **Scout**: Preview the beginning and end of the document
3. **Chunk**: Split the content into manageable pieces (default: 200k chars)
4. **Extract**: Delegate each chunk to the `rlm-subcall` subagent for analysis
5. **Synthesize**: Combine findings into a final answer

### Session Structure

Each RLM session creates a timestamped directory:

```
.pi/rlm_state/
└── my-document-20260120-155234/
    ├── state.pkl           # Persistent REPL state
    └── chunks/
        ├── manifest.json   # Chunk metadata (positions, line numbers)
        ├── chunk_0000.txt
        ├── chunk_0001.txt
        └── ...
```

### REPL Helpers

The persistent REPL provides these functions:

| Function | Description |
|----------|-------------|
| `peek(start, end)` | View a slice of content |
| `grep(pattern, max_matches=20)` | Search with context window |
| `chunk_indices(size, overlap)` | Get chunk boundaries |
| `write_chunks(out_dir, size, overlap)` | Materialize chunks to disk |
| `add_buffer(text)` | Accumulate subagent results |

## Configuration

### Sub-LLM Model

The default sub-LLM uses `google-antigravity/gemini-3-flash`. To change it, edit `agents/rlm-subcall.md`:

```yaml
model: anthropic/claude-sonnet-4-20250514  # or your preferred model
```

### Chunk Size

Adjust chunk size in your `/skill:rlm` invocation or when calling `write_chunks()`:

```python
write_chunks(chunks_dir, size=100000, overlap=5000)  # 100k chars with 5k overlap
```

## Development

This extension was created and improved across multiple pi sessions:
- [Initial implementation](https://buildwithpi.ai/session?73eb4c3795064fe93b5c651dd931535a)
- [Handle system & manifest hints](https://buildwithpi.ai/session/#f74ebcfe6673e3a748c44de1565c0ecd)
- Raw sessions: `sessions/` directory

## License

MIT
