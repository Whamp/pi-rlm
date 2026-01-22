#!/bin/bash
# RLM Experience Test: Smart JSON Chunking
#
# Tests that JSON files are chunked at element/key boundaries,
# preserving parseable JSON structure.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RLM_SCRIPT="$HOME/projects/pi-rlm/skills/rlm/scripts/rlm_repl.py"
TEST_DIR="/tmp/rlm-json-test-$$"

echo "=== RLM Smart JSON Chunking Test ==="
echo ""

mkdir -p "$TEST_DIR"

# ============================================
# Test A: JSON Array Chunking
# ============================================
echo "=== Test A: JSON Array Chunking ==="
echo ""

ARRAY_FILE="$TEST_DIR/test-array.json"
echo "1. Generating JSON array (~100KB with 200 objects)..."

python3 << PYTHON > "$ARRAY_FILE"
import json

data = []
for i in range(200):
    data.append({
        "id": i,
        "name": f"Item {i}",
        "description": f"This is a detailed description for item {i}. " * 10,
        "metadata": {
            "created": f"2025-01-{(i % 28) + 1:02d}",
            "category": f"category_{i % 5}",
            "tags": [f"tag_{j}" for j in range(i % 10)]
        },
        "values": [j * i for j in range(20)]
    })

print(json.dumps(data, indent=2))
PYTHON

ARRAY_SIZE=$(wc -c < "$ARRAY_FILE")
echo "   Created: $ARRAY_SIZE bytes"

echo ""
echo "2. Initializing RLM session..."
INIT_OUTPUT=$(python3 "$RLM_SCRIPT" init "$ARRAY_FILE" 2>&1)
STATE_PATH=$(echo "$INIT_OUTPUT" | grep -o "\.pi/rlm_state/[^/]*/state.pkl" | head -1)
echo "   State: $STATE_PATH"

echo ""
echo "3. Running smart_chunk() with target=20000 chars..."
python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec << 'PYTHON'
import json

chunks_dir = session_dir / 'chunks'
paths = smart_chunk(str(chunks_dir), target_size=20000, min_size=5000, max_size=40000)
print(f'Chunks created: {len(paths)}')

# Read and analyze manifest
manifest_path = chunks_dir / 'manifest.json'
manifest = json.loads(manifest_path.read_text())

print(f'Format detected: {manifest.get("format", "unknown")}')
print(f'Chunking method: {manifest.get("chunking_method", "unknown")}')

print()
print('Chunk analysis:')
valid_json_count = 0
for i, chunk in enumerate(manifest.get('chunks', [])):
    size = chunk.get('char_count', 0)
    elem_range = chunk.get('element_range', None)
    range_str = f"[{elem_range[0]}-{elem_range[1]}]" if elem_range else "?"
    
    # Try to parse the chunk file
    chunk_path = chunks_dir / f'chunk_{i:04d}.json'
    try:
        chunk_data = json.loads(chunk_path.read_text())
        valid = "✓ valid JSON"
        valid_json_count += 1
        elem_count = len(chunk_data) if isinstance(chunk_data, list) else "N/A"
    except Exception as e:
        valid = f"✗ invalid: {e}"
        elem_count = "?"
    
    print(f'  [{i}] {size:>6} chars, elements {range_str}, count={elem_count}: {valid}')

total = len(manifest.get('chunks', []))
print()
if valid_json_count == total:
    print(f'✅ PASS: All {total} chunks are valid JSON arrays')
else:
    print(f'❌ FAIL: Only {valid_json_count}/{total} chunks are valid JSON')
PYTHON

# ============================================
# Test B: JSON Object Chunking  
# ============================================
echo ""
echo "=== Test B: JSON Object Chunking ==="
echo ""

OBJECT_FILE="$TEST_DIR/test-object.json"
echo "1. Generating JSON object (~80KB with 50 top-level keys)..."

python3 << PYTHON > "$OBJECT_FILE"
import json

data = {}
for i in range(50):
    key = f"section_{i:02d}"
    data[key] = {
        "title": f"Section {i} Title",
        "content": f"This is the detailed content for section {i}. " * 20,
        "subsections": [
            {"name": f"Subsection {i}.{j}", "data": list(range(50))}
            for j in range(5)
        ],
        "metadata": {
            "author": f"Author {i % 10}",
            "version": f"{i}.0.0"
        }
    }

print(json.dumps(data, indent=2))
PYTHON

OBJECT_SIZE=$(wc -c < "$OBJECT_FILE")
echo "   Created: $OBJECT_SIZE bytes"

echo ""
echo "2. Initializing RLM session..."
INIT_OUTPUT=$(python3 "$RLM_SCRIPT" init "$OBJECT_FILE" 2>&1)
STATE_PATH=$(echo "$INIT_OUTPUT" | grep -o "\.pi/rlm_state/[^/]*/state.pkl" | head -1)
echo "   State: $STATE_PATH"

echo ""
echo "3. Running smart_chunk() with target=15000 chars..."
python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec << 'PYTHON'
import json

chunks_dir = session_dir / 'chunks'
paths = smart_chunk(str(chunks_dir), target_size=15000, min_size=5000, max_size=30000)
print(f'Chunks created: {len(paths)}')

# Read and analyze manifest
manifest_path = chunks_dir / 'manifest.json'
manifest = json.loads(manifest_path.read_text())

print(f'Format detected: {manifest.get("format", "unknown")}')
print(f'Chunking method: {manifest.get("chunking_method", "unknown")}')

print()
print('Chunk analysis:')
valid_json_count = 0
all_keys = set()
for i, chunk in enumerate(manifest.get('chunks', [])):
    size = chunk.get('char_count', 0)
    keys = chunk.get('keys', [])
    key_range = chunk.get('key_range', None)
    range_str = f"[{key_range[0]}-{key_range[1]}]" if key_range else "?"
    
    # Try to parse the chunk file
    chunk_path = chunks_dir / f'chunk_{i:04d}.json'
    try:
        chunk_data = json.loads(chunk_path.read_text())
        valid = "✓ valid JSON"
        valid_json_count += 1
        chunk_keys = list(chunk_data.keys()) if isinstance(chunk_data, dict) else []
        all_keys.update(chunk_keys)
    except Exception as e:
        valid = f"✗ invalid: {e}"
        chunk_keys = []
    
    print(f'  [{i}] {size:>6} chars, keys {range_str}, count={len(chunk_keys)}: {valid}')

total = len(manifest.get('chunks', []))
print()
if valid_json_count == total:
    print(f'✅ PASS: All {total} chunks are valid JSON objects')
    print(f'   Total unique keys across chunks: {len(all_keys)}')
else:
    print(f'❌ FAIL: Only {valid_json_count}/{total} chunks are valid JSON')
PYTHON

echo ""
echo "=== Test Complete ==="
echo "Test directory: $TEST_DIR"
