# SPEC.md — Agent Orchestration System (AOS) Technical Specification

This SPEC complements `PRD.md` with **implementation-grade** details:
- File formats, schemas, and parsing rules
- Lock acquisition algorithm
- CLI behavior and exit codes
- Stage execution (including Clarification, Documentation, UAT)
- Agent integration contracts (Codex, Claude Code)
- Run artifact schemas
- Golden run scenarios with expected outputs
- Self-testing requirements

This document is written to be implementable without guesswork.

---

## 1) Path conventions and environment variables

### 1.1 Directory structure (parameterized)

AOS uses these canonical paths, all derived from configuration:

| Variable | Description | Example |
|----------|-------------|---------|
| `AOS_WORKFLOW_REPO` | This workflow repository | `~/dev/myproject.ops/workflow` |
| `AOS_OPS_DIR` | Ops runtime (untracked sibling) | `~/dev/myproject.ops` |
| `AOS_PROJECTS_DIR` | Projects directory | `$AOS_WORKFLOW_REPO/projects` |
| `PROJECT_REPO` | Product repository (per-project) | `~/dev/myproject` |
| `PROJECT_WORKTREES` | Worktrees directory | `$AOS_OPS_DIR/worktrees` |
| `PROJECT_RUNS` | Run logs directory | `$AOS_OPS_DIR/runs` |

**Resolution order:**
1. Environment variable (if set)
2. `project.env` value (if exists)
3. Default (derived from workflow repo location)

### 1.2 Environment variables

**Global (optional overrides):**
| Variable | Default | Description |
|----------|---------|-------------|
| `AOS_OPS_DIR` | Derived from workflow repo parent | Ops runtime directory |
| `AOS_PROJECT` | None (required for multi-project) | Active project name |
| `AOS_LOG_LEVEL` | `info` | `debug`, `info`, `warn`, `error` |
| `AOS_LOCK_TIMEOUT` | `600` | Lock acquisition timeout in seconds |

**Secrets (never in git):**
- Must be stored in `$AOS_OPS_DIR/secrets/`
- Loaded via `secrets/env.sh` or similar
- AOS must NEVER print secret values
- AOS must NEVER commit files from secrets directory

### 1.3 Multi-project support

When multiple projects are registered:
```
workflow/projects/
  projectA/
    project.env
    ...
  projectB/
    project.env
    ...
```

Commands require project context:
- `wf --project projectA run ws1 --once`
- Or set `AOS_PROJECT=projectA` in environment
- Or `cd` into project directory and infer from path

---

## 2) File formats and parsing

### 2.1 Env file format (AOS-safe subset)

AOS uses `.env`-style files for configuration. Parsing MUST be safe.

**Grammar:**
```
file     = line*
line     = empty | comment | assignment
empty    = WS* NL
comment  = WS* "#" [^\n]* NL
assignment = KEY "=" VALUE NL

KEY      = [A-Z][A-Z0-9_]*
VALUE    = unquoted | quoted
unquoted = [^\s"'`$;|&\n]+
quoted   = '"' [^"`$]* '"'
```

**Forbidden in values (MUST reject):**
- Backticks: `` ` ``
- Command substitution: `$(`, `${`
- Shell operators: `;`, `&&`, `||`, `|`
- Unbalanced quotes

**Multiline values:** NOT supported. Use separate keys or JSON files for complex data.

**Space-separated lists:** Allowed within quoted values:
```env
EXPECTED_PATHS="requirements/ scripts/test/ mk/"
```
Parsed as single string; consumer splits on whitespace.

### 2.2 Safe env parser implementation

**Required:** `orchestrator/lib/envparse.py`

```python
"""
Safe .env parser for AOS.

Usage:
    from envparse import load_env
    config = load_env("/path/to/file.env")  # Returns dict

CLI:
    python -m envparse /path/to/file.env    # Prints JSON to stdout
"""

import re
import json
import sys
from pathlib import Path

FORBIDDEN_PATTERNS = [
    r'`',           # backticks
    r'\$\(',        # command substitution
    r'\$\{',        # variable expansion
    r';',           # command chaining
    r'&&',          # AND chaining
    r'\|\|',        # OR chaining
    r'\|',          # pipe
]

KEY_PATTERN = re.compile(r'^[A-Z][A-Z0-9_]*$')

def load_env(filepath: str) -> dict:
    """Parse env file safely, return dict."""
    result = {}
    path = Path(filepath)

    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {filepath}")

    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()

        # Skip empty and comments
        if not line or line.startswith('#'):
            continue

        # Must have =
        if '=' not in line:
            raise ValueError(f"Line {lineno}: Invalid syntax (no '=')")

        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()

        # Validate key
        if not KEY_PATTERN.match(key):
            raise ValueError(f"Line {lineno}: Invalid key '{key}'")

        # Strip quotes if present
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]

        # Check for forbidden patterns
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, value):
                raise ValueError(f"Line {lineno}: Forbidden pattern in value")

        result[key] = value

    return result

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python -m envparse <file.env>", file=sys.stderr)
        sys.exit(1)
    try:
        config = load_env(sys.argv[1])
        print(json.dumps(config))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
```

**Bash integration (safe):**
```bash
load_project_config() {
    local config_json
    config_json=$(python3 -m envparse "$1") || {
        echo "ERROR: Failed to parse $1" >&2
        return 2
    }
    # Extract specific values via jq
    PROJECT_NAME=$(echo "$config_json" | jq -r '.PROJECT_NAME')
    REPO_PATH=$(echo "$config_json" | jq -r '.REPO_PATH')
    # ... etc
}
```

**DO NOT** use `eval` or `source` on untrusted env files.

### 2.3 Plan.md parsing

**Micro-commit heading pattern:**
```regex
^###\s+(COMMIT-[A-Za-z0-9_-]+-\d{3}):\s*(.+?)\s*$
```

Captures:
- Group 1: Commit ID (e.g., `COMMIT-TF-001`)
- Group 2: Title

**Done state pattern (within micro-commit block):**
```regex
^Done:\s*\[([ xX])\]\s*$
```

Captures:
- Group 1: ` ` (undone) or `x`/`X` (done)

**Block boundaries:**
- A micro-commit block starts at its `###` heading
- A micro-commit block ends at the next `###` heading or EOF

**Selection rule:**
- Select the FIRST micro-commit where Done is `[ ]` (space, not x)
- If no undone commits exist: workstream is complete

**Required parser:** `orchestrator/lib/planparse.py`

```python
"""
Plan.md parser for AOS.

Usage:
    from planparse import parse_plan, get_next_microcommit

    commits = parse_plan("/path/to/plan.md")
    next_commit = get_next_microcommit(commits)
"""

import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

HEADING_RE = re.compile(r'^###\s+(COMMIT-[A-Za-z0-9_-]+-\d{3}):\s*(.+?)\s*$')
DONE_RE = re.compile(r'^Done:\s*\[([ xX])\]\s*$')

@dataclass
class MicroCommit:
    id: str
    title: str
    done: bool
    line_number: int
    block_content: str

def parse_plan(filepath: str) -> list[MicroCommit]:
    """Parse plan.md and return list of micro-commits."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Plan file not found: {filepath}")

    lines = path.read_text().splitlines()
    commits = []
    current = None
    current_lines = []

    for lineno, line in enumerate(lines, 1):
        heading_match = HEADING_RE.match(line)

        if heading_match:
            # Save previous block
            if current:
                current.block_content = '\n'.join(current_lines)
                commits.append(current)

            # Start new block
            current = MicroCommit(
                id=heading_match.group(1),
                title=heading_match.group(2),
                done=False,
                line_number=lineno,
                block_content=""
            )
            current_lines = [line]
        elif current:
            current_lines.append(line)
            done_match = DONE_RE.match(line)
            if done_match:
                current.done = done_match.group(1).lower() == 'x'

    # Save last block
    if current:
        current.block_content = '\n'.join(current_lines)
        commits.append(current)

    return commits

def get_next_microcommit(commits: list[MicroCommit]) -> Optional[MicroCommit]:
    """Return first undone micro-commit, or None if all done."""
    for commit in commits:
        if not commit.done:
            return commit
    return None

def mark_done(filepath: str, commit_id: str) -> bool:
    """Mark a micro-commit as done in the plan file."""
    path = Path(filepath)
    content = path.read_text()

    # Find the commit block and its Done line
    lines = content.splitlines()
    in_block = False

    for i, line in enumerate(lines):
        if HEADING_RE.match(line) and commit_id in line:
            in_block = True
        elif HEADING_RE.match(line):
            in_block = False
        elif in_block and DONE_RE.match(line):
            lines[i] = 'Done: [x]'
            path.write_text('\n'.join(lines) + '\n')
            return True

    return False
```

### 2.4 JSON file conventions

All JSON files MUST:
- Use 2-space indentation
- Use UTF-8 encoding
- End with newline
- Be valid JSON (parseable by standard libraries)

---

## 3) Schemas (authoritative)

### 3.1 `project.env` (required)

```env
# Required
PROJECT_NAME=myproject
REPO_PATH=/home/user/dev/myproject
OPS_PATH=/home/user/dev/myproject.ops
DEFAULT_BRANCH=main

# Optional
BRANCH_PREFIX=feat
```

### 3.2 `project_profile.env` (optional, has smart defaults)

Controls build/test behavior and merge workflow. If not present, defaults are used.

```env
# Build configuration
MAKEFILE_PATH=Makefile              # Path to Makefile (default: Makefile)
MAKE_TARGET_TEST=test               # Test target for micro-commit validation
MAKE_TARGET_MERGE_GATE_TEST=test    # Test target for final merge gate (defaults to MAKE_TARGET_TEST)

# Timeouts (seconds)
IMPLEMENT_TIMEOUT=1200              # Codex implementation timeout (default: 1200 = 20 min)
REVIEW_TIMEOUT=600                  # Claude review timeout (default: 600 = 10 min)
TEST_TIMEOUT=300                    # Test suite timeout (default: 300 = 5 min)
BREAKDOWN_TIMEOUT=180               # Story breakdown timeout (default: 180 = 3 min)

# Review mode
SUPERVISED_MODE=false               # true = require human approval for every commit
                                    # false = auto-approve if AI review passes (gatekeeper mode)

# Merge workflow
MERGE_MODE=github_pr                # How to merge completed workstreams:
                                    #   github_pr - Create GitHub PR, wait for approval (default if gh CLI available)
                                    #   local     - Merge directly to main locally (default if no gh CLI)
                                    # TODO: gitlab_mr, bitbucket_pr
```

**Smart defaults:**
- `MERGE_MODE`: Auto-detected. Uses `github_pr` if `gh auth status` succeeds, otherwise `local`.
- User can always override to `local` even when GitHub is available.
- All timeouts have sensible defaults for typical projects.

### 3.3 Workstream `meta.env`

```env
# Required
ID=test_framework
TITLE="Production-grade test framework"
BRANCH=feat/test_framework
WORKTREE=/home/user/dev/myproject.ops/worktrees/test_framework
BASE_BRANCH=main
BASE_SHA=abc123def456
STATUS=implement
EXPECTED_PATHS="requirements/ scripts/test/ mk/"
CREATED_AT=2025-01-15T10:00:00Z
LAST_REFRESHED=2025-01-15T12:00:00Z

# Optional (updated by wf run)
LAST_RUN_ID=20250115-120000_myproject_test_framework_COMMIT-TF-001
LAST_COMMIT_SHA=def789abc012
LAST_RESULT=passed
BLOCKED_BY=""
```

**STATUS values:**
| Status | Description |
|--------|-------------|
| `planning` | Plan being created |
| `implement` | Implementation in progress |
| `blocked:clarification` | Waiting for CLQ answers |
| `blocked:review` | Review requested changes |
| `blocked:test` | Tests failing |
| `docs` | Documentation stage |
| `uat:pending` | Awaiting UAT |
| `uat:failed` | UAT failed, needs fixes |
| `merge-ready` | All gates passed |
| `done` | Merged and closed |

### 3.4 `result.json` schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["version", "project", "workstream", "microcommit", "status", "timestamps"],
  "properties": {
    "version": { "const": 1 },
    "project": { "type": "string" },
    "workstream": { "type": "string" },
    "microcommit": { "type": "string", "pattern": "^COMMIT-[A-Za-z0-9_-]+-\\d{3}$" },
    "status": { "enum": ["passed", "failed", "blocked"] },
    "failed_stage": {
      "enum": ["load", "select", "clarification", "implement", "test", "review", "qa_gate", "docs", "uat"]
    },
    "blocked_reason": { "type": "string" },
    "base_sha": { "type": "string", "pattern": "^[a-f0-9]{7,40}$" },
    "commit_sha": { "type": "string", "pattern": "^[a-f0-9]{7,40}$" },
    "touched_files_count": { "type": "integer", "minimum": 0 },
    "timestamps": {
      "type": "object",
      "required": ["started", "ended"],
      "properties": {
        "started": { "type": "string", "format": "date-time" },
        "ended": { "type": "string", "format": "date-time" },
        "duration_seconds": { "type": "number" }
      }
    },
    "stages": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "properties": {
          "status": { "enum": ["passed", "failed", "skipped"] },
          "duration_seconds": { "type": "number" },
          "notes": { "type": "string" }
        }
      }
    },
    "notes": { "type": "string" }
  }
}
```

**Example:**
```json
{
  "version": 1,
  "project": "myproject",
  "workstream": "test_framework",
  "microcommit": "COMMIT-TF-001",
  "status": "passed",
  "base_sha": "abc123d",
  "commit_sha": "def789a",
  "touched_files_count": 5,
  "timestamps": {
    "started": "2025-01-15T12:00:00Z",
    "ended": "2025-01-15T12:05:30Z",
    "duration_seconds": 330
  },
  "stages": {
    "load": { "status": "passed", "duration_seconds": 0.5 },
    "select": { "status": "passed", "duration_seconds": 0.1 },
    "clarification": { "status": "passed", "duration_seconds": 0.1 },
    "implement": { "status": "passed", "duration_seconds": 180 },
    "test": { "status": "passed", "duration_seconds": 120 },
    "review": { "status": "passed", "duration_seconds": 30 },
    "qa_gate": { "status": "passed", "duration_seconds": 1 }
  },
  "notes": ""
}
```

### 3.5 `claude_review.json` schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["version", "decision"],
  "properties": {
    "version": { "const": 1 },
    "decision": { "enum": ["approve", "request_changes"] },
    "blockers": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["file", "issue"],
        "properties": {
          "file": { "type": "string" },
          "line": { "type": ["integer", "null"] },
          "issue": { "type": "string" },
          "severity": { "enum": ["critical", "major", "minor"] },
          "fix_hint": { "type": "string" }
        }
      }
    },
    "required_changes": {
      "type": "array",
      "items": { "type": "string" }
    },
    "suggestions": {
      "type": "array",
      "items": { "type": "string" }
    },
    "documentation": {
      "type": "object",
      "properties": {
        "required": { "type": "boolean" },
        "present": { "type": "boolean" },
        "quality": { "enum": ["adequate", "good", "needs_work"] },
        "missing_sections": { "type": "array", "items": { "type": "string" } },
        "stale_screenshots": { "type": "array", "items": { "type": "string" } }
      }
    },
    "notes": { "type": "string" }
  }
}
```

**Decision rules:**
- `decision == "request_changes"` AND `blockers` non-empty → run fails
- `decision == "request_changes"` AND `required_changes` non-empty → run fails
- `decision == "approve"` → run continues
- Invalid JSON → run fails

### 3.6 `test_manifest.json` schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["version", "suites"],
  "properties": {
    "version": { "const": 1 },
    "generated": { "type": "string", "format": "date-time" },
    "suites": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "status", "artifacts"],
        "properties": {
          "name": { "type": "string" },
          "status": { "enum": ["passed", "failed", "skipped"] },
          "exit_code": { "type": "integer" },
          "duration_seconds": { "type": "number" },
          "artifacts": {
            "type": "object",
            "properties": {
              "summary_json": { "type": "string" },
              "junit_xml": { "type": "string" },
              "coverage": { "type": "string" },
              "screenshots": { "type": "array", "items": { "type": "string" } }
            }
          },
          "stats": {
            "type": "object",
            "properties": {
              "total": { "type": "integer" },
              "passed": { "type": "integer" },
              "failed": { "type": "integer" },
              "skipped": { "type": "integer" }
            }
          }
        }
      }
    }
  }
}
```

### 3.7 Clarification `CLQ-xxx.json` (machine-readable companion)

Each `CLQ-xxx.md` has an optional `CLQ-xxx.json` for programmatic access:

```json
{
  "version": 1,
  "id": "CLQ-001",
  "status": "pending",
  "created": "2025-01-15T10:00:00Z",
  "answered": null,
  "urgency": "blocking",
  "source_stage": "implementation",
  "workstream": "user_auth",
  "blocks": ["COMMIT-UA-003"],
  "question": "Which authentication method should we use?",
  "options": [
    { "id": "jwt", "label": "JWT tokens", "tradeoffs": "Stateless, needs refresh" },
    { "id": "session", "label": "Session cookies", "tradeoffs": "Simpler, needs storage" }
  ],
  "answer": null,
  "answered_by": null
}
```

### 3.8 UAT `UAT-xxx.json` (machine-readable companion)

```json
{
  "version": 1,
  "id": "UAT-001",
  "status": "pending",
  "created": "2025-01-15T10:00:00Z",
  "completed": null,
  "workstream": "user_auth",
  "requirements": ["REQ-012-1", "REQ-012-2", "STORY-012"],
  "scenarios": [
    {
      "name": "Successful login",
      "steps": ["Navigate to /login", "Enter credentials", "Click Sign In"],
      "expected": "Redirected to dashboard",
      "result": null
    }
  ],
  "result": null,
  "validated_by": null,
  "issues": []
}
```

---

## 4) Workstream operations

### 4.1 `wf new <id> "<title>" "<expected_paths>"`

**Validation:**
```
id         := [a-z][a-z0-9_-]*       # lowercase, start with letter
title      := .{1,100}               # 1-100 chars
expected   := space-separated paths
```

**Branch naming:**
- Default pattern: `feat/<id>`
- Configurable via `BRANCH_PREFIX` in project.env
- Other valid prefixes: `fix/`, `chore/`, `refactor/`, `docs/`

**Algorithm:**
```python
def wf_new(id: str, title: str, expected_paths: str):
    # 1. Validate inputs
    if not re.match(r'^[a-z][a-z0-9_-]*$', id):
        fail(2, f"Invalid workstream ID: {id}")

    # 2. Load project config
    project = load_project_config()

    # 3. Compute paths
    branch = f"{project.branch_prefix}/{id}"
    worktree = f"{project.ops_path}/worktrees/{id}"

    # 4. Check for conflicts
    # Branch exists locally?
    if run(f"git -C {project.repo} show-ref --verify --quiet refs/heads/{branch}"):
        fail(2, f"Branch already exists: {branch}")

    # Branch exists on remote?
    run(f"git -C {project.repo} fetch --all --prune", check=False)
    if run(f"git -C {project.repo} show-ref --verify --quiet refs/remotes/origin/{branch}"):
        fail(2, f"Branch exists on remote: {branch}")

    # Worktree directory exists?
    if Path(worktree).exists():
        fail(2, f"Worktree directory exists: {worktree}")

    # Workstream already registered?
    if workstream_exists(id):
        fail(2, f"Workstream already exists: {id}")

    # 5. Get base SHA
    base_sha = run(f"git -C {project.repo} rev-parse {project.default_branch}")

    # 6. Create branch + worktree atomically
    run(f"git -C {project.repo} worktree add {worktree} -b {branch} {base_sha}")

    # 7. Create workstream directory structure
    ws_dir = f"{project.workflow_repo}/projects/{project.name}/workstreams/{id}"
    mkdir(ws_dir)

    # 8. Write meta.env
    write_env(f"{ws_dir}/meta.env", {
        "ID": id,
        "TITLE": title,
        "BRANCH": branch,
        "WORKTREE": worktree,
        "BASE_BRANCH": project.default_branch,
        "BASE_SHA": base_sha,
        "STATUS": "planning",
        "EXPECTED_PATHS": expected_paths,
        "CREATED_AT": now_iso(),
        "LAST_REFRESHED": now_iso(),
    })

    # 9. Create placeholder files
    write(f"{ws_dir}/plan.md", PLAN_TEMPLATE.format(id=id, title=title))
    write(f"{ws_dir}/notes.md", f"# Notes: {title}\n")
    write(f"{ws_dir}/touched_files.txt", "")

    # 10. Create queue directories
    mkdir(f"{ws_dir}/clarifications/pending")
    mkdir(f"{ws_dir}/clarifications/answered")
    mkdir(f"{ws_dir}/uat/pending")
    mkdir(f"{ws_dir}/uat/passed")

    # 11. Initial refresh
    wf_refresh(id)

    print(f"Created workstream: {id}")
    print(f"  Branch: {branch}")
    print(f"  Worktree: {worktree}")
```

**Failure recovery:**
If creation fails mid-way:
- Remove worktree if created: `git worktree remove --force <path>`
- Remove branch if created: `git branch -D <branch>`
- Remove workstream directory if created

### 4.2 `wf refresh [<id>]`

If `<id>` provided, refresh that workstream. Otherwise, refresh all active workstreams.

**Algorithm:**
```python
def wf_refresh(id: str = None):
    workstreams = [id] if id else get_active_workstreams()

    for ws_id in workstreams:
        ws = load_workstream(ws_id)

        # Check worktree exists and is valid
        if not Path(ws.worktree).exists():
            warn(f"Worktree missing for {ws_id}")
            continue

        # Check for uncommitted changes (warning only)
        status = run(f"git -C {ws.worktree} status --porcelain")
        if status:
            warn(f"Uncommitted changes in {ws_id}")

        # Compute touched files
        touched = run(f"git -C {ws.worktree} diff --name-only {ws.base_sha}..HEAD")
        touched_sorted = sorted(set(touched.splitlines()))

        write(f"{ws.dir}/touched_files.txt", '\n'.join(touched_sorted))

        # Update meta.env
        update_env(f"{ws.dir}/meta.env", {
            "LAST_REFRESHED": now_iso()
        })

    # Regenerate ACTIVE_WORKSTREAMS.md
    regenerate_active_workstreams()
```

### 4.3 `wf conflicts <id>`

**Algorithm:**
```python
def wf_conflicts(id: str):
    target = load_workstream(id)
    target_files = set(read_lines(f"{target.dir}/touched_files.txt"))
    target_expected = target.expected_paths.split()

    results = []

    for other in get_active_workstreams():
        if other.id == id:
            continue

        other_files = set(read_lines(f"{other.dir}/touched_files.txt"))

        # Actual overlap
        actual_overlap = target_files & other_files

        # Predicted overlap (expected paths of target vs touched of other)
        predicted_overlap = {}
        for prefix in target_expected:
            matching = [f for f in other_files if f.startswith(prefix)]
            if matching:
                predicted_overlap[prefix] = matching

        if actual_overlap or predicted_overlap:
            results.append({
                "workstream": other.id,
                "actual_overlap": sorted(actual_overlap),
                "predicted_overlap": predicted_overlap
            })

    # Output
    if not results:
        print(f"No conflicts detected for {id}")
        return

    print(f"Conflicts for {id}:")
    for r in results:
        print(f"\n  vs {r['workstream']}:")
        if r['actual_overlap']:
            print(f"    Actual overlap ({len(r['actual_overlap'])} files):")
            for f in r['actual_overlap'][:10]:
                print(f"      - {f}")
            if len(r['actual_overlap']) > 10:
                print(f"      ... and {len(r['actual_overlap']) - 10} more")
        if r['predicted_overlap']:
            print(f"    Predicted overlap:")
            for prefix, files in r['predicted_overlap'].items():
                print(f"      {prefix}: {len(files)} files")
```

### 4.4 `wf close <id>`

**Preconditions:**
- Workstream exists
- STATUS is `done` or `merge-ready`, OR `--force` flag provided

**Algorithm:**
```python
def wf_close(id: str, force: bool = False):
    ws = load_workstream(id)

    if ws.status not in ("done", "merge-ready") and not force:
        fail(2, f"Workstream not complete. Use --force to close anyway.")

    # Archive workstream
    timestamp = now_iso().replace(":", "-")
    archive_dir = f"{ws.project_dir}/workstreams/_closed/{id}-{timestamp}"

    # Move workstream directory
    move(ws.dir, archive_dir)

    # Remove worktree (but keep branch for history)
    run(f"git -C {ws.project.repo} worktree remove --force {ws.worktree}")

    # Update ACTIVE_WORKSTREAMS.md
    regenerate_active_workstreams()

    print(f"Closed workstream: {id}")
    print(f"  Archived to: {archive_dir}")
```

### 4.5 `wf delete <id>` (new command)

Completely removes a workstream, including branch. For abandoned work.

**Algorithm:**
```python
def wf_delete(id: str, confirm: bool = False):
    ws = load_workstream(id)

    if not confirm:
        print(f"This will permanently delete workstream {id}")
        print(f"  Branch: {ws.branch}")
        print(f"  Worktree: {ws.worktree}")
        print(f"Use --confirm to proceed")
        return

    # Remove worktree
    if Path(ws.worktree).exists():
        run(f"git -C {ws.project.repo} worktree remove --force {ws.worktree}")

    # Delete branch (local)
    run(f"git -C {ws.project.repo} branch -D {ws.branch}", check=False)

    # Delete branch (remote) - only if not merged
    run(f"git -C {ws.project.repo} push origin --delete {ws.branch}", check=False)

    # Remove workstream directory
    rmtree(ws.dir)

    # Update ACTIVE_WORKSTREAMS.md
    regenerate_active_workstreams()

    print(f"Deleted workstream: {id}")
```

---

## 5) `wf run` — Cycle execution

### 5.1 Stage pipeline

```
LOCK → LOAD → SELECT → CLARIFICATION_CHECK → IMPLEMENT → TEST → REVIEW → QA_GATE → DOCS → UPDATE_STATE → REFRESH → UNLOCK
```

For UAT (after all micro-commits done):
```
... → UPDATE_STATE → UAT_GATE → REFRESH → UNLOCK
```

### 5.2 Locking

**MVP: Global lock**

```python
LOCK_FILE = f"{ops_dir}/locks/global.lock"
LOCK_TIMEOUT = int(os.environ.get("AOS_LOCK_TIMEOUT", 600))

def acquire_lock():
    """Acquire global lock with timeout."""
    lock_dir = Path(LOCK_FILE).parent
    lock_dir.mkdir(parents=True, exist_ok=True)

    # Try flock first (preferred)
    try:
        import fcntl
        fd = open(LOCK_FILE, 'w')
        start = time.time()
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Write PID for debugging
                fd.write(f"{os.getpid()}\n{datetime.now().isoformat()}\n")
                fd.flush()
                return fd  # Return fd to keep lock
            except BlockingIOError:
                if time.time() - start > LOCK_TIMEOUT:
                    fail(3, "Lock acquisition timeout")
                time.sleep(1)
    except ImportError:
        # Fallback: mkdir-based locking
        return acquire_lock_mkdir()

def acquire_lock_mkdir():
    """Fallback locking using mkdir atomicity."""
    lock_dir = f"{LOCK_FILE}.d"
    start = time.time()

    while True:
        try:
            os.mkdir(lock_dir)
            # Write info
            Path(f"{lock_dir}/owner").write_text(f"{os.getpid()}\n{datetime.now().isoformat()}\n")
            return lock_dir
        except FileExistsError:
            # Check for stale lock
            try:
                info = Path(f"{lock_dir}/owner").read_text()
                pid = int(info.splitlines()[0])
                if not pid_exists(pid):
                    # Stale lock, remove it
                    shutil.rmtree(lock_dir)
                    continue
            except:
                pass

            if time.time() - start > LOCK_TIMEOUT:
                fail(3, "Lock acquisition timeout")
            time.sleep(1)

def release_lock(lock):
    """Release lock (works with both fd and mkdir)."""
    if isinstance(lock, str):
        # mkdir-based
        shutil.rmtree(lock, ignore_errors=True)
    else:
        # flock-based
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
        Path(LOCK_FILE).unlink(missing_ok=True)
```

**Lock must be released on exit (including signals):**
```python
import signal
import atexit

lock_handle = None

def cleanup():
    if lock_handle:
        release_lock(lock_handle)

atexit.register(cleanup)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
signal.signal(signal.SIGINT, lambda *_: sys.exit(1))
```

**Future: Per-workstream locks**
```
locks/
  global.lock           # For operations that touch multiple workstreams
  workstream.{id}.lock  # Per-workstream lock
  crosscutting.lock     # For changes to shared infrastructure
```

### 5.3 Run directory

**Naming:**
```
{ops_dir}/runs/{YYYYMMDD-HHMMSS}_{project}_{workstream}_{microcommit}/
```

Example:
```
~/dev/myproject.ops/runs/20250115-120000_myproject_test_framework_COMMIT-TF-001/
```

**Required files:**
| File | Description |
|------|-------------|
| `run_summary.md` | Human-readable summary |
| `result.json` | Machine-readable result (schema §3.4) |
| `commands.log` | All commands with timestamps and exit codes |
| `env_snapshot.txt` | Tool versions |
| `diff.patch` | Git diff used for review |
| `claude_review.json` | Review output (schema §3.5) |
| `test_manifest.json` | Test results (schema §3.6) |
| `implement.log` | Codex/implementation output |
| `stages/` | Per-stage logs (optional, for debugging) |

### 5.4 Stage: LOAD

```python
def stage_load(ws_id: str) -> Context:
    """Load and validate all configuration."""

    # Load project config
    project = load_project_config()
    profile = load_project_profile()

    # Load workstream
    ws = load_workstream(ws_id)

    # Validate worktree
    if not Path(ws.worktree).exists():
        fail(4, f"Worktree does not exist: {ws.worktree}")

    # Validate worktree is on correct branch
    actual_branch = run(f"git -C {ws.worktree} branch --show-current")
    if actual_branch != ws.branch.split('/')[-1]:  # Handle feat/xxx format
        fail(4, f"Worktree on wrong branch: {actual_branch} (expected {ws.branch})")

    # Validate BASE_SHA exists
    try:
        run(f"git -C {ws.worktree} cat-file -e {ws.base_sha}")
    except:
        fail(4, f"BASE_SHA does not exist: {ws.base_sha}")

    # Validate plan.md exists
    plan_path = f"{ws.dir}/plan.md"
    if not Path(plan_path).exists():
        fail(4, f"Plan file missing: {plan_path}")

    # Check for uncommitted changes (warning)
    status = run(f"git -C {ws.worktree} status --porcelain")
    if status:
        warn(f"Uncommitted changes in worktree")

    return Context(project=project, profile=profile, workstream=ws)
```

### 5.5 Stage: SELECT

```python
def stage_select(ctx: Context) -> MicroCommit:
    """Select next micro-commit to execute."""

    plan_path = f"{ctx.workstream.dir}/plan.md"
    commits = parse_plan(plan_path)

    if not commits:
        fail(4, "No micro-commits found in plan.md")

    next_commit = get_next_microcommit(commits)

    if next_commit is None:
        # All done - but check if UAT pending
        if ctx.workstream.status != "merge-ready":
            return None  # Signal: workstream complete, trigger UAT
        else:
            success("All micro-commits complete")

    return next_commit
```

### 5.6 Stage: CLARIFICATION_CHECK

```python
def stage_clarification_check(ctx: Context) -> bool:
    """Check for blocking clarifications."""

    pending_dir = f"{ctx.workstream.dir}/clarifications/pending"

    blocking = []
    for clq_file in Path(pending_dir).glob("CLQ-*.json"):
        clq = json.loads(clq_file.read_text())
        if clq.get("urgency") == "blocking":
            blocking.append(clq["id"])

    if blocking:
        # Update status
        update_workstream_status(ctx.workstream, "blocked:clarification",
                                  blocked_by=",".join(blocking))
        fail(8, f"Blocked by clarifications: {', '.join(blocking)}")

    return True
```

### 5.7 Stage: IMPLEMENT

**Agent integration contract:**

AOS invokes an implementation agent (Codex, Claude, or other) with:

**Input:**
```json
{
  "task": "implement",
  "microcommit": {
    "id": "COMMIT-TF-001",
    "title": "Add requirements doc",
    "description": "Create initial requirements documentation...",
    "tests": ["unit"],
    "docs": null
  },
  "context": {
    "worktree": "/path/to/worktree",
    "base_sha": "abc123",
    "project_profile": { ... },
    "plan_block": "### COMMIT-TF-001: Add requirements doc\n..."
  },
  "constraints": {
    "max_files": 10,
    "max_lines_changed": 200,
    "allowed_paths": ["requirements/", "docs/"],
    "forbidden_paths": ["secrets/", ".env"]
  }
}
```

**Expected behavior:**
1. Make changes ONLY for the specified micro-commit
2. Stay within constraints
3. Run specified tests
4. Create a commit with message: `{microcommit_id}: {title}`

**Output:**
```json
{
  "status": "success" | "failure",
  "commit_sha": "def789" | null,
  "files_changed": ["path/to/file1", "path/to/file2"],
  "lines_added": 45,
  "lines_removed": 12,
  "tests_run": ["unit"],
  "test_results": { "unit": "passed" },
  "notes": "",
  "clarification_needed": null | {
    "question": "...",
    "options": [...]
  }
}
```

**Implementation (using Claude Code CLI):**

```python
def stage_implement(ctx: Context, microcommit: MicroCommit) -> dict:
    """Execute implementation via agent."""

    # Prepare prompt
    prompt = f"""You are implementing micro-commit {microcommit.id} in the worktree at {ctx.workstream.worktree}.

## Task
{microcommit.title}

## Description
{microcommit.block_content}

## Constraints
- Change ONLY files related to this micro-commit
- Maximum {ctx.profile.commitsize_max_files} files
- Maximum {ctx.profile.commitsize_max_loc} lines changed
- Run tests: {ctx.profile.make_target_unit}

## Required
1. Implement the changes
2. Run: make {ctx.profile.make_target_unit}
3. If tests pass, commit with message: "{microcommit.id}: {microcommit.title}"

## Output
When done, output a JSON block:
```json
{{
  "status": "success",
  "commit_sha": "<sha>",
  "files_changed": [...],
  "notes": ""
}}
```

If you encounter ambiguity requiring human input, output:
```json
{{
  "status": "clarification_needed",
  "question": "...",
  "options": [...]
}}
```
"""

    # Invoke agent
    result = run_agent(
        prompt=prompt,
        cwd=ctx.workstream.worktree,
        timeout=ctx.profile.implement_timeout or 600,
        log_file=f"{ctx.run_dir}/implement.log"
    )

    # Parse output
    output = extract_json_from_output(result.stdout)

    if output.get("status") == "clarification_needed":
        # Create CLQ
        create_clarification(ctx, output)
        fail(8, "Implementation requires clarification")

    if output.get("status") != "success":
        fail(4, f"Implementation failed: {output.get('notes', 'unknown error')}")

    # Verify commit was created
    new_sha = run(f"git -C {ctx.workstream.worktree} rev-parse HEAD")
    if new_sha == ctx.workstream.base_sha:
        fail(4, "No commit was created")

    # Verify commit message
    msg = run(f"git -C {ctx.workstream.worktree} log -1 --format=%s")
    if not msg.startswith(f"{microcommit.id}:"):
        fail(4, f"Commit message doesn't start with {microcommit.id}:")

    return output
```

### 5.8 Stage: TEST

```python
def stage_test(ctx: Context, microcommit: MicroCommit) -> dict:
    """Run test suites."""

    results = {
        "suites": [],
        "overall": "passed"
    }

    # Determine which suites to run
    # MVP: Run all configured suites
    suites = [
        ("unit", ctx.profile.make_target_unit),
        ("integration", ctx.profile.make_target_integration),
        ("smoke", ctx.profile.make_target_smoke),
        ("e2e", ctx.profile.make_target_e2e),
    ]

    for name, target in suites:
        if not target:
            results["suites"].append({
                "name": name,
                "status": "skipped",
                "reason": "No target configured"
            })
            continue

        # Check target exists in Makefile
        if not make_target_exists(ctx.workstream.worktree, target):
            results["suites"].append({
                "name": name,
                "status": "skipped",
                "reason": f"Target '{target}' not in Makefile"
            })
            continue

        # Run tests
        start = time.time()
        try:
            output = run(
                f"make -C {ctx.workstream.worktree} {target}",
                timeout=ctx.profile.test_timeout or 300,
                capture=True
            )
            exit_code = 0
        except subprocess.CalledProcessError as e:
            output = e.output
            exit_code = e.returncode

        duration = time.time() - start

        suite_result = {
            "name": name,
            "status": "passed" if exit_code == 0 else "failed",
            "exit_code": exit_code,
            "duration_seconds": duration,
            "artifacts": find_test_artifacts(ctx, name)
        }

        results["suites"].append(suite_result)

        if exit_code != 0:
            results["overall"] = "failed"

    # Write test manifest
    write_json(f"{ctx.run_dir}/test_manifest.json", {
        "version": 1,
        "generated": now_iso(),
        "suites": results["suites"]
    })

    if results["overall"] == "failed":
        fail(5, "Tests failed")

    return results

def make_target_exists(worktree: str, target: str) -> bool:
    """Check if Make target exists."""
    try:
        run(f"make -C {worktree} -n {target}", capture=True)
        return True
    except:
        return False

def find_test_artifacts(ctx: Context, suite: str) -> dict:
    """Find test artifacts for a suite."""
    base = f"{ctx.workstream.worktree}/test-results/{ctx.project.name}/{suite}"
    return {
        "summary_json": f"{base}/summary.json" if Path(f"{base}/summary.json").exists() else None,
        "junit_xml": f"{base}/junit.xml" if Path(f"{base}/junit.xml").exists() else None,
    }
```

### 5.9 Stage: REVIEW

```python
def stage_review(ctx: Context, microcommit: MicroCommit) -> dict:
    """Run Claude Code review on diff."""

    # Generate diff
    diff = run(f"git -C {ctx.workstream.worktree} diff {ctx.workstream.base_sha}..HEAD")
    diff_path = f"{ctx.run_dir}/diff.patch"
    Path(diff_path).write_text(diff)

    # Prepare review prompt
    prompt = f"""Review this code change for micro-commit {microcommit.id}: {microcommit.title}

## Requirements
{microcommit.block_content}

## Diff
{diff}

## Review Criteria
1. Does the implementation match the requirements?
2. Are there any bugs or security issues?
3. Are there missing tests?
4. Is documentation needed and present?

## Output Format
Respond with ONLY a JSON object:
```json
{{
  "version": 1,
  "decision": "approve" | "request_changes",
  "blockers": [
    {{"file": "path", "line": 123, "issue": "description", "severity": "critical|major|minor", "fix_hint": "suggestion"}}
  ],
  "required_changes": ["change 1", "change 2"],
  "suggestions": ["suggestion 1"],
  "documentation": {{
    "required": true|false,
    "present": true|false,
    "quality": "adequate|good|needs_work"
  }},
  "notes": "overall assessment"
}}
```
"""

    # Invoke Claude
    result = run_claude(
        prompt=prompt,
        output_format="json",
        timeout=60,
        log_file=f"{ctx.run_dir}/review.log"
    )

    # Parse and validate JSON
    try:
        review = json.loads(result)
    except json.JSONDecodeError as e:
        fail(6, f"Review returned invalid JSON: {e}")

    # Validate schema
    if "decision" not in review:
        fail(6, "Review missing 'decision' field")

    if review["decision"] not in ("approve", "request_changes"):
        fail(6, f"Invalid decision: {review['decision']}")

    # Write review
    write_json(f"{ctx.run_dir}/claude_review.json", review)

    # Check result
    if review["decision"] == "request_changes":
        blockers = review.get("blockers", [])
        required = review.get("required_changes", [])

        if blockers or required:
            update_workstream_status(ctx.workstream, "blocked:review")
            fail(6, f"Review requested changes: {len(blockers)} blockers, {len(required)} required changes")

    return review
```

### 5.10 Stage: QA_GATE

```python
def stage_qa_gate(ctx: Context) -> bool:
    """Validate test outputs and review."""

    # Check test manifest exists
    manifest_path = f"{ctx.run_dir}/test_manifest.json"
    if not Path(manifest_path).exists():
        fail(7, "Test manifest missing")

    manifest = json.loads(Path(manifest_path).read_text())

    for suite in manifest["suites"]:
        if suite["status"] == "skipped":
            continue

        artifacts = suite.get("artifacts", {})

        # Validate summary.json if present
        if artifacts.get("summary_json"):
            try:
                json.loads(Path(artifacts["summary_json"]).read_text())
            except:
                fail(7, f"Invalid summary.json for {suite['name']}")

        # Validate junit.xml exists for unit tests
        if suite["name"] == "unit" and suite["status"] == "passed":
            if not artifacts.get("junit_xml") or not Path(artifacts["junit_xml"]).exists():
                warn(f"junit.xml missing for unit tests")
            else:
                # Basic XML validation
                content = Path(artifacts["junit_xml"]).read_text()
                if "<testsuite" not in content or "<testcase" not in content:
                    fail(7, "junit.xml appears malformed")

    # Check review exists and is valid
    review_path = f"{ctx.run_dir}/claude_review.json"
    if not Path(review_path).exists():
        fail(7, "Review JSON missing")

    review = json.loads(Path(review_path).read_text())
    if review.get("decision") != "approve":
        fail(7, "Review did not approve")

    return True
```

### 5.11 Stage: DOCS (new)

```python
def stage_docs(ctx: Context, microcommit: MicroCommit) -> bool:
    """Validate and update documentation."""

    # Check if docs are required for this commit
    review_path = f"{ctx.run_dir}/claude_review.json"
    review = json.loads(Path(review_path).read_text())

    doc_status = review.get("documentation", {})

    if doc_status.get("required") and not doc_status.get("present"):
        warn("Documentation required but not present")
        # Don't fail - this is tracked for future

    # If E2E tests ran, check for screenshots
    manifest = json.loads(Path(f"{ctx.run_dir}/test_manifest.json").read_text())
    e2e_suite = next((s for s in manifest["suites"] if s["name"] == "e2e"), None)

    if e2e_suite and e2e_suite["status"] == "passed":
        screenshots = e2e_suite.get("artifacts", {}).get("screenshots", [])
        if screenshots:
            # Update doc asset manifest
            update_doc_assets(ctx, screenshots)

    return True

def update_doc_assets(ctx: Context, screenshots: list):
    """Update documentation asset manifest with new screenshots."""

    manifest_path = f"{ctx.workstream.worktree}/docs/assets/manifest.json"

    if Path(manifest_path).exists():
        manifest = json.loads(Path(manifest_path).read_text())
    else:
        manifest = {"version": 1, "generated": now_iso(), "assets": [], "orphaned": []}

    for screenshot in screenshots:
        # Check if already tracked
        existing = next((a for a in manifest["assets"] if a["path"] == screenshot), None)

        if existing:
            existing["created"] = now_iso()
            existing["workstream"] = ctx.workstream.id
        else:
            manifest["assets"].append({
                "path": screenshot,
                "created": now_iso(),
                "source": f"e2e:{ctx.microcommit.id}",
                "referenced_by": [],
                "workstream": ctx.workstream.id,
                "commit": ctx.microcommit.id
            })

    manifest["generated"] = now_iso()
    write_json(manifest_path, manifest)
```

### 5.12 Stage: UPDATE_STATE

```python
def stage_update_state(ctx: Context, microcommit: MicroCommit):
    """Update workstream state after successful cycle."""

    # Mark micro-commit done
    mark_done(f"{ctx.workstream.dir}/plan.md", microcommit.id)

    # Get new commit SHA
    new_sha = run(f"git -C {ctx.workstream.worktree} rev-parse HEAD")

    # Update meta.env
    update_env(f"{ctx.workstream.dir}/meta.env", {
        "LAST_RUN_ID": ctx.run_id,
        "LAST_COMMIT_SHA": new_sha,
        "LAST_RESULT": "passed",
        "STATUS": "implement",  # Will be updated if all done
        "LAST_REFRESHED": now_iso()
    })

    # Check if all micro-commits done
    commits = parse_plan(f"{ctx.workstream.dir}/plan.md")
    next_commit = get_next_microcommit(commits)

    if next_commit is None:
        # All implementation done - move to UAT
        update_env(f"{ctx.workstream.dir}/meta.env", {
            "STATUS": "uat:pending"
        })
        # Generate UAT request
        generate_uat_request(ctx)
```

### 5.13 Stage: UAT_GATE (triggered after all micro-commits)

```python
def stage_uat_gate(ctx: Context) -> bool:
    """Check UAT status before allowing merge."""

    passed_dir = f"{ctx.workstream.dir}/uat/passed"
    pending_dir = f"{ctx.workstream.dir}/uat/pending"
    failed_dir = f"{ctx.workstream.dir}/uat/failed"

    passed = list(Path(passed_dir).glob("UAT-*.json"))
    pending = list(Path(pending_dir).glob("UAT-*.json"))
    failed = list(Path(failed_dir).glob("UAT-*.json"))

    if failed:
        update_workstream_status(ctx.workstream, "uat:failed")
        fail(8, f"UAT failed: {[f.stem for f in failed]}")

    if pending:
        update_workstream_status(ctx.workstream, "uat:pending")
        fail(8, f"UAT pending: {[f.stem for f in pending]}")

    if not passed:
        # Generate UAT if none exists
        generate_uat_request(ctx)
        fail(8, "UAT required but none defined")

    # All passed
    update_workstream_status(ctx.workstream, "merge-ready")
    return True

def generate_uat_request(ctx: Context):
    """Generate UAT request from requirements."""

    # Find requirements linked to this workstream
    # (This is a simplified version - real implementation would parse requirements)

    uat_id = f"UAT-{ctx.workstream.id.upper()[:3]}-001"

    uat = {
        "version": 1,
        "id": uat_id,
        "status": "pending",
        "created": now_iso(),
        "completed": None,
        "workstream": ctx.workstream.id,
        "requirements": [],  # TODO: extract from plan/stories
        "scenarios": [],     # TODO: generate from acceptance criteria
        "result": None,
        "validated_by": None,
        "issues": []
    }

    write_json(f"{ctx.workstream.dir}/uat/pending/{uat_id}.json", uat)

    # Also write human-readable version
    write_uat_markdown(ctx, uat)
```

---

## 6) Exit codes

| Code | Name | Description |
|------|------|-------------|
| 0 | SUCCESS | Operation completed successfully |
| 1 | ERROR_GENERAL | Unspecified error |
| 2 | ERROR_CONFIG | Configuration error (missing files, invalid format) |
| 3 | ERROR_LOCK | Lock acquisition failed (timeout) |
| 4 | ERROR_IMPLEMENT | Implementation failed (agent error, no commit) |
| 5 | ERROR_TEST | Tests failed |
| 6 | ERROR_REVIEW | Review failed (invalid JSON, request_changes) |
| 7 | ERROR_QA | QA gate failed (missing artifacts, validation) |
| 8 | ERROR_BLOCKED | Workstream blocked (clarification, UAT pending) |
| 9 | ERROR_INTERNAL | Internal error (unexpected exception) |

**Exit code in scripts:**
```bash
case $? in
    0) echo "Success" ;;
    2) echo "Check your configuration files" ;;
    3) echo "Another process holds the lock" ;;
    4) echo "Implementation failed - check implement.log" ;;
    5) echo "Tests failed - check test results" ;;
    6) echo "Review requested changes" ;;
    7) echo "QA validation failed" ;;
    8) echo "Workstream blocked - human action required" ;;
    *) echo "Error: $?" ;;
esac
```

---

## 7) Logging

### 7.1 `commands.log` format

```
[2025-01-15T12:00:00Z] [CWD:/path/to/worktree] [CMD:git status] [EXIT:0]
[2025-01-15T12:00:01Z] [CWD:/path/to/worktree] [CMD:make test-unit] [EXIT:0]
--- STDOUT ---
Running tests...
All tests passed.
--- END STDOUT ---
[2025-01-15T12:01:30Z] [CWD:/path/to/worktree] [CMD:git commit -m "..."] [EXIT:0]
```

**Implementation:**
```python
def run_logged(cmd: str, cwd: str, log_file: str, **kwargs):
    """Run command with full logging."""

    timestamp = datetime.now().isoformat()

    with open(log_file, 'a') as f:
        f.write(f"[{timestamp}] [CWD:{cwd}] [CMD:{cmd}]\n")

        result = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True, **kwargs
        )

        f.write(f"[EXIT:{result.returncode}]\n")

        if result.stdout:
            f.write("--- STDOUT ---\n")
            f.write(result.stdout)
            f.write("\n--- END STDOUT ---\n")

        if result.stderr:
            f.write("--- STDERR ---\n")
            f.write(result.stderr)
            f.write("\n--- END STDERR ---\n")

        f.write("\n")

    return result
```

### 7.2 `env_snapshot.txt` format

```
# Environment Snapshot
# Generated: 2025-01-15T12:00:00Z

## System
OS: Linux 5.15.0
Shell: /bin/bash

## Tools
git: 2.39.0
node: 20.10.0
npm: 10.2.0
python: 3.11.0
make: GNU Make 4.3

## Claude Code
claude --version: claude-code 1.0.0

## Docker (if available)
docker: Docker version 24.0.0

## Environment (non-secret)
AOS_LOG_LEVEL: info
AOS_PROJECT: myproject
```

### 7.3 Structured logging (optional enhancement)

For machine parsing, optionally write `run.jsonl`:

```jsonl
{"ts":"2025-01-15T12:00:00Z","stage":"load","status":"start"}
{"ts":"2025-01-15T12:00:01Z","stage":"load","status":"complete","duration":0.5}
{"ts":"2025-01-15T12:00:01Z","stage":"select","status":"start"}
{"ts":"2025-01-15T12:00:01Z","stage":"select","status":"complete","microcommit":"COMMIT-TF-001"}
...
```

---

## 8) Test framework contract

### 8.1 Make targets (default)

AOS expects these targets if using Make:
```makefile
test-unit:        # Fast unit tests
test-integration: # Integration tests (may need services)
test-smoke:       # Quick sanity checks
test-e2e:         # End-to-end tests (slowest)
```

### 8.2 Alternative runners

If not using Make, configure in `project_profile.env`:
```env
# Disable Make, use direct commands
MAKEFILE_PATH=
TEST_CMD_UNIT="npm run test:unit"
TEST_CMD_INTEGRATION="npm run test:integration"
TEST_CMD_SMOKE=""
TEST_CMD_E2E="npm run test:e2e"
```

### 8.3 Test output contract

All test suites MUST produce:
```
test-results/{project}/{suite}/
  summary.json    # Required: {"passed": N, "failed": N, "skipped": N}
  junit.xml       # Required for unit tests
  coverage/       # Optional
  screenshots/    # Optional (E2E)
```

**summary.json schema:**
```json
{
  "passed": 42,
  "failed": 0,
  "skipped": 3,
  "total": 45,
  "duration_ms": 12345
}
```

---

## 9) Interview script

### 9.1 `wf interview` behavior

**Interactive mode (default):**
```
$ wf interview

=== AOS Project Interview ===

This will configure your project for AOS. Press Ctrl+C to abort.

1. Product repository path
   [default: /home/user/dev/myproject]:

2. Default branch name
   [default: main]:

3. Makefile location (relative to repo root)
   [default: Makefile]:

4. Test targets (leave blank to skip):
   - Unit tests target [default: test-unit]:
   - Integration tests target [default: test-integration]:
   - Smoke tests target [default: test-smoke]:
   - E2E tests target [default: test-e2e]:

5. Documentation path (relative to repo root)
   [default: docs]:

6. HITL mode (strict/stage-only/exceptions/off)
   [default: strict]:

7. Raw requirements paths (space-separated)
   [default: requirements/raw]:

Writing project_profile.env... done
Writing project_profile.md... done

Project configured successfully!
```

**Non-interactive mode:**
```bash
wf interview --non-interactive \
  --repo-path /path/to/repo \
  --default-branch main \
  --make-target-unit test-unit \
  --hitl-mode strict
```

### 9.2 Validation

- `REPO_PATH`: Must exist and be a git repository
- `MAKEFILE_PATH`: If provided, must exist
- `HITL_MODE`: Must be one of: `strict`, `stage-only`, `exceptions`, `off`
- Paths: Must not contain `..` or absolute paths outside repo

---

## 10) Golden run scenarios

### 10.1 Happy path: Single micro-commit cycle

**Preconditions:**
```bash
# Workflow repo exists
ls ~/dev/myproject.ops/workflow/

# Project configured
cat ~/dev/myproject.ops/workflow/projects/myproject/project.env
cat ~/dev/myproject.ops/workflow/projects/myproject/project_profile.env

# Workstream exists with one undone micro-commit
wf new tf "Test framework" "mk/ tests/"
# plan.md has: ### COMMIT-TF-001: Add test harness
#              Done: [ ]
```

**Execution:**
```bash
wf run tf --once
```

**Expected results (MUST):**
1. Exit code: `0`
2. Run directory created: `~/dev/myproject.ops/runs/YYYYMMDD-HHMMSS_myproject_tf_COMMIT-TF-001/`
3. Run directory contains:
   - `result.json` with `"status": "passed"`
   - `commands.log` with all executed commands
   - `env_snapshot.txt` with tool versions
   - `diff.patch` with the changes
   - `claude_review.json` with `"decision": "approve"`
   - `test_manifest.json` with test results
4. Worktree has new commit starting with `COMMIT-TF-001:`
5. `plan.md` updated: `Done: [x]`
6. `meta.env` updated with `LAST_RUN_ID`, `LAST_COMMIT_SHA`
7. `touched_files.txt` updated
8. `ACTIVE_WORKSTREAMS.md` updated

### 10.2 Failure: Tests fail

**Setup:** Same as 10.1, but `make test-unit` will fail.

**Expected:**
1. Exit code: `5`
2. `result.json`: `"status": "failed"`, `"failed_stage": "test"`
3. `plan.md`: Still `Done: [ ]` (not marked done)
4. `meta.env`: `STATUS=blocked:test`

### 10.3 Failure: Review requests changes

**Setup:** Same as 10.1, but Claude returns `"decision": "request_changes"`.

**Expected:**
1. Exit code: `6`
2. `result.json`: `"status": "failed"`, `"failed_stage": "review"`
3. `claude_review.json`: Contains blockers
4. `meta.env`: `STATUS=blocked:review`

### 10.4 Blocked: Clarification needed

**Setup:** Workstream has pending CLQ with `urgency: blocking`.

**Expected:**
1. Exit code: `8`
2. `result.json`: `"status": "blocked"`, `"blocked_reason": "CLQ-001"`
3. No implementation attempted
4. `meta.env`: `STATUS=blocked:clarification`

### 10.5 Complete: All micro-commits done, UAT pending

**Setup:** All micro-commits marked `Done: [x]`.

**Expected:**
1. Exit code: `8` (blocked for UAT)
2. `meta.env`: `STATUS=uat:pending`
3. UAT request generated in `uat/pending/`

### 10.6 Loop until blocked

```bash
wf run tf --loop
```

**Expected:**
- Runs cycles until:
  - All micro-commits done (then blocks for UAT), or
  - Tests fail, or
  - Review requests changes, or
  - Clarification raised
- Each cycle creates its own run directory

---

## 11) Self-testing requirements

### 11.1 Unit tests (REQUIRED)

Location: `orchestrator/tests/unit/`

| Test file | Coverage |
|-----------|----------|
| `test_envparse.py` | Safe env parsing, forbidden patterns |
| `test_planparse.py` | Micro-commit extraction, done detection |
| `test_schemas.py` | JSON schema validation |
| `test_conflicts.py` | Overlap computation |
| `test_locking.py` | Lock acquire/release |

### 11.2 Integration tests (REQUIRED)

Location: `orchestrator/tests/integration/`

**Test fixture:** A minimal git repo with:
- Simple Makefile with test targets
- Fake test output generators
- Sample plan.md

| Test | Description |
|------|-------------|
| `test_wf_new.py` | Create workstream, verify all files |
| `test_wf_run_once.py` | Run single cycle, verify output |
| `test_wf_run_fail_test.py` | Verify test failure handling |
| `test_wf_run_fail_review.py` | Verify review failure handling |
| `test_wf_clarification.py` | Verify CLQ blocking |
| `test_wf_uat.py` | Verify UAT flow |

### 11.3 Running tests

```bash
# Run all tests
make test-aos

# Run unit tests only
pytest orchestrator/tests/unit/

# Run integration tests only
pytest orchestrator/tests/integration/
```

---

## 12) Implementation boundaries

### 12.1 AOS MUST NEVER

- Commit files to product repo outside of designated worktree
- Commit workflow metadata to product repo
- Run `rm -rf` on repository roots or home directories
- Print secret environment variables or API keys
- Execute commands as root/sudo unless explicitly configured
- Modify `.git/config` in product repo
- Push to remote without explicit user action
- Run `git push --force` ever
- Access files outside of: worktree, workflow repo, ops directory

### 12.2 AOS MUST ALWAYS

- Create a run directory for every `wf run` invocation
- Write `result.json` even on failure
- Write `commands.log` for all executed commands
- Release locks on exit (including SIGTERM, SIGINT)
- Validate all external input (env files, JSON, user input)
- Use safe parsing for configuration files
- Check preconditions before destructive operations
- Preserve git history (no force operations)

### 12.3 Clarification on "product repo" vs "workflow repo"

- **Product repo** (`REPO_PATH`): The actual software being built. AOS only writes here via the worktree, and only during IMPLEMENT stage.

- **Workflow repo** (`AOS_WORKFLOW_REPO`): This repository. AOS writes plans, meta, CLQs, UAT files, etc. here.

- **Ops directory** (`AOS_OPS_DIR`): Untracked runtime data. Worktrees, run logs, caches, secrets live here.

---

## 13) Agent SDK integration (future)

This section is a placeholder for Anthropic Agents SDK integration.

### 13.1 Agent topology (planned)

```
Orchestrator (AOS)
├── Developer Agent (Codex/Claude)
│   └── Tools: file_read, file_write, bash, git
├── Reviewer Agent (Claude)
│   └── Tools: file_read, diff_analyze
├── PM Agent (Claude) [future]
│   └── Tools: requirements_parse, story_generate
└── QA Agent [future]
    └── Tools: test_run, coverage_check
```

### 13.2 Tool schemas (planned)

To be defined when Agents SDK integration begins.

---

## APPENDIX A — Quick reference

### A.1 CLI commands

```bash
# Project management
wf interview                    # Configure project
wf list                         # List active workstreams

# Workstream lifecycle
wf new <id> "<title>" "<paths>" # Create workstream
wf status <id>                  # Show status
wf refresh [<id>]               # Update touched files
wf conflicts <id>               # Check for conflicts
wf run <id> --once              # Run one cycle
wf run <id> --loop              # Run until blocked
wf merge <id>                   # Merge to main (if ready)
wf close <id>                   # Archive workstream
wf delete <id> --confirm        # Delete workstream

# Clarifications
wf clarify list                 # List pending
wf clarify show <CLQ-id>        # View details
wf clarify answer <CLQ-id>      # Answer question

# UAT
wf uat list                     # List pending
wf uat show <UAT-id>            # View details
wf uat pass <UAT-id>            # Mark passed
wf uat fail <UAT-id>            # Mark failed

# Documentation
wf docs status                  # Show coverage
wf docs cleanup                 # Remove orphaned assets
```

### A.2 Status reference

```
planning           → Working on plan
implement          → Running micro-commits
blocked:clarify    → Waiting for CLQ answers
blocked:review     → Review requested changes
blocked:test       → Tests failing
docs               → Documentation stage
uat:pending        → Waiting for UAT
uat:failed         → UAT failed
merge-ready        → All gates passed
done               → Merged and closed
```

### A.3 Exit codes reference

```
0 = success
1 = general error
2 = config error
3 = lock timeout
4 = implement failed
5 = tests failed
6 = review failed
7 = QA gate failed
8 = blocked (CLQ/UAT)
9 = internal error
```

---

*End of specification.*
