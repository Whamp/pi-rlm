#!/usr/bin/env python3
"""Example: Basic RLM workflow.

This example demonstrates the core RLM workflow:
1. Initialize a session with content
2. Use grep to find patterns
3. Smart chunk the content
4. Set a final answer

Run from the skills/rlm directory:
    python3 examples/01_basic_workflow.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Path to rlm_repl.py
RLM_REPL = Path(__file__).parent.parent / "scripts" / "rlm_repl.py"


def run_cmd(cmd: list, cwd: Path = None) -> tuple:
    """Run command and return (stdout, stderr, returncode)."""
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result.stdout, result.stderr, result.returncode


def main():
    # Create temporary directory and sample content
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Create sample log file
        log_content = """
2026-01-21 10:00:01 INFO Starting application
2026-01-21 10:00:02 INFO Loading configuration
2026-01-21 10:00:03 ERROR Failed to connect to database: timeout
2026-01-21 10:00:04 INFO Retrying connection...
2026-01-21 10:00:05 ERROR Database connection failed again
2026-01-21 10:00:06 WARNING Using fallback configuration
2026-01-21 10:00:07 INFO Application started in degraded mode
2026-01-21 10:00:08 ERROR User authentication failed: invalid token
2026-01-21 10:00:09 INFO Processing request from user_123
2026-01-21 10:00:10 INFO Request completed successfully
"""
        log_file = tmpdir / "application.log"
        log_file.write_text(log_content)

        print("=" * 60)
        print("Example 1: Basic RLM Workflow")
        print("=" * 60)

        # Step 1: Initialize session
        print("\n[Step 1] Initializing session...")
        stdout, stderr, code = run_cmd(
            ["python3", str(RLM_REPL), "init", str(log_file)],
            cwd=tmpdir
        )
        if code != 0:
            print(f"Error: {stderr}")
            return 1

        # Extract state path
        state_path = None
        for line in stdout.splitlines():
            if "Session path:" in line:
                state_path = tmpdir / line.split(":", 1)[1].strip()
                break
        
        print(f"  Session created: {state_path.parent.name}")

        # Step 2: Search for errors
        print("\n[Step 2] Searching for ERROR entries...")
        stdout, stderr, code = run_cmd([
            "python3", str(RLM_REPL),
            "--state", str(state_path),
            "exec", "-c", """
result = grep('ERROR')
print(result)
print(f'Total errors: {count(last_handle())}')
for item in expand(last_handle()):
    print(f"  Line {item['line_num']}: {item['match'][:60]}")
"""
        ])
        print(stdout)

        # Step 3: Write chunks
        print("\n[Step 3] Writing chunks to disk...")
        stdout, stderr, code = run_cmd([
            "python3", str(RLM_REPL),
            "--state", str(state_path),
            "exec", "-c", f"""
paths = write_chunks(str(session_dir / 'chunks'), size=500)
print(f'Created {{len(paths)}} chunks')
"""
        ])
        print(stdout)

        # Step 4: Set final answer
        print("\n[Step 4] Setting final answer...")
        stdout, stderr, code = run_cmd([
            "python3", str(RLM_REPL),
            "--state", str(state_path),
            "exec", "-c", """
set_final_answer({
    'summary': 'Found 3 errors in log',
    'errors': ['database timeout', 'database connection failed', 'authentication failed']
})
"""
        ])
        print(stdout)

        # Step 5: Retrieve final answer
        print("\n[Step 5] Retrieving final answer via CLI...")
        stdout, stderr, code = run_cmd([
            "python3", str(RLM_REPL),
            "--state", str(state_path),
            "get-final-answer"
        ])
        data = json.loads(stdout)
        print(f"  Set: {data['set']}")
        print(f"  Value: {json.dumps(data['value'], indent=4)}")

        print("\n" + "=" * 60)
        print("Example completed successfully!")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
