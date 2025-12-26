"""
wf open - Resurrect an archived workstream.
"""

import shutil
import subprocess
from pathlib import Path

from orchestrator.lib.config import (
    ProjectConfig,
    load_workstream,
    set_current_workstream,
)


def analyze_staleness(repo_path: Path, branch: str, base_sha: str, default_branch: str):
    """
    Analyze how stale the branch is compared to main.

    Returns: (commits_behind, overlap_files, main_lines_changed, file_details)
    """
    # 1. Commits behind main
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-list", "--count", f"{branch}..{default_branch}"],
        capture_output=True, text=True
    )
    commits_behind = int(result.stdout.strip()) if result.returncode == 0 else 0

    # 2. Files this branch touched
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--name-only", f"{base_sha}..{branch}"],
        capture_output=True, text=True
    )
    branch_files = set(f for f in result.stdout.strip().split('\n') if f)

    # 3. Files main touched since base
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--name-only", f"{base_sha}..{default_branch}"],
        capture_output=True, text=True
    )
    main_files = set(f for f in result.stdout.strip().split('\n') if f)

    # 4. Overlap = potential conflicts
    overlap = branch_files & main_files

    # 5. Get line counts for overlapping files (changes on main)
    main_lines_changed = 0
    file_details = {}
    for filepath in overlap:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "diff", "--numstat", f"{base_sha}..{default_branch}", "--", filepath],
            capture_output=True, text=True
        )
        if result.stdout.strip():
            parts = result.stdout.strip().split('\t')
            if len(parts) >= 2:
                added = int(parts[0]) if parts[0] != '-' else 0
                deleted = int(parts[1]) if parts[1] != '-' else 0
                file_details[filepath] = (added, deleted)
                main_lines_changed += added + deleted

    return commits_behind, overlap, main_lines_changed, file_details


def calculate_severity(commits_behind: int, overlap_count: int, main_lines_changed: int):
    """
    Calculate conflict severity score.

    Returns: (severity: str, score: int, recommendation: str)
    """
    if overlap_count == 0:
        return ("low", 0, "Safe to reopen")

    # Score components
    file_score = min(overlap_count * 15, 50)  # 0-50 based on file count
    churn_score = min(main_lines_changed // 50, 30)  # 0-30 based on lines changed
    age_score = min(commits_behind // 10, 20)  # 0-20 based on staleness

    total = file_score + churn_score + age_score

    if total < 25:
        return ("low", total, "Minor overlap - should be fine")
    elif total < 50:
        return ("moderate", total, "Some overlap - review after opening")
    elif total < 75:
        return ("high", total, "Significant overlap - expect conflicts")
    else:
        return ("critical", total, "Major divergence - consider fresh start")


def print_staleness_report(ws_id: str, commits_behind: int, overlap: set,
                           main_lines_changed: int, file_details: dict,
                           severity: str, score: int, recommendation: str):
    """Print the staleness analysis report."""
    severity_icons = {
        "low": "\u001b[32m\u25cf LOW\u001b[0m",  # Green
        "moderate": "\u001b[33m\u25cf MODERATE\u001b[0m",  # Yellow
        "high": "\u001b[31m\u25cf HIGH\u001b[0m",  # Red
        "critical": "\u001b[31;1m\u25cf CRITICAL\u001b[0m",  # Bold red
    }

    print(f"\nWorkstream '{ws_id}' analysis:")
    print(f"  Branch: {commits_behind} commits behind main")

    if not overlap:
        print(f"  Overlap: none")
    else:
        print(f"  Overlap: {len(overlap)} files ({main_lines_changed:,} lines changed on main)")
        for filepath in sorted(overlap)[:5]:  # Show up to 5 files
            if filepath in file_details:
                added, deleted = file_details[filepath]
                print(f"    - {filepath} (+{added}/-{deleted})")
            else:
                print(f"    - {filepath}")
        if len(overlap) > 5:
            print(f"    ... and {len(overlap) - 5} more files")

    print(f"  Severity: {severity_icons.get(severity, severity)} ({score}) - {recommendation}")


def cmd_open(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Resurrect an archived workstream."""
    ws_id = args.id
    workstreams_dir = ops_dir / "workstreams"
    closed_dir = workstreams_dir / "_closed" / ws_id
    active_dir = workstreams_dir / ws_id

    # 1. Validate archived workstream exists
    if not closed_dir.exists():
        print(f"ERROR: Archived workstream '{ws_id}' not found")
        print(f"  Use 'wf archive' to list archived workstreams")
        return 2

    # 2. Check not already active
    if active_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' already exists as active")
        return 2

    # 3. Load metadata
    ws = load_workstream(closed_dir)

    # 4. Verify git branch still exists
    result = subprocess.run(
        ["git", "-C", str(project_config.repo_path),
         "show-ref", "--verify", f"refs/heads/{ws.branch}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: Branch '{ws.branch}' no longer exists")
        print(f"  Was it deleted with 'wf archive delete {ws_id}'?")
        return 1

    # 5. Analyze staleness
    commits_behind, overlap, main_lines_changed, file_details = analyze_staleness(
        project_config.repo_path, ws.branch, ws.base_sha, project_config.default_branch
    )
    severity, score, recommendation = calculate_severity(
        commits_behind, len(overlap), main_lines_changed
    )

    print_staleness_report(
        ws_id, commits_behind, overlap, main_lines_changed,
        file_details, severity, score, recommendation
    )

    # 6. For high/critical severity, require confirmation
    if severity in ("high", "critical") and not getattr(args, 'force', False):
        try:
            response = input("\nProceed anyway? [y/N]: ").strip().lower()
            if response not in ('y', 'yes'):
                print("Aborted.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1

    print("\nReopening...")

    # 7. Recreate worktree
    worktree_path = ops_dir / "worktrees" / ws_id
    if worktree_path.exists():
        print(f"  Worktree path exists, cleaning up...")
        # Try to remove stale worktree reference
        subprocess.run(
            ["git", "-C", str(project_config.repo_path), "worktree", "prune"],
            capture_output=True
        )
        if worktree_path.exists():
            shutil.rmtree(str(worktree_path))

    result = subprocess.run(
        ["git", "-C", str(project_config.repo_path),
         "worktree", "add", str(worktree_path), ws.branch],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to create worktree: {result.stderr}")
        return 1

    # 8. Update meta.env - set STATUS back to active, update WORKTREE path
    meta_path = closed_dir / "meta.env"
    content = meta_path.read_text()
    lines = content.splitlines()
    new_lines = []
    for line in lines:
        if line.startswith("STATUS="):
            new_lines.append('STATUS="active"')
        elif line.startswith("WORKTREE="):
            new_lines.append(f'WORKTREE="{worktree_path}"')
        elif line.startswith("CLOSED_AT="):
            # Remove CLOSED_AT when reopening
            continue
        else:
            new_lines.append(line)
    meta_path.write_text("\n".join(new_lines) + "\n")

    # 9. Move directory back to active
    shutil.move(str(closed_dir), str(active_dir))

    # 10. Optionally set as current workstream
    if getattr(args, 'use', False):
        set_current_workstream(ops_dir, ws_id)
        print(f"\nWorkstream '{ws_id}' reopened and set as current.")
    else:
        print(f"\nWorkstream '{ws_id}' reopened.")

    print(f"  Branch: {ws.branch}")
    print(f"  Worktree: {worktree_path}")

    if severity in ("moderate", "high", "critical"):
        print(f"\n  Note: You may need to resolve conflicts with main.")

    return 0
