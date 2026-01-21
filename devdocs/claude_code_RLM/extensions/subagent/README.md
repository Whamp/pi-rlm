# Subagent Extension

Delegate tasks to specialized subagents with isolated context windows.

## Features

- **Isolated context**: Each subagent runs in a separate `pi` process
- **Streaming output**: See tool calls and progress as they happen
- **Parallel streaming**: All parallel tasks stream updates simultaneously
- **Markdown rendering**: Final output rendered with proper formatting (expanded view)
- **Usage tracking**: Shows turns, tokens, cost, and context usage per agent
- **Abort support**: Ctrl+C propagates to kill subagent processes
- **Skills support**: Agents can specify skills to enable (e.g., `git-*,docker`)
- **Extensions support**: Agents can load custom extension files

## Structure

```
subagent/
├── README.md            # This file
├── package.json         # NPM package with test scripts
├── index.ts             # The extension (entry point)
├── agents.ts            # Agent discovery logic
├── agents/              # Sample agent definitions
│   ├── scout.md         # Fast recon, returns compressed context
│   ├── planner.md       # Creates implementation plans
│   ├── reviewer.md      # Code review
│   └── worker.md        # General-purpose (full capabilities)
├── prompts/             # Workflow presets (prompt templates)
│   ├── implement.md     # scout -> planner -> worker
│   ├── scout-and-plan.md    # scout -> planner (no implementation)
│   └── implement-and-review.md  # worker -> reviewer -> worker
└── tests/               # Test suite
    ├── agents.test.ts    # Frontmatter parsing tests
    ├── index.test.ts     # CLI arg generation tests
    └── integration.test.ts  # End-to-end integration tests
```

## Security Model

This tool executes a separate `pi` subprocess with a delegated system prompt and tool/model configuration.

**Project-local agents** (`.pi/agents/*.md`) are repo-controlled prompts that can instruct the model to read files, run bash commands, etc.

**Default behavior:** Only loads **user-level agents** from `~/.pi/agent/agents`.

To enable project-local agents, pass `agentScope: "both"` (or `"project"`). Only do this for repositories you trust.

When running interactively, the tool prompts for confirmation before running project-local agents. Set `confirmProjectAgents: false` to disable.

## Agent Definitions

Agents are markdown files with YAML frontmatter:

### Basic Agent

```markdown
---
name: my-agent
description: What this agent does
tools: read, grep, find, ls
model: claude-haiku-4-5
---

System prompt for the agent goes here.
```

### Agent with Skills

Skills enable specialized capabilities for the agent:

```markdown
---
name: git-helper
description: Git operations helper
skills: git,git-flow,docker
---
You help with git and docker operations.
```

### Agent with Extensions

Extensions are custom TypeScript files that extend `pi` functionality:

```markdown
---
name: custom-agent
description: Agent with custom extensions
extensions: /path/to/custom-extension.ts,~/local/ext.js
---
You have access to custom extensions.
```

### Complete Agent Configuration

All supported frontmatter fields:

```markdown
---
name: example-agent
description: Agent with tools, skills, and extensions
tools: read,grep,bash
skills: git-*,docker
extensions: /path/to/extension1.ts,/path/to/extension2.ts
model: gemini-2.5-flash
thinking: medium
---

System prompt for the agent.
```

### Frontmatter Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `name` | string (required) | Unique identifier for the agent |
| `description` | string (required) | Human-readable description |
| `tools` | string (optional) | Comma-separated tool names |
| `skills` | string (optional) | Comma-separated skill patterns (supports wildcards) |
| `extensions` | string (optional) | Comma-separated file paths to load |
| `model` | string (optional) | Model to use for this agent |
| `thinking` | string (optional) | Thinking level: `off`, `minimal`, `low`, `medium`, `high` |

### Skills Pattern Support

Skills support wildcard patterns:
- `git` - matches skill "git" exactly
- `git-*` - matches any skill starting with "git-"
- `*-flow` - matches any skill ending with "-flow"

### Extension Paths

Extension paths can be:
- **Absolute**: `/path/to/extension.ts`
- **Home-relative**: `~/custom/ext.ts`
- **Current-directory-relative**: `./local/ext.js`
- **Parent-relative**: `../shared/ext.ts`

**Validation**: Extension file existence and skill names are validated by the `pi` subprocess. Invalid skills or non-existent extension files will cause the subagent process to fail with an appropriate error message. This design keeps the agent configuration layer simple while providing clear feedback at runtime.

For paths with spaces, quote the entire value:
```yaml
extensions: "/path/with spaces/ext.ts,/another/path.ts"
```

## Agent Locations

Agents are loaded from two locations:

- `~/.pi/agent/agents/*.md` - User-level (always loaded)
- `.pi/agents/*.md` - Project-level (only with `agentScope: "project"` or `"both"`)

Project agents override user agents with the same name when `agentScope: "both"`.

## Usage

### Single agent
```
Use scout to find all authentication code
```

### Parallel execution
```
Run 2 scouts in parallel: one to find models, one to find providers
```

### Chained workflow
```
Use a chain: first have scout find read tool, then have planner suggest improvements
```

### Workflow prompts
```
/implement add Redis caching to the session store
/scout-and-plan refactor auth to support OAuth
/implement-and-review add input validation to API endpoints
```

## Tool Modes

| Mode | Parameter | Description |
|------|-----------|-------------|
| Single | `{ agent, task }` | One agent, one task |
| Parallel | `{ tasks: [...] }` | Multiple agents run concurrently (max 8, 4 concurrent) |
| Chain | `{ chain: [...] }` | Sequential with `{previous}` placeholder |

## CLI Arguments Generated

The extension automatically generates the correct CLI arguments for the subagent process:

### Base Arguments
- `--mode json` - JSON output mode
- `-p` - Enable prompts
- `--no-session` - No session persistence

### Optional Arguments (based on agent config)
- `--model <name>` - If agent specifies a model
- `--thinking <level>` - If agent specifies a thinking level
- `--tools <list>` - Comma-separated tools
- `--skills <list>` - Comma-separated skills
- `-e <path>` - Extension file (repeated for each extension)

### Example CLI Generation

For an agent with:
```yaml
tools: read,bash
skills: git,docker
extensions: /ext1.ts,/ext2.ts
model: gemini-2.5-flash
thinking: high
```

Generated CLI:
```bash
pi --mode json -p --no-session \
  --model gemini-2.5-flash \
  --thinking high \
  --model gemini-2.5-flash \
  --tools read,bash \
  --skills git,docker \
  -e /ext1.ts \
  -e /ext2.ts \
  "Task: ..."
```

## Testing

The extension includes a comprehensive test suite:

```bash
# Run all tests
npm run test:all

# Run specific test suites
npm test          # Frontmatter parsing tests
npm run test:cli   # CLI arg generation tests
npm run test:integration  # End-to-end integration tests
```

### Test Coverage

- **Frontmatter parsing**: 29 tests
  - Single and multiple values
  - Whitespace handling
  - Empty/missing fields
  - Special characters and wildcards
  - Various path formats
  - Edge cases

- **CLI arg generation**: 27 tests
  - Individual fields (tools, skills, extensions, model)
  - Combinations of fields
  - Arg ordering
  - Edge cases

- **Integration**: 16 tests
  - Full discovery flow
  - Agent configuration variations
  - File filtering
  - Validation
  - Real-world scenarios

## Output Display

**Collapsed view** (default):
- Status icon (✓/✗/⏳) and agent name
- Last 5-10 items (tool calls and text)
- Usage stats: `3 turns ↑input ↓output RcacheRead WcacheWrite $cost ctx:contextTokens model`

**Expanded view** (Ctrl+O):
- Full task text
- All tool calls with formatted arguments
- Final output rendered as Markdown
- Per-task usage (for chain/parallel)

**Parallel mode streaming**:
- Shows all tasks with live status (⏳ running, ✓ done, ✗ failed)
- Updates as each task makes progress
- Shows "2/3 done, 1 running" status

**Tool call formatting** (mimics built-in tools):
- `$ command` for bash
- `read ~/path:1-10` for read
- `grep /pattern/ in ~/path` for grep
- etc.

## Sample Agents

| Agent | Purpose | Model | Tools | Skills | Extensions |
|-------|---------|-------|-------|--------|------------|
| `scout` | Fast codebase recon | Haiku | read, grep, find, ls, bash | - | - |
| `planner` | Implementation plans | Sonnet | read, grep, find, ls | - | - |
| `reviewer` | Code review | Sonnet | read, grep, find, ls, bash | - | - |
| `worker` | General-purpose | Sonnet | (all default) | - | - |

## Workflow Prompts

| Prompt | Flow |
|--------|------|
| `/implement <query>` | scout → planner → worker |
| `/scout-and-plan <query>` | scout → planner |
| `/implement-and-review <query>` | worker → reviewer → worker |

## Error Handling

- **Exit code != 0**: Tool returns error with stderr/output
- **stopReason "error"**: LLM error propagated with error message
- **stopReason "aborted"**: User abort (Ctrl+C) kills subprocess, throws error
- **Chain mode**: Stops at first failing step, reports which step failed

## Edge Cases and Known Limitations

### Frontmatter Parsing
- **Individual quoted values in comma-separated lists**: Not supported. Quote the entire value instead:
  ```yaml
  # ❌ Individual quotes don't work
  extensions: "/path1.ts","/path2.ts"

  # ✅ Quote entire value
  extensions: "/path1.ts,/path2.ts"
  ```

### Empty Values
- Empty fields are treated as if they were omitted
- Whitespace-only fields are filtered out

### Trailing Commas
- Trailing commas are handled gracefully
- `skills: git,docker,` → parsed as `["git", "docker"]`

## Limitations

- Output truncated to last 10 items in collapsed view (expand to see all)
- Agents discovered fresh on each invocation (allows editing mid-session)
- Parallel mode limited to 8 tasks, 4 concurrent
