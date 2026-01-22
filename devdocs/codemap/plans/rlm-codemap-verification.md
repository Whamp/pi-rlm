# RLM Plan: Verification & Quality (Codemap Integration)

**Status:** Draft
**Related:** Option 6 from Brainstorm
**Target System:** pi-rlm (running on top of codemap)

## Overview

This plan details the implementation of a "Verification Phase" in the RLM workflow. By integrating `codemap`'s static analysis capabilities into `pi-rlm`, we can verify sub-agent claims (hallucination checks) and proactively identify breakage risks (impact analysis) without reading every file in the codebase.

## Core Features

### 1. Hallucination Check (`verify_call`)

**Problem:** RLM sub-agents reading a single chunk may hallucinate function calls or invent relationships that don't exist in the broader codebase.
**Solution:** Use `codemap calls` to validate claims of structural dependencies.

**Workflow:**
1.  **Extraction:** RLM sub-agent outputs a "Claim" in its JSON:
    ```json
    {
      "relevant": [...],
      "claims": [
        {"type": "call", "source": "processPayment", "target": "validateUser"}
      ]
    }
    ```
2.  **Verification:** The `rlm_repl.py` script exposes a verification helper:
    ```python
    def verify_claim(source_symbol, target_symbol, relationship="call"):
        # Runs: codemap calls <source_symbol> -o json
        # Checks if <target_symbol> appears in the output
        return True/False
    ```
3.  **Feedback:** If verification fails, the Root LLM discards the claim or flags it as "Dynamic/Unverified" in the final synthesis.

### 2. Impact Analysis (`check_impact`)

**Problem:** When refactoring a base class or interface, RLM agents operating on partial context might miss downstream implementations that break.
**Solution:** Use `codemap subtypes` (and `find-refs`) to enumerate all dependents.

**Workflow:**
1.  **Trigger:** User asks to "Refactor `BaseHandler` interface".
2.  **Scout:** Root LLM calls `codemap subtypes BaseHandler`.
3.  **Expansion:** `codemap` returns 50 files implementing that interface.
4.  **Planning:** RLM adds these 50 files to the "Verification List". Even if they weren't in the initial search results, they are forcibly checked (lightweight pass) to ensure the refactor is compatible.

## Implementation Details

### A. `rlm_repl.py` Enhancements

Add a `codemap` wrapper class to the persistent REPL.

```python
class CodemapClient:
    def __init__(self, repo_root):
        self.repo_root = repo_root
        self.bin = "./dist/codemap" # or resolved path

    def calls(self, symbol):
        # Executes: codemap calls <symbol> -o json
        return json.loads(stdout)

    def subtypes(self, symbol):
        # Executes: codemap subtypes <symbol> -o json
        return json.loads(stdout)

    def verify_connection(self, source, target):
        data = self.calls(source)
        # Scan data['refs']['items'] for target
        pass
```

### B. `rlm-subcall` Prompt Update

Update the system prompt to encourage structured claim reporting.

```markdown
If you identify a key relationship (e.g., function A calls function B), explicit state it:
<claim source="A" target="B" type="call" />
```
*Note: We can also rely on the Root LLM to extract these claims from natural text during the synthesis phase, reducing sub-agent complexity.*

### C. Skill Workflow Update (`SKILL.md`)

Add a "Verification" step to the RLM procedure.

```markdown
5. **Verification (Optional)**
   If the user request involves refactoring or structural analysis:
   a. Identify key symbols involved (e.g., the class being changed).
   b. Run `codemap subtypes <symbol>` to find all inheritors.
   c. Run `codemap find-refs <symbol>` to find all usages.
   d. Ensure your final answer addresses compatibility with these files.
```

## Feasibility & Limitations

### Dependencies
- Requires `codemap` to be installed and built in the target repo.
- **Language Support Constraint:** `codemap` currently only supports reference tracking for **TypeScript/JavaScript**.
  - **Result:** This feature will fail or return empty results for Python/Rust/C++ repos.
  - **Mitigation:** The `verify_claim` function must check the file extension/language capabilities and return "Skipped (Unsupported Language)" rather than "False".

### Performance
- `codemap` is fast (cached), but spawning a CLI process for every single claim is slow.
- **Optimization:** Batch verifications or only verify *critical* disputed claims (e.g., where two sub-agents disagree).

---

## Introspective Analysis

**Goal:** Extend the functional context window of LLMs for effective, high-quality long-context work.

**How this plan moves us CLOSER:**
1.  **Safety at Scale:** The biggest danger of "long context" agents is that they confidently break code they can't see. By explicitly traversing the dependency graph using `codemap`, we verify the "invisible" parts of the codebase. This effectively makes the context window "structurally infinite" even if the LLM's token window is finite.
2.  **Hallucination Dampening:** RLM's "chunked" nature makes it prone to losing the thread between chunks. Verification acts as a hard logical constraint, grounding the stochastic LLM generation in static analysis reality.
3.  **High-Quality Refactors:** A refactor that misses one subclass is a broken refactor. This feature ensures 100% coverage of structurally relevant files, which is a hallmark of "high quality" work.

**How this plan might move us FURTHER AWAY (Risks):**
1.  **False Confidence:** If `codemap` misses a dynamic reference (e.g., `eval` or obscure dependency injection), the RLM agent might confidently assert "No impact" because the tool said so. Static analysis is never perfect. We trade "LLM uncertainty" for "Tool certainty," which can be dangerous if the tool is wrong.
2.  **Language Friction:** Since this only works well for TS/JS today, implementing it might create a confusing user experience ("Why does this work for my Node app but not my Python script?"). It fragments the "Universal RLM" promise.
3.  **Complexity Overhead:** Adding a second "truth source" (Codemap) alongside the "text source" (RLM chunks) complicates the prompt. The Root LLM must decide which truth to believe when they conflict.

**Verdict:**
This is a **high-value multiplier** for TS/JS codebases, effectively solving the "blind refactor" problem. However, its lack of multi-language support restricts it to a specialized role rather than a core RLM primitive. It should be implemented as an **optional skill enhancement**, not a mandatory step in the loop.
