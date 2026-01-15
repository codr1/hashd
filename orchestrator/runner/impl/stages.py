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
from orchestrator.lib.review import load_review, format_review, format_review_for_retry, print_review
from orchestrator.lib.context import get_codebase_context
from orchestrator.lib.config import load_escalation_config, get_confidence_threshold, update_workstream_meta
from orchestrator.lib.directives import load_directives
from orchestrator.agents.codex import CodexAgent
from orchestrator.agents.claude import ClaudeAgent
from orchestrator.runner.impl.breakdown import generate_breakdown, append_commits_to_plan
from orchestrator.lib.stats import AgentStats, record_agent_stats
from orchestrator.runner.git_utils import has_uncommitted_changes


def _save_codex_session_id(workstream_dir: Path, session_id: str) -> None:
    """Save Codex session ID for this workstream for later resume.

    Session IDs are stored in meta.env per-workstream to enable resuming the correct
    session when multiple workstreams are running concurrently. Without this, Codex's
    `resume --last` would resume the most recent session globally, which could
    be from a different workstream.

    The session ID is cleared when a commit is completed (see stage_update_state).
    """
    try:
        update_workstream_meta(workstream_dir, {"CODEX_SESSION_ID": session_id})
    except OSError as e:
        logger.warning(f"Failed to save Codex session ID: {e}")


def _clear_codex_session_id(workstream_dir: Path) -> None:
    """Clear saved Codex session ID.

    Called when a commit is completed so the next commit starts with a fresh session.
    """
    try:
        update_workstream_meta(workstream_dir, {"CODEX_SESSION_ID": None})
    except OSError as e:
        logger.warning(f"Failed to clear Codex session ID: {e}")


def _verbose_header(title: str):
    """Print a section header for verbose output."""
    print(f"\n{'='*60}")
    print(title)
    print('='*60)


def _verbose_footer():
    """Print a section footer for verbose output."""
    print('='*60 + "\n")


MAX_FAILURE_OUTPUT_CHARS = 3000

def _truncate_output(output: str) -> str:
    """Truncate output, keeping start and end for context."""
    if len(output) <= MAX_FAILURE_OUTPUT_CHARS:
        return output
    marker = "\n\n... [truncated] ...\n\n"
    available = MAX_FAILURE_OUTPUT_CHARS - len(marker)
    head_chars = (available * 2) // 3
    tail_chars = available - head_chars
    return f"{output[:head_chars]}{marker}{output[-tail_chars:]}"


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
        agents_config=ctx.agents_config,
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


def _get_uncommitted_changes_context(worktree: Path) -> str:
    """Get context about uncommitted changes for the implement prompt.

    When Codex blocks with uncommitted changes (e.g., asking for clarification),
    those changes persist in the worktree. On the next run, Codex needs to know
    about these changes to avoid confusion. Without this context, Codex might:
    - Claim files don't exist when they're already staged
    - Try to re-implement work that's already done
    - Ask for confirmation instead of proceeding

    This function builds a context string describing staged, unstaged, and untracked
    files. The caller prepends this to the prompt so Codex understands the current
    worktree state.

    Returns:
        A markdown-formatted context string if there are uncommitted changes,
        or empty string if the worktree is clean.
    """
    # Check for staged changes
    staged_result = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--cached", "--stat"],
        capture_output=True, text=True
    )
    staged_stat = staged_result.stdout.strip()

    # Check for unstaged changes
    unstaged_result = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--stat"],
        capture_output=True, text=True
    )
    unstaged_stat = unstaged_result.stdout.strip()

    # Check for untracked files
    untracked_result = subprocess.run(
        ["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True
    )
    untracked = untracked_result.stdout.strip()

    if not staged_stat and not unstaged_stat and not untracked:
        return ""

    lines = [
        "## IMPORTANT: Uncommitted Changes From Previous Attempt",
        "",
        "There are uncommitted changes in the worktree from your previous attempt.",
        "These changes are likely partial progress toward the current task.",
        "",
    ]

    if staged_stat:
        lines.append("**Staged changes:**")
        lines.append("```")
        lines.append(staged_stat)
        lines.append("```")
        lines.append("")

    if unstaged_stat:
        lines.append("**Unstaged changes:**")
        lines.append("```")
        lines.append(unstaged_stat)
        lines.append("```")
        lines.append("")

    if untracked:
        untracked_lines = untracked.split("\n")
        untracked_files = untracked_lines[:10]  # Limit to 10 files
        lines.append("**Untracked files:**")
        for f in untracked_files:
            lines.append(f"- {f}")
        if len(untracked_lines) > 10:
            lines.append(f"- ... and {len(untracked_lines) - 10} more")
        lines.append("")

    lines.extend([
        "**Action:** Continue from where you left off. If these changes are correct,",
        "complete any remaining work and proceed. Do NOT ask for confirmation -",
        "just continue implementing. Only discard if changes are fundamentally wrong.",
        "",
        "---",
        "",
    ])

    return "\n".join(lines)


def _build_full_implement_prompt(ctx: RunContext, human_guidance_section: str) -> str:
    """Build the full implementation prompt with all context.

    Used for first attempts and as fallback when session resume fails.
    """
    conversation_history_section = ""
    if ctx.review_history:
        history_entries = format_conversation_history(ctx.review_history)
        conversation_history_section = render_prompt(
            "implement_history",
            history_entries=history_entries
        )

    # Build review context section from last review output
    review_context_section = ""
    last_review = _load_last_review_output(ctx)
    if last_review:
        review_content = format_review(last_review)
        review_context_section = render_prompt(
            "implement_review_context",
            review_content=review_content
        )

    # Pre-compute codebase context to reduce agent exploration time
    codebase_context = ""
    if ctx.workstream and ctx.workstream.worktree:
        codebase_context = get_codebase_context(Path(ctx.workstream.worktree))

    # Build directives section from global/project/feature directives
    directives_section = ""
    repo_path = Path(ctx.project.repo_path) if ctx.project.repo_path else None
    workstream_dir = ctx.workstream_dir if ctx.workstream_dir else None
    if repo_path:
        directives_content = load_directives(repo_path, workstream_dir)
        if directives_content:
            directives_section = render_prompt(
                "implement_directives",
                directives_content=directives_content
            )

    return render_prompt(
        "implement",
        system_description=ctx.project.description or f"Project: {ctx.project.name}",
        tech_preferred=ctx.project.tech_preferred or "Not specified",
        tech_acceptable=ctx.project.tech_acceptable or "Not specified",
        tech_avoid=ctx.project.tech_avoid or "Not specified",
        commit_id=ctx.microcommit.id,
        commit_title=ctx.microcommit.title,
        commit_description=ctx.microcommit.block_content,
        codebase_context=codebase_context,
        directives_section=directives_section,
        review_context_section=review_context_section,
        conversation_history_section=conversation_history_section,
        human_guidance_section=human_guidance_section
    )


def _is_session_resume_failure(result) -> bool:
    """Check if a failed result is due to session resume failure.

    Returns True if the error indicates no session to resume (as opposed to
    other errors like network issues or code problems).
    """
    if result.success:
        return False

    stderr_lower = result.stderr.lower()
    # Common patterns for "no session to resume" errors
    session_error_patterns = [
        "no session",
        "session not found",
        "no previous session",
        "cannot resume",
        "nothing to resume",
        "no conversation",
    ]
    return any(pattern in stderr_lower for pattern in session_error_patterns)


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
    uncommitted_context = _get_uncommitted_changes_context(ctx.workstream.worktree)
    if uncommitted_context:
        ctx.log("Detected uncommitted changes from previous attempt - injecting context")

    agent = CodexAgent(timeout=ctx.profile.implement_timeout, agents_config=ctx.agents_config)
    log_file = ctx.run_dir / "stages" / "implement.log"

    # Get saved session ID from workstream meta (if any)
    saved_session_id = ctx.workstream.codex_session_id

    # Determine if we should try session resume
    # We need BOTH a saved session ID and review history to resume
    should_try_resume = saved_session_id and ctx.review_history

    if should_try_resume:
        # Try session resume with short prompt
        last_entry = ctx.review_history[-1]

        if last_entry.get("test_failure"):
            review_feedback = f"Tests failed:\n\n{_truncate_output(last_entry['test_failure'])}\n\nFix the code to make tests pass."
        elif last_entry.get("build_failure"):
            review_feedback = f"Build failed:\n\n{_truncate_output(last_entry['build_failure'])}\n\nFix the build errors."
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

        ctx.log(f"Running Codex (session resume: {saved_session_id[:8]}...) for {ctx.microcommit.id}")

        if ctx.verbose:
            _verbose_header("IMPLEMENT PROMPT (resume)")
            print(retry_prompt)
            _verbose_footer()

        result = agent.implement(
            retry_prompt, ctx.workstream.worktree, log_file,
            stage="implement_resume", session_id=saved_session_id
        )

        # Check if resume failed due to no session - fall back to fresh
        if _is_session_resume_failure(result):
            ctx.log("Session resume failed (no session found), falling back to fresh session")
            prompt = _build_full_implement_prompt(ctx, human_guidance_section)
            if uncommitted_context:
                prompt = uncommitted_context + prompt

            if ctx.verbose:
                _verbose_header("IMPLEMENT PROMPT (fallback)")
                print(prompt)
                _verbose_footer()

            # Use a separate log file for the fallback attempt
            fallback_log_file = ctx.run_dir / "stages" / "implement_fallback.log"
            result = agent.implement(prompt, ctx.workstream.worktree, fallback_log_file, stage="implement")
        elif not result.success:
            # Resume failed for other reasons - log for pattern discovery but don't fall back
            logger.debug(f"Codex resume failed with unrecognized error: {result.stderr[:200]}")
    else:
        # First attempt: use full prompt
        prompt = _build_full_implement_prompt(ctx, human_guidance_section)
        if uncommitted_context:
            prompt = uncommitted_context + prompt
        ctx.log(f"Running Codex for {ctx.microcommit.id}")

        if ctx.verbose:
            _verbose_header("IMPLEMENT PROMPT")
            print(prompt)
            _verbose_footer()

        result = agent.implement(prompt, ctx.workstream.worktree, log_file, stage="implement")

    # Mark session as active for next iteration within this commit's review loop
    ctx.codex_session_active = True

    # Save session ID for this workstream for later resume
    # This prevents the bug where --last resumes a different workstream's session
    if result.session_id:
        _save_codex_session_id(ctx.workstream_dir, result.session_id)
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
    result = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True, text=True
    )

    # Parse status: ?? = untracked, ' M' = modified unstaged, 'M ' = modified staged
    # We want to stage anything that's not already fully staged
    lines = result.stdout.splitlines()
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
    add_result = subprocess.run(
        ["git", "-C", str(worktree), "add", "--"] + to_stage,
        capture_output=True, text=True
    )
    if add_result.returncode != 0:
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


def _build_full_review_prompt(ctx: RunContext, agent: ClaudeAgent) -> str:
    """Build the full contextual review prompt.

    Used for first attempts and as fallback when session resume fails.
    """
    system_description = ctx.project.description or f"Project: {ctx.project.name}"
    story_context = _get_story_context(ctx)

    return agent.build_contextual_review_prompt(
        system_description=system_description,
        tech_preferred=ctx.project.tech_preferred,
        tech_acceptable=ctx.project.tech_acceptable,
        tech_avoid=ctx.project.tech_avoid,
        story_context=story_context,
        commit_title=ctx.microcommit.title,
        commit_description=ctx.microcommit.block_content,
        review_history=ctx.review_history
    )


def _is_claude_session_resume_failure(review) -> bool:
    """Check if a failed review is due to session resume failure.

    Returns True if the error indicates no session to resume (as opposed to
    other errors like network issues or parsing problems).
    """
    if review.success:
        return False

    # Check both stderr and notes for session-related errors
    error_text = (review.stderr + " " + review.notes).lower()
    session_error_patterns = [
        "no session",
        "session not found",
        "no previous session",
        "cannot continue",
        "nothing to continue",
        "no conversation",
    ]
    return any(pattern in error_text for pattern in session_error_patterns)


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

    agent = ClaudeAgent(timeout=ctx.profile.review_timeout, agents_config=ctx.agents_config)
    log_file = ctx.run_dir / "stages" / "review.log"

    # Determine if we should try session resume
    should_try_resume = ctx.claude_session_active and ctx.review_history

    if should_try_resume:
        # Try session resume with short prompt
        last_review = ctx.review_history[-1].get("review_feedback", {})
        previous_feedback = format_review_for_retry(last_review)

        retry_prompt = render_prompt(
            "review_retry",
            previous_feedback=previous_feedback
        )

        ctx.log("Running Claude review (session resume)")

        if ctx.verbose:
            _verbose_header("REVIEW PROMPT (resume)")
            print(retry_prompt)
            _verbose_footer()

        review = agent.contextual_review(retry_prompt, ctx.workstream.worktree, log_file, stage="review_resume")

        # Check if resume failed due to no session - fall back to fresh
        if _is_claude_session_resume_failure(review):
            ctx.log("Session resume failed (no session found), falling back to fresh session")
            prompt = _build_full_review_prompt(ctx, agent)

            if ctx.verbose:
                _verbose_header("REVIEW PROMPT (fallback)")
                print(prompt)
                _verbose_footer()

            # Use a separate log file for the fallback attempt
            fallback_log_file = ctx.run_dir / "stages" / "review_fallback.log"
            review = agent.contextual_review(prompt, ctx.workstream.worktree, fallback_log_file, stage="review")
        elif not review.success:
            # Resume failed for other reasons - log for pattern discovery but don't fall back
            error_snippet = (review.stderr + " " + review.notes)[:200]
            logger.debug(f"Claude resume failed with unrecognized error: {error_snippet}")
    else:
        # First attempt: use full contextual prompt
        prompt = _build_full_review_prompt(ctx, agent)
        ctx.log("Running Claude review (contextual)")

        if ctx.verbose:
            _verbose_header("REVIEW PROMPT")
            print(prompt)
            _verbose_footer()

        review = agent.contextual_review(prompt, ctx.workstream.worktree, log_file, stage="review")

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
        _verbose_header("REVIEW RESULT")
        _print_review_result(review)
        _verbose_footer()

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

    # Clear Codex session ID - next commit starts fresh
    _clear_codex_session_id(ctx.workstream_dir)

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

    # 2. Check branch is up to date with main
    # First, fetch to ensure we have latest main
    subprocess.run(
        ["git", "-C", worktree_str, "fetch", "origin", default_branch],
        capture_output=True, text=True
    )

    # Check if main is ancestor of HEAD (meaning we're rebased on main)
    result = subprocess.run(
        ["git", "-C", worktree_str, "merge-base", "--is-ancestor",
         f"origin/{default_branch}", "HEAD"],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        # Get divergence info for context
        diverge_result = subprocess.run(
            ["git", "-C", worktree_str, "rev-list", "--left-right", "--count",
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
        ["git", "-C", worktree_str, "diff", "--check", f"origin/{default_branch}...HEAD"],
        capture_output=True, text=True
    )

    if result.returncode != 0 and result.stdout.strip():
        output = result.stdout.strip()

        # Detect actual issue type - git diff --check reports both conflicts and whitespace
        has_conflicts = any(m in output for m in ["<<<<<<<", "=======", ">>>>>>>"])
        has_whitespace = "trailing whitespace" in output.lower() or "space before tab" in output.lower()

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
            details={"type": error_type, "output": output}
        )

    ctx.log("No conflict markers found")
    ctx.log("Merge gate passed")

    return {}


def _get_changed_files(worktree: Path) -> list[str]:
    """Get list of changed files in the worktree (staged + unstaged)."""
    result = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--name-only", "HEAD"],
        capture_output=True, text=True
    )
    files = result.stdout.strip().split("\n") if result.stdout.strip() else []

    # Also include untracked files
    status = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True, text=True
    )
    for line in status.stdout.strip().split("\n"):
        if line and line.startswith("??"):
            files.append(line[3:].strip())

    return files


def _format_escalation_context(
    ctx: RunContext,
    confidence: float,
    threshold: float,
    concerns: list[str],
    changed_files: list[str],
    sensitive_touched: bool,
    reason: str,
) -> str:
    """Format rich escalation context for human review pause.

    Shows confidence, concerns, changed files, and available commands.
    """
    lines = []

    # Header with confidence
    lines.append(f"REVIEW PAUSED - {reason}")
    lines.append("")

    # Commit info
    if ctx.microcommit:
        lines.append(f"Commit: {ctx.microcommit.id} - {ctx.microcommit.title}")

    # Confidence vs threshold
    lines.append(f"Confidence: {confidence:.0%} (threshold: {threshold:.0%})")
    if sensitive_touched:
        lines.append("  [Sensitive paths touched - threshold raised]")

    # Changed files summary
    if changed_files:
        lines.append(f"Changed: {len(changed_files)} file(s)")
        # Show first few files
        for f in changed_files[:5]:
            lines.append(f"  - {f}")
        if len(changed_files) > 5:
            lines.append(f"  ... and {len(changed_files) - 5} more")

    # Concerns from AI
    if concerns:
        lines.append("")
        lines.append("Concerns:")
        for c in concerns:
            lines.append(f"  - {c}")

    # Commands
    lines.append("")
    lines.append("Commands:")
    lines.append("  wf approve           # Accept and continue")
    lines.append("  wf approve -f \"...\"  # Accept with guidance")
    lines.append("  wf reject -f \"...\"   # Reject with feedback")
    lines.append("  wf diff              # See full diff")

    return "\n".join(lines)


def stage_human_review(ctx: RunContext):
    """Block until human approves or rejects.

    Uses confidence-based decision logic:
    - Supervised: Always pause (show confidence as info)
    - Gatekeeper: Auto-continue if confidence >= threshold
    - Autonomous: Auto-continue if confidence >= threshold (merge gate separate)

    Sensitive paths (auth/*, *.env) increase the required threshold.

    Checks for approval file:
    - human_approval.json with {"action": "approve"} -> proceed
    - human_approval.json with {"action": "reject", "reset": bool, "feedback": "..."} -> StageError
    - No file -> StageBlocked (waiting for human)
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
    autonomy = escalation_config.autonomy
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
    elif autonomy in ("gatekeeper", "autonomous"):
        if confidence >= threshold:
            should_auto_continue = True
            ctx.log(f"Auto-continuing: confidence {confidence:.0%} >= {threshold:.0%} threshold")
        else:
            reason = f"Low confidence: {confidence:.0%} < {threshold:.0%} required"
            if sensitive_touched:
                reason += " (sensitive paths touched)"

    if should_auto_continue:
        return

    approval_file = ctx.workstream_dir / "human_approval.json"

    if not approval_file.exists():
        # Update status to awaiting human review
        _update_workstream_status(ctx.workstream_dir, "awaiting_human_review")
        ctx.log("Awaiting human review")

        # Build rich escalation context
        escalation_msg = _format_escalation_context(
            ctx=ctx,
            confidence=confidence,
            threshold=threshold,
            concerns=concerns,
            changed_files=changed_files,
            sensitive_touched=sensitive_touched,
            reason=reason,
        )
        raise StageBlocked("human_review", escalation_msg)

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
