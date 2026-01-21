# pi-rlm

An RLM (Recursive Language Model) extension for [pi](https://github.com/mariozechner/pi-coding-agent), enabling processing of extremely large context files that exceed typical LLM context windows.

Based on the RLM pattern from [arXiv:2512.24601](https://arxiv.org/abs/2512.24601).

## What is RLM?

The Recursive Language Model pattern breaks down large documents into manageable chunks, processes each with a specialized sub-LLM, then synthesizes results in the main agent. This allows you to analyze textbooks, massive documentation, log dumps, or any context too large to paste into chat.

```
┌─────────────────────────────────────────────────────────────────┐
│                     Pi Main Session (Root LLM)                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
            ┌──────────────┴──────────────┐
            ▼                             ▼
┌───────────────────────┐     ┌───────────────────────────────────┐
│   rlm_repl.py         │     │        rlm-subcall                 │
│   (Persistent REPL)   │     │        (Sub-LLM Agent)             │
│ • Load large context  │     │ • Reads individual chunks         │
│ • Chunk text          │     │ • Extracts relevant info          │
│ • Search/grep         │     │ • Returns structured JSON         │
│ • Accumulate results  │     │                                   │
└───────────────────────┘     └───────────────────────────────────┘
```

## Installation

### Option 1: Symlink (recommended for development)

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/pi-rlm.git ~/projects/pi-rlm

# Symlink the skill
ln -s ~/projects/pi-rlm/skills/rlm ~/skills/rlm

# Symlink the agent
ln -s ~/projects/pi-rlm/agents/rlm-subcall.md ~/.pi/agent/agents/rlm-subcall.md
```

### Option 2: Copy files

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/pi-rlm.git /tmp/pi-rlm

# Copy the skill
cp -r /tmp/pi-rlm/skills/rlm ~/skills/

# Copy the agent
cp /tmp/pi-rlm/agents/rlm-subcall.md ~/.pi/agent/agents/
```

## Usage

Invoke the skill with a context file and query:

```
/skill:rlm context=path/to/large-file.txt query="What patterns appear in this document?"
```

Or start the skill and it will prompt you:

```
/skill:rlm
```

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

## Performance Notes

| File Size | Performance |
|-----------|-------------|
| 1-50MB | ✓ Works well |
| 50-100MB | ⚠ Slower but functional |
| 500MB+ | ✗ Consider splitting first |

## License

MIT
