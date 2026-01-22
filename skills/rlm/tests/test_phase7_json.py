"""Phase 7 tests: Semantic chunking - JSON.

Tests for JSON-aware chunking that splits at structural boundaries:
- Arrays: split into element groups
- Objects: split by top-level keys
- Each chunk is valid, parseable JSON
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
    _chunk_json,
    _smart_chunk_impl,
    _detect_format,
)

# Helper to call _chunk_json for arrays
def _chunk_json_array(content, target_size, min_size, max_size):
    """Wrapper for backward compatibility with tests."""
    return _chunk_json(content, target_size, min_size, max_size)

# Helper to call _chunk_json for objects
def _chunk_json_object(content, target_size, min_size, max_size):
    """Wrapper for backward compatibility with tests."""
    return _chunk_json(content, target_size, min_size, max_size)


class TestChunkJsonArray:
    """Unit tests for _chunk_json_array()."""
    
    def test_empty_array_single_chunk(self):
        """Empty array returns single chunk."""
        content = "[]"
        chunks, success = _chunk_json_array(content, 100, 10, 200)
        assert success is True
        assert len(chunks) == 1
        assert chunks[0]['element_range'] == [0, 0]
        assert chunks[0]['split_reason'] == 'single_chunk'
    
    def test_small_array_single_chunk(self):
        """Small array that fits returns single chunk."""
        data = [{"id": i} for i in range(5)]
        content = json.dumps(data)
        chunks, success = _chunk_json_array(content, 1000, 100, 2000)
        assert success is True
        assert len(chunks) == 1
        assert chunks[0]['element_range'] == [0, 5]
    
    def test_large_array_splits(self):
        """Large array is split into multiple chunks."""
        data = [{"id": i, "value": f"item_{i}" * 10} for i in range(100)]
        content = json.dumps(data)
        # Small target size to force multiple chunks
        chunks, success = _chunk_json_array(content, target_size=500, min_size=100, max_size=1000)
        assert success is True
        assert len(chunks) > 1
        
        # Verify all elements are covered
        total_elements = sum(c['element_range'][1] - c['element_range'][0] for c in chunks)
        assert total_elements == 100
    
    def test_each_chunk_is_valid_json(self):
        """Each chunk content is valid JSON array."""
        data = [{"id": i} for i in range(50)]
        content = json.dumps(data)
        chunks, success = _chunk_json_array(content, target_size=200, min_size=50, max_size=500)
        assert success is True
        
        for chunk in chunks:
            chunk_content = chunk['json_content']
            parsed = json.loads(chunk_content)
            assert isinstance(parsed, list)
            assert len(parsed) > 0 or chunk['element_range'] == [0, 0]
    
    def test_respects_max_size(self):
        """Chunks don't exceed max_size."""
        data = [{"id": i, "data": "x" * 100} for i in range(100)]
        content = json.dumps(data)
        chunks, success = _chunk_json_array(content, target_size=300, min_size=100, max_size=500)
        assert success is True
        
        for chunk in chunks:
            assert len(chunk['json_content']) <= 500
    
    def test_element_range_continuity(self):
        """Element ranges are continuous and cover all elements."""
        data = [{"id": i} for i in range(30)]
        content = json.dumps(data)
        chunks, success = _chunk_json_array(content, target_size=100, min_size=20, max_size=200)
        assert success is True
        
        # Check continuity
        expected_start = 0
        for chunk in chunks:
            assert chunk['element_range'][0] == expected_start
            expected_start = chunk['element_range'][1]
        assert expected_start == 30
    
    def test_invalid_json_returns_failure(self):
        """Invalid JSON returns empty list and failure."""
        content = "not valid json"
        chunks, success = _chunk_json_array(content, 100, 10, 200)
        assert success is False
        assert chunks == []
    
    def test_non_array_returns_success_as_object(self):
        """Non-array JSON (object) is handled by unified _chunk_json."""
        content = '{"key": "value"}'
        chunks, success = _chunk_json_array(content, 100, 10, 200)
        # Unified _chunk_json handles both arrays and objects
        assert success is True
        assert len(chunks) == 1
    
    def test_split_reasons(self):
        """Chunks have appropriate split_reason values."""
        data = [{"id": i} for i in range(20)]
        content = json.dumps(data)
        chunks, success = _chunk_json_array(content, target_size=50, min_size=10, max_size=100)
        assert success is True
        assert len(chunks) >= 2
        
        assert chunks[0]['split_reason'] == 'start'
        assert chunks[-1]['split_reason'] in ('end', 'element_boundary')
    
    def test_minified_json_handled(self):
        """Minified JSON is handled correctly."""
        data = [{"id": i} for i in range(10)]
        content = json.dumps(data, separators=(',', ':'))  # Minified
        chunks, success = _chunk_json_array(content, target_size=50, min_size=10, max_size=100)
        assert success is True
        
        for chunk in chunks:
            parsed = json.loads(chunk['json_content'])
            assert isinstance(parsed, list)


class TestChunkJsonObject:
    """Unit tests for _chunk_json_object()."""
    
    def test_empty_object_single_chunk(self):
        """Empty object returns single chunk."""
        content = "{}"
        chunks, success = _chunk_json_object(content, 100, 10, 200)
        assert success is True
        assert len(chunks) == 1
        assert chunks[0]['key_range'] == [0, 0]
        assert chunks[0]['keys'] == []
        assert chunks[0]['split_reason'] == 'single_chunk'
    
    def test_small_object_single_chunk(self):
        """Small object that fits returns single chunk."""
        data = {f"key_{i}": {"value": i} for i in range(3)}
        content = json.dumps(data)
        chunks, success = _chunk_json_object(content, 1000, 100, 2000)
        assert success is True
        assert len(chunks) == 1
        assert len(chunks[0]['keys']) == 3
    
    def test_large_object_splits(self):
        """Large object is split into multiple chunks."""
        data = {f"key_{i}": {"value": f"data_{i}" * 20} for i in range(50)}
        content = json.dumps(data)
        chunks, success = _chunk_json_object(content, target_size=500, min_size=100, max_size=1000)
        assert success is True
        assert len(chunks) > 1
        
        # Verify all keys are covered
        all_keys = []
        for chunk in chunks:
            all_keys.extend(chunk['keys'])
        assert len(all_keys) == 50
    
    def test_each_chunk_is_valid_json(self):
        """Each chunk content is valid JSON object."""
        data = {f"key_{i}": {"nested": i} for i in range(20)}
        content = json.dumps(data)
        chunks, success = _chunk_json_object(content, target_size=200, min_size=50, max_size=500)
        assert success is True
        
        for chunk in chunks:
            chunk_content = chunk['json_content']
            parsed = json.loads(chunk_content)
            assert isinstance(parsed, dict)
    
    def test_respects_max_size(self):
        """Chunks don't exceed max_size."""
        data = {f"key_{i}": {"data": "x" * 100} for i in range(50)}
        content = json.dumps(data)
        chunks, success = _chunk_json_object(content, target_size=300, min_size=100, max_size=600)
        assert success is True
        
        for chunk in chunks:
            assert len(chunk['json_content']) <= 600
    
    def test_key_range_continuity(self):
        """Key ranges are continuous and cover all keys."""
        data = {f"key_{i}": i for i in range(15)}
        content = json.dumps(data)
        chunks, success = _chunk_json_object(content, target_size=50, min_size=10, max_size=100)
        assert success is True
        
        expected_start = 0
        for chunk in chunks:
            assert chunk['key_range'][0] == expected_start
            expected_start = chunk['key_range'][1]
        assert expected_start == 15
    
    def test_keys_match_key_range(self):
        """Keys list matches key_range indices."""
        data = {f"key_{i}": i for i in range(10)}
        content = json.dumps(data)
        chunks, success = _chunk_json_object(content, target_size=30, min_size=10, max_size=80)
        assert success is True
        
        original_keys = list(data.keys())
        for chunk in chunks:
            start_idx, end_idx = chunk['key_range']
            expected_keys = original_keys[start_idx:end_idx]
            assert chunk['keys'] == expected_keys
    
    def test_invalid_json_returns_failure(self):
        """Invalid JSON returns empty list and failure."""
        content = "not valid json"
        chunks, success = _chunk_json_object(content, 100, 10, 200)
        assert success is False
        assert chunks == []
    
    def test_non_object_returns_success_as_array(self):
        """Non-object JSON (array) is handled by unified _chunk_json."""
        content = '[1, 2, 3]'
        chunks, success = _chunk_json_object(content, 100, 10, 200)
        # Unified _chunk_json handles both arrays and objects
        assert success is True
        assert len(chunks) == 1
    
    def test_split_reasons(self):
        """Chunks have appropriate split_reason values."""
        data = {f"k{i}": i for i in range(10)}
        content = json.dumps(data)
        chunks, success = _chunk_json_object(content, target_size=30, min_size=10, max_size=60)
        assert success is True
        
        if len(chunks) >= 2:
            assert chunks[0]['split_reason'] == 'start'
            for chunk in chunks[1:-1]:
                assert chunk['split_reason'] == 'key_boundary'


class TestChunkJson:
    """Unit tests for _chunk_json() dispatcher."""
    
    def test_detects_array(self):
        """Correctly dispatches array to _chunk_json_array."""
        content = '[1, 2, 3]'
        chunks, success = _chunk_json(content, 100, 10, 200)
        assert success is True
        assert 'element_range' in chunks[0]
    
    def test_detects_object(self):
        """Correctly dispatches object to _chunk_json_object."""
        content = '{"a": 1}'
        chunks, success = _chunk_json(content, 100, 10, 200)
        assert success is True
        assert 'keys' in chunks[0]
    
    def test_handles_whitespace(self):
        """Handles leading/trailing whitespace."""
        content = '  \n  [1, 2, 3]  \n  '
        chunks, success = _chunk_json(content, 100, 10, 200)
        assert success is True
    
    def test_invalid_json(self):
        """Returns failure for invalid JSON."""
        content = 'not json'
        chunks, success = _chunk_json(content, 100, 10, 200)
        assert success is False
    
    def test_empty_content(self):
        """Returns failure for empty content."""
        content = '   '
        chunks, success = _chunk_json(content, 100, 10, 200)
        assert success is False
    
    def test_non_container_json(self):
        """Returns failure for non-container JSON (string, number)."""
        chunks1, success1 = _chunk_json('"just a string"', 100, 10, 200)
        assert success1 is False
        
        chunks2, success2 = _chunk_json('42', 100, 10, 200)
        assert success2 is False


class TestSmartChunkJsonIntegration:
    """Integration tests for smart_chunk with JSON files."""
    
    def test_json_file_uses_json_chunking(self, tmp_path):
        """JSON file detected and uses JSON chunking method."""
        data = [{"id": i} for i in range(50)]
        content = json.dumps(data, indent=2)
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="data.json",
            out_dir=out_dir,
            target_size=200,
            min_size=50,
            max_size=500,
        )
        
        assert manifest['format'] == 'json'
        assert manifest['chunking_method'] == 'smart_json'
        assert manifest['json_chunked'] is True
    
    def test_json_chunks_have_json_extension(self, tmp_path):
        """JSON chunks use .json file extension."""
        data = [{"id": i} for i in range(20)]
        content = json.dumps(data)
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="data.json",
            out_dir=out_dir,
            target_size=50,
            min_size=10,
            max_size=100,
        )
        
        for path in paths:
            assert path.endswith('.json')
        
        for chunk in manifest['chunks']:
            assert chunk['file'].endswith('.json')
    
    def test_each_chunk_file_is_valid_json(self, tmp_path):
        """Each chunk file contains valid JSON."""
        data = [{"id": i, "data": f"item_{i}"} for i in range(30)]
        content = json.dumps(data)
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="data.json",
            out_dir=out_dir,
            target_size=100,
            min_size=20,
            max_size=300,
        )
        
        for path in paths:
            chunk_content = Path(path).read_text()
            parsed = json.loads(chunk_content)  # Should not raise
            assert isinstance(parsed, list)
    
    def test_manifest_has_element_range_for_arrays(self, tmp_path):
        """Manifest chunks include element_range for arrays."""
        data = [{"id": i} for i in range(20)]
        content = json.dumps(data)
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="data.json",
            out_dir=out_dir,
            target_size=50,
            min_size=10,
            max_size=100,
        )
        
        for chunk in manifest['chunks']:
            assert 'element_range' in chunk
            start, end = chunk['element_range']
            assert isinstance(start, int)
            assert isinstance(end, int)
            assert start <= end
    
    def test_manifest_has_keys_for_objects(self, tmp_path):
        """Manifest chunks include keys list for objects."""
        data = {f"key_{i}": {"value": i} for i in range(10)}
        content = json.dumps(data)
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="data.json",
            out_dir=out_dir,
            target_size=50,
            min_size=10,
            max_size=150,
        )
        
        for chunk in manifest['chunks']:
            assert 'keys' in chunk
            assert 'key_range' in chunk
            assert isinstance(chunk['keys'], list)
    
    def test_invalid_json_falls_back_to_text(self, tmp_path):
        """Invalid JSON content falls back to text chunking."""
        content = "this is not valid JSON at all " * 100
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="data.json",  # .json extension but invalid content
            out_dir=out_dir,
            target_size=100,
            min_size=20,
            max_size=200,
        )
        
        assert manifest['format'] == 'json'  # Detected by extension
        assert manifest['chunking_method'] == 'smart_text'  # Fell back
        assert manifest['json_chunked'] is False
        
        # Chunks have .txt extension when falling back
        for path in paths:
            assert path.endswith('.txt')
    
    def test_pretty_printed_json_handled(self, tmp_path):
        """Pretty-printed JSON is handled correctly."""
        data = [{"id": i, "nested": {"value": i * 2}} for i in range(20)]
        content = json.dumps(data, indent=4)  # Pretty printed
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="data.json",
            out_dir=out_dir,
            target_size=200,
            min_size=50,
            max_size=500,
        )
        
        assert manifest['json_chunked'] is True
        for path in paths:
            chunk_content = Path(path).read_text()
            parsed = json.loads(chunk_content)
            assert isinstance(parsed, list)
    
    def test_minified_json_handled(self, tmp_path):
        """Minified JSON is handled correctly."""
        data = [{"id": i} for i in range(30)]
        content = json.dumps(data, separators=(',', ':'))  # Minified
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="data.json",
            out_dir=out_dir,
            target_size=50,
            min_size=10,
            max_size=100,
        )
        
        assert manifest['json_chunked'] is True


class TestGoalJsonSplitting:
    """Goal-alignment: JSON splits on structural boundaries.
    
    Paper requirement: Content-aware chunking for JSON at natural boundaries.
    """
    
    def test_goal_json_array_splitting(self, tmp_path):
        """Large JSON array splits into element groups, each valid JSON.
        
        Goal: JSON arrays are split at element boundaries, not mid-element.
        Each chunk is a valid JSON array that can be parsed independently.
        """
        # Create a large JSON array
        data = [{"id": i, "name": f"Item {i}", "data": "x" * 50} for i in range(100)]
        content = json.dumps(data, indent=2)
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="large_array.json",
            out_dir=out_dir,
            target_size=500,
            min_size=100,
            max_size=1000,
        )
        
        # Verify chunking method
        assert manifest['format'] == 'json'
        assert manifest['chunking_method'] == 'smart_json'
        assert manifest['json_chunked'] is True
        assert len(paths) > 1, "Should have multiple chunks"
        
        # Verify each chunk is valid JSON array
        all_elements = []
        for path in paths:
            chunk_content = Path(path).read_text()
            parsed = json.loads(chunk_content)
            assert isinstance(parsed, list), "Each chunk should be a JSON array"
            all_elements.extend(parsed)
        
        # Verify all original elements are present
        assert len(all_elements) == 100
        ids = [e['id'] for e in all_elements]
        assert sorted(ids) == list(range(100))
        
        # Verify manifest has element_range for each chunk
        for chunk in manifest['chunks']:
            assert 'element_range' in chunk
            start, end = chunk['element_range']
            assert end > start or (start == 0 and end == 0), "Element range should be non-empty"
    
    def test_goal_json_object_splitting(self, tmp_path):
        """Large JSON object splits by top-level keys, each valid JSON.
        
        Goal: JSON objects are split at key boundaries.
        Each chunk is a valid JSON object containing a subset of keys.
        """
        # Create a large JSON object
        data = {f"section_{i}": {"title": f"Section {i}", "content": "y" * 100} for i in range(50)}
        content = json.dumps(data, indent=2)
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="large_object.json",
            out_dir=out_dir,
            target_size=500,
            min_size=100,
            max_size=1000,
        )
        
        # Verify chunking method
        assert manifest['format'] == 'json'
        assert manifest['chunking_method'] == 'smart_json'
        assert manifest['json_chunked'] is True
        assert len(paths) > 1, "Should have multiple chunks"
        
        # Verify each chunk is valid JSON object
        all_keys = set()
        for path in paths:
            chunk_content = Path(path).read_text()
            parsed = json.loads(chunk_content)
            assert isinstance(parsed, dict), "Each chunk should be a JSON object"
            all_keys.update(parsed.keys())
        
        # Verify all original keys are present
        original_keys = set(data.keys())
        assert all_keys == original_keys
        
        # Verify manifest has keys list for each chunk
        for chunk in manifest['chunks']:
            assert 'keys' in chunk
            assert 'key_range' in chunk
            assert len(chunk['keys']) > 0 or chunk['key_range'] == [0, 0]
    
    def test_goal_json_chunks_parseable_independently(self, tmp_path):
        """Each JSON chunk can be parsed and used independently.
        
        Goal: A sub-agent receiving a single chunk can parse it and work with
        the data without needing context from other chunks.
        """
        # Mixed nested structure
        data = [
            {"type": "user", "id": i, "profile": {"name": f"User {i}", "settings": {"theme": "dark"}}}
            for i in range(40)
        ]
        content = json.dumps(data)
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="users.json",
            out_dir=out_dir,
            target_size=300,
            min_size=50,
            max_size=600,
        )
        
        # Each chunk should be independently usable
        for i, path in enumerate(paths):
            chunk_content = Path(path).read_text()
            
            # Parse without error
            parsed = json.loads(chunk_content)
            
            # Can iterate and access nested data
            for item in parsed:
                assert 'id' in item
                assert 'profile' in item
                assert 'name' in item['profile']


class TestEdgeCases:
    """Edge case tests for JSON chunking."""
    
    def test_single_large_array_element(self, tmp_path):
        """Array with single large element handled."""
        data = [{"data": "x" * 1000}]  # One big element
        content = json.dumps(data)
        
        chunks, success = _chunk_json_array(content, target_size=100, min_size=50, max_size=2000)
        assert success is True
        assert len(chunks) == 1
        assert chunks[0]['element_range'] == [0, 1]
    
    def test_single_large_object_key(self, tmp_path):
        """Object with single large value handled."""
        data = {"big_key": {"data": "y" * 1000}}
        content = json.dumps(data)
        
        chunks, success = _chunk_json_object(content, target_size=100, min_size=50, max_size=2000)
        assert success is True
        assert len(chunks) == 1
        assert chunks[0]['keys'] == ['big_key']
    
    def test_deeply_nested_json(self, tmp_path):
        """Deeply nested JSON handled."""
        data = [{"level1": {"level2": {"level3": {"value": i}}}} for i in range(20)]
        content = json.dumps(data, indent=2)
        
        out_dir = tmp_path / "chunks"
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path="nested.json",
            out_dir=out_dir,
            target_size=200,
            min_size=50,
            max_size=500,
        )
        
        assert manifest['json_chunked'] is True
        for path in paths:
            parsed = json.loads(Path(path).read_text())
            assert isinstance(parsed, list)
    
    def test_json_with_unicode(self, tmp_path):
        """JSON with unicode characters handled."""
        data = [{"name": f"用户 {i}", "emoji": "🎉"} for i in range(10)]
        content = json.dumps(data, ensure_ascii=False)
        
        chunks, success = _chunk_json_array(content, target_size=50, min_size=10, max_size=150)
        assert success is True
        
        for chunk in chunks:
            parsed = json.loads(chunk['json_content'])
            for item in parsed:
                assert '用户' in item['name'] or item['name'].startswith('用户')
    
    def test_json_with_null_values(self):
        """JSON with null values handled."""
        data = [{"id": i, "value": None if i % 2 == 0 else i} for i in range(10)]
        content = json.dumps(data)
        
        chunks, success = _chunk_json_array(content, target_size=50, min_size=10, max_size=150)
        assert success is True
        
        for chunk in chunks:
            parsed = json.loads(chunk['json_content'])
            assert isinstance(parsed, list)
    
    def test_json_with_boolean_values(self):
        """JSON with boolean values handled."""
        data = [{"id": i, "active": i % 2 == 0} for i in range(10)]
        content = json.dumps(data)
        
        chunks, success = _chunk_json_array(content, target_size=50, min_size=10, max_size=150)
        assert success is True
    
    def test_array_of_primitives(self):
        """Array of primitive values (numbers, strings) handled."""
        data = list(range(100))
        content = json.dumps(data)
        
        chunks, success = _chunk_json_array(content, target_size=50, min_size=10, max_size=100)
        assert success is True
        
        all_values = []
        for chunk in chunks:
            parsed = json.loads(chunk['json_content'])
            all_values.extend(parsed)
        assert all_values == list(range(100))
    
    def test_merge_small_trailing_chunk_array(self):
        """Small trailing chunk is merged with previous for arrays."""
        # Create data where last chunk would be very small
        data = [{"id": i, "data": "x" * 50} for i in range(10)]
        content = json.dumps(data)
        
        # With these sizes, we should see merging behavior
        chunks, success = _chunk_json_array(content, target_size=300, min_size=200, max_size=800)
        assert success is True
        
        # Verify element continuity
        expected_start = 0
        for chunk in chunks:
            assert chunk['element_range'][0] == expected_start
            expected_start = chunk['element_range'][1]
    
    def test_merge_small_trailing_chunk_object(self):
        """Small trailing chunk is merged with previous for objects."""
        data = {f"k{i}": {"v": i} for i in range(10)}
        content = json.dumps(data)
        
        chunks, success = _chunk_json_object(content, target_size=100, min_size=80, max_size=300)
        assert success is True
        
        # Verify key continuity
        expected_start = 0
        for chunk in chunks:
            assert chunk['key_range'][0] == expected_start
            expected_start = chunk['key_range'][1]
