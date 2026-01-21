# Plan: RLM Context Compression via Codemap Integration ("The Zipper")

## 1. Executive Summary

This plan proposes integrating `codemap`'s structural analysis capabilities into the `pi-rlm` workflow to solve the "context flooding" problem. By enriching RLM's raw text chunks with syntactic metadata (symbols, density, types) and using `codemap`'s logic to compress outputs, we can transform `pi-rlm` from a brute-force text scanner into a structure-aware semantic engine.

**Core Concept**: Instead of sub-agents returning massive raw text dumps, they return structured *references* to symbols. The Main Agent then uses `codemap` to "hydrate" these references at the appropriate detail level (Outline, Minimal, Standard, Full) based on its remaining token budget.

## 2. Problem Statement

Current `pi-rlm` limitations:
1.  **Blind Chunking**: Chunks are cut by character count, often severing functions or classes.
2.  **Opaque Manifests**: The Main Agent sees `chunk_001.txt (lines 1-500)` but has no idea it contains the critical `AuthenticationService` class.
3.  **Synthesis Flooding**: Sub-agents return verbose findings. Aggregating 10 sub-agent responses often blows the Main Agent's context window, forcing truncation.
4.  **Redundant Reading**: Sub-agents burn tokens reading boilerplate that `codemap` could have summarized cost-effectively.

## 3. Solution Architecture

We will implement a "Zipper" architecture that interlocks `pi-rlm`'s semantic processing with `codemap`'s syntactic structure.

### 3.1 Component View

```
┌─────────────────┐       1. Analyze        ┌──────────────┐
│ Context File    │ ──────────────────────► │   codemap    │
└────────┬────────┘                         └──────┬───────┘
         │                                         │ 2. JSON Map
         │                                         ▼
┌────────▼────────┐       3. Enrich         ┌──────────────┐
│ rlm_repl.py     │ ◄────────────────────── │ Symbol Graph │
└────────┬────────┘                         └──────────────┘
         │ 4. Generate
         ▼
┌─────────────────┐       5. Guide          ┌──────────────┐
│ manifest.json   │ ──────────────────────► │ Main Agent   │
│ (Chunks + Syms) │                         └──────┬───────┘
└─────────────────┘                                │
                                                   ▼
                                            ┌──────────────┐
                                            │ Sub-Agents   │
                                            └──────────────┘
```

### 3.2 Key Innovations

#### A. The Enriched Manifest
The `manifest.json` will no longer just be a list of file offsets. It will include a "mini-map" for each chunk.

**Current:**
```json
{ "id": "chunk_0", "start_line": 1, "end_line": 500 }
```

**Proposed:**
```json
{
  "id": "chunk_0",
  "start_line": 1,
  "end_line": 500,
  "symbols": [
    { "name": "AuthService", "kind": "class", "line": 12, "exported": true },
    { "name": "login", "kind": "method", "line": 45, "parent": "AuthService" }
  ],
  "imports": ["better-sqlite3", "zod"]
}
```

#### B. Token-Budgeted Synthesis (The "Zipper" Effect)
When the Main Agent synthesizes findings, it needs to fit the answer into a strict budget. We adopt `codemap`'s "Detail Level" paradigm for the synthesis prompt.

Instead of pasting raw sub-agent outputs, the Main Agent constructs a **Synthetic Source Map**:
1.  **High Relevance**: Full detail (signatures + comments + implementation notes from sub-agent).
2.  **Medium Relevance**: Compact detail (signatures + summary).
3.  **Low Relevance**: Outline only (symbol names).

This "zips" the context: expanding what matters, compressing what doesn't.

## 4. Implementation Plan

### Phase 1: Infrastructure Bridge
Enable `rlm_repl.py` to invoke `codemap`.

1.  **Requirement**: `codemap` must be available in the environment.
    - *Action*: Update `pi-rlm` setup to check for `codemap` binary or node script.
    - *Fallback*: If `codemap` is missing, fall back to current behavior (no symbols).
2.  **Wrapper**: Add `_run_codemap(path)` to `rlm_repl.py`.
    - Arguments: `--output json --no-stats --no-refs`.
    - Returns: Parsed JSON object containing file structure.

### Phase 2: Manifest Enrichment
Upgrade `write_chunks` in `rlm_repl.py` to map symbols to chunks.

1.  **Analysis Step**: Before chunking, run `codemap` on the source file.
2.  **Symbol Distribution**:
    - Iterate through `codemap.files[0].symbols`.
    - For each symbol, calculate which chunk index it falls into based on `symbol.startLine`.
    - Handle edge cases: Symbols spanning chunk boundaries (assign to start chunk or duplicate).
3.  **Manifest Output**: Write the enriched JSON structure.

### Phase 3: Agent Protocol Updates
Teach the agents to use the new map.

1.  **Main Agent Skill (`SKILL.md`)**:
    - *Instruction*: "Read `manifest.json`. Use the `symbols` list to route questions. If looking for 'login logic', check the chunk containing `AuthService.login` first."
    - *Optimization*: "Do not ask sub-agents to summarize file structure; rely on the manifest for that."

2.  **Sub-Agent (`rlm-subcall.md`)**:
    - *Context*: Pass the `chunk_symbols` list into the sub-agent's prompt context.
    - *Instruction*: "You are provided a list of symbols in this chunk. Refer to them by name. If a function is too long to quote, just cite its name and `codemap` signature."

### Phase 4: Synthesis Tooling (Optional Advanced Step)
Create a REPL helper to render compressed views.

1.  **Helper**: `render_view(symbol_names, level='standard')`
    - Main Agent calls: `render_view(['AuthService'], level='compact')`
    - REPL uses `codemap` logic (ported or invoked) to return the compact representation of that symbol.
    - *Benefit*: Main Agent doesn't need to hallucinate the summary; it gets the ground-truth signature.

## 5. Feasibility & Risk

### Feasibility
- **High**: `codemap` outputs machine-readable JSON.
- **High**: Python `json` parsing is trivial.
- **Medium**: Line-number alignment. `codemap` uses 1-based indexing; Python slicing is 0-based. Needs careful off-by-one testing.

### Risks
- **Performance**: Running `codemap` on a 50MB log file might be slow or crash if the parser hangs.
    - *Mitigation*: Set a timeout. If `codemap` fails, proceed with raw text chunks.
- **Language Support**: `codemap` supports TS/JS/C++/Rust. If user uploads Python/Go, `codemap` only gives outlines.
    - *Mitigation*: The system gracefully degrades to "Outline" mode (file structure only) for unsupported languages.
- **Context Bloat**: Adding full symbol tables to `manifest.json` might make the manifest itself too large for the Main Agent.
    - *Mitigation*: Truncate symbol lists in the manifest preview. Provide a REPL tool `search_manifest(pattern)` instead of reading the whole JSON file.

## 6. Success Metrics
1.  **Synthesis Quality**: The final answer contains precise signatures and locations, not vague descriptions.
2.  **Token Efficiency**: Main Agent uses 30% fewer tokens to understand file structure.
3.  **Routing Accuracy**: "Find `processPayment`" queries route to the exact chunk immediately, 0 misses.
