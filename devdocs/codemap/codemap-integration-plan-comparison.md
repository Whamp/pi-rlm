# Codemap Integration Plan: Enhancing the RLM REPL

## Executive Summary

Following a review of the "Recursive Language Models" (RLM) literature and `pi-rlm`'s architecture, we have refined our integration strategy.

**Goal:** Enhance the `pi-rlm` Python REPL with `codemap`'s structural analysis capabilities **without** compromising the agent's ability to hold and manipulate the full context state.

**Core Philosophy:** The Agent remains the driver; the REPL remains the engine. `codemap` becomes a high-fidelity instrument panel (AST, Dependencies, References) available within the REPL environment. We will **not** replace the "Load All Context" model with a strictly "Lazy Load" model, as the power of RLM comes from the agent's ability to programmatically explore the full state.

---

## The Integration Strategy: "Augmented RLM"

We will integrate `codemap` as a **library of helper functions** injected into the `rlm_repl.py` environment. This gives the agent new "senses" (structural vision) while preserving its existing "hands" (Python execution).

### 1. New Capabilities (The "Tool Injection")
*Replaces "Vision" and "Librarian" architectural pivots.*

We will expose `codemap` functionality directly to the agent in the REPL:

| Capability | New Python Helper | Description |
| :--- | :--- | :--- |
| **Structure** | `get_symbols(path)` | Returns classes, functions, and variables in a file (AST). |
| **Navigation** | `find_refs(symbol)` | Returns precise locations of symbol usage across the repo. |
| **Topology** | `get_deps(path)` | Returns the dependency graph (imports/exports) for a file. |
| **Flow** | `get_call_graph(func)` | Returns the call hierarchy for a specific function. |

**Benefit:** The agent can write sophisticated analysis scripts.
*Example:* "Find all functions calling `auth.login` and create a chunk for each one to verify security."

### 2. Semantic Chunking (Data Layer)
*Ref: `devdocs/plans/semantic-segmentation-rlm.md`*

We will add a new chunking strategy to the REPL, complementing the existing character-based one.

*   **Current:** `chunk_indices(size=200k)` -> Blind cuts.
*   **New:** `semantic_chunks(target_size=200k)` -> Respects function boundaries.
    *   Uses `codemap` to find safe cut points (end of functions).
    *   Prevents "torn" logic where a signature is in Chunk A and body in Chunk B.

### 3. Verification & Quality
*Ref: `devdocs/plans/rlm-codemap-verification.md`*

The agent can use `codemap` to **verify** its own deductions before answering.
*   *Scenario:* Agent thinks removing `User.id` is safe.
*   *Action:* Agent runs `find_refs('User.id')` in the REPL.
*   *Result:* Finds 50 usages. Agent self-corrects: "Actually, this is a breaking change."

---

## Comparison of Original Proposals (Re-evaluated)

We have consolidated the previous 8 proposals into features of this single "Augmented REPL" plan.

| Original Plan | Status | Integration Path |
| :--- | :--- | :--- |
| **The Librarian** | **Modified** | Implemented as `codemap` bindings in REPL. Agent acts as Librarian. |
| **Intelligent Routing** | **Adpated** | Becomes an emergent behavior. Agent "routes" itself using `get_deps`. |
| **Semantic Segmentation** | **Adopted** | Implemented as `semantic_chunks()` helper function. |
| **Verification** | **Adopted** | Implemented as a standard workflow pattern using `find_refs`. |
| **Vision (Graphs)** | **Tooling** | `get_call_graph` helper provides this data on demand. |
| **The Zipper** | **Optional** | Advanced optimization for `manifest.json` generation later. |
| **Blast Radius** | **Workflow** | Can be implemented by agent: `git diff | get_deps`. |
| **Learning Codebase** | **Deferred** | focusing on read-only analysis first. |

---

## Implementation Roadmap

1.  **Phase 1: The Bridge (Python Wrappers)**
    *   Modify `rlm_repl.py` to detect `codemap`.
    *   Implement `_run_codemap_command` wrapper.
    *   Expose `get_symbols`, `get_deps`, etc. to the `exec` environment.

2.  **Phase 2: The Data (Semantic Chunking)**
    *   Implement `semantic_chunks` algorithm in `rlm_repl.py` using `codemap` output.

3.  **Phase 3: The Skill (Prompt Engineering)**
    *   Update `SKILL.md` to teach the RLM agent about its new tools.
    *   "You have access to `get_symbols()`. Use it to map the file before chunking."

---

## Addressing the "Context" Concern

**User Concern:** "Discarding the benefit of having the python repl and pickle hold the whole context."

**Resolution:**
We explicitly **retain** the current architecture where the REPL holds the full context state (pickled).
*   **Small/Medium Repos:** We load full text + full map. Agent has god-mode.
*   **Large Repos:** We load full map + partial text (on demand). Agent uses map to decide what text to load into the state variables.

This approach ensures we **enhance** the agent's capability (adding structural awareness) without **limiting** its scope (it can still read/grep/process raw text exactly as before).
