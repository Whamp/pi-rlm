#!/usr/bin/env python3
"""Example: LLM queries for semantic reasoning (Level 2).

This example demonstrates llm_query() and llm_query_batch():
- Single LLM calls for classification/interpretation
- Batch LLM calls for parallel processing
- add_buffer() for accumulating results
- export-buffers CLI for synthesis

NOTE: These examples require the 'pi' CLI to be available.
Without it, llm_query() will return error strings.

Run from the skills/rlm directory:
    python3 examples/06_llm_query.py
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
    if result.stderr:
        print(f"  [stderr: {result.stderr[:200]}]")
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
        print("Example 6: LLM Queries (Level 2)")
        print("=" * 60)
        print("\nNOTE: These examples call the 'pi' CLI for LLM queries.")
        print("If 'pi' is not available, you'll see [ERROR:...] responses.\n")

        # Create sample log content
        log_content = """2026-01-21 10:00:01 ERROR Database connection failed: ETIMEDOUT after 30s to db.example.com:5432
2026-01-21 10:00:02 ERROR Authentication failed for user admin@corp.com: invalid password attempt 5
2026-01-21 10:00:03 ERROR File not found: /var/data/config.json - required for startup
2026-01-21 10:00:04 ERROR Memory allocation failed: requested 16GB but only 2GB available
2026-01-21 10:00:05 ERROR SSL certificate expired: cert for api.example.com expired 2026-01-15"""

        state_path = init_session(log_content, "errors.log", tmpdir)
        print(f"Initialized session: {state_path.parent.name}")

        # --- Single llm_query for classification ---
        print("\n" + "-" * 40)
        print("[1] Single llm_query() for classification")
        print("-" * 40)

        output = run_exec(state_path, """
# Extract first error line
lines = content.split('\\n')
first_error = lines[0] if lines else "No errors"

# Use LLM to classify severity
result = llm_query(f'''
Classify this error as CRITICAL, HIGH, MEDIUM, or LOW severity.
Respond with only the severity level, nothing else.

Error: {first_error}
''')

print(f"Error: {first_error[:60]}...")
print(f"LLM classification: {result.strip()}")
""", tmpdir)
        print(output)

        # --- llm_query with add_buffer ---
        print("\n" + "-" * 40)
        print("[2] llm_query() with add_buffer() accumulation")
        print("-" * 40)

        output = run_exec(state_path, """
# Process each error line and accumulate findings
lines = [l for l in content.split('\\n') if l.strip()]

for i, line in enumerate(lines[:3]):  # Process first 3
    result = llm_query(f'''
Analyze this error. Respond in exactly this format:
Category: <category>
Action: <one-sentence remediation>

Error: {line}
''')
    
    # Accumulate in buffer for later synthesis
    add_buffer(f"Error {i+1}:\\n{result}")
    print(f"Processed error {i+1}")

print(f"\\nBuffers accumulated: {len(buffers)}")
""", tmpdir)
        print(output)

        # --- Check buffer contents ---
        print("\n" + "-" * 40)
        print("[3] Inspect accumulated buffers")
        print("-" * 40)

        output = run_exec(state_path, """
print(f"Buffer count: {len(buffers)}")
for i, buf in enumerate(buffers):
    preview = buf[:100].replace('\\n', ' ')
    print(f"  Buffer {i}: {preview}...")
""", tmpdir)
        print(output)

        # --- export-buffers CLI ---
        print("\n" + "-" * 40)
        print("[4] export-buffers CLI command")
        print("-" * 40)

        findings_path = tmpdir / "findings.txt"
        stdout, stderr, code = run_cmd([
            "python3", str(RLM_REPL),
            "--state", str(state_path),
            "export-buffers", str(findings_path)
        ], tmpdir)
        print(stdout)
        
        if findings_path.exists():
            content = findings_path.read_text()
            print(f"Exported file size: {len(content)} chars")
            print(f"Preview: {content[:200]}...")

        # --- llm_query_batch for parallel processing ---
        print("\n" + "-" * 40)
        print("[5] llm_query_batch() for parallel processing")
        print("-" * 40)

        # Create new session for batch demo
        state_path = init_session(log_content, "errors2.log", tmpdir)

        output = run_exec(state_path, """
# Prepare prompts for each error
lines = [l for l in content.split('\\n') if l.strip()]
prompts = []
for line in lines:
    prompts.append(f'''
Categorize this error into exactly one of: NETWORK, AUTH, FILESYSTEM, MEMORY, SECURITY
Respond with only the category name.

Error: {line}
''')

# Run all prompts in parallel (max 5 concurrent)
print(f"Sending {len(prompts)} queries in parallel...")
results, failures = llm_query_batch(prompts, concurrency=5, max_retries=2)

# Process results
print(f"Results received: {len(results)}")
print(f"Failures: {len(failures)}")

for i, (line, result) in enumerate(zip(lines, results)):
    category = result.strip() if not result.startswith('[ERROR') else 'FAILED'
    print(f"  Error {i+1}: {category}")
""", tmpdir)
        print(output)

        # --- Demonstrating workflow pattern ---
        print("\n" + "-" * 40)
        print("[6] Complete workflow: grep → llm_query → buffer → synthesize")
        print("-" * 40)

        output = run_exec(state_path, """
# Step 1: Find specific errors with grep
grep('SSL|certificate')
ssl_errors = expand(last_handle())
print(f"Found {len(ssl_errors)} SSL-related errors")

# Step 2: Analyze with LLM
for err in ssl_errors:
    analysis = llm_query(f'''
For this SSL error, provide:
1. Root cause (one sentence)
2. Immediate fix (one sentence)

Error: {err['snippet']}
''')
    add_buffer(f"Line {err['line_num']}: {analysis}")

# Step 3: Final synthesis with LLM
all_findings = "\\n\\n".join(buffers)
summary = llm_query(f'''
Summarize these SSL error findings into a brief incident report.
Include: affected systems, root cause, recommended actions.

Findings:
{all_findings}
''')

print("=== INCIDENT SUMMARY ===")
print(summary)
""", tmpdir)
        print(output)

        print("\n" + "=" * 60)
        print("Example completed!")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
