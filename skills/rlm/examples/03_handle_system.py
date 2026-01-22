#!/usr/bin/env python3
"""Example: Handle-based search for token-efficient exploration.

This example demonstrates the handle system:
- grep() returns handles, not raw data
- expand() materializes only what you need
- filter_handle() and map_field() for server-side transformations

Run from the skills/rlm directory:
    python3 examples/03_handle_system.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

RLM_REPL = Path(__file__).parent.parent / "scripts" / "rlm_repl.py"


def run_exec(state_path: Path, code: str, tmpdir: Path) -> str:
    """Run exec command and return stdout."""
    result = subprocess.run(
        ["python3", str(RLM_REPL), "--state", str(state_path), "exec", "-c", code],
        capture_output=True, text=True, cwd=tmpdir
    )
    return result.stdout


def init_session(content: str, filename: str, tmpdir: Path) -> Path:
    """Initialize session and return state path."""
    file_path = tmpdir / filename
    file_path.write_text(content)
    
    result = subprocess.run(
        ["python3", str(RLM_REPL), "init", str(file_path)],
        capture_output=True, text=True, cwd=tmpdir
    )
    
    for line in result.stdout.splitlines():
        if "Session path:" in line:
            return tmpdir / line.split(":", 1)[1].strip()
    raise RuntimeError(f"Failed to init: {result.stderr}")


def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        print("=" * 60)
        print("Example 3: Handle-Based Search System")
        print("=" * 60)

        # Create sample log with various patterns
        log_lines = []
        for i in range(200):
            if i % 7 == 0:
                log_lines.append(f"[ERROR] Line {i}: Connection timeout to server-{i % 5}")
            elif i % 5 == 0:
                log_lines.append(f"[ERROR] Line {i}: Authentication failed for user_{i}")
            elif i % 3 == 0:
                log_lines.append(f"[WARNING] Line {i}: Cache miss, fetching from database")
            else:
                log_lines.append(f"[INFO] Line {i}: Processing request #{i}")

        state_path = init_session("\n".join(log_lines), "server.log", tmpdir)
        print(f"\nInitialized session with 200 log lines")

        # Step 1: Basic grep returns a handle
        print("\n" + "-" * 40)
        print("[1] grep() returns a handle, not data")
        print("-" * 40)

        output = run_exec(state_path, """
result = grep('ERROR')
print(f'Type returned: {type(result).__name__}')
print(f'Handle stub: {result[:80]}...')
""", tmpdir)
        print(output)

        # Step 2: Count without expanding
        print("\n" + "-" * 40)
        print("[2] count() without loading all data")
        print("-" * 40)

        output = run_exec(state_path, """
grep('ERROR')
error_count = count(last_handle())
print(f'Total errors: {error_count}')
print('(No data was loaded into context!)')
""", tmpdir)
        print(output)

        # Step 3: Expand only what you need
        print("\n" + "-" * 40)
        print("[3] expand() with limit and offset")
        print("-" * 40)

        output = run_exec(state_path, """
grep('ERROR')
handle = last_handle()

print('First 3 errors:')
for item in expand(handle, limit=3, offset=0):
    print(f"  Line {item['line_num']}: {item['match'][:50]}...")

print()
print('Errors 5-7:')
for item in expand(handle, limit=3, offset=5):
    print(f"  Line {item['line_num']}: {item['match'][:50]}...")
""", tmpdir)
        print(output)

        # Step 4: Filter handle server-side
        print("\n" + "-" * 40)
        print("[4] filter_handle() for subset")
        print("-" * 40)

        output = run_exec(state_path, """
grep('ERROR')
print(f'All errors: {count(last_handle())}')

# Filter to just timeout errors
filter_handle(last_handle(), 'timeout')
print(f'Timeout errors: {count(last_handle())}')

# Filter to auth errors
grep('ERROR')
filter_handle(last_handle(), 'Authentication')
print(f'Auth errors: {count(last_handle())}')
""", tmpdir)
        print(output)

        # Step 5: map_field() for extraction
        print("\n" + "-" * 40)
        print("[5] map_field() extracts single field")
        print("-" * 40)

        output = run_exec(state_path, """
grep('ERROR')
map_field(last_handle(), 'line_num')
line_numbers = expand(last_handle(), limit=10)
print(f'First 10 error line numbers: {line_numbers}')
""", tmpdir)
        print(output)

        # Step 6: Chaining operations
        print("\n" + "-" * 40)
        print("[6] Chaining with last_handle()")
        print("-" * 40)

        output = run_exec(state_path, """
# Chain: grep -> filter -> map -> expand
grep('WARNING')
print(f'All warnings: {count(last_handle())}')

filter_handle(last_handle(), 'Cache')
print(f'Cache warnings: {count(last_handle())}')

map_field(last_handle(), 'line_num')
lines = expand(last_handle())
print(f'Line numbers: {lines[:5]}...')
""", tmpdir)
        print(output)

        # Step 7: Handle management
        print("\n" + "-" * 40)
        print("[7] Managing handles")
        print("-" * 40)

        output = run_exec(state_path, """
# List all handles
print('Current handles:')
print(handles())

# Delete a handle
delete_handle('$res1')
print()
print('After deletion:')
print(handles())
""", tmpdir)
        print(output)

        print("\n" + "=" * 60)
        print("Example completed!")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
