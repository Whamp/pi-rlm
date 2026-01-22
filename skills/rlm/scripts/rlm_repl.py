#!/usr/bin/env python3
"""Persistent mini-REPL for RLM-style workflows.

Provides a stateful Python environment across invocations by saving state to disk.

Usage:
  python rlm_repl.py init path/to/context.txt
  python rlm_repl.py --state .pi/rlm_state/<session>/state.pkl exec -c 'print(len(content))'

Injected environment:
  - context, content: The loaded file content
  - buffers: list[str] for intermediate results  
  - state_path, session_dir: Path objects
  - peek, grep, grep_raw, write_chunks, smart_chunk, add_buffer
  - handles, last_handle, expand, count, filter_handle, map_field, sum_field
  - llm_query, llm_query_batch, set_final_answer, has_final_answer, get_final_answer
"""

from __future__ import annotations

import argparse
import io
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


# Constants
DEFAULT_RLM_STATE_DIR = Path(".pi/rlm_state")
DEFAULT_MAX_DEPTH = 3
DEFAULT_LLM_TIMEOUT = 120
DEFAULT_LLM_MODEL = "google/gemini-2.0-flash-lite"
DEFAULT_MAX_OUTPUT_CHARS = 8000
PREVIEW_LENGTH = 80
MANIFEST_PREVIEW_LINES = 5

_GLOBAL_CONCURRENCY_SEMAPHORE = threading.Semaphore(5)
_CODEMAP_CACHE: Optional[Union[str, bool]] = None


class RlmReplError(RuntimeError):
    pass


# =============================================================================
# Utility Functions
# =============================================================================

def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _sanitize_session_name(filename: str) -> str:
    """Convert filename to a clean session name component."""
    name = re.sub(r'[^a-zA-Z0-9]+', '-', Path(filename).stem.lower()).strip('-')
    return (name[:30].rstrip('-') if len(name) > 30 else name) or 'context'


def _create_session_path(context_path: Path) -> Path:
    """Generate a timestamped session directory path."""
    name = _sanitize_session_name(context_path.name)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    return DEFAULT_RLM_STATE_DIR / f"{name}-{timestamp}" / "state.pkl"


def _load_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        raise RlmReplError(f"No state found at {state_path}. Run: python rlm_repl.py init <context_path>")
    with state_path.open("rb") as f:
        state = pickle.load(f)
    if not isinstance(state, dict):
        raise RlmReplError(f"Corrupt state file: {state_path}")
    # Auto-migrate to v3
    if state.get("version", 1) < 3:
        state.update(version=3, max_depth=DEFAULT_MAX_DEPTH, remaining_depth=DEFAULT_MAX_DEPTH,
                     preserve_recursive_state=False, final_answer=None)
    return state


def _save_state(state: Dict[str, Any], state_path: Path) -> None:
    _ensure_parent_dir(state_path)
    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    with tmp_path.open("wb") as f:
        pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp_path.replace(state_path)


def _read_text_file(path: Path, max_bytes: int | None = None) -> str:
    if not path.exists():
        raise RlmReplError(f"Context file does not exist: {path}")
    with path.open("rb") as f:
        data = f.read() if max_bytes is None else f.read(max_bytes)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _truncate(s: str, max_chars: int) -> str:
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    return s[:max_chars] + f"\n... [truncated to {max_chars} chars] ...\n"


def _filter_pickleable(d: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    kept, dropped = {}, []
    for k, v in d.items():
        try:
            pickle.dumps(v, protocol=pickle.HIGHEST_PROTOCOL)
            kept[k] = v
        except Exception:
            dropped.append(k)
    return kept, dropped


def _count_lines_in_range(content: str, start: int, end: int) -> Tuple[int, int]:
    """Return (start_line, end_line) for a character range (1-indexed)."""
    if not content:
        return (1, 1)
    return (content[:start].count('\n') + 1, content[:end].count('\n') + 1)


def _line_to_char_position(content: str, line_num: int) -> int:
    """Convert 1-indexed line number to character position."""
    if line_num <= 1:
        return 0
    lines = content.split('\n')
    return sum(len(lines[i]) + 1 for i in range(min(line_num - 1, len(lines))))


# =============================================================================
# Codemap Detection
# =============================================================================

def _detect_codemap() -> Optional[str]:
    """Auto-detect codemap availability. Returns command string or None."""
    global _CODEMAP_CACHE
    if _CODEMAP_CACHE is not None:
        return _CODEMAP_CACHE if _CODEMAP_CACHE else None
    
    # Check RLM_CODEMAP_PATH env var
    env_path = os.environ.get('RLM_CODEMAP_PATH', '').strip()
    if env_path and Path(env_path).exists():
        _CODEMAP_CACHE = env_path
        return env_path
    
    # Try codemap in PATH
    for cmd in ['codemap', 'npx codemap']:
        try:
            result = subprocess.run(cmd.split() + ['--version'], capture_output=True, timeout=30)
            if result.returncode == 0:
                _CODEMAP_CACHE = cmd
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    
    _CODEMAP_CACHE = False
    return None


def _extract_symbol_boundaries(codemap_output: str, context_path: str) -> List[Dict[str, Any]]:
    """Parse codemap JSON output to extract symbol boundaries."""
    try:
        data = json.loads(codemap_output)
    except json.JSONDecodeError:
        return []
    
    files = data if isinstance(data, list) else data.get('files', [])
    context_name = Path(context_path).name
    context_resolved = Path(context_path).resolve() if Path(context_path).exists() else None
    
    for file_entry in files:
        file_path = file_entry.get('path', '')
        if Path(file_path).name == context_name or \
           (context_resolved and Path(file_path).resolve() == context_resolved):
            symbols = []
            for sym in file_entry.get('symbols', []):
                lines = sym.get('lines', [])
                if len(lines) >= 2:
                    symbols.append({
                        'name': sym.get('name', ''), 'kind': sym.get('kind', 'unknown'),
                        'signature': sym.get('signature', ''), 'start_line': lines[0],
                        'end_line': lines[1], 'exported': sym.get('exported', False),
                    })
            return sorted(symbols, key=lambda s: s['start_line'])
    return []


# =============================================================================
# Chunking Helpers
# =============================================================================

def _merge_trailing_chunk(chunks: List[Dict], min_size: int, max_size: int, 
                          get_size: Callable, merge_fn: Callable) -> None:
    """Merge tiny trailing chunk into previous if under min_size. Modifies chunks in place."""
    if len(chunks) < 2:
        return
    last_size = get_size(chunks[-1])
    if last_size >= min_size:
        return
    prev_size = get_size(chunks[-2])
    if prev_size + last_size <= max_size:
        merge_fn(chunks[-2], chunks[-1])
        chunks.pop()


# =============================================================================
# Text Chunking
# =============================================================================

def _chunk_text(content: str, target_size: int, min_size: int, max_size: int) -> List[Dict[str, Any]]:
    """Split plain text at paragraph/line boundaries."""
    if len(content) <= max_size:
        return [{'start': 0, 'end': len(content), 'split_reason': 'single_chunk', 'boundaries': []}]
    
    chunks, pos = [], 0
    while pos < len(content):
        remaining = len(content) - pos
        if remaining <= max_size:
            chunks.append({'start': pos, 'end': len(content), 
                          'split_reason': 'end' if chunks else 'single_chunk', 'boundaries': []})
            break
        
        search_start = pos + min(target_size, remaining)
        search_end = pos + min(max_size, remaining)
        search_region = content[search_start:search_end]
        
        # Find best break point
        split_pos, split_reason = None, None
        for pattern, reason in [(r'\n\n+', 'paragraph'), (r'\n', 'line'), (r'\s', 'word')]:
            match = re.search(pattern, search_region)
            if match:
                split_pos, split_reason = search_start + match.end(), reason
                break
        if split_pos is None:
            split_pos, split_reason = pos + max_size, 'hard_split'
        
        chunks.append({'start': pos, 'end': split_pos, 
                      'split_reason': 'start' if not chunks else split_reason, 'boundaries': []})
        pos = split_pos
    return chunks


# =============================================================================
# Markdown Chunking
# =============================================================================

def _find_header_boundaries(content: str) -> List[Tuple[int, int, int, str]]:
    """Find all markdown header positions. Returns [(start, end, level, text), ...]."""
    return [(m.start(), m.end(), len(m.group(1)), m.group(2).strip()) 
            for m in re.finditer(r'^(#{1,6})\s+(.+?)$', content, re.MULTILINE)]


def _chunk_markdown(content: str, target_size: int, min_size: int, max_size: int) -> List[Dict[str, Any]]:
    """Split markdown content at header boundaries."""
    headers = _find_header_boundaries(content)
    if not headers:
        return _chunk_text(content, target_size, min_size, max_size)
    
    # Build sections
    sections = []
    for i, (start, end, level, text) in enumerate(headers):
        next_start = headers[i + 1][0] if i + 1 < len(headers) else len(content)
        sections.append({'start': start, 'end': next_start, 'level': level, 
                        'header_text': text, 'header_line': content[:start].count('\n') + 1})
    
    # Handle preamble
    if headers[0][0] > 0:
        sections.insert(0, {'start': 0, 'end': headers[0][0], 'level': 0, 
                           'header_text': '(preamble)', 'header_line': 1})
    
    # Combine sections into chunks
    chunks = []
    current = {'start': sections[0]['start'], 'end': sections[0]['end'], 
               'split_reason': 'start', 'boundaries': []}
    
    if sections[0]['level'] > 0:
        current['boundaries'].append({'type': 'heading', 'level': sections[0]['level'],
                                      'text': sections[0]['header_text'], 'line': sections[0]['header_line']})
    
    for section in sections[1:]:
        section_size = section['end'] - section['start']
        current_size = current['end'] - current['start']
        
        should_split = False
        if current_size + section_size > max_size:
            should_split, split_reason = True, 'max_size'
        elif current_size >= target_size and section['level'] <= 3:
            should_split, split_reason = True, f"header_level_{section['level']}"
        elif current_size >= target_size and current_size + section_size > target_size * 1.5:
            should_split, split_reason = True, 'target_size'
        
        if should_split:
            chunks.append(current)
            current = {'start': section['start'], 'end': section['end'], 
                      'split_reason': split_reason, 'boundaries': []}
        else:
            current['end'] = section['end']
        
        if section['level'] > 0:
            current['boundaries'].append({'type': 'heading', 'level': section['level'],
                                         'text': section['header_text'], 'line': section['header_line']})
    chunks.append(current)
    
    # Merge trailing chunk
    def get_size(c): return c['end'] - c['start']
    def merge(prev, last):
        prev['end'] = last['end']
        prev['boundaries'].extend(last['boundaries'])
    _merge_trailing_chunk(chunks, min_size, max_size, get_size, merge)
    
    # Split oversized chunks
    final_chunks = []
    for chunk in chunks:
        if chunk['end'] - chunk['start'] > max_size:
            chunk_content = content[chunk['start']:chunk['end']]
            for i, sub in enumerate(_chunk_text(chunk_content, target_size, min_size, max_size)):
                final_chunks.append({
                    'start': chunk['start'] + sub['start'], 'end': chunk['start'] + sub['end'],
                    'split_reason': 'oversized_section' if i > 0 else chunk['split_reason'],
                    'boundaries': chunk['boundaries'] if i == 0 else [],
                })
        else:
            final_chunks.append(chunk)
    return final_chunks


# =============================================================================
# Code Chunking
# =============================================================================

_CODE_EXTENSIONS = frozenset({
    '.py', '.pyi', '.pyw', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts',
    '.rs', '.go', '.java', '.c', '.h', '.cc', '.cpp', '.cxx', '.hpp', '.hxx', '.cs',
    '.rb', '.php', '.swift', '.kt', '.kts', '.scala', '.lua', '.sh', '.bash', '.zsh',
    '.pl', '.pm', '.r', '.R', '.sql',
})
_MARKDOWN_EXTENSIONS = frozenset({'.md', '.markdown', '.mdx', '.mdown', '.mkd'})


def _chunk_code(content: str, context_path: str, target_size: int, min_size: int, 
                max_size: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Split code at function/class boundaries using codemap. Returns (chunks, codemap_used)."""
    codemap_cmd = _detect_codemap()
    if not codemap_cmd:
        return _chunk_text(content, target_size, min_size, max_size), False
    
    context_resolved = Path(context_path).resolve()
    if not context_resolved.exists():
        return _chunk_text(content, target_size, min_size, max_size), False
    
    try:
        cmd = codemap_cmd.split() + ['-o', 'json', str(context_resolved)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, 
                               cwd=context_resolved.parent)
        if result.returncode != 0:
            return _chunk_text(content, target_size, min_size, max_size), False
        symbols = _extract_symbol_boundaries(result.stdout, str(context_resolved))
        if not symbols:
            return _chunk_text(content, target_size, min_size, max_size), False
    except (subprocess.TimeoutExpired, OSError, Exception):
        return _chunk_text(content, target_size, min_size, max_size), False
    
    # Convert to char positions
    symbol_positions = []
    for sym in symbols:
        start_pos = _line_to_char_position(content, sym['start_line'])
        end_pos = min(_line_to_char_position(content, sym['end_line'] + 1), len(content))
        symbol_positions.append({**sym, 'start_char': start_pos, 'end_char': end_pos})
    
    # Build chunks
    chunks = []
    current = {'start': 0, 'end': 0, 'split_reason': 'start', 'boundaries': []}
    
    if symbol_positions and symbol_positions[0]['start_char'] > 0:
        current['end'] = symbol_positions[0]['start_char']
    
    for sym in symbol_positions:
        sym_size = sym['end_char'] - sym['start_char']
        current_size = current['end'] - current['start']
        
        if current['end'] == 0:
            current['end'] = sym['end_char']
            current['boundaries'].append({'type': sym['kind'], 'name': sym['name'],
                                         'signature': sym['signature'], 'line': sym['start_line']})
            continue
        
        should_split, split_reason = False, None
        if current_size + sym_size > max_size:
            should_split, split_reason = True, 'max_size'
        elif current_size >= target_size and sym['kind'] in ('function', 'class', 'method', 'impl'):
            should_split, split_reason = True, f"symbol_{sym['kind']}"
        elif current_size >= target_size * 1.2:
            should_split, split_reason = True, 'target_size'
        
        if should_split:
            chunks.append(current)
            current = {'start': sym['start_char'], 'end': sym['end_char'], 
                      'split_reason': split_reason, 'boundaries': []}
        else:
            current['end'] = sym['end_char']
        
        current['boundaries'].append({'type': sym['kind'], 'name': sym['name'],
                                      'signature': sym['signature'], 'line': sym['start_line']})
    
    # Handle trailing content
    if symbol_positions and symbol_positions[-1]['end_char'] < len(content):
        current['end'] = len(content)
    elif not symbol_positions:
        current['end'] = len(content)
    
    if current['end'] > current['start']:
        chunks.append(current)
    
    # Merge trailing
    def get_size(c): return c['end'] - c['start']
    def merge(prev, last):
        prev['end'] = last['end']
        prev['boundaries'].extend(last['boundaries'])
    _merge_trailing_chunk(chunks, min_size, max_size, get_size, merge)
    
    return (chunks, True) if chunks else (_chunk_text(content, target_size, min_size, max_size), False)


# =============================================================================
# JSON Chunking
# =============================================================================

def _chunk_json_collection(data: Any, content: str, target_size: int, min_size: int, 
                           max_size: int, is_array: bool) -> Tuple[List[Dict], bool]:
    """Generic chunker for JSON arrays and objects."""
    items = list(data) if is_array else list(data.keys())
    
    if not items:
        return [{'start': 0, 'end': len(content), 'split_reason': 'single_chunk', 'boundaries': [],
                 'element_range' if is_array else 'key_range': [0, 0],
                 **({'keys': []} if not is_array else {}), 'json_content': content}], True
    
    if len(content) <= max_size:
        meta = {'start': 0, 'end': len(content), 'split_reason': 'single_chunk', 'boundaries': [],
                'element_range' if is_array else 'key_range': [0, len(items)], 'json_content': content}
        if not is_array:
            meta['keys'] = items
        return [meta], True
    
    # Estimate items per chunk
    if is_array:
        item_sizes = [len(json.dumps(data[i], separators=(',', ':'))) for i in range(len(data))]
    else:
        item_sizes = [len(json.dumps({k: data[k]}, separators=(',', ':'))) - 2 for k in items]
    
    avg_size = sum(item_sizes) / len(items) if items else 0
    items_per_chunk = max(1, int((target_size - 2) / (avg_size + 1))) if avg_size > 0 else len(items)
    
    chunks, i = [], 0
    while i < len(items):
        chunk_end = min(i + items_per_chunk, len(items))
        
        # Build chunk data
        if is_array:
            chunk_data = data[i:chunk_end]
        else:
            chunk_keys = items[i:chunk_end]
            chunk_data = {k: data[k] for k in chunk_keys}
        chunk_json = json.dumps(chunk_data, indent=2)
        
        # Adjust size
        while len(chunk_json) > max_size and chunk_end > i + 1:
            chunk_end -= 1
            chunk_data = data[i:chunk_end] if is_array else {k: data[k] for k in items[i:chunk_end]}
            chunk_json = json.dumps(chunk_data, indent=2)
        
        while chunk_end < len(items) and len(chunk_json) < target_size:
            test_end = chunk_end + 1
            test_data = data[i:test_end] if is_array else {k: data[k] for k in items[i:test_end]}
            test_json = json.dumps(test_data, indent=2)
            if len(test_json) <= max_size:
                chunk_end, chunk_json = test_end, test_json
            else:
                break
        
        split_reason = 'start' if not chunks else ('end' if chunk_end >= len(items) else 
                       ('element_boundary' if is_array else 'key_boundary'))
        
        meta = {'start': 0, 'end': len(chunk_json), 'split_reason': split_reason, 'boundaries': [],
                'element_range' if is_array else 'key_range': [i, chunk_end], 'json_content': chunk_json}
        if not is_array:
            meta['keys'] = items[i:chunk_end]
        chunks.append(meta)
        i = chunk_end
    
    # Merge trailing
    def get_size(c): return len(c['json_content'])
    def merge(prev, last):
        combined_start = prev['element_range' if is_array else 'key_range'][0]
        combined_end = last['element_range' if is_array else 'key_range'][1]
        if is_array:
            combined_data = data[combined_start:combined_end]
        else:
            combined_keys = items[combined_start:combined_end]
            combined_data = {k: data[k] for k in combined_keys}
        combined_json = json.dumps(combined_data, indent=2)
        if len(combined_json) <= max_size:
            prev['element_range' if is_array else 'key_range'] = [combined_start, combined_end]
            if not is_array:
                prev['keys'] = combined_keys
            prev['json_content'] = combined_json
            prev['end'] = len(combined_json)
            return True
        return False
    
    if len(chunks) >= 2 and get_size(chunks[-1]) < min_size:
        if merge(chunks[-2], chunks[-1]):
            chunks.pop()
    
    return chunks, True


def _chunk_json(content: str, target_size: int, min_size: int, 
                max_size: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Split JSON content at structural boundaries."""
    stripped = content.strip()
    if not stripped:
        return [], False
    
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [], False
    
    if isinstance(data, list):
        return _chunk_json_collection(data, content, target_size, min_size, max_size, is_array=True)
    elif isinstance(data, dict):
        return _chunk_json_collection(data, content, target_size, min_size, max_size, is_array=False)
    return [], False


# =============================================================================
# Smart Chunking
# =============================================================================

def _detect_format(content: str, context_path: str) -> str:
    """Detect content format from extension or content analysis."""
    ext = Path(context_path).suffix.lower()
    if ext in _MARKDOWN_EXTENSIONS:
        return 'markdown'
    if ext in _CODE_EXTENSIONS:
        return 'code'
    if ext == '.json':
        return 'json'
    if ext in {'.txt', '.text', '.log'}:
        return 'text'
    # Fallback: check for markdown headers
    if len(re.findall(r'^#{1,6}\s+\S', content, re.MULTILINE)) > 5:
        return 'markdown'
    return 'text'


def _generate_chunk_hints(chunk_text: str) -> Dict[str, Any]:
    """Generate content hints for a chunk."""
    hints: Dict[str, Any] = {}
    lines = chunk_text.split('\n')
    
    # Section headers
    headers = [l.strip()[:80] for l in lines[:100] if l.strip().startswith('#')]
    if headers:
        hints["section_headers"] = headers[:5]
    
    # Code blocks
    code_blocks = chunk_text.count('```')
    if code_blocks >= 2:
        hints["has_code_blocks"] = True
        hints["code_block_count"] = code_blocks // 2
    
    # Code density
    if len(chunk_text) > 0:
        code_density = sum(1 for c in chunk_text if c in '{}();[]<>=') / len(chunk_text)
        if code_density > 0.02:
            hints["likely_code"] = True
    
    # JSON detection
    stripped = chunk_text.strip()
    if (stripped.startswith('{') and stripped.endswith('}')) or \
       (stripped.startswith('[') and stripped.endswith(']')):
        hints["likely_json"] = True
    
    # Density
    if lines:
        density = sum(1 for l in lines if l.strip()) / len(lines)
        hints["density"] = "dense" if density > 0.8 else ("sparse" if density < 0.4 else "normal")
    
    return hints


def _generate_chunk_preview(chunk_text: str, max_lines: int = MANIFEST_PREVIEW_LINES) -> str:
    """Generate a preview of the chunk's beginning."""
    lines = chunk_text.split('\n')[:max_lines]
    preview = '\n'.join(lines)
    if len(chunk_text.split('\n')) > max_lines:
        preview += '\n...'
    return preview


def _smart_chunk_impl(content: str, context_path: str, out_dir: Path, target_size: int = 200_000,
                      min_size: int = 50_000, max_size: int = 400_000, 
                      encoding: str = "utf-8") -> Tuple[List[str], Dict[str, Any]]:
    """Core smart_chunk implementation."""
    out_dir.mkdir(parents=True, exist_ok=True)
    format_type = _detect_format(content, context_path)
    
    codemap_used, json_chunked = False, False
    
    if format_type == 'markdown':
        chunk_metas = _chunk_markdown(content, target_size, min_size, max_size)
        chunking_method = 'smart_markdown'
    elif format_type == 'code':
        chunk_metas, codemap_used = _chunk_code(content, context_path, target_size, min_size, max_size)
        chunking_method = 'smart_code' if codemap_used else 'smart_text'
    elif format_type == 'json':
        chunk_metas, json_chunked = _chunk_json(content, target_size, min_size, max_size)
        if not json_chunked:
            chunk_metas = _chunk_text(content, target_size, min_size, max_size)
        chunking_method = 'smart_json' if json_chunked else 'smart_text'
    else:
        chunk_metas = _chunk_text(content, target_size, min_size, max_size)
        chunking_method = 'smart_text'
    
    # Write chunks
    paths, manifest_chunks = [], []
    for i, meta in enumerate(chunk_metas):
        chunk_id = f"chunk_{i:04d}"
        chunk_file = f"{chunk_id}.json" if json_chunked else f"{chunk_id}.txt"
        chunk_path = out_dir / chunk_file
        
        chunk_text = meta.get('json_content') or content[meta['start']:meta['end']]
        chunk_path.write_text(chunk_text, encoding=encoding)
        paths.append(str(chunk_path))
        
        if 'json_content' in meta:
            start_line, end_line = 1, chunk_text.count('\n') + 1
        else:
            start_line, end_line = _count_lines_in_range(content, meta['start'], meta['end'])
        
        chunk_entry = {
            'id': chunk_id, 'file': chunk_file, 'start_char': meta['start'], 'end_char': meta['end'],
            'start_line': start_line, 'end_line': end_line, 'split_reason': meta['split_reason'],
            'format': format_type, 'preview': _generate_chunk_preview(chunk_text),
        }
        
        for key in ['element_range', 'key_range', 'keys', 'boundaries']:
            if meta.get(key):
                chunk_entry[key] = meta[key]
        
        hints = _generate_chunk_hints(chunk_text)
        if hints:
            chunk_entry['hints'] = hints
        
        manifest_chunks.append(chunk_entry)
    
    manifest = {
        'context_file': context_path, 'format': format_type, 'chunking_method': chunking_method,
        'codemap_available': _detect_codemap() is not None, 'codemap_used': codemap_used,
        'json_chunked': json_chunked, 'target_size': target_size, 'min_size': min_size,
        'max_size': max_size, 'total_chars': len(content), 'total_lines': content.count('\n') + 1,
        'chunk_count': len(manifest_chunks), 'chunks': manifest_chunks,
    }
    (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding=encoding)
    
    return paths, manifest


# =============================================================================
# LLM Query Infrastructure
# =============================================================================

def _parse_pi_json_output(output: str) -> str:
    """Extract final assistant text from pi --mode json output."""
    for line in reversed(output.strip().split('\n')):
        try:
            event = json.loads(line)
            if event.get('type') == 'message_end':
                message = event.get('message', {})
                if message.get('role') == 'assistant':
                    return '\n'.join(c['text'] for c in message.get('content', []) 
                                    if c.get('type') == 'text' and c.get('text'))
        except json.JSONDecodeError:
            continue
    return ""


def _log_query(session_dir: Path, entry: Dict[str, Any]) -> None:
    """Append a query log entry to llm_queries.jsonl."""
    if "timestamp" not in entry:
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with (session_dir / "llm_queries.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _spawn_sub_agent(prompt: str, remaining_depth: int, session_dir: Path, cleanup: bool = True,
                     model: str = DEFAULT_LLM_MODEL, timeout: int = DEFAULT_LLM_TIMEOUT) -> str:
    """Spawn a pi subprocess for a sub-query. Returns response text or error string."""
    query_id = f"q_{uuid.uuid4().hex[:8]}"
    start_time = time.time()
    depth_level = remaining_depth
    sub_session_dir = session_dir / f"depth-{depth_level}" / query_id
    sub_session_dir.mkdir(parents=True, exist_ok=True)
    
    def log_and_cleanup(response: str, status: str):
        _log_query(session_dir, {
            "query_id": query_id, "depth_level": depth_level, "remaining_depth": remaining_depth,
            "prompt_preview": prompt[:200] if prompt else "", "prompt_chars": len(prompt),
            "sub_state_dir": str(sub_session_dir), "response_preview": response[:200] if response else "",
            "response_chars": len(response), "duration_ms": int((time.time() - start_time) * 1000),
            "status": status, "cleanup": cleanup,
        })
        if cleanup and sub_session_dir.exists():
            shutil.rmtree(sub_session_dir, ignore_errors=True)
            depth_dir = sub_session_dir.parent
            if depth_dir.exists() and not any(depth_dir.iterdir()):
                depth_dir.rmdir()
        return response
    
    if remaining_depth <= 0:
        return log_and_cleanup("[ERROR: Recursion depth limit reached]", "depth_exceeded")
    
    prompt_file = sub_session_dir / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    
    cmd = ["pi", "--mode", "json", "-p", "--no-session", "--model", model,
           "--append-system-prompt", f"RLM_STATE_DIR={sub_session_dir} RLM_REMAINING_DEPTH={remaining_depth - 1}"]
    
    try:
        with prompt_file.open("r", encoding="utf-8") as f:
            result = subprocess.run(cmd, stdin=f, capture_output=True, text=True, timeout=timeout)
        
        if result.returncode != 0:
            return log_and_cleanup(f"[ERROR: Exit code {result.returncode}: {result.stderr[:500]}]", "failed")
        
        response = _parse_pi_json_output(result.stdout)
        if not response:
            return log_and_cleanup("[ERROR: Failed to parse response]", "parse_error")
        return log_and_cleanup(response, "success")
        
    except subprocess.TimeoutExpired:
        return log_and_cleanup(f"[ERROR: Timed out after {timeout}s]", "timeout")
    except Exception as e:
        return log_and_cleanup(f"[ERROR: {str(e)[:200]}]", "exception")


def _llm_query_batch_impl(prompts: List[str], remaining_depth: int, session_dir: Path,
                          cleanup: bool = True, concurrency: int = 5, 
                          max_retries: int = 3) -> Tuple[List[str], Dict[int, Dict[str, Any]]]:
    """Execute multiple queries concurrently with retry support."""
    batch_id = f"batch_{uuid.uuid4().hex[:8]}"
    effective_concurrency = min(concurrency, 5)
    results: List[Optional[str]] = [None] * len(prompts)
    failures: Dict[int, Dict[str, Any]] = {}
    
    def execute_with_retry(index: int, prompt: str) -> Tuple[int, str, Optional[Dict]]:
        last_error = ""
        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                time.sleep(2 ** (attempt - 2))
            
            with _GLOBAL_CONCURRENCY_SEMAPHORE:
                response = _spawn_sub_agent(prompt, remaining_depth, session_dir, cleanup)
            
            _log_query(session_dir, {"batch_id": batch_id, "batch_index": index, 
                                     "batch_size": len(prompts), "attempt": attempt,
                                     "prompt_preview": prompt[:200], "response_preview": response[:200],
                                     "status": "error" if response.startswith("[ERROR:") else "success"})
            
            if not response.startswith("[ERROR:"):
                return (index, response, None)
            last_error = response
        
        return (index, last_error, {"reason": "max_retries_exhausted", "attempts": max_retries, "error": last_error})
    
    with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
        futures = [executor.submit(execute_with_retry, i, p) for i, p in enumerate(prompts)]
        for future in as_completed(futures):
            index, response, failure_info = future.result()
            results[index] = response
            if failure_info:
                failures[index] = failure_info
    
    return ([r or "[ERROR: Unexpected None]" for r in results], failures)


# =============================================================================
# REPL Helpers Factory
# =============================================================================

def _make_handle_stub(handle: str, data: List[Any]) -> str:
    """Create a compact stub representation for a handle."""
    if not data:
        return f"{handle}: Array(0) []"
    
    first = data[0]
    preview = ""
    if isinstance(first, dict):
        for key in ('snippet', 'line', 'match'):
            if key in first:
                preview = first[key][:PREVIEW_LENGTH]
                break
        if not preview:
            for k, v in first.items():
                preview = f"{k}: {str(v)[:40]}"
                break
    else:
        preview = str(first)[:PREVIEW_LENGTH]
    
    preview = ' '.join(preview.split())[:PREVIEW_LENGTH]
    return f"{handle}: Array({len(data)}) [{preview}]"


def _make_helpers(context_ref: Dict[str, Any], buffers_ref: List[str], 
                  state_ref: Dict[str, Any], state_path_ref: Path):
    """Create the REPL helper functions."""
    state_ref.setdefault("handles", {})
    state_ref.setdefault("handle_counter", 0)
    handles_ref = state_ref["handles"]
    
    def _store_handle(data: List[Any]) -> str:
        state_ref["handle_counter"] += 1
        handle = f"$res{state_ref['handle_counter']}"
        handles_ref[handle] = data
        return _make_handle_stub(handle, data)
    
    def _parse_handle(h: str) -> str:
        if not h:
            raise ValueError("Empty handle")
        if h.startswith('$res') and ':' not in h:
            return h
        match = re.match(r'(\$res\d+):', h)
        return match.group(1) if match else h
    
    def _get_handle_data(h: str) -> List[Any]:
        h = _parse_handle(h)
        if h not in handles_ref:
            raise ValueError(f"Unknown handle: {h}")
        return handles_ref[h]
    
    # Content exploration
    def peek(start: int = 0, end: int = 1000) -> str:
        return context_ref.get("content", "")[start:end]
    
    def grep_raw(pattern: str, max_matches: int = 20, window: int = 120, flags: int = 0) -> List[Dict]:
        content = context_ref.get("content", "")
        out = []
        for m in re.finditer(pattern, content, flags):
            start, end = m.span()
            out.append({
                "match": m.group(0), "span": (start, end),
                "line_num": content[:start].count('\n') + 1,
                "snippet": content[max(0, start - window):min(len(content), end + window)],
            })
            if len(out) >= max_matches:
                break
        return out
    
    def grep(pattern: str, max_matches: int = 20, window: int = 120, flags: int = 0) -> str:
        return _store_handle(grep_raw(pattern, max_matches, window, flags))
    
    # Handle system
    def handles() -> str:
        if not handles_ref:
            return "No active handles."
        lines = [f"  {h}: Array({len(handles_ref[h])})" 
                 for h in sorted(handles_ref, key=lambda x: int(x.replace('$res', '')))]
        return "Active handles:\n" + "\n".join(lines)
    
    def last_handle() -> str:
        if state_ref["handle_counter"] == 0:
            raise ValueError("No handles created yet")
        return f"$res{state_ref['handle_counter']}"
    
    def expand(handle: str, limit: int = 10, offset: int = 0) -> List[Any]:
        return _get_handle_data(handle)[offset:offset + limit]
    
    def count(handle: str) -> int:
        return len(_get_handle_data(handle))
    
    def delete_handle(handle: str) -> str:
        h = _parse_handle(handle)
        if h not in handles_ref:
            return f"Handle {h} not found."
        del handles_ref[h]
        return f"Deleted {h}."
    
    def filter_handle(handle: str, predicate: Union[str, Callable]) -> str:
        data = _get_handle_data(handle)
        if isinstance(predicate, str):
            pattern = re.compile(predicate)
            def match_fn(item):
                if isinstance(item, dict):
                    return any(pattern.search(str(item.get(k, ''))) 
                              for k in ('snippet', 'line', 'match', 'content', 'text'))
                return bool(pattern.search(str(item)))
            filtered = [item for item in data if match_fn(item)]
        else:
            filtered = [item for item in data if predicate(item)]
        return _store_handle(filtered)
    
    def map_field(handle: str, field: str) -> str:
        data = _get_handle_data(handle)
        return _store_handle([item.get(field) if isinstance(item, dict) else None for item in data])
    
    def sum_field(handle: str, field: str = None) -> float:
        data = _get_handle_data(handle)
        total = 0.0
        for item in data:
            val = item.get(field, 0) if field and isinstance(item, dict) else item
            try:
                total += float(val)
            except (TypeError, ValueError):
                pass
        return total
    
    # Chunking
    def chunk_indices(size: int = 200_000, overlap: int = 0) -> List[Tuple[int, int]]:
        if size <= 0 or overlap < 0 or overlap >= size:
            raise ValueError("Invalid size/overlap")
        content = context_ref.get("content", "")
        spans, step = [], size - overlap
        for start in range(0, len(content), step):
            end = min(len(content), start + size)
            spans.append((start, end))
            if end >= len(content):
                break
        return spans
    
    def write_chunks(out_dir: str, size: int = 200_000, overlap: int = 0, 
                     prefix: str = "chunk", encoding: str = "utf-8", include_hints: bool = True) -> List[str]:
        content = context_ref.get("content", "")
        spans = chunk_indices(size=size, overlap=overlap)
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        
        paths, manifest_chunks = [], []
        for i, (s, e) in enumerate(spans):
            chunk_id = f"{prefix}_{i:04d}"
            p = out_path / f"{chunk_id}.txt"
            chunk_text = content[s:e]
            p.write_text(chunk_text, encoding=encoding)
            paths.append(str(p))
            
            start_line, end_line = _count_lines_in_range(content, s, e)
            chunk_meta = {"id": chunk_id, "file": f"{chunk_id}.txt", "start_char": s, "end_char": e,
                         "start_line": start_line, "end_line": end_line}
            if include_hints:
                chunk_meta["preview"] = _generate_chunk_preview(chunk_text)
                hints = _generate_chunk_hints(chunk_text)
                if hints:
                    chunk_meta["hints"] = hints
            manifest_chunks.append(chunk_meta)
        
        manifest = {"session": state_path_ref.parent.name, "context_file": context_ref.get("path", "unknown"),
                    "total_chars": len(content), "total_lines": content.count('\n') + 1,
                    "chunk_size": size, "overlap": overlap, "chunk_count": len(manifest_chunks),
                    "chunks": manifest_chunks}
        (out_path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return paths
    
    def smart_chunk(out_dir: str, target_size: int = 200_000, min_size: int = 50_000,
                    max_size: int = 400_000, encoding: str = "utf-8") -> List[str]:
        paths, _ = _smart_chunk_impl(
            content=context_ref.get("content", ""), context_path=context_ref.get("path", "unknown"),
            out_dir=Path(out_dir), target_size=target_size, min_size=min_size,
            max_size=max_size, encoding=encoding)
        return paths
    
    def add_buffer(text: str) -> None:
        buffers_ref.append(str(text))
    
    # LLM queries
    def llm_query(prompt: str, cleanup: bool = True) -> str:
        remaining_depth = state_ref.get("remaining_depth", DEFAULT_MAX_DEPTH)
        effective_cleanup = cleanup and not state_ref.get("preserve_recursive_state", False)
        with _GLOBAL_CONCURRENCY_SEMAPHORE:
            return _spawn_sub_agent(prompt, remaining_depth, state_path_ref.parent, effective_cleanup)
    
    def llm_query_batch(prompts: List[str], concurrency: int = 5, max_retries: int = 3,
                        cleanup: bool = True) -> Tuple[List[str], Dict[int, Dict]]:
        remaining_depth = state_ref.get("remaining_depth", DEFAULT_MAX_DEPTH)
        effective_cleanup = cleanup and not state_ref.get("preserve_recursive_state", False)
        return _llm_query_batch_impl(prompts, remaining_depth, state_path_ref.parent,
                                     effective_cleanup, concurrency, max_retries)
    
    # Finalization
    def set_final_answer(value: Any) -> None:
        try:
            json.dumps(value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Final answer must be JSON-serializable: {e}")
        state_ref["final_answer"] = {"set_at": datetime.now(timezone.utc).isoformat(), "value": value}
        vtype = type(value).__name__
        print(f"Final answer set (type: {vtype}" + (f", length: {len(value)})" if isinstance(value, (list, dict, str)) else ")"))
    
    def has_final_answer() -> bool:
        return state_ref.get("final_answer") is not None
    
    def get_final_answer() -> Any:
        fa = state_ref.get("final_answer")
        return fa["value"] if fa else None
    
    return {
        "peek": peek, "grep": grep, "grep_raw": grep_raw, "chunk_indices": chunk_indices,
        "write_chunks": write_chunks, "smart_chunk": smart_chunk, "add_buffer": add_buffer,
        "handles": handles, "last_handle": last_handle, "expand": expand, "count": count,
        "delete_handle": delete_handle, "filter_handle": filter_handle, "map_field": map_field,
        "sum_field": sum_field, "llm_query": llm_query, "llm_query_batch": llm_query_batch,
        "set_final_answer": set_final_answer, "has_final_answer": has_final_answer,
        "get_final_answer": get_final_answer,
    }


# =============================================================================
# CLI Commands
# =============================================================================

def cmd_init(args: argparse.Namespace) -> int:
    ctx_path = Path(args.context).resolve()
    state_path = Path(args.state) if args.state else _create_session_path(ctx_path)
    content = _read_text_file(ctx_path, max_bytes=args.max_bytes)
    
    state = {
        "version": 3, "max_depth": args.max_depth, "remaining_depth": args.max_depth,
        "preserve_recursive_state": args.preserve_recursive_state,
        "context": {"path": str(ctx_path), "loaded_at": time.time(), "content": content},
        "buffers": [], "handles": {}, "handle_counter": 0, "globals": {}, "final_answer": None,
    }
    _save_state(state, state_path)
    
    print(f"Session path: {state_path}")
    print(f"Session directory: {state_path.parent}")
    print(f"Context: {ctx_path} ({len(content):,} chars)")
    print(f"Max depth: {args.max_depth}")
    if args.preserve_recursive_state:
        print("Preserve recursive state: enabled")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = _load_state(state_path)
    ctx = state.get("context", {})
    
    print("RLM REPL status")
    print(f"  State file: {args.state}")
    print(f"  Session directory: {state_path.parent}")
    print(f"  Context path: {ctx.get('path')}")
    print(f"  Context chars: {len(ctx.get('content', '')):,}")
    print(f"  Max depth: {state.get('max_depth', DEFAULT_MAX_DEPTH)}")
    print(f"  Remaining depth: {state.get('remaining_depth', DEFAULT_MAX_DEPTH)}")
    if state.get("preserve_recursive_state"):
        print("  Preserve recursive state: enabled")
    
    fa = state.get("final_answer")
    if fa:
        v = fa.get("value")
        vtype = type(v).__name__
        print(f"  Final answer: SET (type: {vtype}" + (f", length: {len(v)})" if isinstance(v, (list, dict, str)) else ")"))
    else:
        print("  Final answer: NOT SET")
    
    print(f"  Buffers: {len(state.get('buffers', []))}")
    print(f"  Handles: {len(state.get('handles', {}))}")
    print(f"  Persisted vars: {len(state.get('globals', {}))}")
    
    if args.show_vars:
        for k in sorted(state.get('globals', {}).keys()):
            print(f"    - {k}")
        for h, data in sorted(state.get('handles', {}).items(), key=lambda x: int(x[0].replace('$res', ''))):
            print(f"    - {h}: Array({len(data)})")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    if state_path.exists():
        state_path.unlink()
        print(f"Deleted state: {state_path}")
    else:
        print(f"No state to delete at: {state_path}")
    return 0


def cmd_export_buffers(args: argparse.Namespace) -> int:
    state = _load_state(Path(args.state))
    buffers = state.get("buffers", [])
    out_path = Path(args.out)
    _ensure_parent_dir(out_path)
    out_path.write_text("\n\n".join(str(b) for b in buffers), encoding="utf-8")
    print(f"Wrote {len(buffers)} buffers to: {out_path}")
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    state_path = Path(args.state).resolve()
    state = _load_state(state_path)
    
    ctx = state.get("context")
    if not isinstance(ctx, dict) or "content" not in ctx:
        raise RlmReplError("State is missing a valid 'context'. Re-run init.")
    
    buffers = state.setdefault("buffers", [])
    persisted = state.setdefault("globals", {})
    code = args.code if args.code is not None else sys.stdin.read()
    
    # Build execution environment
    env = dict(persisted)
    env.update(context=ctx, content=ctx.get("content", ""), buffers=buffers,
               state_path=state_path, session_dir=state_path.parent)
    env.update(_make_helpers(ctx, buffers, state, state_path))
    
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(code, env, env)
    except Exception:
        traceback.print_exc(file=stderr_buf)
    
    # Update state from env
    if isinstance(env.get("context"), dict) and "content" in env["context"]:
        state["context"] = env["context"]
    if isinstance(env.get("buffers"), list):
        state["buffers"] = env["buffers"]
    
    # Persist new variables
    injected = {"__builtins__", "context", "content", "buffers", "state_path", "session_dir",
                *_make_helpers({}, [], {}, state_path).keys()}
    filtered, dropped = _filter_pickleable({k: v for k, v in env.items() if k not in injected})
    state["globals"] = filtered
    _save_state(state, state_path)
    
    out, err = stdout_buf.getvalue(), stderr_buf.getvalue()
    if dropped and args.warn_unpickleable:
        err += f"\nDropped unpickleable variables: {', '.join(dropped)}\n"
    if out:
        sys.stdout.write(_truncate(out, args.max_output_chars))
    if err:
        sys.stderr.write(_truncate(err, args.max_output_chars))
    return 0


def cmd_get_final_answer(args: argparse.Namespace) -> int:
    state = _load_state(Path(args.state))
    fa = state.get("final_answer")
    result = {"set": fa is not None, "value": fa.get("value") if fa else None,
              "set_at": fa.get("set_at") if fa else None}
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rlm_repl", description="Persistent mini-REPL for RLM-style workflows.")
    p.add_argument("--state", help="Path to state pickle")
    
    sub = p.add_subparsers(dest="cmd", required=True)
    
    p_init = sub.add_parser("init", help="Initialize from context file")
    p_init.add_argument("context", help="Path to context file")
    p_init.add_argument("--max-bytes", type=int, help="Max bytes to read")
    p_init.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH, help="Max recursion depth")
    p_init.add_argument("--preserve-recursive-state", action="store_true", help="Keep sub-session dirs")
    p_init.set_defaults(func=cmd_init)
    
    p_status = sub.add_parser("status", help="Show state summary")
    p_status.add_argument("--show-vars", action="store_true", help="List variable names")
    p_status.set_defaults(func=cmd_status)
    
    p_reset = sub.add_parser("reset", help="Delete state file")
    p_reset.set_defaults(func=cmd_reset)
    
    p_export = sub.add_parser("export-buffers", help="Export buffers to file")
    p_export.add_argument("out", help="Output file")
    p_export.set_defaults(func=cmd_export_buffers)
    
    p_exec = sub.add_parser("exec", help="Execute Python code")
    p_exec.add_argument("-c", "--code", help="Inline code (or read from stdin)")
    p_exec.add_argument("--max-output-chars", type=int, default=DEFAULT_MAX_OUTPUT_CHARS)
    p_exec.add_argument("--warn-unpickleable", action="store_true")
    p_exec.set_defaults(func=cmd_exec)
    
    p_final = sub.add_parser("get-final-answer", help="Get final answer as JSON")
    p_final.set_defaults(func=cmd_get_final_answer)
    
    return p


def main(argv: List[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    
    if args.cmd != "init" and not args.state:
        parser.error(f"--state is required for '{args.cmd}' command")
    
    try:
        return int(args.func(args))
    except RlmReplError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
