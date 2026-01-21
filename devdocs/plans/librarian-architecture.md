# Plan: The Librarian Architecture (Codemap Integration for pi-rlm)

**Status:** Draft
**Target Project:** pi-rlm
**Dependency:** Codemap (Node.js tool)

## Executive Summary

This plan details the implementation of **"The Librarian"** pattern for `pi-rlm`. By integrating `codemap` as a structural backend, we transform `pi-rlm` from a purely brute-force textual analyzer into a graph-aware system that can:
1.  **Persist insights** (annotations) across sessions.
2.  **Navigate structure** before reading text (saving massive context).
3.  **Lazily load** content only when necessary.

## 1. Architecture Overview

```mermaid
graph TD
    User[User / Root LLM] -->|Query| REPL[rlm_repl.py]
    REPL -->|Read Structure| CM_CLI[Codemap CLI]
    REPL -->|Read Annotations| CM_DB[Codemap Cache/DB]
    REPL -->|Delegates| SA[Subagent (rlm-subcall)]
    SA -->|Reads| Text[Source Code]
    SA -->|Writes| Anno[Annotation]
    Anno -->|Persists to| CM_DB
```

## 2. Phase 1: The Semantic Annotation Loop (Write-Path)

**Goal:** Allow ephemeral sub-agents to leave permanent notes for future agents.

### 2.1. Mechanism
When an `rlm-subcall` agent burns context to understand a complex chunk, it should not just return the answer to the user; it should "tag" the code with its findings.

### 2.2. Implementation Details

1.  **Tool Availability**:
    *   Ensure `codemap` is available in the `rlm-subcall` environment.
    *   Add `codemap` to the `rlm-subcall` agent definition (tools list).

2.  **Prompt Engineering (`rlm-subcall.md`)**:
    *   Update system prompt: "If you analyze a complex function/class, use `codemap annotate` to leave a summary for future agents."
    *   Constraint: "Annotations must be high-level summaries (e.g., 'Handles OAuth2 flow'), not trivial ('This is a function')."

3.  **Command Execution**:
    *   Agent calls: `codemap annotate src/auth.ts:validateToken:function "Validates JWT, returns null on expiry. Uses RS256."`

4.  **Handling Orphans**:
    *   `codemap` handles moving annotations if code shifts, but if files are renamed, annotations might be orphaned.
    *   The REPL should occasionally run `codemap cache` to report orphan stats.

## 3. Phase 2: The "Stub" State (Read-Path)

**Goal:** Enable `pi-rlm` to load a "skeleton" of the repo (10k tokens) instead of the full text (1M tokens), navigating to specific parts on demand.

### 3.1. New REPL Strategy: `CodemapStrategy`

Extend `rlm_repl.py` to support a new initialization mode.

*   **Current**: `python rlm_repl.py init file.txt` (Text Mode)
*   **New**: `python rlm_repl.py init --mode=codemap ./src` (Map Mode)

### 3.2. REPL Features (Map Mode)

The persistent Python REPL will wrap `codemap` CLI commands to expose Pythonic navigation:

| Python Function | Underlying Command | Description |
|-----------------|--------------------|-------------|
| `map.ls(pattern)` | `codemap "pattern" --detail outline` | List files/symbols matching pattern. |
| `map.inspect(symbol)` | `codemap find-refs symbol` + extract def | Show signature, docstring, and **annotations**. |
| `map.deps(file)` | `codemap deps file` | Show dependency tree (what else do I need?). |
| `map.read(symbol)` | `read file:line-range` | **Materialize text**: Actually read the code into context. |

### 3.3. Lazy-Loading Workflow

1.  **Init**: Load `codemap --detail outline` into Python state (very small).
2.  **Explore**:
    *   User: "How does billing work?"
    *   Agent: `map.ls("*billing*")` -> finds `src/billing.ts`.
    *   Agent: `map.inspect("src/billing.ts:process")` -> sees signature + annotation "Handles Stripe webhooks".
3.  **Target**:
    *   Agent decides `src/billing.ts` is relevant.
    *   Agent: `map.read("src/billing.ts")` -> Loads text buffer.
4.  **Chunk & Subcall**:
    *   Agent runs standard RLM chunking *only on the loaded buffer*.

## 4. Implementation Plan

### Step 1: Proof of Concept (Manual)
- [ ] Install `codemap` in `pi-rlm` environment.
- [ ] Manually run `codemap annotate` on a file.
- [ ] Verify `codemap --budget ...` output includes the annotation.

### Step 2: Update `rlm-subcall` Agent
- [ ] Edit `agents/rlm-subcall.md` in `pi-rlm`.
- [ ] Add `bash` tool (or specific wrapper) to allow running `codemap`.
- [ ] Update prompt instructions.

### Step 3: Enhance `rlm_repl.py`
- [ ] Add `CodemapWrapper` class in Python.
- [ ] Implement `init --mode=codemap`.
- [ ] Implement `map` object with methods `ls`, `inspect`, `read`.
- [ ] Add `codemap` binary path configuration.

### Step 4: Verification
- [ ] Run a "Librarian" session:
    1.  Start clean.
    2.  Agent 1 reads code, adds annotation.
    3.  Restart session (clear memory).
    4.  Agent 2 reads map, sees annotation, answers question *without* reading code text.

## 5. Potential Challenges

*   **Latency**: `codemap` is fast, but repeated CLI calls in a loop might add overhead.
    *   *Mitigation*: Batch queries or keep `codemap`'s SQLite DB open in Python directly (using `sqlite3` stdlib) instead of shelling out.
*   **Context Sync**: If `pi-rlm` edits files, `codemap` cache needs refresh.
    *   *Mitigation*: Run `codemap index` after edits.

## 6. Value Proposition

*   **Memory**: The repo "remembers" things agents figured out previously.
*   **Scalability**: Navigating a 1GB repo becomes possible by looking at the map (1MB) first.
*   **Cost**: Drastically reduces tokens by avoiding "read all files" scouting.

## 7. Introspective Analysis: Alignment with Long-Context Goals

**Goal:** Extend the functional context window of LLMs to perform effective high-quality long-context work.

### How this plan advances the goal

1.  **Functional Extension via O(1) Access**:
    By using `codemap` as a structural index, we shift `pi-rlm` from linear $O(N)$ text scanning to $O(1)$ indexed retrieval. The "Functional Context Window" effectively becomes the size of the *index*, not the size of the *text*. An agent can "know" a 1GB repo exists and access any part of it without ever loading more than 100kb into RAM/context. This is the ultimate extension of the window—making it virtual.

2.  **Semantic Persistence (The "Long-Term Memory")**:
    Standard context windows are ephemeral; they vanish when the session ends. By writing annotations back to the `codemap` database, we create a persistent semantic layer. The "effective" context of a new session now includes the accumulated wisdom of all previous sessions. This directly addresses "high quality work" by preventing the agent from re-learning basic architectural facts every time.

3.  **High-Density Information**:
    `codemap` strips syntax (braces, boilerplate, whitespace) to reveal structure. A 5,000-line file might be represented by 100 lines of structural map. This 50x compression ratio means the same 200k token window can hold the *architecture* of 50x more code than before, allowing for reasoning at a much higher level of abstraction.

### Risks & Counter-arguments

1.  **Lossy Compression Risks**:
    Navigating via map means the agent *does not see the code*. If a bug lies in a specific line of implementation detail that isn't captured in the map or annotations, the agent will miss it. The "Librarian" relies on the agent knowing *when* to check the book (read the text) vs just reading the catalog (the map).
    *   *Mitigation*: The `map.read()` function must be cheap and easy to use, encouraging verification.

2.  **Cognitive Overhead**:
    This architecture changes the agent's task from "Read this text and answer" to "Navigate this filesystem, query this database, decide what to read, then read it." This consumes "reasoning tokens" (Chain of Thought) to save "context tokens." For simple queries, this might be overkill or lead to getting lost in the library.

### Verdict
This plan is a **High Impact** architectural pivot. It moves `pi-rlm` from a "Big Reader" (brute force) to a "Smart Navigator" (RAG-like). It is the most scalable way to handle repo-scale contexts that physically cannot fit in any model's window, making it essential for the stated goal.
