"""Git operations for HASHD orchestrator.

This module provides clean interfaces for git operations.
New code should use these functions instead of direct subprocess calls.

Return type conventions:
- Functions returning GitResult: Caller must check .success before using output.
  Examples: stage_files(), commit(), fetch(), push()
- Functions returning bool: True on success/condition met, False otherwise.
  Examples: has_uncommitted_changes(), branch_exists(), is_ancestor()
- Functions returning parsed values (str, int, list): Return empty/zero on failure.
  Examples: get_changed_files() -> [], get_commit_count() -> 0
"""

from orchestrator.git.status import (
    has_uncommitted_changes,
    get_status_porcelain,
    get_changed_files,
    get_staged_stat,
    get_unstaged_stat,
    get_untracked_files,
)
from orchestrator.git.diff import (
    has_changes_vs_head,
    get_diff_check,
    get_diff_names,
    get_conflicted_files,
)
from orchestrator.git.branch import (
    get_current_branch,
    branch_exists,
    get_commit_sha,
    commit_exists,
    get_commit_count,
    is_ancestor,
    get_log_oneline,
    get_divergence_count,
)
from orchestrator.git.commit import (
    stage_files,
    stage_all,
    commit,
    reset_worktree,
    checkout_file,
)
from orchestrator.git.remote import (
    has_remote,
    fetch,
    push,
    push_set_upstream,
    pull_ff_only,
    checkout_branch,
)

__all__ = [
    # status
    "has_uncommitted_changes",
    "get_status_porcelain",
    "get_changed_files",
    "get_staged_stat",
    "get_unstaged_stat",
    "get_untracked_files",
    # diff
    "has_changes_vs_head",
    "get_diff_check",
    "get_diff_names",
    "get_conflicted_files",
    # branch
    "get_current_branch",
    "branch_exists",
    "get_commit_sha",
    "commit_exists",
    "get_commit_count",
    "is_ancestor",
    "get_log_oneline",
    "get_divergence_count",
    # commit
    "stage_files",
    "stage_all",
    "commit",
    "reset_worktree",
    "checkout_file",
    # remote
    "has_remote",
    "fetch",
    "push",
    "push_set_upstream",
    "pull_ff_only",
    "checkout_branch",
]
