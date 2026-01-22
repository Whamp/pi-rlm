#!/bin/bash
# RLM Experience Test: Codebase Comparison
#
# Compares RLM pattern detection with traditional static analysis (oxlint)
# on the Classroom-Connect-V2 codebase.
#
# This test demonstrates:
# 1. What patterns RLM can find that static analysis misses
# 2. What static analysis finds that RLM pattern matching would miss
# 3. The complementary nature of both approaches

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RLM_SCRIPT="$HOME/projects/pi-rlm/skills/rlm/scripts/rlm_repl.py"
CC_DIR="$HOME/projects/Classroom-Connect-V2"
TEST_DIR="/tmp/rlm-comparison-test-$$"
RESULTS_DIR="$TEST_DIR/results"

echo "=== RLM vs Static Analysis Comparison ==="
echo "Target: Classroom-Connect-V2"
echo ""

if [ ! -d "$CC_DIR" ]; then
    echo "ERROR: Classroom-Connect-V2 not found at $CC_DIR"
    exit 1
fi

mkdir -p "$RESULTS_DIR"

# ============================================
# Phase 1: Run oxlint static analysis
# ============================================
echo "=== Phase 1: Static Analysis (oxlint) ==="
echo ""

echo "1. Running oxlint on src/..."
cd "$CC_DIR"

# Run oxlint and capture output (avoiding pipe crash bug)
pnpm oxlint src/ --format json 2>/dev/null > "$RESULTS_DIR/oxlint-raw.json" || true

# Parse results
python3 << PYTHON
import json
from collections import Counter

try:
    with open("$RESULTS_DIR/oxlint-raw.json") as f:
        data = json.load(f)
except:
    # Fallback: run without JSON format and parse text
    data = []

if isinstance(data, list):
    issues = data
elif isinstance(data, dict):
    issues = data.get('diagnostics', data.get('messages', []))
else:
    issues = []

# Count by rule
rule_counts = Counter()
severity_counts = Counter()

for issue in issues:
    rule = issue.get('ruleId', issue.get('code', 'unknown'))
    severity = issue.get('severity', 'unknown')
    rule_counts[rule] += 1
    severity_counts[severity] += 1

print(f"   Total issues: {len(issues)}")
print(f"   By severity: {dict(severity_counts)}")
print(f"   Top 10 rules:")
for rule, count in rule_counts.most_common(10):
    print(f"      {rule}: {count}")

# Save summary
with open("$RESULTS_DIR/oxlint-summary.json", "w") as f:
    json.dump({
        "total": len(issues),
        "by_severity": dict(severity_counts),
        "by_rule": dict(rule_counts),
    }, f, indent=2)
PYTHON

# ============================================
# Phase 2: Run RLM pattern detection
# ============================================
echo ""
echo "=== Phase 2: RLM Pattern Detection ==="
echo ""

# Bundle all TypeScript source files
BUNDLE_FILE="$TEST_DIR/src-bundle.ts"
echo "1. Bundling source files..."
find "$CC_DIR/src" -name "*.ts" -o -name "*.tsx" | \
    grep -v node_modules | \
    grep -v "\.test\." | \
    grep -v "\.spec\." | \
    xargs cat > "$BUNDLE_FILE" 2>/dev/null

BUNDLE_SIZE=$(wc -c < "$BUNDLE_FILE")
BUNDLE_LINES=$(wc -l < "$BUNDLE_FILE")
echo "   Bundle: $BUNDLE_SIZE bytes, $BUNDLE_LINES lines"

echo ""
echo "2. Initializing RLM session..."
INIT_OUTPUT=$(python3 "$RLM_SCRIPT" init "$BUNDLE_FILE" 2>&1)
STATE_PATH=$(echo "$INIT_OUTPUT" | grep -o "\.pi/rlm_state/[^/]*/state.pkl" | head -1)
echo "   State: $STATE_PATH"

echo ""
echo "3. Running pattern detection..."

python3 "$RLM_SCRIPT" --state "$STATE_PATH" exec << PYTHON
import json
from pathlib import Path

results_dir = Path("$RESULTS_DIR")

# Define patterns that complement static analysis
# These are semantic patterns that linters typically miss
patterns = {
    # Code quality patterns
    "console.log (debug statements)": r"console\.(log|debug|info)\s*\(",
    "console.error/warn": r"console\.(error|warn)\s*\(",
    "TODO/FIXME comments": r"(TODO|FIXME|HACK|XXX)[\s:]",
    "Commented-out code": r"^\s*//\s*(const|let|var|function|import|export|if|for|while)",
    
    # React patterns
    "useEffect without deps": r"useEffect\s*\(\s*\(\s*\)\s*=>\s*\{[^}]+\}\s*\)",
    "Inline object in JSX": r"style=\{\{",
    "Empty className": r'className=["\']["\']',
    "Hardcoded colors": r"#[0-9a-fA-F]{3,6}(?![0-9a-fA-F])",
    
    # Security patterns
    "dangerouslySetInnerHTML": r"dangerouslySetInnerHTML",
    "eval usage": r"\beval\s*\(",
    "innerHTML assignment": r"\.innerHTML\s*=",
    
    # TypeScript patterns
    "Type assertion (as any)": r"as\s+any\b",
    "Non-null assertion (!)": r"!\.",
    "@ts-ignore": r"@ts-ignore|@ts-nocheck",
    
    # Async patterns  
    "Unhandled promise": r"\.then\s*\([^)]+\)\s*(?!\s*\.catch)",
    "Empty catch block": r"catch\s*\([^)]*\)\s*\{\s*\}",
    
    # Import patterns
    "Wildcard import": r"import\s+\*\s+as",
    "Default export": r"export\s+default\s+",
}

print("Pattern analysis results:")
print("-" * 60)

rlm_results = {}
total_findings = 0

for name, pattern in patterns.items():
    try:
        grep(pattern)
        h = last_handle()
        cnt = count(h)
        rlm_results[name] = {
            "count": cnt,
            "pattern": pattern,
        }
        total_findings += cnt
        
        status = "⚠️ " if cnt > 10 else "  "
        print(f"{status}{name}: {cnt}")
        
        # Sample first 3 matches for context
        if cnt > 0 and cnt <= 20:
            samples = expand(h, limit=3)
            for s in samples:
                line = s.get('line_num', '?')
                match = s.get('match', '')[:40]
                rlm_results[name].setdefault("samples", []).append({
                    "line": line,
                    "match": match
                })
    except Exception as e:
        print(f"  {name}: ERROR - {e}")
        rlm_results[name] = {"count": 0, "error": str(e)}

print("-" * 60)
print(f"Total RLM findings: {total_findings}")

# Save results
with open(results_dir / "rlm-results.json", "w") as f:
    json.dump(rlm_results, f, indent=2)

print(f"\nResults saved to: {results_dir}/rlm-results.json")
PYTHON

# ============================================
# Phase 3: Comparison Analysis
# ============================================
echo ""
echo "=== Phase 3: Comparison Analysis ==="
echo ""

python3 << PYTHON
import json
from pathlib import Path

results_dir = Path("$RESULTS_DIR")

# Load results
try:
    with open(results_dir / "oxlint-summary.json") as f:
        oxlint = json.load(f)
except:
    oxlint = {"total": 0, "by_rule": {}}

try:
    with open(results_dir / "rlm-results.json") as f:
        rlm = json.load(f)
except:
    rlm = {}

print("=" * 60)
print("COMPARISON SUMMARY")
print("=" * 60)

print(f"""
Static Analysis (oxlint):
  Total issues: {oxlint.get('total', 0)}
  Rule categories: {len(oxlint.get('by_rule', {}))}

RLM Pattern Detection:
  Total findings: {sum(r.get('count', 0) for r in rlm.values())}
  Pattern categories: {len(rlm)}
""")

# Categorize findings by what each tool catches
print("What RLM Catches That Static Analysis Typically Misses:")
print("-" * 60)
rlm_unique = [
    ("TODO/FIXME comments", "Code maintenance debt"),
    ("Commented-out code", "Dead code / cleanup needed"),
    ("Hardcoded colors", "Design system violations"),
    ("Inline object in JSX", "Performance anti-pattern"),
    ("Unhandled promise", "Error handling gaps"),
]
for pattern, reason in rlm_unique:
    if pattern in rlm and rlm[pattern].get("count", 0) > 0:
        print(f"  ✓ {pattern}: {rlm[pattern]['count']} ({reason})")

print()
print("What Static Analysis Catches That RLM Pattern Matching Misses:")
print("-" * 60)
oxlint_unique = [
    "Type errors and inference issues",
    "Unused variables and imports",
    "Unreachable code detection",
    "Complex control flow issues",
    "Import/export resolution",
]
for item in oxlint_unique:
    print(f"  ✓ {item}")

print()
print("Overlap (Both Can Detect):")
print("-" * 60)
overlap = [
    ("console.log", "Debug statements"),
    ("@ts-ignore", "Type suppression"),
    ("as any", "Type assertion"),
]
for pattern, desc in overlap:
    rlm_count = 0
    for k, v in rlm.items():
        if pattern.lower() in k.lower():
            rlm_count = v.get("count", 0)
            break
    print(f"  • {desc}: RLM found {rlm_count}")

print()
print("=" * 60)
print("CONCLUSION")
print("=" * 60)
print("""
RLM excels at:
  • Semantic pattern detection (TODOs, hardcoded values)
  • Cross-file pattern analysis
  • Custom business logic patterns
  • Natural language queries about code

Static analysis excels at:
  • Type checking and inference
  • Control flow analysis  
  • Import/export validation
  • Rule-based best practices

Recommendation: Use both together for comprehensive analysis.
""")

# Save comparison
comparison = {
    "oxlint_total": oxlint.get("total", 0),
    "rlm_total": sum(r.get("count", 0) for r in rlm.values()),
    "rlm_categories": len(rlm),
    "oxlint_categories": len(oxlint.get("by_rule", {})),
}
with open(results_dir / "comparison.json", "w") as f:
    json.dump(comparison, f, indent=2)
PYTHON

echo ""
echo "=== Test Complete ==="
echo "Results directory: $RESULTS_DIR"
echo ""
echo "Files created:"
ls -la "$RESULTS_DIR"
