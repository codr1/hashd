"""Git status operations."""

from pathlib import Path

from orchestrator.git.runner import run_git


def has_uncommitted_changes(worktree: Path) -> bool:
    """Check if worktree has any uncommitted changes (staged, unstaged, or untracked)."""
    result = run_git(["status", "--porcelain"], worktree)
    return bool(result.stdout.strip())


def get_status_porcelain(worktree: Path) -> str:
    """Get git status in porcelain format."""
    result = run_git(["status", "--porcelain"], worktree)
    return result.stdout


def get_changed_files(worktree: Path) -> list[str]:
    """Get list of changed files (staged + unstaged + untracked).

    Uses -z for null-separated output to handle filenames with spaces/special chars.
    Returns empty list on git failure (e.g., not a repo).
    """
    result = run_git(["status", "--porcelain", "-z"], worktree)
    if not result.success or not result.stdout:
        return []

    files = []
    # -z format: "XY filename\0" or "XY old\0new\0" for renames
    entries = result.stdout.split('\0')
    i = 0
    while i < len(entries):
        entry = entries[i]
        if not entry:
            i += 1
            continue

        if len(entry) < 3:
            i += 1
            continue

        status = entry[:2]
        filename = entry[3:]

        # Renames (R) and copies (C) have a second entry with the new name
        if status[0] in ('R', 'C') and i + 1 < len(entries):
            # For renames, report the new name (destination)
            files.append(entries[i + 1])
            i += 2
        else:
            files.append(filename)
            i += 1

    return files


def get_staged_stat(worktree: Path) -> str:
    """Get stat of staged changes."""
    result = run_git(["diff", "--cached", "--stat"], worktree)
    return result.stdout.strip()


def get_unstaged_stat(worktree: Path) -> str:
    """Get stat of unstaged changes."""
    result = run_git(["diff", "--stat"], worktree)
    return result.stdout.strip()


def get_untracked_files(worktree: Path) -> list[str]:
    """Get list of untracked files."""
    result = run_git(["ls-files", "--others", "--exclude-standard"], worktree)
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]
