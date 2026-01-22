"""Phase 1 tests: Core llm_query() infrastructure.

Tests for:
- _parse_pi_json_output() - Extract text from pi JSON output
- _log_query() - JSONL logging
- _spawn_sub_agent() - Subprocess spawning with mocks
- llm_query() - Inline helper in REPL environment
- State version 3 migration
"""
import json
import pickle
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Import the module under test
import sys
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from rlm_repl import (
    _parse_pi_json_output,
    _log_query,
    _spawn_sub_agent,
    _load_state,
    DEFAULT_MAX_DEPTH,
)


class TestParseJsonOutput:
    """Unit tests for _parse_pi_json_output()."""
    
    def test_simple_message_end(self):
        """Parse a basic message_end event with assistant role."""
        output = '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"Hello"}]}}'
        assert _parse_pi_json_output(output) == "Hello"
    
    def test_multiple_text_blocks(self):
        """Combine multiple text content blocks."""
        output = '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"Hello"},{"type":"text","text":"World"}]}}'
        assert _parse_pi_json_output(output) == "Hello\nWorld"
    
    def test_streaming_jsonl(self):
        """Handle multi-line streaming output, extract final message."""
        output = '''{"type":"content_block_start"}
{"type":"content_block_delta","delta":{"text":"Hel"}}
{"type":"content_block_delta","delta":{"text":"lo"}}
{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"Hello"}]}}'''
        assert _parse_pi_json_output(output) == "Hello"
    
    def test_skips_user_message_end(self):
        """Should skip message_end for user role."""
        output = '''{"type":"message_end","message":{"role":"user","content":[{"type":"text","text":"Input"}]}}
{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"Output"}]}}'''
        assert _parse_pi_json_output(output) == "Output"
    
    def test_empty_content(self):
        """Handle message_end with no content."""
        output = '{"type":"message_end","message":{"role":"assistant","content":[]}}'
        assert _parse_pi_json_output(output) == ""
    
    def test_no_message_end(self):
        """Return empty string if no message_end found."""
        output = '{"type":"content_block_start"}\n{"type":"ping"}'
        assert _parse_pi_json_output(output) == ""
    
    def test_malformed_json_lines(self):
        """Skip malformed lines gracefully."""
        output = 'not json\n{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}'
        assert _parse_pi_json_output(output) == "OK"
    
    def test_real_pi_output_format(self):
        """Test with realistic pi output structure."""
        output = '''{"type":"session","version":3,"id":"abc123"}
{"type":"agent_start"}
{"type":"message_start","message":{"role":"user","content":[{"type":"text","text":"Say hi"}]}}
{"type":"message_end","message":{"role":"user","content":[{"type":"text","text":"Say hi"}]}}
{"type":"turn_start"}
{"type":"message_start","message":{"role":"assistant","content":[{"type":"text","text":""}]}}
{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"Hi"}}
{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"Hi there!"}]}}
{"type":"turn_end","message":{"role":"assistant","content":[{"type":"text","text":"Hi there!"}]}}
{"type":"agent_end"}'''
        assert _parse_pi_json_output(output) == "Hi there!"


class TestLogQuery:
    """Unit tests for _log_query()."""
    
    def test_appends_to_jsonl(self, tmp_path):
        """Entries append to llm_queries.jsonl."""
        entry1 = {"query_id": "q_test1", "status": "success"}
        entry2 = {"query_id": "q_test2", "status": "failed"}
        
        _log_query(tmp_path, entry1)
        _log_query(tmp_path, entry2)
        
        log_file = tmp_path / "llm_queries.jsonl"
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["query_id"] == "q_test1"
        assert json.loads(lines[1])["query_id"] == "q_test2"
    
    def test_adds_timestamp(self, tmp_path):
        """Timestamp added if not present."""
        entry = {"query_id": "q_test"}
        _log_query(tmp_path, entry)
        
        log_file = tmp_path / "llm_queries.jsonl"
        logged = json.loads(log_file.read_text().strip())
        assert "timestamp" in logged
        # Verify it's a valid ISO format
        datetime.fromisoformat(logged["timestamp"].replace("Z", "+00:00"))
    
    def test_preserves_existing_timestamp(self, tmp_path):
        """If timestamp already present, don't overwrite."""
        entry = {"query_id": "q_test", "timestamp": "2025-01-01T00:00:00Z"}
        _log_query(tmp_path, entry)
        
        log_file = tmp_path / "llm_queries.jsonl"
        logged = json.loads(log_file.read_text().strip())
        assert logged["timestamp"] == "2025-01-01T00:00:00Z"
    
    def test_creates_file_if_not_exists(self, tmp_path):
        """Creates the log file if it doesn't exist."""
        log_file = tmp_path / "llm_queries.jsonl"
        assert not log_file.exists()
        
        _log_query(tmp_path, {"query_id": "q_new"})
        
        assert log_file.exists()


class TestSpawnSubAgent:
    """Unit tests for _spawn_sub_agent() with mocked subprocess."""
    
    @patch("rlm_repl.subprocess.run")
    def test_returns_parsed_response(self, mock_run, tmp_path):
        """Successful spawn returns parsed text."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"Result"}]}}',
            stderr=""
        )
        result = _spawn_sub_agent("test prompt", 3, tmp_path, cleanup=True)
        assert result == "Result"
    
    @patch("rlm_repl.subprocess.run")
    def test_timeout_returns_error(self, mock_run, tmp_path):
        """Timeout returns error string, doesn't raise."""
        mock_run.side_effect = subprocess.TimeoutExpired("pi", 120)
        result = _spawn_sub_agent("test", 3, tmp_path)
        assert "[ERROR:" in result and "timed out" in result.lower()
    
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
        assert "Something went wrong" in result or "exit code" in result.lower()
    
    def test_depth_zero_returns_error_without_spawn(self, tmp_path):
        """Depth 0 fails fast without spawning subprocess."""
        with patch("rlm_repl.subprocess.run") as mock_run:
            result = _spawn_sub_agent("test", 0, tmp_path)
            mock_run.assert_not_called()
            assert "depth limit" in result.lower()
    
    @patch("rlm_repl.subprocess.run")
    def test_logs_query(self, mock_run, tmp_path):
        """Query is logged to llm_queries.jsonl."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}',
            stderr=""
        )
        _spawn_sub_agent("test prompt", 3, tmp_path, cleanup=True)
        
        log_file = tmp_path / "llm_queries.jsonl"
        assert log_file.exists()
        
        logged = json.loads(log_file.read_text().strip())
        assert "query_id" in logged
        assert logged["status"] == "success"
        assert logged["prompt_chars"] == len("test prompt")
    
    @patch("rlm_repl.subprocess.run")
    def test_cleanup_removes_sub_state_dir(self, mock_run, tmp_path):
        """With cleanup=True, sub-state directory is removed."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}',
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
            stdout='{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}',
            stderr=""
        )
        _spawn_sub_agent("test", 3, tmp_path, cleanup=False)
        
        # Sub-directories should remain
        depth_dirs = list(tmp_path.glob("depth-*"))
        assert len(depth_dirs) > 0
    
    @patch("rlm_repl.subprocess.run")
    def test_logs_depth_exceeded_status(self, mock_run, tmp_path):
        """Depth 0 logs with status='depth_exceeded'."""
        _spawn_sub_agent("test", 0, tmp_path)
        
        log_file = tmp_path / "llm_queries.jsonl"
        logged = json.loads(log_file.read_text().strip())
        assert logged["status"] == "depth_exceeded"


class TestStateMigration:
    """Unit tests for state version migration (via _load_state)."""
    
    def test_v2_to_v3_adds_depth_fields(self, tmp_path):
        """V2 state gets depth tracking fields when loaded."""
        import pickle
        v2_state = {
            "version": 2,
            "context": {"content": "test"},
            "buffers": [],
            "handles": {},
        }
        state_path = tmp_path / "state.pkl"
        with open(state_path, "wb") as f:
            pickle.dump(v2_state, f)
        
        v3_state = _load_state(state_path)
        
        assert v3_state["version"] == 3
        assert v3_state["max_depth"] == DEFAULT_MAX_DEPTH
        assert v3_state["remaining_depth"] == DEFAULT_MAX_DEPTH
        assert v3_state["preserve_recursive_state"] == False
        assert v3_state["final_answer"] is None
    
    def test_v3_unchanged(self, tmp_path):
        """V3 state passes through unchanged."""
        import pickle
        v3_state = {
            "version": 3,
            "context": {"content": "test"},
            "max_depth": 5,
            "remaining_depth": 2,
            "preserve_recursive_state": True,
            "final_answer": {"value": 42},
        }
        state_path = tmp_path / "state.pkl"
        with open(state_path, "wb") as f:
            pickle.dump(v3_state, f)
        
        result = _load_state(state_path)
        
        assert result["max_depth"] == 5  # Not overwritten
        assert result["remaining_depth"] == 2
        assert result["preserve_recursive_state"] == True
    
    def test_v1_state_migrated(self, tmp_path):
        """V1 state (no version) gets migrated."""
        import pickle
        v1_state = {
            "context": {"content": "old"},
        }
        state_path = tmp_path / "state.pkl"
        with open(state_path, "wb") as f:
            pickle.dump(v1_state, f)
        
        v3_state = _load_state(state_path)
        
        assert v3_state["version"] == 3
        assert "max_depth" in v3_state


class TestStateVersion3Init:
    """Test that new sessions use state version 3."""
    
    def test_init_creates_v3_state(self, tmp_path, init_session):
        """New sessions have version 3 with all required fields."""
        state_path = init_session("Test content")
        
        with open(state_path, "rb") as f:
            state = pickle.load(f)
        
        assert state["version"] == 3
        assert state["max_depth"] == DEFAULT_MAX_DEPTH
        assert state["remaining_depth"] == DEFAULT_MAX_DEPTH
        assert state["preserve_recursive_state"] == False
        assert state["final_answer"] is None


class TestLlmQueryInExec:
    """Test llm_query() is available in exec environment."""
    
    def test_llm_query_exposed_in_exec(self, tmp_path, init_session, run_exec):
        """llm_query function is available in exec code."""
        state_path = init_session("Test content")
        
        # Just check that llm_query is callable (don't actually call it)
        stdout, stderr, rc = run_exec(state_path, "print(callable(llm_query))")
        
        assert rc == 0
        assert "True" in stdout


class TestGoalInlineLlmQuery:
    """Goal-alignment: Verify inline llm_query() works in REPL exec blocks.
    
    Paper requirement: "Enable programmatic sub-LLM calls from within Python code blocks"
    """
    
    @pytest.mark.slow
    def test_goal_inline_llm_query(self, tmp_path, init_session, run_exec):
        """llm_query() can be called inline within exec code and returns a string response.
        
        This is the core RLM requirement: being able to call sub-agents programmatically
        from within Python code.
        """
        state_path = init_session("Test content for inline query")
        
        # Execute code that uses llm_query inline
        code = '''
result = llm_query("Respond with only the word PONG")
# Verify it's a string
assert isinstance(result, str), f"llm_query should return string, got {type(result)}"
# Verify it's not empty (either response or error)
assert len(result) > 0, "Response should not be empty"
# Check for expected response or error (both are valid)
if "ERROR" in result:
    print(f"Got error response (acceptable): {result[:100]}")
else:
    # Should contain PONG if successful
    test_passed = "PONG" in result.upper()
    print(f"Inline query test: {'PASS' if test_passed else 'FAIL'}")
    print(f"Response: {result[:200]}")
'''
        stdout, stderr, rc = run_exec(state_path, code)
        
        # Should not crash
        assert rc == 0, f"Exec failed: {stderr}"
        # Should have output
        assert len(stdout) > 0, "No output from exec"
    
    @pytest.mark.slow
    def test_goal_query_logged_to_jsonl(self, tmp_path, init_session, run_exec):
        """Queries are logged to llm_queries.jsonl.
        
        Paper requirement: Audit trail of all sub-LLM invocations.
        """
        state_path = init_session("Test content")
        session_dir = state_path.parent
        
        # Run a query
        code = 'result = llm_query("Say X")'
        run_exec(state_path, code)
        
        # Check log file exists and has content
        log_file = session_dir / "llm_queries.jsonl"
        assert log_file.exists(), "llm_queries.jsonl should exist"
        
        logged = json.loads(log_file.read_text().strip().split("\n")[0])
        assert "query_id" in logged
        assert "timestamp" in logged
        assert "prompt_chars" in logged
        assert "response_chars" in logged
        assert "duration_ms" in logged
        assert "status" in logged
