"""Phase 5 tests: Answer finalization signal.

Tests for:
- set_final_answer() - Mark value as final (JSON-serializable)
- has_final_answer() - Check if answer is set
- get_final_answer() - Retrieve the value
- get-final-answer CLI command - JSON output for external retrieval
- status command - Show final answer info
"""
import json
import pickle
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from collections import namedtuple

import pytest


# Helper to match the existing run_exec return format
ExecResult = namedtuple('ExecResult', ['stdout', 'stderr', 'returncode'])


class TestSetFinalAnswer:
    """Unit tests for set_final_answer()."""

    def test_stores_value_in_state(self, init_session, run_exec):
        """set_final_answer() persists value to state."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer({'result': 42})")
        
        state = pickle.load(open(state_path, "rb"))
        assert state["final_answer"] is not None
        assert state["final_answer"]["value"] == {"result": 42}

    def test_adds_timestamp(self, init_session, run_exec):
        """Timestamp is added in ISO 8601 format."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer('test')")
        
        state = pickle.load(open(state_path, "rb"))
        set_at = state["final_answer"]["set_at"]
        assert set_at is not None
        # Should be ISO 8601 format with UTC timezone
        assert set_at.endswith("+00:00") or set_at.endswith("Z")
        # Should be parseable
        datetime.fromisoformat(set_at.replace("Z", "+00:00"))

    def test_accepts_dict(self, init_session, run_exec):
        """Accepts dict values."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer({'key': 'value', 'nested': {'a': 1}})")
        
        state = pickle.load(open(state_path, "rb"))
        assert state["final_answer"]["value"] == {"key": "value", "nested": {"a": 1}}

    def test_accepts_list(self, init_session, run_exec):
        """Accepts list values."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer([1, 2, 3, 'four'])")
        
        state = pickle.load(open(state_path, "rb"))
        assert state["final_answer"]["value"] == [1, 2, 3, "four"]

    def test_accepts_string(self, init_session, run_exec):
        """Accepts string values."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer('hello world')")
        
        state = pickle.load(open(state_path, "rb"))
        assert state["final_answer"]["value"] == "hello world"

    def test_accepts_number(self, init_session, run_exec):
        """Accepts numeric values."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer(3.14159)")
        
        state = pickle.load(open(state_path, "rb"))
        assert state["final_answer"]["value"] == 3.14159

    def test_accepts_bool(self, init_session, run_exec):
        """Accepts boolean values."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer(True)")
        
        state = pickle.load(open(state_path, "rb"))
        assert state["final_answer"]["value"] is True

    def test_accepts_none(self, init_session, run_exec):
        """Accepts None as a value."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer(None)")
        
        state = pickle.load(open(state_path, "rb"))
        # final_answer is set, but value is None
        assert state["final_answer"] is not None
        assert state["final_answer"]["value"] is None

    def test_rejects_non_serializable(self, init_session, run_exec):
        """Non-JSON-serializable values raise ValueError."""
        state_path = init_session("test content")
        
        # regex pattern is not JSON serializable
        stdout, stderr, rc = run_exec(state_path, """
import re
try:
    set_final_answer(re.compile('test'))
    print("ERROR: Should have raised")
except ValueError as e:
    print(f"Caught: {e}")
""")
        assert "Caught:" in stdout
        assert "JSON-serializable" in stdout

    def test_rejects_function(self, init_session, run_exec):
        """Functions are not JSON-serializable."""
        state_path = init_session("test content")
        
        stdout, stderr, rc = run_exec(state_path, """
def my_func():
    pass
try:
    set_final_answer(my_func)
    print("ERROR: Should have raised")
except ValueError as e:
    print(f"Caught: {e}")
""")
        assert "Caught:" in stdout

    def test_overwrites_previous(self, init_session, run_exec):
        """Subsequent calls overwrite previous answer."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer('first')")
        run_exec(state_path, "set_final_answer('second')")
        
        state = pickle.load(open(state_path, "rb"))
        assert state["final_answer"]["value"] == "second"

    def test_prints_confirmation_with_type(self, init_session, run_exec):
        """Prints confirmation message with type info."""
        state_path = init_session("test content")
        
        stdout, stderr, rc = run_exec(state_path, "set_final_answer({'a': 1})")
        assert "Final answer set" in stdout
        assert "dict" in stdout

    def test_prints_length_for_collections(self, init_session, run_exec):
        """Prints length for lists, dicts, and strings."""
        state_path = init_session("test content")
        
        stdout, stderr, rc = run_exec(state_path, "set_final_answer([1, 2, 3])")
        assert "length: 3" in stdout


class TestHasFinalAnswer:
    """Unit tests for has_final_answer()."""

    def test_false_initially(self, init_session, run_exec):
        """Returns False when no answer set."""
        state_path = init_session("test content")
        
        stdout, stderr, rc = run_exec(state_path, "print(has_final_answer())")
        assert "False" in stdout

    def test_true_after_set(self, init_session, run_exec):
        """Returns True after set_final_answer() called."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer('test')")
        stdout, stderr, rc = run_exec(state_path, "print(has_final_answer())")
        assert "True" in stdout

    def test_true_even_when_value_is_none(self, init_session, run_exec):
        """Returns True when value is explicitly set to None."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer(None)")
        stdout, stderr, rc = run_exec(state_path, "print(has_final_answer())")
        assert "True" in stdout


class TestGetFinalAnswer:
    """Unit tests for get_final_answer()."""

    def test_returns_none_if_not_set(self, init_session, run_exec):
        """Returns None when no answer set."""
        state_path = init_session("test content")
        
        stdout, stderr, rc = run_exec(state_path, "print(repr(get_final_answer()))")
        assert "None" in stdout

    def test_returns_value_if_set(self, init_session, run_exec):
        """Returns the value after set_final_answer() called."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer({'result': 42})")
        stdout, stderr, rc = run_exec(state_path, "print(get_final_answer())")
        assert "{'result': 42}" in stdout

    def test_returns_list(self, init_session, run_exec):
        """Returns list values correctly."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer([1, 2, 3])")
        stdout, stderr, rc = run_exec(state_path, "print(get_final_answer())")
        assert "[1, 2, 3]" in stdout


class TestGetFinalAnswerCLI:
    """Unit tests for get-final-answer CLI command."""

    def test_outputs_valid_json(self, init_session, rlm_repl_path):
        """CLI outputs valid JSON."""
        state_path = init_session("test content")
        
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "get-final-answer"],
            capture_output=True, text=True
        )
        
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "set" in data
        assert "value" in data

    def test_shows_set_false_when_not_set(self, init_session, rlm_repl_path):
        """Shows set: false when no answer set."""
        state_path = init_session("test content")
        
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "get-final-answer"],
            capture_output=True, text=True
        )
        
        data = json.loads(result.stdout)
        assert data["set"] is False
        assert data["value"] is None
        assert data["set_at"] is None

    def test_shows_set_true_with_value(self, init_session, run_exec, rlm_repl_path):
        """Shows set: true with value after set."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer({'result': 42})")
        
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "get-final-answer"],
            capture_output=True, text=True
        )
        
        data = json.loads(result.stdout)
        assert data["set"] is True
        assert data["value"] == {"result": 42}
        assert data["set_at"] is not None

    def test_includes_timestamp(self, init_session, run_exec, rlm_repl_path):
        """Includes set_at timestamp in output."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer('test')")
        
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "get-final-answer"],
            capture_output=True, text=True
        )
        
        data = json.loads(result.stdout)
        assert data["set_at"].endswith("+00:00") or data["set_at"].endswith("Z")


class TestStatusCommandFinalAnswer:
    """Tests for final answer display in status command."""

    def test_shows_not_set_initially(self, init_session, rlm_repl_path):
        """Status shows 'NOT SET' when no answer."""
        state_path = init_session("test content")
        
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "status"],
            capture_output=True, text=True
        )
        
        assert "Final answer: NOT SET" in result.stdout

    def test_shows_set_with_type(self, init_session, run_exec, rlm_repl_path):
        """Status shows 'SET' with type info."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer({'a': 1, 'b': 2})")
        
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "status"],
            capture_output=True, text=True
        )
        
        assert "Final answer: SET" in result.stdout
        assert "dict" in result.stdout
        assert "length: 2" in result.stdout

    def test_shows_list_length(self, init_session, run_exec, rlm_repl_path):
        """Status shows list length."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer([1, 2, 3, 4, 5])")
        
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "status"],
            capture_output=True, text=True
        )
        
        assert "Final answer: SET" in result.stdout
        assert "list" in result.stdout
        assert "length: 5" in result.stdout

    def test_shows_int_type(self, init_session, run_exec, rlm_repl_path):
        """Status shows int type without length."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer(42)")
        
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "status"],
            capture_output=True, text=True
        )
        
        assert "Final answer: SET" in result.stdout
        assert "int" in result.stdout


class TestGoalFinalizationSignal:
    """Goal-alignment: Main agent can retrieve final answer.
    
    Paper requirement: "Add set_final_answer() to mark variables for retrieval"
    """

    def test_goal_finalization_signal(self, init_session, run_exec, rlm_repl_path):
        """Full cycle: set in exec, retrieve via CLI."""
        state_path = init_session("test content")
        
        # Set answer via exec
        run_exec(state_path, "set_final_answer({'summary': 'Done', 'count': 42})")
        
        # Retrieve via CLI
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "get-final-answer"],
            capture_output=True, text=True
        )
        
        data = json.loads(result.stdout)
        assert data["set"] is True
        assert data["value"]["summary"] == "Done"
        assert data["value"]["count"] == 42
        assert data["set_at"] is not None

    def test_goal_conditional_set(self, init_session, run_exec):
        """Pattern: Check before setting to avoid overwrite."""
        state_path = init_session("test content")
        
        # First call sets
        stdout1, stderr1, rc1 = run_exec(state_path, """
if not has_final_answer():
    set_final_answer('first')
    print('Set first answer')
else:
    print('Already set')
""")
        assert "Set first answer" in stdout1
        
        # Second call skips
        stdout2, stderr2, rc2 = run_exec(state_path, """
if not has_final_answer():
    set_final_answer('second')
    print('Set second answer')
else:
    print('Already set')
""")
        assert "Already set" in stdout2
        
        # Value is still 'first'
        state = pickle.load(open(state_path, "rb"))
        assert state["final_answer"]["value"] == "first"

    def test_goal_complex_result(self, init_session, run_exec, rlm_repl_path):
        """Set a complex nested structure as final answer."""
        state_path = init_session("test content")
        
        run_exec(state_path, """
result = {
    'chunks_processed': 10,
    'summaries': ['A', 'B', 'C'],
    'metadata': {
        'source': 'test.md',
        'timestamp': '2026-01-21T12:00:00Z'
    }
}
set_final_answer(result)
""")
        
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "get-final-answer"],
            capture_output=True, text=True
        )
        
        data = json.loads(result.stdout)
        assert data["value"]["chunks_processed"] == 10
        assert len(data["value"]["summaries"]) == 3
        assert data["value"]["metadata"]["source"] == "test.md"


class TestEdgeCases:
    """Edge case tests for finalization."""

    def test_empty_dict(self, init_session, run_exec):
        """Handles empty dict."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer({})")
        
        state = pickle.load(open(state_path, "rb"))
        assert state["final_answer"]["value"] == {}

    def test_empty_list(self, init_session, run_exec):
        """Handles empty list."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer([])")
        
        state = pickle.load(open(state_path, "rb"))
        assert state["final_answer"]["value"] == []

    def test_empty_string(self, init_session, run_exec):
        """Handles empty string."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer('')")
        
        state = pickle.load(open(state_path, "rb"))
        assert state["final_answer"]["value"] == ""

    def test_unicode_content(self, init_session, run_exec, rlm_repl_path):
        """Handles unicode content."""
        state_path = init_session("test content")
        
        run_exec(state_path, "set_final_answer({'emoji': '🎉', 'chinese': '你好'})")
        
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "get-final-answer"],
            capture_output=True, text=True
        )
        
        data = json.loads(result.stdout)
        assert data["value"]["emoji"] == "🎉"
        assert data["value"]["chinese"] == "你好"

    def test_large_value(self, init_session, run_exec):
        """Handles large values."""
        state_path = init_session("test content")
        
        # Create a large list
        run_exec(state_path, "set_final_answer(list(range(1000)))")
        
        state = pickle.load(open(state_path, "rb"))
        assert len(state["final_answer"]["value"]) == 1000

    def test_deeply_nested(self, init_session, run_exec, rlm_repl_path):
        """Handles deeply nested structures."""
        state_path = init_session("test content")
        
        run_exec(state_path, """
deep = {'a': {'b': {'c': {'d': {'e': 'deep'}}}}}
set_final_answer(deep)
""")
        
        result = subprocess.run(
            [sys.executable, str(rlm_repl_path),
             "--state", str(state_path),
             "get-final-answer"],
            capture_output=True, text=True
        )
        
        data = json.loads(result.stdout)
        assert data["value"]["a"]["b"]["c"]["d"]["e"] == "deep"
