"""
wf merge - Merge completed workstream to main and auto-archive.

Handles merge conflicts with retry loop similar to implement/review cycle.
"""

import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional

from orchestrator.lib.config import (
    ProjectConfig,
    ProjectProfile,
    load_workstream,
    load_project_profile,
    get_current_workstream,
    clear_current_workstream,
)
from orchestrator.lib.planparse import parse_plan, get_next_microcommit
from orchestrator.agents.codex import CodexAgent
from orchestrator.pm.models import Story
from orchestrator.pm.stories import (
    find_story_by_workstream,
    mark_story_implemented,
    archive_story,
)
from orchestrator.pm.reqs_annotate import delete_reqs_sections
from orchestrator.commands.docs import run_docs_update


MAX_CONFLICT_RESOLUTION_ATTEMPTS = 3


def cmd_merge(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Merge workstream branch to main and auto-archive."""
    ws_id = args.id
    workstreams_dir = ops_dir / "workstreams"
    workstream_dir = workstreams_dir / ws_id

    if not workstream_dir.exists():
        # Check if already in _closed (idempotent)
        closed_dir = workstreams_dir / "_closed" / ws_id
        if closed_dir.exists():
            print(f"Workstream '{ws_id}' already archived")
            return 0
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    ws = load_workstream(workstream_dir)

    # Load project profile for timeouts
    project_dir = ops_dir / "projects" / project_config.name
    try:
        profile = load_project_profile(project_dir)
    except FileNotFoundError:
        profile = ProjectProfile(
            makefile_path="Makefile",
            make_target_test="test",
            merge_gate_test_target="test",
            implement_timeout=1200,
            review_timeout=300,
            test_timeout=300,
            breakdown_timeout=180,
            supervised_mode=False,
        )

    # If already merged but not archived, just archive
    if ws.status == "merged":
        print(f"Workstream already merged, completing archive...")
        return _archive_workstream(workstream_dir, workstreams_dir, ws, project_config, ops_dir)

    # If blocked on merge conflicts, check if resolved
    if ws.status == "merge_conflicts":
        return _resume_merge(args, ops_dir, project_config, ws, workstream_dir, workstreams_dir)

    # 1. Verify all micro-commits are complete
    plan_path = workstream_dir / "plan.md"
    if plan_path.exists():
        commits = parse_plan(str(plan_path))
        next_commit = get_next_microcommit(commits)
        if next_commit is not None:
            print(f"ERROR: Not all micro-commits complete")
            print(f"  Next: {next_commit.id} - {next_commit.title}")
            print(f"  Use 'wf run {ws_id}' to complete remaining work")
            return 2

    # 2. Verify no uncommitted changes in worktree
    if ws.worktree.exists():
        result = subprocess.run(
            ["git", "-C", str(ws.worktree), "status", "--porcelain"],
            capture_output=True, text=True
        )
        if result.stdout.strip():
            print("ERROR: Uncommitted changes in worktree")
            print(result.stdout)
            return 2

    # 2.4 Update SPEC.md (before merge, so it's in the commit)
    # Find story now and pass to _archive_workstream to avoid duplicate lookup
    # Note: If SPEC update succeeds but later steps fail, the SPEC commit remains
    # in the branch. This is acceptable - the commit is valid, merge just didn't complete.
    story = find_story_by_workstream(project_dir, ws.id)
    if ws.worktree.exists():
        print(f"Updating SPEC.md...")
        spec_ok, spec_content = run_docs_update(
            ws.id, ops_dir, project_config,
            timeout=300,
            spec_source_dir=ws.worktree,  # Read from worktree, not main repo
        )
        if spec_ok:
            # Write to worktree so it's part of the merge
            spec_path = ws.worktree / "SPEC.md"
            spec_path.write_text(spec_content)
            # Stage and commit
            add_result = subprocess.run(
                ["git", "-C", str(ws.worktree), "add", "SPEC.md"],
                capture_output=True, text=True
            )
            if add_result.returncode == 0:
                story_ref = f"Story: {story.id}" if story else f"Workstream: {ws.id}"
                commit_result = subprocess.run(
                    ["git", "-C", str(ws.worktree), "commit", "-m",
                     f"Update SPEC.md with implemented functionality\n\n{story_ref}"],
                    capture_output=True, text=True
                )
                if commit_result.returncode == 0:
                    print("  Committed SPEC update")
                elif "nothing to commit" in commit_result.stdout:
                    print("  SPEC unchanged")
                else:
                    print(f"  Warning: git commit failed: {commit_result.stderr.strip()}")
        else:
            print(f"  Warning: SPEC update failed: {spec_content}")

    # 2.5 Delete REQS sections for this story (before merge, so it's in the commit)
    if story and ws.worktree.exists():
        # Delete from worktree so changes are included in merge
        success, msg, extracted = delete_reqs_sections(story.id, project_config, ws.worktree)
        if not success:
            print(f"  Warning: REQS cleanup failed: {msg}")
        elif extracted:
            print(f"Cleaning REQS.md: {msg}")
            # Commit the REQS cleanup in the worktree
            reqs_file = project_config.reqs_path
            add_result = subprocess.run(
                ["git", "-C", str(ws.worktree), "add", reqs_file],
                capture_output=True, text=True
            )
            if add_result.returncode != 0:
                print(f"  Warning: git add failed: {add_result.stderr}")
            else:
                commit_result = subprocess.run(
                    ["git", "-C", str(ws.worktree), "commit", "-m",
                     f"Remove implemented requirements from REQS.md\n\nStory: {story.id}"],
                    capture_output=True, text=True
                )
                if commit_result.returncode == 0:
                    print("  Committed REQS cleanup")
                else:
                    print(f"  Warning: git commit failed: {commit_result.stderr.strip()}")

    # 3. Verify branch is ahead of base
    git_dir = str(ws.worktree) if ws.worktree.exists() else str(project_config.repo_path)
    result = subprocess.run(
        ["git", "-C", git_dir, "rev-list", "--count", f"{ws.base_sha}..{ws.branch}"],
        capture_output=True, text=True
    )
    if result.returncode != 0 or result.stdout.strip() == "0":
        print("ERROR: No commits to merge (branch is not ahead of base)")
        return 2

    commit_count = result.stdout.strip()
    print(f"Merging {commit_count} commit(s) from {ws.branch} to {project_config.default_branch}")

    repo_path = project_config.repo_path

    # 3.5 Check main repo for uncommitted changes BEFORE checkout
    result = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain"],
        capture_output=True, text=True
    )
    if result.stdout.strip():
        print("ERROR: Main repo has uncommitted changes - cannot merge")
        print(result.stdout)
        print()
        print("Options:")
        print(f"  cd {repo_path} && git stash     # Save changes for later")
        print(f"  cd {repo_path} && git checkout . # Discard changes")
        return 2

    # 4. Checkout main branch
    print(f"Checking out {project_config.default_branch}...")
    result = subprocess.run(
        ["git", "-C", str(repo_path), "checkout", project_config.default_branch],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to checkout {project_config.default_branch}")
        print(result.stderr)
        return 1

    # 5. Pull latest (optional, only if remote exists)
    result = subprocess.run(
        ["git", "-C", str(repo_path), "remote"],
        capture_output=True, text=True
    )
    if result.stdout.strip():
        print("Pulling latest...")
        pull_result = subprocess.run(
            ["git", "-C", str(repo_path), "pull", "--ff-only"],
            capture_output=True, text=True
        )
        if pull_result.returncode != 0:
            print(f"WARNING: Pull failed (continuing anyway): {pull_result.stderr.strip()}")

    # 6. Attempt merge with conflict resolution loop
    merge_msg = f"Merge {ws.branch}: {ws.title}"
    merge_result = _attempt_merge_with_retry(
        repo_path, ws, merge_msg, profile
    )

    if merge_result == "blocked":
        _update_status(workstream_dir, "merge_conflicts")
        print(f"\nBlocked: merge conflicts require human resolution")
        print(f"  Resolve conflicts in {repo_path}")
        print(f"  Then run: wf merge {ws_id}")
        return 8  # Blocked exit code

    if merge_result == "failed":
        return 1

    # Merge succeeded - update status IMMEDIATELY
    print(f"Merged: {merge_msg}")
    _update_status(workstream_dir, "merged")

    # 7. Push if requested
    if getattr(args, 'push', False):
        print("Pushing to remote...")
        result = subprocess.run(
            ["git", "-C", str(repo_path), "push"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print("WARNING: Push failed")
            print(result.stderr)

    # 8. Archive (pass story to avoid duplicate lookup)
    return _archive_workstream(workstream_dir, workstreams_dir, ws, project_config, ops_dir, story)


def _attempt_merge_with_retry(repo_path: Path, ws, merge_msg: str,
                               profile: ProjectProfile) -> str:
    """
    Attempt merge with conflict resolution retry loop.

    Returns: "success", "failed", or "blocked"
    """
    for attempt in range(1, MAX_CONFLICT_RESOLUTION_ATTEMPTS + 1):
        print(f"Merge attempt {attempt}/{MAX_CONFLICT_RESOLUTION_ATTEMPTS}...")

        result = subprocess.run(
            ["git", "-C", str(repo_path), "merge", "--no-ff", ws.branch, "-m", merge_msg],
            capture_output=True, text=True
        )

        if result.returncode == 0:
            return "success"

        # Check if this is a conflict
        if "CONFLICT" not in result.stdout and "CONFLICT" not in result.stderr:
            # Non-conflict error
            print("ERROR: Merge failed (not a conflict)")
            print(result.stderr)
            return "failed"

        print(f"Merge conflicts detected on attempt {attempt}")

        # Get list of conflicted files
        status_result = subprocess.run(
            ["git", "-C", str(repo_path), "diff", "--name-only", "--diff-filter=U"],
            capture_output=True, text=True
        )
        conflicted_files = [f.strip() for f in status_result.stdout.splitlines() if f.strip()]
        print(f"  Conflicted files: {', '.join(conflicted_files)}")

        if attempt >= MAX_CONFLICT_RESOLUTION_ATTEMPTS:
            # Abort merge, escalate to HITL
            subprocess.run(
                ["git", "-C", str(repo_path), "merge", "--abort"],
                capture_output=True, text=True
            )
            return "blocked"

        # Try to resolve conflicts with Codex
        print(f"  Attempting automatic resolution...")
        resolved = _resolve_conflicts_with_codex(
            repo_path, conflicted_files, ws, profile
        )

        if not resolved:
            # Abort this attempt, try again
            subprocess.run(
                ["git", "-C", str(repo_path), "merge", "--abort"],
                capture_output=True, text=True
            )
            continue

        # Conflicts resolved, complete the merge
        result = subprocess.run(
            ["git", "-C", str(repo_path), "commit", "-m", merge_msg],
            capture_output=True, text=True
        )

        if result.returncode == 0:
            return "success"

        # Commit failed, abort and retry
        subprocess.run(
            ["git", "-C", str(repo_path), "merge", "--abort"],
            capture_output=True, text=True
        )

    return "blocked"


def _resolve_conflicts_with_codex(repo_path: Path, conflicted_files: list,
                                   ws, profile: ProjectProfile) -> bool:
    """
    Use Codex to resolve merge conflicts.

    Returns True if conflicts resolved successfully.
    """
    # Build prompt for Codex
    conflict_details = []
    for filepath in conflicted_files:
        full_path = repo_path / filepath
        if full_path.exists():
            content = full_path.read_text()
            # Only include if it has conflict markers
            if "<<<<<<<" in content:
                conflict_details.append(f"### {filepath}\n```\n{content[:2000]}\n```")

    if not conflict_details:
        return False

    prompt = f"""Resolve these merge conflicts for workstream: {ws.title}

The following files have merge conflicts. Edit each file to resolve the conflicts
by choosing the correct code or combining both versions appropriately.

Remove all conflict markers (<<<<<<, =======, >>>>>>>) and leave only the correct code.

{chr(10).join(conflict_details)}

After resolving, stage all files with: git add <file>

Do NOT create a commit - just resolve conflicts and stage.
"""

    agent = CodexAgent(timeout=profile.implement_timeout)
    result = agent.implement(prompt, repo_path)

    if not result.success:
        print(f"    Codex failed to resolve conflicts: {result.stderr[:100]}")
        return False

    # Check if conflicts are actually resolved
    status_result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--name-only", "--diff-filter=U"],
        capture_output=True, text=True
    )

    if status_result.stdout.strip():
        print(f"    Conflicts remain after Codex attempt")
        return False

    print(f"    Conflicts resolved by Codex")
    return True


def _resume_merge(args, ops_dir: Path, project_config: ProjectConfig,
                  ws, workstream_dir: Path, workstreams_dir: Path) -> int:
    """Resume merge after human resolved conflicts."""
    repo_path = project_config.repo_path

    # Check if merge is in progress
    merge_head = repo_path / ".git" / "MERGE_HEAD"
    if merge_head.exists():
        # Human needs to complete the merge
        print("Merge in progress. Complete it with:")
        print(f"  cd {repo_path}")
        print(f"  git add . && git commit")
        print(f"  wf merge {ws.id}")
        return 1

    # Check if conflicts are resolved (merge completed)
    # Verify branch is now merged into main
    result = subprocess.run(
        ["git", "-C", str(repo_path), "branch", "--contains", ws.branch, project_config.default_branch],
        capture_output=True, text=True
    )

    if project_config.default_branch not in result.stdout:
        # Not merged yet - reset status and try again
        _update_status(workstream_dir, "active")
        print("Merge not complete. Retrying...")
        return cmd_merge(args, ops_dir, project_config)

    # Merge was completed - update status and archive
    print("Merge completed. Archiving...")
    _update_status(workstream_dir, "merged")

    if getattr(args, 'push', False):
        print("Pushing to remote...")
        subprocess.run(
            ["git", "-C", str(repo_path), "push"],
            capture_output=True, text=True
        )

    return _archive_workstream(workstream_dir, workstreams_dir, ws, project_config, ops_dir)


def _update_status(workstream_dir: Path, status: str):
    """Update STATUS in meta.env."""
    meta_path = workstream_dir / "meta.env"
    content = meta_path.read_text()
    lines = content.splitlines()

    status_updated = False
    for i, line in enumerate(lines):
        if line.startswith("STATUS="):
            lines[i] = f'STATUS="{status}"'
            status_updated = True
            break

    if not status_updated:
        lines.append(f'STATUS="{status}"')

    # Add MERGED_AT timestamp only if not already present
    if status == "merged" and not any(line.startswith("MERGED_AT=") for line in lines):
        lines.append(f'MERGED_AT="{datetime.now().isoformat()}"')

    meta_path.write_text("\n".join(lines) + "\n")


def _archive_workstream(workstream_dir: Path, workstreams_dir: Path,
                        ws, project_config: ProjectConfig, ops_dir: Path,
                        story: Optional[Story] = None) -> int:
    """Archive workstream to _closed/."""
    print("Archiving workstream...")
    repo_path = project_config.repo_path

    # Remove worktree if exists
    if ws.worktree.exists():
        result = subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "remove", str(ws.worktree)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(ws.worktree)],
                capture_output=True, text=True
            )

    # Move to _closed/
    closed_dir = workstreams_dir / "_closed"
    closed_dir.mkdir(exist_ok=True)
    dest = closed_dir / workstream_dir.name

    if dest.exists():
        print(f"WARNING: {dest} already exists, overwriting")
        shutil.rmtree(str(dest))

    shutil.move(str(workstream_dir), str(dest))

    # Clear context if this was the current workstream
    if get_current_workstream(ops_dir) == ws.id:
        clear_current_workstream(ops_dir)

    # Archive associated story
    project_dir = ops_dir / "projects" / project_config.name
    if not story:
        story = find_story_by_workstream(project_dir, ws.id)
    if story:
        mark_story_implemented(project_dir, story.id)
        if archive_story(project_dir, story.id):
            print(f"  Story '{story.id}' archived")

    print(f"\nWorkstream '{ws.id}' merged and archived.")
    print(f"  Branch '{ws.branch}' preserved in git history")

    return 0
