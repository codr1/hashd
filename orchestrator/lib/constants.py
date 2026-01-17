"""Shared constants for the orchestrator."""

import re

# Workstream ID validation
WS_ID_PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')
MAX_WS_ID_LEN = 16

# Human gate action constants (used by approve/reject/reset commands and stages)
ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"

# Flow status constants (used by run_once return values)
STATUS_HUMAN_GATE_DONE = "human_gate_done"

# Exit codes for CLI commands
# These provide consistent, documented exit status across all commands.
# Scripts can use these to distinguish failure modes programmatically.
EXIT_SUCCESS = 0           # Command completed successfully
EXIT_ERROR = 1             # General error (Prefect failure, git error, etc.)
EXIT_NOT_FOUND = 2         # Resource not found (workstream, story, project doesn't exist)
EXIT_LOCK_TIMEOUT = 3      # Could not acquire workstream lock
EXIT_TOOL_MISSING = 4      # Required tool binary not found (codex, claude)
EXIT_INVALID_STATE = 5     # Resource exists but in wrong state (e.g., uncommitted changes, wrong status)
EXIT_BLOCKED = 8           # Blocked by human gate or merge conflicts
