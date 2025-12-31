"""
wf review - Final AI review of entire branch before merge.
"""

import subprocess
from datetime import datetime
from pathlib import Path

from orchestrator.lib.config import ProjectConfig, load_workstream
from orchestrator.lib.prompts import render_prompt
from orchestrator.lib.stats import AgentStats, record_agent_stats
from orchestrator.agents.claude import ClaudeAgent


def run_final_review(workstream_dir: Path, project_config: ProjectConfig, verbose: bool = True) -> str:
    """
    Run final branch review.

    Returns: "approve" or "concerns"
    """
    ws = load_workstream(workstream_dir)

    # Get git directory
    if ws.worktree.exists():
        git_dir = str(ws.worktree)
    else:
        git_dir = str(project_config.repo_path)

    # Get full branch diff against main
    diff_result = subprocess.run(
        ["git", "-C", git_dir, "diff", f"{project_config.default_branch}...{ws.branch}"],
        capture_output=True, text=True
    )

    if diff_result.returncode != 0:
        if verbose:
            print(f"ERROR: Could not get diff: {diff_result.stderr}")
        return "concerns"

    diff = diff_result.stdout
    if not diff.strip():
        if verbose:
            print("No changes to review")
        return "approve"

    # Get diff stats for summary
    stats_result = subprocess.run(
        ["git", "-C", git_dir, "diff", "--stat", f"{project_config.default_branch}...{ws.branch}"],
        capture_output=True, text=True
    )
    diff_stats = stats_result.stdout if stats_result.returncode == 0 else ""

    # Get commit log for context
    log_result = subprocess.run(
        ["git", "-C", git_dir, "log", "--oneline", f"{project_config.default_branch}..{ws.branch}"],
        capture_output=True, text=True
    )
    commit_log = log_result.stdout if log_result.returncode == 0 else ""

    # Build review prompt
    prompt = render_prompt(
        "final_review",
        feature_title=ws.title,
        commit_log=commit_log,
        diff_stats=diff_stats,
        diff=diff
    )

    if verbose:
        print(f"Reviewing {ws.id}: {ws.title}")
        print("=" * 60)

    agent = ClaudeAgent(timeout=180)
    result = agent.review_freeform(prompt, project_config.repo_path)

    # Record stats
    now = datetime.now()
    record_agent_stats(workstream_dir, AgentStats(
        timestamp=now.isoformat(),
        run_id=f"final_review_{now.strftime('%Y%m%d-%H%M%S')}",
        agent="claude",
        elapsed_seconds=result.elapsed_seconds,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    ))

    if verbose:
        print(result.text)
        print("=" * 60)

    # Save to workstream directory
    review_file = workstream_dir / "final_review.md"
    review_file.write_text(f"# Final Branch Review: {ws.id}\n\n{result.text}\n")

    if verbose:
        print(f"\nSaved to: {review_file}")

    # Determine verdict from review text
    review_lower = result.text.lower()
    if "verdict" in review_lower:
        # Look for verdict line
        if "approve" in review_lower.split("verdict")[-1][:50]:
            return "approve"

    # Default to concerns if we can't determine
    if "concerns: none" in review_lower or "no concerns" in review_lower:
        return "approve"

    return "concerns"


def cmd_review(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Run final AI review of workstream branch."""
    workstream_dir = ops_dir / "workstreams" / args.id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{args.id}' not found")
        return 1

    verdict = run_final_review(workstream_dir, project_config, verbose=True)

    print(f"\nVerdict: {verdict.upper()}")

    return 0 if verdict == "approve" else 0  # Always success, verdict is informational
