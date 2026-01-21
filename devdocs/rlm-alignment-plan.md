# RLM Paper Alignment Plan

Aligning pi-rlm with the core patterns from [arXiv:2512.24601](https://arxiv.org/abs/2512.24601) (Recursive Language Models).

## Goals

Address these gaps between the paper's RLM design and current pi-rlm implementation:

1. **Inline `llm_query()` in REPL** — Enable programmatic sub-LLM calls from within Python code blocks
2. **Recursive depth support** — Allow sub-LLMs to spawn their own sub-LLMs (default depth limit: 3)
3. **Batch/async execution** — Add `llm_query_batch()` for parallel sub-LLM invocation
4. **Semantic chunking** — Content-aware chunking using markdown structure and tree-sitter
5. **Answer finalization signal** — Add `set_final_answer()` to mark variables for retrieval

---

## 1. Inline `llm_query()` in REPL

### Current State
Sub-LLM calls require exiting the `exec` block and invoking the `subagent` tool from the main pi session.

### Target State
The REPL provides an `llm_query(prompt: str) -> str` function that spawns a **full recursive pi agent** with its own REPL state.

### Design

```python
# Usage in exec block
answer = llm_query(f"Summarize this section: {chunk}")
classifications = [llm_query(f"Classify: {line}") for line in lines[:5]]
```

**Implementation:**
- Model: `google-antigravity/gemini-3-flash` (hardcoded, matching rlm-subcall)
- Each `llm_query()` spawns a **full pi subprocess** with the RLM skill enabled
- Sub-agent gets its own REPL state in a subdirectory
- Logging: all queries logged to `session_dir/llm_queries.jsonl`
- Timeout: 120s per query (sub-agents may do complex work)

**Query log format:**
```json
{
  "timestamp": "2026-01-21T12:30:45.123Z",
  "query_id": "q_a1b2c3d4",
  "depth": 2,
  "remaining_depth": 1,
  "prompt_preview": "Summarize this section: The authentication...",
  "prompt_chars": 4523,
  "sub_state_dir": ".pi/rlm_state/myfile-20260121/depth-1/q_a1b2c3d4",
  "response_preview": "This section describes JWT token validation...",
  "response_chars": 892,
  "duration_ms": 8500,
  "status": "success",
  "cleanup": true
}
```

**Internal mechanism:**

1. Create sub-session directory: `{session_dir}/depth-{N}/q_{uuid}`
2. Write prompt to `{sub_session_dir}/prompt.txt`
3. Spawn pi subprocess:
   ```bash
   pi --mode json -p --no-session \
      --model google-antigravity/gemini-3-flash \
      --skills rlm \
      --append-system-prompt "RLM_STATE_DIR={sub_session_dir} RLM_REMAINING_DEPTH={depth-1}" \
      < prompt.txt
   ```
4. Parse streaming JSONL output, extract final `message_end` → `message.content[].text`
5. If `cleanup=True` (default), delete `{sub_session_dir}` after extracting result
6. Return text response
7. Log to `llm_queries.jsonl`

**Parsing pi JSON output:**
```python
def _parse_pi_json_output(output: str) -> str:
    """Extract final assistant text from pi --mode json output."""
    lines = output.strip().split('\n')
    for line in reversed(lines):
        try:
            event = json.loads(line)
            if event.get('type') == 'message_end':
                message = event.get('message', {})
                content = message.get('content', [])
                texts = [c['text'] for c in content if c.get('type') == 'text' and c.get('text')]
                return '\n'.join(texts)
        except json.JSONDecodeError:
            continue
    return ""
```

**Error handling:**
- Subprocess timeout (120s): return `"[ERROR: Sub-agent timed out after 120s]"`
- Non-zero exit: return `"[ERROR: Sub-agent failed: <stderr preview>]"`
- JSON parse failure: return `"[ERROR: Failed to parse sub-agent response]"`
- Depth exceeded: return `"[ERROR: Recursion depth limit reached]"` (don't spawn)

### State Cleanup

**Default behavior:** Clean up recursive state directories after result extraction.

**Debug mode:** Pass `cleanup=False` to preserve sub-session state:
```python
result = llm_query(prompt, cleanup=False)
# Sub-session preserved at session_dir/depth-1/q_xxx/
```

**Init flag:** `--preserve-recursive-state` to disable cleanup globally:
```bash
python3 rlm_repl.py init context.txt --preserve-recursive-state
```

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py`:
  - Add `_spawn_sub_agent(prompt, depth, session_dir, cleanup) -> str`
  - Add `_parse_pi_json_output(output) -> str`
  - Add `_log_query(session_dir, entry) -> None`
  - Add `llm_query(prompt, cleanup=True) -> str` in `_make_helpers()`

---

## 2. Recursive Depth Support

### Current State
`rlm-subcall` has `tools: read` only — cannot spawn sub-agents. No depth tracking.

### Target State
Sub-LLMs can recursively spawn their own sub-LLMs up to a configurable depth (default: 3).

### Design

**Depth initialization:**
- `init` command accepts `--max-depth N` (default: 3)
- State stores both `max_depth` and `remaining_depth`
- Top-level session starts with `remaining_depth = max_depth`

**Directory structure for recursive calls:**
```
.pi/rlm_state/
└── myfile-20260121-143022/           # Root session
    ├── state.pkl
    ├── chunks/
    ├── llm_queries.jsonl
    └── depth-2/                       # First recursion level
        └── q_a1b2c3d4/               # Sub-query session
            ├── state.pkl
            └── depth-1/              # Second recursion level
                └── q_e5f6g7h8/
                    └── state.pkl
```

**Depth propagation:**
When `llm_query()` spawns a sub-agent, it passes:
- `RLM_STATE_DIR` pointing to the sub-session directory
- `RLM_REMAINING_DEPTH` decremented by 1

The sub-agent's REPL reads these from environment or system prompt injection.

**State file updates:**
```python
state = {
    "version": 3,  # Bump for new fields
    "max_depth": 3,
    "remaining_depth": 3,
    "preserve_recursive_state": False,  # Cleanup flag
    "context": {...},
    "buffers": [],
    "handles": {},
    "handle_counter": 0,
    "globals": {},
    "final_answer": None,
}
```

### Depth-0 Behavior
When `remaining_depth == 0` and `llm_query()` is called:
1. Log warning to stderr: `"Warning: Depth limit reached (remaining_depth=0)"`
2. Return error string: `"[ERROR: Recursion depth limit reached. Process without sub-queries.]"`
3. Log to `llm_queries.jsonl` with `"status": "depth_exceeded"`
4. **Do NOT spawn subprocess** — fail fast

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py`:
  - Add `--max-depth` arg to `init` command (default: 3)
  - Add `--preserve-recursive-state` flag to `init`
  - Add depth fields to state
  - Inject `remaining_depth` into exec environment
  - Check depth before spawning in `llm_query()`
- `skills/rlm/SKILL.md`: Document `--max-depth` and `--preserve-recursive-state`

---

## 3. Batch/Async Execution with `llm_query_batch()`

### Current State
Only synchronous single-query execution from within the REPL.

### Target State
`llm_query_batch(prompts: list[str]) -> tuple[list[str], dict]` executes multiple queries concurrently with structured failure metadata.

### Design

```python
# Usage
prompts = [f"Classify: {line}" for line in lines]
results, failures = llm_query_batch(prompts)
# results[i] = response string or "[ERROR: ...]"
# failures = {2: {"reason": "timeout", "attempts": 3}, ...}
```

**Function signature:**
```python
def llm_query_batch(
    prompts: list[str],
    concurrency: int = 5,      # Max concurrent requests (capped by global limit)
    max_retries: int = 3,      # Retry failed items
    cleanup: bool = True,      # Clean up sub-session state
) -> tuple[list[str], dict[int, dict]]:
```

**Global concurrency limit:**
- A process-wide semaphore limits total concurrent sub-agents to **5**
- Both `llm_query()` and `llm_query_batch()` share this semaphore
- Prevents API hammering and resource exhaustion
- Queued requests wait for semaphore availability

```python
# Global semaphore (module level)
_GLOBAL_CONCURRENCY_SEMAPHORE = threading.Semaphore(5)

def _spawn_sub_agent_with_semaphore(...):
    with _GLOBAL_CONCURRENCY_SEMAPHORE:
        return _spawn_sub_agent(...)
```

**Behavior:**
- Executes up to `min(concurrency, 5)` prompts simultaneously
- On failure: auto-retry up to `max_retries` times with exponential backoff (1s, 2s, 4s)
- Returns tuple: (results_list, failures_dict)
- Results list maintains input order
- Failed items after retries contain error string AND appear in failures dict
- All queries logged to `llm_queries.jsonl` with `batch_id` field

**Implementation:**
```python
import concurrent.futures
import threading
import uuid

_GLOBAL_CONCURRENCY_SEMAPHORE = threading.Semaphore(5)
_batch_counter = 0

def llm_query_batch(prompts, concurrency=5, max_retries=3, cleanup=True):
    global _batch_counter
    _batch_counter += 1
    batch_id = f"batch_{_batch_counter:04d}"
    
    effective_concurrency = min(concurrency, 5)  # Cap at global limit
    results = [None] * len(prompts)
    failures = {}
    
    def process_one(index, prompt):
        last_error = None
        for attempt in range(1, max_retries + 1):
            with _GLOBAL_CONCURRENCY_SEMAPHORE:
                result = _spawn_sub_agent(prompt, remaining_depth, session_dir, cleanup)
            
            if not result.startswith("[ERROR:"):
                return index, result, attempt, "success", None
            
            last_error = result
            if attempt < max_retries:
                time.sleep(1.0 * (2 ** (attempt - 1)))  # Exponential backoff
        
        return index, last_error, attempt, "failed", last_error
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
        futures = [executor.submit(process_one, i, p) for i, p in enumerate(prompts)]
        for future in concurrent.futures.as_completed(futures):
            idx, result, attempt, status, error = future.result()
            results[idx] = result
            if status == "failed":
                failures[idx] = {
                    "reason": _extract_error_reason(error),
                    "attempts": attempt,
                    "error": error
                }
            _log_query(session_dir, {
                "batch_id": batch_id,
                "batch_index": idx,
                "batch_size": len(prompts),
                "attempt": attempt,
                "status": status,
                # ... other fields
            })
    
    return results, failures
```

**Failures dict format:**
```python
{
    2: {"reason": "timeout", "attempts": 3, "error": "[ERROR: Sub-agent timed out after 120s]"},
    7: {"reason": "depth_exceeded", "attempts": 1, "error": "[ERROR: Recursion depth limit reached]"},
}
```

**Log format for batch:**
```json
{
  "timestamp": "2026-01-21T12:30:45.123Z",
  "batch_id": "batch_0001",
  "batch_index": 3,
  "batch_size": 10,
  "depth": 2,
  "remaining_depth": 1,
  "prompt_preview": "Classify: This line contains...",
  "prompt_chars": 150,
  "response_preview": "category: error",
  "response_chars": 45,
  "status": "success",
  "attempt": 1,
  "duration_ms": 8500
}
```

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py`: 
  - Add global semaphore
  - Add `llm_query_batch()` helper in `_make_helpers()`

---

## 4. Semantic Chunking

### Current State
`write_chunks()` splits by character count only.

### Target State
`smart_chunk()` function detects content type and chunks at natural boundaries.

### Design

**New function signature:**
```python
def smart_chunk(
    out_dir: str,
    target_size: int = 200_000,    # Target chars per chunk (soft limit)
    min_size: int = 50_000,        # Minimum chunk size
    max_size: int = 400_000,       # Hard maximum
    encoding: str = "utf-8",
) -> list[str]:
```

**Format detection priority:**
1. **Markdown** — Split on headers (`##`, `###` preferred as boundaries)
2. **Code** — Split on function/class boundaries using codemap
3. **JSON** — Split on top-level array elements or object keys
4. **Plain text** — Fall back to paragraph boundaries (double newline)

**Codemap detection:**
```python
def _detect_codemap() -> str | None:
    """Auto-detect codemap availability. Returns path or None."""
    # 1. Check environment variable
    env_path = os.environ.get('RLM_CODEMAP_PATH')
    if env_path and Path(env_path).exists():
        return env_path
    
    # 2. Try npx detection
    try:
        result = subprocess.run(
            ['npx', 'codemap', '--version'],
            capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return 'npx codemap'
    except:
        pass
    
    # 3. Try known paths
    known_paths = [
        Path.home() / 'projects/codemap',
        Path('/usr/local/bin/codemap'),
    ]
    for p in known_paths:
        if p.exists():
            return str(p)
    
    return None  # Fall back to character chunking for code files
```

**Detection heuristics:**
```python
def _detect_format(content: str, context_path: str) -> str:
    ext = Path(context_path).suffix.lower()
    
    # Extension-based detection
    if ext in ('.md', '.markdown', '.mdx'):
        return 'markdown'
    if ext in ('.ts', '.js', '.tsx', '.jsx', '.py', '.rs', '.cpp', '.c', '.h', '.go', '.java'):
        return 'code'
    if ext == '.json':
        return 'json'
    
    # Content-based fallback
    stripped = content.strip()
    if stripped.startswith('{') or stripped.startswith('['):
        try:
            json.loads(stripped[:50000])  # Try parsing prefix
            return 'json'
        except:
            pass
    
    if content.count('\n#') > 5:  # Multiple markdown headers
        return 'markdown'
    
    return 'text'
```

### Chunking Strategies

**Markdown chunking:**
```python
def _chunk_markdown(content: str, target_size: int, min_size: int, max_size: int) -> list[tuple[int, int]]:
    """Split markdown on header boundaries."""
    # Find all header positions with levels
    header_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
    boundaries = [(0, 0, "start")]  # (position, level, text)
    
    for match in header_pattern.finditer(content):
        level = len(match.group(1))
        text = match.group(2).strip()[:50]
        boundaries.append((match.start(), level, text))
    
    boundaries.append((len(content), 0, "end"))
    
    # Greedily accumulate until target_size, prefer level 2-3 splits
    chunks = []
    chunk_start = 0
    
    for i in range(1, len(boundaries)):
        pos, level, _ = boundaries[i]
        chunk_len = pos - chunk_start
        
        if chunk_len >= target_size or (chunk_len >= min_size and level <= 3):
            # Good split point
            chunks.append((chunk_start, pos))
            chunk_start = pos
        elif chunk_len >= max_size:
            # Forced split even at bad boundary
            chunks.append((chunk_start, pos))
            chunk_start = pos
    
    if chunk_start < len(content):
        chunks.append((chunk_start, len(content)))
    
    return chunks
```

**Code chunking (via codemap):**
```python
def _chunk_code(content: str, context_path: str, target_size: int, min_size: int, max_size: int) -> list[tuple[int, int]]:
    """Split code on function/class boundaries using codemap."""
    codemap_cmd = _detect_codemap()
    if not codemap_cmd:
        return _chunk_text(content, target_size, min_size, max_size)
    
    try:
        if codemap_cmd.startswith('npx'):
            cmd = ['npx', 'codemap', '-o', 'json', context_path]
        else:
            cmd = [codemap_cmd, '-o', 'json', context_path]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return _chunk_text(content, target_size, min_size, max_size)
        
        # Parse codemap output for symbol ranges
        data = json.loads(result.stdout)
        boundaries = _extract_symbol_boundaries(data, content)
        return _build_chunks_from_boundaries(boundaries, content, target_size, min_size, max_size)
    except Exception:
        return _chunk_text(content, target_size, min_size, max_size)
```

**JSON chunking:**
```python
def _chunk_json(content: str, target_size: int, min_size: int, max_size: int) -> list[tuple[int, int]]:
    """Split JSON on top-level array elements or object keys."""
    stripped = content.strip()
    
    if stripped.startswith('['):
        return _chunk_json_array(content, target_size, min_size, max_size)
    elif stripped.startswith('{'):
        return _chunk_json_object(content, target_size, min_size, max_size)
    else:
        return [(0, len(content))]

def _chunk_json_array(content: str, target_size: int, min_size: int, max_size: int) -> list[tuple[int, int]]:
    """Split JSON array into chunks of elements."""
    data = json.loads(content)
    if not isinstance(data, list) or len(data) == 0:
        return [(0, len(content))]
    
    # Estimate elements per chunk
    avg_element_size = len(content) / len(data)
    elements_per_chunk = max(1, int(target_size / avg_element_size))
    
    chunks = []
    for i in range(0, len(data), elements_per_chunk):
        chunk_data = data[i:i + elements_per_chunk]
        chunk_text = json.dumps(chunk_data, indent=2)
        # Store as (start_index, end_index) in element space
        chunks.append((i, min(i + elements_per_chunk, len(data))))
    
    return chunks  # Note: returns element indices, not char positions
```

**Text chunking (fallback):**
```python
def _chunk_text(content: str, target_size: int, min_size: int, max_size: int) -> list[tuple[int, int]]:
    """Split plain text on paragraph boundaries."""
    para_pattern = re.compile(r'\n\n+')
    boundaries = [0] + [m.end() for m in para_pattern.finditer(content)] + [len(content)]
    
    chunks = []
    chunk_start = 0
    
    for boundary in boundaries[1:]:
        chunk_len = boundary - chunk_start
        if chunk_len >= target_size or boundary == len(content):
            chunks.append((chunk_start, boundary))
            chunk_start = boundary
    
    return chunks
```

### Enhanced Manifest

```json
{
  "session": "myfile-20260121-143022",
  "context_file": "/path/to/file.md",
  "total_chars": 850000,
  "total_lines": 18500,
  "format": "markdown",
  "chunking_method": "smart",
  "target_size": 200000,
  "codemap_available": true,
  "chunk_count": 5,
  "chunks": [
    {
      "id": "chunk_0000",
      "file": "chunk_0000.txt",
      "start_char": 0,
      "end_char": 185000,
      "start_line": 1,
      "end_line": 4200,
      "format": "markdown",
      "split_reason": "header_level_2",
      "boundaries": [
        {"type": "heading", "level": 2, "text": "Authentication", "line": 1},
        {"type": "heading", "level": 3, "text": "JWT Tokens", "line": 450}
      ],
      "preview": "## Authentication\n\nThis module handles...",
      "hints": {"section_headers": ["Authentication", "JWT Tokens"]}
    }
  ]
}
```

### Implementation Phases

**Phase 3a: Markdown chunking (no external deps)**
- Implement `_detect_format()`, `_chunk_markdown()`, `_chunk_text()`
- Add `smart_chunk()` wrapper function
- Test with markdown documentation files
- **Estimate: 2-3 hours**

**Phase 3b: Code chunking (codemap integration)**
- Add `_detect_codemap()` with auto-detect → env var → fallback
- Add `_chunk_code()` with codemap CLI call
- Graceful fallback if codemap unavailable
- Test with TypeScript/Python source files
- **Estimate: 3-4 hours**

**Phase 3c: JSON chunking**
- Implement `_chunk_json_array()` and `_chunk_json_object()`
- Handle edge cases (nested structures, minified JSON)
- **Estimate: 1-2 hours**

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py`: Add `smart_chunk()` and helper functions
- Consider `skills/rlm/scripts/chunkers.py` if implementation exceeds 200 lines

### Dependencies
- `codemap` CLI (optional, auto-detected, graceful fallback)
- `RLM_CODEMAP_PATH` env var for explicit path
- No new Python package dependencies

---

## 5. Answer Finalization Signal

### Current State
No explicit signal for when the root agent should retrieve a final answer from the REPL.

### Target State
`set_final_answer(value)` marks a value as the final answer, signaling completion. **Root-only** — sub-sessions cannot set final answers that propagate up.

### Design

```python
# Usage in exec block
final_pairs = [(1, 2), (3, 4), (5, 6)]
set_final_answer(final_pairs)  # Store directly

# Check if set
if has_final_answer():
    print("Answer ready")

# Read back (rarely needed in exec, but available)
answer = get_final_answer()
```

**Behavior:**
- Stores the answer in state under `final_answer` key
- Does NOT terminate execution (just a signal)
- Subsequent calls overwrite previous answer
- Value must be JSON-serializable (for retrieval via CLI)
- `status` command shows if final answer is set
- **Root-only**: In sub-sessions, `set_final_answer()` stores locally but doesn't propagate

**Helper functions:**
```python
def set_final_answer(value: Any) -> None:
    """Mark a value as the final answer."""
    # Validate JSON-serializability
    try:
        json.dumps(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"Final answer must be JSON-serializable: {e}")
    
    state_ref["final_answer"] = {
        "set_at": datetime.utcnow().isoformat() + "Z",
        "value": value,
    }
    print(f"Final answer set (type: {type(value).__name__})")

def has_final_answer() -> bool:
    """Check if a final answer has been set."""
    return state_ref.get("final_answer") is not None

def get_final_answer() -> Any:
    """Retrieve the final answer value (or None if not set)."""
    fa = state_ref.get("final_answer")
    return fa["value"] if fa else None
```

**New CLI command:**
```bash
python3 rlm_repl.py --state ... get-final-answer
```

**Output format (JSON to stdout):**
```json
{"set": true, "value": [[1, 2], [3, 4], [5, 6]], "set_at": "2026-01-21T12:30:45.123Z"}
```

Or if not set:
```json
{"set": false, "value": null, "set_at": null}
```

**Status output update:**
```
RLM REPL status
  State file: .pi/rlm_state/myfile-20260121-143022/state.pkl
  Session directory: .pi/rlm_state/myfile-20260121-143022
  Context path: /path/to/file.txt
  Context chars: 850,000
  Buffers: 3
  Handles: 5
  Persisted vars: 12
  Final answer: SET (type: list, length: 3)
```

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py`:
  - Add `set_final_answer()`, `has_final_answer()`, `get_final_answer()` helpers
  - Add `cmd_get_final_answer()` command handler
  - Update `cmd_status()` to show final answer info
- `skills/rlm/SKILL.md`: Document finalization pattern

---

## Implementation Order

| Phase | Feature | Complexity | Dependencies | Time Est. |
|-------|---------|------------|--------------|-----------|
| 1a | `llm_query()` with full pi subprocess | High | None | 4-5h |
| 1b | Depth tracking + recursive state dirs | Medium | 1a | 2-3h |
| 1c | State cleanup + debug flag | Low | 1b | 1h |
| 1d | `set_final_answer()` | Low | None | 1h |
| 2 | `llm_query_batch()` with global semaphore | Medium | 1a | 2-3h |
| 3a | `smart_chunk()` - Markdown | Medium | None | 2-3h |
| 3b | `smart_chunk()` - Code (codemap) | High | codemap | 3-4h |
| 3c | `smart_chunk()` - JSON | Low | None | 1-2h |

**Total estimate: 17-23 hours**

### Phase 1: Core Sub-LLM Support (~8-10 hours)
- Implement `_spawn_sub_agent()` with full pi subprocess
- Implement `_parse_pi_json_output()` for extracting results
- Implement `_log_query()` for JSONL logging
- Add `llm_query()` helper with global semaphore
- Add `--max-depth` to init, depth tracking in state
- Implement recursive state directory structure
- Add `--preserve-recursive-state` flag
- Add `set_final_answer()`, `has_final_answer()`, `get_final_answer()`
- Add `get-final-answer` CLI command
- Update `status` command
- Update SKILL.md documentation

### Phase 2: Parallel Execution (~2-3 hours)
- Add global concurrency semaphore (5)
- Implement `llm_query_batch()` with ThreadPoolExecutor
- Add retry logic with exponential backoff
- Return structured failures dict
- Add batch logging with batch_id
- Test with 10-20 parallel queries

### Phase 3: Semantic Chunking (~6-9 hours)
- 3a: Implement markdown chunking (2-3 hours)
- 3b: Integrate codemap for code chunking with auto-detect (3-4 hours)
- 3c: Add JSON chunking (1-2 hours)
- Enhanced manifest generation
- Testing across file types

---

## Testing Strategy

### Unit Tests (`skills/rlm/tests/`)

**test_llm_query.py:**
- Mock subprocess to return canned JSON responses
- Test pi JSON output parsing
- Test timeout handling
- Test error response formatting
- Test JSONL logging format
- Test recursive state directory creation/cleanup

**test_depth.py:**
- Verify depth initialization from `--max-depth`
- Verify `remaining_depth` injection into env
- Verify depth-0 returns error without spawning
- Verify recursive directory structure

**test_batch.py:**
- Mock subprocess, verify concurrency limit (5)
- Test global semaphore sharing
- Test retry logic (inject failures)
- Test structured failures dict
- Test partial results ordering

**test_smart_chunk.py:**
- Test format detection heuristics
- Test markdown boundary extraction
- Test codemap auto-detection
- Test JSON array/object splitting
- Test text paragraph splitting
- Test graceful fallback when codemap unavailable

**test_final_answer.py:**
- Test set/get cycle
- Test JSON-serializability validation
- Test `get-final-answer` CLI output format
- Test status display

### Integration Tests

**test_rlm_workflow.py:**
- Full init → query → chunk → synthesize cycle
- Uses small test fixtures (< 10KB)
- Mocked sub-LLM calls

**test_recursive_depth.py:**
- Actual depth-2 recursion with real pi calls (slow test)
- Verify state cleanup
- Verify `--preserve-recursive-state` keeps files

### Manual Testing Checklist
- [ ] Large markdown file (e.g., concatenated docs > 500KB)
- [ ] TypeScript codebase (codemap's own src/)
- [ ] Large JSON file (API response dump > 1MB)
- [ ] Recursive depth-2 query scenario
- [ ] Batch of 20 queries with 2 intentional failures
- [ ] `get-final-answer` retrieval from main pi session
- [ ] Verify recursive state cleanup (default)
- [ ] Verify `--preserve-recursive-state` keeps directories

---

## Migration Notes

### State Version Bump
State file version bumps from 2 → 3. The REPL handles version migration:

```python
def _migrate_state(state: dict) -> dict:
    version = state.get("version", 1)
    
    if version < 3:
        state["version"] = 3
        state.setdefault("max_depth", 3)
        state.setdefault("remaining_depth", 3)
        state.setdefault("preserve_recursive_state", False)
        state.setdefault("final_answer", None)
    
    return state
```

### Backward Compatibility
- Existing sessions (version 2) auto-migrate on first load
- `llm_query()` uses default depth (3) for migrated sessions
- Old `write_chunks()` remains available alongside `smart_chunk()`

---

## Future Enhancement: Unified Read Tool

As noted in the discussion, `llm_query()` could eventually replace the default `read` tool, enabling:
- Quick inline tasks (classify, normalize, yes/no)
- Deep analysis of unlimited context via recursive RLM

This would make the RLM pattern transparent to the user — just "read" any file regardless of size, and the system handles chunking/recursion automatically.

**Deferred to future version** — current implementation keeps `llm_query()` as an explicit REPL function.

---

## Open Questions (Resolved)

| Question | Resolution |
|----------|------------|
| Sub-LLM architecture | Full pi agent with own REPL state (Option B) |
| Sub-LLM model | `google-antigravity/gemini-3-flash` (fixed) |
| Prompt control | Caller controls full prompt; logging provides visibility |
| Depth limit behavior | Return error string, don't spawn (fail fast) |
| Depth tracking | Top-level via `--max-depth`, auto-decrement per recursion |
| Recursive state | Cleanup by default; `--preserve-recursive-state` for debugging |
| Result flow | Parse pi `--mode json` output for `message_end` event |
| Batch failure handling | Return tuple (results, failures_dict) with structured metadata |
| Global concurrency | 5 concurrent sub-agents max, shared semaphore |
| Codemap detection | Auto-detect → `RLM_CODEMAP_PATH` env → fallback to char chunking |
| `set_final_answer()` | Root-only, signal only, must be JSON-serializable |
| State migration | Auto-migrate version 2 → 3 on load |

---

## Success Criteria

1. **llm_query()**: Can spawn recursive pi agents; queries logged to JSONL; sub-state cleaned up by default
2. **Depth**: Depth-2 recursion works with nested state dirs; depth-0 returns error without spawning
3. **Batch**: 20 parallel queries respect global concurrency (5); failed items retry 3x; structured failures returned
4. **Smart chunking**: Markdown chunks on headers; code chunks on functions (with codemap); JSON splits on elements; graceful fallback
5. **Finalization**: `set_final_answer()` persists; `get-final-answer` returns JSON; `status` shows answer info
