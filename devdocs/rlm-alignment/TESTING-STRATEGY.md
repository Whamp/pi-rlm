# RLM Testing Strategy

## Status: ✅ COMPLETED (January 21, 2026)

All four testing phases have been implemented and validated.

| Phase | Status | Commit |
|-------|--------|--------|
| 1. Fix Handle UX | ✅ Complete | `d6e5542` |
| 2. Smart Chunking Tests | ✅ Complete | `3dc20df` |
| 3. LLM Integration Tests | ✅ Complete | `57b6788` |
| 4. Static Analysis Comparison | ✅ Complete | `195e5dc` |

**Test Suite**: 260 passed, 8 skipped | **Experience Tests**: 6 scripts, all passing

---

## Executive Summary

This document outlines practical approaches to test the RLM implementation beyond unit tests. The goal is to move from "theory and plan" to "testing the actual experience" of using recursive language model capabilities.

---

## 1. Benchmark Opportunities

### 1.1 Established Large-Context Benchmarks

| Benchmark | Description | RLM Fit | Difficulty |
|-----------|-------------|---------|------------|
| **SCROLLS** | Long document QA (contract, literature, gov docs) | ⭐⭐⭐ Excellent | Medium |
| **Needle in a Haystack** | Find specific info buried in large context | ⭐⭐⭐ Excellent | ✅ Implemented |
| **QuALITY** | Long document multiple choice QA | ⭐⭐ Good | Medium |
| **NarrativeQA** | Story comprehension and summarization | ⭐⭐ Good | Medium |
| **GovReport** | Long government report summarization | ⭐⭐⭐ Excellent | Easy |

**Implemented**: Needle in a Haystack test (`01_needle_haystack.sh`)
- 500KB haystack with hidden needle
- Found in 0.048s at line ~3377
- Validates grep + handle system

### 1.2 Custom Benchmark: Codebase Archaeology ✅ Implemented

| Task | Status | Test File |
|------|--------|-----------|
| **Pattern detection** | ✅ | `02_codebase_analysis.sh` |
| **Static analysis comparison** | ✅ | `06_comparison.sh` |

---

## 2. Classroom-Connect-V2 Testing

The project has **~1.7MB** of TypeScript/TSX code across **7,678 files** with **40,624 lines** of application code. This is a perfect real-world test case.

### 2.1 Concrete Test Scenarios

#### Scenario A: Full Codebase Review
**Goal**: Analyze entire codebase for code quality issues

```bash
# Step 1: Concatenate all source files
cd ~/projects/Classroom-Connect-V2
find . -name "*.ts" -o -name "*.tsx" | \
  grep -v node_modules | \
  xargs cat > /tmp/classroom-connect-full.ts

# Step 2: Initialize RLM session  
python3 ~/skills/rlm/scripts/rlm_repl.py init /tmp/classroom-connect-full.ts

# Step 3: Smart chunk by code structure
python3 ~/skills/rlm/scripts/rlm_repl.py --state $STATE exec -c "
paths = smart_chunk(session_dir / 'chunks', target_size=100000)
print(f'Created {len(paths)} chunks')
"

# Step 4: Batch query each chunk for issues
# (delegated to llm_query_batch with code review prompts)
```

**Expected outcome**: List of issues categorized by severity, with file/line references

#### Scenario B: Cross-file Pattern Detection
**Goal**: Find all usages of a specific pattern across the codebase

Example patterns:
- `supabase.from()` calls outside services (RLS violation)
- `useEffect` without cleanup
- Hardcoded strings that should be constants
- API calls without error handling

**RLM advantage**: Handle system lets you grep across 1.7MB without loading it all

```python
# Example workflow
result = grep('supabase\\.from\\(')
print(f"Found {count(result)} usages")
for match in expand(result, limit=20):
    # Each match has file context via character position
    print(f"Line {match['line_num']}: {match['match'][:80]}")
```

#### Scenario C: Documentation Generation
**Goal**: Generate comprehensive docs from code

Tasks:
1. Extract all public API functions with JSDoc
2. Map component hierarchy
3. Generate service layer documentation
4. Document database schema from migrations (300KB SQL)

**Unique RLM value**: Can process the 300KB of migration SQL in chunks, extracting schema evolution

#### Scenario D: Test Coverage Analysis  
**Goal**: Identify untested code paths

Approach:
1. Extract all function signatures from source
2. Extract all function references from tests
3. Find the delta (untested functions)

This requires correlating information across two large file sets - a perfect recursive decomposition task.

### 2.2 Test Metrics to Capture

| Metric | Measurement |
|--------|-------------|
| **Token efficiency** | Handles vs raw data: tokens used to find same info |
| **Accuracy** | Compare RLM findings to manual review |
| **Latency** | Time to complete full codebase analysis |
| **Depth usage** | Did recursive calls improve results? |
| **Chunk quality** | Were smart chunk boundaries semantically meaningful? |

---

## 3. Experience Testing (RLM vs Standard)

### 3.1 A/B Comparison Tasks

Design tasks that explicitly compare:
- **Condition A**: Single LLM call with truncated context
- **Condition B**: RLM with chunking + sub-LLMs

| Task | Expected Winner | Why |
|------|-----------------|-----|
| "Summarize this 500KB log file" | RLM | Can't fit in context otherwise |
| "Find the bug in this 50-line function" | Standard | RLM overhead not worth it |
| "Review this 200KB markdown doc" | RLM | Smart markdown chunking preserves structure |
| "Answer a question about a 10KB file" | Standard | Fits in context |
| "Find cross-file dependencies in 50 files" | RLM | Aggregation across chunks |

### 3.2 UX Friction Point Testing

Document these experiences:

1. **Session management friction**
   - How easy is it to resume a session?
   - Is the state file path obvious?
   - Error messages when session is stale?

2. **Chunk boundary quality**
   - Do smart chunks break at sensible points?
   - Are there edge cases where chunks split mid-concept?
   - Is the manifest useful for understanding chunks?

3. **Handle ergonomics**
   - Is the `$res1` syntax intuitive?
   - Is `last_handle()` chaining discoverable?
   - Are error messages helpful when handles expire?

4. **LLM query DX**
   - Is the subagent spawn time acceptable?
   - Are error messages from failed queries actionable?
   - Is batching worth the complexity?

### 3.3 Practical Limits Testing

| Parameter | Test Range | Question |
|-----------|------------|----------|
| **Content size** | 100KB → 10MB | Where does performance degrade? |
| **Chunk count** | 5 → 500 | Where does synthesis quality drop? |
| **Recursion depth** | 1 → 5 | When does deeper recursion help? |
| **Batch concurrency** | 1 → 5 | Token efficiency vs latency tradeoff |
| **Grep result size** | 10 → 1000 | When does handle expansion hurt? |

---

## 4. Comparison Scenarios (Impossible Without RLM)

These tasks are specifically designed to be impossible/impractical with standard approaches:

### 4.1 The "Impossible" Tasks

#### Task 1: Full Repo Security Audit
- **Input**: All 40K lines of Classroom-Connect-V2
- **Query**: "Find all potential security vulnerabilities"
- **Why impossible otherwise**: No single context window can hold 1.7MB

#### Task 2: Cross-Reference Analysis
- **Input**: All source code + all migrations (300KB SQL) + all docs
- **Query**: "Are all database columns referenced in the application code?"
- **Why impossible otherwise**: Requires correlating 3 different large sources

#### Task 3: Comprehensive Refactoring Plan
- **Input**: Full codebase
- **Query**: "Create a refactoring plan to remove all deprecated patterns"
- **Why impossible otherwise**: Needs global view to ensure consistency

#### Task 4: Multi-File Bug Hunt
- **Input**: 5 related files (total 50KB) + 200KB of logs
- **Query**: "Why is the user seeing error X?" 
- **Why impossible otherwise**: Context too large, but all pieces needed

### 4.2 Degradation Testing

Compare output quality when artificially limiting RLM:
1. Full RLM (depth=3, smart chunking, batch queries)
2. RLM with depth=1 only
3. RLM with basic chunking (no smart boundaries)
4. Truncated single-context (first N tokens only)

---

## 5. Implementation Plan ✅ COMPLETED

### Phase 1: Quick Validation ✅
1. ✅ Created needle-in-haystack test (`01_needle_haystack.sh`)
2. ✅ Ran against Classroom-Connect source (`02_codebase_analysis.sh`)
3. ✅ Verified smart chunking (`03_smart_markdown.sh`, `04_smart_json.sh`)

### Phase 2: Real-World Testing ✅
1. ✅ Full codebase review on Classroom-Connect (584KB, 18K lines)
2. ✅ Compared findings to oxlint output (`06_comparison.sh`)
3. ✅ Documented UX friction points (handle parsing issue fixed)

### Phase 3: Benchmark Suite ✅
1. ✅ Formalized test scripts in `tests/experience/`
2. ✅ Added metrics (timing, counts, validation)
3. ✅ Created reproducible scripts (6 total)

### Phase 4: Comparison Study ✅
1. ✅ Designed RLM vs static analysis comparison
2. ✅ Ran both on same codebase
3. ✅ Quantified: RLM 92 findings, oxlint 245 issues (complementary)

---

## 6. Quick-Start Test Scripts

### Test 1: Needle in Haystack

```bash
#!/bin/bash
# Create test data
NEEDLE="The secret code is: XYZZY-12345"
HAYSTACK_FILE="/tmp/needle-test.txt"

# Generate 500KB of filler
python3 -c "
import random
words = ['lorem', 'ipsum', 'dolor', 'sit', 'amet', 'consectetur', 'adipiscing']
for _ in range(50000):
    print(' '.join(random.choices(words, k=random.randint(5, 15))))
" > $HAYSTACK_FILE

# Insert needle in middle
head -c 250000 $HAYSTACK_FILE > /tmp/nh1.txt
echo "$NEEDLE" >> /tmp/nh1.txt
tail -c +250001 $HAYSTACK_FILE >> /tmp/nh1.txt
mv /tmp/nh1.txt $HAYSTACK_FILE

echo "Haystack size: $(wc -c < $HAYSTACK_FILE) bytes"
echo "Needle inserted at ~middle"

# Initialize RLM session
STATE=$(python3 ~/skills/rlm/scripts/rlm_repl.py init $HAYSTACK_FILE 2>&1 | grep "state:" | awk '{print $2}')
echo "State: $STATE"

# Search for needle
python3 ~/skills/rlm/scripts/rlm_repl.py --state $STATE exec -c "
result = grep('secret code is')
print(f'Matches: {count(result)}')
for m in expand(result):
    print(f'Found: {m}')
"
```

### Test 2: Classroom-Connect Quick Analysis

```bash
#!/bin/bash
cd ~/projects/Classroom-Connect-V2

# Concatenate all services (small subset)
find src/services -name "*.ts" -exec cat {} \; > /tmp/cc-services.ts
echo "Services file: $(wc -c < /tmp/cc-services.ts) bytes"

# Initialize
STATE=$(python3 ~/skills/rlm/scripts/rlm_repl.py init /tmp/cc-services.ts 2>&1 | grep "state:" | awk '{print $2}')

# Analyze
python3 ~/skills/rlm/scripts/rlm_repl.py --state $STATE exec -c "
# Check for patterns
for pattern in ['.from\\(', 'throw new Error', 'try.*catch', 'async function']:
    matches = grep(pattern)
    print(f'{pattern}: {count(matches)} occurrences')
"
```

---

## 7. Success Criteria

| Criterion | Target |
|-----------|--------|
| Needle in Haystack accuracy | 100% (finds all needles) |
| Codebase review finds real issues | ≥5 valid findings |
| Smart chunking preserves semantics | Manual inspection passes |
| Batch queries complete | <2 min for 10 chunks |
| Handle system saves tokens | ≥50% reduction vs raw |
| UX friction documented | ≥3 improvement suggestions |

---

## Appendix: Project Stats

**Classroom-Connect-V2**:
- TypeScript/TSX files: 7,678
- Lines of code: ~40,624
- Total size: ~1.7MB
- Migrations: 300KB SQL
- Tests: Unit, Integration, E2E, Performance

**RLM Implementation**:
- Main script: 2,555 lines
- Tests: 254 across 8 files
- Examples: 5 documented workflows

---

## 8. Lessons Learned from Initial Testing

### 8.1 UX Friction Points Discovered

#### Issue 1: ~~Handle String vs Handle Name~~ ✅ FIXED
**Status**: Resolved - `_parse_handle()` now extracts handle name from full stub.

Handle functions accept both formats:
```python
result = grep('pattern')
count(result)  # ✅ Works with full stub '$res1: Array(20) [...]'
count('$res1') # ✅ Also works with just handle name
```

#### Issue 2: Default max_matches Truncation
**Problem**: `grep()` defaults to `max_matches=20`, silently truncating results.

**Impact**: Users may miss matches without realizing it.

**Recommendation**: Add a warning when results are truncated:
```
$res1: Array(20+) [...]  # "+" indicates truncation
# OR
"Note: showing first 20 of 47 matches"
```

#### Issue 3: Code Chunking Fallback
**Observation**: Without `codemap` binary, code files fall back to `smart_text` (paragraph-based) splitting.

**Impact**: 66KB TypeScript file produced only 1 chunk instead of semantic function boundaries.

**Recommendation**: Add a line-count based fallback for code that keeps functions together.

### 8.2 What Worked Well

1. **Handle system**: Token-efficient exploration without loading full content
2. **Session persistence**: State files survived between test runs
3. **grep() performance**: 0.048s to search 500KB file
4. **Snippet context**: `snippet` field provides useful surrounding context

### 8.3 Test Coverage Gaps

| Area | Status | Notes |
|------|--------|-------|
| Needle in Haystack | ✅ Tested | Works reliably |
| Pattern counting | ✅ Tested | Works with `last_handle()` pattern |
| Smart markdown chunking | ❌ Not tested | Need markdown test file |
| Smart JSON chunking | ❌ Not tested | Need JSON test file |
| Smart code chunking | ⚠️ Partial | Needs codemap binary |
| `llm_query()` | ❌ Not tested | Requires actual LLM calls |
| `llm_query_batch()` | ❌ Not tested | Requires actual LLM calls |
| Recursive depth | ❌ Not tested | Requires LLM integration |
| Answer finalization | ❌ Not tested | Need end-to-end test |

### 8.4 Next Testing Priorities

1. **Fix handle string UX** - Users will hit this immediately
2. **Add markdown chunking test** - Verify header-based splitting
3. **Add JSON chunking test** - Verify array/object splitting  
4. **Create LLM integration test** - Test actual `llm_query()` with mocked subagent
5. **Benchmark depth vs performance** - When does recursion help?
