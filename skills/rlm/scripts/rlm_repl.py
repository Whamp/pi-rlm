#!/usr/bin/env python3
"""Persistent mini-REPL for RLM-style workflows in pi.

This script provides a *stateful* Python environment across invocations by
saving a pickle file to disk. It is intentionally small and dependency-free.

Typical flow:
  1) Initialize context (creates session directory automatically):
       python rlm_repl.py init path/to/context.txt
  2) Execute code repeatedly (state persists):
       python rlm_repl.py --state .pi/rlm_state/<session>/state.pkl exec -c 'print(len(content))'
       python rlm_repl.py --state ... exec <<'PYCODE'
       # you can write multi-line code
       hits = grep('TODO')
       print(hits[:3])
       PYCODE

The script injects these variables into the exec environment:
  - context: dict with keys {path, loaded_at, content}
  - content: string alias for context['content']
  - buffers: list[str] for storing intermediate text results
  - state_path: Path to the current state file
  - session_dir: Path to the session directory

It also injects helpers:
  - peek(start=0, end=1000) -> str
  - grep(pattern, max_matches=20, window=120, flags=0) -> str (handle stub)
  - grep_raw(pattern, ...) -> list[dict] (raw results, no handle)
  - chunk_indices(size=200000, overlap=0) -> list[(start,end)]
  - write_chunks(out_dir, size=200000, overlap=0, prefix='chunk') -> list[str]
  - add_buffer(text: str) -> None

Handle system (token-efficient result storage):
  - handles() -> str (list all active handles)
  - last_handle() -> str (get name of most recent handle for chaining)
  - expand(handle, limit=10, offset=0) -> list (materialize handle data)
  - count(handle) -> int (count items without expanding)
  - filter_handle(handle, pattern_or_fn) -> str (new handle with filtered results)
  - map_field(handle, field) -> str (extract single field from each item)
  - sum_field(handle, field) -> float (sum numeric field values)

Security note:
  This runs arbitrary Python via exec. Treat it like running code you wrote.
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
import textwrap
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


DEFAULT_RLM_STATE_DIR = Path(".pi/rlm_state")
DEFAULT_MAX_DEPTH = 3
DEFAULT_LLM_TIMEOUT = 120
DEFAULT_LLM_MODEL = "google/gemini-2.0-flash-lite"

# Global concurrency semaphore - limits concurrent sub-agent spawns to 5
_GLOBAL_CONCURRENCY_SEMAPHORE = threading.Semaphore(5)
PREVIEW_LENGTH = 80  # Characters to show in handle previews
MANIFEST_PREVIEW_LINES = 5  # Lines to include in chunk preview
DEFAULT_MAX_OUTPUT_CHARS = 8000


class RlmReplError(RuntimeError):
    pass


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _sanitize_session_name(filename: str) -> str:
    """Convert filename to a clean session name component."""
    # Remove extension
    name = Path(filename).stem
    # Lowercase, replace non-alphanumeric with hyphens
    name = re.sub(r'[^a-zA-Z0-9]+', '-', name.lower())
    # Remove leading/trailing hyphens
    name = name.strip('-')
    # Truncate to ~30 chars
    if len(name) > 30:
        name = name[:30].rstrip('-')
    return name or 'context'


def _create_session_path(context_path: Path) -> Path:
    """Generate a timestamped session directory path."""
    name = _sanitize_session_name(context_path.name)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    session_name = f"{name}-{timestamp}"
    session_dir = DEFAULT_RLM_STATE_DIR / session_name
    return session_dir / "state.pkl"


def _migrate_state_v2_to_v3(state: Dict[str, Any]) -> Dict[str, Any]:
    """Migrate state from version 2 to version 3.
    
    Adds depth tracking fields for recursive sub-agent support.
    """
    if state.get("version", 1) >= 3:
        return state
    
    state["version"] = 3
    state["max_depth"] = DEFAULT_MAX_DEPTH
    state["remaining_depth"] = DEFAULT_MAX_DEPTH
    state["preserve_recursive_state"] = False
    state["final_answer"] = None
    
    return state


def _load_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        raise RlmReplError(
            f"No state found at {state_path}. Run: python rlm_repl.py init <context_path>"
        )
    with state_path.open("rb") as f:
        state = pickle.load(f)
    if not isinstance(state, dict):
        raise RlmReplError(f"Corrupt state file: {state_path}")
    
    # Auto-migrate to v3 if needed
    state = _migrate_state_v2_to_v3(state)
    
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
    data: bytes
    with path.open("rb") as f:
        data = f.read() if max_bytes is None else f.read(max_bytes)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Fall back to a lossy decode that will not crash.
        return data.decode("utf-8", errors="replace")


def _truncate(s: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"\n... [truncated to {max_chars} chars] ...\n"


def _is_pickleable(value: Any) -> bool:
    try:
        pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        return True
    except Exception:
        return False


def _filter_pickleable(d: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    kept: Dict[str, Any] = {}
    dropped: List[str] = []
    for k, v in d.items():
        if _is_pickleable(v):
            kept[k] = v
        else:
            dropped.append(k)
    return kept, dropped


def _count_lines_in_range(content: str, start: int, end: int) -> Tuple[int, int]:
    """Count the starting and ending line numbers for a character range."""
    if not content:
        return (1, 1)
    
    # Count newlines before start
    start_line = content[:start].count('\n') + 1
    
    # Count newlines up to end
    end_line = content[:end].count('\n') + 1
    
    return (start_line, end_line)


# =============================================================================
# Semantic Chunking Infrastructure (Phase 4 + Phase 6)
# =============================================================================

# Codemap detection cache (None = not checked yet, False = unavailable)
_CODEMAP_CACHE: Optional[Union[str, bool]] = None


def _detect_codemap() -> Optional[str]:
    """Auto-detect codemap availability.
    
    Checks in order:
    1. RLM_CODEMAP_PATH environment variable (explicit path)
    2. npx codemap (via npx)
    3. codemap in PATH (system install)
    
    Returns:
        Command string to invoke codemap, or None if unavailable.
        Example: "/usr/local/bin/codemap" or "npx codemap"
    """
    global _CODEMAP_CACHE
    
    # Return cached result if available
    if _CODEMAP_CACHE is not None:
        return _CODEMAP_CACHE if _CODEMAP_CACHE else None
    
    # 1. Check RLM_CODEMAP_PATH env var
    env_path = os.environ.get('RLM_CODEMAP_PATH', '').strip()
    if env_path:
        if Path(env_path).exists():
            _CODEMAP_CACHE = env_path
            return env_path
        # Env var set but path doesn't exist - treat as explicit disable if empty string
        if env_path == '':
            _CODEMAP_CACHE = False
            return None
    
    # 2. Try codemap in PATH first (faster than npx)
    try:
        result = subprocess.run(
            ['codemap', '--version'],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            _CODEMAP_CACHE = 'codemap'
            return 'codemap'
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    
    # 3. Try npx codemap
    try:
        result = subprocess.run(
            ['npx', 'codemap', '--version'],
            capture_output=True,
            timeout=30,  # npx may need to download
        )
        if result.returncode == 0:
            _CODEMAP_CACHE = 'npx codemap'
            return 'npx codemap'
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    
    _CODEMAP_CACHE = False
    return None


def _extract_symbol_boundaries(
    codemap_output: str,
    context_path: str,
) -> List[Dict[str, Any]]:
    """Parse codemap JSON output to extract symbol boundaries.
    
    Args:
        codemap_output: JSON string from codemap -o json
        context_path: Path to the source file (for matching)
    
    Returns:
        List of symbol boundary dicts with keys:
        - name: symbol name
        - kind: symbol kind (function, class, method, etc.)
        - signature: full signature if available
        - start_line: 1-indexed start line
        - end_line: 1-indexed end line
        - exported: whether symbol is exported
    """
    try:
        data = json.loads(codemap_output)
    except json.JSONDecodeError:
        return []
    
    # Handle both array format and object with 'files' key
    if isinstance(data, list):
        files = data
    elif isinstance(data, dict) and 'files' in data:
        files = data['files']
    else:
        return []
    
    # Find the file matching our context
    context_name = Path(context_path).name
    context_resolved = Path(context_path).resolve() if Path(context_path).exists() else None
    
    symbols = []
    for file_entry in files:
        file_path = file_entry.get('path', '')
        
        # Match by name or resolved path
        if Path(file_path).name == context_name or \
           (context_resolved and Path(file_path).resolve() == context_resolved):
            for sym in file_entry.get('symbols', []):
                lines = sym.get('lines', [])
                if len(lines) >= 2:
                    symbols.append({
                        'name': sym.get('name', ''),
                        'kind': sym.get('kind', 'unknown'),
                        'signature': sym.get('signature', ''),
                        'start_line': lines[0],
                        'end_line': lines[1],
                        'exported': sym.get('exported', False),
                    })
            break  # Found our file
    
    # Sort by start_line
    symbols.sort(key=lambda s: s['start_line'])
    return symbols


def _line_to_char_position(content: str, line_num: int) -> int:
    """Convert 1-indexed line number to character position.
    
    Args:
        content: The file content.
        line_num: 1-indexed line number.
    
    Returns:
        Character position at the start of that line.
    """
    if line_num <= 1:
        return 0
    
    lines = content.split('\n')
    pos = 0
    for i in range(min(line_num - 1, len(lines))):
        pos += len(lines[i]) + 1  # +1 for newline
    return pos


def _chunk_code(
    content: str,
    context_path: str,
    target_size: int,
    min_size: int,
    max_size: int,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Split code content at function/class boundaries using codemap.
    
    Uses tree-sitter (via codemap) to identify symbol boundaries and
    splits code at natural function/class/method boundaries.
    
    Args:
        content: Source code content to chunk.
        context_path: Path to the source file (for codemap).
        target_size: Target chunk size in characters.
        min_size: Minimum chunk size.
        max_size: Maximum chunk size.
    
    Returns:
        Tuple of (chunk_metas, codemap_used) where:
        - chunk_metas: List of chunk metadata dicts
        - codemap_used: True if codemap was used, False if fell back to text
    """
    codemap_cmd = _detect_codemap()
    
    if not codemap_cmd:
        # Fall back to text chunking
        return _chunk_text(content, target_size, min_size, max_size), False
    
    # Resolve the context path for codemap
    context_resolved = Path(context_path).resolve()
    if not context_resolved.exists():
        return _chunk_text(content, target_size, min_size, max_size), False
    
    # Run codemap to get symbol boundaries
    try:
        # Build command
        cmd_parts = codemap_cmd.split()
        cmd = cmd_parts + ['-o', 'json', str(context_resolved)]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=context_resolved.parent,  # Run from file's directory
        )
        
        if result.returncode != 0:
            # Codemap failed - fall back to text chunking
            return _chunk_text(content, target_size, min_size, max_size), False
        
        symbols = _extract_symbol_boundaries(result.stdout, str(context_resolved))
        
        if not symbols:
            # No symbols found - fall back to text chunking
            return _chunk_text(content, target_size, min_size, max_size), False
        
    except (subprocess.TimeoutExpired, OSError, Exception):
        return _chunk_text(content, target_size, min_size, max_size), False
    
    # Build chunks from symbol boundaries
    # Strategy: Group symbols together until reaching target_size
    # Split at symbol boundaries to avoid breaking mid-function
    
    chunks = []
    current_chunk = {
        'start': 0,
        'end': 0,
        'split_reason': 'start',
        'boundaries': [],
    }
    
    # Convert line numbers to character positions for each symbol
    symbol_positions = []
    for sym in symbols:
        start_pos = _line_to_char_position(content, sym['start_line'])
        end_pos = _line_to_char_position(content, sym['end_line'] + 1)
        end_pos = min(end_pos, len(content))
        symbol_positions.append({
            **sym,
            'start_char': start_pos,
            'end_char': end_pos,
        })
    
    # Handle content before first symbol (imports, comments, etc.)
    if symbol_positions and symbol_positions[0]['start_char'] > 0:
        preamble_end = symbol_positions[0]['start_char']
        current_chunk['end'] = preamble_end
    
    for i, sym in enumerate(symbol_positions):
        sym_size = sym['end_char'] - sym['start_char']
        current_size = current_chunk['end'] - current_chunk['start']
        
        # If this is the first symbol and we have no content yet, start here
        if current_chunk['end'] == 0:
            current_chunk['end'] = sym['end_char']
            current_chunk['boundaries'].append({
                'type': sym['kind'],
                'name': sym['name'],
                'signature': sym['signature'],
                'line': sym['start_line'],
            })
            continue
        
        combined_size = current_size + sym_size
        
        # Decide whether to extend current chunk or start new one
        should_split = False
        split_reason = None
        
        if combined_size > max_size:
            # Hard limit - must split
            should_split = True
            split_reason = 'max_size'
        elif current_size >= target_size and sym['kind'] in ('function', 'class', 'method', 'impl'):
            # At target and hit a good boundary
            should_split = True
            split_reason = f"symbol_{sym['kind']}"
        elif current_size >= target_size * 1.2:
            # Well past target
            should_split = True
            split_reason = 'target_size'
        
        if should_split:
            # Finalize current chunk
            chunks.append(current_chunk)
            
            # Start new chunk at this symbol
            current_chunk = {
                'start': sym['start_char'],
                'end': sym['end_char'],
                'split_reason': split_reason,
                'boundaries': [{
                    'type': sym['kind'],
                    'name': sym['name'],
                    'signature': sym['signature'],
                    'line': sym['start_line'],
                }],
            }
        else:
            # Extend current chunk
            current_chunk['end'] = sym['end_char']
            current_chunk['boundaries'].append({
                'type': sym['kind'],
                'name': sym['name'],
                'signature': sym['signature'],
                'line': sym['start_line'],
            })
    
    # Handle content after last symbol
    if symbol_positions:
        last_sym_end = symbol_positions[-1]['end_char']
        if last_sym_end < len(content):
            current_chunk['end'] = len(content)
    else:
        current_chunk['end'] = len(content)
    
    # Add final chunk
    if current_chunk['end'] > current_chunk['start']:
        chunks.append(current_chunk)
    
    # Merge tiny trailing chunk if under min_size
    if len(chunks) > 1:
        last_chunk = chunks[-1]
        last_size = last_chunk['end'] - last_chunk['start']
        if last_size < min_size:
            prev_chunk = chunks[-2]
            prev_size = prev_chunk['end'] - prev_chunk['start']
            if prev_size + last_size <= max_size:
                # Merge last into previous
                prev_chunk['end'] = last_chunk['end']
                prev_chunk['boundaries'].extend(last_chunk['boundaries'])
                chunks.pop()
    
    # If chunking produced no valid chunks, fall back
    if not chunks:
        return _chunk_text(content, target_size, min_size, max_size), False
    
    return chunks, True


# File extensions mapped to format types
_CODE_EXTENSIONS = frozenset({
    '.py', '.pyi', '.pyw',  # Python
    '.js', '.jsx', '.mjs', '.cjs',  # JavaScript
    '.ts', '.tsx', '.mts', '.cts',  # TypeScript
    '.rs',  # Rust
    '.go',  # Go
    '.java',  # Java
    '.c', '.h', '.cc', '.cpp', '.cxx', '.hpp', '.hxx',  # C/C++
    '.cs',  # C#
    '.rb',  # Ruby
    '.php',  # PHP
    '.swift',  # Swift
    '.kt', '.kts',  # Kotlin
    '.scala',  # Scala
    '.lua',  # Lua
    '.sh', '.bash', '.zsh',  # Shell
    '.pl', '.pm',  # Perl
    '.r', '.R',  # R
    '.sql',  # SQL
})

_MARKDOWN_EXTENSIONS = frozenset({'.md', '.markdown', '.mdx', '.mdown', '.mkd'})


def _detect_format(content: str, context_path: str) -> str:
    """Detect content format from extension or content analysis.
    
    Args:
        content: The file content.
        context_path: Path to the context file for extension detection.
    
    Returns:
        One of: 'markdown', 'code', 'json', 'text'
    """
    ext = Path(context_path).suffix.lower()
    
    if ext in _MARKDOWN_EXTENSIONS:
        return 'markdown'
    if ext in _CODE_EXTENSIONS:
        return 'code'
    if ext == '.json':
        return 'json'
    if ext in {'.txt', '.text', '.log'}:
        return 'text'
    
    # Content-based fallback: check for markdown header density
    # Count ## or # headers in the content
    header_matches = re.findall(r'^#{1,6}\s+\S', content, re.MULTILINE)
    if len(header_matches) > 5:
        return 'markdown'
    
    return 'text'


def _find_header_boundaries(content: str) -> List[Tuple[int, int, int, str]]:
    """Find all markdown header positions in content.
    
    Returns:
        List of (start_pos, end_pos, level, header_text) tuples.
        start_pos is position of '#', end_pos is end of header line.
    """
    boundaries = []
    for match in re.finditer(r'^(#{1,6})\s+(.+?)$', content, re.MULTILINE):
        level = len(match.group(1))
        header_text = match.group(2).strip()
        boundaries.append((match.start(), match.end(), level, header_text))
    return boundaries


def _chunk_markdown(
    content: str,
    target_size: int,
    min_size: int,
    max_size: int,
) -> List[Dict[str, Any]]:
    """Split markdown content at header boundaries.
    
    Strategy:
    - Prefer splitting at h2 (##) or h3 (###) boundaries
    - Keep sections together until reaching target_size
    - Force split if max_size would be exceeded
    - Merge small trailing sections if under min_size
    
    Args:
        content: Markdown content to chunk.
        target_size: Target chunk size in characters (soft limit).
        min_size: Minimum chunk size (avoids tiny chunks).
        max_size: Maximum chunk size (hard limit).
    
    Returns:
        List of chunk metadata dicts with keys:
        - start: start character position
        - end: end character position
        - split_reason: why split occurred here
        - boundaries: list of headers in this chunk
    """
    headers = _find_header_boundaries(content)
    
    if not headers:
        # No headers - fall back to text chunking
        return _chunk_text(content, target_size, min_size, max_size)
    
    # Build list of section boundaries (each header starts a new section)
    # Sections run from one header to the next (or end of content)
    sections = []
    for i, (start, end, level, text) in enumerate(headers):
        next_start = headers[i + 1][0] if i + 1 < len(headers) else len(content)
        sections.append({
            'start': start,
            'end': next_start,
            'level': level,
            'header_text': text,
            'header_line': content[:start].count('\n') + 1,
        })
    
    # Handle content before first header (preamble)
    if headers[0][0] > 0:
        sections.insert(0, {
            'start': 0,
            'end': headers[0][0],
            'level': 0,
            'header_text': '(preamble)',
            'header_line': 1,
        })
    
    # Combine sections into chunks
    chunks = []
    current_chunk = {
        'start': sections[0]['start'],
        'end': sections[0]['end'],
        'split_reason': 'start',
        'boundaries': [],
    }
    
    if sections[0]['level'] > 0:
        current_chunk['boundaries'].append({
            'type': 'heading',
            'level': sections[0]['level'],
            'text': sections[0]['header_text'],
            'line': sections[0]['header_line'],
        })
    
    for section in sections[1:]:
        section_size = section['end'] - section['start']
        current_size = current_chunk['end'] - current_chunk['start']
        combined_size = current_size + section_size
        
        # Decide whether to extend current chunk or start new one
        should_split = False
        split_reason = None
        
        if combined_size > max_size:
            # Hard limit - must split
            should_split = True
            split_reason = 'max_size'
        elif current_size >= target_size and section['level'] <= 3:
            # At target and hit a good boundary (h1, h2, h3)
            should_split = True
            split_reason = f"header_level_{section['level']}"
        elif current_size >= target_size and combined_size > target_size * 1.5:
            # Well past target and adding this would go way over
            should_split = True
            split_reason = 'target_size'
        
        if should_split:
            # Finalize current chunk
            chunks.append(current_chunk)
            
            # Start new chunk
            current_chunk = {
                'start': section['start'],
                'end': section['end'],
                'split_reason': split_reason,
                'boundaries': [],
            }
        else:
            # Extend current chunk
            current_chunk['end'] = section['end']
        
        # Record header boundary in current chunk
        if section['level'] > 0:
            current_chunk['boundaries'].append({
                'type': 'heading',
                'level': section['level'],
                'text': section['header_text'],
                'line': section['header_line'],
            })
    
    # Add final chunk
    chunks.append(current_chunk)
    
    # Merge tiny trailing chunk if under min_size
    if len(chunks) > 1:
        last_chunk = chunks[-1]
        last_size = last_chunk['end'] - last_chunk['start']
        if last_size < min_size:
            prev_chunk = chunks[-2]
            prev_size = prev_chunk['end'] - prev_chunk['start']
            if prev_size + last_size <= max_size:
                # Merge last into previous
                prev_chunk['end'] = last_chunk['end']
                prev_chunk['boundaries'].extend(last_chunk['boundaries'])
                chunks.pop()
    
    # Post-process: split any chunk that exceeds max_size using text chunking
    final_chunks = []
    for chunk in chunks:
        chunk_size = chunk['end'] - chunk['start']
        if chunk_size > max_size:
            # This section is too big - split it using text chunking
            chunk_content = content[chunk['start']:chunk['end']]
            sub_chunks = _chunk_text(chunk_content, target_size, min_size, max_size)
            for i, sub in enumerate(sub_chunks):
                final_chunks.append({
                    'start': chunk['start'] + sub['start'],
                    'end': chunk['start'] + sub['end'],
                    'split_reason': 'oversized_section' if i > 0 else chunk['split_reason'],
                    'boundaries': chunk['boundaries'] if i == 0 else [],
                })
        else:
            final_chunks.append(chunk)
    
    return final_chunks


def _chunk_text(
    content: str,
    target_size: int,
    min_size: int,
    max_size: int,
) -> List[Dict[str, Any]]:
    """Split plain text content at paragraph boundaries.
    
    Strategy:
    - Prefer splitting at double-newline (paragraph break)
    - Fall back to single newline if no paragraphs
    - Fall back to space if no newlines
    - Hard split if no breaks found within max_size
    
    Args:
        content: Text content to chunk.
        target_size: Target chunk size in characters.
        min_size: Minimum chunk size.
        max_size: Maximum chunk size (hard limit).
    
    Returns:
        List of chunk metadata dicts with keys:
        - start: start character position
        - end: end character position
        - split_reason: why split occurred here
        - boundaries: always empty for text chunks
    """
    if len(content) <= max_size:
        return [{
            'start': 0,
            'end': len(content),
            'split_reason': 'single_chunk',
            'boundaries': [],
        }]
    
    chunks = []
    pos = 0
    
    while pos < len(content):
        remaining = len(content) - pos
        
        if remaining <= max_size:
            # Last chunk - take it all
            chunks.append({
                'start': pos,
                'end': len(content),
                'split_reason': 'end' if chunks else 'single_chunk',
                'boundaries': [],
            })
            break
        
        # Look for a good break point between target_size and max_size
        search_start = pos + min(target_size, remaining)
        search_end = pos + min(max_size, remaining)
        search_region = content[search_start:search_end]
        
        split_pos = None
        split_reason = None
        
        # Try paragraph break (double newline)
        para_match = re.search(r'\n\n+', search_region)
        if para_match:
            split_pos = search_start + para_match.end()
            split_reason = 'paragraph'
        else:
            # Try line break
            line_match = re.search(r'\n', search_region)
            if line_match:
                split_pos = search_start + line_match.end()
                split_reason = 'line'
            else:
                # Try space
                space_match = re.search(r'\s', search_region)
                if space_match:
                    split_pos = search_start + space_match.end()
                    split_reason = 'word'
                else:
                    # Hard split at max_size
                    split_pos = pos + max_size
                    split_reason = 'hard_split'
        
        chunks.append({
            'start': pos,
            'end': split_pos,
            'split_reason': 'start' if not chunks else split_reason,
            'boundaries': [],
        })
        pos = split_pos
    
    return chunks


# =============================================================================
# JSON Semantic Chunking (Phase 7)
# =============================================================================

def _chunk_json_array(
    content: str,
    target_size: int,
    min_size: int,
    max_size: int,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Split JSON array content into element groups.
    
    Strategy:
    - Parse JSON to identify array elements
    - Group elements together to meet target_size
    - Each chunk is re-serialized as valid JSON array
    - Manifest includes element indices (not char positions)
    
    Args:
        content: JSON array content to chunk.
        target_size: Target chunk size in characters.
        min_size: Minimum chunk size.
        max_size: Maximum chunk size (hard limit).
    
    Returns:
        Tuple of (chunk_metas, success) where:
        - chunk_metas: List of chunk metadata dicts with special fields:
            - start: start character position (always 0 for serialized chunks)
            - end: end character position
            - split_reason: why split occurred
            - boundaries: empty for JSON
            - element_range: [start_idx, end_idx] for array elements
            - json_content: re-serialized JSON chunk content
        - success: True if JSON parsing succeeded, False otherwise
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [], False
    
    if not isinstance(data, list):
        return [], False
    
    if len(data) == 0:
        # Empty array - single chunk
        return [{
            'start': 0,
            'end': len(content),
            'split_reason': 'single_chunk',
            'boundaries': [],
            'element_range': [0, 0],
            'json_content': content,
        }], True
    
    # If content is small enough, return as single chunk
    if len(content) <= max_size:
        return [{
            'start': 0,
            'end': len(content),
            'split_reason': 'single_chunk',
            'boundaries': [],
            'element_range': [0, len(data)],
            'json_content': content,
        }], True
    
    # Estimate elements per chunk based on average element size
    # Serialize each element to estimate sizes
    element_sizes = []
    for elem in data:
        elem_json = json.dumps(elem, separators=(',', ':'))
        element_sizes.append(len(elem_json))
    
    total_size = sum(element_sizes)
    avg_element_size = total_size / len(data) if data else 0
    
    # Account for array brackets and commas
    overhead_per_element = 1  # comma
    overhead_fixed = 2  # [ and ]
    
    # Estimate elements per chunk
    if avg_element_size + overhead_per_element > 0:
        elements_per_chunk = max(1, int(
            (target_size - overhead_fixed) / (avg_element_size + overhead_per_element)
        ))
    else:
        elements_per_chunk = len(data)
    
    chunks = []
    i = 0
    
    while i < len(data):
        # Start with estimated elements_per_chunk
        chunk_end = min(i + elements_per_chunk, len(data))
        
        # Serialize chunk to check size
        chunk_data = data[i:chunk_end]
        chunk_json = json.dumps(chunk_data, indent=2)
        
        # Adjust if too large
        while len(chunk_json) > max_size and chunk_end > i + 1:
            chunk_end -= 1
            chunk_data = data[i:chunk_end]
            chunk_json = json.dumps(chunk_data, indent=2)
        
        # Adjust if too small and can add more
        while chunk_end < len(data) and len(chunk_json) < target_size:
            test_end = chunk_end + 1
            test_data = data[i:test_end]
            test_json = json.dumps(test_data, indent=2)
            if len(test_json) <= max_size:
                chunk_end = test_end
                chunk_data = test_data
                chunk_json = test_json
            else:
                break
        
        # Determine split reason
        if not chunks:
            split_reason = 'start'
        elif chunk_end >= len(data):
            split_reason = 'end'
        else:
            split_reason = 'element_boundary'
        
        chunks.append({
            'start': 0,  # Not used for JSON - we use json_content
            'end': len(chunk_json),
            'split_reason': split_reason,
            'boundaries': [],
            'element_range': [i, chunk_end],
            'json_content': chunk_json,
        })
        
        i = chunk_end
    
    # Merge tiny trailing chunk if under min_size
    if len(chunks) > 1:
        last = chunks[-1]
        last_size = len(last['json_content'])
        if last_size < min_size:
            prev = chunks[-2]
            # Merge elements and re-serialize
            combined_start = prev['element_range'][0]
            combined_end = last['element_range'][1]
            combined_data = data[combined_start:combined_end]
            combined_json = json.dumps(combined_data, indent=2)
            
            if len(combined_json) <= max_size:
                prev['element_range'] = [combined_start, combined_end]
                prev['json_content'] = combined_json
                prev['end'] = len(combined_json)
                chunks.pop()
    
    return chunks, True


def _chunk_json_object(
    content: str,
    target_size: int,
    min_size: int,
    max_size: int,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Split JSON object content by top-level keys.
    
    Strategy:
    - Parse JSON to identify top-level keys
    - Group keys together to meet target_size
    - Each chunk is re-serialized as valid JSON object
    - Manifest includes key list for each chunk
    
    Args:
        content: JSON object content to chunk.
        target_size: Target chunk size in characters.
        min_size: Minimum chunk size.
        max_size: Maximum chunk size (hard limit).
    
    Returns:
        Tuple of (chunk_metas, success) where:
        - chunk_metas: List of chunk metadata dicts with special fields:
            - start: start character position (always 0 for serialized chunks)
            - end: end character position
            - split_reason: why split occurred
            - boundaries: empty for JSON
            - key_range: [start_idx, end_idx] for key indices
            - keys: list of key names in this chunk
            - json_content: re-serialized JSON chunk content
        - success: True if JSON parsing succeeded, False otherwise
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [], False
    
    if not isinstance(data, dict):
        return [], False
    
    if len(data) == 0:
        # Empty object - single chunk
        return [{
            'start': 0,
            'end': len(content),
            'split_reason': 'single_chunk',
            'boundaries': [],
            'key_range': [0, 0],
            'keys': [],
            'json_content': content,
        }], True
    
    # If content is small enough, return as single chunk
    if len(content) <= max_size:
        keys = list(data.keys())
        return [{
            'start': 0,
            'end': len(content),
            'split_reason': 'single_chunk',
            'boundaries': [],
            'key_range': [0, len(keys)],
            'keys': keys,
            'json_content': content,
        }], True
    
    keys = list(data.keys())
    
    # Estimate key-value pair sizes
    kv_sizes = []
    for k in keys:
        kv_json = json.dumps({k: data[k]}, separators=(',', ':'))
        # Subtract 2 for outer braces, this gives us the key-value size
        kv_sizes.append(len(kv_json) - 2)
    
    total_size = sum(kv_sizes)
    avg_kv_size = total_size / len(keys) if keys else 0
    
    # Estimate keys per chunk
    overhead_fixed = 2  # { and }
    overhead_per_kv = 1  # comma
    
    if avg_kv_size + overhead_per_kv > 0:
        keys_per_chunk = max(1, int(
            (target_size - overhead_fixed) / (avg_kv_size + overhead_per_kv)
        ))
    else:
        keys_per_chunk = len(keys)
    
    chunks = []
    i = 0
    
    while i < len(keys):
        # Start with estimated keys_per_chunk
        chunk_end = min(i + keys_per_chunk, len(keys))
        
        # Build chunk dict and serialize
        chunk_keys = keys[i:chunk_end]
        chunk_data = {k: data[k] for k in chunk_keys}
        chunk_json = json.dumps(chunk_data, indent=2)
        
        # Adjust if too large
        while len(chunk_json) > max_size and chunk_end > i + 1:
            chunk_end -= 1
            chunk_keys = keys[i:chunk_end]
            chunk_data = {k: data[k] for k in chunk_keys}
            chunk_json = json.dumps(chunk_data, indent=2)
        
        # Adjust if too small and can add more
        while chunk_end < len(keys) and len(chunk_json) < target_size:
            test_end = chunk_end + 1
            test_keys = keys[i:test_end]
            test_data = {k: data[k] for k in test_keys}
            test_json = json.dumps(test_data, indent=2)
            if len(test_json) <= max_size:
                chunk_end = test_end
                chunk_keys = test_keys
                chunk_data = test_data
                chunk_json = test_json
            else:
                break
        
        # Determine split reason
        if not chunks:
            split_reason = 'start'
        elif chunk_end >= len(keys):
            split_reason = 'end'
        else:
            split_reason = 'key_boundary'
        
        chunks.append({
            'start': 0,  # Not used for JSON - we use json_content
            'end': len(chunk_json),
            'split_reason': split_reason,
            'boundaries': [],
            'key_range': [i, chunk_end],
            'keys': chunk_keys,
            'json_content': chunk_json,
        })
        
        i = chunk_end
    
    # Merge tiny trailing chunk if under min_size
    if len(chunks) > 1:
        last = chunks[-1]
        last_size = len(last['json_content'])
        if last_size < min_size:
            prev = chunks[-2]
            # Merge keys and re-serialize
            combined_start = prev['key_range'][0]
            combined_end = last['key_range'][1]
            combined_keys = keys[combined_start:combined_end]
            combined_data = {k: data[k] for k in combined_keys}
            combined_json = json.dumps(combined_data, indent=2)
            
            if len(combined_json) <= max_size:
                prev['key_range'] = [combined_start, combined_end]
                prev['keys'] = combined_keys
                prev['json_content'] = combined_json
                prev['end'] = len(combined_json)
                chunks.pop()
    
    return chunks, True


def _chunk_json(
    content: str,
    target_size: int,
    min_size: int,
    max_size: int,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Split JSON content at structural boundaries.
    
    Detects whether content is an array or object and delegates
    to the appropriate chunking function.
    
    Args:
        content: JSON content to chunk.
        target_size: Target chunk size in characters.
        min_size: Minimum chunk size.
        max_size: Maximum chunk size (hard limit).
    
    Returns:
        Tuple of (chunk_metas, success) where:
        - chunk_metas: List of chunk metadata dicts
        - success: True if JSON parsing succeeded, False otherwise
    """
    # Handle minified JSON - try to detect structure from first non-whitespace char
    stripped = content.strip()
    
    if not stripped:
        return [], False
    
    if stripped.startswith('['):
        return _chunk_json_array(content, target_size, min_size, max_size)
    elif stripped.startswith('{'):
        return _chunk_json_object(content, target_size, min_size, max_size)
    else:
        return [], False


def _smart_chunk_impl(
    content: str,
    context_path: str,
    out_dir: Path,
    target_size: int = 200_000,
    min_size: int = 50_000,
    max_size: int = 400_000,
    encoding: str = "utf-8",
) -> Tuple[List[str], Dict[str, Any]]:
    """Core implementation of smart_chunk for testing.
    
    Args:
        content: Content to chunk.
        context_path: Original file path (for format detection).
        out_dir: Output directory for chunks.
        target_size: Target chars per chunk.
        min_size: Minimum chunk size.
        max_size: Maximum chunk size (hard limit).
        encoding: File encoding for output.
    
    Returns:
        Tuple of (chunk_file_paths, manifest_dict).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Detect format
    format_type = _detect_format(content, context_path)
    
    # Choose chunking strategy
    # Phase 6: Track whether codemap was used for manifest
    # Phase 7: Track whether JSON chunking was used
    codemap_used = False
    json_chunked = False
    
    if format_type == 'markdown':
        chunk_metas = _chunk_markdown(content, target_size, min_size, max_size)
        chunking_method = 'smart_markdown'
    elif format_type == 'code':
        # Phase 6: Use codemap for code chunking when available
        chunk_metas, codemap_used = _chunk_code(
            content, context_path, target_size, min_size, max_size
        )
        chunking_method = 'smart_code' if codemap_used else 'smart_text'
    elif format_type == 'json':
        # Phase 7: Use JSON-aware chunking for JSON files
        chunk_metas, json_chunked = _chunk_json(
            content, target_size, min_size, max_size
        )
        if not json_chunked:
            # Fall back to text chunking if JSON parsing fails
            chunk_metas = _chunk_text(content, target_size, min_size, max_size)
        chunking_method = 'smart_json' if json_chunked else 'smart_text'
    else:
        # For text - use text chunking
        chunk_metas = _chunk_text(content, target_size, min_size, max_size)
        chunking_method = 'smart_text'
    
    # Write chunks and build manifest
    paths = []
    manifest_chunks = []
    
    for i, meta in enumerate(chunk_metas):
        chunk_id = f"chunk_{i:04d}"
        # Phase 7: Use .json extension for JSON chunks
        chunk_file = f"{chunk_id}.json" if json_chunked else f"{chunk_id}.txt"
        chunk_path = out_dir / chunk_file
        
        # Phase 7: JSON chunks have pre-serialized content in json_content field
        if 'json_content' in meta:
            chunk_text = meta['json_content']
        else:
            chunk_text = content[meta['start']:meta['end']]
        
        chunk_path.write_text(chunk_text, encoding=encoding)
        paths.append(str(chunk_path))
        
        # For JSON chunks, count lines in the serialized content
        if 'json_content' in meta:
            start_line = 1
            end_line = chunk_text.count('\n') + 1
        else:
            start_line, end_line = _count_lines_in_range(content, meta['start'], meta['end'])
        
        chunk_entry = {
            'id': chunk_id,
            'file': chunk_file,
            'start_char': meta['start'],
            'end_char': meta['end'],
            'start_line': start_line,
            'end_line': end_line,
            'split_reason': meta['split_reason'],
            'format': format_type,
        }
        
        # Phase 7: Add element_range for JSON arrays, key info for JSON objects
        if 'element_range' in meta:
            chunk_entry['element_range'] = meta['element_range']
        if 'key_range' in meta:
            chunk_entry['key_range'] = meta['key_range']
        if 'keys' in meta:
            chunk_entry['keys'] = meta['keys']
        
        # Add boundaries if present
        if meta.get('boundaries'):
            chunk_entry['boundaries'] = meta['boundaries']
        
        # Add preview and hints
        chunk_entry['preview'] = _generate_chunk_preview(chunk_text)
        hints = _generate_chunk_hints(chunk_text)
        if hints:
            chunk_entry['hints'] = hints
        
        manifest_chunks.append(chunk_entry)
    
    # Build manifest
    manifest = {
        'context_file': context_path,
        'format': format_type,
        'chunking_method': chunking_method,
        'codemap_available': _detect_codemap() is not None,
        'codemap_used': codemap_used,
        'json_chunked': json_chunked,  # Phase 7: Track JSON chunking
        'target_size': target_size,
        'min_size': min_size,
        'max_size': max_size,
        'total_chars': len(content),
        'total_lines': content.count('\n') + 1,
        'chunk_count': len(manifest_chunks),
        'chunks': manifest_chunks,
    }
    
    # Write manifest
    manifest_path = out_dir / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding=encoding)
    
    return paths, manifest


# =============================================================================
# LLM Query Infrastructure (Phase 1)
# =============================================================================

def _parse_pi_json_output(output: str) -> str:
    """Extract final assistant text from pi --mode json output.
    
    The output is streaming JSONL. We look for the final message_end event
    with role="assistant" and extract the text content.
    
    Returns:
        The extracted text, or empty string if not found.
    """
    lines = output.strip().split('\n')
    
    # Look for message_end with role=assistant from the end
    for line in reversed(lines):
        try:
            event = json.loads(line)
            if event.get('type') == 'message_end':
                message = event.get('message', {})
                if message.get('role') == 'assistant':
                    content = message.get('content', [])
                    texts = [
                        c['text'] 
                        for c in content 
                        if c.get('type') == 'text' and c.get('text')
                    ]
                    return '\n'.join(texts)
        except json.JSONDecodeError:
            continue
    
    return ""


def _log_query(session_dir: Path, entry: Dict[str, Any]) -> None:
    """Append a query log entry to llm_queries.jsonl.
    
    Adds timestamp if not present.
    """
    if "timestamp" not in entry:
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    
    log_file = session_dir / "llm_queries.jsonl"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _spawn_sub_agent(
    prompt: str,
    remaining_depth: int,
    session_dir: Path,
    cleanup: bool = True,
    model: str = DEFAULT_LLM_MODEL,
    timeout: int = DEFAULT_LLM_TIMEOUT,
) -> str:
    """Spawn a full pi subprocess for a sub-query.
    
    Args:
        prompt: The prompt to send to the sub-agent.
        remaining_depth: Current remaining recursion depth. If 0, fails fast.
        session_dir: Parent session directory for state management.
        cleanup: If True, remove sub-session directory after completion.
        model: Model to use for the sub-agent.
        timeout: Timeout in seconds for the subprocess.
    
    Returns:
        The text response from the sub-agent, or an error string on failure.
    """
    query_id = f"q_{uuid.uuid4().hex[:8]}"
    start_time = time.time()
    
    # Calculate depth level for directory naming
    # remaining_depth=3 means we're at depth level 0 (root)
    # remaining_depth=2 means we're at depth level 1, etc.
    depth_level = remaining_depth
    
    # Create sub-session directory
    sub_session_dir = session_dir / f"depth-{depth_level}" / query_id
    sub_session_dir.mkdir(parents=True, exist_ok=True)
    
    # Check depth limit BEFORE spawning
    if remaining_depth <= 0:
        error_msg = "[ERROR: Recursion depth limit reached. Process without sub-queries.]"
        _log_query(session_dir, {
            "query_id": query_id,
            "depth_level": depth_level,
            "remaining_depth": remaining_depth,
            "prompt_preview": prompt[:200] if prompt else "",
            "prompt_chars": len(prompt),
            "sub_state_dir": str(sub_session_dir),
            "response_preview": error_msg[:200],
            "response_chars": len(error_msg),
            "duration_ms": int((time.time() - start_time) * 1000),
            "status": "depth_exceeded",
            "cleanup": cleanup,
        })
        if cleanup and sub_session_dir.exists():
            shutil.rmtree(sub_session_dir, ignore_errors=True)
        return error_msg
    
    # Write prompt to file
    prompt_file = sub_session_dir / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    
    # Build pi command
    # Inject RLM_STATE_DIR and RLM_REMAINING_DEPTH via --append-system-prompt
    system_append = f"RLM_STATE_DIR={sub_session_dir} RLM_REMAINING_DEPTH={remaining_depth - 1}"
    
    cmd = [
        "pi",
        "--mode", "json",
        "-p",  # Prompt mode (non-interactive)
        "--no-session",
        "--model", model,
        "--append-system-prompt", system_append,
    ]
    
    response = ""
    status = "success"
    
    try:
        with prompt_file.open("r", encoding="utf-8") as f:
            result = subprocess.run(
                cmd,
                stdin=f,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        
        if result.returncode != 0:
            stderr_preview = result.stderr[:500] if result.stderr else "Unknown error"
            response = f"[ERROR: Sub-agent failed with exit code {result.returncode}: {stderr_preview}]"
            status = "failed"
        else:
            response = _parse_pi_json_output(result.stdout)
            if not response:
                response = "[ERROR: Failed to parse sub-agent response]"
                status = "parse_error"
    
    except subprocess.TimeoutExpired:
        response = f"[ERROR: Sub-agent timed out after {timeout}s]"
        status = "timeout"
    except Exception as e:
        response = f"[ERROR: Sub-agent exception: {str(e)[:200]}]"
        status = "exception"
    
    duration_ms = int((time.time() - start_time) * 1000)
    
    # Log the query
    _log_query(session_dir, {
        "query_id": query_id,
        "depth_level": depth_level,
        "remaining_depth": remaining_depth,
        "prompt_preview": prompt[:200] if prompt else "",
        "prompt_chars": len(prompt),
        "sub_state_dir": str(sub_session_dir),
        "response_preview": response[:200] if response else "",
        "response_chars": len(response),
        "duration_ms": duration_ms,
        "status": status,
        "cleanup": cleanup,
    })
    
    # Cleanup sub-session directory if requested and successful
    if cleanup and sub_session_dir.exists():
        shutil.rmtree(sub_session_dir, ignore_errors=True)
        # Also clean up parent depth directory if empty
        depth_dir = sub_session_dir.parent
        if depth_dir.exists() and not any(depth_dir.iterdir()):
            depth_dir.rmdir()
    
    return response




def _llm_query_batch_impl(
    prompts: List[str],
    remaining_depth: int,
    session_dir: Path,
    cleanup: bool = True,
    concurrency: int = 5,
    max_retries: int = 3,
) -> Tuple[List[str], Dict[int, Dict[str, Any]]]:
    """Core implementation of batch LLM queries.
    
    This is the module-level implementation used by llm_query_batch() in the
    REPL environment. It can be tested directly with mocks.
    
    Args:
        prompts: List of prompts to send to sub-agents.
        remaining_depth: Current remaining recursion depth.
        session_dir: Parent session directory for state management.
        cleanup: If True (default), remove sub-session state after completion.
        concurrency: Max concurrent queries (capped at 5 by global limit).
        max_retries: Number of retry attempts for failed queries.
    
    Returns:
        Tuple of (results, failures) - see llm_query_batch docstring.
    """
    # Generate batch ID for logging
    batch_id = f"batch_{uuid.uuid4().hex[:8]}"
    batch_size = len(prompts)
    
    # Cap concurrency at global limit (5)
    effective_concurrency = min(concurrency, 5)
    
    # Results placeholder - maintains order
    results: List[Optional[str]] = [None] * batch_size
    failures: Dict[int, Dict[str, Any]] = {}
    
    def execute_with_retry(index: int, prompt: str) -> Tuple[int, str, Optional[Dict]]:
        """Execute a single query with retry logic."""
        last_error = ""
        
        for attempt in range(1, max_retries + 1):
            # Exponential backoff: 1s, 2s, 4s (only after first attempt)
            if attempt > 1:
                backoff = 2 ** (attempt - 2)  # 1, 2, 4 for attempts 2, 3, 4
                time.sleep(backoff)
            
            # Use global semaphore for actual spawn
            with _GLOBAL_CONCURRENCY_SEMAPHORE:
                response = _spawn_sub_agent(
                    prompt=prompt,
                    remaining_depth=remaining_depth,
                    session_dir=session_dir,
                    cleanup=cleanup,
                )
            
            # Log with batch info
            _log_query(session_dir, {
                "batch_id": batch_id,
                "batch_index": index,
                "batch_size": batch_size,
                "attempt": attempt,
                "prompt_preview": prompt[:200] if prompt else "",
                "response_preview": response[:200] if response else "",
                "status": "error" if response.startswith("[ERROR:") else "success",
            })
            
            # Check if successful (not an error response)
            if not response.startswith("[ERROR:"):
                return (index, response, None)
            
            last_error = response
        
        # All retries exhausted
        failure_info = {
            "reason": "max_retries_exhausted",
            "attempts": max_retries,
            "error": last_error,
        }
        return (index, last_error, failure_info)
    
    # Execute in parallel using ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
        futures = [
            executor.submit(execute_with_retry, i, prompt)
            for i, prompt in enumerate(prompts)
        ]
        
        for future in as_completed(futures):
            index, response, failure_info = future.result()
            results[index] = response
            if failure_info is not None:
                failures[index] = failure_info
    
    # Convert Optional list to str list (all should be filled)
    return ([r if r is not None else "[ERROR: Unexpected None result]" for r in results], failures)


def _generate_chunk_hints(chunk_text: str) -> Dict[str, Any]:
    """Generate content hints for a chunk to help main agent understand it."""
    hints: Dict[str, Any] = {}
    
    lines = chunk_text.split('\n')
    
    # Detect section headers (markdown style)
    headers = []
    for line in lines[:100]:  # Check first 100 lines
        stripped = line.strip()
        if stripped.startswith('#') and len(stripped) > 1:
            headers.append(stripped[:80])
        elif stripped.startswith('##'):
            headers.append(stripped[:80])
    if headers:
        hints["section_headers"] = headers[:5]  # First 5 headers
    
    # Detect code blocks
    code_block_count = chunk_text.count('```')
    if code_block_count >= 2:
        hints["has_code_blocks"] = True
        hints["code_block_count"] = code_block_count // 2
    
    # Detect if mostly code (heuristic: high density of common code chars)
    code_chars = sum(1 for c in chunk_text if c in '{}();[]<>=')
    if len(chunk_text) > 0:
        code_density = code_chars / len(chunk_text)
        if code_density > 0.02:
            hints["likely_code"] = True
    
    # Detect JSON
    stripped = chunk_text.strip()
    if (stripped.startswith('{') and stripped.endswith('}')) or \
       (stripped.startswith('[') and stripped.endswith(']')):
        hints["likely_json"] = True
    
    # Content density classification
    non_empty_lines = sum(1 for line in lines if line.strip())
    if len(lines) > 0:
        density = non_empty_lines / len(lines)
        if density > 0.8:
            hints["density"] = "dense"
        elif density < 0.4:
            hints["density"] = "sparse"
        else:
            hints["density"] = "normal"
    
    return hints


def _generate_chunk_preview(chunk_text: str, max_lines: int = MANIFEST_PREVIEW_LINES) -> str:
    """Generate a preview of the chunk's beginning."""
    lines = chunk_text.split('\n')[:max_lines]
    preview = '\n'.join(lines)
    if len(chunk_text.split('\n')) > max_lines:
        preview += '\n...'
    return preview


def _make_handle_stub(handle: str, data: List[Any]) -> str:
    """Create a compact stub representation for a handle."""
    if not data:
        return f"{handle}: Array(0) []"
    
    # Get preview from first item
    first = data[0]
    preview = ""
    if isinstance(first, dict):
        # For grep results, show snippet or line
        if "snippet" in first:
            preview = first["snippet"][:PREVIEW_LENGTH]
        elif "line" in first:
            preview = first["line"][:PREVIEW_LENGTH]
        elif "match" in first:
            preview = first["match"][:PREVIEW_LENGTH]
        else:
            # Show first key-value pair
            for k, v in first.items():
                preview = f"{k}: {str(v)[:40]}"
                break
    else:
        preview = str(first)[:PREVIEW_LENGTH]
    
    # Clean up preview (remove newlines, excess whitespace)
    preview = ' '.join(preview.split())
    if len(preview) > PREVIEW_LENGTH:
        preview = preview[:PREVIEW_LENGTH-3] + "..."
    
    return f"{handle}: Array({len(data)}) [{preview}]"


def _make_helpers(context_ref: Dict[str, Any], buffers_ref: List[str], state_ref: Dict[str, Any], state_path_ref: Path):
    # Ensure handles dict exists in state
    if "handles" not in state_ref:
        state_ref["handles"] = {}
    if "handle_counter" not in state_ref:
        state_ref["handle_counter"] = 0
    
    handles_ref = state_ref["handles"]
    
    def _store_handle(data: List[Any]) -> str:
        """Internal: store data and return handle stub."""
        state_ref["handle_counter"] += 1
        handle = f"$res{state_ref['handle_counter']}"
        handles_ref[handle] = data
        return _make_handle_stub(handle, data)
    
    def _parse_handle(handle_input: str) -> str:
        """Parse handle name from either '$res1' or '$res1: Array(20) [...]' format.
        
        This allows users to pass the full grep() return value directly to
        count(), expand(), etc. without needing to call last_handle().
        """
        if not handle_input:
            raise ValueError("Empty handle")
        
        # Already a clean handle name
        if handle_input.startswith('$res') and ':' not in handle_input:
            return handle_input
        
        # Parse from full stub format: "$res1: Array(20) [preview...]"
        match = re.match(r'(\$res\d+):', handle_input)
        if match:
            return match.group(1)
        
        # Fallback: return as-is (will fail validation in caller)
        return handle_input
    
    # These close over context_ref/buffers_ref so changes persist.
    def peek(start: int = 0, end: int = 1000) -> str:
        content = context_ref.get("content", "")
        return content[start:end]

    def grep_raw(
        pattern: str,
        max_matches: int = 20,
        window: int = 120,
        flags: int = 0,
    ) -> List[Dict[str, Any]]:
        """Search content and return raw results (no handle)."""
        content = context_ref.get("content", "")
        out: List[Dict[str, Any]] = []
        for m in re.finditer(pattern, content, flags):
            start, end = m.span()
            snippet_start = max(0, start - window)
            snippet_end = min(len(content), end + window)
            # Calculate line number
            line_num = content[:start].count('\n') + 1
            out.append(
                {
                    "match": m.group(0),
                    "span": (start, end),
                    "line_num": line_num,
                    "snippet": content[snippet_start:snippet_end],
                }
            )
            if len(out) >= max_matches:
                break
        return out

    def grep(
        pattern: str,
        max_matches: int = 20,
        window: int = 120,
        flags: int = 0,
    ) -> str:
        """Search content and return handle stub (token-efficient)."""
        results = grep_raw(pattern, max_matches, window, flags)
        return _store_handle(results)
    
    # === Handle System ===
    
    def handles() -> str:
        """List all active handles with their sizes."""
        if not handles_ref:
            return "No active handles."
        lines = []
        for h in sorted(handles_ref.keys(), key=lambda x: int(x.replace('$res', ''))):
            data = handles_ref[h]
            lines.append(f"  {h}: Array({len(data)})")
        return "Active handles:\n" + "\n".join(lines)
    
    def last_handle() -> str:
        """Return the name of the most recently created handle (for chaining)."""
        if state_ref["handle_counter"] == 0:
            raise ValueError("No handles created yet")
        return f"$res{state_ref['handle_counter']}"
    
    def expand(handle: str, limit: int = 10, offset: int = 0) -> List[Any]:
        """Expand a handle to see its data (with optional pagination).
        
        Args:
            handle: Handle name ('$res1') or full stub ('$res1: Array(20) [...]')
            limit: Max items to return
            offset: Start index for pagination
        """
        handle = _parse_handle(handle)
        if handle not in handles_ref:
            raise ValueError(f"Unknown handle: {handle}")
        data = handles_ref[handle]
        return data[offset:offset + limit]
    
    def count(handle: str) -> int:
        """Get count of items in a handle without expanding.
        
        Args:
            handle: Handle name ('$res1') or full stub ('$res1: Array(20) [...]')
        """
        handle = _parse_handle(handle)
        if handle not in handles_ref:
            raise ValueError(f"Unknown handle: {handle}")
        return len(handles_ref[handle])
    
    def delete_handle(handle: str) -> str:
        """Delete a handle to free memory.
        
        Args:
            handle: Handle name ('$res1') or full stub ('$res1: Array(20) [...]')
        """
        handle = _parse_handle(handle)
        if handle not in handles_ref:
            return f"Handle {handle} not found."
        del handles_ref[handle]
        return f"Deleted {handle}."
    
    def filter_handle(handle: str, predicate: Union[str, Callable]) -> str:
        """Filter handle data and return new handle.
        
        Args:
            handle: Source handle ('$res1') or full stub ('$res1: Array(20) [...]')
            predicate: Either a regex pattern string (searches in 'snippet', 'line', or 'match' fields)
                      or a callable that takes an item and returns bool
        
        Returns:
            New handle stub with filtered results
        """
        handle = _parse_handle(handle)
        if handle not in handles_ref:
            raise ValueError(f"Unknown handle: {handle}")
        
        data = handles_ref[handle]
        
        if isinstance(predicate, str):
            # Treat as regex pattern
            pattern = re.compile(predicate)
            def match_fn(item: Any) -> bool:
                if isinstance(item, dict):
                    for key in ('snippet', 'line', 'match', 'content', 'text'):
                        if key in item and pattern.search(str(item[key])):
                            return True
                    return False
                return bool(pattern.search(str(item)))
            filtered = [item for item in data if match_fn(item)]
        else:
            # Treat as callable
            filtered = [item for item in data if predicate(item)]
        
        return _store_handle(filtered)
    
    def map_field(handle: str, field: str) -> str:
        """Extract a single field from each item, return new handle.
        
        Args:
            handle: Source handle ('$res1') or full stub ('$res1: Array(20) [...]')
            field: Field name to extract (e.g., 'match', 'line_num')
        
        Returns:
            New handle stub with extracted values
        """
        handle = _parse_handle(handle)
        if handle not in handles_ref:
            raise ValueError(f"Unknown handle: {handle}")
        
        data = handles_ref[handle]
        extracted = []
        for item in data:
            if isinstance(item, dict) and field in item:
                extracted.append(item[field])
            else:
                extracted.append(None)
        
        return _store_handle(extracted)
    
    def sum_field(handle: str, field: str = None) -> float:
        """Sum numeric values in handle data.
        
        Args:
            handle: Source handle ('$res1') or full stub ('$res1: Array(20) [...]')
            field: Optional field name. If None, sums items directly.
        
        Returns:
            Sum of numeric values
        """
        handle = _parse_handle(handle)
        if handle not in handles_ref:
            raise ValueError(f"Unknown handle: {handle}")
        
        data = handles_ref[handle]
        total = 0.0
        for item in data:
            if field and isinstance(item, dict):
                val = item.get(field, 0)
            else:
                val = item
            try:
                total += float(val)
            except (TypeError, ValueError):
                pass
        return total

    def chunk_indices(size: int = 200_000, overlap: int = 0) -> List[Tuple[int, int]]:
        if size <= 0:
            raise ValueError("size must be > 0")
        if overlap < 0:
            raise ValueError("overlap must be >= 0")
        if overlap >= size:
            raise ValueError("overlap must be < size")

        content = context_ref.get("content", "")
        n = len(content)
        spans: List[Tuple[int, int]] = []
        step = size - overlap
        for start in range(0, n, step):
            end = min(n, start + size)
            spans.append((start, end))
            if end >= n:
                break
        return spans

    def write_chunks(
        out_dir: str | os.PathLike,
        size: int = 200_000,
        overlap: int = 0,
        prefix: str = "chunk",
        encoding: str = "utf-8",
        include_hints: bool = True,
    ) -> List[str]:
        """Write content chunks to files and generate manifest.
        
        Args:
            out_dir: Output directory for chunks
            size: Chunk size in characters
            overlap: Overlap between chunks in characters
            prefix: Filename prefix for chunks
            encoding: File encoding
            include_hints: If True, add preview and content hints to manifest
        
        Returns:
            List of chunk file paths
        """
        content = context_ref.get("content", "")
        spans = chunk_indices(size=size, overlap=overlap)
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        
        paths: List[str] = []
        manifest_chunks: List[Dict[str, Any]] = []
        
        for i, (s, e) in enumerate(spans):
            chunk_id = f"{prefix}_{i:04d}"
            chunk_file = f"{chunk_id}.txt"
            p = out_path / chunk_file
            chunk_text = content[s:e]
            p.write_text(chunk_text, encoding=encoding)
            paths.append(str(p))
            
            start_line, end_line = _count_lines_in_range(content, s, e)
            
            chunk_meta: Dict[str, Any] = {
                "id": chunk_id,
                "file": chunk_file,
                "start_char": s,
                "end_char": e,
                "start_line": start_line,
                "end_line": end_line,
            }
            
            if include_hints:
                chunk_meta["preview"] = _generate_chunk_preview(chunk_text)
                hints = _generate_chunk_hints(chunk_text)
                if hints:
                    chunk_meta["hints"] = hints
            
            manifest_chunks.append(chunk_meta)
        
        # Write manifest.json
        session_dir = state_path_ref.parent
        manifest = {
            "session": session_dir.name,
            "context_file": context_ref.get("path", "unknown"),
            "total_chars": len(content),
            "total_lines": content.count('\n') + 1,
            "chunk_size": size,
            "overlap": overlap,
            "chunk_count": len(manifest_chunks),
            "chunks": manifest_chunks,
        }
        manifest_path = out_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        
        return paths

    # === Semantic Chunking Helper (Phase 4) ===

    def smart_chunk(
        out_dir: str | os.PathLike,
        target_size: int = 200_000,
        min_size: int = 50_000,
        max_size: int = 400_000,
        encoding: str = "utf-8",
    ) -> List[str]:
        """Smart content-aware chunking.
        
        Unlike write_chunks() which splits at fixed byte/char boundaries,
        smart_chunk() analyzes content structure and splits at natural
        boundaries:
        
        - **Markdown**: Splits at header boundaries (## and ###)
        - **Text**: Splits at paragraph breaks (double newlines)
        - **Code**: Splits at function/class boundaries (when codemap available)
        - **JSON**: Splits arrays at element boundaries, objects at key boundaries
        
        Args:
            out_dir: Output directory for chunks.
            target_size: Target chars per chunk (soft limit, default: 200,000).
            min_size: Minimum chunk size to avoid tiny chunks (default: 50,000).
            max_size: Maximum chunk size hard limit (default: 400,000).
            encoding: File encoding for output files.
        
        Returns:
            List of chunk file paths.
        
        Example:
            paths = smart_chunk(str(session_dir / 'chunks'), target_size=100000)
            for path in paths:
                chunk_text = Path(path).read_text()
                # Process chunk...
        """
        content = context_ref.get("content", "")
        context_path = context_ref.get("path", "unknown")
        
        paths, manifest = _smart_chunk_impl(
            content=content,
            context_path=context_path,
            out_dir=Path(out_dir),
            target_size=target_size,
            min_size=min_size,
            max_size=max_size,
            encoding=encoding,
        )
        
        return paths

    def add_buffer(text: str) -> None:
        buffers_ref.append(str(text))

    # === LLM Query Helpers (Phase 1) ===
    
    def llm_query(prompt: str, cleanup: bool = True) -> str:
        """Send a prompt to a sub-agent and return its response.
        
        This is the core RLM primitive for recursive LLM calls within
        Python code blocks.
        
        Args:
            prompt: The prompt to send to the sub-agent.
            cleanup: If True (default), remove sub-session state after completion.
                    Note: If preserve_recursive_state was set at init, cleanup
                    will be forced to False for debugging purposes.
        
        Returns:
            The text response from the sub-agent, or an error string on failure.
        
        Example:
            summary = llm_query("Summarize this in 50 words: " + chunk_text)
        """
        # Get remaining depth from state, with migration support
        remaining_depth = state_ref.get("remaining_depth", DEFAULT_MAX_DEPTH)
        
        # Phase 2: Respect preserve_recursive_state flag
        # If set at init, force cleanup=False for debugging
        preserve_recursive_state = state_ref.get("preserve_recursive_state", False)
        effective_cleanup = cleanup and not preserve_recursive_state
        
        # Use the global semaphore to limit concurrent spawns
        with _GLOBAL_CONCURRENCY_SEMAPHORE:
            return _spawn_sub_agent(
                prompt=prompt,
                remaining_depth=remaining_depth,
                session_dir=state_path_ref.parent,
                cleanup=effective_cleanup,
            )

    # === Batch LLM Query (Phase 3) ===
    
    def llm_query_batch(
        prompts: List[str],
        concurrency: int = 5,
        max_retries: int = 3,
        cleanup: bool = True,
    ) -> Tuple[List[str], Dict[int, Dict[str, Any]]]:
        """Execute multiple queries concurrently with retry support.
        
        This is the batch variant of llm_query() for parallel sub-LLM invocation.
        It respects the global concurrency limit (5) regardless of the requested
        concurrency, and retries failed queries with exponential backoff.
        
        Args:
            prompts: List of prompts to send to sub-agents.
            concurrency: Max concurrent queries (capped at 5 by global limit).
            max_retries: Number of retry attempts for failed queries (default: 3).
            cleanup: If True (default), remove sub-session state after completion.
        
        Returns:
            Tuple of (results, failures) where:
            - results: List of response strings in same order as input prompts.
                       Failed items contain "[ERROR: ...]" strings.
            - failures: Dict mapping failed indices to failure details:
                       {index: {"reason": str, "attempts": int, "error": str}}
        
        Example:
            prompts = [f"Summarize chunk {i}" for i in range(10)]
            results, failures = llm_query_batch(prompts, concurrency=5)
            for i, result in enumerate(results):
                if i not in failures:
                    print(f"Chunk {i}: {result[:100]}...")
        """
        # Get state info for spawning
        remaining_depth = state_ref.get("remaining_depth", DEFAULT_MAX_DEPTH)
        preserve_recursive_state = state_ref.get("preserve_recursive_state", False)
        effective_cleanup = cleanup and not preserve_recursive_state
        session_dir = state_path_ref.parent
        
        # Delegate to module-level implementation
        return _llm_query_batch_impl(
            prompts=prompts,
            remaining_depth=remaining_depth,
            session_dir=session_dir,
            cleanup=effective_cleanup,
            concurrency=concurrency,
            max_retries=max_retries,
        )

    # === Finalization Signal (Phase 5) ===

    def set_final_answer(value: Any) -> None:
        """Mark a value as the final answer for external retrieval.
        
        The final answer is persisted in state and can be retrieved via
        the `get-final-answer` CLI command. This enables the main agent
        to retrieve results from recursive sub-agent workflows.
        
        Args:
            value: Any JSON-serializable value (dict, list, str, int, float, bool, None).
                  Non-serializable values will raise ValueError.
        
        Raises:
            ValueError: If value is not JSON-serializable.
        
        Example:
            # Set a simple result
            set_final_answer({"summary": "Done", "count": 42})
            
            # Retrieve via CLI:
            # python rlm_repl.py --state ... get-final-answer
        """
        try:
            json.dumps(value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Final answer must be JSON-serializable: {e}")
        
        state_ref["final_answer"] = {
            "set_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "value": value,
        }
        value_type = type(value).__name__
        if isinstance(value, (list, dict, str)):
            print(f"Final answer set (type: {value_type}, length: {len(value)})")
        else:
            print(f"Final answer set (type: {value_type})")

    def has_final_answer() -> bool:
        """Check if a final answer has been set.
        
        Returns:
            True if set_final_answer() has been called, False otherwise.
        
        Example:
            if not has_final_answer():
                set_final_answer(compute_result())
        """
        return state_ref.get("final_answer") is not None

    def get_final_answer() -> Any:
        """Retrieve the final answer value.
        
        Returns:
            The value passed to set_final_answer(), or None if not set.
        
        Example:
            answer = get_final_answer()
            if answer is not None:
                process(answer)
        """
        fa = state_ref.get("final_answer")
        return fa["value"] if fa else None

    return {
        # Content exploration
        "peek": peek,
        "grep": grep,
        "grep_raw": grep_raw,
        "chunk_indices": chunk_indices,
        "write_chunks": write_chunks,
        "smart_chunk": smart_chunk,  # Phase 4: Semantic chunking
        "add_buffer": add_buffer,
        # Handle system
        "handles": handles,
        "last_handle": last_handle,
        "expand": expand,
        "count": count,
        "delete_handle": delete_handle,
        "filter_handle": filter_handle,
        "map_field": map_field,
        "sum_field": sum_field,
        # LLM Query (Phase 1)
        "llm_query": llm_query,
        # Batch LLM Query (Phase 3)
        "llm_query_batch": llm_query_batch,
        # Finalization Signal (Phase 5)
        "set_final_answer": set_final_answer,
        "has_final_answer": has_final_answer,
        "get_final_answer": get_final_answer,
    }


def cmd_init(args: argparse.Namespace) -> int:
    ctx_path = Path(args.context).resolve()
    
    # Generate session path if not explicitly provided
    if args.state:
        state_path = Path(args.state)
    else:
        state_path = _create_session_path(ctx_path)

    # Phase 2: Use CLI args for depth settings
    max_depth = args.max_depth
    preserve_recursive_state = args.preserve_recursive_state

    content = _read_text_file(ctx_path, max_bytes=args.max_bytes)
    state: Dict[str, Any] = {
        "version": 3,  # Phase 1: Added depth tracking
        "max_depth": max_depth,
        "remaining_depth": max_depth,
        "preserve_recursive_state": preserve_recursive_state,
        "context": {
            "path": str(ctx_path),
            "loaded_at": time.time(),
            "content": content,
        },
        "buffers": [],
        "handles": {},
        "handle_counter": 0,
        "globals": {},
        "final_answer": None,
    }
    _save_state(state, state_path)

    print(f"Session path: {state_path}")
    print(f"Session directory: {state_path.parent}")
    print(f"Context: {ctx_path} ({len(content):,} chars)")
    print(f"Max depth: {max_depth}")
    if preserve_recursive_state:
        print("Preserve recursive state: enabled")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = _load_state(state_path)
    ctx = state.get("context", {})
    content = ctx.get("content", "")
    buffers = state.get("buffers", [])
    handles = state.get("handles", {})
    g = state.get("globals", {})
    
    # Phase 2: Include depth info in status
    max_depth = state.get("max_depth", DEFAULT_MAX_DEPTH)
    remaining_depth = state.get("remaining_depth", DEFAULT_MAX_DEPTH)
    preserve_recursive_state = state.get("preserve_recursive_state", False)
    
    # Phase 5: Include final answer info in status
    final_answer = state.get("final_answer")

    print("RLM REPL status")
    print(f"  State file: {args.state}")
    print(f"  Session directory: {state_path.parent}")
    print(f"  Context path: {ctx.get('path')}")
    print(f"  Context chars: {len(content):,}")
    print(f"  Max depth: {max_depth}")
    print(f"  Remaining depth: {remaining_depth}")
    if preserve_recursive_state:
        print("  Preserve recursive state: enabled")
    
    # Phase 5: Display final answer status
    if final_answer is not None:
        value = final_answer.get("value")
        value_type = type(value).__name__
        if isinstance(value, (list, dict, str)):
            print(f"  Final answer: SET (type: {value_type}, length: {len(value)})")
        else:
            print(f"  Final answer: SET (type: {value_type})")
    else:
        print("  Final answer: NOT SET")
    
    print(f"  Buffers: {len(buffers)}")
    print(f"  Handles: {len(handles)}")
    print(f"  Persisted vars: {len(g)}")
    if args.show_vars and g:
        for k in sorted(g.keys()):
            print(f"    - {k}")
    if args.show_vars and handles:
        print("  Active handles:")
        for h in sorted(handles.keys(), key=lambda x: int(x.replace('$res', ''))):
            print(f"    - {h}: Array({len(handles[h])})")
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
    if not isinstance(buffers, list):
        buffers = []
        state["buffers"] = buffers

    persisted = state.setdefault("globals", {})
    if not isinstance(persisted, dict):
        persisted = {}
        state["globals"] = persisted

    code = args.code
    if code is None:
        code = sys.stdin.read()

    # Build execution environment.
    # Start from persisted variables, then inject context, buffers and helpers.
    env: Dict[str, Any] = dict(persisted)
    env["context"] = ctx
    env["content"] = ctx.get("content", "")
    env["buffers"] = buffers
    env["state_path"] = state_path
    env["session_dir"] = state_path.parent

    helpers = _make_helpers(ctx, buffers, state, state_path)
    env.update(helpers)

    # Capture output.
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(code, env, env)
    except Exception:
        traceback.print_exc(file=stderr_buf)

    # Pull back possibly mutated context/buffers.
    maybe_ctx = env.get("context")
    if isinstance(maybe_ctx, dict) and "content" in maybe_ctx:
        state["context"] = maybe_ctx
        ctx = maybe_ctx

    maybe_buffers = env.get("buffers")
    if isinstance(maybe_buffers, list):
        state["buffers"] = maybe_buffers
        buffers = maybe_buffers

    # Persist any new variables, excluding injected keys.
    injected_keys = {
        "__builtins__",
        "context",
        "content",
        "buffers",
        "state_path",
        "session_dir",
        *helpers.keys(),
    }
    to_persist = {k: v for k, v in env.items() if k not in injected_keys}
    filtered, dropped = _filter_pickleable(to_persist)
    state["globals"] = filtered

    _save_state(state, state_path)

    out = stdout_buf.getvalue()
    err = stderr_buf.getvalue()

    if dropped and args.warn_unpickleable:
        msg = "Dropped unpickleable variables: " + ", ".join(dropped)
        err = (err + ("\n" if err else "") + msg + "\n")

    if out:
        sys.stdout.write(_truncate(out, args.max_output_chars))

    if err:
        sys.stderr.write(_truncate(err, args.max_output_chars))

    return 0


def cmd_get_final_answer(args: argparse.Namespace) -> int:
    """CLI command to retrieve the final answer as JSON.
    
    Outputs a JSON object with:
    - "set": boolean indicating if answer is set
    - "value": the answer value (if set)
    - "set_at": ISO timestamp when set (if set)
    """
    state_path = Path(args.state)
    state = _load_state(state_path)
    
    final_answer = state.get("final_answer")
    
    if final_answer is None:
        result = {"set": False, "value": None, "set_at": None}
    else:
        result = {
            "set": True,
            "value": final_answer.get("value"),
            "set_at": final_answer.get("set_at"),
        }
    
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rlm_repl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Persistent mini-REPL for RLM-style workflows.

            Examples:
              # Initialize (auto-creates session directory)
              python rlm_repl.py init context.txt

              # Use the session (pass --state from init output)
              python rlm_repl.py --state .pi/rlm_state/context-20260120-153000/state.pkl status
              python rlm_repl.py --state ... exec -c "print(len(content))"
              python rlm_repl.py --state ... exec <<'PY'
              print(peek(0, 2000))
              PY
            """
        ),
    )
    p.add_argument(
        "--state",
        default=None,
        help="Path to state pickle. For init, this is optional (auto-generated). For other commands, required.",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Initialize state from a context file")
    p_init.add_argument("context", help="Path to the context file")
    p_init.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="Optional cap on bytes read from the context file",
    )
    p_init.add_argument(
        "--max-depth",
        type=int,
        default=DEFAULT_MAX_DEPTH,
        help=f"Maximum recursion depth for sub-LLM calls (default: {DEFAULT_MAX_DEPTH})",
    )
    p_init.add_argument(
        "--preserve-recursive-state",
        action="store_true",
        help="Keep sub-session directories after completion (for debugging)",
    )
    p_init.set_defaults(func=cmd_init)

    p_status = sub.add_parser("status", help="Show current state summary")
    p_status.add_argument(
        "--show-vars", action="store_true", help="List persisted variable names"
    )
    p_status.set_defaults(func=cmd_status)

    p_reset = sub.add_parser("reset", help="Delete the current state file")
    p_reset.set_defaults(func=cmd_reset)

    p_export = sub.add_parser(
        "export-buffers", help="Export buffers list to a text file"
    )
    p_export.add_argument("out", help="Output file path")
    p_export.set_defaults(func=cmd_export_buffers)

    p_exec = sub.add_parser("exec", help="Execute Python code with persisted state")
    p_exec.add_argument(
        "-c",
        "--code",
        default=None,
        help="Inline code string. If omitted, reads code from stdin.",
    )
    p_exec.add_argument(
        "--max-output-chars",
        type=int,
        default=DEFAULT_MAX_OUTPUT_CHARS,
        help=f"Truncate stdout/stderr to this many characters (default: {DEFAULT_MAX_OUTPUT_CHARS})",
    )
    p_exec.add_argument(
        "--warn-unpickleable",
        action="store_true",
        help="Warn on stderr when variables could not be persisted",
    )
    p_exec.set_defaults(func=cmd_exec)

    # Phase 5: get-final-answer command
    p_final = sub.add_parser(
        "get-final-answer",
        help="Retrieve the final answer as JSON (for external retrieval)",
    )
    p_final.set_defaults(func=cmd_get_final_answer)

    return p


def main(argv: List[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate --state for non-init commands
    if args.cmd != "init" and not args.state:
        parser.error(f"--state is required for '{args.cmd}' command")

    try:
        return int(args.func(args))
    except RlmReplError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
