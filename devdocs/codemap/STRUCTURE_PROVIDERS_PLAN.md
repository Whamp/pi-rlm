# Structure Providers Implementation Plan

> **Branch**: `feat/codemap-integration-v2`  
> **Created**: 2026-01-22  
> **Status**: Ready for implementation

## Context

pi-rlm currently uses codemap for one thing: extracting symbol boundaries to avoid splitting code mid-function during smart chunking. This plan extends that to a **generalized structure provider pattern** where codemap is the first implementation, but the architecture supports Python AST, legal documents, and other structured content.

See `devdocs/codemap/CODEMAP_INTEGRATION_V2_BRAINSTORM.md` for full exploration.

---

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| File organization | `providers/` directory | Cleaner separation, easier to extend |
| Python support | Build with stdlib `ast` | Zero deps, fast, primary use case |
| TS/JS/Rust/C++ | Integrate codemap CLI | Tree-sitter is complex, codemap is mature |
| Legal/generic | Build with regex patterns | Domain-specific, codemap won't help |
| Multi-file context | Explore (see below) | Opportunity, not blocking |
| Caching | Defer decision | Explore during implementation |

---

## Architecture

### Directory Structure

```
skills/rlm/scripts/
├── rlm_repl.py                 # Core REPL (import providers)
└── providers/
    ├── __init__.py             # Registry, auto-detection
    ├── base.py                 # StructureProvider protocol
    ├── codemap.py              # Codemap CLI wrapper
    ├── python_ast.py           # Python stdlib AST
    ├── markdown.py             # Heading-based (extract from rlm_repl.py)
    └── generic.py              # Regex fallback
```

### Provider Protocol

```python
# providers/base.py
from typing import Protocol, List, Dict, Optional, Tuple

class StructureProvider(Protocol):
    """Interface for document structure analysis."""
    
    name: str
    extensions: List[str]  # File extensions this provider handles
    
    def get_structure(self, content: str, path: str) -> List[Dict]:
        """Return symbols/sections with line ranges."""
        ...
    
    def get_boundaries(self, content: str, path: str) -> List[Tuple[int, int]]:
        """Return safe chunk boundaries (start_line, end_line)."""
        ...
    
    def find_refs(self, content: str, term: str) -> List[Dict]:
        """Find references to a term within content."""
        ...
    
    def get_deps(self, content: str, path: str) -> Dict:
        """Extract dependencies/imports/references."""
        ...
```

### Registry

```python
# providers/__init__.py
from pathlib import Path
from typing import Optional
from .base import StructureProvider

_PROVIDERS: Dict[str, StructureProvider] = {}

def register(provider: StructureProvider) -> None:
    _PROVIDERS[provider.name] = provider

def get_provider(path: str, preferred: str = None) -> Optional[StructureProvider]:
    """Auto-select provider based on file extension."""
    if preferred and preferred in _PROVIDERS:
        return _PROVIDERS[preferred]
    
    ext = Path(path).suffix.lower()
    for provider in _PROVIDERS.values():
        if ext in provider.extensions:
            return provider
    
    return _PROVIDERS.get('generic')

def available_providers() -> List[str]:
    return list(_PROVIDERS.keys())
```

---

## Implementation Phases

### Phase 1: Foundation (This PR)

**Goal**: Establish provider architecture, extract existing code, add Python AST.

#### 1.1 Create provider directory and base protocol
- [ ] Create `skills/rlm/scripts/providers/`
- [ ] Create `providers/base.py` with `StructureProvider` protocol
- [ ] Create `providers/__init__.py` with registry

#### 1.2 Extract markdown provider
- [ ] Move `_find_header_boundaries()` → `providers/markdown.py`
- [ ] Move `_chunk_markdown()` logic → use provider
- [ ] Implement `MarkdownProvider` class

#### 1.3 Extract codemap provider
- [ ] Move `_detect_codemap()` → `providers/codemap.py`
- [ ] Move `_extract_symbol_boundaries()` → provider
- [ ] Move `_chunk_code()` logic → use provider
- [ ] Implement `CodemapProvider` class

#### 1.4 Add Python AST provider
- [ ] Create `providers/python_ast.py`
- [ ] Implement `get_structure()` using stdlib `ast`
- [ ] Implement `get_boundaries()` for function/class ranges
- [ ] Implement `find_refs()` using AST name visitor
- [ ] Implement `get_deps()` parsing import statements

#### 1.5 Add generic fallback provider
- [ ] Create `providers/generic.py`
- [ ] Paragraph-based boundaries (existing `_chunk_text`)
- [ ] Simple regex `find_refs()`

#### 1.6 Update rlm_repl.py
- [ ] Import provider registry
- [ ] Update `_smart_chunk_impl()` to use providers
- [ ] Add new REPL helpers: `get_structure()`, `find_refs()`, `get_deps()`

#### 1.7 Tests
- [ ] Unit tests for each provider
- [ ] Integration test: smart_chunk uses correct provider by extension

**Deliverable**: Same functionality, cleaner architecture, plus Python AST support.

---

### Phase 2: Enhanced Codemap Integration

**Goal**: Expose full codemap capabilities through provider.

#### 2.1 Wrap additional codemap commands
- [ ] `get_call_graph(symbol, depth)` → `codemap call-graph`
- [ ] `get_callers(symbol)` → `codemap callers`
- [ ] `get_reverse_deps(path)` → `codemap deps --reverse`
- [ ] `get_circular_deps()` → `codemap deps --circular`

#### 2.2 Add REPL helpers
- [ ] `call_graph(symbol)` - returns handle with call tree
- [ ] `callers(symbol)` - returns handle with caller list
- [ ] `deps(path)` - returns dependency tree
- [ ] `circular_deps()` - returns cycles if any

#### 2.3 Update SKILL.md
- [ ] Document new structure helpers
- [ ] Add examples for code analysis workflows
- [ ] Add "when to use" guidance

**Deliverable**: Full codemap power accessible from REPL.

---

### Phase 3: Multi-File Context (Exploration)

**Goal**: Support loading directory trees as unified context.

#### 3.1 Design exploration
- [ ] Define "meta-file" format (concatenated with markers? structured?)
- [ ] Decide: eager load all files vs lazy load on demand
- [ ] Consider: how does `grep()` work across files?
- [ ] Consider: how do chunk boundaries respect file boundaries?

#### 3.2 Implementation (if pursued)
- [ ] `init_directory(path, pattern="**/*")` command
- [ ] File boundary markers in concatenated content
- [ ] Provider coordination across multiple file types
- [ ] Manifest includes per-file metadata

#### 3.3 Alternative: Agent-driven approach
- [ ] Document pattern: agent uses `bash` to list files, loads individually
- [ ] Helper: `load_additional(path)` to add file to context
- [ ] May be sufficient without architectural changes

**Deliverable**: Decision on approach + implementation or documented pattern.

---

### Phase 4: Advanced Chunking Strategies

**Goal**: Leverage structure for smarter chunking.

#### 4.1 Dependency-aware chunking
- [ ] `chunk_by_module()` - group files by import clusters
- [ ] Use provider's `get_deps()` to build graph
- [ ] Tarjan's algorithm for strongly connected components

#### 4.2 Execution-path chunking
- [ ] `chunk_execution_path(entry_point)` 
- [ ] Follow call graph from entry
- [ ] Each chunk is one "step" in execution

#### 4.3 Impact-radius chunking
- [ ] `chunk_impact_radius(symbol)`
- [ ] All files that reference the symbol
- [ ] Grouped by: direct, transitive, type-only

**Deliverable**: New chunking strategies for code analysis.

---

### Phase 5: Caching (If Needed)

**Goal**: Avoid re-parsing on repeated queries.

#### 5.1 Evaluate need
- [ ] Benchmark: Python AST parsing time for large files
- [ ] Benchmark: Codemap CLI overhead
- [ ] Determine if caching provides meaningful speedup

#### 5.2 Implementation (if pursued)
- [ ] Cache structure by (path, mtime) key
- [ ] Store in session directory alongside state.pkl
- [ ] Invalidate on file change
- [ ] Optional: share cache across sessions

**Deliverable**: Decision + implementation if beneficial.

---

## Open Questions

### Multi-File Context Options

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| **A: Meta-file** | Concatenate with `=== FILE: path ===` markers | Simple, grep works | Large memory, all-or-nothing |
| **B: Lazy load** | Index files, load on demand | Memory efficient | Grep requires iteration |
| **C: Agent-driven** | Agent decides what to load | Flexible, no arch change | Relies on agent skill |
| **D: Hybrid** | Index + selective eager load | Balance | More complexity |

**Current lean**: Start with C (agent-driven), upgrade to A or D if pain emerges.

### Caching Strategy Options

| Option | Approach | When to Use |
|--------|----------|-------------|
| **No cache** | Re-parse each time | If parsing is fast enough |
| **Session cache** | Cache in session dir | Multi-query sessions |
| **Global cache** | Cache in ~/.pi/cache | Cross-session, rare |

**Current lean**: Start with no cache, measure, decide.

---

## Success Criteria

### Phase 1 Complete When:
- [ ] `codemap` provider works for TS/JS (existing behavior preserved)
- [ ] `python_ast` provider works for `.py` files
- [ ] `markdown` provider works for `.md` files
- [ ] `generic` provider handles everything else
- [ ] `smart_chunk()` auto-selects correct provider
- [ ] New REPL helpers available: `get_structure()`, `find_refs()`, `get_deps()`
- [ ] All existing tests pass
- [ ] New provider unit tests pass

### Phase 2 Complete When:
- [ ] All codemap commands accessible from REPL
- [ ] SKILL.md documents new capabilities
- [ ] Example workflow in docs

---

## Files to Create/Modify

### Create
```
skills/rlm/scripts/providers/__init__.py
skills/rlm/scripts/providers/base.py
skills/rlm/scripts/providers/codemap.py
skills/rlm/scripts/providers/python_ast.py
skills/rlm/scripts/providers/markdown.py
skills/rlm/scripts/providers/generic.py
skills/rlm/tests/test_providers.py
```

### Modify
```
skills/rlm/scripts/rlm_repl.py     # Import providers, add helpers
skills/rlm/SKILL.md                # Document new features
```

---

## Getting Started (Next Session)

1. **Read this plan** and `CODEMAP_INTEGRATION_V2_BRAINSTORM.md`
2. **Start with Phase 1.1-1.2**: Create directory, extract markdown provider
3. **Test extraction**: Ensure existing markdown chunking still works
4. **Continue sequentially** through Phase 1

```bash
# Verify current state
cd ~/projects/pi-rlm
git branch  # Should be feat/codemap-integration-v2
git status

# Run existing tests to establish baseline
cd skills/rlm && python -m pytest tests/ -v
```
