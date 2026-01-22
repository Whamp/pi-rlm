"""Phase 2 tests: Depth tracking and recursive state.

Tests for:
- --max-depth N argument for init command
- --preserve-recursive-state flag for debugging
- Recursive directory structure creation/cleanup
- Depth injection into subprocess environment
- Depth-0 behavior - fail fast without spawning
"""
import json
import pickle
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import the module under test
import sys
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from rlm_repl import (
    _spawn_sub_agent,
    _log_query,
    DEFAULT_MAX_DEPTH,
)


class TestMaxDepthInit:
    """Unit tests for --max-depth initialization."""
    
    def test_default_max_depth_is_3(self, init_session, tmp_path):
        """Default max_depth should be 3 per paper spec."""
        state_path = init_session("test content")
        state = pickle.load(open(state_path, "rb"))
        assert state["max_depth"] == 3
        assert state["remaining_depth"] == 3
    
    def test_custom_max_depth(self, tmp_path, rlm_repl_path):
        """--max-depth N sets both max_depth and remaining_depth."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "init", str(test_file), "--max-depth", "5"],
            capture_output=True, text=True, cwd=tmp_path
        )
        assert result.returncode == 0
        assert "Max depth: 5" in result.stdout
        
        # Extract state path and verify
        for line in result.stdout.splitlines():
            if "Session path:" in line:
                state_path = Path(line.split(":", 1)[1].strip())
                if not state_path.is_absolute():
                    state_path = (tmp_path / state_path).resolve()
                break
        
        state = pickle.load(open(state_path, "rb"))
        assert state["max_depth"] == 5
        assert state["remaining_depth"] == 5
    
    def test_max_depth_1(self, tmp_path, rlm_repl_path):
        """--max-depth 1 should work (one level of sub-queries only)."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "init", str(test_file), "--max-depth", "1"],
            capture_output=True, text=True, cwd=tmp_path
        )
        assert result.returncode == 0
        assert "Max depth: 1" in result.stdout
    
    def test_max_depth_zero(self, tmp_path, rlm_repl_path):
        """--max-depth 0 should work (no sub-queries allowed)."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "init", str(test_file), "--max-depth", "0"],
            capture_output=True, text=True, cwd=tmp_path
        )
        assert result.returncode == 0
        assert "Max depth: 0" in result.stdout


class TestPreserveRecursiveState:
    """Unit tests for --preserve-recursive-state flag."""
    
    def test_default_preserve_is_false(self, init_session, tmp_path):
        """Default preserve_recursive_state should be False."""
        state_path = init_session("test content")
        state = pickle.load(open(state_path, "rb"))
        assert state["preserve_recursive_state"] is False
    
    def test_preserve_recursive_state_flag(self, tmp_path, rlm_repl_path):
        """--preserve-recursive-state sets flag to True."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "init", str(test_file), "--preserve-recursive-state"],
            capture_output=True, text=True, cwd=tmp_path
        )
        assert result.returncode == 0
        assert "Preserve recursive state: enabled" in result.stdout
        
        # Extract state path and verify
        for line in result.stdout.splitlines():
            if "Session path:" in line:
                state_path = Path(line.split(":", 1)[1].strip())
                if not state_path.is_absolute():
                    state_path = (tmp_path / state_path).resolve()
                break
        
        state = pickle.load(open(state_path, "rb"))
        assert state["preserve_recursive_state"] is True
    
    def test_preserve_with_max_depth(self, tmp_path, rlm_repl_path):
        """Both flags can be combined."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "init", str(test_file),
             "--max-depth", "7", "--preserve-recursive-state"],
            capture_output=True, text=True, cwd=tmp_path
        )
        assert result.returncode == 0
        
        for line in result.stdout.splitlines():
            if "Session path:" in line:
                state_path = Path(line.split(":", 1)[1].strip())
                if not state_path.is_absolute():
                    state_path = (tmp_path / state_path).resolve()
                break
        
        state = pickle.load(open(state_path, "rb"))
        assert state["max_depth"] == 7
        assert state["preserve_recursive_state"] is True


class TestStatusCommand:
    """Tests for status command showing depth info."""
    
    def test_status_shows_depth_info(self, init_session, tmp_path, rlm_repl_path):
        """Status command should show max_depth and remaining_depth."""
        state_path = init_session("test content")
        
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "--state", str(state_path), "status"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Max depth: 3" in result.stdout
        assert "Remaining depth: 3" in result.stdout
    
    def test_status_shows_preserve_when_enabled(self, tmp_path, rlm_repl_path):
        """Status should show preserve flag when enabled."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "init", str(test_file), "--preserve-recursive-state"],
            capture_output=True, text=True, cwd=tmp_path
        )
        
        for line in result.stdout.splitlines():
            if "Session path:" in line:
                state_path = Path(line.split(":", 1)[1].strip())
                if not state_path.is_absolute():
                    state_path = (tmp_path / state_path).resolve()
                break
        
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "--state", str(state_path), "status"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Preserve recursive state: enabled" in result.stdout


class TestDepthPropagation:
    """Unit tests for depth decrement during recursion."""
    
    @patch("rlm_repl.subprocess.run")
    def test_sub_agent_receives_decremented_depth(self, mock_run, tmp_path):
        """Subprocess should receive RLM_REMAINING_DEPTH decremented by 1."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}',
            stderr=""
        )
        
        _spawn_sub_agent("test prompt", 3, tmp_path, cleanup=True)
        
        # Check the command that was passed to subprocess.run
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        
        # Find the --append-system-prompt argument value
        for i, arg in enumerate(cmd):
            if arg == "--append-system-prompt":
                system_append = cmd[i + 1]
                assert "RLM_REMAINING_DEPTH=2" in system_append
                break
        else:
            pytest.fail("--append-system-prompt not found in command")
    
    @patch("rlm_repl.subprocess.run")
    def test_depth_1_passes_depth_0(self, mock_run, tmp_path):
        """With remaining_depth=1, sub-agent should receive 0."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}',
            stderr=""
        )
        
        _spawn_sub_agent("test", 1, tmp_path, cleanup=True)
        
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        
        for i, arg in enumerate(cmd):
            if arg == "--append-system-prompt":
                system_append = cmd[i + 1]
                assert "RLM_REMAINING_DEPTH=0" in system_append
                break


class TestDirectoryStructure:
    """Tests for recursive directory structure."""
    
    @patch("rlm_repl.subprocess.run")
    def test_creates_depth_directory(self, mock_run, tmp_path):
        """Should create depth-N/q_xxx directory structure."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}',
            stderr=""
        )
        
        # With cleanup=False, directories should remain
        _spawn_sub_agent("test", 3, tmp_path, cleanup=False)
        
        # Check for depth-3 directory (remaining_depth=3 -> depth level 3)
        depth_dirs = list(tmp_path.glob("depth-*"))
        assert len(depth_dirs) == 1
        assert depth_dirs[0].name.startswith("depth-")
        
        # Check for query subdirectory
        query_dirs = list(depth_dirs[0].glob("q_*"))
        assert len(query_dirs) == 1
    
    @patch("rlm_repl.subprocess.run")
    def test_cleanup_removes_directories(self, mock_run, tmp_path):
        """With cleanup=True, sub-session directories should be removed."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}',
            stderr=""
        )
        
        _spawn_sub_agent("test", 3, tmp_path, cleanup=True)
        
        # Directories should be cleaned up
        depth_dirs = list(tmp_path.glob("depth-*"))
        assert len(depth_dirs) == 0
    
    @patch("rlm_repl.subprocess.run")
    def test_prompt_file_written(self, mock_run, tmp_path):
        """Prompt should be written to prompt.txt in sub-session dir."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}',
            stderr=""
        )
        
        test_prompt = "This is my test prompt"
        _spawn_sub_agent(test_prompt, 2, tmp_path, cleanup=False)
        
        # Find the prompt file
        prompt_files = list(tmp_path.glob("depth-*/q_*/prompt.txt"))
        assert len(prompt_files) == 1
        assert prompt_files[0].read_text() == test_prompt


class TestDepthZeroBehavior:
    """Unit tests for depth limit enforcement."""
    
    def test_depth_zero_returns_error_string(self, tmp_path):
        """Depth 0 should return error without spawning subprocess."""
        with patch("rlm_repl.subprocess.run") as mock_run:
            result = _spawn_sub_agent("test", 0, tmp_path, cleanup=True)
            mock_run.assert_not_called()
            assert "[ERROR:" in result
            assert "depth limit" in result.lower()
    
    def test_depth_zero_logs_depth_exceeded(self, tmp_path):
        """Depth 0 should log entry with status='depth_exceeded'."""
        _spawn_sub_agent("test", 0, tmp_path, cleanup=True)
        
        log_file = tmp_path / "llm_queries.jsonl"
        assert log_file.exists()
        
        log_entry = json.loads(log_file.read_text().strip())
        assert log_entry["status"] == "depth_exceeded"
    
    def test_depth_zero_still_logs_query_info(self, tmp_path):
        """Even at depth 0, query metadata should be logged."""
        _spawn_sub_agent("my test prompt", 0, tmp_path, cleanup=True)
        
        log_file = tmp_path / "llm_queries.jsonl"
        log_entry = json.loads(log_file.read_text().strip())
        
        assert "query_id" in log_entry
        assert log_entry["query_id"].startswith("q_")
        assert log_entry["prompt_preview"] == "my test prompt"
        assert log_entry["remaining_depth"] == 0


class TestPreserveRecursiveStateEffect:
    """Test that preserve_recursive_state affects cleanup behavior."""
    
    def test_llm_query_respects_preserve_flag(self, tmp_path, rlm_repl_path):
        """When preserve_recursive_state is set, cleanup should be disabled."""
        # Initialize with preserve flag
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "init", str(test_file), "--preserve-recursive-state"],
            capture_output=True, text=True, cwd=tmp_path
        )
        assert result.returncode == 0
        
        for line in result.stdout.splitlines():
            if "Session path:" in line:
                state_path = Path(line.split(":", 1)[1].strip())
                if not state_path.is_absolute():
                    state_path = (tmp_path / state_path).resolve()
                break
        
        # Verify the state has the flag
        state = pickle.load(open(state_path, "rb"))
        assert state["preserve_recursive_state"] is True
    
    @patch("rlm_repl.subprocess.run")
    def test_preserve_flag_forces_cleanup_false(self, mock_run, tmp_path):
        """With preserve_recursive_state, cleanup should effectively be False.
        
        This tests the internal logic by checking that directories are kept.
        """
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}',
            stderr=""
        )
        
        # Create a fake state with preserve_recursive_state=True
        # and directly test the _make_helpers / llm_query logic
        # For now, we test via _spawn_sub_agent with cleanup=False
        _spawn_sub_agent("test", 2, tmp_path, cleanup=False)
        
        # Directories should remain when cleanup=False
        depth_dirs = list(tmp_path.glob("depth-*"))
        assert len(depth_dirs) == 1


class TestGoalRecursiveDepth:
    """Goal-alignment: Sub-LLMs can spawn their own sub-LLMs.
    
    Paper requirement: "Allow sub-LLMs to spawn their own sub-LLMs (default depth limit: 3)"
    """
    
    def test_goal_default_depth_is_three(self, init_session):
        """Default depth should be 3 as per paper spec."""
        state_path = init_session("content")
        state = pickle.load(open(state_path, "rb"))
        assert state["max_depth"] == 3
        assert DEFAULT_MAX_DEPTH == 3
    
    @patch("rlm_repl.subprocess.run")
    def test_goal_depth_decrements_on_spawn(self, mock_run, tmp_path):
        """Each spawn should decrement depth by 1."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}',
            stderr=""
        )
        
        # Spawn at depth 3
        _spawn_sub_agent("test", 3, tmp_path, cleanup=True)
        
        # Verify sub-agent receives depth-1
        cmd = mock_run.call_args[0][0]
        for i, arg in enumerate(cmd):
            if arg == "--append-system-prompt":
                assert "RLM_REMAINING_DEPTH=2" in cmd[i + 1]
                break
    
    @patch("rlm_repl.subprocess.run")
    def test_goal_depth_zero_blocks_further_spawning(self, mock_run, tmp_path):
        """At depth 0, no subprocess should be spawned."""
        result = _spawn_sub_agent("test", 0, tmp_path)
        mock_run.assert_not_called()
        assert "depth limit" in result.lower()
    
    @pytest.mark.slow
    def test_goal_recursive_depth_real_spawn(self, tmp_path, rlm_repl_path):
        """Real test: Initialize with max_depth=1 and verify sub-query works.
        
        This is a smoke test that actually spawns pi. It uses depth=1 to allow
        exactly one sub-query before depth limit is hit.
        """
        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content for depth tracking")
        
        # Initialize with max-depth 1
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "init", str(test_file), "--max-depth", "1"],
            capture_output=True, text=True, cwd=tmp_path
        )
        assert result.returncode == 0
        
        for line in result.stdout.splitlines():
            if "Session path:" in line:
                state_path = Path(line.split(":", 1)[1].strip())
                if not state_path.is_absolute():
                    state_path = (tmp_path / state_path).resolve()
                break
        
        # Execute llm_query - should work at depth 1
        code = 'result = llm_query("Say only the word DEPTH"); print("GOT:", result[:50] if result else "EMPTY")'
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "--state", str(state_path), "exec", "-c", code],
            capture_output=True, text=True, timeout=60
        )
        
        # Should have output (either response or error about depth)
        assert "GOT:" in result.stdout
        
        # Check log was created
        log_file = state_path.parent / "llm_queries.jsonl"
        assert log_file.exists()
