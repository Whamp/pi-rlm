# Codemap Integration Plan Comparison

## Executive Summary

We have evaluated **eight** distinct strategies for integrating `codemap` capabilities into `pi-rlm`. These proposals range from low-level data ingestion improvements to complete architectural overhauls.

**The Verdict:** We recommend a hybrid approach centered on **"The Librarian"** architecture, which supersedes several smaller proposals, supported by **Semantic Segmentation** as the baseline ingestion strategy.

---

## Comparison Matrix

We evaluated each plan against four key metrics for high-quality long-context work:

1.  **Effective Context Extension:** Does this allow the agent to reason about more code than fits in memory?
2.  **Reasoning Depth:** Does this enable multi-step logical deduction?
3.  **Noise Reduction:** Does this filter out irrelevant tokens?
4.  **Temporal Value:** Does the system get smarter over time?

| Strategy | Type | Context Extension | Reasoning Depth | Noise Reduction | Temporal Value |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. The Librarian** | Architecture | ⭐⭐⭐⭐⭐ (Virtual) | ⭐⭐⭐ (Navigational) | ⭐⭐⭐⭐⭐ (Lazy Load) | ⭐⭐⭐⭐⭐ (Cumulative) |
| **2. Intelligent Routing** | Traversal | ⭐⭐⭐⭐⭐ (Unlimited) | ⭐⭐⭐⭐⭐ (High) | ⭐⭐⭐⭐ (High) | Immediate |
| **3. The Zipper** | Compression | ⭐⭐⭐⭐ (Compressed) | ⭐⭐⭐ (Standard) | ⭐⭐⭐⭐ (Symbolic) | Immediate |
| **4. Vision (Graphs)** | Injection | ⭐⭐⭐ (Dense) | ⭐⭐⭐⭐ (Structural) | ⭐⭐ (Additive) | Immediate |
| **5. Verification** | Quality | ⭐ (Targeted) | ⭐⭐⭐⭐⭐ (Verified) | ⭐⭐⭐ (Focused) | Immediate |
| **6. Semantic Segmentation** | Ingestion | ⭐⭐ (Cohesion) | ⭐⭐⭐ (Medium) | ⭐⭐ (Medium) | Immediate |
| **7. Blast Radius** | Workflow | ⭐⭐⭐ (Diff Only) | ⭐⭐ (Low) | ⭐⭐⭐⭐⭐ (Diff Only) | Event-Driven |
| **8. Learning Codebase** | Persistence | ⭐⭐ (Density) | ⭐⭐ (Low) | ⭐⭐⭐ (Medium) | ⭐⭐⭐⭐ (Cumulative) |

---

## Critical Analysis

### 1. The Librarian Architecture (Top Recommendation)
*Ref: `devdocs/plans/librarian-architecture.md`*

**Concept:** Transforms `pi-rlm` from a "Reader" into a "Navigator". The agent loads a lightweight "Index" of the repo (using `codemap` outlines) and lazily reads only the files necessary, while writing persistent annotations back to the map.
*   **Relationship:** Superset of **Learning Codebase** and heavily overlaps with **Intelligent Routing**.
*   **Pros:** Solves the context window problem fundamentally by making it $O(1)$ (index size) rather than $O(N)$ (repo size).
*   **Cons:** Higher cognitive load on the agent (must decide *what* to read).

### 2. Intelligent Routing (Graph-Driven)
*Ref: `devdocs/plans/intelligent-routing-rlm.md`*

**Concept:** Tree-search architecture where the agent follows code references (Definition -> Usage -> Call) to traverse the codebase.
*   **Pros:** Best for "tracing" execution paths across files.
*   **Cons:** Slower serial execution compared to parallel RLM.

### 3. The Zipper (Context Compression)
*Ref: `devdocs/plans/rlm-codemap-context-compression.md`*

**Concept:** Replaces raw text chunks with "Enriched Manifests" containing symbol tables. The agent sees a menu of symbols and requests expansion.
*   **Pros:** Drastically reduces "boilerplate" tokens.
*   **Cons:** Agent might miss implementation details hidden in the compression.

### 4. Vision (Multi-Modal Code)
*Ref: `devdocs/plans/codemap-vision-integration.md`*

**Concept:** Injects ASCII call graphs and dependency trees into the prompt alongside code chunks.
*   **Pros:** Gives sub-agents "peripheral vision" of how their chunk connects to the world.
*   **Cons:** Can pollute context if graphs are too large.

### 5. Verification & Quality
*Ref: `devdocs/plans/rlm-codemap-verification.md`*

**Concept:** Uses static analysis to *verify* sub-agent claims (e.g., "Does function A actually call function B?").
*   **Pros:** Eliminates hallucinations about code structure.
*   **Cons:** Language support limited to what `codemap` supports (TS/JS mainly).

### 6. Semantic Segmentation (Baseline Requirement)
*Ref: `devdocs/plans/semantic-segmentation-rlm.md`*

**Concept:** Chunking by AST nodes (functions/classes) instead of character counts.
*   **Status:** **Mandatory**. This should be the default data layer for *any* higher-level strategy.
*   **Pros:** Ensures "Garbage In" is eliminated.

### 7. Blast Radius (Diff-Driven)
*Ref: `devdocs/plans/blast-radius-rlm.md`*

**Concept:** Only analyzes changed files and their immediate dependents.
*   **Role:** Specialized workflow for PR reviews, not a general architecture.

### 8. Learning Codebase
*Ref: `devdocs/plans/learning-codebase-rlm.md`*

**Concept:** Persistent annotations.
*   **Status:** Merged into **The Librarian**.

---

## Synthesis & Roadmap

We propose a phased integration:

**Phase 1: Foundation (Data Layer)**
*   Implement **Semantic Segmentation** to ensure all RLM operations use clean, valid code blocks.

**Phase 2: The Librarian (Architecture)**
*   Adopt **The Librarian** as the core architecture.
*   Integrate **Vision** features (injecting graphs) as tools available to the Librarian.
*   Integrate **Verification** as a "fact-checking" step before the Librarian commits an answer.

**Phase 3: Specialized Workflows**
*   Add **Blast Radius** as a flag (`--diff`) for the Librarian.
*   Add **Zipper**-style compression for massive files that even the Librarian must read partially.
