#!/usr/bin/env python3
"""Example: Smart chunking with different content types.

This example demonstrates smart_chunk() behavior with:
- Markdown content (splits at headers)
- JSON arrays (splits at element boundaries)
- Plain text (splits at paragraphs)

Run from the skills/rlm directory:
    python3 examples/02_smart_chunking.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

RLM_REPL = Path(__file__).parent.parent / "scripts" / "rlm_repl.py"


def run_exec(state_path: Path, code: str, tmpdir: Path) -> str:
    """Run exec command and return stdout."""
    result = subprocess.run(
        ["python3", str(RLM_REPL), "--state", str(state_path), "exec", "-c", code],
        capture_output=True, text=True, cwd=tmpdir
    )
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
        print("Example 2: Smart Chunking")
        print("=" * 60)

        # --- Markdown Example ---
        print("\n" + "-" * 40)
        print("[1] MARKDOWN CHUNKING")
        print("-" * 40)

        markdown_content = """# API Documentation

Welcome to our API documentation.

## Authentication

All requests require a Bearer token in the Authorization header.

### Getting a Token

Call POST /auth/login with your credentials.

### Token Refresh

Tokens expire after 24 hours. Use POST /auth/refresh.

## Endpoints

### Users API

#### GET /users

Returns a list of all users.

#### POST /users

Creates a new user.

### Products API

#### GET /products

Returns all products.

## Error Handling

All errors return a standard format.
"""

        state_path = init_session(markdown_content, "api.md", tmpdir)
        print(f"\nInitialized session for api.md")

        output = run_exec(state_path, """
import json
paths = smart_chunk(str(session_dir / 'chunks'), target_size=200, min_size=50, max_size=500)
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())
print(f'Format: {manifest["format"]}')
print(f'Method: {manifest["chunking_method"]}')
print(f'Chunks created: {len(manifest["chunks"])}')
print()
for i, chunk in enumerate(manifest['chunks'][:3]):
    boundaries = chunk.get('boundaries', [])
    if boundaries:
        first = boundaries[0]
        print(f'Chunk {i}: starts at "{first.get("text", "?")}" (level {first.get("level", "?")})')
""", tmpdir)
        print(output)

        # --- JSON Array Example ---
        print("\n" + "-" * 40)
        print("[2] JSON ARRAY CHUNKING")
        print("-" * 40)

        json_content = json.dumps([
            {"id": i, "name": f"User {i}", "email": f"user{i}@example.com"}
            for i in range(50)
        ], indent=2)

        state_path = init_session(json_content, "users.json", tmpdir)
        print(f"\nInitialized session for users.json (50 users)")

        output = run_exec(state_path, """
import json
paths = smart_chunk(str(session_dir / 'chunks'), target_size=500, min_size=100, max_size=1000)
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())
print(f'Format: {manifest["format"]}')
print(f'Method: {manifest["chunking_method"]}')
print(f'JSON chunked: {manifest.get("json_chunked", False)}')
print(f'Chunks created: {len(manifest["chunks"])}')
print()
for chunk in manifest['chunks'][:3]:
    if 'element_range' in chunk:
        r = chunk['element_range']
        print(f'{chunk["id"]}: elements [{r[0]}..{r[1]}]')
""", tmpdir)
        print(output)

        # --- JSON Object Example ---
        print("\n" + "-" * 40)
        print("[3] JSON OBJECT CHUNKING")
        print("-" * 40)

        json_obj_content = json.dumps({
            f"section_{i}": {
                "title": f"Section {i}",
                "content": f"This is the content for section {i}. " * 5
            }
            for i in range(20)
        }, indent=2)

        state_path = init_session(json_obj_content, "config.json", tmpdir)
        print(f"\nInitialized session for config.json (20 sections)")

        output = run_exec(state_path, """
import json
paths = smart_chunk(str(session_dir / 'chunks'), target_size=500, min_size=100, max_size=1000)
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())
print(f'Format: {manifest["format"]}')
print(f'Method: {manifest["chunking_method"]}')
print(f'JSON chunked: {manifest.get("json_chunked", False)}')
print(f'Chunks created: {len(manifest["chunks"])}')
print()
for chunk in manifest['chunks'][:3]:
    if 'keys' in chunk:
        print(f'{chunk["id"]}: keys {chunk["keys"][:3]}...')
""", tmpdir)
        print(output)

        # --- Plain Text Example ---
        print("\n" + "-" * 40)
        print("[4] PLAIN TEXT CHUNKING")
        print("-" * 40)

        text_content = "\n\n".join([
            f"Paragraph {i}: " + "This is some example text content. " * 10
            for i in range(30)
        ])

        state_path = init_session(text_content, "document.txt", tmpdir)
        print(f"\nInitialized session for document.txt (30 paragraphs)")

        output = run_exec(state_path, """
import json
paths = smart_chunk(str(session_dir / 'chunks'), target_size=1000, min_size=200, max_size=2000)
manifest = json.loads((session_dir / 'chunks' / 'manifest.json').read_text())
print(f'Format: {manifest["format"]}')
print(f'Method: {manifest["chunking_method"]}')
print(f'Chunks created: {len(manifest["chunks"])}')
""", tmpdir)
        print(output)

        print("\n" + "=" * 60)
        print("Example completed!")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
