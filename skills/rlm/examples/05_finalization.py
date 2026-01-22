#!/usr/bin/env python3
"""Example: Answer finalization workflow.

This example demonstrates:
- set_final_answer() for marking results
- has_final_answer() and get_final_answer() helpers
- get-final-answer CLI command
- Final answer shown in status

Run from the skills/rlm directory:
    python3 examples/05_finalization.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

RLM_REPL = Path(__file__).parent.parent / "scripts" / "rlm_repl.py"


def run_cmd(cmd: list, cwd: Path) -> tuple:
    """Run command and return (stdout, stderr, code)."""
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result.stdout, result.stderr, result.returncode


def run_exec(state_path: Path, code: str, cwd: Path) -> str:
    """Run exec and return stdout."""
    result = subprocess.run(
        ["python3", str(RLM_REPL), "--state", str(state_path), "exec", "-c", code],
        capture_output=True, text=True, cwd=cwd
    )
    return result.stdout


def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        print("=" * 60)
        print("Example 5: Answer Finalization")
        print("=" * 60)

        content_file = tmpdir / "data.txt"
        content_file.write_text("Sample content for analysis")

        # Initialize session
        stdout, _, _ = run_cmd(
            ["python3", str(RLM_REPL), "init", str(content_file)],
            tmpdir
        )
        for line in stdout.splitlines():
            if "Session path:" in line:
                state_path = tmpdir / line.split(":", 1)[1].strip()
                break

        print(f"\nSession initialized: {state_path.parent.name}")

        # --- Check before setting ---
        print("\n" + "-" * 40)
        print("[1] Before setting final answer")
        print("-" * 40)

        output = run_exec(state_path, """
print(f'has_final_answer(): {has_final_answer()}')
print(f'get_final_answer(): {get_final_answer()}')
""", tmpdir)
        print(output)

        # --- Set a simple answer ---
        print("\n" + "-" * 40)
        print("[2] Set a simple final answer")
        print("-" * 40)

        output = run_exec(state_path, """
set_final_answer('Analysis complete: No issues found')
""", tmpdir)
        print(output)

        output = run_exec(state_path, """
print(f'has_final_answer(): {has_final_answer()}')
print(f'get_final_answer(): {get_final_answer()}')
""", tmpdir)
        print(output)

        # --- Update with complex answer ---
        print("\n" + "-" * 40)
        print("[3] Update with complex structured answer")
        print("-" * 40)

        output = run_exec(state_path, """
result = {
    'summary': 'Analysis complete',
    'metrics': {
        'files_processed': 42,
        'errors_found': 3,
        'warnings': 15
    },
    'issues': [
        {'file': 'main.py', 'line': 45, 'message': 'Unused variable'},
        {'file': 'utils.py', 'line': 12, 'message': 'Missing docstring'},
        {'file': 'config.py', 'line': 8, 'message': 'Deprecated import'}
    ],
    'recommendations': ['Fix unused variables', 'Add documentation']
}
set_final_answer(result)
""", tmpdir)
        print(output)

        # --- Retrieve via CLI ---
        print("\n" + "-" * 40)
        print("[4] Retrieve via get-final-answer CLI")
        print("-" * 40)

        stdout, _, _ = run_cmd(
            ["python3", str(RLM_REPL), "--state", str(state_path), "get-final-answer"],
            tmpdir
        )
        data = json.loads(stdout)
        print(f"set: {data['set']}")
        print(f"set_at: {data['set_at']}")
        print(f"value type: {type(data['value']).__name__}")
        print(f"issues found: {len(data['value']['issues'])}")

        # --- Status shows final answer ---
        print("\n" + "-" * 40)
        print("[5] Status shows final answer info")
        print("-" * 40)

        stdout, _, _ = run_cmd(
            ["python3", str(RLM_REPL), "--state", str(state_path), "status"],
            tmpdir
        )
        for line in stdout.splitlines():
            if "Final answer" in line:
                print(line)

        # --- Non-serializable error ---
        print("\n" + "-" * 40)
        print("[6] Error on non-JSON-serializable value")
        print("-" * 40)

        output = run_exec(state_path, """
import re
try:
    set_final_answer({'regex': re.compile('test')})
    print('ERROR: Should have raised')
except ValueError as e:
    print(f'Correctly rejected: ValueError raised')
""", tmpdir)
        print(output)

        print("\n" + "=" * 60)
        print("Example completed!")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
