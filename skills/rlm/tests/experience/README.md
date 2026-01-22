# RLM Experience Tests

Practical tests for validating the RLM (Recursive Language Model) implementation in real-world scenarios.

## Quick Start

```bash
# Run all experience tests
cd ~/projects/pi-rlm
./skills/rlm/tests/experience/01_needle_haystack.sh
./skills/rlm/tests/experience/02_codebase_analysis.sh
```

## Available Tests

### 01_needle_haystack.sh
**Tests**: Core search capability on large documents

Creates a ~500KB file with random text and a hidden "needle" phrase, then uses RLM to find it.

**Usage**:
```bash
./01_needle_haystack.sh [size_kb]
# Default: 500KB
# Example: ./01_needle_haystack.sh 1000  # 1MB haystack
```

**What it validates**:
- `grep()` can find patterns in large files
- Handle system works correctly
- `smart_chunk()` produces sensible chunks
- Basic RLM workflow functions

### 02_codebase_analysis.sh
**Tests**: Real-world codebase pattern detection

Analyzes Classroom-Connect-V2 (a ~40K line TypeScript codebase) for common patterns.

**What it validates**:
- Pattern detection across large codebases
- Multiple grep operations in sequence
- Handle chaining with `last_handle()`
- Smart code chunking

---

## UX Improvements Applied

### Handle Parsing (Fixed in this version)
Handle-consuming functions (`count()`, `expand()`, `filter_handle()`, `map_field()`, `sum_field()`, `delete_handle()`) now accept both formats:

```python
# Both work now:
result = grep('pattern')
count(result)      # ✅ Full stub: '$res1: Array(20) [...]'
count('$res1')     # ✅ Just handle name

# No more need for last_handle() workaround!
```

---

## Previous UX Friction Points (Historical)

### 1. ~~Handle String vs Handle Name~~ (FIXED)
**Status**: ✅ Resolved

Handle functions now accept both formats:
- `$res1` (handle name)
- `$res1: Array(20) [preview...]` (full stub from grep)

```python
# Clean workflow now:
result = grep('pattern')
print(f'Found {count(result)} matches')  # Just works!
```

### 2. Max matches default
**Issue**: `grep()` defaults to `max_matches=20`, which can silently truncate results.

**Recommendation**: Consider warning when results are truncated, or document this more prominently.

### 3. Smart chunking fallback
**Observation**: When code files can't use `codemap`, they fall back to `smart_text` which splits at paragraph breaks - not ideal for code.

**Recommendation**: Consider line-count based fallback for code.

---

## Test Results

### Last Run (January 21, 2026)

**Needle in Haystack** (500KB):
- ✅ Found needle at line ~3377
- Search time: 0.048s
- Chunks created: 3 (text mode)

**Codebase Analysis**:
- Services layer (66KB): Found 20 Supabase queries, 12 error handlers, 5 try-catch blocks
- Components (78KB): Found 5 useState, 18 custom hooks, 20 event handlers
- Chunking: Code detected, 1 chunk (fell back to smart_text)

---

## Adding New Tests

1. Create a new shell script following the pattern of existing tests
2. Initialize with `python3 rlm_repl.py init <file>`
3. Store state path for subsequent commands
4. Use `grep()` + `last_handle()` pattern for searches
5. Report results clearly

Template:
```bash
#!/bin/bash
set -e

RLM_SCRIPT="$HOME/projects/pi-rlm/skills/rlm/scripts/rlm_repl.py"

# Initialize
INIT_OUTPUT=$(python3 "$RLM_SCRIPT" init "$INPUT_FILE" 2>&1)
STATE_PATH=$(echo "$INIT_OUTPUT" | grep -o "\.pi/rlm_state/[^/]*/state.pkl" | head -1)

# Execute analysis
python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec -c "
grep('your pattern')
h = last_handle()
print(f'Found {count(h)} matches')
for m in expand(h, limit=5):
    print(m['snippet'][:80])
"
```
