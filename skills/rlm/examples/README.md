# RLM Examples

These examples demonstrate the key features of the RLM (Recursive Language Model) workflow.

## Running Examples

All examples should be run from the `skills/rlm` directory:

```bash
cd skills/rlm
python3 examples/01_basic_workflow.py
```

## Examples

### 01_basic_workflow.py
**Basic RLM Workflow**

Demonstrates the core workflow:
- Initialize a session with content
- Use grep to find patterns  
- Write chunks to disk
- Set a final answer
- Retrieve the answer via CLI

### 02_smart_chunking.py
**Smart Chunking with Different Content Types**

Shows how `smart_chunk()` handles different formats:
- **Markdown**: Splits at header boundaries (## and ###)
- **JSON arrays**: Splits at element boundaries
- **JSON objects**: Splits by top-level keys
- **Plain text**: Splits at paragraph breaks

### 03_handle_system.py
**Handle-Based Search for Token Efficiency**

Demonstrates the handle system for exploring large content without loading it all into context:
- `grep()` returns handles, not raw data
- `count()` counts without expanding
- `expand()` materializes only what you need
- `filter_handle()` for server-side filtering
- `map_field()` for field extraction
- Handle chaining with `last_handle()`

### 04_depth_configuration.py
**Depth Configuration and Recursive State**

Shows how to configure recursive depth:
- `--max-depth N` for controlling recursion limit
- `--preserve-recursive-state` for debugging
- How depth affects `llm_query()` behavior
- Status command shows depth info

### 05_finalization.py
**Answer Finalization Workflow**

Demonstrates the finalization signal:
- `set_final_answer()` for marking results
- `has_final_answer()` and `get_final_answer()` helpers
- `get-final-answer` CLI command for external retrieval
- Only JSON-serializable values accepted

## Key Concepts

### Token Efficiency
The handle system (`grep()`, `expand()`, etc.) lets you explore large content without loading everything into the LLM context. Results are stored server-side and materialized only when needed.

### Content-Aware Chunking
`smart_chunk()` automatically detects content format and splits at natural boundaries:
- Markdown: Headers
- Code: Functions/classes (if codemap available)
- JSON: Array elements or object keys
- Text: Paragraphs

### Recursive Depth
Sub-LLMs can spawn their own sub-LLMs up to the configured depth limit (default: 3). This enables hierarchical processing of complex tasks.

### Answer Finalization
`set_final_answer()` marks a JSON-serializable value as the final result, which can be retrieved via the CLI for external tooling integration.
