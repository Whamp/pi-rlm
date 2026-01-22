#!/usr/bin/env python3
"""Example: Subagent synthesis workflow (Level 3).

This example demonstrates the subagent integration pattern:
- Chunking large content for parallel processing
- Using rlm-subcall subagent for chunk analysis
- Exporting buffers for final synthesis

NOTE: This example shows the PATTERN for subagent use.
Actual subagent invocation requires the 'pi' agent harness.
The example simulates what the agent would do.

Run from the skills/rlm directory:
    python3 examples/07_subagent_synthesis.py
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
        print("Example 7: Subagent Synthesis (Level 3)")
        print("=" * 60)

        # Create a larger document that would benefit from chunking
        sections = []
        for i in range(10):
            sections.append(f"""
## Section {i+1}: Component Analysis

This section covers the implementation details of component {i+1}.
The component handles {"authentication" if i % 3 == 0 else "data processing" if i % 3 == 1 else "networking"}.

### Key Features
- Feature A: Handles {i * 100} requests per second
- Feature B: Uses {"Redis" if i % 2 == 0 else "PostgreSQL"} for storage
- Feature C: Implements {"OAuth2" if i % 4 == 0 else "JWT"} for security

### Known Issues
- Issue #{i}01: Memory leak under high load
- Issue #{i}02: Timeout handling needs improvement
{"- Issue #" + str(i) + "03: Security vulnerability CVE-2026-" + str(1000+i) if i % 3 == 0 else ""}

### Recommendations
The component should be refactored to improve performance.
Consider implementing caching at the service layer.
""")
        
        large_doc = "\n".join(sections)
        state_path = init_session(large_doc, "analysis.md", tmpdir)
        print(f"\nInitialized session: {state_path.parent.name}")
        print(f"Document size: {len(large_doc):,} chars")

        # --- Step 1: Create chunks ---
        print("\n" + "-" * 40)
        print("[1] Create chunks for parallel processing")
        print("-" * 40)

        output = run_exec(state_path, """
import json

# Use smart_chunk to split at markdown boundaries
paths = smart_chunk(str(session_dir / 'chunks'), target_size=1000, min_size=200, max_size=2000)
print(f"Created {len(paths)} chunks")

# Read manifest to understand chunk contents
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())
print(f"Chunking method: {manifest['chunking_method']}")
print()
for chunk in manifest['chunks'][:3]:
    print(f"  {chunk['id']}: {chunk.get('preview', '')[:60]}...")
""", tmpdir)
        print(output)

        # --- Step 2: Show subagent invocation pattern ---
        print("\n" + "-" * 40)
        print("[2] Subagent invocation pattern (conceptual)")
        print("-" * 40)

        print("""
In the agent context, you would dispatch subagents like this:

    # Read manifest to get chunk paths
    manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())
    
    # Build subagent tasks
    tasks = []
    for chunk in manifest['chunks']:
        chunk_path = session_dir / 'chunks' / chunk['file']
        tasks.append({
            "agent": "rlm-subcall",
            "task": f"Query: Find security issues\\nChunk file: {chunk_path}"
        })
    
    # Dispatch up to 8 in parallel
    subagent(tasks=tasks[:8])

Each subagent returns structured JSON:

    {
        "chunk_id": "chunk_0000",
        "relevant": [
            {"point": "CVE-2026-1000 mentioned", "evidence": "Security vulnerability", "confidence": "high"}
        ],
        "missing": [],
        "answer_if_complete": null
    }
""")

        # --- Step 3: Simulate subagent results ---
        print("\n" + "-" * 40)
        print("[3] Process simulated subagent results")
        print("-" * 40)

        output = run_exec(state_path, """
import json

# Simulate what subagents would return
simulated_results = [
    {"chunk_id": "chunk_0000", "relevant": [{"point": "Auth component has OAuth2", "confidence": "high"}]},
    {"chunk_id": "chunk_0001", "relevant": [{"point": "Data processing uses Redis", "confidence": "high"}]},
    {"chunk_id": "chunk_0002", "relevant": [{"point": "Networking component found", "confidence": "medium"}]},
    {"chunk_id": "chunk_0003", "relevant": [{"point": "Security vulnerability CVE-2026-1003", "confidence": "high"}]},
]

# Accumulate findings in buffers
for result in simulated_results:
    for finding in result.get('relevant', []):
        add_buffer(f"{result['chunk_id']}: {finding['point']} ({finding['confidence']})")

print(f"Accumulated {len(buffers)} findings in buffers")
for buf in buffers:
    print(f"  - {buf}")
""", tmpdir)
        print(output)

        # --- Step 4: Export buffers for synthesis ---
        print("\n" + "-" * 40)
        print("[4] Export buffers for final synthesis")
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
            print(f"Exported {len(content)} chars to findings.txt:")
            print("-" * 30)
            print(content)
            print("-" * 30)

        # --- Step 5: Show synthesis pattern ---
        print("\n" + "-" * 40)
        print("[5] Final synthesis pattern")
        print("-" * 40)

        print("""
For final synthesis, you have two options:

Option A: Use llm_query() in REPL (Level 2)
    
    findings = "\\n".join(buffers)
    summary = llm_query(f'''
    Synthesize these findings into a report:
    {findings}
    ''')
    set_final_answer({"summary": summary})

Option B: Use subagent for complex synthesis (Level 3)
    
    # Export buffers to file
    python3 rlm_repl.py --state <path> export-buffers findings.txt
    
    # Then in agent context:
    subagent(
        agent="rlm-subcall",
        task=f"Synthesize into report:\\n$(cat findings.txt)"
    )

Choose based on complexity:
- Simple aggregation → Level 2 (llm_query)
- Complex multi-step synthesis → Level 3 (subagent)
""")

        # --- Step 6: Set final answer ---
        print("\n" + "-" * 40)
        print("[6] Set final answer with aggregated results")
        print("-" * 40)

        output = run_exec(state_path, """
# Aggregate buffer findings into structured result
findings = []
for buf in buffers:
    parts = buf.split(': ', 1)
    if len(parts) == 2:
        chunk_id, finding = parts
        findings.append({"chunk": chunk_id, "finding": finding})

result = {
    "summary": f"Analyzed {len(findings)} findings across document",
    "findings": findings,
    "security_issues": [f for f in findings if "CVE" in f.get("finding", "")],
}

set_final_answer(result)
""", tmpdir)
        print(output)

        # Retrieve final answer
        stdout, stderr, code = run_cmd([
            "python3", str(RLM_REPL),
            "--state", str(state_path),
            "get-final-answer"
        ], tmpdir)
        result = json.loads(stdout)
        print(f"\nFinal answer set: {result['set']}")
        print(f"Security issues found: {len(result['value']['security_issues'])}")
        print(json.dumps(result['value'], indent=2))

        print("\n" + "=" * 60)
        print("Example completed!")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
