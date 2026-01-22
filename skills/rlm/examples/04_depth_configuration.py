#!/usr/bin/env python3
"""Example: Depth configuration and recursive state.

This example demonstrates:
- --max-depth for controlling recursion limit
- --preserve-recursive-state for debugging
- How depth affects llm_query behavior

Run from the skills/rlm directory:
    python3 examples/04_depth_configuration.py
"""

import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

RLM_REPL = Path(__file__).parent.parent / "scripts" / "rlm_repl.py"


def run_cmd(cmd: list, cwd: Path) -> tuple:
    """Run command and return (stdout, stderr, code)."""
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result.stdout, result.stderr, result.returncode


def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        print("=" * 60)
        print("Example 4: Depth Configuration")
        print("=" * 60)

        content_file = tmpdir / "content.txt"
        content_file.write_text("Test content for depth examples")

        # --- Default depth ---
        print("\n" + "-" * 40)
        print("[1] Default depth (3)")
        print("-" * 40)

        stdout, stderr, code = run_cmd(
            ["python3", str(RLM_REPL), "init", str(content_file)],
            tmpdir
        )
        
        for line in stdout.splitlines():
            if "Session path:" in line:
                state_path = tmpdir / line.split(":", 1)[1].strip()
                break
        
        state = pickle.load(open(state_path, 'rb'))
        print(f"max_depth: {state['max_depth']}")
        print(f"remaining_depth: {state['remaining_depth']}")

        # --- Custom depth ---
        print("\n" + "-" * 40)
        print("[2] Custom depth (--max-depth 5)")
        print("-" * 40)

        stdout, stderr, code = run_cmd(
            ["python3", str(RLM_REPL), "init", str(content_file), "--max-depth", "5"],
            tmpdir
        )
        
        for line in stdout.splitlines():
            if "Session path:" in line:
                state_path = tmpdir / line.split(":", 1)[1].strip()
                break
        
        state = pickle.load(open(state_path, 'rb'))
        print(f"max_depth: {state['max_depth']}")
        print(f"remaining_depth: {state['remaining_depth']}")

        # --- Preserve recursive state ---
        print("\n" + "-" * 40)
        print("[3] Preserve recursive state flag")
        print("-" * 40)

        stdout, stderr, code = run_cmd(
            ["python3", str(RLM_REPL), "init", str(content_file), "--preserve-recursive-state"],
            tmpdir
        )
        
        for line in stdout.splitlines():
            if "Session path:" in line:
                state_path = tmpdir / line.split(":", 1)[1].strip()
                break
        
        state = pickle.load(open(state_path, 'rb'))
        print(f"preserve_recursive_state: {state['preserve_recursive_state']}")
        print("(Sub-session directories will be kept for debugging)")

        # --- Depth 0 behavior ---
        print("\n" + "-" * 40)
        print("[4] Depth 0: llm_query returns error")
        print("-" * 40)

        stdout, stderr, code = run_cmd(
            ["python3", str(RLM_REPL), "init", str(content_file), "--max-depth", "0"],
            tmpdir
        )
        
        for line in stdout.splitlines():
            if "Session path:" in line:
                state_path = tmpdir / line.split(":", 1)[1].strip()
                break

        result = subprocess.run(
            ["python3", str(RLM_REPL), "--state", str(state_path), "exec", "-c",
             "result = llm_query('test'); print(result)"],
            capture_output=True, text=True, cwd=tmpdir
        )
        print(f"llm_query result at depth 0:")
        print(f"  {result.stdout.strip()}")

        # --- Status shows depth ---
        print("\n" + "-" * 40)
        print("[5] Status command shows depth info")
        print("-" * 40)

        stdout, stderr, code = run_cmd(
            ["python3", str(RLM_REPL), "init", str(content_file), "--max-depth", "4"],
            tmpdir
        )
        
        for line in stdout.splitlines():
            if "Session path:" in line:
                state_path = tmpdir / line.split(":", 1)[1].strip()
                break

        stdout, stderr, code = run_cmd(
            ["python3", str(RLM_REPL), "--state", str(state_path), "status"],
            tmpdir
        )
        # Extract depth-related lines
        for line in stdout.splitlines():
            if "depth" in line.lower():
                print(line)

        print("\n" + "=" * 60)
        print("Example completed!")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
