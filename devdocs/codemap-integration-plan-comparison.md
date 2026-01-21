# Codemap Integration Plan Comparison

## Executive Summary

After evaluating four distinct strategies to integrate `codemap` with `pi-rlm`, we have determined that **Intelligent Routing (Graph-Driven RLM)** represents the highest value path for extending the functional context window of LLM agents.

While **Semantic Segmentation** offers a foundational quality improvement for data ingestion, **Intelligent Routing** fundamentally alters the agent's capability to reason across file boundaries, transforming `pi-rlm` from a parallel summarizer into a deep-search engine.

**Recommendation:** Prioritize **Intelligent Routing** as the primary architectural shift, with **Semantic Segmentation** as a necessary data-layer prerequisite.

---

## Comparison Matrix

We evaluated each plan against four key metrics relevant to high-quality long-context work:

1.  **Effective Context Extension:** Does this allow the agent to reason about more code than fits in memory?
2.  **Reasoning Depth:** Does this enable multi-step logical deduction (e.g., tracing execution)?
3.  **Noise Reduction:** Does this filter out irrelevant tokens before they reach the model?
4.  **Temporal Value:** Does the value increase over time or is it immediate?

| Strategy | Effective Context Extension | Reasoning Depth | Noise Reduction | Temporal Value |
| :--- | :--- | :--- | :--- | :--- |
| **1. Intelligent Routing** | ⭐⭐⭐⭐⭐ (Unlimited Hop) | ⭐⭐⭐⭐⭐ (High) | ⭐⭐⭐⭐ (High) | Immediate |
| **2. Semantic Segmentation** | ⭐⭐ (Chunk Cohesion) | ⭐⭐⭐ (Medium) | ⭐⭐ (Medium) | Immediate |
| **3. Blast Radius (Diff)** | ⭐⭐⭐ (Targeted) | ⭐⭐ (Low) | ⭐⭐⭐⭐⭐ (Max) | Event-Driven |
| **4. Learning Codebase** | ⭐⭐ (Density) | ⭐⭐ (Low) | ⭐⭐⭐ (Medium) | Cumulative |

---

## Critical Analysis

### 1. Intelligent Routing (Graph-Driven RLM)
*Ref: `devdocs/plans/intelligent-routing-rlm.md`*

**The Concept:** Shifts `pi-rlm` from a Map-Reduce architecture (read all chunks → synthesize) to a Tree-Search architecture (read entry point → follow references → read next specific node).

*   **Pros:**
    *   **Solves the "Disconnected Logic" Problem:** Standard RLM fails when logic is split across files that end up in different parallel batches. Routing follows the thread of execution regardless of where it lives.
    *   **Infinite Effective Context:** Because it only loads relevant nodes, it can traverse a 10GB codebase without ever exceeding the context window, provided the *path* of inquiry fits.
    *   **High Precision:** Uses `codemap`'s exact reference graph to jump to definitions, minimizing hallucinated connections.
*   **Cons:**
    *   **Latency:** Serial execution (hop -> hop -> hop) is slower than parallel map-reduce.
    *   **Tunnel Vision:** Risk of missing context that isn't explicitly linked via code references (e.g., implicit config coupling).

### 2. Semantic Segmentation (Structural Chunking)
*Ref: `devdocs/plans/semantic-segmentation-rlm.md`*

**The Concept:** Replaces character-count chunking with AST-aware chunking. Uses `codemap` to ensure chunks respect function/class boundaries and group related files.

*   **Pros:**
    *   **Foundational Quality:** Garbage In, Garbage Out. If a standard RLM chunk cuts a function signature off from its body, the sub-agent cannot reason about it. This fixes the data layer.
    *   **Synergy:** Essential for Intelligent Routing. You cannot "route to a function" effectively if that function is split across two arbitrary text chunks.
*   **Cons:**
    *   **Incremental Gain:** On its own, it just makes the existing summaries slightly better. It doesn't unlock new *types* of reasoning.

### 3. Blast Radius (The "Diff" RLM)
*Ref: `devdocs/plans/blast-radius-rlm.md`*

**The Concept:** Limits analysis to changed files and their immediate dependents (incoming refs).

*   **Pros:**
    *   **Efficiency:** Massive token savings for CI/CD or PR review workflows.
    *   **Focus:** Prevents the agent from getting distracted by legacy code irrelevant to the current task.
*   **Cons:**
    *   **Niche Utility:** Only useful when there *is* a diff. Useless for "Explain how this repo works" or "Refactor this module" (before the refactor starts).
    *   **Dependency Blindness:** May miss global side effects if the dependency graph isn't perfect (e.g., dynamic imports).

### 4. Learning Codebase (Persistent Annotations)
*Ref: `devdocs/plans/learning-codebase-rlm.md`*

**The Concept:** Sub-agents write permanent notes back to `codemap` during their analysis, effectively "commenting" the code without touching source files.

*   **Pros:**
    *   **Compound Interest:** The agent gets smarter about the codebase every time it runs.
    *   **Human-Agent Bridge:** Humans can read these annotations via CLI, and agents can read human annotations.
*   **Cons:**
    *   **Slow Start:** Zero value on the first run.
    *   **Stale Data Risk:** Annotations can drift from implementation if not actively maintained.

---

## Verdict & Prioritization

The goal is **"High Quality Long Context Work."**

**Standard RLM** (current) is good at "broad, shallow" understanding (summarization).
**Intelligent Routing** enables "narrow, deep" understanding (tracing).
**Semantic Segmentation** enables "accurate" understanding (cohesion).

We should aim to combine **Semantic Segmentation** and **Intelligent Routing** to create a system that can accurately traverse deep call graphs in massive repositories.

### Recommended Implementation Roadmap

1.  **Phase 1: Semantic Ingestion (The Foundation)**
    *   Implement **Semantic Segmentation**. `pi-rlm` needs to stop reading lines of text and start reading "Code Units."
    *   *Why:* This is low-risk and immediately improves the current `pi-rlm` performance while preparing the data structure needed for routing.

2.  **Phase 2: Graph Navigation (The Leap)**
    *   Implement **Intelligent Routing**. Create a new `rlm-navigator` agent that has access to `codemap` tools (`calls`, `find-refs`, `deps`) and can request specific Semantic Chunks.
    *   *Why:* This unlocks the "Deep Reasoning" capability that is currently missing.

3.  **Phase 3: Optimization (The Polish)**
    *   Implement **Blast Radius** logic as a filter flag (`--diff-only`) for the Routing agent.
    *   Implement **Learning Codebase** as a background "side effect" of the Navigator agent (leaving breadcrumbs as it explores).

### Conclusion

We will focus on **Phase 1 (Semantic Segmentation)** and **Phase 2 (Intelligent Routing)**. These two features combined will allow `pi-rlm` to outperform humans in navigating unfamiliar, massive codebases by maintaining perfect memory of the call stack while jumping between files instantly.
