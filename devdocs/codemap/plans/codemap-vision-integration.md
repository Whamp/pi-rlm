# Plan: Codemap "Vision" Integration for Pi-RLM

**Status:** Draft
**Date:** 2026-01-21
**Author:** Pi (Antigravity)

## 1. Overview

This plan details the implementation of **"Multi-Modal Vision for Code"** within the `pi-rlm` system. By integrating `codemap`'s structural visualization capabilities (dependency trees, call graphs) into `pi-rlm`'s text-processing engine, we bridge the gap between semantic understanding (RLM) and structural awareness (Codemap).

The goal is to enable RLM agents to "see" the shape of the codebase, allowing them to:
1.  **Visualize Complexity**: Understand how local code chunks fit into global flows.
2.  **Detect & Solve Cycles**: Automatically identify and refactor circular dependencies.

## 2. Core Concepts

### 2.1. The "Structural Context" Injection
Standard RLM sub-agents suffer from "keyhole surgery" syndrome—they see the code but not the connections. By injecting `codemap`'s ASCII graphs into the sub-agent's prompt, we give them a "map" of the territory.

**Input to Sub-agent:**
```text
[CHUNK: src/auth.ts (lines 50-150)]
... code ...

[CONTEXT: Call Graph for 'validateUser']
src/auth.ts:validateUser
  - src/db.ts:getUser (lines 20-30)
  - src/crypto.ts:hash (lines 10-15)
```

**Task:** "Explain how `validateUser` in this chunk orchestrates the data flow shown in the graph."

### 2.2. The "Cycle Breaker" Workflow
A specialized RLM strategy triggered when circular dependencies are detected.

1.  **Detect**: Run `codemap deps --circular`.
2.  **Isolate**: Identify files involved in the cycle (the "Cycle Cluster").
3.  **Analyze**: RLM reads *only* the files in the Cycle Cluster.
4.  **Solve**: Synthesize a refactoring plan (e.g., Extract Interface, Dependency Inversion) based on semantic understanding of *why* the cycle exists.

## 3. Implementation Details

### 3.1. Prerequisite: Tool Availability
`codemap` is a separate project. `pi-rlm` needs access to the `codemap` binary.
- **Strategy**: Assume `codemap` is installed in the user's path or available at `../codemap/dist/codemap`.
- **Fallback**: The `rlm_repl.py` script will attempt to locate `codemap`.

### 3.2. Updating `rlm_repl.py`

We will add a `CodemapBridge` class or helper functions to the REPL to wrap `codemap` CLI executions.

**New Functions:**

| Function | Codemap Command | Purpose |
|----------|-----------------|---------|
| `get_call_graph(symbol, depth=3)` | `codemap call-graph <symbol> --depth <n>` | Visualize control flow |
| `get_deps(file, depth=3)` | `codemap deps <file> --depth <n>` | Visualize file dependencies |
| `find_cycles()` | `codemap deps --circular --json` | Return structured cycle data |
| `get_symbol_location(symbol)` | `codemap find-refs <symbol> --json` | Locate symbol to target RLM chunks |

**Output Handling**:
- Graphs are returned as string (ASCII trees) for direct prompt injection.
- Cycles are returned as Python lists for the REPL to iterate over.

### 3.3. Skill Updates (`SKILL.md`)

The RLM skill needs to know when to use these "eyes".

**New Triggers**:
- "Trace data flow..." → Trigger `get_call_graph` -> Inject into sub-agent.
- "Refactor dependencies..." / "Fix cycles..." → Trigger `find_cycles` -> Cycle Breaker workflow.
- "What relies on X?" → Trigger `get_deps --reverse`.

**Prompt Augmentation**:
When `rlm-subcall` is invoked, the `task` field in the JSON payload will be enhanced:
```json
{
  "agent": "rlm-subcall",
  "task": "Query: Explain data flow.\n\nContext Graph:\n" + call_graph_output + "\n\nChunk file: ..."
}
```

## 4. Workflows

### Workflow A: Visualizing Complexity (Data Flow Analysis)

1.  **User Query**: "Explain how the `PaymentProcessor` class handles errors."
2.  **Root LLM (via REPL)**:
    - Calls `get_call_graph("PaymentProcessor")`.
    - Sees graph structure (e.g., calls `Logger`, `StripeAPI`, `Database`).
    - Decides to inspect chunks containing `PaymentProcessor` source code.
3.  **Sub-agent Dispatch**:
    - Passes the chunk containing `PaymentProcessor`.
    - **Crucial Add**: Appends the ASCII call graph to the prompt.
4.  **Sub-agent Output**: "The `process` method (lines 50-80) wraps calls to `StripeAPI` (seen in graph) in a try/catch block. It delegates error recording to `Logger` (lines 85) as shown in the graph structure."

### Workflow B: The Cycle Detective

1.  **User Query**: "Check for and fix circular dependencies."
2.  **Root LLM (via REPL)**:
    - Calls `find_cycles()`.
    - Receives `[['src/A.ts', 'src/B.ts']]`.
3.  **Targeting**:
    - Instead of chunking the whole repo, Root LLM creates chunks *only* for `src/A.ts` and `src/B.ts`.
4.  **Analysis**:
    - Sub-agents read A and B.
    - Task: "Identify the import that causes the cycle and the shared logic."
5.  **Synthesis**:
    - Root LLM proposes: "Move the `SharedType` interface from `B.ts` to a new file `types.ts` to break the loop."

## 5. Development Roadmap

1.  **Phase 1: Bridge Construction**
    - Modify `rlm_repl.py` to invoke `codemap`.
    - Verify robust path handling (finding the binary).
    - Implement `get_call_graph` and `get_deps`.

2.  **Phase 2: Cycle Detection Logic**
    - Implement `find_cycles` parsing in Python.
    - Create a test case with a circular dependency (create 2 dummy files).
    - Verify RLM can "see" the cycle.

3.  **Phase 3: Prompt Engineering**
    - Update `SKILL.md` to instruct the model on using these tools.
    - Test if sub-agents actually utilize the ASCII graph context (they might ignore it if it's too noisy).
    - Tune `codemap` output (e.g., `--depth` limits) to fit context.

4.  **Phase 4: Integration Test**
    - Run a full RLM session on the `codemap` repo itself (which has complex dependencies).
    - Ask RLM to explain the `deps` command logic using the call graph of `deps/tree.ts`.

## 6. Risks & Mitigations

- **Risk**: `codemap` output is too large for prompt context.
    - *Mitigation*: Use `--depth` flags to limit tree size. Implement `truncate` in REPL if graph > 50 lines.
- **Risk**: `codemap` not installed/build fails.
    - *Mitigation*: Graceful degradation. If `codemap` fails, RLM reverts to text-only mode with a warning.
- **Risk**: Symbol ambiguity (e.g., multiple `main` functions).
    - *Mitigation*: Use `codemap`'s file-scoping syntax (`src/index.ts:main`). RLM must be smart enough to qualify symbols.

## 7. Introspective Analysis: Alignment with Core Goal

**Core Goal:** Extend the functional context window of LLMs for effective, high-quality long-context work.

### How this plan moves us closer (Alignment)
- **High-Density Information:** By injecting ASCII graphs, we provide a "compressed" view of code that would otherwise require thousands of lines of text to explain. A 50-line call graph is functionally equivalent to reading 10 files to trace execution. This effectively "expands" the context window by increasing information density.
- **Reduced Hallucination:** Long-context models often "lose the plot" on structural details. Providing a hard, verified ground truth (via `codemap`) anchors the model's reasoning, directly improving quality.
- **Global Awareness:** Sub-agents typically suffer from "tunnel vision" (seeing only their chunk). This plan gives them "peripheral vision" (seeing what their chunk calls and depends on), which is critical for high-quality architectural reasoning.

### How this plan moves us further away (Risks)
- **Context Pollution:** If the graphs are too verbose or irrelevant, we waste precious context tokens on noise. A graph that doesn't help answer the specific query is just clutter.
- **Dependency Fragility:** Relying on an external binary introduces failure modes. If `codemap` fails, the agent might be confused by the missing "vision," potentially degrading performance compared to a pure text baseline.
- **Complexity Overhead:** This transforms `pi-rlm` from a simple, elegant text processor into a multi-tool pipeline. Increased complexity can make the system harder to debug and reason about.

### Verdict
This plan is a **strong positive step**. It attacks the context window problem via **compression and grounding** rather than raw size. It addresses the "quality" aspect of the goal by mitigating the specific weakness of RLM (loss of structural context). The risks are manageable via strict output truncation and graceful error handling.
