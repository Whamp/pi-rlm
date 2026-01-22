#!/bin/bash
# RLM Experience Test: Smart Markdown Chunking
#
# Tests that markdown files are chunked at header boundaries,
# preserving document structure.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RLM_SCRIPT="$HOME/projects/pi-rlm/skills/rlm/scripts/rlm_repl.py"
TEST_DIR="/tmp/rlm-markdown-test-$$"
MD_FILE="$TEST_DIR/test-doc.md"

echo "=== RLM Smart Markdown Chunking Test ==="
echo ""

mkdir -p "$TEST_DIR"

# Create a structured markdown document (~100KB with distinct sections)
echo "1. Generating test markdown document..."

cat > "$MD_FILE" << 'MARKDOWN'
# Main Document Title

This is the introduction to our comprehensive guide. It provides an overview
of all the topics we'll cover in this document.

## Chapter 1: Getting Started

Welcome to Chapter 1. This section covers the basics of getting started
with our system. We'll walk through installation, configuration, and
your first steps.

### 1.1 Installation

To install the software, follow these steps:

1. Download the installer from our website
2. Run the installer with administrator privileges
3. Follow the on-screen instructions
4. Verify the installation by running `myapp --version`

Here's some additional context about the installation process that makes
this section longer. We want to ensure that each section has enough content
to be meaningful when chunked separately.

MARKDOWN

# Add more content to each section to make chunking meaningful
for i in {2..10}; do
    cat >> "$MD_FILE" << MARKDOWN

## Chapter $i: Topic Number $i

This is the content for Chapter $i. It contains detailed information about
topic $i and related concepts. We'll explore various aspects of this topic
including theoretical foundations, practical applications, and advanced
techniques.

### ${i}.1 Subtopic A

The first subtopic of Chapter $i covers fundamental concepts. Here we discuss
the core principles that underpin everything else in this chapter. Understanding
these foundations is crucial for mastering the material.

$(for j in {1..20}; do echo "Paragraph $j of section ${i}.1: Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris."; done)

### ${i}.2 Subtopic B

The second subtopic explores more advanced concepts. Building on the foundations
from the previous section, we now delve into more complex territory.

$(for j in {1..15}; do echo "Paragraph $j of section ${i}.2: Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident."; done)

### ${i}.3 Subtopic C

The third subtopic wraps up the chapter with practical examples and exercises.
Apply what you've learned by working through these hands-on activities.

$(for j in {1..10}; do echo "Example $j: Here is a practical example demonstrating concept $j from this chapter. Follow along to solidify your understanding."; done)

MARKDOWN
done

MD_SIZE=$(wc -c < "$MD_FILE")
MD_LINES=$(wc -l < "$MD_FILE")
echo "   Created: $MD_SIZE bytes, $MD_LINES lines"

echo ""
echo "2. Initializing RLM session..."
INIT_OUTPUT=$(python3 "$RLM_SCRIPT" init "$MD_FILE" 2>&1)
STATE_PATH=$(echo "$INIT_OUTPUT" | grep -o "\.pi/rlm_state/[^/]*/state.pkl" | head -1)
echo "   State: $STATE_PATH"

echo ""
echo "3. Running smart_chunk() with target=30000 chars..."
python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec << 'PYTHON'
import json

chunks_dir = session_dir / 'chunks'
paths = smart_chunk(str(chunks_dir), target_size=30000, min_size=10000, max_size=50000)
print(f'Chunks created: {len(paths)}')

# Read and analyze manifest
manifest_path = chunks_dir / 'manifest.json'
manifest = json.loads(manifest_path.read_text())

print(f'Format detected: {manifest.get("format", "unknown")}')
print(f'Chunking method: {manifest.get("chunking_method", "unknown")}')

print()
print('Chunk analysis:')
for i, chunk in enumerate(manifest.get('chunks', [])):
    size = chunk.get('char_count', 0)
    preview = chunk.get('preview', '')[:60].replace('\n', ' ')
    boundaries = chunk.get('boundaries', [])
    
    # boundaries is a list of heading objects
    headers = [b.get('text', '') for b in boundaries if b.get('type') == 'heading']
    header_info = f", headers: {len(headers)}" if headers else ""
    
    print(f'  [{i}] {size:>6} chars{header_info}: {preview}...')

# Validate: each chunk should have header boundaries
print()
print('Validation:')
chunks_with_headers = 0
for i, chunk in enumerate(manifest.get('chunks', [])):
    boundaries = chunk.get('boundaries', [])
    headers = [b for b in boundaries if b.get('type') == 'heading']
    if headers:
        chunks_with_headers += 1
        first_header = headers[0].get('text', 'none')
        print(f'  Chunk {i}: starts with "{first_header}"')

total = len(manifest.get('chunks', []))
if chunks_with_headers >= total - 1:  # Allow first chunk to not start with header
    print(f'  ✅ PASS: {chunks_with_headers}/{total} chunks have header boundaries')
else:
    print(f'  ⚠️  WARN: Only {chunks_with_headers}/{total} chunks have header boundaries')
PYTHON

echo ""
echo "4. Verifying chunks are readable..."
CHUNKS_DIR="$(dirname "$STATE_PATH")/chunks"
for chunk_file in "$CHUNKS_DIR"/chunk_*.md; do
    if [ -f "$chunk_file" ]; then
        chunk_size=$(wc -c < "$chunk_file")
        chunk_name=$(basename "$chunk_file")
        first_line=$(head -1 "$chunk_file")
        echo "   $chunk_name: $chunk_size bytes - starts with: ${first_line:0:50}"
    fi
done

echo ""
echo "=== Test Complete ==="
echo "Test directory: $TEST_DIR"
