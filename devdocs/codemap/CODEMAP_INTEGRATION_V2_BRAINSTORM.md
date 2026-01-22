# Structure-Aware RLM: Codemap Integration v2

## Framing: Structure Providers, Not Just Codemap

The goal isn't "integrate codemap"—it's **structure-aware document processing** where codemap is the first (and currently best) implementation for code.

```
┌─────────────────────────────────────────────────────────────────┐
│                    RLM Core (Document-Agnostic)                  │
│  REPL • grep • llm_query • handles • chunking orchestration     │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │ Structure Provider │
                    │    Interface       │
                    └─────────┬─────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   codemap    │      │   Python     │      │   Generic    │
│   (external) │      │   (native)   │      │   (regex)    │
│              │      │              │      │              │
│ TS/JS/Rust   │      │ Python AST   │      │ Legal/Docs   │
│ C++ via CLI  │      │ via stdlib   │      │ via patterns │
└──────────────┘      └──────────────┘      └──────────────┘
```

---

## The Structure Provider Interface

Any provider should expose these capabilities:

```python
class StructureProvider(Protocol):
    """Abstract interface for document structure analysis."""
    
    def get_structure(self, path: str) -> List[StructureNode]:
        """
        Return hierarchical structure of the document.
        
        For code: functions, classes, methods
        For legal: articles, sections, clauses
        For papers: sections, theorems, figures
        """
    
    def get_boundaries(self, path: str) -> List[Tuple[int, int]]:
        """
        Return safe chunk boundaries (line ranges).
        Don't split inside a function/clause/section.
        """
    
    def find_refs(self, term: str) -> List[Reference]:
        """
        Find all references to a term/symbol.
        
        For code: where is login() called?
        For legal: where is "Force Majeure" referenced?
        For papers: where is [23] cited?
        """
    
    def get_deps(self, node: str) -> DependencyTree:
        """
        What does this node depend on?
        
        For code: imports, calls
        For legal: incorporated exhibits, referenced sections
        For papers: cited works, prerequisite sections
        """
    
    def get_definition(self, term: str) -> Optional[Definition]:
        """
        Where is this term defined?
        
        For code: function/class definition location
        For legal: "X means..." clause
        For papers: first mention or glossary entry
        """
```

---

## Build vs. Integrate: The Tradeoff

### Option A: Use Codemap CLI (External Tool)

```python
def get_structure_codemap(path: str) -> List[Dict]:
    result = subprocess.run(['codemap', path, '-o', 'json'], ...)
    return json.loads(result.stdout)['files'][0]['symbols']
```

| Pros | Cons |
|------|------|
| Best-in-class for TS/JS/Rust/C++ | Subprocess overhead (~100-500ms) |
| Maintained separately, gets updates | External dependency |
| Caching, tree-sitter parsing done | Can't extend for non-code |
| Less code in pi-rlm | Limited to codemap's languages |

### Option B: Reimplement Ideas in Python (Native)

```python
def get_structure_python(path: str) -> List[Dict]:
    import ast
    tree = ast.parse(Path(path).read_text())
    return [{'name': node.name, 'kind': type(node).__name__, 
             'lines': [node.lineno, node.end_lineno]}
            for node in ast.walk(tree) 
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
```

| Pros | Cons |
|------|------|
| No subprocess overhead | Must reimplement parsing |
| Can generalize to any document type | Python AST only covers Python |
| Single codebase, full control | Won't match codemap quality for TS/JS |
| Integrates with loaded `content` | More maintenance burden |

### Option C: Hybrid (Recommended)

```python
def get_structure(path: str, provider: str = "auto") -> List[Dict]:
    """
    Auto-select best provider based on file type and availability.
    """
    ext = Path(path).suffix
    
    if provider == "auto":
        if ext in ('.ts', '.js', '.tsx', '.jsx') and _detect_codemap():
            provider = "codemap"
        elif ext == '.py':
            provider = "python_ast"
        elif ext in ('.md', '.markdown'):
            provider = "markdown"
        else:
            provider = "generic"
    
    providers = {
        "codemap": _get_structure_codemap,
        "python_ast": _get_structure_python_ast,
        "markdown": _get_structure_markdown,
        "generic": _get_structure_generic,  # Regex-based
    }
    
    return providers[provider](path)
```

**Benefits of hybrid**:
- Use codemap when it's best (TS/JS with codemap installed)
- Use Python stdlib for Python (no external deps, fast)
- Use existing markdown logic for docs
- Fall back to generic patterns for everything else
- Path to legal/research paper support without codemap changes

---

## What Ideas Can Be Extracted from Codemap?

Codemap's value isn't just the tool—it's the **patterns**:

### 1. Symbol Boundary Detection

**Codemap approach**: Tree-sitter AST parsing → function/class line ranges

**Extractable to Python**:
```python
import ast

def get_python_boundaries(content: str) -> List[Tuple[int, int, str, str]]:
    """Return (start_line, end_line, kind, name) for Python."""
    tree = ast.parse(content)
    boundaries = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            boundaries.append((node.lineno, node.end_lineno, 'function', node.name))
        elif isinstance(node, ast.ClassDef):
            boundaries.append((node.lineno, node.end_lineno, 'class', node.name))
    return sorted(boundaries)
```

**Generalizable to legal**:
```python
import re

def get_legal_boundaries(content: str) -> List[Tuple[int, int, str, str]]:
    """Detect Article/Section/Clause boundaries in legal docs."""
    patterns = [
        (r'^Article\s+([IVXLC\d]+)', 'article'),
        (r'^Section\s+(\d+(?:\.\d+)*)', 'section'),
        (r'^\s*\(([a-z]|\d+)\)\s+', 'clause'),
        (r'^EXHIBIT\s+([A-Z])', 'exhibit'),
    ]
    # ... implementation
```

### 2. Reference Tracking

**Codemap approach**: TypeScript language server → find all references

**Extractable patterns**:
```python
def find_refs_generic(content: str, term: str) -> List[Dict]:
    """Find references using regex (works for any document)."""
    refs = []
    for i, line in enumerate(content.split('\n'), 1):
        for match in re.finditer(re.escape(term), line):
            refs.append({
                'line': i,
                'column': match.start(),
                'context': line.strip()[:100]
            })
    return refs

def find_refs_python(content: str, term: str) -> List[Dict]:
    """Find references using Python AST (more precise for Python)."""
    import ast
    tree = ast.parse(content)
    refs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == term:
            refs.append({'line': node.lineno, 'column': node.col_offset, 'kind': 'name'})
        elif isinstance(node, ast.Attribute) and node.attr == term:
            refs.append({'line': node.lineno, 'column': node.col_offset, 'kind': 'attribute'})
    return refs

def find_refs_legal(content: str, term: str) -> List[Dict]:
    """Find legal cross-references."""
    patterns = [
        rf'(?:pursuant to|under|per)\s+{re.escape(term)}',
        rf'(?:as defined in|see)\s+{re.escape(term)}',
        rf'{re.escape(term)}(?:\s+hereof|\s+herein)?',
    ]
    # ... implementation
```

### 3. Dependency/Import Tracking

**Codemap approach**: Parse import statements, resolve paths

**Extractable to Python**:
```python
import ast

def get_python_deps(path: str) -> Dict:
    """Extract Python imports."""
    content = Path(path).read_text()
    tree = ast.parse(content)
    
    deps = {'imports': [], 'from_imports': []}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            deps['imports'].extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            deps['from_imports'].append({
                'module': node.module,
                'names': [alias.name for alias in node.names]
            })
    return deps
```

**Generalizable to legal**:
```python
def get_legal_deps(content: str) -> Dict:
    """Extract legal document dependencies."""
    deps = {
        'incorporates': [],  # "incorporated by reference"
        'amends': [],        # "as amended by"
        'references': [],    # "pursuant to [External Agreement]"
    }
    
    # Pattern matching for legal cross-references
    for match in re.finditer(r'incorporated (?:herein )?by reference[:\s]+([^.]+)', content):
        deps['incorporates'].append(match.group(1).strip())
    
    # ... more patterns
    return deps
```

### 4. Token Budget / Detail Levels

**Codemap approach**: Progressive detail reduction (full → standard → compact → minimal → outline)

**Directly extractable**:
```python
def render_at_detail_level(symbols: List[Dict], level: str) -> str:
    """Render symbols at specified detail level."""
    if level == 'full':
        return render_full(symbols)  # Signatures + comments + body hints
    elif level == 'standard':
        return render_standard(symbols)  # Signatures + truncated comments
    elif level == 'compact':
        return render_compact(symbols)  # Signatures only
    elif level == 'minimal':
        return render_minimal(symbols)  # Names only
    elif level == 'outline':
        return render_outline(symbols)  # Just file + line range

def fit_to_budget(content: str, symbols: List[Dict], budget_tokens: int) -> str:
    """Progressively reduce detail until output fits budget."""
    levels = ['full', 'standard', 'compact', 'minimal', 'outline']
    
    for level in levels:
        output = render_at_detail_level(symbols, level)
        if estimate_tokens(output) <= budget_tokens:
            return output
    
    return render_outline(symbols)  # Last resort
```

---

## Proposed Architecture

### Layer 1: Provider Implementations

```
skills/rlm/scripts/
├── rlm_repl.py              # Core REPL (unchanged)
├── providers/
│   ├── __init__.py          # Provider registry
│   ├── base.py              # StructureProvider protocol
│   ├── codemap.py           # Codemap CLI wrapper
│   ├── python_ast.py        # Python stdlib AST
│   ├── markdown.py          # Heading-based (exists, extract)
│   ├── generic.py           # Regex patterns
│   └── legal.py             # Legal document patterns (future)
```

### Layer 2: REPL Integration

New helpers injected into REPL environment:

```python
# Structure analysis (uses best available provider)
get_structure(path=None)          # Current file or specified path
get_boundaries(path=None)         # Safe chunk points

# Reference tracking
find_refs(term)                   # Where is term used?
find_definition(term)             # Where is term defined?

# Dependency analysis
get_deps(path=None)               # What does this import/reference?
get_reverse_deps(path=None)       # What imports/references this?

# Advanced (when provider supports)
get_call_graph(symbol, depth=3)   # Outgoing call tree
get_callers(symbol, depth=3)      # Incoming call tree
```

### Layer 3: Enhanced Chunking

```python
def smart_chunk(
    out_dir: str,
    target_size: int = 200_000,
    strategy: str = "auto",       # "structure", "deps", "sequential"
    provider: str = "auto",       # "codemap", "python_ast", etc.
) -> List[str]:
    """
    Strategy options:
    - "structure": Respect function/section boundaries (current)
    - "deps": Group by dependency clusters
    - "execution": Follow call graph from entry point
    - "sequential": Simple character-based (fallback)
    """
```

---

## Implementation Roadmap

### Phase 1: Provider Abstraction

1. **Extract existing markdown chunking** into `providers/markdown.py`
2. **Extract existing codemap integration** into `providers/codemap.py`
3. **Add Python AST provider** using stdlib `ast` module
4. **Create provider registry** with auto-detection

**Deliverable**: Same functionality, cleaner architecture

### Phase 2: Native Python Support

1. **Python AST boundaries** — chunking that respects function/class boundaries
2. **Python import tracking** — `get_deps()` for Python files
3. **Python reference finding** — basic `find_refs()` using AST

**Deliverable**: First-class Python support without codemap

### Phase 3: Codemap CLI Integration

1. **Wrap remaining codemap commands** — `deps`, `find-refs`, `call-graph`, `callers`
2. **Add to REPL helpers** — available when codemap detected
3. **Update SKILL.md** — document new capabilities

**Deliverable**: Full codemap power for TS/JS/Rust/C++

### Phase 4: Generic/Legal Patterns

1. **Section detection patterns** — Article/Section/Clause for legal
2. **Cross-reference extraction** — "pursuant to", "as defined in"
3. **Definition tracking** — "X means...", quoted terms

**Deliverable**: Legal document structure support

### Phase 5: Intelligent Routing

1. **Graph walker pattern** — follow references instead of blind chunking
2. **Multi-provider coordination** — mix codemap + Python AST in same session
3. **Execution tracing** — "how does X work?" queries

**Deliverable**: True graph-based navigation

---

## Decision: Build vs Integrate

| Capability | Recommendation | Rationale |
|------------|----------------|-----------|
| **Python structure** | Build (stdlib AST) | Zero deps, fast, covers primary use case |
| **TS/JS/Rust structure** | Integrate (codemap) | Tree-sitter is complex, codemap is mature |
| **Markdown structure** | Build (already have it) | Simple regex, already implemented |
| **Legal structure** | Build (regex patterns) | Domain-specific, codemap won't help |
| **Reference tracking** | Hybrid | AST for Python, codemap for TS/JS, regex fallback |
| **Call graphs** | Integrate (codemap) | Complex analysis, not worth reimplementing |
| **Token budgeting** | Build | Algorithm is simple, we control the output |

**Summary**: 
- **Build** the abstraction layer and Python/generic providers
- **Integrate** codemap for languages it excels at
- **Design** interfaces that work for both

---

## Quick Wins (This Branch)

### 1. Python AST Provider (No deps, immediate value)

```python
# providers/python_ast.py
import ast
from pathlib import Path

def get_python_structure(path: str) -> List[Dict]:
    """Extract Python symbols using stdlib."""
    content = Path(path).read_text()
    tree = ast.parse(content)
    
    symbols = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append({
                'name': node.name,
                'kind': 'function',
                'start_line': node.lineno,
                'end_line': node.end_lineno,
                'signature': _extract_signature(node),
            })
        elif isinstance(node, ast.ClassDef):
            symbols.append({
                'name': node.name,
                'kind': 'class', 
                'start_line': node.lineno,
                'end_line': node.end_lineno,
            })
    return sorted(symbols, key=lambda s: s['start_line'])
```

### 2. Provider Registry

```python
# providers/__init__.py
from pathlib import Path

PROVIDERS = {}

def register(name: str, extensions: List[str]):
    def decorator(fn):
        PROVIDERS[name] = {'fn': fn, 'extensions': extensions}
        return fn
    return decorator

def get_provider(path: str, preferred: str = None):
    ext = Path(path).suffix
    
    if preferred and preferred in PROVIDERS:
        return PROVIDERS[preferred]['fn']
    
    for name, info in PROVIDERS.items():
        if ext in info['extensions']:
            return info['fn']
    
    return PROVIDERS.get('generic', {}).get('fn')
```

### 3. REPL Helper Updates

```python
# In _make_helpers()
def get_structure(path: str = None) -> List[Dict]:
    """Get document structure (functions, classes, sections)."""
    target = path or context_ref.get('path')
    provider = get_provider(target)
    return provider(target) if provider else []

def find_refs_in_content(term: str) -> str:
    """Find all references to a term in loaded content."""
    hits = grep_raw(rf'\b{re.escape(term)}\b', max_matches=100)
    return _store_handle(hits)
```

---

## Open Questions

1. **Provider location**: In `rlm_repl.py` or separate `providers/` directory?
   - Separate is cleaner but adds import complexity
   - Inline keeps single-file simplicity

2. **Lazy loading**: Import providers only when needed?
   - Avoids loading tree-sitter for simple text processing
   - But adds complexity

3. **Caching**: Cache structure analysis across REPL invocations?
   - Codemap has its own cache
   - Python AST is fast enough to not need it
   - But multi-file projects might benefit

4. **Multi-file context**: Current RLM loads one file. Provider pattern assumes single file.
   - For codebase analysis, need to handle multiple files
   - Maybe `get_structure("src/**/*.py")` pattern?

---

## Next Steps

1. **Agree on architecture** — Provider abstraction? Inline or separate?
2. **Implement Python AST provider** — Quick win, no deps
3. **Extract markdown chunking** — Already exists, just reorganize
4. **Wrap codemap CLI** — Full integration for code
5. **Document in SKILL.md** — Teach agents about new tools
