"""
wf plan command - Unified story planning.

Commands:
  wf plan                  - Discovery from REQS.md, interactive session
  wf plan new ["title"]    - Ad-hoc story creation
  wf plan clone STORY-xxx  - Clone a locked story
  wf plan STORY-xxx        - Edit existing story (if unlocked)
"""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from orchestrator.lib.config import ProjectConfig, load_workstream
from orchestrator.lib.agents_config import load_agents_config, get_stage_command
from orchestrator.lib.planparse import parse_plan
from orchestrator.lib.prompts import render_prompt
from orchestrator.lib.suggestions import (
    load_suggestions,
    save_suggestions,
    rotate_suggestions,
    create_suggestions_from_discovery,
    get_suggestion_by_id,
    get_suggestion_by_name,
    mark_suggestion_in_progress,
)
from orchestrator.pm.claude_utils import extract_json_with_preamble
from orchestrator.pm.stories import (
    list_stories,
    load_story,
    clone_story,
    create_story,
    update_story,
    is_story_locked,
    resurrect_story,
)
from orchestrator.pm.planner import run_plan_session, run_refine_session, run_edit_session
from orchestrator.pm.reqs_annotate import annotate_reqs_for_story
from orchestrator.runner.impl.breakdown import append_commits_to_plan


def extract_final_review_concerns(final_review_path: Path) -> str:
    """Extract concerns section from final_review.md.

    Returns the concerns text if the verdict was CONCERNS, empty string if APPROVE.
    If parsing fails, returns the full content so the AI can interpret it.
    """
    content = final_review_path.read_text()
    if not content.strip():
        return ""

    # Check for APPROVE verdict - multiple formats the AI might use
    content_upper = content.upper()
    approve_indicators = ["**APPROVE**", "VERDICT: APPROVE", "## APPROVE", "APPROVED"]
    if any(indicator in content_upper for indicator in approve_indicators):
        # Double-check it's not "APPROVED WITH CONCERNS" or similar
        if "CONCERN" not in content_upper:
            return ""

    # Check for explicit CONCERNS verdict
    concerns_indicators = ["**CONCERNS**", "VERDICT: CONCERNS", "## CONCERNS"]
    has_concerns_verdict = any(indicator in content_upper for indicator in concerns_indicators)

    if not has_concerns_verdict:
        # Either: (1) APPROVE verdict with "concern" word in discussion text, or
        #         (2) No clear verdict at all. Either way, no actionable concerns.
        return ""

    # Try to extract just the concerns section
    # Pattern: ## Concerns or ### Concerns or ### 1. Concerns, etc.
    concerns_match = re.search(
        r'##+ (?:\d+\.\s*)?Concerns\s*\n(.*?)(?=\n##[^#]|\n\*\*[A-Z]|\Z)',
        content,
        re.DOTALL | re.IGNORECASE
    )
    if concerns_match:
        extracted = concerns_match.group(1).strip()
        if extracted:
            return extracted

    # Parsing failed but we know there are concerns - return everything after the verdict
    # so the AI can make sense of it
    verdict_pos = content_upper.find("CONCERNS")
    if verdict_pos != -1:
        # Find the next newline after "CONCERNS" and return everything after
        newline_pos = content.find("\n", verdict_pos)
        if newline_pos != -1:
            remainder = content[newline_pos:].strip()
            if remainder:
                return remainder

    # Last resort: return the full content
    return content


def cmd_plan(args, ops_dir: Path, project_config: ProjectConfig):
    """Main entry point for wf plan."""
    # wf plan list
    if getattr(args, 'list', False):
        return cmd_plan_list(args, ops_dir, project_config)

    # wf plan story "title"
    if getattr(args, 'story', False):
        return cmd_plan_quick(args, ops_dir, project_config, story_type="feature")

    # wf plan bug "title"
    if getattr(args, 'bug', False):
        return cmd_plan_quick(args, ops_dir, project_config, story_type="bug")

    # wf plan new [<id_or_name>]
    if getattr(args, 'new', False):
        return cmd_plan_new(args, ops_dir, project_config)

    # wf plan clone STORY-xxx
    if getattr(args, 'clone', False):
        return cmd_plan_clone(args, ops_dir, project_config)

    # wf plan edit STORY-xxx
    if getattr(args, 'edit', False):
        story_id = getattr(args, 'story_id', None)
        return cmd_plan_edit(args, ops_dir, project_config, story_id)

    # wf plan add <ws_id> "title"
    if getattr(args, 'add', False):
        return cmd_plan_add(args, ops_dir, project_config)

    # wf plan resurrect STORY-xxx
    if getattr(args, 'resurrect', False):
        return cmd_plan_resurrect(args, ops_dir, project_config)

    # wf plan (discovery from REQS.md)
    return cmd_plan_discover(args, ops_dir, project_config)


def cmd_plan_discover(args, ops_dir: Path, project_config: ProjectConfig):
    """Discovery mode: analyze REQS.md and propose stories."""
    project_dir = ops_dir / "projects" / project_config.name
    yes_flag = getattr(args, 'yes', False)

    # Check for REQS.md
    reqs_path = Path(project_config.repo_path) / "REQS.md"
    if not reqs_path.exists():
        print(f"No REQS.md found at {reqs_path}")
        print("Use 'wf plan story' or 'wf plan bug' for quick stories.")
        return 1

    # Check for existing suggestions
    existing = load_suggestions(project_dir)
    if existing:
        if not yes_flag:
            print(f"Suggestions file exists (generated {existing.generated_at})")
            in_progress = [s for s in existing.suggestions if s.status == "in_progress"]
            if in_progress:
                print(f"  Warning: {len(in_progress)} suggestion(s) in progress")
            response = input("Override existing suggestions? [y/N]: ").strip().lower()
            if response != 'y':
                print("Cancelled. Use 'wf plan list' to view existing suggestions.")
                return 0
        # Rotate old suggestions
        rotated = rotate_suggestions(project_dir)
        if rotated:
            print(f"Rotated: {rotated.name}")

    print("Starting planning session...")
    print(f"Reading: {reqs_path}")
    print()

    success, response = run_plan_session(project_config, ops_dir, project_dir)

    if not success:
        print(response)
        return 1

    # Parse JSON from response
    preamble, json_str = extract_json_with_preamble(response)

    # Print Claude's analysis (the preamble)
    if preamble:
        print(preamble)
        print()

    if not json_str:
        print("Warning: No structured suggestions found in response")
        print("Full response:")
        print(response)
        return 1

    try:
        data = json.loads(json_str)
        suggestions_data = data.get("suggestions", [])
    except json.JSONDecodeError as e:
        print(f"Warning: Failed to parse suggestions JSON: {e}")
        print("Full response:")
        print(response)
        return 1

    if not suggestions_data:
        print("No suggestions generated.")
        return 0

    # Save suggestions
    suggestions_file = create_suggestions_from_discovery(suggestions_data)
    if not save_suggestions(project_dir, suggestions_file):
        print("ERROR: Failed to save suggestions")
        return 1

    # Display suggestions
    print("-" * 60)
    print("Suggestions saved. Pick one to create a story:")
    print()
    for s in suggestions_file.suggestions:
        print(f"  [{s.id}] {s.title}")
        print(f"      {s.summary}")
        print()

    print("Next:")
    print("  wf plan list              # View suggestions")
    print("  wf plan new 1             # Create story from suggestion 1")
    print("  wf plan new \"auth\"        # Create story by name match")
    return 0


def cmd_plan_list(args, ops_dir: Path, project_config: ProjectConfig):
    """List current suggestions."""
    project_dir = ops_dir / "projects" / project_config.name

    suggestions_file = load_suggestions(project_dir)
    if not suggestions_file:
        print("No suggestions found.")
        print("Run 'wf plan' to generate suggestions from REQS.md")
        return 0

    print(f"Suggestions (generated {suggestions_file.generated_at})")
    print()

    for s in suggestions_file.suggestions:
        status_marker = ""
        if s.status == "in_progress":
            status_marker = f" [IN PROGRESS -> {s.story_id}]"
        elif s.status == "done":
            status_marker = f" [DONE -> {s.story_id}]"

        print(f"  [{s.id}] {s.title}{status_marker}")
        print(f"      {s.summary}")
        if s.reqs_refs:
            print(f"      Refs: {', '.join(s.reqs_refs)}")
        print()

    available = [s for s in suggestions_file.suggestions if s.status == "available"]
    if available:
        print("Next:")
        print(f"  wf plan new {available[0].id}             # Create story from suggestion")
    else:
        print("All suggestions have been used.")
        print("Run 'wf plan' to generate new suggestions.")

    return 0


def resolve_smart_feedback(feedback: str | None) -> str | None:
    """Resolve -f flag: if it's an existing file, read it; otherwise use as text."""
    if not feedback:
        return None

    # Check if it's an existing file
    if os.path.isfile(feedback):
        try:
            return Path(feedback).read_text()
        except OSError as e:
            logger.warning(f"Could not read file '{feedback}': {e}. Using as literal text.")

    return feedback


def annotate_and_commit_reqs(
    story: "Story",
    project_config: ProjectConfig,
    project_dir: Path,
    print_prefix: str = "  ",
) -> bool:
    """Annotate REQS.md with WIP markers and commit.

    Returns True if annotations were made and committed.
    """
    success, msg = annotate_reqs_for_story(story, project_config, project_dir=project_dir)

    if not success:
        print(f"{print_prefix}Warning: {msg}")
        return False

    # Check if REQS.md actually changed (git status)
    repo_path = project_config.repo_path
    reqs_file = project_config.reqs_path
    status_result = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain", reqs_file],
        capture_output=True, text=True
    )
    if not status_result.stdout.strip():
        # No changes to REQS.md
        return False

    # Print truncated response
    if len(msg) > 80:
        truncated = msg[:80].rsplit(' ', 1)[0]
        print(f"{print_prefix}{truncated}...")
    else:
        print(f"{print_prefix}{msg}")

    # Commit the annotation
    add_result = subprocess.run(
        ["git", "-C", str(repo_path), "add", reqs_file],
        capture_output=True, text=True
    )
    if add_result.returncode != 0:
        print(f"{print_prefix}Warning: git add failed: {add_result.stderr.strip()}")
        return False

    commit_result = subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m",
         f"Mark requirements as WIP for {story.id}\n\n{story.title}"],
        capture_output=True, text=True
    )
    if commit_result.returncode == 0:
        print(f"{print_prefix}Committed REQS annotation")
        return True
    elif "nothing to commit" in (commit_result.stdout + commit_result.stderr):
        return False
    else:
        print(f"{print_prefix}Warning: commit failed: {commit_result.stderr.strip()}")
        return False


def cmd_plan_quick(args, ops_dir: Path, project_config: ProjectConfig, story_type: str):
    """Quick story/bug creation (skips REQS discovery).

    story_type: "feature" or "bug"
    """
    project_dir = ops_dir / "projects" / project_config.name
    title = args.title
    feedback = resolve_smart_feedback(getattr(args, 'feedback', None))

    type_label = "bug fix" if story_type == "bug" else "feature"
    print(f"Creating {type_label}: {title}")

    # Build feedback with type context
    type_context = f"Story type: {story_type}\n"
    if story_type == "bug":
        type_context += "This is a bug fix. Focus on identifying the root cause and minimal fix.\n"
    else:
        type_context += "This is a new feature. Define clear acceptance criteria.\n"

    combined_feedback = type_context
    if feedback:
        combined_feedback += f"\nContext:\n{feedback}"

    success, story_data, message = run_refine_session(
        title, project_config, ops_dir, project_dir, feedback=combined_feedback
    )

    if not success:
        print(message)
        return 1

    # Add type to story data
    story_data["type"] = story_type

    # Create the story
    story = create_story(project_dir, story_data)
    print(f"Created {story.id}: {story.title}")

    # For quick stories, check REQS for overlap (optional, high confidence only for features)
    # For bugs, almost never touch REQS
    if story_type == "feature":
        print("Checking REQS.md for overlap...")
        if not annotate_and_commit_reqs(story, project_config, project_dir):
            print("  No overlapping requirements found (new work)")
    else:
        # Bug - skip REQS annotation, will be handled during SPEC review if behavior changes
        print("  (Bug fix - REQS annotation skipped, SPEC update conditional on behavior change)")

    print(f"\nTo start implementation: wf approve {story.id}")
    return 0


def cmd_plan_new(args, ops_dir: Path, project_config: ProjectConfig):
    """Create a story from suggestion or ad-hoc.

    If title is a number, looks up suggestion by ID.
    If title matches a suggestion name, uses that suggestion.
    Otherwise creates an ad-hoc story.
    """
    project_dir = ops_dir / "projects" / project_config.name
    title = getattr(args, 'title', None)
    feedback = getattr(args, 'feedback', None)

    suggestion = None
    suggestion_context = None

    # Check if title refers to a suggestion
    if title:
        suggestions_file = load_suggestions(project_dir)
        if suggestions_file:
            # Try as number first
            try:
                suggestion_id = int(title)
                suggestion = get_suggestion_by_id(suggestions_file, suggestion_id)
            except ValueError:
                # Try as name match
                suggestion = get_suggestion_by_name(suggestions_file, title)

            if suggestion:
                if suggestion.status != "available":
                    print(f"Suggestion [{suggestion.id}] already used (status: {suggestion.status})")
                    if suggestion.story_id:
                        print(f"  See: {suggestion.story_id}")
                    return 1

                print(f"Creating story from suggestion [{suggestion.id}]: {suggestion.title}")
                # Build context from suggestion for the refine session
                suggestion_context = (
                    f"Based on planning suggestion:\n"
                    f"Title: {suggestion.title}\n"
                    f"Summary: {suggestion.summary}\n"
                    f"Rationale: {suggestion.rationale}\n"
                    f"Requirements refs: {', '.join(suggestion.reqs_refs)}"
                )
                title = suggestion.title

    if not suggestion and title:
        print(f"Creating story: {title}")
    elif not suggestion:
        print("Creating new story...")

    # Combine suggestion context with user feedback
    combined_feedback = suggestion_context
    if feedback:
        if combined_feedback:
            combined_feedback = f"{combined_feedback}\n\nAdditional guidance:\n{feedback}"
        else:
            combined_feedback = feedback

    success, story_data, message = run_refine_session(
        title or "", project_config, ops_dir, project_dir, feedback=combined_feedback
    )

    if not success:
        print(message)
        return 1

    # Create the story
    story = create_story(project_dir, story_data)
    print(f"Created {story.id}: {story.title}")

    # Mark suggestion as in_progress if we used one
    if suggestion:
        mark_suggestion_in_progress(project_dir, suggestion.id, story.id)
        print(f"  Linked to suggestion [{suggestion.id}]")

    # Annotate REQS.md with WIP markers (mandatory for suggestions, they come from REQS)
    print("Annotating REQS.md...")
    annotate_and_commit_reqs(story, project_config, project_dir)

    print(f"\nTo start implementation: wf approve {story.id}")
    return 0


def cmd_plan_clone(args, ops_dir: Path, project_config: ProjectConfig):
    """Clone a locked story to create an editable copy."""
    project_dir = ops_dir / "projects" / project_config.name
    story_id = args.clone_id

    # Validate story exists
    story = load_story(project_dir, story_id)
    if not story:
        print(f"Story not found: {story_id}")
        return 1

    # Clone it
    clone = clone_story(project_dir, story_id)
    if not clone:
        print(f"Failed to clone {story_id}")
        return 1

    print(f"Created {clone.id}: {clone.title}")
    print(f"(cloned from {story_id})")
    return 0


def cmd_plan_resurrect(args, ops_dir: Path, project_config: ProjectConfig):
    """Resurrect an abandoned story."""
    project_dir = ops_dir / "projects" / project_config.name
    story_id = args.resurrect_id

    # Check if story exists in main directory first
    existing = load_story(project_dir, story_id)
    if existing:
        print(f"Story {story_id} is not abandoned (status: {existing.status})")
        return 1

    story = resurrect_story(project_dir, story_id)
    if not story:
        print(f"Story not found: {story_id}")
        print("  Check 'wf archive stories' for available abandoned stories")
        return 1

    print(f"Resurrected {story_id}: {story.title}")

    # Re-annotate REQS
    print("Re-annotating REQS.md...")
    success, msg = annotate_reqs_for_story(story, project_config, project_dir=project_dir)
    if success:
        if len(msg) > 80:
            truncated = msg[:80].rsplit(' ', 1)[0]
            print(f"  {truncated}...")
        else:
            print(f"  {msg}")
    else:
        print(f"  Warning: {msg}")

    print(f"\nTo edit: wf plan edit {story_id}")
    return 0


def cmd_plan_edit(args, ops_dir: Path, project_config: ProjectConfig, story_id: str):
    """Edit an existing story (if unlocked)."""
    project_dir = ops_dir / "projects" / project_config.name

    story = load_story(project_dir, story_id)
    if not story:
        print(f"Story not found: {story_id}")
        return 1

    # Check if locked
    if is_story_locked(story):
        print(f"Story is locked (status: {story.status})")
        if story.workstream:
            print(f"Implementing via workstream: {story.workstream}")
        print()
        print("Options:")
        print(f"  wf plan clone {story_id}    # create editable copy")
        if story.workstream:
            print(f"  wf close {story.workstream}         # cancel implementation, unlocks story")
        return 1

    # Handle feedback flag
    feedback = getattr(args, 'feedback', None)
    if feedback:
        print(f"Refining {story_id} with feedback...")
        success, updated_data, message, reasoning = run_edit_session(
            story, feedback, project_config, ops_dir, project_dir
        )

        # Show Claude PM's reasoning if any
        if reasoning:
            print()
            print(reasoning)
            print()
            print("-" * 60)

        if not success:
            print(f"Error: {message}")
            return 1

        # Update the story with new data
        updated_story = update_story(project_dir, story_id, updated_data)
        if not updated_story:
            print(f"Failed to update story")
            return 1

        print(f"Updated {story_id}: {updated_story.title}")
        if updated_story.open_questions:
            print(f"\nRemaining open questions: {len(updated_story.open_questions)}")
            for i, q in enumerate(updated_story.open_questions, 1):
                print(f"  {i}. {q}")
        else:
            print("\nNo remaining open questions.")
        return 0

    # No feedback - show the story and hint to edit the markdown
    story_path = project_dir / "pm" / "stories" / f"{story_id}.md"
    print(f"Story: {story_id}")
    print(f"Title: {story.title}")
    print(f"Status: {story.status}")
    print()
    print(f"Edit: {story_path}")
    print()
    print("Open questions to resolve:")
    for i, q in enumerate(story.open_questions, 1):
        print(f"  {i}. {q}")

    if not story.open_questions:
        print("  (none)")

    print()
    print("Tip: Use -f to provide feedback inline:")
    print(f"  wf plan edit {story_id} -f \"your feedback here\"")

    return 0


def cmd_plan_add(args, ops_dir: Path, project_config: ProjectConfig):
    """Add a micro-commit to an existing workstream's plan.md."""
    ws_id = args.ws_id
    instruction = getattr(args, 'title', None)
    feedback = getattr(args, 'feedback', '') or ''

    # If no title but feedback provided, use feedback as the instruction
    if not instruction and feedback:
        instruction = feedback
    elif not instruction:
        print("ERROR: Provide an instruction")
        print("  wf plan add <ws_id> \"instruction\"")
        print("  wf plan add <ws_id> -f \"instruction\"")
        return 1

    # Validate workstream exists
    workstream_dir = ops_dir / "workstreams" / ws_id
    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 1

    plan_path = workstream_dir / "plan.md"
    if not plan_path.exists():
        print(f"ERROR: No plan.md found for workstream '{ws_id}'")
        return 1

    # Load workstream to get worktree path
    ws = load_workstream(workstream_dir)

    # Parse existing commits to find next number
    commits = parse_plan(str(plan_path))
    if not commits:
        print(f"ERROR: No existing commits in plan.md - cannot determine prefix")
        print("Use 'wf run' first to generate initial commits.")
        return 1

    # Extract WS prefix from existing commit ID
    first_id = commits[0].id
    parts = first_id.split('-')
    if len(parts) < 3:
        print(f"ERROR: Cannot parse commit ID format: {first_id}")
        return 1
    ws_prefix = '-'.join(parts[1:-1])

    # Find max commit number
    max_num = 0
    for c in commits:
        c_parts = c.id.split('-')
        if len(c_parts) >= 3:
            try:
                num = int(c_parts[-1])
                max_num = max(max_num, num)
            except ValueError:
                pass

    next_num = max_num + 1
    commit_id = f"COMMIT-{ws_prefix}-{next_num:03d}"

    # Build suggestions section from current context only
    # User feedback takes precedence, otherwise load from final_review.md if at final review stage
    suggestions_section = ""
    if feedback:
        # User provided explicit feedback - use it directly as the suggestions
        suggestions_section = f"## Feedback to Address\n{feedback}"
    else:
        # Check if at final review stage (all commits done)
        all_done = all(c.done for c in commits)
        if all_done:
            # Load concerns from final_review.md if it exists and has concerns
            final_review_path = workstream_dir / "final_review.md"
            if final_review_path.exists():
                concerns = extract_final_review_concerns(final_review_path)
                if concerns:
                    suggestions_section = f"## Final Review Concerns to Address\n{concerns}"

    # Build prompt
    plan_content = plan_path.read_text()
    prompt = render_prompt(
        "plan_add",
        instruction=instruction,
        commit_id=commit_id,
        suggestions_section=suggestions_section,
        plan_content=plan_content,
    )

    print(f"Generating commit spec for: {instruction}")

    # Load agent config
    project_dir = ops_dir / "projects" / project_config.name
    agents_config = load_agents_config(project_dir)
    stage_cmd = get_stage_command(agents_config, "plan_add", {"prompt": prompt})
    cmd = stage_cmd.cmd

    stdin_input = stage_cmd.get_stdin_input(prompt)

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(ws.worktree),
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except subprocess.TimeoutExpired:
        print("ERROR: Claude timed out")
        return 1

    if result.returncode != 0:
        print(f"ERROR: Claude failed: {result.stderr}")
        return 1

    # Parse response
    try:
        wrapper = json.loads(result.stdout.strip())
        response_text = wrapper.get("result", result.stdout)

        # extract_json_with_preamble returns (preamble, json_str)
        _, json_str = extract_json_with_preamble(response_text)
        if json_str:
            commit_data = json.loads(json_str)
        else:
            # Try parsing response_text directly as JSON
            commit_data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError) as e:
        print(f"ERROR: Failed to parse response: {e}")
        print(f"Raw output: {result.stdout[:500]}")
        return 1

    # Validate commit data
    if not isinstance(commit_data, dict) or 'title' not in commit_data:
        print(f"ERROR: Invalid commit data: {commit_data}")
        return 1

    # Append the new commit
    new_commit = {
        'id': commit_id,
        'title': commit_data.get('title', instruction),
        'description': commit_data.get('description', ''),
    }
    append_commits_to_plan(plan_path, [new_commit])

    print(f"\nAdded {commit_id}: {new_commit['title']}")
    print(f"\nDescription:\n{new_commit['description'][:200]}..." if len(new_commit['description']) > 200 else f"\nDescription:\n{new_commit['description']}")
    print(f"\nTo implement: wf run {ws_id}")
    return 0
