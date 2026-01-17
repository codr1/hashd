# HASHD - Human-Agent Synchronized Handoff Development

*A crowd of AI agents traversing artifact state, with human oversight at the gates.*

(حشد = Arabic for "crowd")

A full-lifecycle AI development platform that coordinates LLM agents with mandatory human oversight at every gate. Not just implementation - planning, QA, review, and UAT.

## Overview

Hashd orchestrates the entire development lifecycle:

| Phase | Agent | What Happens |
|-------|-------|--------------|
| **Plan** | Claude (PM) | Analyzes REQS.md, proposes stories, generates acceptance criteria |
| **Breakdown** | Claude (Architect) | Decomposes stories into micro-commits with implementation guidance |
| **Implement** | Codex | Writes code in isolated worktree |
| **Test** | Automated | Runs test suite, validates artifacts |
| **Review** | Claude (Staff Engineer) | Structured review with approve/block/request-changes |
| **QA Gate** | Validation | Confirms test + review artifacts before commit |
| **Human UAT** | You | Approve, reject with feedback, or reset entirely |
| **Merge Gate** | Claude + Tests | Full suite + rebase check; AI generates fixes if needed |
| **Final Review** | Claude | Holistic branch review before merge |

Human gates are **mandatory**, not advisory. The clarification queue blocks workstreams until you answer. Every run produces auditable artifacts in `runs/`.

## Human-in-the-Loop

- **Clarification Queue** - Agents raise questions; workstream blocks until you answer (`wf clarify`)
- **Approve/Reject/Reset** - Accept changes, iterate with feedback, or discard entirely
- **Interactive TUI** - `wf watch` for real-time monitoring of workstreams and stories with keyboard shortcuts
- **Desktop Notifications** - Get alerted when workstreams need attention
- **Parallel Workstreams** - Run multiple features simultaneously in isolated worktrees
- **Conflict Detection** - `wf conflicts` warns about overlapping file changes

## Quick Start

```bash
# 1. Clone this repo
git clone https://github.com/codr1/hashd.git
cd hashd

# 2. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
# Or use your package manager:
#   snap install astral-uv          # Ubuntu/Debian/Fedora with snap
#   dnf install uv                  # Fedora 41+
#   brew install uv                 # Homebrew

# 3. Set up the wf command
mkdir -p ~/.local/bin
ln -sf "$(pwd)/bin/wf" ~/.local/bin/wf

# Most distros have ~/.local/bin in PATH already. If not:
# echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc  # or ~/.zshrc
# source ~/.bashrc

# 4. Verify wf is working
wf --help

# 5. Enable shell completion
wf --completion bash >> ~/.bashrc
source ~/.bashrc

# 6. Register your project (runs interactive setup)
wf project add /path/to/your/repo

# 7. Plan a story from requirements
wf plan                    # Discover from REQS.md, saves suggestions
wf plan list               # View suggestions
wf plan new 1              # Create story from suggestion 1
wf approve STORY-0001      # Accept the story

# Or quick mode (skips REQS discovery)
wf plan story "add logout button"
wf plan bug "fix null pointer" -f "crashes on empty input"

# 8. Run the pipeline (creates workstream from story)
wf run STORY-0001 --loop
```

The `wf project add` command auto-detects your build system (Makefile, package.json, etc.) and prompts you to configure test commands, merge mode, and other settings.

## Shell Completion

Tab completion is available for bash, zsh, and fish:

```bash
# Bash
wf --completion bash >> ~/.bashrc

# Zsh
wf --completion zsh >> ~/.zshrc

# Fish
wf --completion fish > ~/.config/fish/completions/wf.fish
```

Examples:
```bash
wf r<TAB>                    # -> wf run
wf run o<TAB>                # -> wf run open_play_rules
wf run STORY-<TAB>           # -> wf run STORY-0001
wf show <TAB>                # Shows both stories and workstreams
```

## Parallel Workstreams

Hashd supports running multiple workstreams simultaneously. Each workstream gets its own git worktree and lock file, allowing true parallel development:

```bash
# Terminal 1
./bin/wf run feature_auth --loop

# Terminal 2 (at the same time)
./bin/wf run feature_api --loop

# Terminal 3
./bin/wf run bugfix_123 --loop
```

A warning is shown when more than 3 workstreams are running concurrently (to avoid API rate limits).

## Desktop Notifications

Hashd sends desktop notifications when workstreams need attention:

| Event | Urgency | When |
|-------|---------|------|
| Ready for review | normal | Human approval needed |
| Blocked | critical | Clarification needed or other blocker |
| Complete | low | All micro-commits done |
| Failed | critical | Stage failure |

Works with any freedesktop-compliant notification daemon (mako, dunst, GNOME, KDE).

Requires `notify-send` to be installed:
```bash
# Debian/Ubuntu
sudo apt install libnotify-bin

# Arch
sudo pacman -S libnotify
```

## Workstream Context

Set a current workstream to avoid typing it repeatedly:

```bash
wf use my_feature        # Set current workstream
wf run --loop            # Operates on my_feature
wf approve               # Still my_feature
wf show                  # Still my_feature

wf use                   # Show current workstream
wf use --clear           # Clear current workstream
```

When a workstream context is set, you can still override it explicitly:

```bash
wf use my_feature
wf show other_feature  # Operates on other_feature, context unchanged
```

## Commands

### Core Commands

| Command | Description |
|---------|-------------|
| `wf plan` | Plan stories from REQS.md (saves suggestions) |
| `wf plan list` | View current suggestions |
| `wf plan new <id_or_name>` | Create story from suggestion (by number or name match) |
| `wf plan story "title"` | Quick feature story (skips REQS discovery) |
| `wf plan bug "title"` | Quick bug fix (skips REQS discovery, conditional SPEC update) |
| `wf plan clone STORY-xxx` | Clone a locked story to edit |
| `wf plan edit STORY-xxx` | Edit existing story (if unlocked) |
| `wf plan add <ws> "title"` | Add micro-commit to existing workstream |
| `wf run <id> [name]` | Run workstream or create from story |
| `wf list` | List all stories and workstreams |
| `wf show <id>` | Show story or workstream details |
| `wf approve <id>` | Accept story or approve workstream gate |
| `wf pr <ws>` | Create GitHub PR (github_pr mode) |
| `wf pr feedback <ws>` | View PR comments from GitHub |
| `wf merge <ws>` | Merge completed workstream to main |
| `wf close <id>` | Close story or workstream (abandon) |
| `wf watch [id]` | Interactive TUI (dashboard, or detail for workstream/STORY-xxxx) |

### Watch UI Keybindings

The `wf watch` TUI adapts keybindings to workstream status:

| Status | Key Actions |
|--------|-------------|
| `awaiting_human_review` | `[a]` approve, `[r]` reject, `[R]` reset |
| `complete` | `[P]` create PR, `[m]` merge, `[e]` edit microcommit |
| `pr_open` / `pr_approved` | `[r]` reject (pre-fills PR feedback), `[o]` open PR, `[a]` merge |

In PR states, `[r]` opens a modal pre-filled with GitHub feedback for editing.

### Supporting Commands

| Command | Description |
|---------|-------------|
| `wf use [id]` | Set/show current workstream context |
| `wf run [id] --loop` | Run until blocked or complete |
| `wf run [id] --yes` | Skip confirmation prompts |
| `wf run [id] --verbose` | Show implement/review exchange |
| `wf log [id]` | Show workstream timeline |
| `wf review [id]` | Final AI review before merge |
| `wf reject [id] -f "..."` | Reject with feedback (context-aware) |
| `wf reject [id] --reset` | Discard changes, start fresh (human gate only) |
| `wf refresh [id]` | Refresh touched files |
| `wf conflicts [id]` | Check for file conflicts |
| `wf archive work` | List archived workstreams |
| `wf archive stories` | List archived stories |
| `wf open <id>` | Resurrect archived workstream |
| `wf clarify` | Manage clarification requests |
| `wf directives` | View/edit project directives |

### Project Commands

| Command | Description |
|---------|-------------|
| `wf project add <path>` | Register a new project (runs interactive setup) |
| `wf project add <path> --no-interview` | Quick register without interactive setup |
| `wf project list` | List registered projects |
| `wf project use <name>` | Set active project context |
| `wf project show` | Show current project configuration |
| `wf interview` | Update existing project configuration interactively |

### Smart ID Routing

Commands automatically route based on ID prefix:
- `STORY-xxx` - Routes to story commands (e.g., `wf show STORY-0001`)
- `lowercase_id` - Routes to workstream commands (e.g., `wf show my_feature`)

Commands marked with `[id]` use the current workstream context if no ID is provided.

When reopening archived workstreams, `wf open` analyzes staleness by comparing file changes on the branch vs main. It shows a severity score (LOW/MODERATE/HIGH/CRITICAL) and prompts for confirmation if conflicts are likely.

## Lifecycle

### Story Lifecycle

```
draft -> accepted -> implementing -> implemented
         (editable)    (LOCKED)       (LOCKED)
```

- Stories in `draft` can be edited with `wf plan STORY-xxx`
- `wf approve STORY-xxx` moves draft to accepted (ready for implementation)
- `wf run STORY-xxx` creates workstream and locks the story
- `wf close <workstream>` unlocks the linked story
- `wf merge <workstream>` marks story as implemented

### Workstream Lifecycle

```
                    MICRO-COMMIT LOOP
                    =================
run -> breakdown -> [implement -> test -> review -> human_review -> commit] x N
                         ^                                       |
                         |                                       v
                         |                           [approve] -> next micro-commit
                         |                           [reject]  -> iterate with feedback
                         |                           [reject --reset] -> discard changes
                         |
                    MERGE GATE
                    ==========
All micro-commits complete -> MERGE_GATE (full test suite + rebase check)
                                    |
                              ┌─────┴─────┐
                              |           |
                            PASS        FAIL
                              |           |
                              v           v
                           MERGE    FIX_GENERATION
                              |           |
                              v           └──> AI generates fix commits
                          archived             (loops back to SELECT)
```

## Context-Aware Reject

The `wf reject` command adapts its behavior based on workstream state:

### During Human Review Gate

When status is `awaiting_human_review` (mid-micro-commit):

```bash
wf reject my_feature -f "Fix the null check"    # Iterate with feedback
wf reject my_feature --reset                     # Discard, start fresh
```

This writes a rejection file and continues the run loop.

### After All Commits Complete

When all micro-commits are done (pre-merge):

```bash
wf reject my_feature                             # Uses final_review.md concerns
wf reject my_feature -f "Also fix the tests"     # Add guidance
```

This:
1. Parses `final_review.md` for concerns (## Concerns section)
2. Generates a fix micro-commit (COMMIT-*-FIX-001)
3. Appends it to plan.md
4. Sets status back to `active`

### After PR Created

When a GitHub PR exists:

```bash
wf pr feedback my_feature                        # View PR comments
wf reject my_feature -f "Fix the null check"     # Create fix commit
```

For PR states (`pr_open`, `pr_approved`):
- `-f` flag is **required** (no auto-fetch)
- Use `wf pr feedback` to view comments first
- In `wf watch`, the `[r]` modal pre-fills with PR feedback for editing

## Automatic Retries

Hashd uses [Prefect](https://www.prefect.io/) to automatically retry transient failures:

| Stage | Retries | Delay | Handles |
|-------|---------|-------|---------|
| implement | 2 | 10s | Codex timeouts, API errors |
| test | 2 | 5s | Subprocess timeouts |
| review | 1 | 30s | Claude rate limits |
| qa_gate | 1 | 5s | Validation errors |
| update_state | 2 | 5s | Git push failures |

This is transparent - `wf run` works exactly as before. Prefect handles retries automatically without any configuration.

## Requirements

- Python 3.11+
- Git
- [uv](https://github.com/astral-sh/uv) - for dependency management
- [Prefect](https://www.prefect.io/) - for workflow orchestration (automatic retry)
- [Claude CLI](https://github.com/anthropics/claude-cli) - for code review
- [Codex CLI](https://github.com/openai/codex) - for implementation
- A project with a Makefile and test target

## Directory Structure

```
hashd/
├── bin/wf                  # CLI entrypoint
├── orchestrator/           # Core orchestration code
│   ├── agents/             # LLM agent wrappers
│   ├── commands/           # CLI command implementations
│   ├── lib/                # Config, parsing, validation
│   └── runner/             # Stage execution engine
├── projects/               # Project configurations
│   └── <project>/
│       ├── project.env
│       └── project_profile.env
├── workstreams/            # Active workstreams
├── worktrees/              # Git worktrees for isolation
├── runs/                   # Execution logs
└── schemas/                # JSON schemas for validation
```

## Configuration

Configuration files are generated by `wf project add` or `wf interview`. You can also edit them manually.

### project.env

```bash
PROJECT_NAME="myproject"      # Project identifier
REPO_PATH="/path/to/repo"     # Absolute path to git repo
DEFAULT_BRANCH="main"         # Branch to merge into
REQS_PATH="REQS.md"           # Requirements file (relative to repo)
```

### project_profile.env

```bash
# Test/Build Commands (command-based - recommended)
TEST_CMD="make test"          # Command to run tests (e.g., "npm test", "pytest")
BUILD_CMD=""                  # Optional build command before tests
MERGE_GATE_TEST_CMD="make test"  # Full test suite at merge gate (defaults to TEST_CMD)

# Merge Settings
MERGE_MODE="local"            # "local" or "github_pr"
SUPERVISED_MODE="false"       # See "Autonomy Modes" below

# Timeouts (seconds)
IMPLEMENT_TIMEOUT="1200"      # Codex timeout
REVIEW_TIMEOUT="900"          # Claude review timeout
TEST_TIMEOUT="300"            # Test execution timeout
BREAKDOWN_TIMEOUT="180"       # Claude breakdown generation timeout
```

**Note:** Legacy target-based config (`BUILD_RUNNER`, `TEST_TARGET`) is still supported for backward compatibility.

### Autonomy Modes

Hashd currently supports two modes controlled by `SUPERVISED_MODE`:

| Mode | Setting | Behavior |
|------|---------|----------|
| **Gatekeeper** | `false` (default) | Runs autonomously between gates, blocks at human checkpoints (clarifications, merge gate) |
| **Supervised** | `true` | Also pauses after breakdown for human review of plan.md |

Default is gatekeeper mode - the pipeline runs autonomously but requires human approval at gates.

## Merge Behavior

### Automatic Conflict Resolution

When using GitHub PR mode (`MERGE_MODE="github_pr"`), PRs may become conflicting if main moves ahead. The merge command handles this automatically:

1. Fetches latest main
2. Attempts rebase of the PR branch
3. Force-pushes rebased branch (using `--force-with-lease`)
4. Re-checks PR status

If rebase fails due to merge conflicts, blocks for human resolution with instructions.

### Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Force push loses work | `--force-with-lease` prevents overwriting if branch changed |
| Infinite rebase loop | Max 3 attempts before blocking for human |
| GitHub API timing | 2s delay after push; worst case run `wf merge` again |
| Review bypass | Checks for `REVIEW_REQUIRED` status from GitHub |

### Review Requirements

The merge respects GitHub's configured review requirements:

- **APPROVED** - Merge proceeds
- **PENDING/None** - Merge proceeds (assumes no review required)
- **CHANGES_REQUESTED** - Blocks; use `wf reject` to generate fix commit from PR feedback
- **REVIEW_REQUIRED** - Blocks until required reviews complete

### Check Requirements

- **success** - Merge proceeds
- **pending** - Merge proceeds (for slow bots like CodeRabbit)
- **failure** - Blocks until checks pass

<!-- TODO: Reassess pending check behavior. Currently allows merge with pending checks
     to avoid blocking on slow bots (CodeRabbit). Consider:
     - Configurable list of ignorable checks
     - Timeout-based promotion of pending to success
     - Separate "required" vs "optional" check categories
-->

## Agent Configuration

By default, hashd uses **Codex** for implementation and **Claude** for everything else. You can override which tool runs each stage by creating an `agents.yaml` file in your project directory.

### Quick Setup

If you don't have Codex installed and want to use Claude for everything:

```bash
# Create agents.yaml in your project directory
cat > projects/myproject/agents.yaml << 'EOF'
stages:
  implement: claude --dangerously-skip-permissions --cwd {worktree} -p {prompt}
  implement_resume: claude --continue --dangerously-skip-permissions --cwd {worktree} -p {prompt}
EOF
```

### Configuration File

Copy `agents.sample.yaml` to `projects/<name>/agents.yaml` and uncomment the stages you want to override:

```bash
cp agents.sample.yaml projects/myproject/agents.yaml
```

The sample file contains all default commands (commented out) plus examples.

### Stage Reference

| Phase | Stage | Default Tool | Description |
|-------|-------|--------------|-------------|
| Planning | `pm_discovery` | claude | Analyze REQS.md for story candidates |
| Planning | `pm_refine` | claude | Refine story with feedback |
| Planning | `pm_edit` | claude | Edit existing story |
| Planning | `pm_annotate` | claude | Mark up REQS.md with WIP annotations |
| Implementation | `breakdown` | claude | Decompose story into micro-commits |
| Implementation | `implement` | codex | First implementation attempt |
| Implementation | `implement_resume` | codex | Retry after review rejection |
| Implementation | `review` | claude | Review implementation |
| Implementation | `review_resume` | claude | Re-review after fixes |
| Implementation | `fix_generation` | claude | Generate fix commits for test failures |
| Implementation | `plan_add` | claude | Add micro-commit to plan |
| Completion | `final_review` | claude | Holistic branch review |
| Completion | `pm_spec` | claude | Generate SPEC.md content |
| Completion | `pm_docs` | claude | Generate documentation |

### Template Variables

Command templates support these variables:

| Variable | Description | Used In |
|----------|-------------|---------|
| `{prompt}` | The prompt text | All stages |
| `{worktree}` | Path to git worktree | `implement`, `implement_resume` |
| `{session_id}` | Session UUID for resuming | `implement_resume` |

If `{prompt}` is in the command template, it's passed as a CLI argument. Otherwise, the prompt is passed via stdin (useful for multi-line prompts).

### Missing Tool Detection

If a required tool isn't installed, hashd will fail early with a clear error:

```
ERROR: Required tool 'codex' is not installed.

Stages that need it: implement, implement_resume

To fix this, either:
  1. Install codex: https://github.com/openai/codex
  2. Create agents.yaml in your project directory to use a different tool:
     ...
```

## Local-Only Mode

Hashd works without a git remote configured. When no `origin` remote exists:

- Rebase checks are skipped (no fetch/rebase against remote main)
- Conflict detection against remote is skipped
- PR features are unavailable
- Workstreams complete locally after tests pass

This is useful for:
- Local experimentation before pushing
- Air-gapped development environments
- Learning hashd without setting up a remote

To enable full features later:
```bash
git remote add origin <url>
```

## License

BSL 1.1 (Business Source License)

See [LICENSE](LICENSE) for details.
