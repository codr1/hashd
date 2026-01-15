"""Merge gate logic for workstream completion.

Handles:
- Running merge gate checks (tests, rebase status, conflicts)
- Automatic rebase with conflict resolution
- Fix commit generation for test failures
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from orchestrator.runner.stages import StageError
from orchestrator.runner.impl.stages import stage_merge_gate
from orchestrator.runner.impl.fix_generation import generate_fix_commits
from orchestrator.runner.impl.breakdown import append_commits_to_plan
from orchestrator.lib.planparse import parse_plan
from orchestrator.notifications import notify_blocked

if TYPE_CHECKING:
    from orchestrator.runner.context import RunContext

logger = logging.getLogger(__name__)


def run_merge_gate(ctx: "RunContext") -> tuple[str, int]:
    """Run the merge gate after all micro-commits complete.

    On success: returns ("merge_ready", 0)
    On failure:
        - test_failure: generates fix commits, returns ("fixed", 0) or ("blocked", 8)
        - rebase/conflict: blocks immediately for human intervention

    Args:
        ctx: RunContext with workstream, project, and profile info

    Returns:
        Tuple of (status, exit_code)
    """
    ctx.log("All micro-commits complete - running merge gate")
    print("\n" + "="*60)
    print("=== MERGE GATE ===")
    print("="*60)

    try:
        stage_merge_gate(ctx)
        ctx.log("Merge gate passed")
        print("Merge gate: PASSED")
        return "merge_ready", 0

    except subprocess.TimeoutExpired:
        ctx.log("Merge gate timed out running tests")
        print("Merge gate: FAILED - test suite timed out")
        print("\nThe test suite took too long to run.")
        print(f"  Current timeout: {ctx.profile.test_timeout}s")
        print(f"  Increase TEST_TIMEOUT in project_profile.env if needed")
        print(f"  Then run: wf run {ctx.workstream.id}")
        notify_blocked(ctx.workstream.id, "Merge gate timed out")
        return "blocked", 8

    except StageError as e:
        ctx.log(f"Merge gate failed: {e.message}")
        print(f"Merge gate: FAILED - {e.message}")

        # Get failure details
        failure_type = "unknown"
        failure_output = ""
        if e.details:
            failure_type = e.details.get("type", "unknown")
            failure_output = e.details.get("output", "")

        # Only try to generate fixes for things AI can actually fix
        if failure_type == "test_failure":
            return _generate_fixes_for_test_failure(ctx, failure_output)

        # Try automatic rebase for simple cases
        if failure_type == "rebase":
            return _handle_rebase(ctx)

        if failure_type == "conflict":
            ctx.log("Conflict markers found - blocking for human")
            print("\n" + "="*60)
            print("ACTION REQUIRED: Conflict markers in code")
            print("="*60)
            print("\nThere are unresolved conflict markers in your code:")
            print(failure_output[:1000] if len(failure_output) > 1000 else failure_output)
            print(f"\nResolve the conflicts manually, then run: wf run {ctx.workstream.id}")
            notify_blocked(ctx.workstream.id, "Conflict markers in code")
            return "blocked", 8

        # Unknown failure type - block
        ctx.log(f"Unknown failure type '{failure_type}' - blocking for human")
        print(f"\nUnknown failure. Manual intervention required.")
        print(f"  Fix manually and run: wf run {ctx.workstream.id}")
        notify_blocked(ctx.workstream.id, f"Merge gate failed: {failure_type}")
        return "blocked", 8


def _handle_rebase(ctx: "RunContext") -> tuple[str, int]:
    """Handle rebase required situation with auto-resolution attempt."""
    ctx.log("Rebase required - attempting automatic rebase")
    print("\nBranch needs rebase, attempting automatic rebase...")

    worktree = ctx.workstream.worktree
    default_branch = ctx.project.default_branch

    # Fetch latest
    fetch_result = subprocess.run(
        ["git", "-C", str(worktree), "fetch", "origin", default_branch],
        capture_output=True, text=True
    )
    if fetch_result.returncode != 0:
        ctx.log(f"Fetch failed: {fetch_result.stderr}")
        print(f"Fetch failed: {fetch_result.stderr}")
        notify_blocked(ctx.workstream.id, "Git fetch failed")
        return "blocked", 8

    # Try rebase
    rebase_result = subprocess.run(
        ["git", "-C", str(worktree), "rebase", f"origin/{default_branch}"],
        capture_output=True, text=True
    )

    if rebase_result.returncode == 0:
        return _verify_build_after_rebase(ctx, worktree)

    # Rebase failed - try to auto-resolve trivial conflicts
    conflicted = _get_conflicted_files(worktree)
    if conflicted and _try_auto_resolve_conflicts(worktree, conflicted):
        # Try to continue rebase after auto-resolution
        continue_result = subprocess.run(
            ["git", "-C", str(worktree), "rebase", "--continue"],
            capture_output=True, text=True,
            env={**os.environ, "GIT_EDITOR": "true"}  # Skip commit message editor
        )
        if continue_result.returncode == 0:
            ctx.log("Auto-resolved trivial conflicts and completed rebase")
            return _verify_build_after_rebase(ctx, worktree, auto_resolved=True)

    # Still failed - abort and block for human
    ctx.log(f"Rebase failed with conflicts: {rebase_result.stderr}")
    subprocess.run(
        ["git", "-C", str(worktree), "rebase", "--abort"],
        capture_output=True, text=True
    )

    print("\n" + "="*60)
    print("ACTION REQUIRED: Rebase has conflicts")
    print("="*60)
    print(f"\nAutomatic rebase failed due to conflicts.")
    print("Please resolve manually:")
    print(f"\n  cd {ctx.workstream.worktree}")
    print(f"  git fetch origin {default_branch}")
    print(f"  git rebase origin/{default_branch}")
    print("  # resolve conflicts")
    print("  git rebase --continue")
    print(f"\nThen continue with: wf run {ctx.workstream.id}")
    notify_blocked(ctx.workstream.id, "Rebase has conflicts")
    return "blocked", 8


def _verify_build_after_rebase(
    ctx: "RunContext",
    worktree: Path,
    auto_resolved: bool = False
) -> tuple[str, int]:
    """Verify build still works after rebase."""
    ctx.log("Automatic rebase succeeded, verifying build...")
    build_result = subprocess.run(
        ["task", "-d", str(worktree), "build"],
        capture_output=True, text=True, timeout=120
    )
    if build_result.returncode != 0:
        build_error = build_result.stderr or build_result.stdout
        ctx.log(f"Build failed after rebase: {build_error[:500]}")
        print("\n" + "="*60)
        print("WARNING: Build failed after rebase")
        print("="*60)
        if auto_resolved:
            print("\nAuto-resolved conflicts but the build now fails.")
        else:
            print("\nThe rebase succeeded but the build now fails.")
            print("This may indicate semantic conflicts that need manual review.")
        print(f"\nBuild error:\n{build_error[:1000]}")
        print(f"\nFix the issue, then run: wf run {ctx.workstream.id}")
        notify_blocked(ctx.workstream.id, "Build failed after rebase")
        return "blocked", 8

    ctx.log("Build verified after rebase")
    resolution_type = "Auto-resolved trivial conflicts, rebase" if auto_resolved else "Automatic rebase"
    print(f"{resolution_type} succeeded")
    return "rebased", 0


def _generate_fixes_for_test_failure(ctx: "RunContext", failure_output: str) -> tuple[str, int]:
    """Generate fix commits for test failures."""
    print("\nTest failures detected. Generating fix commits...")

    plan_path = ctx.workstream_dir / "plan.md"
    plan_content = plan_path.read_text()
    commits = parse_plan(str(plan_path))
    existing_count = len(commits)

    log_file = ctx.run_dir / "stages" / "fix_generation.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    fix_commits = generate_fix_commits(
        ws_id=ctx.workstream.id,
        worktree=ctx.workstream.worktree,
        plan_content=plan_content,
        failure_output=failure_output,
        failure_type="test_failure",
        existing_commit_count=existing_count,
        timeout=ctx.profile.breakdown_timeout,
        log_file=log_file,
        agents_config=ctx.agents_config,
    )

    if fix_commits:
        append_commits_to_plan(plan_path, fix_commits)
        ctx.log(f"Generated {len(fix_commits)} fix commits:")
        print(f"\nGenerated {len(fix_commits)} fix commit(s):")
        for c in fix_commits:
            ctx.log(f"  - {c['id']}: {c['title']}")
            print(f"  - {c['id']}: {c['title']}")
        return "fixed", 0
    else:
        ctx.log("Fix generation failed - blocking for human intervention")
        print("\n" + "="*60)
        print("ACTION REQUIRED: Fix generation failed")
        print("="*60)
        print("\nThe AI could not generate fixes for the test failures.")
        print(f"  Test output: {ctx.run_dir}/stages/merge_gate_test.log")
        print(f"  Fix log: {log_file}")
        print(f"\nFix the tests manually, then run: wf run {ctx.workstream.id}")
        notify_blocked(ctx.workstream.id, "Test failures, fix generation failed")
        return "blocked", 8


# --- Conflict resolution helpers ---

def _get_conflicted_files(worktree: Path) -> list[str]:
    """Get list of files with conflicts during rebase."""
    result = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--name-only", "--diff-filter=U"],
        capture_output=True, text=True
    )
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def _get_conflict_type(worktree: Path, filepath: str) -> str:
    """Get the conflict type for a file (UU, AA, etc.) from git status."""
    result = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain", filepath],
        capture_output=True, text=True
    )
    if result.stdout and len(result.stdout) >= 2:
        return result.stdout[:2]
    return ""


def _git_show_index(worktree: Path, stage: int, filepath: str) -> Optional[str]:
    """Get file content from git index at given stage.

    During merge/rebase conflicts:
      stage 1 = base (common ancestor)
      stage 2 = ours (HEAD)
      stage 3 = theirs (incoming)

    Returns None if file doesn't exist, is binary, or can't decode as UTF-8.
    """
    result = subprocess.run(
        ["git", "-C", str(worktree), "show", f":{stage}:{filepath}"],
        capture_output=True
    )
    if result.returncode != 0:
        return None

    # Check for binary content
    if b'\x00' in result.stdout:
        logger.info(f"File {filepath} stage {stage} is binary, skipping auto-resolution")
        return None

    try:
        return result.stdout.decode('utf-8')
    except UnicodeDecodeError:
        logger.info(f"File {filepath} stage {stage} is not valid UTF-8, skipping auto-resolution")
        return None


def _try_auto_resolve_conflicts(worktree: Path, files: list[str]) -> bool:
    """Try to auto-resolve conflicts using git merge-file --union.

    Only attempts resolution for 'both modified' (UU) conflicts.
    Returns True if all conflicts were successfully resolved.
    """
    for filepath in files:
        conflict_type = _get_conflict_type(worktree, filepath)

        if conflict_type != "UU":
            logger.info(f"Conflict {filepath}: type {conflict_type!r} not auto-resolvable")
            return False

        base = _git_show_index(worktree, 1, filepath)
        ours = _git_show_index(worktree, 2, filepath)
        theirs = _git_show_index(worktree, 3, filepath)

        if base is None or ours is None or theirs is None:
            logger.warning(f"Conflict {filepath}: missing index stage")
            return False

        resolved_content = _merge_union(base, ours, theirs)
        if resolved_content is None:
            logger.warning(f"Conflict {filepath}: git merge-file failed")
            return False

        file_path = worktree / filepath
        try:
            file_path.write_text(resolved_content)
        except IOError as e:
            logger.warning(f"Conflict {filepath}: failed to write: {e}")
            return False

        stage_result = subprocess.run(
            ["git", "-C", str(worktree), "add", filepath],
            capture_output=True, text=True
        )
        if stage_result.returncode != 0:
            logger.warning(f"Conflict {filepath}: failed to stage: {stage_result.stderr}")
            return False

        logger.info(f"Auto-resolved conflict in {filepath} using union merge")

    return True


def _merge_union(base: str, ours: str, theirs: str) -> Optional[str]:
    """Run git merge-file --union and return resolved content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = Path(tmpdir) / "base"
        ours_path = Path(tmpdir) / "ours"
        theirs_path = Path(tmpdir) / "theirs"

        base_path.write_text(base)
        ours_path.write_text(ours)
        theirs_path.write_text(theirs)

        result = subprocess.run(
            ["git", "merge-file", "--union",
             str(ours_path), str(base_path), str(theirs_path)],
            capture_output=True, text=True
        )

        if result.returncode < 0:
            logger.error(f"git merge-file error: {result.stderr}")
            return None

        return ours_path.read_text()
