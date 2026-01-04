"""
Stage implementations for AOS.

Each stage function takes a RunContext and raises StageError/StageBlocked on failure.
"""

import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from orchestrator.runner.context import RunContext
from orchestrator.runner.stages import StageError, StageBlocked
from orchestrator.lib.planparse import parse_plan, get_next_microcommit, mark_done
from orchestrator.lib.prompts import render_prompt
from orchestrator.lib.history import format_conversation_history
from orchestrator.lib.review import load_review, format_review, print_review
from orchestrator.agents.codex import CodexAgent
from orchestrator.agents.claude import ClaudeAgent
from orchestrator.runner.impl.breakdown import generate_breakdown, append_commits_to_plan
from orchestrator.lib.stats import AgentStats, record_agent_stats


def _verbose_header(title: str):
    """Print a section header for verbose output."""
    print(f"\n{'='*60}")
    print(title)
    print('='*60)


def _verbose_footer():
    """Print a section footer for verbose output."""
    print('='*60 + "\n")


def _load_last_review_output(ctx: RunContext) -> dict | None:
    """Load the most recent review output for context."""
    runs_dir = ctx.run_dir.parent  # run_dir is {ops_dir}/runs/{run_id}
    if not runs_dir.exists():
        return None

    if not ctx.workstream:
        return None

    ws_id = ctx.workstream.id

    # Find most recent run for this workstream
    ws_runs = sorted(
        [d for d in runs_dir.iterdir() if ws_id in d.name],
        key=lambda d: d.stat().st_mtime,
        reverse=True
    )
    for run_dir in ws_runs:
        review = load_review(run_dir)
        if review:
            return review
    return None


def _print_review_result(review):
    """Print formatted review result for verbose output."""
    print(f"Decision: {review.decision}")
    if review.blockers:
        print(f"\nBlockers ({len(review.blockers)}):")
        for b in review.blockers:
            if isinstance(b, dict):
                print(f"  - {b.get('file', '?')}:{b.get('line', '?')} [{b.get('severity', '?')}] {b.get('issue', '?')}")
            else:
                print(f"  - {b}")
    if review.required_changes:
        print(f"\nRequired changes ({len(review.required_changes)}):")
        for c in review.required_changes:
            print(f"  - {c}")
    if review.suggestions:
        print(f"\nSuggestions ({len(review.suggestions)}):")
        for s in review.suggestions:
            print(f"  - {s}")
    if review.notes:
        print(f"\nNotes: {review.notes}")


def stage_load(ctx: RunContext):
    """Validate configuration and workstream state."""
    # Check worktree exists
    if not ctx.workstream.worktree.exists():
        raise StageError("load", f"Worktree not found: {ctx.workstream.worktree}", 2)

    # Check worktree is on correct branch
    result = subprocess.run(
        ["git", "-C", str(ctx.workstream.worktree), "branch", "--show-current"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise StageError("load", "Could not determine current branch", 2)

    current_branch = result.stdout.strip()
    if current_branch != ctx.workstream.branch:
        raise StageError("load", f"Worktree on wrong branch: {current_branch} (expected {ctx.workstream.branch})", 2)

    # Check BASE_SHA exists
    result = subprocess.run(
        ["git", "-C", str(ctx.workstream.worktree), "cat-file", "-t", ctx.workstream.base_sha],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise StageError("load", f"BASE_SHA not found: {ctx.workstream.base_sha}", 2)

    # Check plan.md exists
    plan_path = ctx.workstream_dir / "plan.md"
    if not plan_path.exists():
        raise StageError("load", "plan.md not found", 2)

    ctx.log("Load stage passed - configuration validated")


def stage_breakdown(ctx: RunContext):
    """Generate micro-commits from story/ACs if not present.

    This stage runs before SELECT. If plan.md already has micro-commits,
    it passes through. Otherwise, calls Claude to generate them.

    In supervised mode, pauses after generating breakdown for human review.
    """
    plan_path = ctx.workstream_dir / "plan.md"
    commits = parse_plan(str(plan_path))

    if commits:
        ctx.log(f"Plan already has {len(commits)} micro-commits, skipping breakdown")
        return

    ctx.log("No micro-commits found - generating breakdown")

    # Read plan content for Claude
    plan_content = plan_path.read_text()

    # Generate breakdown
    log_file = ctx.run_dir / "stages" / "breakdown.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    breakdown = generate_breakdown(
        ws_id=ctx.workstream.id,
        worktree=ctx.workstream.worktree,
        plan_content=plan_content,
        timeout=ctx.profile.breakdown_timeout,
        log_file=log_file,
    )

    if not breakdown:
        raise StageError("breakdown", "Failed to generate micro-commits", 2)

    # Append to plan.md
    append_commits_to_plan(plan_path, breakdown)

    ctx.log(f"Generated {len(breakdown)} micro-commits:")
    for c in breakdown:
        ctx.log(f"  - {c['id']}: {c['title']}")

    # In supervised mode, pause for human review of plan.md
    if ctx.profile.supervised_mode:
        ctx.log("Supervised mode: pausing for human review of plan.md")
        raise StageBlocked(
            "breakdown",
            f"Review plan.md, then run: wf run {ctx.workstream.id}"
        )


def stage_select(ctx: RunContext):
    """Select next micro-commit to work on."""
    plan_path = ctx.workstream_dir / "plan.md"
    commits = parse_plan(str(plan_path))

    if not commits:
        raise StageError("select", "No micro-commits defined in plan.md", 2)

    next_commit = get_next_microcommit(commits)
    if next_commit is None:
        # All done!
        ctx.log("All micro-commits complete")
        raise StageBlocked("select", "all_complete")

    # Transition from awaiting_human_review when we have work to do
    if ctx.workstream.status == "awaiting_human_review":
        ctx.log("Pending commits found - transitioning to implementing")
        _update_workstream_status(ctx.workstream_dir, "implementing")

    ctx.microcommit = next_commit
    ctx.log(f"Selected micro-commit: {next_commit.id} - {next_commit.title}")


def stage_clarification_check(ctx: RunContext):
    """Check for blocking clarifications."""
    from orchestrator.clarifications import get_blocking_clarifications

    blocking = get_blocking_clarifications(ctx.workstream_dir)

    if blocking:
        ids = [c.id for c in blocking]
        raise StageBlocked(
            "clarification_check",
            f"Blocked by: {', '.join(ids)}. "
            f"Run 'wf clarify {ctx.workstream.id}' to view and answer."
        )

    ctx.log("No blocking clarifications")


def stage_implement(ctx: RunContext, human_feedback: str = None):
    """Run Codex to implement the micro-commit.

    Args:
        ctx: Run context (includes review_history for iterative feedback)
        human_feedback: Human-provided guidance from rejection
    """
    if not ctx.microcommit:
        raise StageError("implement", "No micro-commit selected", 9)

    # Build conversation history section if this is a retry
    conversation_history_section = ""
    if ctx.review_history:
        history_entries = format_conversation_history(ctx.review_history)
        conversation_history_section = render_prompt(
            "implement_history",
            history_entries=history_entries
        )

    # Build human guidance section
    human_guidance_section = ""
    if human_feedback:
        human_guidance_section = f"## HUMAN GUIDANCE\n\n{human_feedback}\n"

    # Build review context section from last review output
    review_context_section = ""
    last_review = _load_last_review_output(ctx)
    if last_review:
        review_content = format_review(last_review)
        review_context_section = render_prompt(
            "implement_review_context",
            review_content=review_content
        )

    # Build the full prompt from template
    prompt = render_prompt(
        "implement",
        system_description=ctx.project.description or f"Project: {ctx.project.name}",
        tech_preferred=ctx.project.tech_preferred or "Not specified",
        tech_acceptable=ctx.project.tech_acceptable or "Not specified",
        tech_avoid=ctx.project.tech_avoid or "Not specified",
        commit_id=ctx.microcommit.id,
        commit_title=ctx.microcommit.title,
        commit_description=ctx.microcommit.block_content,
        review_context_section=review_context_section,
        conversation_history_section=conversation_history_section,
        human_guidance_section=human_guidance_section
    )

    ctx.log(f"Running Codex for {ctx.microcommit.id}")

    if ctx.verbose:
        _verbose_header("IMPLEMENT PROMPT")
        print(prompt)
        _verbose_footer()

    agent = CodexAgent(timeout=ctx.profile.implement_timeout)
    log_file = ctx.run_dir / "stages" / "implement.log"

    result = agent.implement(prompt, ctx.workstream.worktree, log_file)

    # Record stats
    record_agent_stats(ctx.workstream_dir, AgentStats(
        timestamp=datetime.now().isoformat(),
        run_id=ctx.run_id,
        agent="codex",
        elapsed_seconds=result.elapsed_seconds,
        microcommit_id=ctx.microcommit.id if ctx.microcommit else None,
    ))

    if ctx.verbose and result.stdout:
        _verbose_header("IMPLEMENT OUTPUT")
        print(result.stdout)
        _verbose_footer()

    # Check for clarification request
    if result.clarification_needed:
        from orchestrator.clarifications import create_clarification

        clq_data = {
            "question": result.clarification_needed.get("question", "Codex needs clarification"),
            "context": result.clarification_needed.get("context", ""),
            "options": result.clarification_needed.get("options", []),
            "blocks": [ctx.microcommit.id] if ctx.microcommit else [],
            "urgency": "blocking",
        }
        clq = create_clarification(ctx.workstream_dir, clq_data)
        ctx.log(f"Created clarification: {clq.id}")
        raise StageBlocked("implement", f"Codex needs clarification: {clq.id}")

    if not result.success:
        raise StageError("implement", f"Codex failed: {result.stderr}", 4)

    # Verify Codex wrote something
    git_status = subprocess.run(
        ["git", "-C", str(ctx.workstream.worktree), "status", "--porcelain"],
        capture_output=True, text=True
    )
    if not git_status.stdout.strip():
        # If Codex had something to say but made no changes, surface it
        if result.stdout and result.stdout.strip():
            msg = result.stdout.strip()
            if len(msg) > 500:
                msg = msg[:500] + "..."
            raise StageError("implement", f"Codex made no changes. Output:\n{msg}", 4)
        raise StageError("implement", "Codex made no changes", 4)

    ctx.log(f"Implementation complete, {len(git_status.stdout.strip().splitlines())} files changed")


def stage_test(ctx: RunContext):
    """Run tests."""
    makefile = ctx.workstream.worktree / ctx.profile.makefile_path
    if not makefile.exists():
        ctx.log("No Makefile found, skipping tests")
        return

    cmd = ["make", "-C", str(ctx.workstream.worktree), ctx.profile.make_target_test]

    ctx.log(f"Running: {' '.join(cmd)}")
    start = time.time()

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=ctx.profile.test_timeout
    )

    duration = time.time() - start
    ctx.log_command(cmd, result.returncode, duration)

    # Save test output
    (ctx.run_dir / "stages" / "test.log").write_text(
        f"=== STDOUT ===\n{result.stdout}\n\n=== STDERR ===\n{result.stderr}\n"
    )

    if result.returncode != 0:
        raise StageError("test", f"Tests failed (exit {result.returncode})", 5)

    ctx.log("Tests passed")


def _get_story_context(ctx: RunContext) -> str:
    """Extract story title and description from plan.md."""
    plan_path = ctx.workstream_dir / "plan.md"
    if not plan_path.exists():
        return ctx.workstream.title

    content = plan_path.read_text()
    lines = content.split("\n")

    # Extract title (first # heading) and description (text before first ## or ---)
    title = ctx.workstream.title
    description_lines = []
    in_description = False

    for line in lines:
        if line.startswith("# ") and not in_description:
            title = line[2:].strip()
            in_description = True
        elif in_description:
            if line.startswith("## ") or line.startswith("---") or line.startswith("### COMMIT"):
                break
            description_lines.append(line)

    description = "\n".join(description_lines).strip()
    if description:
        return f"{title}\n\n{description}"
    return title


def stage_review(ctx: RunContext):
    """Run Claude to review the changes with full codebase access."""
    if not ctx.microcommit:
        raise StageError("review", "No micro-commit selected", 9)

    # Check there are changes to review
    result = subprocess.run(
        ["git", "-C", str(ctx.workstream.worktree), "diff", "--quiet", "HEAD"],
        capture_output=True
    )
    if result.returncode == 0:
        # Also check for untracked files
        status = subprocess.run(
            ["git", "-C", str(ctx.workstream.worktree), "status", "--porcelain"],
            capture_output=True, text=True
        )
        if not status.stdout.strip():
            ctx.log("No changes to review")
            return

    # Get context for the reviewer
    system_description = ctx.project.description or f"Project: {ctx.project.name}"
    story_context = _get_story_context(ctx)

    # Build review prompt
    agent = ClaudeAgent(timeout=ctx.profile.review_timeout)
    prompt = agent.build_contextual_review_prompt(
        system_description=system_description,
        tech_preferred=ctx.project.tech_preferred,
        tech_acceptable=ctx.project.tech_acceptable,
        tech_avoid=ctx.project.tech_avoid,
        story_context=story_context,
        commit_title=ctx.microcommit.title,
        commit_description=ctx.microcommit.block_content,
        review_history=ctx.review_history
    )

    ctx.log("Running Claude review (contextual)")

    if ctx.verbose:
        _verbose_header("REVIEW PROMPT")
        print(prompt)
        _verbose_footer()

    log_file = ctx.run_dir / "stages" / "review.log"

    review = agent.contextual_review(prompt, ctx.workstream.worktree, log_file)

    # Record stats
    record_agent_stats(ctx.workstream_dir, AgentStats(
        timestamp=datetime.now().isoformat(),
        run_id=ctx.run_id,
        agent="claude",
        elapsed_seconds=review.elapsed_seconds,
        input_tokens=review.input_tokens,
        output_tokens=review.output_tokens,
        microcommit_id=ctx.microcommit.id if ctx.microcommit else None,
    ))

    if ctx.verbose:
        _verbose_header("REVIEW RESULT")
        _print_review_result(review)
        _verbose_footer()

    # Save review result
    (ctx.run_dir / "claude_review.json").write_text(json.dumps({
        "version": 1,
        "decision": review.decision,
        "blockers": review.blockers,
        "required_changes": review.required_changes,
        "suggestions": review.suggestions,
        "notes": review.notes,
    }, indent=2))

    if not review.success:
        raise StageError("review", f"Review failed: {review.notes}", 6)

    if review.decision == "request_changes":
        issues = []
        if review.blockers:
            issues.append(f"{len(review.blockers)} blocker(s)")
        if review.required_changes:
            issues.append(f"{len(review.required_changes)} required change(s)")
        raise StageError("review", f"Review rejected: {', '.join(issues)}", 6)

    ctx.log("Review approved")


def stage_qa_gate(ctx: RunContext):
    """Validate that all quality gates pass."""
    # Check test output exists (if tests were run)
    test_log = ctx.run_dir / "stages" / "test.log"
    if test_log.exists():
        ctx.log("Test artifacts present")

    # Check review result
    review_path = ctx.run_dir / "claude_review.json"
    if review_path.exists():
        review = json.loads(review_path.read_text())
        if review.get("decision") != "approve":
            raise StageError("qa_gate", "Review not approved", 7)
        ctx.log("Review approval confirmed")

    ctx.log("QA gate passed")


def stage_update_state(ctx: RunContext):
    """Update workstream state after successful run - THIS IS WHERE WE COMMIT."""
    if not ctx.microcommit:
        return

    # COMMIT - all gates have passed, now we commit
    worktree = str(ctx.workstream.worktree)

    # Stage all changes
    result = subprocess.run(
        ["git", "-C", worktree, "add", "-A"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise StageError("update_state", f"git add failed: {result.stderr}", 9)

    # Create commit
    commit_msg = f"{ctx.microcommit.id}: {ctx.microcommit.title}"
    result = subprocess.run(
        ["git", "-C", worktree, "commit", "-m", commit_msg],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise StageError("update_state", f"git commit failed: {result.stderr}", 9)

    # Get the commit SHA
    result = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "HEAD"],
        capture_output=True, text=True
    )
    commit_sha = result.stdout.strip() if result.returncode == 0 else "unknown"
    ctx.log(f"Committed: {commit_sha[:8]} - {commit_msg}")

    # Mark micro-commit as done
    plan_path = ctx.workstream_dir / "plan.md"
    mark_done(str(plan_path), ctx.microcommit.id)
    ctx.log(f"Marked {ctx.microcommit.id} as done")

    # Auto-push if PR is open (so CI re-runs on the fix)
    if ctx.workstream.pr_number:
        print(f"Pushing to update PR #{ctx.workstream.pr_number}...")
        ctx.log(f"Pushing to update PR #{ctx.workstream.pr_number}...")
        push_result = subprocess.run(
            ["git", "-C", worktree, "push"],
            capture_output=True, text=True, timeout=60
        )
        if push_result.returncode == 0:
            print("  Pushed successfully")
            ctx.log("Pushed successfully")
        else:
            # Non-fatal: commit is local, user can push manually
            print(f"  Push warning: {push_result.stderr.strip()}")
            ctx.log(f"Push warning: {push_result.stderr.strip()}")

    # Update meta.env with last run info
    meta_path = ctx.workstream_dir / "meta.env"
    meta_content = meta_path.read_text()

    # Update or add LAST_RUN_ID
    if "LAST_RUN_ID=" in meta_content:
        lines = meta_content.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("LAST_RUN_ID="):
                lines[i] = f'LAST_RUN_ID="{ctx.run_id}"'
                break
        meta_content = "\n".join(lines) + "\n"
    else:
        meta_content += f'LAST_RUN_ID="{ctx.run_id}"\n'

    meta_path.write_text(meta_content)

    # Refresh touched files
    from orchestrator.commands.refresh import refresh_workstream
    refresh_workstream(ctx.workstream_dir)

    ctx.log("State updated")


def stage_merge_gate(ctx: RunContext) -> dict:
    """
    Validate branch is ready to merge.

    Runs after all micro-commits are complete. Checks:
    1. Full test suite passes
    2. Branch is rebased on main (no merge conflicts)
    3. No conflict markers in files

    Returns:
        Empty dict on success.

    Raises:
        StageError with details dict containing 'type' and 'output' on failure.
    """
    ctx.log("Running merge gate...")
    worktree = str(ctx.workstream.worktree)
    default_branch = ctx.project.default_branch

    # 1. Run full test suite
    makefile = ctx.workstream.worktree / ctx.profile.makefile_path
    if makefile.exists():
        test_target = ctx.profile.merge_gate_test_target
        cmd = ["make", "-C", worktree, test_target]

        ctx.log(f"Running full test suite: {' '.join(cmd)}")
        start = time.time()

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=ctx.profile.test_timeout
        )

        duration = time.time() - start
        ctx.log_command(cmd, result.returncode, duration)

        # Save test output
        log_path = ctx.run_dir / "stages" / "merge_gate_test.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"=== STDOUT ===\n{result.stdout}\n\n=== STDERR ===\n{result.stderr}\n"
        )

        if result.returncode != 0:
            output = result.stdout + "\n" + result.stderr
            raise StageError(
                "merge_gate",
                f"Full test suite failed (exit {result.returncode})",
                10,
                details={"type": "test_failure", "output": output}
            )

        ctx.log("Full test suite passed")

    # 2. Check branch is up to date with main
    # First, fetch to ensure we have latest main
    subprocess.run(
        ["git", "-C", worktree, "fetch", "origin", default_branch],
        capture_output=True, text=True
    )

    # Check if main is ancestor of HEAD (meaning we're rebased on main)
    result = subprocess.run(
        ["git", "-C", worktree, "merge-base", "--is-ancestor",
         f"origin/{default_branch}", "HEAD"],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        # Get divergence info for context
        diverge_result = subprocess.run(
            ["git", "-C", worktree, "rev-list", "--left-right", "--count",
             f"origin/{default_branch}...HEAD"],
            capture_output=True, text=True
        )
        output = f"Branch needs rebase on {default_branch}.\n"
        if diverge_result.returncode == 0:
            parts = diverge_result.stdout.strip().split()
            if len(parts) == 2:
                output += f"Main is {parts[0]} commits ahead, branch is {parts[1]} commits ahead.\n"
        output += f"Run: git rebase origin/{default_branch}"

        raise StageError(
            "merge_gate",
            f"Branch needs rebase on {default_branch}",
            11,
            details={"type": "rebase", "output": output}
        )

    ctx.log(f"Branch is up to date with {default_branch}")

    # 3. Check for conflict markers in tracked files
    result = subprocess.run(
        ["git", "-C", worktree, "diff", "--check", f"origin/{default_branch}...HEAD"],
        capture_output=True, text=True
    )

    if result.returncode != 0 and result.stdout.strip():
        raise StageError(
            "merge_gate",
            "Conflict markers detected in files",
            12,
            details={"type": "conflict", "output": result.stdout}
        )

    ctx.log("No conflict markers found")
    ctx.log("Merge gate passed")

    return {}


def stage_human_review(ctx: RunContext):
    """Block until human approves or rejects.

    In gatekeeper mode (supervised_mode=False), this stage is skipped since
    the AI review already approved. Human review only happens at the merge gate.

    In supervised mode (supervised_mode=True), checks for approval file:
    - human_approval.json with {"action": "approve"} -> proceed
    - human_approval.json with {"action": "reject", "reset": bool, "feedback": "..."} -> StageError
    - No file -> StageBlocked (waiting for human)
    """
    # In gatekeeper mode, skip human review IF AI approved
    # If AI rejected after max attempts, we still need human review
    review_status = ctx.stages.get("review", {}).get("status")
    if not ctx.profile.supervised_mode and review_status == "passed":
        ctx.log("Gatekeeper mode: skipping human review (AI approved)")
        return

    approval_file = ctx.workstream_dir / "human_approval.json"

    if not approval_file.exists():
        # Update status to awaiting human review
        _update_workstream_status(ctx.workstream_dir, "awaiting_human_review")
        ctx.log("Awaiting human review")
        raise StageBlocked("human_review", "Waiting for human approval (use: wf approve/reject/reset)")

    try:
        approval = json.loads(approval_file.read_text())
    except (json.JSONDecodeError, IOError) as e:
        raise StageError("human_review", f"Invalid approval file: {e}", 9)

    action = approval.get("action")

    if action == "approve":
        ctx.log("Human approved")
        # Clean up approval file
        approval_file.unlink()
        _update_workstream_status(ctx.workstream_dir, "active")
        return

    elif action == "reject":
        feedback = approval.get("feedback", "")
        reset = approval.get("reset", False)
        ctx.log(f"Human rejected (reset={reset}) with feedback: {feedback[:100]}...")
        # Store feedback and reset flag for next implement run
        _store_human_feedback(ctx.workstream_dir, feedback, reset)
        # Clean up approval file
        approval_file.unlink()
        _update_workstream_status(ctx.workstream_dir, "human_rejected")
        raise StageError("human_review", f"Human rejected: {feedback[:100]}", 6)

    else:
        raise StageError("human_review", f"Unknown action: {action}", 9)


def _update_workstream_status(workstream_dir: Path, status: str):
    """Update STATUS in meta.env."""
    meta_path = workstream_dir / "meta.env"
    content = meta_path.read_text()
    lines = content.splitlines()

    for i, line in enumerate(lines):
        if line.startswith("STATUS="):
            lines[i] = f'STATUS="{status}"'
            break

    meta_path.write_text("\n".join(lines) + "\n")


def _store_human_feedback(workstream_dir: Path, feedback: str, reset: bool = False):
    """Store human feedback and reset flag for next implement run."""
    import json
    feedback_file = workstream_dir / "human_feedback.json"
    feedback_file.write_text(json.dumps({
        "feedback": feedback,
        "reset": reset,
        "timestamp": datetime.now().isoformat()
    }, indent=2))


def get_human_feedback(workstream_dir: Path) -> tuple:
    """Get stored human feedback and reset flag if any, and clear it.

    Checks both:
    - human_feedback.json (from previous run's human_review stage)
    - human_approval.json with action="reject" (fresh rejection, consume immediately)

    Returns: (feedback: str|None, reset: bool)
    """
    import json

    # First check for fresh rejection in human_approval.json
    approval_file = workstream_dir / "human_approval.json"
    if approval_file.exists():
        try:
            data = json.loads(approval_file.read_text())
            if data.get("action") == "reject":
                feedback = data.get("feedback", "")
                reset = data.get("reset", False)
                # Consume the rejection
                approval_file.unlink()
                _update_workstream_status(workstream_dir, "active")
                return feedback, reset
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to read human approval from {approval_file}: {e}")

    # Fall back to stored feedback from previous run
    feedback_file = workstream_dir / "human_feedback.json"
    if not feedback_file.exists():
        return None, False

    try:
        data = json.loads(feedback_file.read_text())
        feedback = data.get("feedback")
        reset = data.get("reset", False)
        # Clear after reading
        feedback_file.unlink()
        return feedback, reset
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to read human feedback from {feedback_file}: {e}")
        return None, False
