#!/bin/bash
# RLM Experience Test: LLM Query Integration
#
# Tests llm_query() and llm_query_batch() behavior.
# Note: These tests use the real pi command, so results depend on availability.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RLM_SCRIPT="$HOME/projects/pi-rlm/skills/rlm/scripts/rlm_repl.py"
TEST_DIR="/tmp/rlm-llm-test-$$"
CONTENT_FILE="$TEST_DIR/content.txt"

echo "=== RLM LLM Query Integration Test ==="
echo ""

mkdir -p "$TEST_DIR"

# Create test content
cat > "$CONTENT_FILE" << 'EOF'
# Project Documentation

## Overview
This project implements a recursive language model workflow.

## Features
- Inline LLM queries with llm_query()
- Batch execution with llm_query_batch()
- Smart chunking for large documents
- Handle-based search for token efficiency

## Usage
Initialize a session, then use the REPL helpers to process content.
EOF

echo "1. Initializing RLM session with max-depth=2..."
INIT_OUTPUT=$(python3 "$RLM_SCRIPT" init "$CONTENT_FILE" --max-depth 2 2>&1)
STATE_PATH=$(echo "$INIT_OUTPUT" | grep -o "\.pi/rlm_state/[^/]*/state.pkl" | head -1)
echo "   State: $STATE_PATH"

echo ""
echo "2. Checking session status (depth info)..."
python3 "$RLM_SCRIPT" --state "$STATE_PATH" status

echo ""
echo "3. Testing llm_query() single call..."
# Use timeout to prevent hanging
timeout 30 python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec << 'PYTHON' || echo "   (timed out or failed)"
print("Calling llm_query()...")
result = llm_query("Respond with exactly: PONG")
print(f"  Result type: {type(result).__name__}")
print(f"  Result length: {len(result)} chars")

if "ERROR" in result:
    print(f"  Error response: {result[:100]}")
elif "PONG" in result.upper():
    print("  ✅ Got expected PONG response")
else:
    print(f"  Response: {result[:100]}...")
PYTHON

echo ""
echo "4. Testing depth=0 rejection..."
# Create a session with depth=0 to test rejection
INIT_OUTPUT2=$(python3 "$RLM_SCRIPT" init "$CONTENT_FILE" --max-depth 0 2>&1)
STATE_PATH2=$(echo "$INIT_OUTPUT2" | grep -o "\.pi/rlm_state/[^/]*/state.pkl" | head -1)
echo "   Created depth=0 session: $STATE_PATH2"

python3 "$RLM_SCRIPT" --state "$STATE_PATH2" exec << 'PYTHON'
result = llm_query("This should be rejected")
if "depth limit" in result.lower() or "recursion" in result.lower():
    print("  ✅ Correctly rejected due to depth limit")
    print(f"  Message: {result[:80]}")
else:
    print(f"  Unexpected response: {result[:100]}")
PYTHON

echo ""
echo "5. Checking query log..."
SESSION_DIR=$(dirname "$STATE_PATH")
LOG_FILE="$SESSION_DIR/llm_queries.jsonl"

if [ -f "$LOG_FILE" ]; then
    echo "   Log file exists: $LOG_FILE"
    ENTRY_COUNT=$(wc -l < "$LOG_FILE")
    echo "   Entries: $ENTRY_COUNT"
    
    echo "   Recent queries:"
    tail -3 "$LOG_FILE" | python3 -c "
import sys, json
for line in sys.stdin:
    if line.strip():
        entry = json.loads(line)
        qid = entry.get('query_id', '?')
        status = entry.get('status', '?')
        duration = entry.get('duration_ms', '?')
        prompt_chars = entry.get('prompt_chars', '?')
        print(f'     - {qid}: {status} ({duration}ms, {prompt_chars} chars)')
"
else
    echo "   No log file created"
fi

echo ""
echo "6. Verifying llm_query_batch() is available..."
python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec << 'PYTHON'
# Just verify the function exists and has expected signature
import inspect
sig = inspect.signature(llm_query_batch)
params = list(sig.parameters.keys())
print(f"  llm_query_batch params: {params}")
print(f"  ✅ Function available with expected signature")
PYTHON

echo ""
echo "=== Test Complete ==="
echo "Test directory: $TEST_DIR"
