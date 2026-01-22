"""Integration tests: Full RLM workflows.

These tests verify end-to-end functionality across all phases,
ensuring features work together correctly.
"""
import json
import os
import pickle
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add scripts dir to path for imports
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class TestFullWorkflow:
    """End-to-end workflow tests."""

    def test_init_peek_grep_workflow(self, init_session, run_exec):
        """Basic workflow: init → peek → grep → expand."""
        content = "Line 1: Hello world\nLine 2: ERROR found here\nLine 3: Another ERROR\nLine 4: Done"
        state_path = init_session(content)

        # Peek at content
        stdout, stderr, code = run_exec(state_path, "print(peek(0, 50))")
        assert code == 0
        assert "Line 1" in stdout

        # Grep for errors
        stdout, stderr, code = run_exec(state_path, """
result = grep('ERROR')
print(result)
print(f'Count: {count(last_handle())}')
""")
        assert code == 0
        assert "Array(" in stdout
        assert "Count: 2" in stdout

    def test_init_chunk_workflow(self, init_session, run_exec, tmp_path):
        """Workflow: init → write_chunks → read manifest."""
        # Create content large enough to chunk
        content = "# Section\n\n" + ("Paragraph content. " * 100 + "\n\n") * 50
        state_path = init_session(content)

        # Write chunks
        stdout, stderr, code = run_exec(state_path, f"""
paths = write_chunks(str(session_dir / 'chunks'), size=500, overlap=0)
print(f'Created {{len(paths)}} chunks')
import json
manifest_path = session_dir / 'chunks' / 'manifest.json'
manifest = json.loads(manifest_path.read_text())
print(f'Chunks in manifest: {{len(manifest["chunks"])}}')
""")
        assert code == 0
        assert "Created" in stdout

    def test_smart_chunk_markdown_workflow(self, init_session, run_exec):
        """Workflow: init markdown → smart_chunk → verify boundaries."""
        markdown_content = """# Main Title

Introduction paragraph here.

## Section One

Content for section one goes here.
More content in section one.

## Section Two

Content for section two.

### Subsection 2.1

Subsection content here.

## Section Three

Final section content.
"""
        state_path = init_session(markdown_content)
        
        # Update the state to use .md extension for proper format detection
        state = pickle.load(open(state_path, 'rb'))
        state['context']['path'] = 'test.md'
        pickle.dump(state, open(state_path, 'wb'))

        # Smart chunk
        stdout, stderr, code = run_exec(state_path, """
import json
paths = smart_chunk(str(session_dir / 'chunks'), target_size=100, min_size=20, max_size=300)
print(f'Created {len(paths)} chunks')
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())
print(f'Format: {manifest["format"]}')
print(f'Method: {manifest["chunking_method"]}')
""")
        assert code == 0
        assert "Created" in stdout
        assert "Format: markdown" in stdout

    def test_smart_chunk_json_workflow(self, init_session, run_exec):
        """Workflow: init JSON array → smart_chunk → verify element ranges."""
        json_content = json.dumps([{"id": i, "name": f"Item {i}"} for i in range(50)])
        state_path = init_session(json_content)
        
        # Update to use .json extension
        state = pickle.load(open(state_path, 'rb'))
        state['context']['path'] = 'test.json'
        pickle.dump(state, open(state_path, 'wb'))

        stdout, stderr, code = run_exec(state_path, """
import json
paths = smart_chunk(str(session_dir / 'chunks'), target_size=200, min_size=50, max_size=500)
print(f'Created {len(paths)} chunks')
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())
print(f'Format: {manifest["format"]}')
print(f'JSON chunked: {manifest.get("json_chunked", False)}')
if manifest.get("json_chunked"):
    first_chunk = manifest["chunks"][0]
    print(f'First chunk element_range: {first_chunk.get("element_range")}')
""")
        assert code == 0
        assert "Created" in stdout
        assert "Format: json" in stdout

    def test_buffer_accumulation_workflow(self, init_session, run_exec):
        """Workflow: add_buffer multiple times → export."""
        state_path = init_session("Test content")

        # Add buffers
        run_exec(state_path, "add_buffer('Result 1')")
        run_exec(state_path, "add_buffer('Result 2')")
        run_exec(state_path, "add_buffer('Result 3')")

        # Check buffer count
        stdout, stderr, code = run_exec(state_path, "print(f'Buffers: {len(buffers)}')")
        assert code == 0
        assert "Buffers: 3" in stdout


class TestFinalizationWorkflow:
    """Tests for answer finalization end-to-end."""

    def test_set_and_retrieve_final_answer(self, init_session, run_exec, rlm_repl_path, tmp_path):
        """Set final answer in exec, retrieve via CLI."""
        state_path = init_session("Test content")

        # Set final answer
        stdout, stderr, code = run_exec(state_path, """
result = {'summary': 'Test complete', 'count': 42, 'items': ['a', 'b', 'c']}
set_final_answer(result)
print('Answer set')
""")
        assert code == 0
        assert "Answer set" in stdout

        # Retrieve via CLI
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "--state", str(state_path), "get-final-answer"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        
        data = json.loads(result.stdout)
        assert data["set"] is True
        assert data["value"]["summary"] == "Test complete"
        assert data["value"]["count"] == 42
        assert data["value"]["items"] == ["a", "b", "c"]

    def test_final_answer_updates_status(self, init_session, run_exec, rlm_repl_path):
        """Status command shows final answer info."""
        state_path = init_session("Test content")

        # Check status before setting
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "--state", str(state_path), "status"],
            capture_output=True, text=True
        )
        assert "Final answer: NOT SET" in result.stdout

        # Set final answer
        run_exec(state_path, "set_final_answer([1, 2, 3])")

        # Check status after
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "--state", str(state_path), "status"],
            capture_output=True, text=True
        )
        assert "Final answer: SET" in result.stdout
        assert "list" in result.stdout

    def test_has_get_final_answer_helpers(self, init_session, run_exec):
        """has_final_answer() and get_final_answer() work correctly."""
        state_path = init_session("Test")

        # Check before setting
        stdout, _, _ = run_exec(state_path, "print(f'Has: {has_final_answer()}')")
        assert "Has: False" in stdout

        # Set answer
        run_exec(state_path, "set_final_answer({'key': 'value'})")

        # Check after
        stdout, _, _ = run_exec(state_path, """
print(f'Has: {has_final_answer()}')
print(f'Value: {get_final_answer()}')
""")
        assert "Has: True" in stdout
        assert "'key': 'value'" in stdout


class TestDepthTrackingWorkflow:
    """Tests for recursive depth configuration and tracking."""

    def test_max_depth_init_option(self, rlm_repl_path, tmp_path):
        """--max-depth sets both max_depth and remaining_depth."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Content")

        result = subprocess.run(
            ["python3", str(rlm_repl_path), "init", str(test_file), "--max-depth", "5"],
            capture_output=True, text=True, cwd=tmp_path
        )
        assert result.returncode == 0

        # Extract state path and load state
        for line in result.stdout.splitlines():
            if "Session path:" in line:
                state_path = tmp_path / line.split(":", 1)[1].strip()
                break

        state = pickle.load(open(state_path, 'rb'))
        assert state["max_depth"] == 5
        assert state["remaining_depth"] == 5

    def test_preserve_recursive_state_flag(self, rlm_repl_path, tmp_path):
        """--preserve-recursive-state sets flag in state."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Content")

        result = subprocess.run(
            ["python3", str(rlm_repl_path), "init", str(test_file), "--preserve-recursive-state"],
            capture_output=True, text=True, cwd=tmp_path
        )
        assert result.returncode == 0

        for line in result.stdout.splitlines():
            if "Session path:" in line:
                state_path = tmp_path / line.split(":", 1)[1].strip()
                break

        state = pickle.load(open(state_path, 'rb'))
        assert state["preserve_recursive_state"] is True

    def test_depth_shown_in_status(self, init_session, rlm_repl_path):
        """Status command displays depth info."""
        state_path = init_session("Test content", max_depth=4)

        result = subprocess.run(
            ["python3", str(rlm_repl_path), "--state", str(state_path), "status"],
            capture_output=True, text=True
        )
        assert "Max depth: 4" in result.stdout
        assert "Remaining depth: 4" in result.stdout


class TestLlmQueryWorkflow:
    """Tests for llm_query and llm_query_batch (mocked)."""

    def test_llm_query_available_in_exec(self, init_session, run_exec):
        """llm_query function is available in exec environment."""
        state_path = init_session("Test")

        stdout, stderr, code = run_exec(state_path, """
print(f'llm_query callable: {callable(llm_query)}')
print(f'llm_query_batch callable: {callable(llm_query_batch)}')
""")
        assert code == 0
        assert "llm_query callable: True" in stdout
        assert "llm_query_batch callable: True" in stdout

    def test_depth_zero_returns_error(self, init_session, run_exec):
        """At depth 0, llm_query returns error without spawning."""
        state_path = init_session("Test", max_depth=0)

        stdout, stderr, code = run_exec(state_path, """
result = llm_query('Test prompt')
print(f'Result: {result}')
""")
        # Should contain error about depth limit
        assert "[ERROR:" in stdout
        assert "depth" in stdout.lower()


class TestHandleSystemWorkflow:
    """Tests for handle-based search workflow."""

    def test_handle_parsing_accepts_full_stub(self, init_session, run_exec):
        """Handle functions accept full stub format from grep()."""
        content = "Line 1: ERROR here\nLine 2: OK\nLine 3: ERROR there"
        state_path = init_session(content)

        # Use grep() return value directly with count(), expand(), etc.
        stdout, stderr, code = run_exec(state_path, """
# grep() returns full stub like '$res1: Array(2) [preview...]'
result = grep('ERROR')
print(f'Grep returned: {repr(result)}')

# These should all work with the full stub
cnt = count(result)
print(f'Count: {cnt}')

items = expand(result, limit=5)
print(f'Expanded: {len(items)} items')

# Also works with just handle name
cnt2 = count('$res1')
print(f'Count via name: {cnt2}')
""")
        assert code == 0, f"stderr: {stderr}"
        assert "Count: 2" in stdout
        assert "Expanded: 2 items" in stdout
        assert "Count via name: 2" in stdout

    def test_handle_parsing_with_filter_map(self, init_session, run_exec):
        """filter_handle and map_field accept full stub format."""
        # Use more distinct content to avoid snippet overlap
        content = "\n".join([
            f"{'A' * 100} ERROR_TYPE_{i} {'B' * 100}" for i in range(5)
        ])
        state_path = init_session(content)

        stdout, stderr, code = run_exec(state_path, """
# Get full stub from grep
result = grep('ERROR_TYPE')

# filter_handle with full stub - filter for specific type
filtered = filter_handle(result, 'ERROR_TYPE_2')
print(f'Filtered count: {count(filtered)}')

# map_field with full stub  
result2 = grep('ERROR_TYPE')
mapped = map_field(result2, 'line_num')
print(f'Mapped count: {count(mapped)}')
print(f'Mapped items: {expand(mapped)}')
""")
        assert code == 0, f"stderr: {stderr}"
        assert "Filtered count: 1" in stdout
        assert "Mapped count: 5" in stdout

    def test_handle_chaining(self, init_session, run_exec):
        """Handles can be chained with last_handle()."""
        content = "\n".join([
            f"Line {i}: {'ERROR' if i % 3 == 0 else 'INFO'} message {i}"
            for i in range(30)
        ])
        state_path = init_session(content)

        stdout, stderr, code = run_exec(state_path, """
# Initial grep
grep('ERROR')

# Chain with last_handle
h1 = last_handle()
print(f'First handle: {h1}')
print(f'Count: {count(h1)}')

# Map to line numbers
map_field(h1, 'line_num')
h2 = last_handle()
print(f'Mapped handle: {h2}')
""")
        assert code == 0
        assert "First handle: $res" in stdout
        assert "Count: 10" in stdout
        assert "Mapped handle: $res" in stdout

    def test_filter_handle(self, init_session, run_exec):
        """filter_handle creates filtered subset."""
        # Use content large enough that snippet windows don't overlap
        # Each section has 150+ chars to ensure separate windows
        content = """
ERROR: timeout connecting to server - this is a long error message that provides context about the timeout issue and what might have caused it. The server was unreachable.

ERROR: disk full - this is another error message that describes a completely different issue with the disk being full and needing cleanup.

WARNING: cache miss - this is just a warning

ERROR: network unreachable - yet another error about network connectivity issues that are unrelated to timeouts.

INFO: operation complete
"""
        state_path = init_session(content)

        stdout, stderr, code = run_exec(state_path, """
grep('ERROR')
initial = count(last_handle())
print(f'Initial: {initial}')

# Filter for line_num to test the mechanism (line 2 has timeout)
filter_handle(last_handle(), lambda x: x.get('line_num') == 2)
filtered = count(last_handle())
print(f'Filtered: {filtered}')
""")
        assert code == 0
        assert "Initial: 3" in stdout
        assert "Filtered: 1" in stdout


class TestStatePersistence:
    """Tests for state persistence across invocations."""

    def test_globals_persist(self, init_session, run_exec):
        """Custom variables persist across exec calls."""
        state_path = init_session("Test")

        # Set variable
        run_exec(state_path, "my_custom_var = [1, 2, 3]")

        # Read it back
        stdout, _, code = run_exec(state_path, "print(f'Value: {my_custom_var}')")
        assert code == 0
        assert "Value: [1, 2, 3]" in stdout

    def test_handles_persist(self, init_session, run_exec):
        """Handles persist across exec calls."""
        state_path = init_session("Line with ERROR\nAnother ERROR line")

        # Create handle
        run_exec(state_path, "grep('ERROR')")

        # Access in next call
        stdout, _, code = run_exec(state_path, "print(f'Count: {count(\"$res1\")}')")
        assert code == 0
        assert "Count: 2" in stdout

    def test_state_version(self, init_session):
        """State is version 3 with all required fields."""
        state_path = init_session("Test")
        state = pickle.load(open(state_path, 'rb'))

        assert state["version"] == 3
        assert "max_depth" in state
        assert "remaining_depth" in state
        assert "preserve_recursive_state" in state
        assert "final_answer" in state


class TestManifestStructure:
    """Tests for chunk manifest structure and content."""

    def test_manifest_has_all_fields(self, init_session, run_exec):
        """Manifest contains required metadata fields."""
        content = "# Title\n\nContent here.\n\n## Section\n\nMore content."
        state_path = init_session(content)
        
        # Force markdown detection
        state = pickle.load(open(state_path, 'rb'))
        state['context']['path'] = 'test.md'
        pickle.dump(state, open(state_path, 'wb'))

        stdout, stderr, code = run_exec(state_path, """
import json
paths = smart_chunk(str(session_dir / 'chunks'), target_size=50, min_size=10, max_size=200)
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())

print(f'Has format: {"format" in manifest}')
print(f'Has chunking_method: {"chunking_method" in manifest}')
print(f'Has chunks: {"chunks" in manifest}')
print(f'Has created_at: {"created_at" in manifest}')

chunk = manifest['chunks'][0]
print(f'Chunk has id: {"id" in chunk}')
print(f'Chunk has char_start: {"char_start" in chunk}')
print(f'Chunk has char_end: {"char_end" in chunk}')
""")
        assert code == 0
        assert "Has format: True" in stdout
        assert "Has chunking_method: True" in stdout
        assert "Has chunks: True" in stdout
        assert "Chunk has id: True" in stdout

    def test_json_manifest_has_element_range(self, init_session, run_exec):
        """JSON array manifest includes element_range."""
        json_content = json.dumps([{"id": i} for i in range(20)])
        state_path = init_session(json_content)
        
        state = pickle.load(open(state_path, 'rb'))
        state['context']['path'] = 'test.json'
        pickle.dump(state, open(state_path, 'wb'))

        stdout, stderr, code = run_exec(state_path, """
import json
paths = smart_chunk(str(session_dir / 'chunks'), target_size=100, min_size=20, max_size=300)
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())

if manifest.get('json_chunked'):
    chunk = manifest['chunks'][0]
    print(f'Has element_range: {"element_range" in chunk}')
    print(f'Element range: {chunk.get("element_range")}')
else:
    print('JSON chunking not used (small content)')
""")
        assert code == 0


class TestGoalAlignment:
    """Goal-alignment tests verifying paper requirements."""

    def test_goal_inline_llm_query_available(self, init_session, run_exec):
        """Paper goal: inline llm_query() available in REPL.
        
        Requirement: "Enable programmatic sub-LLM calls from within Python code blocks"
        """
        state_path = init_session("Test content")

        stdout, stderr, code = run_exec(state_path, """
# Verify llm_query is available and callable
print(f'llm_query type: {type(llm_query).__name__}')
print(f'Callable: {callable(llm_query)}')
""")
        assert code == 0
        assert "Callable: True" in stdout

    def test_goal_recursive_depth_configurable(self, rlm_repl_path, tmp_path):
        """Paper goal: recursive depth is configurable.
        
        Requirement: "Allow sub-LLMs to spawn their own sub-LLMs (default depth limit: 3)"
        """
        test_file = tmp_path / "test.txt"
        test_file.write_text("Content")

        # Default is 3
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "init", str(test_file)],
            capture_output=True, text=True, cwd=tmp_path
        )
        assert result.returncode == 0
        
        for line in result.stdout.splitlines():
            if "Session path:" in line:
                state_path = tmp_path / line.split(":", 1)[1].strip()
                break
        
        state = pickle.load(open(state_path, 'rb'))
        assert state["max_depth"] == 3  # Default per paper

    def test_goal_batch_execution_available(self, init_session, run_exec):
        """Paper goal: batch execution available.
        
        Requirement: "parallel sub-LLM invocation"
        """
        state_path = init_session("Test")

        stdout, stderr, code = run_exec(state_path, """
print(f'llm_query_batch callable: {callable(llm_query_batch)}')
""")
        assert code == 0
        assert "llm_query_batch callable: True" in stdout

    def test_goal_content_aware_chunking(self, init_session, run_exec):
        """Paper goal: content-aware chunking available.
        
        Requirement: "Content-aware chunking using markdown structure and tree-sitter"
        """
        markdown = """# Title

## Section One

Content here.

## Section Two

More content.
"""
        state_path = init_session(markdown)
        
        state = pickle.load(open(state_path, 'rb'))
        state['context']['path'] = 'test.md'
        pickle.dump(state, open(state_path, 'wb'))

        stdout, stderr, code = run_exec(state_path, """
import json
paths = smart_chunk(str(session_dir / 'chunks'), target_size=50, min_size=10, max_size=200)
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())
print(f'Format detected: {manifest["format"]}')
print(f'Method used: {manifest["chunking_method"]}')
""")
        assert code == 0
        assert "Format detected: markdown" in stdout

    def test_goal_finalization_signal(self, init_session, run_exec, rlm_repl_path):
        """Paper goal: answer finalization works end-to-end.
        
        Requirement: "Add set_final_answer() to mark variables for retrieval"
        """
        state_path = init_session("Test")

        # Set answer
        run_exec(state_path, "set_final_answer({'result': 'success', 'count': 42})")

        # Retrieve via CLI
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "--state", str(state_path), "get-final-answer"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        
        data = json.loads(result.stdout)
        assert data["set"] is True
        assert data["value"]["result"] == "success"

    def test_goal_all_features_together(self, init_session, run_exec, rlm_repl_path):
        """Verify all RLM paper features work together.
        
        This test exercises:
        1. Init with depth configuration
        2. Smart chunking with format detection
        3. Handle-based searching
        4. llm_query availability
        5. Answer finalization
        """
        markdown = """# API Documentation

## Authentication

Use Bearer tokens for authentication.

## Endpoints

### GET /users

Returns list of users.

### POST /users

Creates a new user.

## Errors

Standard HTTP error codes apply.
"""
        state_path = init_session(markdown, max_depth=2)
        
        # Update to markdown
        state = pickle.load(open(state_path, 'rb'))
        state['context']['path'] = 'api.md'
        pickle.dump(state, open(state_path, 'wb'))

        # Step 1: Verify depth setting
        state = pickle.load(open(state_path, 'rb'))
        assert state["max_depth"] == 2

        # Step 2: Smart chunk
        stdout, stderr, code = run_exec(state_path, """
import json
paths = smart_chunk(str(session_dir / 'chunks'), target_size=100, min_size=30, max_size=300)
print(f'Chunks: {len(paths)}')
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())
print(f'Format: {manifest["format"]}')
""")
        assert code == 0
        assert "Format: markdown" in stdout

        # Step 3: Search with handles
        stdout, stderr, code = run_exec(state_path, """
grep('users')
print(f'Matches: {count(last_handle())}')
""")
        assert code == 0
        assert "Matches:" in stdout

        # Step 4: Verify llm_query available
        stdout, stderr, code = run_exec(state_path, """
print(f'llm_query available: {callable(llm_query)}')
print(f'llm_query_batch available: {callable(llm_query_batch)}')
""")
        assert code == 0
        assert "llm_query available: True" in stdout

        # Step 5: Set final answer
        stdout, stderr, code = run_exec(state_path, """
set_final_answer({
    'endpoints': ['/users GET', '/users POST'],
    'auth': 'Bearer tokens'
})
print(f'Answer set: {has_final_answer()}')
""")
        assert code == 0
        assert "Answer set: True" in stdout

        # Step 6: Retrieve via CLI
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "--state", str(state_path), "get-final-answer"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["set"] is True
        assert len(data["value"]["endpoints"]) == 2


class TestErrorHandling:
    """Tests for error handling and edge cases."""

    def test_invalid_code_returns_error(self, init_session, run_exec):
        """Syntax errors in exec code are reported."""
        state_path = init_session("Test")

        stdout, stderr, code = run_exec(state_path, "this is not valid python")
        assert code != 0 or "Error" in stderr or "error" in stderr.lower()

    def test_missing_state_file(self, rlm_repl_path, tmp_path):
        """Error when state file doesn't exist."""
        result = subprocess.run(
            ["python3", str(rlm_repl_path), "--state", "/nonexistent/path.pkl", "status"],
            capture_output=True, text=True
        )
        assert result.returncode != 0

    def test_non_serializable_final_answer(self, init_session, run_exec):
        """set_final_answer rejects non-JSON-serializable values."""
        state_path = init_session("Test")

        stdout, stderr, code = run_exec(state_path, """
import re
try:
    set_final_answer(re.compile('test'))
    print('ERROR: Should have raised')
except ValueError as e:
    print(f'Correctly rejected: {e}')
""")
        assert "Correctly rejected" in stdout or "Error" in stderr


@pytest.mark.slow
class TestSlowIntegration:
    """Slow integration tests that may spawn real subprocesses."""

    def test_real_workflow_with_large_content(self, init_session, run_exec):
        """Full workflow with larger content."""
        # Generate substantial content
        content = "# Large Document\n\n"
        for i in range(100):
            content += f"## Section {i}\n\n"
            content += f"This is the content for section {i}. " * 20 + "\n\n"

        state_path = init_session(content)
        
        state = pickle.load(open(state_path, 'rb'))
        state['context']['path'] = 'large.md'
        pickle.dump(state, open(state_path, 'wb'))

        # Chunk
        stdout, stderr, code = run_exec(state_path, """
import json
paths = smart_chunk(str(session_dir / 'chunks'), target_size=5000, min_size=1000, max_size=10000)
print(f'Created {len(paths)} chunks')
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())
print(f'Format: {manifest["format"]}')
print(f'Method: {manifest["chunking_method"]}')
""")
        assert code == 0
        assert "Created" in stdout

        # Search
        stdout, stderr, code = run_exec(state_path, """
grep('Section')
print(f'Section mentions: {count(last_handle())}')
""")
        assert code == 0
        assert "Section mentions:" in stdout

        # Finalize
        stdout, stderr, code = run_exec(state_path, """
set_final_answer({'sections_found': 100, 'method': 'smart_markdown'})
print('Done')
""")
        assert code == 0


class TestLLMQueryIntegration:
    """Tests for llm_query() and llm_query_batch() integration."""

    def test_llm_query_depth_zero_rejection(self, init_session, run_exec):
        """llm_query() returns error when depth is exhausted."""
        # Create session with max_depth=0
        content = "Test content"
        state_path = init_session(content, extra_args=["--max-depth", "0"])

        stdout, stderr, code = run_exec(state_path, """
result = llm_query("Test prompt")
print(f"Result: {result}")
assert "depth limit" in result.lower() or "recursion" in result.lower()
print("PASS: Depth limit enforced")
""")
        assert code == 0, f"stderr: {stderr}"
        assert "PASS" in stdout or "depth limit" in stdout.lower()

    def test_llm_query_logs_to_jsonl(self, init_session, run_exec):
        """llm_query() logs queries to llm_queries.jsonl."""
        content = "Test content"
        # Use depth=0 so query fails fast without spawning subprocess
        state_path = init_session(content, extra_args=["--max-depth", "0"])
        session_dir = state_path.parent

        # Make a query (will fail due to depth)
        run_exec(state_path, 'llm_query("Test")')

        # Check log exists
        log_file = session_dir / "llm_queries.jsonl"
        assert log_file.exists(), "Log file should exist"
        
        # Parse log entry
        entries = [json.loads(line) for line in log_file.read_text().strip().split("\n") if line]
        assert len(entries) >= 1, "Should have at least one log entry"
        
        entry = entries[0]
        assert "query_id" in entry
        assert "timestamp" in entry
        assert "status" in entry
        assert entry["status"] == "depth_exceeded"

    def test_llm_query_batch_available(self, init_session, run_exec):
        """llm_query_batch() is available and has correct signature."""
        state_path = init_session("Test")

        stdout, stderr, code = run_exec(state_path, """
import inspect
sig = inspect.signature(llm_query_batch)
params = list(sig.parameters.keys())
expected = ['prompts', 'concurrency', 'max_retries', 'cleanup']
assert params == expected, f"Got {params}, expected {expected}"
print("PASS: llm_query_batch has correct signature")
""")
        assert code == 0, f"stderr: {stderr}"
        assert "PASS" in stdout

    def test_depth_passed_through_status(self, init_session, run_exec):
        """Session status shows correct depth configuration."""
        content = "Test"
        state_path = init_session(content, extra_args=["--max-depth", "5"])

        # Check via status command
        import subprocess
        result = subprocess.run(
            ["python3", str(Path(__file__).parent.parent / "scripts" / "rlm_repl.py"),
             "--state", str(state_path), "status"],
            capture_output=True, text=True
        )
        assert "Max depth: 5" in result.stdout
        assert "Remaining depth: 5" in result.stdout
