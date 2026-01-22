#!/usr/bin/env python3
"""Example: Depth configuration (advanced).

Depth controls how many levels of llm_query() nesting are allowed.
This is mostly an implementation detail - the default of 3 works for most cases.

This example demonstrates:
- Default depth behavior
- Custom depth with --max-depth
- --preserve-recursive-state for debugging

Run from the skills/rlm directory:
    python3 examples/04_depth_configuration.py
"""

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
        print("Example 4: Depth Configuration (Advanced)")
        print("=" * 60)
        print("\nDepth limits how many nested llm_query() calls are allowed.")
        print("Default is 3, which is sufficient for most workflows.\n")

        content_file = tmpdir / "content.txt"
        content_file.write_text("Test content for depth examples")

        # --- Default depth ---
        print("-" * 40)
        print("[1] Default initialization")
        print("-" * 40)

        stdout, stderr, code = run_cmd(
            ["python3", str(RLM_REPL), "init", str(content_file)],
            tmpdir
        )
        print(stdout)

        # --- Custom depth ---
        print("-" * 40)
        print("[2] Custom depth (--max-depth 5)")
        print("-" * 40)

        stdout, stderr, code = run_cmd(
            ["python3", str(RLM_REPL), "init", str(content_file), "--max-depth", "5"],
            tmpdir
        )
        print(stdout)

        # --- Preserve recursive state (for debugging) ---
        print("-" * 40)
        print("[3] Preserve sub-session state for debugging")
        print("-" * 40)

        stdout, stderr, code = run_cmd(
            ["python3", str(RLM_REPL), "init", str(content_file), "--preserve-recursive-state"],
            tmpdir
        )
        print(stdout)
        print("With --preserve-recursive-state, sub-query session dirs are kept")
        print("for debugging instead of being cleaned up automatically.")

        # --- Depth 0 prevents LLM calls ---
        print("\n" + "-" * 40)
        print("[4] Depth 0 prevents llm_query()")
        print("-" * 40)

        stdout, stderr, code = run_cmd(
            ["python3", str(RLM_REPL), "init", str(content_file), "--max-depth", "0"],
            tmpdir
        )
        
        state_path = None
        for line in stdout.splitlines():
            if "Session path:" in line:
                state_path = tmpdir / line.split(":", 1)[1].strip()
                break

        if state_path:
            result = subprocess.run(
                ["python3", str(RLM_REPL), "--state", str(state_path), "exec", "-c",
                 "result = llm_query('test'); print(result)"],
                capture_output=True, text=True, cwd=tmpdir
            )
            print(f"llm_query() at depth 0 returns:")
            print(f"  {result.stdout.strip()}")
            print("\nThis is useful when you want REPL-only analysis (Level 1).")

        print("\n" + "=" * 60)
        print("Example completed!")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
