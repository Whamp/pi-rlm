# Blast Radius RLM (Diff-Driven Analysis)

**Status:** Proposed
**Goal:** Transform `pi-rlm` from a linear "read-everything" engine into a smart "read-what-matters" engine by leveraging `codemap`'s change detection and reference graph.

## The Concept

The "Blast Radius" is defined as:
1.  **The Spark**: Files that have been modified (detected via `mtime`/hash).
2.  **The Fuel**: Files that import, call, or rely on the modified files (detected via `codemap` reference graph).

By feeding only the Blast Radius into `pi-rlm`, we effectively extend the *functional* context window. An 100GB repository becomes a 5MB context file containing only the active feature slice.

## Implementation Plan

### 1. New Tool: `calculate-blast-radius`

We need a script to interface with `codemap`'s programmatic API since the CLI doesn't explicitly output "changed files + dependents" in a raw list format.

**Script:** `skills/rlm/scripts/calc_blast_radius.ts`

```typescript
import { openCache, detectChanges, generateSourceMap } from "codemap";
import { resolve } from "path";

// 1. Detect Changes
const db = openCache(process.cwd());
// We might need to expose detectChanges more directly or rely on the cache state
// For now, we assume we can query the DB for files where mtime > last_indexed
// OR utilize the `refreshCache` return value if exposed.

// Alternative: Use git for the "Spark"
// const changedFiles = git diff --name-only HEAD;

// 2. Expand to Dependents (The Fuel)
const blastRadius = new Set<string>(changedFiles);

for (const file of changedFiles) {
  // Get files that import the changed file
  const dependents = db.getDependents(file);
  dependents.forEach(d => blastRadius.add(d));
  
  // Get files that call symbols in the changed file (if detailed refs enabled)
  const incomingRefs = db.getReferences('in', { filePath: file });
  incomingRefs.forEach(ref => blastRadius.add(ref.sourceFile));
}

// 3. Output
console.log(Array.from(blastRadius).join('\n'));
```

### 2. RLM Workflow Update

Modify `skills/rlm/SKILL.md` to support a "smart-context" workflow.

**Current Flow:**
1. User provides `context=bigfile.txt`.
2. RLM chunks `bigfile.txt`.

**New Flow (Repo Mode):**
1. User invokes `/skill:rlm mode=blast-radius`.
2. **Step 1: Calculate Radius**
   - Run `calc_blast_radius.ts` -> returns `[src/auth.ts, src/login.ts, tests/auth.test.ts]`.
3. **Step 2: Materialize Context**
   - Concatenate the raw content of these files into a single temporary context file: `.pi/rlm_state/blast_context_<timestamp>.txt`.
   - *Optimization:* Inject file separators (`--- src/auth.ts ---`) so RLM knows file boundaries.
4. **Step 3: Standard RLM**
   - Run `rlm_repl.py init .pi/rlm_state/blast_context.txt`.
   - Proceed with standard chunking/sub-agent analysis.

### 3. Sub-Agent Adaptation

The `rlm-subcall` agent is currently designed for generic text. For code analysis, knowing the *origin* of the text is crucial.

- **Manifest Enhancement**: When `rlm_repl.py` chunks the concatenated file, it should respect the injected file separators. A chunk should not straddle two files if possible.
- **Prompt Tweak**: "You are analyzing a Blast Radius context. This contains changed files and their immediate dependents."

## Feasibility Analysis

- **Codemap API**: `codemap` exports `openCache`, but `detectChanges` is internal or tied to `refreshCache`. We might need to expose a cleaner "what changed?" API in `codemap` or rely on `git diff` for the initial "Spark".
- **Reference Accuracy**: `codemap` reference tracking is robust for TS/JS but non-existent for C++/Rust cross-file refs. This feature would be TS/JS only initially.
- **Context Size**: A "Blast Radius" can still be huge if a core utility (`utils.ts`) changes. We may need a "depth" limiter or exclusion list.

## Introspective Analysis

**Goal:** Extend the functional context window of LLMs for high-quality long-context work.

### How this helps (The "Pro" argument)
1.  **Signal-to-Noise Ratio**: Standard RLM on a repo reads 95% static, irrelevant code. "Blast Radius" RLM reads only the active graph. This is the definition of "effective" context.
2.  **Latency**: Processing 5MB of "active" code is 20x faster than processing 100MB of "entire" code.
3.  **Logical Coherence**: By explicitly gathering dependents (dependents = context), we ensure the LLM sees the *consequences* of a change, which is the primary thing missing from simple "git diff" analysis.

### How this hurts (The "Con" argument)
1.  **Loss of Global Context**: If the task is "Refactor the architecture," looking only at changed files is insufficient. The agent loses the "Forest" for the "Trees."
2.  **Dependency on `codemap` correctness**: If `codemap` misses a reference (e.g., dynamic import, complex type inference), RLM effectively puts on blinders. Standard RLM ("read everything") is robust against tooling failures because it brute-forces the problem.
3.  **Complexity**: It couples `pi-rlm` tightly to `codemap`. Currently, `pi-rlm` is tool-agnostic (just needs text).

### Conclusion
This moves us closer to the goal of **efficient** engineering workflows (PR review, feature iteration) but potentially further from **greenfield/architectural** understanding where "reading everything" is actually a feature, not a bug. It should be implemented as a *mode*, not a replacement.
