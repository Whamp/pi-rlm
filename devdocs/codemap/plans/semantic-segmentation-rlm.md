# Semantic Segmentation RLM (Structural Chunking)

**Status:** Proposed
**Goal:** Replace `pi-rlm`'s blind character-based chunking with structure-aware segmentation using `codemap`, ensuring sub-agents receive logically complete code blocks.

## The Concept

Current RLM chunking (`chunk_indices` in `rlm_repl.py`) is based on arbitrary character counts (e.g., 200KB).
- **The Problem:** It ruthlessly cuts functions, classes, and comments in half. A sub-agent receiving the second half of a function lacks the signature, parameters, and JSDoc.
- **The Solution:** Use `codemap` to identify "Atomic Units of Context" (Classes, Functions, Interfaces). Build chunks by packing these atoms together until the budget is filled.

## Implementation Plan

### 1. New Strategy: `write_semantic_chunks`

Modify `skills/rlm/scripts/rlm_repl.py` to support a new chunking strategy.

**Algorithm:**
1.  **Scan:** Run `codemap -o json` on the target file/context.
2.  **Atomize:** Parse the JSON to create a list of "Atoms".
    - Atom: A range of lines (start, end) representing a symbol.
    - Gaps: Lines between symbols (imports, whitespace, comments) are also Atoms.
3.  **Bin Packing:**
    - Initialize `current_chunk`.
    - Iterate through Atoms.
    - If `current_chunk + atom < chunk_limit`: Add Atom.
    - Else: Close `current_chunk`, start new.
    - *Edge Case:* If Atom > `chunk_limit` (massive function), fall back to character splitting *within* that atom, but try to split on line boundaries or internal blocks.

### 2. Integration with `codemap`

Since `rlm_repl.py` is Python and `codemap` is Node/CLI:
- We invoke `codemap` via subprocess within `rlm_repl.py`.
- `codemap` output is JSON, which Python handles natively.

**Python Implementation Sketch:**
```python
import subprocess
import json

def get_symbols(filepath):
    # Get structural map
    out = subprocess.check_output(['codemap', filepath, '-o', 'json'])
    data = json.loads(out)
    return data['files'][0]['symbols']

def create_chunks(content, symbols, limit=200000):
    chunks = []
    current_chunk_start = 0
    # ... logic to align boundaries with symbol['endLine'] ...
    return chunks
```

### 3. Header Injection

Each chunk should be "Hydrated" with context that might have been left in a previous chunk (e.g., file imports, class declaration line).
- If we are inside `class AuthService` but in `chunk_02`, we should inject a synthetic header:
  ```typescript
  // ... inside class AuthService ...
  ```
- `codemap` structure tells us the hierarchy.

## Feasibility Analysis

- **Technical:** High. `codemap` provides exactly the line ranges needed.
- **Performance:** `codemap` parsing is fast (<1s per file usually). Bin packing is instant.
- **Benefit:** Immediate improvement in sub-agent comprehension.

## Introspective Analysis

**Goal:** Extend the functional context window of LLMs.

### How this helps (The "Pro" argument)
1.  **Context Integrity:** "Functional context" depends on *completeness*. A function without its signature is functionally useless context. This ensures every piece of context is syntactically valid (mostly).
2.  **Reduced Hallucination:** Agents don't have to guess what the variable `config` is, because the chunk includes the function signature defining `config`.
3.  **Optimized Retrieval:** We can tag chunks with the symbols they contain. "This is the 'Auth Chunk'".

### How this hurts (The "Con" argument)
1.  **Complexity:** Moving from `s[0:20000]` to a parsing-based logic introduces failure modes (parser crashes, syntax errors in source).
2.  **Fragmentation:** Large monolithic functions still need to be split. This doesn't solve "Bad Code", it only optimizes "Good Code".

### Conclusion
This is a foundational infrastructure upgrade. It doesn't change the *user* workflow, but it significantly raises the baseline competence of the sub-agents. It should be the default chunking mechanism for code files.

## Strategic Analysis (From Structural Chunking Deep Dive)

### Goal Alignment
Our high-level goal is to **extend the functional context window**—increasing the amount of information an LLM can effectively reason about, not just physically load.

### How This Plan Helps (Pros)
1.  **Increased Semantic Density**: By ensuring chunks contain complete logic blocks (functions/classes) rather than fragments, we remove the "reconstruction overhead" sub-agents currently face. A sub-agent processing a clean class definition can reason about it deeply; a sub-agent processing the bottom half of a class and the top half of a README is wasting tokens on orientation.
2.  **Contextual Integrity**: Grouping files by dependency (Phase 3.3) means the "functional context" of a chunk is self-contained. The sub-agent sees `Interface I` and `Class C implements I` in the same prompt, allowing it to verify compliance immediately. In naive chunking, these might be separated by megabytes of text, effectively making the relationship invisible to the parallel workers.
3.  **Reduced Hallucination**: "Context Frames" (Phase 3.5) explicitly tell the model "You are in the Billing Module." This reduces the likelihood of the model hallucinating that a generic `process()` function belongs to a different domain.

### How This Plan Moves Us Further
It transforms `pi-rlm` from a **text-processing** engine into a **code-processing** engine. It acknowledges that code has structure and leverages that structure to "pack" the context window more efficiently. It's akin to defragmenting a hard drive—the total space is the same, but the usable contiguous blocks are much larger.

### Risks & Limitations
1.  **The "Mega-File" Problem**: Some files (e.g., generated code, massive reducers) simply exceed the context window on their own. Structural chunking helps split them *safely*, but the sub-agent still loses the ability to see the file as a whole unit.
2.  **Graph Complexity**: For "spaghetti code" repos with high cyclic dependencies, the topological sort may produce arbitrary orderings that are no better than alphabetical, negating the clustering benefit.
