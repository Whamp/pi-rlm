#!/bin/bash
# RLM Experience Test: Real Codebase Analysis
#
# Tests RLM against Classroom-Connect-V2 - a real production codebase
# with ~1.7MB of TypeScript code.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RLM_SCRIPT="$HOME/projects/pi-rlm/skills/rlm/scripts/rlm_repl.py"
CC_DIR="$HOME/projects/Classroom-Connect-V2"
TEST_DIR="/tmp/rlm-codebase-test-$$"

echo "=== RLM Real Codebase Analysis Test ==="
echo "Target: Classroom-Connect-V2"
echo ""

# Verify project exists
if [ ! -d "$CC_DIR" ]; then
    echo "ERROR: Classroom-Connect-V2 not found at $CC_DIR"
    exit 1
fi

mkdir -p "$TEST_DIR"

# Test 1: Services layer analysis
echo "=== Test 1: Services Layer Analysis ==="
echo ""

SERVICES_FILE="$TEST_DIR/services-bundle.ts"
echo "1. Bundling services layer..."
find "$CC_DIR/src/services" -name "*.ts" ! -path "*/node_modules/*" -exec cat {} \; > "$SERVICES_FILE" 2>/dev/null

SERVICES_SIZE=$(wc -c < "$SERVICES_FILE")
SERVICES_LINES=$(wc -l < "$SERVICES_FILE")
echo "   Size: $SERVICES_SIZE bytes, $SERVICES_LINES lines"

echo ""
echo "2. Initializing RLM session..."
INIT_OUTPUT=$(python3 "$RLM_SCRIPT" init "$SERVICES_FILE" 2>&1)
STATE_PATH=$(echo "$INIT_OUTPUT" | grep -o "\.pi/rlm_state/[^/]*/state.pkl" | head -1)
echo "   State: $STATE_PATH"

echo ""
echo "3. Pattern analysis..."

# Run pattern detection
python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec -c "
patterns = {
    'Supabase queries': r'\\.from\\(',
    'Error handling': r'throw new Error',
    'Try-catch blocks': r'try\\s*\\{',
    'Async functions': r'async\\s+function|async\\s*\\(',
    'Type assertions': r'as\\s+[A-Z]',
    'Console logs': r'console\\.(log|warn|error)',
    'TODO comments': r'TODO|FIXME|HACK',
}

print('Pattern counts:')
for name, pattern in patterns.items():
    try:
        result = grep(pattern)
        cnt = count(result)  # Works with full stub now!
        print(f'  {name}: {cnt}')
    except Exception as e:
        print(f'  {name}: ERROR - {e}')
"

# Test 2: Full source analysis (if time permits)
echo ""
echo "=== Test 2: Source Components Analysis ==="
echo ""

COMPONENTS_FILE="$TEST_DIR/components-bundle.tsx"
echo "1. Bundling React components..."
find "$CC_DIR/src/components" -name "*.tsx" ! -path "*/node_modules/*" -exec cat {} \; > "$COMPONENTS_FILE" 2>/dev/null

COMP_SIZE=$(wc -c < "$COMPONENTS_FILE")
COMP_LINES=$(wc -l < "$COMPONENTS_FILE")
echo "   Size: $COMP_SIZE bytes, $COMP_LINES lines"

echo ""
echo "2. Initializing RLM session..."
INIT_OUTPUT=$(python3 "$RLM_SCRIPT" init "$COMPONENTS_FILE" 2>&1)
STATE_PATH=$(echo "$INIT_OUTPUT" | grep -o "\.pi/rlm_state/[^/]*/state.pkl" | head -1)
echo "   State: $STATE_PATH"

echo ""
echo "3. Component pattern analysis..."

python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec -c "
patterns = {
    'useState hooks': r'useState\\s*[<(]',
    'useEffect hooks': r'useEffect\\s*\\(',
    'useCallback hooks': r'useCallback\\s*\\(',
    'useMemo hooks': r'useMemo\\s*\\(',
    'Custom hooks used': r'use[A-Z][a-zA-Z]+\\(',
    'Event handlers': r'on[A-Z][a-zA-Z]*=',
    'Conditional renders': r'\\?\\s*<|&&\\s*<',
    'Key props': r'key=',
}

print('React pattern counts:')
for name, pattern in patterns.items():
    try:
        result = grep(pattern)
        cnt = count(result)  # Works with full stub now!
        print(f'  {name}: {cnt}')
    except Exception as e:
        print(f'  {name}: ERROR - {e}')
"

# Test 3: Smart chunking test
echo ""
echo "=== Test 3: Smart Chunking Quality ==="
echo ""

echo "Testing smart chunk on services bundle..."
python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec -c "
import json
chunks_dir = session_dir / 'chunks'
paths = smart_chunk(str(chunks_dir), target_size=50000)
print(f'Chunks created: {len(paths)}')

# Read manifest
manifest_path = chunks_dir / 'manifest.json'
if manifest_path.exists():
    manifest = json.loads(manifest_path.read_text())
    print(f'Format detected: {manifest.get(\"format\", \"unknown\")}')
    print(f'Chunking method: {manifest.get(\"chunking_method\", \"unknown\")}')
    
    # Show chunk previews
    print()
    print('Chunk previews:')
    for i, chunk in enumerate(manifest.get('chunks', [])[:5]):
        preview = chunk.get('preview', '')[:60].replace('\\n', ' ')
        size = chunk.get('char_count', 0)
        print(f'  [{i}] {size:>6} chars: {preview}...')
"

echo ""
echo "=== Summary ==="
echo "Services bundle: $SERVICES_SIZE bytes"
echo "Components bundle: $COMP_SIZE bytes"
echo "Test directory: $TEST_DIR"
echo ""
echo "To explore interactively:"
echo "  python3 $RLM_SCRIPT --state $STATE_PATH exec -c 'print(handles())'"
