# The Learning Codebase (Persistent Semantic Annotations)

**Status:** Proposed
**Goal:** Create a virtuous cycle where `pi-rlm`'s deep analysis is persisted back into `codemap`'s semantic layer, reducing the need for re-analysis in future sessions.

## The Concept

Current LLM coding is ephemeral. We burn 1M tokens understanding a legacy module, fix a bug, and then the "understanding" evaporates. The next agent starts from zero (or just the code).

By bridging `pi-rlm` (the analyzer) and `codemap` (the persistent memory), we turn the codebase into a "Learning" entity.
- **Read:** `pi-rlm` agents consume `codemap` annotations to orient themselves instantly.
- **Write:** `pi-rlm` agents output structured annotations to persist their findings.

## Implementation Plan

### 1. Update `rlm-subcall` Output Schema

Modify `agents/rlm-subcall.md` to include an `annotations` field in its JSON output.

```json
{
  "chunk_id": "chunk_0042",
  "relevant": [...],
  "annotations": [
    {
      "target": "src/utils.ts:retry",
      "kind": "function",
      "note": "Uses exponential backoff with jitter; max 5 attempts by default."
    }
  ]
}
```

**Prompt Addition:** "If you gain a high-confidence understanding of a symbol's purpose, usage, or edge cases, suggest a concise annotation for it."

### 2. Orchestrator Logic (Main Agent)

The Main Agent (executing the `rlm` Skill) needs to handle these suggestions.

**Workflow in `SKILL.md`:**
1.  Receive JSON from sub-agent.
2.  Extract `annotations` array.
3.  (Optional) Verify/Filter: "Is this annotation novel and useful?"
4.  Execute: `codemap annotate <target> "<note>"` via bash.

### 3. Closing the Loop: Scout Phase

When `pi-rlm` starts a new session, the "Scout" phase (step 2 in `SKILL.md`) currently uses `grep` or `peek`.
We enhance this to use `codemap` to get a "Semantic Map".

**Enhanced Scout:**
```bash
# Get a high-level map with existing annotations
codemap --depth 2 --annotations
```

**Result:**
```
src/auth.ts
  class AuthService
    [note: Handles legacy OAuth1.0 flow; slated for removal in v2.0]
    method validate
    method login
```
The agent *immediately* knows "This is legacy code" without reading a single line of source. It can decide to skip this file or treat it with caution.

## Feasibility Analysis

- **Technical:** Extremely high. `codemap` already supports `annotate` CLI and persistence. `pi-rlm` agents already output JSON. It's purely a prompt/process change.
- **Cost:** Low. Writing annotations is cheap.
- **Risk:** "Annotation Drift". If code changes but annotations don't, the map lies.
    - *Mitigation:* `codemap` could implement "Annotation Rot" detection (e.g., flag annotations on lines that have been modified since the annotation was written).

## Introspective Analysis

**Goal:** Extend the functional context window of LLMs.

### How this helps (The "Pro" argument)
1.  **Compression of Understanding:** A 500-line function is compressed into a 1-line semantic note. The "Effective Context Window" explodes because we are loading high-density summaries instead of low-density code.
2.  **Temporal Context Extension:** The "Context Window" usually refers to the *current* session. This extends the context window *across time* to include insights from previous sessions/agents.
3.  **Onboarding:** Helps new agents (and humans!) onboard to the repo faster.

### How this hurts (The "Con" argument)
1.  **Stale Data:** Misleading annotations are worse than no annotations. If an agent trusts a stale note, it will hallucinate.
2.  **Noise:** If agents over-annotate ("This is a function", "It returns a boolean"), the map becomes cluttered. Needs strict quality guidelines in the system prompt.
3.  **Scope Creep:** Transforms `pi-rlm` from a passive reader into an active gardener. Users might not want their repo "graffitied" by AI without explicit approval.

### Conclusion
This is a high-leverage "Meta-Context" feature. It doesn't make the RLM *reading* faster, but it makes the *need* to read less frequent. It aligns perfectly with "high quality" work by accumulating knowledge.
