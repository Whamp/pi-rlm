---
name: rlm
description: Run a Recursive Language Model-style loop for long-context tasks. Uses a persistent local Python REPL and an rlm-subcall subagent as the sub-LLM (llm_query).
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

2. **Materialize chunks as files** (so subagents can read them)
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
   The manifest contains start/end character and line positions for each chunk.

3. **Subcall loop — use parallel mode**
   
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

4. **Synthesis**
   - Once enough evidence is collected, synthesize the final answer in the main conversation.
   - Use the manifest to cite specific locations (line numbers, character positions).
   - Optionally invoke rlm-subcall once more to merge collected buffers into a coherent draft.

## Guardrails

- **Do not paste large raw chunks into the main chat context.**
- Use the REPL to locate exact excerpts; quote only what you need.
- Subagents cannot spawn other subagents. Any orchestration stays in the main conversation.
- Keep scratch/state files under `.pi/rlm_state/`.
- Always use absolute paths when invoking subagents.
