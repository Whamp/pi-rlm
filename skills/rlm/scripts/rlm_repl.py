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
        """Expand a handle to see its data (with optional pagination)."""
        if handle not in handles_ref:
            raise ValueError(f"Unknown handle: {handle}")
        data = handles_ref[handle]
        return data[offset:offset + limit]
    
    def count(handle: str) -> int:
        """Get count of items in a handle without expanding."""
        if handle not in handles_ref:
            raise ValueError(f"Unknown handle: {handle}")
        return len(handles_ref[handle])
    
    def delete_handle(handle: str) -> str:
        """Delete a handle to free memory."""
        if handle not in handles_ref:
            return f"Handle {handle} not found."
        del handles_ref[handle]
        return f"Deleted {handle}."
    
    def filter_handle(handle: str, predicate: Union[str, Callable]) -> str:
        """Filter handle data and return new handle.
        
        Args:
            handle: Source handle (e.g., '$res1')
            predicate: Either a regex pattern string (searches in 'snippet', 'line', or 'match' fields)
                      or a callable that takes an item and returns bool
        
        Returns:
            New handle stub with filtered results
        """
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
            handle: Source handle
            field: Field name to extract (e.g., 'match', 'line_num')
        
        Returns:
            New handle stub with extracted values
        """
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
            handle: Source handle
            field: Optional field name. If None, sums items directly.
        
        Returns:
            Sum of numeric values
        """
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
        
        Returns:
            The text response from the sub-agent, or an error string on failure.
        
        Example:
            summary = llm_query("Summarize this in 50 words: " + chunk_text)
        """
        # Get remaining depth from state, with migration support
        remaining_depth = state_ref.get("remaining_depth", DEFAULT_MAX_DEPTH)
        
        # Use the global semaphore to limit concurrent spawns
        with _GLOBAL_CONCURRENCY_SEMAPHORE:
            return _spawn_sub_agent(
                prompt=prompt,
                remaining_depth=remaining_depth,
                session_dir=state_path_ref.parent,
                cleanup=cleanup,
            )

    return {
        # Content exploration
        "peek": peek,
        "grep": grep,
        "grep_raw": grep_raw,
        "chunk_indices": chunk_indices,
        "write_chunks": write_chunks,
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
    }


def cmd_init(args: argparse.Namespace) -> int:
    ctx_path = Path(args.context).resolve()
    
    # Generate session path if not explicitly provided
    if args.state:
        state_path = Path(args.state)
    else:
        state_path = _create_session_path(ctx_path)

    content = _read_text_file(ctx_path, max_bytes=args.max_bytes)
    state: Dict[str, Any] = {
        "version": 3,  # Phase 1: Added depth tracking
        "max_depth": DEFAULT_MAX_DEPTH,
        "remaining_depth": DEFAULT_MAX_DEPTH,
        "preserve_recursive_state": False,
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
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = _load_state(state_path)
    ctx = state.get("context", {})
    content = ctx.get("content", "")
    buffers = state.get("buffers", [])
    handles = state.get("handles", {})
    g = state.get("globals", {})

    print("RLM REPL status")
    print(f"  State file: {args.state}")
    print(f"  Session directory: {state_path.parent}")
    print(f"  Context path: {ctx.get('path')}")
    print(f"  Context chars: {len(content):,}")
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
