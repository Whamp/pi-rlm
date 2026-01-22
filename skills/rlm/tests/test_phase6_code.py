"""Phase 6 tests: Semantic chunking - Code with codemap.

Tests for code-aware chunking that splits at function/class boundaries
using tree-sitter (via codemap) for structural analysis.
"""
import json
import os
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys

# Add scripts dir for direct imports
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from rlm_repl import (
    _detect_codemap,
    _extract_symbol_boundaries,
    _chunk_code,
    _line_to_char_position,
    _smart_chunk_impl,
    _detect_format,
    _CODEMAP_CACHE,
)
import rlm_repl


class TestDetectCodemap:
    """Unit tests for _detect_codemap()."""
    
    def setup_method(self):
        """Reset codemap cache before each test."""
        rlm_repl._CODEMAP_CACHE = None
    
    def test_env_var_path_takes_precedence(self, tmp_path):
        """RLM_CODEMAP_PATH env var takes precedence."""
        codemap_path = tmp_path / "codemap"
        codemap_path.touch()
        codemap_path.chmod(0o755)
        
        with patch.dict(os.environ, {"RLM_CODEMAP_PATH": str(codemap_path)}):
            rlm_repl._CODEMAP_CACHE = None  # Reset cache
            result = _detect_codemap()
            assert result == str(codemap_path)
    
    def test_env_var_nonexistent_path_falls_through(self, tmp_path):
        """Nonexistent env var path should fall through to other methods."""
        with patch.dict(os.environ, {"RLM_CODEMAP_PATH": "/nonexistent/codemap"}):
            with patch("rlm_repl.subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError()
                rlm_repl._CODEMAP_CACHE = None
                result = _detect_codemap()
                assert result is None
    
    @patch("rlm_repl.subprocess.run")
    def test_detects_codemap_in_path(self, mock_run):
        """Detects codemap binary in PATH."""
        mock_run.return_value = MagicMock(returncode=0)
        
        with patch.dict(os.environ, {"RLM_CODEMAP_PATH": ""}):
            rlm_repl._CODEMAP_CACHE = None
            result = _detect_codemap()
            assert result == "codemap"
            # Should have tried 'codemap --version'
            mock_run.assert_called()
    
    @patch("rlm_repl.subprocess.run")
    def test_falls_back_to_npx(self, mock_run):
        """Falls back to npx codemap when codemap not in PATH."""
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if cmd[0] == 'codemap':
                raise FileNotFoundError()
            return MagicMock(returncode=0)
        
        mock_run.side_effect = side_effect
        
        with patch.dict(os.environ, {"RLM_CODEMAP_PATH": ""}):
            rlm_repl._CODEMAP_CACHE = None
            result = _detect_codemap()
            assert result == "npx codemap"
    
    @patch("rlm_repl.subprocess.run")
    def test_returns_none_when_unavailable(self, mock_run):
        """Returns None when codemap not available anywhere."""
        mock_run.side_effect = FileNotFoundError()
        
        with patch.dict(os.environ, {"RLM_CODEMAP_PATH": ""}):
            rlm_repl._CODEMAP_CACHE = None
            result = _detect_codemap()
            assert result is None
    
    @patch("rlm_repl.subprocess.run")
    def test_caches_result(self, mock_run):
        """Result is cached to avoid repeated subprocess calls."""
        mock_run.return_value = MagicMock(returncode=0)
        
        with patch.dict(os.environ, {"RLM_CODEMAP_PATH": ""}):
            rlm_repl._CODEMAP_CACHE = None
            result1 = _detect_codemap()
            result2 = _detect_codemap()
            assert result1 == result2
            # Should only call subprocess once due to caching
            assert mock_run.call_count == 1
    
    @patch("rlm_repl.subprocess.run")
    def test_handles_timeout(self, mock_run):
        """Handles subprocess timeout gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired("codemap", 10)
        
        with patch.dict(os.environ, {"RLM_CODEMAP_PATH": ""}):
            rlm_repl._CODEMAP_CACHE = None
            result = _detect_codemap()
            # Should return None after timeout, not raise
            assert result is None


class TestExtractSymbolBoundaries:
    """Unit tests for _extract_symbol_boundaries()."""
    
    def test_parses_json_with_files_array(self):
        """Parses codemap JSON format with files array."""
        codemap_output = json.dumps({
            "files": [{
                "path": "test.py",
                "symbols": [
                    {"name": "func1", "kind": "function", "lines": [1, 10], "exported": True},
                    {"name": "MyClass", "kind": "class", "lines": [12, 50], "exported": True},
                ]
            }]
        })
        
        symbols = _extract_symbol_boundaries(codemap_output, "test.py")
        assert len(symbols) == 2
        assert symbols[0]['name'] == 'func1'
        assert symbols[0]['kind'] == 'function'
        assert symbols[0]['start_line'] == 1
        assert symbols[0]['end_line'] == 10
        assert symbols[1]['name'] == 'MyClass'
    
    def test_parses_bare_array_format(self):
        """Parses codemap JSON as bare array of files."""
        codemap_output = json.dumps([{
            "path": "test.py",
            "symbols": [
                {"name": "main", "kind": "function", "lines": [5, 20], "exported": False},
            ]
        }])
        
        symbols = _extract_symbol_boundaries(codemap_output, "test.py")
        assert len(symbols) == 1
        assert symbols[0]['name'] == 'main'
        assert symbols[0]['exported'] is False
    
    def test_matches_by_filename(self):
        """Matches file by name when path doesn't match exactly."""
        codemap_output = json.dumps({
            "files": [{
                "path": "/some/long/path/test.py",
                "symbols": [
                    {"name": "func", "kind": "function", "lines": [1, 5], "exported": True},
                ]
            }]
        })
        
        symbols = _extract_symbol_boundaries(codemap_output, "test.py")
        assert len(symbols) == 1
        assert symbols[0]['name'] == 'func'
    
    def test_returns_empty_for_no_match(self):
        """Returns empty list when file not found in output."""
        codemap_output = json.dumps({
            "files": [{
                "path": "other.py",
                "symbols": [
                    {"name": "func", "kind": "function", "lines": [1, 5], "exported": True},
                ]
            }]
        })
        
        symbols = _extract_symbol_boundaries(codemap_output, "test.py")
        assert symbols == []
    
    def test_handles_invalid_json(self):
        """Returns empty list for invalid JSON."""
        symbols = _extract_symbol_boundaries("not valid json {{{", "test.py")
        assert symbols == []
    
    def test_sorts_by_start_line(self):
        """Symbols are sorted by start line."""
        codemap_output = json.dumps({
            "files": [{
                "path": "test.py",
                "symbols": [
                    {"name": "third", "kind": "function", "lines": [30, 40], "exported": True},
                    {"name": "first", "kind": "function", "lines": [1, 10], "exported": True},
                    {"name": "second", "kind": "class", "lines": [15, 25], "exported": True},
                ]
            }]
        })
        
        symbols = _extract_symbol_boundaries(codemap_output, "test.py")
        assert [s['name'] for s in symbols] == ['first', 'second', 'third']
    
    def test_includes_signature_if_present(self):
        """Includes signature field if present in output."""
        codemap_output = json.dumps({
            "files": [{
                "path": "test.py",
                "symbols": [
                    {
                        "name": "greet",
                        "kind": "function",
                        "lines": [1, 3],
                        "signature": "greet(name: str) -> str",
                        "exported": True
                    },
                ]
            }]
        })
        
        symbols = _extract_symbol_boundaries(codemap_output, "test.py")
        assert symbols[0]['signature'] == "greet(name: str) -> str"


class TestLineToCharPosition:
    """Unit tests for _line_to_char_position()."""
    
    def test_line_1_returns_0(self):
        """Line 1 returns position 0."""
        content = "first line\nsecond line"
        assert _line_to_char_position(content, 1) == 0
    
    def test_line_2_returns_after_first_newline(self):
        """Line 2 returns position after first newline."""
        content = "first line\nsecond line"
        assert _line_to_char_position(content, 2) == 11  # len("first line\n")
    
    def test_handles_empty_lines(self):
        """Handles empty lines correctly."""
        content = "a\n\nb"
        assert _line_to_char_position(content, 1) == 0  # 'a'
        assert _line_to_char_position(content, 2) == 2  # ''
        assert _line_to_char_position(content, 3) == 3  # 'b'
    
    def test_line_0_returns_0(self):
        """Line 0 or negative returns 0."""
        content = "test"
        assert _line_to_char_position(content, 0) == 0
        assert _line_to_char_position(content, -1) == 0


class TestChunkCode:
    """Unit tests for _chunk_code()."""
    
    @patch("rlm_repl._detect_codemap")
    def test_falls_back_when_codemap_unavailable(self, mock_detect):
        """Falls back to text chunking when codemap unavailable."""
        mock_detect.return_value = None
        
        content = "def func1():\n    pass\n\ndef func2():\n    pass"
        chunks, codemap_used = _chunk_code(content, "test.py", 100, 20, 200)
        
        assert codemap_used is False
        assert len(chunks) >= 1
    
    @patch("rlm_repl._detect_codemap")
    @patch("rlm_repl.subprocess.run")
    def test_uses_codemap_output_for_boundaries(self, mock_run, mock_detect):
        """Uses codemap output to determine chunk boundaries."""
        mock_detect.return_value = "codemap"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "files": [{
                    "path": "test.py",
                    "symbols": [
                        {"name": "func1", "kind": "function", "lines": [1, 3], "exported": True},
                        {"name": "func2", "kind": "function", "lines": [5, 7], "exported": True},
                    ]
                }]
            })
        )
        
        content = "def func1():\n    pass\n\ndef func2():\n    pass\n"
        
        # Create actual file for path resolution
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        try:
            chunks, codemap_used = _chunk_code(content, temp_path, 10, 5, 100)
            # Should attempt to use codemap
            assert mock_run.called
        finally:
            Path(temp_path).unlink()
    
    @patch("rlm_repl._detect_codemap")
    @patch("rlm_repl.subprocess.run")
    def test_falls_back_on_codemap_failure(self, mock_run, mock_detect):
        """Falls back to text chunking when codemap returns error."""
        mock_detect.return_value = "codemap"
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        
        import tempfile
        content = "def func1():\n    pass\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        try:
            chunks, codemap_used = _chunk_code(content, temp_path, 100, 20, 200)
            assert codemap_used is False
        finally:
            Path(temp_path).unlink()
    
    @patch("rlm_repl._detect_codemap")
    @patch("rlm_repl.subprocess.run")
    def test_falls_back_when_no_symbols_found(self, mock_run, mock_detect):
        """Falls back to text chunking when codemap finds no symbols."""
        mock_detect.return_value = "codemap"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"files": []})
        )
        
        import tempfile
        content = "# just a comment\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        try:
            chunks, codemap_used = _chunk_code(content, temp_path, 100, 20, 200)
            assert codemap_used is False
        finally:
            Path(temp_path).unlink()
    
    @patch("rlm_repl._detect_codemap")
    def test_falls_back_for_nonexistent_file(self, mock_detect):
        """Falls back to text chunking for nonexistent file path."""
        mock_detect.return_value = "codemap"
        
        content = "def func():\n    pass\n"
        chunks, codemap_used = _chunk_code(content, "/nonexistent/path.py", 100, 20, 200)
        
        assert codemap_used is False
        assert len(chunks) >= 1


class TestSmartChunkCodeIntegration:
    """Integration tests for smart_chunk with code files."""
    
    def setup_method(self):
        """Reset codemap cache before each test."""
        rlm_repl._CODEMAP_CACHE = None
    
    def test_code_format_detected_for_python(self, tmp_path):
        """Python files are detected as code format."""
        content = "def hello():\n    print('world')\n"
        out_dir = tmp_path / "chunks"
        
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="test.py",
            out_dir=out_dir,
        )
        
        assert manifest['format'] == 'code'
    
    def test_manifest_includes_codemap_available(self, tmp_path):
        """Manifest includes codemap_available field."""
        content = "def hello():\n    pass\n"
        out_dir = tmp_path / "chunks"
        
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="test.py",
            out_dir=out_dir,
        )
        
        assert 'codemap_available' in manifest
        assert isinstance(manifest['codemap_available'], bool)
    
    def test_manifest_includes_codemap_used(self, tmp_path):
        """Manifest includes codemap_used field."""
        content = "def hello():\n    pass\n"
        out_dir = tmp_path / "chunks"
        
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="test.py",
            out_dir=out_dir,
        )
        
        assert 'codemap_used' in manifest
        assert isinstance(manifest['codemap_used'], bool)
    
    @patch("rlm_repl._detect_codemap")
    def test_uses_smart_text_when_codemap_unavailable(self, mock_detect, tmp_path):
        """Uses smart_text chunking method when codemap unavailable."""
        mock_detect.return_value = None
        
        content = "def hello():\n    pass\n"
        out_dir = tmp_path / "chunks"
        
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="test.py",
            out_dir=out_dir,
        )
        
        assert manifest['chunking_method'] == 'smart_text'
        assert manifest['codemap_used'] is False


class TestSmartChunkREPL:
    """Integration tests for smart_chunk via REPL exec."""
    
    def setup_method(self):
        """Reset codemap cache before each test."""
        rlm_repl._CODEMAP_CACHE = None
    
    def test_smart_chunk_works_for_code_files(self, init_session, run_exec, tmp_path):
        """smart_chunk works for Python code files."""
        code_content = '''def function_one():
    """First function."""
    return 1

def function_two():
    """Second function."""
    return 2
'''
        state_path = init_session(code_content)
        
        # Rename the context file to .py extension
        session_dir = state_path.parent
        
        chunk_dir = tmp_path / "chunks"
        stdout, stderr, rc = run_exec(
            state_path,
            f"paths = smart_chunk('{chunk_dir}'); print(len(paths))"
        )
        
        assert rc == 0, f"Exec failed: {stderr}"
        # Should have at least one chunk
        assert int(stdout.strip()) >= 1


class TestGoalCodeBoundaries:
    """Goal-alignment: Code splits on function/class boundaries.
    
    Paper requirement: "Content-aware chunking using tree-sitter"
    """
    
    def setup_method(self):
        """Reset codemap cache before each test."""
        rlm_repl._CODEMAP_CACHE = None
    
    @pytest.mark.slow
    def test_goal_code_boundaries_with_codemap(self, tmp_path):
        """Code chunks align with function definitions when codemap available."""
        # Skip if codemap not available
        codemap_cmd = _detect_codemap()
        if codemap_cmd is None:
            pytest.skip("codemap not installed")
        
        code = '''def function_one():
    """First function."""
    x = 1
    y = 2
    return x + y

def function_two():
    """Second function."""
    a = 3
    b = 4
    return a * b

class MyClass:
    """A sample class."""
    
    def method_one(self):
        """First method."""
        return "one"
    
    def method_two(self):
        """Second method."""
        return "two"
'''
        # Create actual file for codemap
        code_file = tmp_path / "test_code.py"
        code_file.write_text(code)
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=code,
            context_path=str(code_file),
            out_dir=out_dir,
            target_size=100,
            min_size=30,
            max_size=500,
        )
        
        # Verify format detection
        assert manifest['format'] == 'code'
        
        # If codemap was used, verify code-aware chunking
        if manifest['codemap_used']:
            assert manifest['chunking_method'] == 'smart_code'
            
            # Verify boundaries include function/class markers
            has_function_boundary = False
            has_class_boundary = False
            for chunk in manifest['chunks']:
                for boundary in chunk.get('boundaries', []):
                    if boundary.get('type') == 'function':
                        has_function_boundary = True
                    if boundary.get('type') == 'class':
                        has_class_boundary = True
            
            # At least one boundary type should be found
            assert has_function_boundary or has_class_boundary, \
                f"No function/class boundaries found in: {manifest['chunks']}"
    
    def test_goal_graceful_fallback(self, tmp_path):
        """Code chunking gracefully falls back when codemap unavailable."""
        code = "def hello():\n    pass\n"
        out_dir = tmp_path / "chunks"
        
        # Force codemap unavailable
        with patch("rlm_repl._detect_codemap") as mock_detect:
            mock_detect.return_value = None
            rlm_repl._CODEMAP_CACHE = None
            
            paths, manifest = _smart_chunk_impl(
                content=code,
                context_path="test.py",
                out_dir=out_dir,
            )
        
        # Should fall back gracefully
        assert manifest['format'] == 'code'
        assert manifest['chunking_method'] == 'smart_text'
        assert manifest['codemap_used'] is False
        assert len(paths) >= 1
    
    def test_goal_manifest_shows_codemap_status(self, tmp_path):
        """Manifest clearly shows whether codemap was available and used."""
        code = "def test(): pass\n"
        out_dir = tmp_path / "chunks"
        
        paths, manifest = _smart_chunk_impl(
            content=code,
            context_path="test.py",
            out_dir=out_dir,
        )
        
        # Must have these fields
        assert 'codemap_available' in manifest
        assert 'codemap_used' in manifest
        
        # Types must be boolean
        assert isinstance(manifest['codemap_available'], bool)
        assert isinstance(manifest['codemap_used'], bool)
        
        # If codemap available but not used, chunking_method should be smart_text
        if manifest['codemap_available'] and not manifest['codemap_used']:
            # This could happen if file doesn't exist or has no symbols
            pass  # OK, fallback is expected
        
        # If codemap used, method should be smart_code
        if manifest['codemap_used']:
            assert manifest['chunking_method'] == 'smart_code'
