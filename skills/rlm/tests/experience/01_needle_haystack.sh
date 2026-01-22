#!/bin/bash
# RLM Experience Test: Needle in a Haystack
# 
# Tests the core value proposition: finding specific info in large documents
# that wouldn't fit in a single context window.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RLM_SCRIPT="$HOME/projects/pi-rlm/skills/rlm/scripts/rlm_repl.py"
TEST_DIR="/tmp/rlm-needle-test-$$"
HAYSTACK_FILE="$TEST_DIR/haystack.txt"

# Configurable parameters
HAYSTACK_SIZE_KB=${1:-500}  # Default 500KB
NEEDLE="The secret activation code for Project RLM is: NEEDLE-FOUND-42XYZ"

echo "=== RLM Needle in Haystack Test ==="
echo "Haystack size: ${HAYSTACK_SIZE_KB}KB"
echo ""

# Setup
mkdir -p "$TEST_DIR"

echo "1. Generating haystack..."
python3 -c "
import random
import sys

target_bytes = ${HAYSTACK_SIZE_KB} * 1024
words = [
    'lorem', 'ipsum', 'dolor', 'sit', 'amet', 'consectetur', 'adipiscing',
    'elit', 'sed', 'do', 'eiusmod', 'tempor', 'incididunt', 'ut', 'labore',
    'et', 'dolore', 'magna', 'aliqua', 'enim', 'ad', 'minim', 'veniam',
    'quis', 'nostrud', 'exercitation', 'ullamco', 'laboris', 'nisi',
]

content = []
current_size = 0

while current_size < target_bytes:
    line = ' '.join(random.choices(words, k=random.randint(8, 20))) + '\n'
    content.append(line)
    current_size += len(line)

# Insert needle at approximately 60% depth (harder than middle)
insert_point = int(len(content) * 0.6)
content.insert(insert_point, '${NEEDLE}\n')

sys.stdout.write(''.join(content))
" > "$HAYSTACK_FILE"

ACTUAL_SIZE=$(wc -c < "$HAYSTACK_FILE")
LINE_COUNT=$(wc -l < "$HAYSTACK_FILE")
echo "   Created: $ACTUAL_SIZE bytes, $LINE_COUNT lines"

echo ""
echo "2. Initializing RLM session..."
INIT_OUTPUT=$(python3 "$RLM_SCRIPT" init "$HAYSTACK_FILE" 2>&1)
STATE_PATH=$(echo "$INIT_OUTPUT" | grep -o "\.pi/rlm_state/[^/]*/state.pkl" | head -1)

if [ -z "$STATE_PATH" ]; then
    echo "ERROR: Failed to initialize session"
    echo "$INIT_OUTPUT"
    exit 1
fi

echo "   State: $STATE_PATH"

echo ""
echo "3. Searching for needle using grep()..."
START_TIME=$(date +%s.%N)

GREP_OUTPUT=$(python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec -c "
# grep() returns full stub - now works directly with count()/expand()
result = grep('secret activation code')
print(f'Handle: {result}')
match_count = count(result)  # Works with full stub now!
print(f'MATCHES: {match_count}')

if match_count > 0:
    matches = expand(result)  # Works with full stub now!
    for m in matches:
        print(f'FOUND_AT_LINE: {m.get(\"line_num\", \"?\")}')
        snippet = m.get('snippet', '')
        print(f'SNIPPET: {snippet}')
")

END_TIME=$(date +%s.%N)
DURATION=$(python3 -c "print(f'{$END_TIME - $START_TIME:.3f}')")

echo "$GREP_OUTPUT"

echo ""
echo "4. Results:"
if echo "$GREP_OUTPUT" | grep -q "NEEDLE-FOUND-42XYZ"; then
    echo "   ✅ SUCCESS: Needle found!"
else
    echo "   ❌ FAILURE: Needle not found"
fi

echo "   Search time: ${DURATION}s"

echo ""
echo "5. Testing smart chunking on haystack..."
CHUNK_OUTPUT=$(python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec -c "
import os
chunks_dir = session_dir / 'chunks'
paths = smart_chunk(str(chunks_dir), target_size=100000)
print(f'CHUNKS_CREATED: {len(paths)}')

# Check manifest
manifest_path = chunks_dir / 'manifest.json'
if manifest_path.exists():
    import json
    manifest = json.loads(manifest_path.read_text())
    print(f'FORMAT_DETECTED: {manifest.get(\"format\", \"unknown\")}')
    print(f'METHOD: {manifest.get(\"chunking_method\", \"unknown\")}')
")

echo "$CHUNK_OUTPUT"

echo ""
echo "6. Cleanup..."
# Keep session for debugging, just report location
echo "   Test dir: $TEST_DIR"
echo "   Session: $(dirname "$STATE_PATH")"

echo ""
echo "=== Test Complete ==="
