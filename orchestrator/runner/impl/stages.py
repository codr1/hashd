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
from orchestrator.runner.stages import StageError, StageBlocked, StageHumanGateProcessed
from orchestrator.lib.planparse import parse_plan, get_next_microcommit, mark_done
from orchestrator.lib.prompts import render_prompt
from orchestrator.lib.review import format_review_for_retry
from orchestrator.lib.config import load_escalation_config, get_confidence_threshold, update_workstream_meta, AUTONOMY_MODES, EscalationConfig
from orchestrator.lib.constants import ACTION_APPROVE, ACTION_REJECT
from orchestrator.agents.codex import CodexAgent
from orchestrator.agents.claude import ClaudeAgent
from orchestrator.runner.impl.breakdown import generate_breakdown, append_commits_to_plan
from orchestrator.runner.impl.session_utils import is_codex_session_resume_failure, is_claude_session_resume_failure
from orchestrator.runner.impl.output import verbose_header, verbose_footer, truncate_output
from orchestrator.runner.impl.state_files import (
    save_codex_session_id,
    clear_codex_session_id,
    store_human_feedback,
    get_human_feedback,
)
from orchestrator.workflow.state_machine import transition, WorkstreamState
from orchestrator.runner.impl.prompt_context import (
    get_uncommitted_changes_context,
    build_full_implement_prompt,
    build_full_review_prompt,
)
from orchestrator.lib.stats import AgentStats, record_agent_stats
from orchestrator.git import (
    has_uncommitted_changes,
    get_current_branch,
    commit_exists,
    get_status_porcelain,
    stage_files,
    stage_all,
    commit as git_commit,
    get_commit_sha,
    push,
    fetch,
    is_ancestor,
    get_diff_check,
    has_changes_vs_head,
    get_diff_names,
    get_divergence_count,
    has_remote,
)
from orchestrator.stages import Actor


def get_effective_autonomy(ctx: RunContext, escalation_config: EscalationConfig | None = None) -> str:
    """Get the effective autonomy mode, respecting CLI override.

    Args:
        ctx: Run context with optional autonomy_override.
        escalation_config: Pre-loaded EscalationConfig. If None, loads from project_dir.

    Returns:
        "supervised", "gatekeeper", or "autonomous"

    Raises:
        ValueError: If autonomy_override is not a valid mode.
    """
    if ctx.autonomy_override:
        if ctx.autonomy_override not in AUTONOMY_MODES:
            raise ValueError(
                f"Invalid autonomy mode '{ctx.autonomy_override}'. "
                f"Valid modes: {', '.join(sorted(AUTONOMY_MODES))}"
            )
        return ctx.autonomy_override
    if escalation_config is None:
        escalation_config = load_escalation_config(ctx.project_dir)
    return escalation_config.autonomy


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
    current_branch = get_current_branch(ctx.workstream.worktree)
    if current_branch is None:
        raise StageError("load", "Could not determine current branch", 2)

    if current_branch != ctx.workstream.branch:
        raise StageError("load", f"Worktree on wrong branch: {current_branch} (expected {ctx.workstream.branch})", 2)

    # Check BASE_SHA exists
    if not commit_exists(ctx.workstream.worktree, ctx.workstream.base_sha):
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
        ctx.transcript.record("breakdown", Actor.SYSTEM, "out", f"Skipped: plan already has {len(commits)} micro-commits")
        return

    ctx.log("No micro-commits found - generating breakdown")

    # Read plan content for Claude
    plan_content = plan_path.read_text()

    # Record breakdown request to transcript
    ctx.transcript.record_agent_call("breakdown", Actor.CLAUDE, f"Generate micro-commits from plan:\n\n{plan_content[:500]}...")

    # Generate breakdown
    log_file = ctx.run_dir / "stages" / "breakdown.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    breakdown = generate_breakdown(
        ws_id=ctx.workstream.id,
        worktree=ctx.workstream.worktree,
        plan_content=plan_content,
        timeout=ctx.profile.breakdown_timeout,
        log_file=log_file,
        agents_config=ctx.agents_config,
    )

    if not breakdown:
        ctx.transcript.record_agent_response("breakdown", Actor.CLAUDE, "Failed to generate micro-commits", success=False)
        raise StageError("breakdown", "Failed to generate micro-commits", 2)

    # Record breakdown result to transcript
    commits_summary = "\n".join(f"- {c['id']}: {c['title']}" for c in breakdown)
    ctx.transcript.record_agent_response("breakdown", Actor.CLAUDE, commits_summary, success=True)

    # Append to plan.md
    append_commits_to_plan(plan_path, breakdown)

    ctx.log(f"Generated {len(breakdown)} micro-commits:")
    for c in breakdown:
        ctx.log(f"  - {c['id']}: {c['title']}")

    # In supervised mode, pause for human review of plan.md
    if get_effective_autonomy(ctx) == "supervised":
        ctx.log("Supervised mode: pausing for human review of plan.md")
        raise StageBlocked(
            "breakdown",
            f"Plan ready for review\n  View plan: wf show {ctx.workstream.id}\n  Continue:  wf run {ctx.workstream.id}"
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
        transition(ctx.workstream_dir, WorkstreamState.IMPLEMENTING, reason="pending commits found")

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

    Session Reuse:
        On retries (ctx.codex_session_active=True), we attempt `codex exec resume --last`
        to continue the previous session. If resume fails (no session exists), we fall
        back to a fresh session with the full prompt. This handles cases where the
        previous session was lost (timeout, crash, process killed).
    """
    if not ctx.microcommit:
        raise StageError("implement", "No micro-commit selected", 9)

    # Build human guidance section (used in both full and retry prompts)
    human_guidance_section = ""
    if human_feedback:
        human_guidance_section = f"\n## HUMAN GUIDANCE\n\n{human_feedback}\n"

    # Check for uncommitted changes from previous attempts and build context
    uncommitted_context = get_uncommitted_changes_context(ctx.workstream.worktree)
    if uncommitted_context:
        ctx.log("Detected uncommitted changes from previous attempt - injecting context")

    agent = CodexAgent(timeout=ctx.profile.implement_timeout, agents_config=ctx.agents_config)
    log_file = ctx.run_dir / "stages" / "implement.log"

    # Get saved session ID from workstream meta (if any)
    saved_session_id = ctx.workstream.codex_session_id

    # Determine if we should try session resume
    # We need BOTH a saved session ID and review history to resume
    should_try_resume = saved_session_id and ctx.review_history

    # Track prompt for transcript
    used_prompt = None

    if should_try_resume:
        # Try session resume with short prompt
        last_entry = ctx.review_history[-1]

        if last_entry.get("test_failure"):
            review_feedback = f"Tests failed:\n\n{truncate_output(last_entry['test_failure'])}\n\nFix the code to make tests pass."
        elif last_entry.get("build_failure"):
            review_feedback = f"Build failed:\n\n{truncate_output(last_entry['build_failure'])}\n\nFix the build errors."
        else:
            last_review = last_entry.get("review_feedback", {})
            review_feedback = format_review_for_retry(last_review)

        retry_prompt = render_prompt(
            "implement_retry",
            review_feedback=review_feedback,
            human_guidance_section=human_guidance_section
        )

        # Prepend uncommitted changes context if any
        if uncommitted_context:
            retry_prompt = uncommitted_context + retry_prompt

        used_prompt = retry_prompt
        ctx.log(f"Running Codex (session resume: {saved_session_id[:8]}...) for {ctx.microcommit.id}")

        if ctx.verbose:
            verbose_header("IMPLEMENT PROMPT (resume)")
            print(retry_prompt)
            verbose_footer()

        result = agent.implement(
            retry_prompt, ctx.workstream.worktree, log_file,
            stage="implement_resume", session_id=saved_session_id
        )

        # Check if resume failed due to no session - fall back to fresh
        if is_codex_session_resume_failure(result):
            ctx.log("Session resume failed (no session found), falling back to fresh session")
            prompt = build_full_implement_prompt(ctx, human_guidance_section)
            if uncommitted_context:
                prompt = uncommitted_context + prompt

            used_prompt = prompt
            if ctx.verbose:
                verbose_header("IMPLEMENT PROMPT (fallback)")
                print(prompt)
                verbose_footer()

            # Use a separate log file for the fallback attempt
            fallback_log_file = ctx.run_dir / "stages" / "implement_fallback.log"
            result = agent.implement(prompt, ctx.workstream.worktree, fallback_log_file, stage="implement")
        elif not result.success:
            # Resume failed for other reasons - log for pattern discovery but don't fall back
            logger.debug(f"Codex resume failed with unrecognized error: {result.stderr[:200]}")
    else:
        # First attempt: use full prompt
        prompt = build_full_implement_prompt(ctx, human_guidance_section)
        if uncommitted_context:
            prompt = uncommitted_context + prompt
        used_prompt = prompt
        ctx.log(f"Running Codex for {ctx.microcommit.id}")

        if ctx.verbose:
            verbose_header("IMPLEMENT PROMPT")
            print(prompt)
            verbose_footer()

        result = agent.implement(prompt, ctx.workstream.worktree, log_file, stage="implement")

    # Record to transcript
    ctx.transcript.record_agent_call("implement", Actor.CODEX, used_prompt)
    ctx.transcript.record_agent_response(
        "implement", Actor.CODEX, result.stdout or "",
        success=result.success,
        elapsed_seconds=result.elapsed_seconds,
    )

    # Mark session as active for next iteration within this commit's review loop
    ctx.codex_session_active = True

    # Save session ID for this workstream for later resume
    # This prevents the bug where --last resumes a different workstream's session
    if result.session_id:
        save_codex_session_id(ctx.workstream_dir, result.session_id)
        ctx.log(f"Saved Codex session: {result.session_id[:8]}...")

    # Record stats
    record_agent_stats(ctx.workstream_dir, AgentStats(
        timestamp=datetime.now().isoformat(),
        run_id=ctx.run_id,
        agent="codex",
        elapsed_seconds=result.elapsed_seconds,
        microcommit_id=ctx.microcommit.id if ctx.microcommit else None,
    ))

    if ctx.verbose and result.stdout:
        verbose_header("IMPLEMENT OUTPUT")
        print(result.stdout)
        verbose_footer()

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

    # Parse structured status output (JSON block with "status" key)
    # Scan lines in reverse to find the last valid JSON status block
    status_data = None
    if result.stdout:
        for line in reversed(result.stdout.splitlines()):
            line = line.strip()
            if line.startswith('{') and '"status"' in line:
                try:
                    status_data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

    # Handle structured status responses
    if status_data:
        status = status_data.get("status")
        if status == "already_done":
            reason = status_data.get("reason", "work already complete")
            # Only auto-skip if there are NO uncommitted changes.
            # If there ARE uncommitted changes, the work IS done - proceed to test/review.
            if has_uncommitted_changes(ctx.workstream.worktree):
                ctx.log(f"Codex says '{reason}' but uncommitted changes exist - proceeding to test/review")
                # Fall through to normal completion (changes exist, will be tested/reviewed)
            else:
                ctx.log(f"Auto-skipping: {reason}")
                # Mark commit as done without changes
                plan_path = ctx.workstream_dir / "plan.md"
                mark_done(str(plan_path), ctx.microcommit.id)
                ctx.log(f"Marked {ctx.microcommit.id} as done (auto-skip)")
                # HACK: We abuse StageBlocked to signal "success, move to next commit".
                # The caller (run.py) checks for "auto_skip:" prefix and returns "passed".
                # A proper fix would be a dedicated return type, but this minimizes changes.
                raise StageBlocked("implement", f"auto_skip:{ctx.microcommit.id}")
        elif status == "blocked":
            reason = status_data.get("reason", "Codex is blocked")
            raise StageBlocked("implement", f"Codex blocked: {reason}")

    # Verify Codex wrote something
    status_output = get_status_porcelain(ctx.workstream.worktree)
    if not status_output.strip():
        # If Codex had something to say but made no changes, surface it
        if result.stdout and result.stdout.strip():
            msg = result.stdout.strip()
            if len(msg) > 500:
                msg = msg[:500] + "..."
            raise StageError("implement", f"Codex made no changes. Output:\n{msg}", 4)
        raise StageError("implement", "Codex made no changes", 4)

    ctx.log(f"Implementation complete, {len(status_output.strip().splitlines())} files changed")


def _auto_stage_changes(worktree: Path, ctx: RunContext) -> None:
    """Stage all changes (new and modified files) before test.

    Codex sometimes creates or modifies files but doesn't git add them. This causes:
    - Build failures (e.g., templ files not generating .go files)
    - Review blockers ("unstaged changes" warnings)
    - Wasted review cycles when reviewer flags staging issues

    By auto-staging all changes before test, we ensure the reviewer sees exactly
    what will be committed. The commit stage does `git add -A` anyway, so this
    just catches issues earlier and avoids unnecessary feedback loops.
    """
    status_output = get_status_porcelain(worktree)

    # Parse status: ?? = untracked, ' M' = modified unstaged, 'M ' = modified staged
    # We want to stage anything that's not already fully staged
    lines = status_output.splitlines()
    untracked = [line[3:] for line in lines if line.startswith("??")]
    modified_unstaged = [line[3:] for line in lines if line.startswith(" M") or line.startswith(" D")]

    to_stage = untracked + modified_unstaged
    if not to_stage:
        return

    if untracked:
        ctx.log(f"Auto-staging {len(untracked)} new file(s)")
        for f in untracked[:5]:  # Show first 5
            ctx.log(f"  + {f}")
        if len(untracked) > 5:
            ctx.log(f"  ... and {len(untracked) - 5} more")

    if modified_unstaged:
        ctx.log(f"Auto-staging {len(modified_unstaged)} modified file(s)")
        for f in modified_unstaged[:5]:  # Show first 5
            ctx.log(f"  ~ {f}")
        if len(modified_unstaged) > 5:
            ctx.log(f"  ... and {len(modified_unstaged) - 5} more")

    # Stage all unstaged changes
    add_result = stage_files(worktree, to_stage)
    if not add_result.success:
        ctx.log(f"  Warning: git add failed: {add_result.stderr.strip()}")


def stage_test(ctx: RunContext):
    """Run tests."""
    worktree = ctx.workstream.worktree

    # Auto-stage all changes before build/test
    # Codex sometimes creates or modifies files but doesn't git add them
    _auto_stage_changes(worktree, ctx)

    # Run build first to catch compile errors fast (if build command is configured)
    build_cmd = ctx.profile.get_build_command_str()
    if build_cmd:
        ctx.log(f"Running build: {' '.join(build_cmd)}")
        build_start = time.time()
        build_result = subprocess.run(
            build_cmd,
            capture_output=True,
            text=True,
            cwd=str(worktree),
            timeout=ctx.profile.test_timeout
        )
        build_duration = time.time() - build_start
        ctx.log_command(build_cmd, build_result.returncode, build_duration)

        if build_result.returncode != 0:
            # Save build output
            log_path = ctx.run_dir / "stages" / "build.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"=== STDOUT ===\n{build_result.stdout}\n\n=== STDERR ===\n{build_result.stderr}\n"
            )
            raise StageError("test", f"Build failed (exit {build_result.returncode})", 5)
        ctx.log("Build passed")

    # Then run tests
    cmd = ctx.profile.get_test_command()

    ctx.log(f"Running: {' '.join(cmd)}")
    start = time.time()

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(worktree),
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


def stage_review(ctx: RunContext):
    """Run Claude to review the changes with full codebase access.

    Session Reuse:
        On retries (ctx.claude_session_active=True), we attempt `claude --continue`
        to continue the previous session. If resume fails (no session exists), we fall
        back to a fresh session with the full prompt. This handles cases where the
        previous session was lost (timeout, crash, process killed).
    """
    if not ctx.microcommit:
        raise StageError("review", "No micro-commit selected", 9)

    # Check there are changes to review
    if not has_changes_vs_head(ctx.workstream.worktree):
        # Also check for untracked files
        status_output = get_status_porcelain(ctx.workstream.worktree)
        if not status_output.strip():
            ctx.log("No changes to review")
            ctx.transcript.record("review", Actor.SYSTEM, "out", "Skipped: no changes to review")
            return

    agent = ClaudeAgent(timeout=ctx.profile.review_timeout, agents_config=ctx.agents_config)
    log_file = ctx.run_dir / "stages" / "review.log"

    # Determine if we should try session resume
    should_try_resume = ctx.claude_session_active and ctx.review_history

    # Track prompt for transcript
    used_prompt = None

    if should_try_resume:
        # Try session resume with short prompt
        last_review = ctx.review_history[-1].get("review_feedback", {})
        previous_feedback = format_review_for_retry(last_review)

        retry_prompt = render_prompt(
            "review_retry",
            previous_feedback=previous_feedback
        )

        used_prompt = retry_prompt
        ctx.log("Running Claude review (session resume)")

        if ctx.verbose:
            verbose_header("REVIEW PROMPT (resume)")
            print(retry_prompt)
            verbose_footer()

        review = agent.contextual_review(retry_prompt, ctx.workstream.worktree, log_file, stage="review_resume")

        # Check if resume failed due to no session - fall back to fresh
        if is_claude_session_resume_failure(review):
            ctx.log("Session resume failed (no session found), falling back to fresh session")
            prompt = build_full_review_prompt(ctx, agent)
            used_prompt = prompt

            if ctx.verbose:
                verbose_header("REVIEW PROMPT (fallback)")
                print(prompt)
                verbose_footer()

            # Use a separate log file for the fallback attempt
            fallback_log_file = ctx.run_dir / "stages" / "review_fallback.log"
            review = agent.contextual_review(prompt, ctx.workstream.worktree, fallback_log_file, stage="review")
        elif not review.success:
            # Resume failed for other reasons - log for pattern discovery but don't fall back
            error_snippet = (review.stderr + " " + review.notes)[:200]
            logger.debug(f"Claude resume failed with unrecognized error: {error_snippet}")
    else:
        # First attempt: use full contextual prompt
        prompt = build_full_review_prompt(ctx, agent)
        used_prompt = prompt
        ctx.log("Running Claude review (contextual)")

        if ctx.verbose:
            verbose_header("REVIEW PROMPT")
            print(prompt)
            verbose_footer()

        review = agent.contextual_review(prompt, ctx.workstream.worktree, log_file, stage="review")

    # Record to transcript
    ctx.transcript.record_agent_call("review", Actor.CLAUDE, used_prompt)
    review_response = json.dumps({
        "decision": review.decision,
        "confidence": review.confidence,
        "concerns": review.concerns,
        "blockers": review.blockers,
        "required_changes": review.required_changes,
    }, indent=2)
    ctx.transcript.record_agent_response(
        "review", Actor.CLAUDE, review_response,
        success=review.success,
        elapsed_seconds=review.elapsed_seconds,
        input_tokens=review.input_tokens,
        output_tokens=review.output_tokens,
    )

    # Mark session as active for next iteration within this commit's review loop
    ctx.claude_session_active = True

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
        verbose_header("REVIEW RESULT")
        _print_review_result(review)
        verbose_footer()

    # Save review result (including confidence for threshold-based decisions)
    (ctx.run_dir / "claude_review.json").write_text(json.dumps({
        "version": 2,
        "decision": review.decision,
        "confidence": review.confidence,
        "concerns": review.concerns,
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


def stage_commit(ctx: RunContext):
    """Commit changes after all gates pass."""
    if not ctx.microcommit:
        return

    worktree = ctx.workstream.worktree

    # Stage all changes
    add_result = stage_all(worktree)
    if not add_result.success:
        raise StageError("commit", f"git add failed: {add_result.stderr}", 9)

    # Create commit
    commit_msg = f"{ctx.microcommit.id}: {ctx.microcommit.title}"
    commit_result = git_commit(worktree, commit_msg)
    if not commit_result.success:
        raise StageError("commit", f"git commit failed: {commit_result.stderr}", 9)

    # Get the commit SHA
    commit_sha = get_commit_sha(worktree) or "unknown"
    ctx.log(f"Committed: {commit_sha[:8]} - {commit_msg}")

    # Mark micro-commit as done
    plan_path = ctx.workstream_dir / "plan.md"
    mark_done(str(plan_path), ctx.microcommit.id)
    ctx.log(f"Marked {ctx.microcommit.id} as done")

    # Clear Codex session ID - next commit starts fresh
    clear_codex_session_id(ctx.workstream_dir)

    # Auto-push if PR is open (so CI re-runs on the fix)
    if ctx.workstream.pr_number:
        print(f"Pushing to update PR #{ctx.workstream.pr_number}...")
        ctx.log(f"Pushing to update PR #{ctx.workstream.pr_number}...")
        push_result = push(worktree)
        if push_result.success:
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
    worktree = ctx.workstream.worktree
    worktree_str = str(worktree)
    default_branch = ctx.project.default_branch

    # 1. Run full test suite
    cmd = ctx.profile.get_merge_gate_test_command()

    ctx.log(f"Running full test suite: {' '.join(cmd)}")
    start = time.time()

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=worktree_str,
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

    # 2. Check branch is up to date with main (only if remote exists)
    if has_remote(worktree):
        # Fetch to ensure we have latest main
        fetch(worktree, "origin", default_branch)

        # Check if main is ancestor of HEAD (meaning we're rebased on main)
        if not is_ancestor(worktree, f"origin/{default_branch}", "HEAD"):
            # Get divergence info for context
            divergence = get_divergence_count(worktree, f"origin/{default_branch}", "HEAD")
            output = f"Branch needs rebase on {default_branch}.\n"
            if divergence:
                output += f"Main is {divergence[0]} commits ahead, branch is {divergence[1]} commits ahead.\n"
            output += f"Run: git rebase origin/{default_branch}"

            raise StageError(
                "merge_gate",
                f"Branch needs rebase on {default_branch}",
                11,
                details={"type": "rebase", "output": output}
            )

        ctx.log(f"Branch is up to date with {default_branch}")

        # 3. Check for conflict markers in tracked files
        has_issues, diff_output = get_diff_check(worktree, f"origin/{default_branch}...HEAD")

        if has_issues and diff_output:
            # Detect actual issue type - git diff --check reports both conflicts and whitespace
            has_conflicts = any(m in diff_output for m in ["<<<<<<<", "=======", ">>>>>>>"])
            has_whitespace = "trailing whitespace" in diff_output.lower() or "space before tab" in diff_output.lower()

            if has_conflicts:
                message = "Conflict markers detected in files"
                error_type = "conflict"
            elif has_whitespace:
                message = "Whitespace errors detected"
                error_type = "whitespace"
            else:
                message = "Git diff check failed"
                error_type = "diff_check"

            raise StageError(
                "merge_gate",
                message,
                12,
                details={"type": error_type, "output": diff_output}
            )

        ctx.log("No conflict markers found")
    else:
        ctx.log("No remote configured - skipping rebase/conflict checks (local-only mode)")

    ctx.log("Merge gate passed")

    return {}


def _get_changed_files(worktree: Path) -> list[str]:
    """Get list of changed files in the worktree (staged + unstaged)."""
    files = get_diff_names(worktree, "HEAD")

    # Also include untracked files
    status_output = get_status_porcelain(worktree)
    for line in status_output.strip().split("\n"):
        if line and line.startswith("??"):
            files.append(line[3:].strip())

    return files


def stage_human_review(ctx: RunContext):
    """Block until human approves or rejects.

    Uses confidence-based decision logic:
    - Supervised: Always pause (show confidence as info)
    - Gatekeeper: Always pause (human approval required before PR/merge)
    - Autonomous: Auto-continue if confidence >= threshold

    Sensitive paths (auth/*, *.env) increase the required threshold.

    Human gate callback (required):
    - Returns {"action": "approve"} -> proceed
    - Returns {"action": "reject", "reset": bool, "feedback": "..."} -> StageError
    - Callback is mandatory - run must be executed via Prefect flow
    """
    # Load escalation config
    escalation_config = load_escalation_config(ctx.project_dir)

    # Get review result with confidence
    review_path = ctx.run_dir / "claude_review.json"
    review_data = {}
    if review_path.exists():
        review_data = json.loads(review_path.read_text())

    confidence = review_data.get("confidence", 0.5)
    concerns = review_data.get("concerns", [])
    review_decision = review_data.get("decision", "request_changes")

    # Get changed files for threshold calculation
    changed_files = _get_changed_files(ctx.workstream.worktree)
    threshold = get_confidence_threshold(changed_files, escalation_config)

    # Check if any sensitive paths were touched
    sensitive_touched = threshold > escalation_config.commit_confidence_threshold

    # Determine if we should auto-continue
    # Use get_effective_autonomy to respect CLI override (--supervised/--gatekeeper)
    autonomy = get_effective_autonomy(ctx, escalation_config)
    review_approved = review_decision == "approve"

    # Decision logic
    should_auto_continue = False
    reason = ""

    if not review_approved:
        # AI rejected - always need human
        reason = "AI review rejected"
    elif autonomy == "supervised":
        # Always pause in supervised mode
        reason = f"Supervised mode (confidence: {confidence:.0%})"
    elif autonomy == "gatekeeper":
        # Gatekeeper always requires human approval before PR/merge
        reason = f"Gatekeeper mode - human approval required (confidence: {confidence:.0%})"
    elif autonomy == "autonomous":
        if confidence >= threshold:
            should_auto_continue = True
            ctx.log(f"Auto-continuing: confidence {confidence:.0%} >= {threshold:.0%} threshold")
        else:
            reason = f"Low confidence: {confidence:.0%} < {threshold:.0%} required"
            if sensitive_touched:
                reason += " (sensitive paths touched)"

    if should_auto_continue:
        ctx.transcript.record(
            "human_review", Actor.SYSTEM, "out",
            f"Auto-approved: confidence {confidence:.0%} >= {threshold:.0%} threshold"
        )
        return

    # Human gate callback is required (Prefect suspend_flow_run)
    if not ctx.human_gate_callback:
        raise StageError(
            "human_review",
            "No human gate callback configured. Run must be executed via Prefect flow.",
            9
        )

    # Build escalation context for callback
    escalation_context = {
        "confidence": confidence,
        "threshold": threshold,
        "concerns": concerns,
        "changed_files": changed_files,
        "sensitive_touched": sensitive_touched,
        "reason": reason,
        "workstream_id": ctx.workstream.id,
        "workstream_dir": str(ctx.workstream_dir),
        "run_id": ctx.run_id,
    }

    transition(ctx.workstream_dir, WorkstreamState.AWAITING_HUMAN_REVIEW,
              reason="review confidence below threshold")
    ctx.log("Awaiting human review")

    try:
        approval = ctx.human_gate_callback(escalation_context)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        raise StageError("human_review", f"Human gate callback failed: {e}", 9)

    action = approval.get("action")

    if action == ACTION_APPROVE:
        ctx.log("Human approved")
        # Transcript uses past tense ("approved") for human-readable logging,
        # distinct from action constants ("approve") used in the protocol layer
        ctx.transcript.record_human_input("human_review", "approved")
        transition(ctx.workstream_dir, WorkstreamState.ACTIVE, reason="human approved")
        # Exit flow so approve command can trigger new run
        raise StageHumanGateProcessed("human_review", ACTION_APPROVE)

    elif action == ACTION_REJECT:
        feedback = approval.get("feedback", "")
        reset = approval.get("reset", False)
        ctx.log(f"Human rejected (reset={reset}) with feedback: {feedback[:100]}...")
        ctx.transcript.record_human_input("human_review", "rejected", feedback)
        # Store feedback and reset flag for next implement run
        store_human_feedback(ctx.workstream_dir, feedback, reset)
        transition(ctx.workstream_dir, WorkstreamState.HUMAN_REJECTED, reason="human rejected")
        # Exit flow so reject command can trigger new run
        raise StageHumanGateProcessed("human_review", ACTION_REJECT, feedback, reset)

    else:
        raise StageError("human_review", f"Unknown action: {action}", 9)


