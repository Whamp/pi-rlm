# Intelligent Routing RLM (Graph-Based Navigation)

**Status:** Proposed
**Goal:** Shift `pi-rlm` from a Map-Reduce architecture (parallel chunk processing) to a Tree-Search architecture (recursive graph traversal) using `codemap` as the compass.

## The Concept

Instead of reading the entire book at once, we trace the narrative thread.
1.  **Start Node:** The user's query usually targets a specific entry point (e.g., "How does `login` work?").
2.  **The Compass:** `codemap` provides the edges (`calls`, `find-refs`, `deps`).
3.  **The Walker:** `pi-rlm` provides the node processing (reading the actual code text).

## Implementation Plan

### 1. New REPL Capability: `get_chunk_for_location`

Currently, `rlm_repl.py` chunks blindly. We need "semantic addressing".

**Update `rlm_repl.py`:**
Add a function `materialize_chunk_around(filepath, line_number, window_lines=50)`:
1.  Locate the file in the context (assuming multi-file context or mapping).
2.  Extract lines `line_number - window` to `line_number + window`.
3.  Return this as a text buffer or write to a temp file.

### 2. The Crawler Agent Loop

We introduce a new sub-agent mode or modify the main `SKILL.md` to support an iterative loop.

**Workflow:**

1.  **User Query:** "Trace the execution flow of `PaymentService.charge`."
2.  **Scout (Root Agent):**
    - Call `codemap find-refs PaymentService:charge` to find the definition.
    - Result: `src/services/payment.ts:45`.
3.  **Step 1:**
    - Call `rlm_repl.py materialize_chunk_around('src/services/payment.ts', 45)`.
    - Delegate to `rlm-subcall`: "Analyze this chunk. Identify outgoing calls that look relevant to 'charging'."
4.  **Result 1:**
    - Sub-agent returns: "Calls `gateway.submit()` and `db.recordTransaction()`."
5.  **Step 2 (Branching):**
    - Root Agent decides: "I need to see `gateway.submit`."
    - Call `codemap find-refs gateway:submit`.
    - Result: `src/lib/stripe.ts:102`.
6.  **Step 3:**
    - Call `rlm_repl.py materialize_chunk_around('src/lib/stripe.ts', 102)`.
    - Delegate to `rlm-subcall`.
7.  **Synthesis:**
    - Construct the narrative from the visited nodes.

### 3. Tool Integration

We need to bridge `codemap` CLI output into `rlm`'s python environment to make this seamless, or keep the orchestration in the Main Pi Session (Bash).

**Preferred Approach:** Orchestration in Main Pi Session.
The Main Pi Agent already has access to `bash` (for `codemap`) and `rlm_repl.py`. It just needs the *procedure* defined in `SKILL.md`.

**Update `SKILL.md`:**
Add a section "Deep Dive Tracing":
- "When asked to trace execution or understand specific logical flows:"
- "1. Locate start symbol with `codemap`."
- "2. Materialize chunk with `rlm`."
- "3. Sub-call to analyze."
- "4. Identify next hops from sub-call output."
- "5. Repeat."

## Feasibility Analysis

- **Latency:** Serial execution (Agent -> Tool -> Agent) is much slower than parallel blast. A trace of depth 5 might take 5 minutes.
    - *Mitigation:* Parallel branching. If step 1 finds 3 calls, spawn 3 sub-agents in parallel for step 2.
- **Context Boundaries:** "Materialize chunk around line X" is fragile. It might miss the `import` statements at the top of the file or the class definition wrapping the method.
    - *Mitigation:* Use `codemap`'s `detail=full` output as a guide, or ensure `rlm_repl.py` understands file structure (using indentation-based folding or simple heuristics).
- **Tool Gaps:** `codemap` doesn't support local variable refs or dynamic dispatch well. The "Compass" might be broken in complex code.

## Introspective Analysis

**Goal:** Extend the functional context window of LLMs.

### How this helps (The "Pro" argument)
1.  **Infinite Effective Context:** This approach scales to Google-sized monorepos. You never read the whole thing; you only read the path you walk.
2.  **High-Fidelity Context:** The context loaded is exactly what is needed for the specific step—no more, no less. It mimics how a senior engineer debugs (jumping to definitions).
3.  **Dynamic Discovery:** Unlike "Blast Radius" (which assumes you know the relevant files `a priori` via git status), this *discovers* relevancy dynamically. It finds "unknown unknowns" if they are linked in the call graph.

### How this hurts (The "Con" argument)
1.  **Tunnel Vision:** The agent only sees the path it chose. It might miss a global event listener or a side-effect that isn't in the direct call graph (e.g., database triggers, middleware).
2.  **High Coordination Overhead:** Requires constant Main Agent <-> Tool interaction. This burns tokens on "reasoning about where to go next" rather than "reading code."
3.  **Fragility:** If one link in the chain is broken (e.g., `codemap` fails to resolve a generic call), the trace ends abruptly. "Blast Radius" or "Read Everything" are more robust to broken links.

### Conclusion
This is the "Holy Grail" of agentic coding but is technically demanding. It shifts the burden from "Context Size" (RAM) to "Agent Reasoning" (CPU). It represents a fundamental shift from RLM (Recursive *Language Model*) to something more like an "Autonomous Code Rover."
