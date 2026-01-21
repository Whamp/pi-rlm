# RLM Paper Alignment Plan

Aligning pi-rlm with the core patterns from [arXiv:2512.24601](https://arxiv.org/abs/2512.24601) (Recursive Language Models).

---

## Implementation Protocol

### Phase Sizing
Each phase is scoped for **~100k-125k tokens** of agent work. This allows:
- Complete implementation of the feature
- Testing and validation
- Documentation updates
- Buffer for debugging

### Phase Workflow
1. **Read prior phase diff** (if not Phase 1): `git diff HEAD~1` or `git show HEAD`
2. **Implement** the phase deliverables
3. **Validate** using the defined validation steps
4. **Append to `progress-notes.txt`** with:
   - Phase number and title
   - What was implemented
   - Test results
   - Any deviations from plan
   - Timestamp
5. **Commit** with message format: `feat(rlm): Phase N - <title>`

### Progress Notes Format
```markdown
## Phase N: <Title>
**Completed:** <ISO timestamp>

### Implemented
- <bullet list of changes>

### Validation Results
- <test outputs, manual checks>

### Notes
- <deviations, decisions, blockers resolved>
```

---

## Testing Strategy

### Test Structure
```
skills/rlm/tests/
├── conftest.py              # Shared fixtures (mock subprocess, temp sessions)
├── test_phase1_llm_query.py # Unit + integration tests for Phase 1
├── test_phase2_depth.py     # Depth tracking tests
├── test_phase3_batch.py     # Batch execution tests
├── test_phase4_finalize.py  # Finalization signal tests
├── test_phase5_markdown.py  # Markdown chunking tests
├── test_phase6_code.py      # Code chunking tests
├── test_phase7_json.py      # JSON chunking tests
└── test_integration.py      # End-to-end workflow tests
```

### Test Categories Per Phase

Each phase produces three types of tests:

1. **Unit Tests** — Fast, isolated, mock external dependencies
   - Test individual functions (`_parse_pi_json_output`, `_detect_format`, etc.)
   - Mock subprocess calls to avoid actual LLM invocations
   - Run in <5 seconds

2. **Integration Tests** — Slower, test real interactions
   - Actually spawn pi subprocess (marked with `@pytest.mark.slow`)
   - Test state persistence across invocations
   - Verify file I/O (chunks, manifests, logs)

3. **Goal-Alignment Tests** — Verify paper requirements are met
   - Named `test_goal_*` to be explicit
   - Each maps to a specific paper requirement
   - Include docstrings citing the requirement

### Goal-Alignment Test Mapping

| Goal | Test | Validates |
|------|------|-----------|
| Inline `llm_query()` in REPL | `test_goal_inline_llm_query` | Code like `answer = llm_query(prompt)` works inside exec blocks |
| Recursive depth support | `test_goal_recursive_depth` | Sub-LLM can spawn its own sub-LLM up to depth limit |
| Batch/async execution | `test_goal_parallel_execution` | `llm_query_batch()` runs queries concurrently, not serially |
| Semantic chunking | `test_goal_content_aware_splits` | Markdown splits on headers, code on functions |
| Answer finalization | `test_goal_finalization_signal` | Main agent can retrieve final answer via CLI |

### Running Tests

```bash
# All unit tests (fast)
pytest skills/rlm/tests/ -v --ignore=skills/rlm/tests/test_integration.py

# Include slow integration tests
pytest skills/rlm/tests/ -v --slow

# Goal-alignment tests only
pytest skills/rlm/tests/ -v -k "test_goal_"

# Specific phase
pytest skills/rlm/tests/test_phase1_llm_query.py -v

# Full regression (run before each commit)
pytest skills/rlm/tests/ -v
```

### Validation Per Phase

Each phase's validation section now includes:
1. **Run phase tests**: `pytest skills/rlm/tests/test_phaseN_*.py -v`
2. **Run regression**: `pytest skills/rlm/tests/ -v` (all prior phases still pass)
3. **Goal-alignment check**: Confirm specific paper requirement is met
4. **Manual smoke test**: One real-world command demonstrating the feature

---

## Goals

Address these gaps between the paper's RLM design and current pi-rlm implementation:

1. **Inline `llm_query()` in REPL** — Enable programmatic sub-LLM calls from within Python code blocks
2. **Recursive depth support** — Allow sub-LLMs to spawn their own sub-LLMs (default depth limit: 3)
3. **Batch/async execution** — Add `llm_query_batch()` for parallel sub-LLM invocation
4. **Semantic chunking** — Content-aware chunking using markdown structure and tree-sitter
5. **Answer finalization signal** — Add `set_final_answer()` to mark variables for retrieval

---

## Phase 1: Core `llm_query()` Infrastructure
**Estimated tokens:** ~100k-110k

### Deliverables
1. **`_spawn_sub_agent(prompt, depth, session_dir, cleanup)`** - Spawn full pi subprocess
2. **`_parse_pi_json_output(output)`** - Extract final text from `--mode json` output
3. **`_log_query(session_dir, entry)`** - Append to `llm_queries.jsonl`
4. **`llm_query(prompt, cleanup=True)`** - Exposed helper in REPL environment
5. **Global concurrency semaphore** (limit: 5)
6. **State version migration** (v2 → v3)

### Implementation Details

**Function: `_spawn_sub_agent()`**
```python
def _spawn_sub_agent(
    prompt: str,
    remaining_depth: int,
    session_dir: Path,
    cleanup: bool = True,
) -> str:
    """Spawn a full pi subprocess for the sub-query."""
```

- Create sub-session directory: `{session_dir}/depth-{N}/q_{uuid[:8]}`
- Write prompt to `{sub_session_dir}/prompt.txt`
- Spawn pi subprocess:
  ```bash
  pi --mode json -p --no-session \
     --model google-antigravity/gemini-3-flash \
     --skills rlm \
     --append-system-prompt "RLM_STATE_DIR={sub_session_dir} RLM_REMAINING_DEPTH={depth-1}" \
     < prompt.txt
  ```
- Timeout: 120s per query
- Parse streaming JSONL output
- Clean up `{sub_session_dir}` if `cleanup=True`
- Return text response

**Function: `_parse_pi_json_output()`**
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
- Subprocess timeout: `"[ERROR: Sub-agent timed out after 120s]"`
- Non-zero exit: `"[ERROR: Sub-agent failed: <stderr preview>]"`
- JSON parse failure: `"[ERROR: Failed to parse sub-agent response]"`
- Depth exceeded: `"[ERROR: Recursion depth limit reached]"`

**Query log format:**
```json
{
  "timestamp": "2026-01-21T12:30:45.123Z",
  "query_id": "q_a1b2c3d4",
  "depth": 2,
  "remaining_depth": 1,
  "prompt_preview": "Summarize this section...",
  "prompt_chars": 4523,
  "sub_state_dir": ".pi/rlm_state/.../depth-1/q_a1b2c3d4",
  "response_preview": "This section describes...",
  "response_chars": 892,
  "duration_ms": 8500,
  "status": "success",
  "cleanup": true
}
```

**State schema (v3):**
```python
state = {
    "version": 3,
    "max_depth": 3,
    "remaining_depth": 3,
    "preserve_recursive_state": False,
    "context": {...},
    "buffers": [],
    "handles": {},
    "handle_counter": 0,
    "globals": {},
    "final_answer": None,
}
```

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py` (add ~150-200 lines)
- `skills/rlm/tests/conftest.py` (new - shared fixtures)
- `skills/rlm/tests/test_phase1_llm_query.py` (new - phase tests)

### Tests to Write

**`skills/rlm/tests/test_phase1_llm_query.py`:**

```python
"""Phase 1 tests: Core llm_query() infrastructure."""
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import after adding to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from rlm_repl import _parse_pi_json_output, _log_query, _spawn_sub_agent


class TestParseJsonOutput:
    """Unit tests for _parse_pi_json_output()."""
    
    def test_simple_message_end(self):
        """Parse a basic message_end event."""
        output = '{"type":"message_end","message":{"content":[{"type":"text","text":"Hello"}]}}'
        assert _parse_pi_json_output(output) == "Hello"
    
    def test_multiple_text_blocks(self):
        """Combine multiple text content blocks."""
        output = '{"type":"message_end","message":{"content":[{"type":"text","text":"Hello"},{"type":"text","text":"World"}]}}'
        assert _parse_pi_json_output(output) == "Hello\nWorld"
    
    def test_streaming_jsonl(self):
        """Handle multi-line streaming output, extract final message."""
        output = '''{"type":"content_block_start"}
{"type":"content_block_delta","delta":{"text":"Hel"}}
{"type":"content_block_delta","delta":{"text":"lo"}}
{"type":"message_end","message":{"content":[{"type":"text","text":"Hello"}]}}'''
        assert _parse_pi_json_output(output) == "Hello"
    
    def test_empty_content(self):
        """Handle message_end with no content."""
        output = '{"type":"message_end","message":{"content":[]}}'
        assert _parse_pi_json_output(output) == ""
    
    def test_no_message_end(self):
        """Return empty string if no message_end found."""
        output = '{"type":"content_block_start"}\n{"type":"ping"}'
        assert _parse_pi_json_output(output) == ""
    
    def test_malformed_json_lines(self):
        """Skip malformed lines gracefully."""
        output = 'not json\n{"type":"message_end","message":{"content":[{"type":"text","text":"OK"}]}}'
        assert _parse_pi_json_output(output) == "OK"


class TestLogQuery:
    """Unit tests for _log_query()."""
    
    def test_appends_to_jsonl(self, tmp_path):
        """Entries append to llm_queries.jsonl."""
        entry = {"query_id": "q_test", "status": "success"}
        _log_query(tmp_path, entry)
        _log_query(tmp_path, {"query_id": "q_test2", "status": "failed"})
        
        log_file = tmp_path / "llm_queries.jsonl"
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["query_id"] == "q_test"
    
    def test_adds_timestamp(self, tmp_path):
        """Timestamp added if not present."""
        entry = {"query_id": "q_test"}
        _log_query(tmp_path, entry)
        
        log_file = tmp_path / "llm_queries.jsonl"
        logged = json.loads(log_file.read_text().strip())
        assert "timestamp" in logged


class TestSpawnSubAgent:
    """Unit tests for _spawn_sub_agent() with mocked subprocess."""
    
    @patch("rlm_repl.subprocess.run")
    def test_returns_parsed_response(self, mock_run, tmp_path):
        """Successful spawn returns parsed text."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"content":[{"type":"text","text":"Result"}]}}',
            stderr=""
        )
        result = _spawn_sub_agent("test prompt", 3, tmp_path, cleanup=True)
        assert result == "Result"
    
    @patch("rlm_repl.subprocess.run")
    def test_timeout_returns_error(self, mock_run, tmp_path):
        """Timeout returns error string, doesn't raise."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("pi", 120)
        result = _spawn_sub_agent("test", 3, tmp_path)
        assert "[ERROR:" in result and "timeout" in result.lower()
    
    @patch("rlm_repl.subprocess.run")
    def test_nonzero_exit_returns_error(self, mock_run, tmp_path):
        """Non-zero exit returns error with stderr preview."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Something went wrong"
        )
        result = _spawn_sub_agent("test", 3, tmp_path)
        assert "[ERROR:" in result
    
    def test_depth_zero_returns_error_without_spawn(self, tmp_path):
        """Depth 0 fails fast without spawning subprocess."""
        with patch("rlm_repl.subprocess.run") as mock_run:
            result = _spawn_sub_agent("test", 0, tmp_path)
            mock_run.assert_not_called()
            assert "depth limit" in result.lower()
    
    @patch("rlm_repl.subprocess.run")
    def test_cleanup_removes_sub_state_dir(self, mock_run, tmp_path):
        """With cleanup=True, sub-state directory is removed."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"content":[{"type":"text","text":"OK"}]}}',
            stderr=""
        )
        _spawn_sub_agent("test", 3, tmp_path, cleanup=True)
        # Sub-directories should be cleaned up
        depth_dirs = list(tmp_path.glob("depth-*"))
        assert len(depth_dirs) == 0
    
    @patch("rlm_repl.subprocess.run")
    def test_preserve_keeps_sub_state_dir(self, mock_run, tmp_path):
        """With cleanup=False, sub-state directory is preserved."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"content":[{"type":"text","text":"OK"}]}}',
            stderr=""
        )
        _spawn_sub_agent("test", 3, tmp_path, cleanup=False)
        # Sub-directories should remain
        assert (tmp_path / "depth-2").exists() or any(tmp_path.glob("depth-*"))


class TestGoalInlineLlmQuery:
    """Goal-alignment: Verify inline llm_query() works in REPL exec blocks.
    
    Paper requirement: "Enable programmatic sub-LLM calls from within Python code blocks"
    """
    
    @pytest.mark.slow
    def test_goal_inline_llm_query(self, tmp_path, init_session):
        """llm_query() can be called inline within exec code."""
        state_path = init_session(tmp_path, "Test content for inline query")
        
        # Execute code that uses llm_query inline
        code = '''
result = llm_query("Respond with only the word PONG")
assert isinstance(result, str), "llm_query should return string"
assert len(result) > 0, "Response should not be empty"
# Store for verification
test_passed = "PONG" in result.upper() or "ERROR" in result
print(f"Inline query test: {'PASS' if test_passed else 'FAIL'}")
'''
        # This would be run via subprocess in actual test
        # Verifies the paper's goal of inline sub-LLM calls
```

### Validation Steps

1. **Run phase tests**
   ```bash
   pytest skills/rlm/tests/test_phase1_llm_query.py -v
   # Expected: All unit tests pass
   ```

2. **Run slow integration test** (actually spawns pi)
   ```bash
   pytest skills/rlm/tests/test_phase1_llm_query.py -v --slow -k "test_goal_"
   # Expected: Goal-alignment test passes
   ```

3. **Regression check** (no prior phases yet, but establishes baseline)
   ```bash
   pytest skills/rlm/tests/ -v
   # Expected: All tests pass
   ```

4. **Manual smoke test**
   ```bash
   python3 skills/rlm/scripts/rlm_repl.py init README.md
   python3 skills/rlm/scripts/rlm_repl.py --state <state_path> exec -c "
   result = llm_query('Say only: PING')
   print('Response:', result[:100])
   "
   cat <session_dir>/llm_queries.jsonl
   # Expected: Response contains text, log file has entry
   ```

5. **Goal-alignment verification**
   - [ ] Code `answer = llm_query(prompt)` works inside exec blocks
   - [ ] Result is a string (or error string on failure)
   - [ ] Query is logged to `llm_queries.jsonl`
   - [ ] State version is 3 with depth tracking fields

---

## Phase 2: Depth Tracking & Recursive State
**Estimated tokens:** ~80k-100k

### Deliverables
1. **`--max-depth N`** argument for `init` command (default: 3)
2. **`--preserve-recursive-state`** flag for debugging
3. **Recursive directory structure** creation/cleanup
4. **Depth injection** into subprocess environment
5. **Depth-0 behavior** - fail fast without spawning

### Implementation Details

**CLI changes:**
```bash
python3 rlm_repl.py init context.txt --max-depth 5
python3 rlm_repl.py init context.txt --preserve-recursive-state
```

**Directory structure:**
```
.pi/rlm_state/
└── myfile-20260121-143022/           # Root session
    ├── state.pkl
    ├── chunks/
    ├── llm_queries.jsonl
    └── depth-2/                       # First recursion level
        └── q_a1b2c3d4/               # Sub-query session
            ├── prompt.txt
            ├── state.pkl
            └── depth-1/              # Second recursion level
                └── q_e5f6g7h8/
                    └── state.pkl
```

**Depth propagation:**
- Sub-agent receives `RLM_REMAINING_DEPTH` via system prompt injection
- REPL reads from env or defaults to state value
- Each recursion decrements by 1

**Depth-0 behavior:**
```python
if remaining_depth <= 0:
    _log_query(session_dir, {..., "status": "depth_exceeded"})
    return "[ERROR: Recursion depth limit reached. Process without sub-queries.]"
```

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py` (modify ~50 lines)
- `skills/rlm/SKILL.md` (document new flags)
- `skills/rlm/tests/test_phase2_depth.py` (new)

### Tests to Write

**`skills/rlm/tests/test_phase2_depth.py`:**

```python
"""Phase 2 tests: Depth tracking and recursive state."""
import pytest
import pickle
from pathlib import Path

class TestMaxDepthInit:
    """Unit tests for --max-depth initialization."""
    
    def test_default_max_depth_is_3(self, init_session, tmp_path):
        state_path = init_session(tmp_path, "content")
        state = pickle.load(open(state_path, "rb"))
        assert state["max_depth"] == 3
        assert state["remaining_depth"] == 3
    
    def test_custom_max_depth(self, tmp_path):
        # Run init with --max-depth 5
        # Verify state has max_depth=5, remaining_depth=5
        pass
    
    def test_preserve_recursive_state_flag(self, tmp_path):
        # Run init with --preserve-recursive-state
        # Verify state["preserve_recursive_state"] == True
        pass

class TestDepthPropagation:
    """Unit tests for depth decrement during recursion."""
    
    def test_sub_agent_receives_decremented_depth(self, tmp_path):
        # Mock subprocess, verify RLM_REMAINING_DEPTH in command
        pass
    
    def test_nested_directory_structure(self, tmp_path):
        # Verify depth-N/q_xxx directory created
        pass

class TestDepthZeroBehavior:
    """Unit tests for depth limit enforcement."""
    
    def test_depth_zero_returns_error_string(self, tmp_path):
        # Call _spawn_sub_agent with remaining_depth=0
        # Verify returns error string without spawning
        pass
    
    def test_depth_zero_logs_depth_exceeded(self, tmp_path):
        # Verify log entry has status="depth_exceeded"
        pass

class TestGoalRecursiveDepth:
    """Goal-alignment: Sub-LLMs can spawn their own sub-LLMs.
    
    Paper requirement: "Allow sub-LLMs to spawn their own sub-LLMs (default depth limit: 3)"
    """
    
    @pytest.mark.slow
    def test_goal_recursive_depth(self, tmp_path, init_session):
        """Depth-2 recursion: root -> sub-LLM -> sub-sub-LLM."""
        # Initialize with max_depth=2
        # Call llm_query that itself calls llm_query
        # Verify nested state directories created
        # Verify depth-0 blocks third level
        pass
```

### Validation Steps

1. **Run phase tests**
   ```bash
   pytest skills/rlm/tests/test_phase2_depth.py -v
   ```

2. **Regression check**
   ```bash
   pytest skills/rlm/tests/test_phase1_llm_query.py skills/rlm/tests/test_phase2_depth.py -v
   ```

3. **Goal-alignment test** (slow, real recursion)
   ```bash
   pytest skills/rlm/tests/test_phase2_depth.py -v --slow -k "test_goal_"
   ```

4. **Manual smoke test**
   ```bash
   python3 skills/rlm/scripts/rlm_repl.py init README.md --max-depth 2
   # Verify state has correct depth fields
   ```

5. **Goal-alignment verification**
   - [ ] `--max-depth N` sets both `max_depth` and `remaining_depth`
   - [ ] Sub-agent receives decremented depth
   - [ ] Depth-0 returns error without spawning
   - [ ] Directory structure matches `depth-N/q_xxx` pattern

---

## Phase 3: `llm_query_batch()` Implementation
**Estimated tokens:** ~80k-100k

### Deliverables
1. **`llm_query_batch(prompts, concurrency=5, max_retries=3, cleanup=True)`**
2. **Shared global semaphore** enforcement
3. **Retry with exponential backoff**
4. **Structured failures dict** return
5. **Batch logging** with `batch_id` field

### Implementation Details

**Function signature:**
```python
def llm_query_batch(
    prompts: list[str],
    concurrency: int = 5,      # Max concurrent (capped by global 5)
    max_retries: int = 3,      # Retry failed items
    cleanup: bool = True,      # Clean up sub-session state
) -> tuple[list[str], dict[int, dict]]:
    """Execute multiple queries concurrently.
    
    Returns:
        (results, failures) where:
        - results[i] = response string or "[ERROR: ...]"
        - failures = {index: {"reason": str, "attempts": int, "error": str}}
    """
```

**Global semaphore:**
```python
import threading
_GLOBAL_CONCURRENCY_SEMAPHORE = threading.Semaphore(5)

def _spawn_sub_agent_with_semaphore(...):
    with _GLOBAL_CONCURRENCY_SEMAPHORE:
        return _spawn_sub_agent(...)
```

**Retry logic:**
- 1st retry: wait 1s
- 2nd retry: wait 2s  
- 3rd retry: wait 4s (exponential backoff)

**Batch log entry:**
```json
{
  "timestamp": "...",
  "batch_id": "batch_0001",
  "batch_index": 3,
  "batch_size": 10,
  "attempt": 2,
  "status": "success",
  ...
}
```

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py` (add ~80-100 lines)
- `skills/rlm/tests/test_phase3_batch.py` (new)

### Tests to Write

**`skills/rlm/tests/test_phase3_batch.py`:**

```python
"""Phase 3 tests: Batch execution with llm_query_batch()."""
import pytest
import time
from unittest.mock import patch, MagicMock
from concurrent.futures import ThreadPoolExecutor

class TestBatchExecution:
    """Unit tests for llm_query_batch()."""
    
    @patch("rlm_repl._spawn_sub_agent")
    def test_returns_results_in_order(self, mock_spawn, tmp_path):
        """Results maintain input order regardless of completion order."""
        mock_spawn.side_effect = ["Result A", "Result B", "Result C"]
        results, failures = llm_query_batch(["A", "B", "C"])
        assert results == ["Result A", "Result B", "Result C"]
        assert failures == {}
    
    @patch("rlm_repl._spawn_sub_agent")  
    def test_failures_dict_structure(self, mock_spawn, tmp_path):
        """Failed items appear in results AND failures dict."""
        mock_spawn.side_effect = ["OK", "[ERROR: timeout]", "OK"]
        results, failures = llm_query_batch(["A", "B", "C"], max_retries=1)
        assert "[ERROR:" in results[1]
        assert 1 in failures
        assert "reason" in failures[1]
        assert "attempts" in failures[1]

class TestConcurrencyLimit:
    """Unit tests for global concurrency semaphore."""
    
    def test_max_5_concurrent(self):
        """Never more than 5 concurrent subprocess calls."""
        # Track concurrent calls
        concurrent_count = []
        max_concurrent = [0]
        lock = threading.Lock()
        
        def mock_spawn(*args):
            with lock:
                concurrent_count.append(1)
                max_concurrent[0] = max(max_concurrent[0], len(concurrent_count))
            time.sleep(0.1)
            with lock:
                concurrent_count.pop()
            return "OK"
        
        with patch("rlm_repl._spawn_sub_agent", side_effect=mock_spawn):
            llm_query_batch(["x"] * 20, concurrency=10)  # Request 10, capped to 5
        
        assert max_concurrent[0] <= 5

class TestRetryLogic:
    """Unit tests for exponential backoff retry."""
    
    @patch("rlm_repl._spawn_sub_agent")
    def test_retries_on_error(self, mock_spawn):
        """Transient failures are retried up to max_retries."""
        mock_spawn.side_effect = ["[ERROR: temp]", "[ERROR: temp]", "Success"]
        results, failures = llm_query_batch(["test"], max_retries=3)
        assert results[0] == "Success"
        assert 0 not in failures
        assert mock_spawn.call_count == 3
    
    @patch("rlm_repl._spawn_sub_agent")
    def test_exponential_backoff_timing(self, mock_spawn):
        """Backoff doubles each retry: 1s, 2s, 4s."""
        mock_spawn.return_value = "[ERROR: always fail]"
        start = time.time()
        llm_query_batch(["test"], max_retries=3)
        elapsed = time.time() - start
        # 1 + 2 + 4 = 7s minimum for backoff
        assert elapsed >= 6.5  # Allow some tolerance

class TestGoalParallelExecution:
    """Goal-alignment: Batch queries run concurrently.
    
    Paper requirement: "parallel sub-LLM invocation"
    """
    
    @pytest.mark.slow
    def test_goal_parallel_execution(self, tmp_path, init_session):
        """10 queries complete faster than 10x single query time."""
        # Time single query
        single_start = time.time()
        llm_query("Say X")
        single_time = time.time() - single_start
        
        # Time batch of 10
        batch_start = time.time()
        results, _ = llm_query_batch([f"Say {i}" for i in range(10)])
        batch_time = time.time() - batch_start
        
        # Batch should be significantly faster than 10x serial
        assert batch_time < single_time * 5  # At least 2x speedup
```

### Validation Steps

1. **Run phase tests**
   ```bash
   pytest skills/rlm/tests/test_phase3_batch.py -v
   ```

2. **Regression check**
   ```bash
   pytest skills/rlm/tests/test_phase{1,2,3}_*.py -v
   ```

3. **Goal-alignment test**
   ```bash
   pytest skills/rlm/tests/test_phase3_batch.py -v --slow -k "test_goal_"
   ```

4. **Manual smoke test**
   ```bash
   python3 skills/rlm/scripts/rlm_repl.py --state <state_path> exec -c "
   results, failures = llm_query_batch(['Say A', 'Say B', 'Say C'])
   print(f'Got {len(results)} results, {len(failures)} failures')
   "
   grep 'batch_id' <session_dir>/llm_queries.jsonl | head -3
   ```

5. **Goal-alignment verification**
   - [ ] Batch of N queries runs concurrently (not serially)
   - [ ] Max 5 concurrent (global semaphore enforced)
   - [ ] Failed items retry with exponential backoff
   - [ ] Returns (results_list, failures_dict) tuple

---

## Phase 4: Finalization Signal
**Estimated tokens:** ~60k-80k

### Deliverables
1. **`set_final_answer(value)`** - Mark value as final (JSON-serializable)
2. **`has_final_answer()`** - Check if answer is set
3. **`get_final_answer()`** - Retrieve the value
4. **`get-final-answer` CLI command** - JSON output for external retrieval
5. **Updated `status` command** - Show final answer info

### Implementation Details

**Helper functions:**
```python
def set_final_answer(value: Any) -> None:
    """Mark a value as the final answer."""
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
    return state_ref.get("final_answer") is not None

def get_final_answer() -> Any:
    fa = state_ref.get("final_answer")
    return fa["value"] if fa else None
```

**CLI command:**
```bash
python3 rlm_repl.py --state ... get-final-answer
# Output (JSON):
{"set": true, "value": [[1, 2], [3, 4]], "set_at": "2026-01-21T12:30:45.123Z"}
```

**Updated status output:**
```
RLM REPL status
  ...
  Final answer: SET (type: list, length: 3)
```

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py` (add ~60-80 lines)
- `skills/rlm/SKILL.md` (document finalization pattern)
- `skills/rlm/tests/test_phase4_finalize.py` (new)

### Tests to Write

**`skills/rlm/tests/test_phase4_finalize.py`:**

```python
"""Phase 4 tests: Answer finalization signal."""
import pytest
import json
import pickle
import subprocess
from pathlib import Path

class TestSetFinalAnswer:
    """Unit tests for set_final_answer()."""
    
    def test_stores_value_in_state(self, init_session, tmp_path):
        state_path = init_session(tmp_path, "content")
        # Run exec with set_final_answer
        # Load state, verify final_answer key exists
        pass
    
    def test_adds_timestamp(self, init_session, tmp_path):
        # Verify set_at is ISO 8601 format
        pass
    
    def test_rejects_non_serializable(self, init_session, tmp_path):
        """Non-JSON-serializable values raise ValueError."""
        import re
        # set_final_answer(re.compile('test')) should raise
        pass
    
    def test_overwrites_previous(self, init_session, tmp_path):
        """Subsequent calls overwrite previous answer."""
        pass

class TestHasGetFinalAnswer:
    """Unit tests for has_final_answer() and get_final_answer()."""
    
    def test_has_final_answer_false_initially(self):
        pass
    
    def test_has_final_answer_true_after_set(self):
        pass
    
    def test_get_returns_none_if_not_set(self):
        pass
    
    def test_get_returns_value_if_set(self):
        pass

class TestGetFinalAnswerCLI:
    """Unit tests for get-final-answer CLI command."""
    
    def test_outputs_valid_json(self, init_session, tmp_path):
        # Run CLI command, parse output as JSON
        pass
    
    def test_shows_set_false_when_not_set(self):
        pass
    
    def test_shows_set_true_with_value(self):
        pass

class TestGoalFinalizationSignal:
    """Goal-alignment: Main agent can retrieve final answer.
    
    Paper requirement: "Add set_final_answer() to mark variables for retrieval"
    """
    
    def test_goal_finalization_signal(self, init_session, tmp_path):
        """Full cycle: set in exec, retrieve via CLI."""
        state_path = init_session(tmp_path, "content")
        
        # Set answer via exec
        subprocess.run([
            "python3", "skills/rlm/scripts/rlm_repl.py",
            "--state", str(state_path),
            "exec", "-c", "set_final_answer({'result': 42})"
        ])
        
        # Retrieve via CLI
        result = subprocess.run([
            "python3", "skills/rlm/scripts/rlm_repl.py",
            "--state", str(state_path),
            "get-final-answer"
        ], capture_output=True, text=True)
        
        data = json.loads(result.stdout)
        assert data["set"] == True
        assert data["value"]["result"] == 42
```

### Validation Steps

1. **Run phase tests**
   ```bash
   pytest skills/rlm/tests/test_phase4_finalize.py -v
   ```

2. **Regression check**
   ```bash
   pytest skills/rlm/tests/test_phase{1,2,3,4}_*.py -v
   ```

3. **Goal-alignment test**
   ```bash
   pytest skills/rlm/tests/test_phase4_finalize.py -v -k "test_goal_"
   ```

4. **Manual smoke test**
   ```bash
   python3 skills/rlm/scripts/rlm_repl.py --state <state_path> exec -c "
   set_final_answer({'summary': 'Test complete', 'items': [1, 2, 3]})
   "
   python3 skills/rlm/scripts/rlm_repl.py --state <state_path> get-final-answer
   python3 skills/rlm/scripts/rlm_repl.py --state <state_path> status
   ```

5. **Goal-alignment verification**
   - [ ] `set_final_answer(value)` persists to state
   - [ ] Only JSON-serializable values accepted
   - [ ] `get-final-answer` CLI returns valid JSON
   - [ ] `status` shows "Final answer: SET (type: ...)"

---

## Phase 5: Semantic Chunking - Markdown
**Estimated tokens:** ~80k-100k

### Deliverables
1. **`smart_chunk()` function** - Main entry point
2. **`_detect_format(content, path)`** - Format detection
3. **`_chunk_markdown(content, target, min, max)`** - Header-aware splitting
4. **`_chunk_text(content, target, min, max)`** - Paragraph-based fallback
5. **Enhanced manifest** with format and split_reason fields

### Implementation Details

**Function signature:**
```python
def smart_chunk(
    out_dir: str,
    target_size: int = 200_000,    # Target chars per chunk
    min_size: int = 50_000,        # Minimum chunk size
    max_size: int = 400_000,       # Hard maximum
    encoding: str = "utf-8",
) -> list[str]:
    """Smart content-aware chunking."""
```

**Format detection:**
```python
def _detect_format(content: str, context_path: str) -> str:
    ext = Path(context_path).suffix.lower()
    
    if ext in ('.md', '.markdown', '.mdx'):
        return 'markdown'
    if ext in ('.ts', '.js', '.py', '.rs', '.go', '.java', ...):
        return 'code'
    if ext == '.json':
        return 'json'
    
    # Content-based fallback
    if content.count('\n#') > 5:
        return 'markdown'
    
    return 'text'
```

**Markdown chunking:**
- Split on level 2-3 headers preferentially
- Respect target_size (soft) and max_size (hard) limits
- Keep sections together when possible

**Enhanced manifest:**
```json
{
  "format": "markdown",
  "chunking_method": "smart",
  "chunks": [
    {
      "id": "chunk_0000",
      "split_reason": "header_level_2",
      "format": "markdown",
      "boundaries": [
        {"type": "heading", "level": 2, "text": "Authentication", "line": 1}
      ],
      ...
    }
  ]
}
```

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py` (add ~120-150 lines)
- `skills/rlm/tests/test_phase5_markdown.py` (new)

### Tests to Write

**`skills/rlm/tests/test_phase5_markdown.py`:**

```python
"""Phase 5 tests: Semantic chunking - Markdown."""
import pytest
import json
from pathlib import Path

class TestDetectFormat:
    """Unit tests for _detect_format()."""
    
    def test_markdown_by_extension(self):
        assert _detect_format("content", "test.md") == "markdown"
        assert _detect_format("content", "test.markdown") == "markdown"
        assert _detect_format("content", "test.mdx") == "markdown"
    
    def test_code_by_extension(self):
        for ext in [".py", ".ts", ".js", ".rs", ".go", ".java"]:
            assert _detect_format("content", f"test{ext}") == "code"
    
    def test_json_by_extension(self):
        assert _detect_format("content", "test.json") == "json"
    
    def test_markdown_by_content(self):
        """Detect markdown by header density if no extension."""
        content = "# H1\n## H2\n## H3\n## H4\n## H5\n## H6\n"
        assert _detect_format(content, "unknown") == "markdown"
    
    def test_text_fallback(self):
        assert _detect_format("plain text", "test.txt") == "text"

class TestChunkMarkdown:
    """Unit tests for _chunk_markdown()."""
    
    def test_splits_on_level_2_headers(self):
        content = "# Title\nIntro\n## Section 1\nContent 1\n## Section 2\nContent 2"
        chunks = _chunk_markdown(content, target_size=20, min_size=5, max_size=100)
        # Should split at ## boundaries
        assert len(chunks) >= 2
    
    def test_respects_max_size(self):
        content = "## Section\n" + "x" * 1000
        chunks = _chunk_markdown(content, target_size=100, min_size=50, max_size=200)
        for start, end in chunks:
            assert end - start <= 200
    
    def test_keeps_sections_together(self):
        """Small sections stay together until target_size."""
        content = "## A\na\n## B\nb\n## C\nc"
        chunks = _chunk_markdown(content, target_size=100, min_size=10, max_size=200)
        # All sections small, might fit in one chunk
        pass

class TestChunkText:
    """Unit tests for _chunk_text() fallback."""
    
    def test_splits_on_paragraphs(self):
        content = "Para 1.\n\nPara 2.\n\nPara 3."
        chunks = _chunk_text(content, target_size=10, min_size=5, max_size=50)
        assert len(chunks) >= 2

class TestSmartChunk:
    """Integration tests for smart_chunk()."""
    
    def test_creates_chunks_and_manifest(self, init_session, tmp_path):
        state_path = init_session(tmp_path, "# Title\n## Section\nContent")
        # Call smart_chunk, verify files created
        pass
    
    def test_manifest_has_format_field(self, init_session, tmp_path):
        pass
    
    def test_manifest_has_chunking_method(self, init_session, tmp_path):
        pass

class TestGoalContentAwareSplits:
    """Goal-alignment: Content-aware chunking at natural boundaries.
    
    Paper requirement: "Content-aware chunking using markdown structure"
    """
    
    def test_goal_content_aware_splits(self, tmp_path):
        """Markdown chunks align with section headers."""
        md_content = '''# Main Title

Introduction paragraph.

## Authentication

This section covers auth.

### JWT Tokens

JWT details here.

## Authorization

This section covers authz.

## Logging

Logging details.
'''
        # Create file, init session, smart_chunk
        # Verify chunks start at ## or ### boundaries
        # Verify manifest has split_reason indicating header
        pass
```

### Validation Steps

1. **Run phase tests**
   ```bash
   pytest skills/rlm/tests/test_phase5_markdown.py -v
   ```

2. **Regression check**
   ```bash
   pytest skills/rlm/tests/test_phase{1,2,3,4,5}_*.py -v
   ```

3. **Goal-alignment test**
   ```bash
   pytest skills/rlm/tests/test_phase5_markdown.py -v -k "test_goal_"
   ```

4. **Manual smoke test**
   ```bash
   python3 skills/rlm/scripts/rlm_repl.py init <large_markdown_file>
   python3 skills/rlm/scripts/rlm_repl.py --state <state_path> exec -c "
   paths = smart_chunk(str(session_dir / 'chunks'), target_size=50000)
   print(f'Created {len(paths)} chunks')
   "
   head -20 <session_dir>/chunks/chunk_0000.txt  # Should start at header
   cat <session_dir>/chunks/manifest.json | python3 -m json.tool | head -30
   ```

5. **Goal-alignment verification**
   - [ ] Markdown splits on header boundaries (not mid-paragraph)
   - [ ] Format auto-detected from extension or content
   - [ ] Manifest includes `format`, `chunking_method`, `split_reason`
   - [ ] Fallback to paragraph-based text chunking works

---

## Phase 6: Semantic Chunking - Code (Codemap Integration)
**Estimated tokens:** ~100k-120k

### Deliverables
1. **`_detect_codemap()`** - Auto-detect codemap availability
2. **`_chunk_code(content, path, target, min, max)`** - Function/class boundary splitting
3. **`_extract_symbol_boundaries(codemap_output)`** - Parse codemap JSON
4. **Graceful fallback** when codemap unavailable

### Implementation Details

**Codemap detection:**
```python
def _detect_codemap() -> str | None:
    """Auto-detect codemap. Returns command string or None."""
    # 1. Check RLM_CODEMAP_PATH env var
    env_path = os.environ.get('RLM_CODEMAP_PATH')
    if env_path and Path(env_path).exists():
        return env_path
    
    # 2. Try npx
    try:
        result = subprocess.run(['npx', 'codemap', '--version'],
                               capture_output=True, timeout=10)
        if result.returncode == 0:
            return 'npx codemap'
    except:
        pass
    
    return None  # Fallback to text chunking
```

**Code chunking:**
```python
def _chunk_code(content, context_path, target_size, min_size, max_size):
    codemap_cmd = _detect_codemap()
    if not codemap_cmd:
        return _chunk_text(content, target_size, min_size, max_size)
    
    # Run codemap to get symbol boundaries
    result = subprocess.run([*codemap_cmd.split(), '-o', 'json', context_path],
                           capture_output=True, text=True, timeout=30)
    
    if result.returncode != 0:
        return _chunk_text(content, target_size, min_size, max_size)
    
    # Parse and build chunks from symbol boundaries
    ...
```

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py` (add ~100-120 lines)
- `skills/rlm/tests/test_phase6_code.py` (new)

### Tests to Write

**`skills/rlm/tests/test_phase6_code.py`:**

```python
"""Phase 6 tests: Semantic chunking - Code with codemap."""
import pytest
import os
from unittest.mock import patch, MagicMock
from pathlib import Path

class TestDetectCodemap:
    """Unit tests for _detect_codemap()."""
    
    def test_env_var_path(self, tmp_path):
        """RLM_CODEMAP_PATH env var takes precedence."""
        codemap_path = tmp_path / "codemap"
        codemap_path.touch()
        with patch.dict(os.environ, {"RLM_CODEMAP_PATH": str(codemap_path)}):
            result = _detect_codemap()
            assert result == str(codemap_path)
    
    @patch("rlm_repl.subprocess.run")
    def test_npx_detection(self, mock_run):
        """Falls back to npx codemap if available."""
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict(os.environ, {"RLM_CODEMAP_PATH": ""}):
            result = _detect_codemap()
            assert result == "npx codemap"
    
    @patch("rlm_repl.subprocess.run")
    def test_returns_none_if_unavailable(self, mock_run):
        """Returns None when codemap not found anywhere."""
        mock_run.side_effect = FileNotFoundError()
        with patch.dict(os.environ, {"RLM_CODEMAP_PATH": ""}):
            result = _detect_codemap()
            assert result is None

class TestChunkCode:
    """Unit tests for _chunk_code()."""
    
    @patch("rlm_repl._detect_codemap")
    @patch("rlm_repl.subprocess.run")
    def test_uses_codemap_output(self, mock_run, mock_detect):
        """Parses codemap JSON to find symbol boundaries."""
        mock_detect.return_value = "npx codemap"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"symbols": [{"name": "func1", "start": 0, "end": 100}]}'
        )
        # Test chunking uses these boundaries
        pass
    
    @patch("rlm_repl._detect_codemap")
    def test_falls_back_to_text_chunking(self, mock_detect):
        """Without codemap, falls back to text chunking."""
        mock_detect.return_value = None
        # Verify _chunk_text is called instead
        pass
    
    @patch("rlm_repl._detect_codemap")
    @patch("rlm_repl.subprocess.run")
    def test_handles_codemap_failure(self, mock_run, mock_detect):
        """Gracefully falls back if codemap returns error."""
        mock_detect.return_value = "codemap"
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        # Should fall back to text chunking, not raise
        pass

class TestGoalCodeBoundaries:
    """Goal-alignment: Code splits on function/class boundaries.
    
    Paper requirement: "Content-aware chunking using tree-sitter"
    """
    
    @pytest.mark.slow
    @pytest.mark.skipif(not _detect_codemap(), reason="codemap not installed")
    def test_goal_code_boundaries(self, tmp_path):
        """Code chunks align with function definitions."""
        # Use a real Python file with multiple functions
        code = '''
def function_one():
    \"\"\"First function.\"\"\"
    return 1

def function_two():
    \"\"\"Second function.\"\"\"
    return 2

class MyClass:
    def method_one(self):
        pass
    
    def method_two(self):
        pass
'''
        # Create file, init, smart_chunk with small target_size
        # Verify chunks start at function/class boundaries
        pass
```

### Validation Steps

1. **Run phase tests**
   ```bash
   pytest skills/rlm/tests/test_phase6_code.py -v
   ```

2. **Regression check**
   ```bash
   pytest skills/rlm/tests/test_phase{1,2,3,4,5,6}_*.py -v
   ```

3. **Goal-alignment test** (requires codemap)
   ```bash
   pytest skills/rlm/tests/test_phase6_code.py -v --slow -k "test_goal_"
   ```

4. **Manual smoke test**
   ```bash
   # With codemap
   python3 skills/rlm/scripts/rlm_repl.py init skills/rlm/scripts/rlm_repl.py
   python3 skills/rlm/scripts/rlm_repl.py --state <state_path> exec -c "
   paths = smart_chunk(str(session_dir / 'chunks'), target_size=5000)
   print(f'Created {len(paths)} chunks')
   "
   # Without codemap
   RLM_CODEMAP_PATH="" python3 skills/rlm/scripts/rlm_repl.py --state <state_path> exec -c "
   paths = smart_chunk(str(session_dir / 'chunks'))
   print('Fallback mode')
   "
   ```

5. **Goal-alignment verification**
   - [ ] Codemap auto-detected (env var → npx → None)
   - [ ] Code chunks align with function/class boundaries
   - [ ] Graceful fallback when codemap unavailable
   - [ ] Manifest shows `codemap_available: true/false`

---

## Phase 7: Semantic Chunking - JSON
**Estimated tokens:** ~60k-80k

### Deliverables
1. **`_chunk_json_array(content, target, min, max)`** - Split arrays into element groups
2. **`_chunk_json_object(content, target, min, max)`** - Split objects by top-level keys
3. **Handle minified vs pretty-printed JSON**
4. **Element-based manifest** (indices not char positions for arrays)

### Implementation Details

**Array chunking:**
```python
def _chunk_json_array(content, target_size, min_size, max_size):
    data = json.loads(content)
    if not isinstance(data, list) or len(data) == 0:
        return [(0, len(content))]
    
    # Estimate elements per chunk
    avg_element_size = len(content) / len(data)
    elements_per_chunk = max(1, int(target_size / avg_element_size))
    
    chunks = []
    for i in range(0, len(data), elements_per_chunk):
        chunk_data = data[i:i + elements_per_chunk]
        # Write as re-serialized JSON
        ...
```

**Object chunking:**
```python
def _chunk_json_object(content, target_size, min_size, max_size):
    data = json.loads(content)
    # Group top-level keys to meet target_size
    ...
```

### Files to Modify
- `skills/rlm/scripts/rlm_repl.py` (add ~80-100 lines)
- `skills/rlm/tests/test_phase7_json.py` (new)

### Tests to Write

**`skills/rlm/tests/test_phase7_json.py`:**

```python
"""Phase 7 tests: Semantic chunking - JSON."""
import pytest
import json

class TestChunkJsonArray:
    def test_splits_array_into_chunks(self):
        data = [{"id": i} for i in range(100)]
        content = json.dumps(data)
        chunks = _chunk_json_array(content, target_size=500, min_size=100, max_size=1000)
        assert len(chunks) > 1

class TestChunkJsonObject:
    def test_groups_keys_by_size(self):
        data = {f"key_{i}": {"value": i} for i in range(20)}
        content = json.dumps(data)
        chunks = _chunk_json_object(content, target_size=200, min_size=50, max_size=500)
        assert len(chunks) > 1

class TestGoalJsonSplitting:
    """Goal-alignment: JSON splits on structural boundaries."""
    def test_goal_json_array_splitting(self, tmp_path):
        """Large JSON array splits into element groups, each valid JSON."""
        pass
```

### Validation Steps

1. **Run phase tests**
   ```bash
   pytest skills/rlm/tests/test_phase7_json.py -v
   ```

2. **Regression check**
   ```bash
   pytest skills/rlm/tests/test_phase{1,2,3,4,5,6,7}_*.py -v
   ```

3. **Goal-alignment test**
   ```bash
   pytest skills/rlm/tests/test_phase7_json.py -v -k "test_goal_"
   ```

4. **Manual smoke test**
   ```bash
   python3 -c "import json; print(json.dumps([{'id': i} for i in range(1000)]))" > test.json
   python3 skills/rlm/scripts/rlm_repl.py init test.json
   ```

5. **Goal-alignment verification**
   - [ ] JSON arrays split into element groups
   - [ ] Each chunk is valid parseable JSON
   - [ ] Manifest shows `element_range` for arrays

---

## Phase 8: Documentation & Integration Testing
**Estimated tokens:** ~60k-80k

### Deliverables
1. **Updated `SKILL.md`** with all new features
2. **README.md updates** (if exists)
3. **Integration test suite** covering full workflows
4. **Example scripts** demonstrating new capabilities

### Documentation Updates

**SKILL.md additions:**
- `llm_query()` and `llm_query_batch()` usage
- `--max-depth` and `--preserve-recursive-state` flags
- `set_final_answer()` pattern
- `smart_chunk()` vs `write_chunks()`
- Recursive depth diagram

### Files to Modify
- `skills/rlm/SKILL.md` (major updates)
- `skills/rlm/README.md` (if exists)
- `skills/rlm/tests/test_integration.py` (new file)
- `skills/rlm/tests/conftest.py` (shared fixtures)
- `skills/rlm/examples/` (new directory with examples)

### Tests to Write

**`skills/rlm/tests/test_integration.py`:**

```python
"""Integration tests: Full RLM workflows."""
import pytest
import subprocess
import json
from pathlib import Path

class TestFullWorkflow:
    """End-to-end workflow tests."""
    
    @pytest.mark.slow
    def test_init_chunk_query_finalize(self, tmp_path):
        """Complete workflow: init → smart_chunk → llm_query → set_final_answer."""
        # Create test content
        content = "# Title\n\n" + "Content paragraph.\n\n" * 100
        test_file = tmp_path / "test.md"
        test_file.write_text(content)
        
        # Init
        result = subprocess.run([
            "python3", "skills/rlm/scripts/rlm_repl.py",
            "init", str(test_file)
        ], capture_output=True, text=True)
        assert result.returncode == 0
        state_path = # extract from output
        
        # Chunk
        # Query
        # Finalize
        # Retrieve
        pass

class TestRecursiveWorkflow:
    """Tests for recursive depth scenarios."""
    
    @pytest.mark.slow
    def test_depth_2_recursion(self, tmp_path):
        """Depth-2: root calls sub-LLM, which calls sub-sub-LLM."""
        pass
    
    @pytest.mark.slow  
    def test_depth_exhaustion(self, tmp_path):
        """Verify graceful handling when depth limit reached."""
        pass

class TestBatchWorkflow:
    """Tests for batch execution scenarios."""
    
    @pytest.mark.slow
    def test_batch_with_partial_failures(self, tmp_path):
        """Some prompts fail, others succeed, all tracked correctly."""
        pass

class TestGoalAllFeatures:
    """Goal-alignment: Verify all paper requirements work together."""
    
    @pytest.mark.slow
    def test_goal_all_features(self, tmp_path):
        """
        Verify complete RLM paper alignment:
        1. Inline llm_query() works
        2. Recursive depth supported
        3. Batch execution parallel
        4. Semantic chunking on boundaries
        5. Final answer retrievable
        """
        pass
```

**`skills/rlm/tests/conftest.py`:**

```python
"""Shared test fixtures for RLM tests."""
import pytest
import subprocess
from pathlib import Path

@pytest.fixture
def init_session():
    """Factory fixture to create initialized sessions."""
    created_paths = []
    
    def _init(tmp_path: Path, content: str, **kwargs):
        test_file = tmp_path / "test_context.txt"
        test_file.write_text(content)
        
        cmd = ["python3", "skills/rlm/scripts/rlm_repl.py", "init", str(test_file)]
        for key, value in kwargs.items():
            cmd.extend([f"--{key.replace('_', '-')}", str(value)])
        
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
        assert result.returncode == 0, f"Init failed: {result.stderr}"
        
        # Extract state path from output
        for line in result.stdout.splitlines():
            if "Session path:" in line:
                state_path = Path(line.split(":", 1)[1].strip())
                created_paths.append(state_path)
                return state_path
        
        raise ValueError("Could not find state path in init output")
    
    yield _init
    
    # Cleanup (optional)
    for path in created_paths:
        if path.exists():
            path.unlink()

@pytest.fixture
def mock_subprocess(monkeypatch):
    """Mock subprocess.run for testing without actual LLM calls."""
    calls = []
    
    def mock_run(*args, **kwargs):
        calls.append((args, kwargs))
        # Return successful pi JSON output
        from unittest.mock import MagicMock
        return MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"content":[{"type":"text","text":"Mocked response"}]}}',
            stderr=""
        )
    
    monkeypatch.setattr("subprocess.run", mock_run)
    return calls
```

### Validation Steps

1. **Run integration tests**
   ```bash
   pytest skills/rlm/tests/test_integration.py -v --slow
   ```

2. **Full regression** (all phases)
   ```bash
   pytest skills/rlm/tests/ -v
   ```

3. **Documentation check**
   ```bash
   # All new features documented
   grep -c 'llm_query' skills/rlm/SKILL.md  # Should be > 0
   grep -c 'smart_chunk' skills/rlm/SKILL.md  # Should be > 0
   grep -c 'set_final_answer' skills/rlm/SKILL.md  # Should be > 0
   grep -c 'max-depth' skills/rlm/SKILL.md  # Should be > 0
   ```

4. **Example scripts**
   ```bash
   ls skills/rlm/examples/
   python3 skills/rlm/examples/basic_workflow.py --help
   ```

5. **Goal-alignment verification (all goals)**
   - [ ] Inline `llm_query()` in REPL — works in exec blocks
   - [ ] Recursive depth support — sub-LLMs spawn sub-LLMs
   - [ ] Batch/async execution — parallel with retries
   - [ ] Semantic chunking — markdown/code/JSON aware
   - [ ] Answer finalization — CLI retrieval works

---

## Summary

| Phase | Title | Est. Tokens | Key Deliverables |
|-------|-------|-------------|------------------|
| 1 | Core `llm_query()` | ~100k-110k | Subprocess spawning, JSON parsing, logging |
| 2 | Depth Tracking | ~80k-100k | `--max-depth`, recursive directories, cleanup |
| 3 | `llm_query_batch()` | ~80k-100k | Parallel execution, retries, failures dict |
| 4 | Finalization Signal | ~60k-80k | `set_final_answer()`, CLI retrieval |
| 5 | Smart Chunk - Markdown | ~80k-100k | Header-aware splitting, format detection |
| 6 | Smart Chunk - Code | ~100k-120k | Codemap integration, symbol boundaries |
| 7 | Smart Chunk - JSON | ~60k-80k | Array/object splitting |
| 8 | Docs & Integration | ~60k-80k | Full documentation, integration tests |

**Total: 8 phases, ~620k-750k estimated tokens**

---

## Migration Notes

### State Version Migration (v2 → v3)
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
- Existing v2 sessions auto-migrate on load
- `write_chunks()` remains available alongside `smart_chunk()`
- Default behavior unchanged for existing workflows
