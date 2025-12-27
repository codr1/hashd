# HASHD - Human-Agent Synchronized Handoff Development

*A crowd of AI agents traversing artifact state, with human oversight at the gates.*

(حشد = Arabic for "crowd")

An AI-assisted development workflow system that coordinates LLM agents (Claude, Codex) to implement, test, and review code changes with human oversight.

## Overview

Hashd breaks down development work into **workstreams** containing **micro-commits** - small, reviewable units of work. The pipeline stages are:

1. **BREAKDOWN** - Claude generates micro-commits from acceptance criteria (first run only)
2. **IMPLEMENT** - Codex writes the code
3. **TEST** - Automated tests run
4. **REVIEW** - Claude reviews the changes (as a senior staff engineer)
5. **HUMAN_REVIEW** - Human approves, rejects, or resets
6. **COMMIT** - Changes are committed to the branch

## Quick Start

```bash
# 1. Clone this repo
git clone https://github.com/codr1/hashd.git
cd hashd

# 2. Install dependencies (uses uv)
uv pip install -r requirements.txt

# 3. Configure your project
mkdir -p projects/myproject
cat > projects/myproject/project.env << 'EOF'
PROJECT_NAME="myproject"
REPO_PATH="/path/to/your/repo"
DEFAULT_BRANCH="main"
EOF

cat > projects/myproject/project_profile.env << 'EOF'
MAKEFILE_PATH="Makefile"
MAKE_TARGET_TEST="test"
IMPLEMENT_TIMEOUT="600"
REVIEW_TIMEOUT="120"
TEST_TIMEOUT="300"
EOF

# 4. Set up the wf command (optional but recommended)
mkdir -p ~/bin
ln -sf "$(pwd)/bin/wf" ~/bin/wf
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc

# 5. Enable shell completion
wf --completion bash >> ~/.bashrc
source ~/.bashrc

# 6. Plan a story from requirements
wf plan                    # Interactive discovery from REQS.md
wf approve STORY-0001      # Accept the story

# 7. Run the pipeline (creates workstream from story)
wf run STORY-0001 --loop
```

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
| `wf plan` | Plan stories from REQS.md (interactive discovery) |
| `wf plan new ["title"]` | Create ad-hoc story (not from REQS.md) |
| `wf plan clone STORY-xxx` | Clone a locked story to edit |
| `wf plan STORY-xxx` | Edit existing story (if unlocked) |
| `wf run <id> [name]` | Run workstream or create from story |
| `wf list` | List all stories and workstreams |
| `wf show <id>` | Show story or workstream details |
| `wf approve <id>` | Accept story or approve workstream gate |
| `wf merge <ws>` | Merge completed workstream to main |
| `wf close <id>` | Close story or workstream (abandon) |
| `wf watch <ws>` | Interactive TUI for monitoring |

### Supporting Commands

| Command | Description |
|---------|-------------|
| `wf use [id]` | Set/show current workstream context |
| `wf run [id] --loop` | Run until blocked or complete |
| `wf run [id] --yes` | Skip confirmation prompts |
| `wf run [id] --verbose` | Show implement/review exchange |
| `wf log [id]` | Show workstream timeline |
| `wf review [id]` | Final AI review before merge |
| `wf reject [id] -f "..."` | Reject with feedback (iterate) |
| `wf reject [id] --reset` | Discard changes, start fresh |
| `wf refresh [id]` | Refresh touched files |
| `wf conflicts [id]` | Check for file conflicts |
| `wf archive` | List archived workstreams |
| `wf open <id>` | Resurrect archived workstream |
| `wf clarify` | Manage clarification requests |

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
                                                                 |
                                                                 v
                                                     [approve] -> next micro-commit
                                                     [reject]  -> iterate with feedback
                                                     [reject --reset] -> discard changes, start fresh

                    COMPLETION
                    ==========
All micro-commits complete -> final branch review -> merge -> archived
                                    |
                                    v
                              AI reviews entire branch
                              as senior staff engineer
```

## Requirements

- Python 3.11+
- Git
- [uv](https://github.com/astral-sh/uv) - for dependency management
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

### project.env

```bash
PROJECT_NAME="myproject"      # Project identifier
REPO_PATH="/path/to/repo"     # Absolute path to git repo
DEFAULT_BRANCH="main"         # Branch to merge into
```

### project_profile.env

```bash
MAKEFILE_PATH="Makefile"      # Path to Makefile (relative to repo)
MAKE_TARGET_TEST="test"       # Make target for running tests
IMPLEMENT_TIMEOUT="600"       # Codex timeout (seconds)
REVIEW_TIMEOUT="120"          # Claude review timeout (seconds)
TEST_TIMEOUT="300"            # Test execution timeout (seconds)
BREAKDOWN_TIMEOUT="180"       # Claude breakdown generation timeout (seconds)
SUPERVISED_MODE="false"       # Pause after breakdown for human review
```

## License

BSL 1.1 (Business Source License)

See [LICENSE](LICENSE) for details.
