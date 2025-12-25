# Hashd - Agent Orchestration System

An AI-assisted development workflow system that coordinates LLM agents (Claude, Codex) to implement, test, and review code changes with human oversight.

## Overview

Hashd breaks down development work into **workstreams** containing **micro-commits** - small, reviewable units of work. Each micro-commit goes through:

1. **IMPLEMENT** - Codex writes the code
2. **TEST** - Automated tests run
3. **REVIEW** - Claude reviews the changes (as a senior staff engineer)
4. **HUMAN_REVIEW** - Human approves, rejects, or resets
5. **COMMIT** - Changes are committed to the branch

## Quick Start

```bash
# 1. Clone this repo
git clone https://github.com/codr1/hashd.git
cd hashd

# 2. Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

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
echo "source $(pwd)/bin/wf-completion.bash" >> ~/.bashrc
source ~/.bashrc

# 5. Create a workstream
wf new my_feature "Add user authentication"

# 6. Run the pipeline
wf run my_feature --loop
```

## Shell Completion

Tab completion is available for bash:

```bash
wf r<TAB>                    # -> wf run
wf run o<TAB>                # -> wf run open_play_rules
wf run myfeature -<TAB>      # -> --verbose --loop --once
```

The completion script is at `bin/wf-completion.bash`. Source it in your shell config as shown above.

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

## Commands

| Command | Description |
|---------|-------------|
| `wf new <id> "title"` | Create a new workstream |
| `wf list` | List all workstreams |
| `wf status <id>` | Show workstream status |
| `wf run <id>` | Run one micro-commit cycle |
| `wf run <id> --loop` | Run until blocked or complete |
| `wf run <id> --verbose` | Show implement/review exchange |
| `wf show <id>` | Show pending changes and review feedback |
| `wf approve <id>` | Approve changes for commit |
| `wf reject <id> -f "feedback"` | Reject with feedback (iterate) |
| `wf reset <id>` | Discard changes, start fresh |
| `wf merge <id>` | Merge completed workstream to main |
| `wf close <id>` | Archive without merge (abandon) |
| `wf archive` | List archived workstreams |
| `wf clarify` | List pending clarification requests |

## Workstream Lifecycle

```
new -> run -> [implement -> test -> review] x N -> human_review -> commit
                                                        |
                                                        v
                                            [approve] -> next micro-commit
                                            [reject]  -> iterate with feedback
                                            [reset]   -> discard, start fresh

All micro-commits complete -> merge -> archived
```

## Requirements

- Python 3.11+
- Git
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
```

## License

MIT
