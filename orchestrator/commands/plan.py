"""
wf plan command - Unified story planning.

Commands:
  wf plan                  - Discovery from REQS.md, interactive session
  wf plan new ["title"]    - Ad-hoc story creation
  wf plan clone STORY-xxx  - Clone a locked story
  wf plan STORY-xxx        - Edit existing story (if unlocked)
"""

import sys
from pathlib import Path

from orchestrator.lib.config import ProjectConfig
from orchestrator.lib.planparse import parse_plan
from orchestrator.pm.stories import (
    list_stories,
    load_story,
    clone_story,
    create_story,
    update_story,
    is_story_locked,
)
from orchestrator.pm.planner import run_plan_session, run_refine_session, run_edit_session
from orchestrator.runner.impl.breakdown import append_commits_to_plan


def cmd_plan(args, ops_dir: Path, project_config: ProjectConfig):
    """Main entry point for wf plan."""
    # wf plan new
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

    # wf plan (discovery from REQS.md)
    return cmd_plan_discover(args, ops_dir, project_config)


def cmd_plan_discover(args, ops_dir: Path, project_config: ProjectConfig):
    """Discovery mode: analyze REQS.md and propose stories."""
    project_dir = ops_dir / "projects" / project_config.name

    # Check for REQS.md
    reqs_path = Path(project_config.repo_path) / "REQS.md"
    if not reqs_path.exists():
        print(f"No REQS.md found at {reqs_path}")
        print("Use 'wf plan new' to create ad-hoc stories.")
        return 1

    print("Starting planning session...")
    print(f"Reading: {reqs_path}")
    print()

    success, response = run_plan_session(project_config, ops_dir, project_dir)
    print(response)
    return 0 if success else 1


def cmd_plan_new(args, ops_dir: Path, project_config: ProjectConfig):
    """Create an ad-hoc story not from REQS.md."""
    project_dir = ops_dir / "projects" / project_config.name
    title = getattr(args, 'title', None)

    if title:
        print(f"Creating story: {title}")
    else:
        print("Creating new story...")

    success, story_data, message = run_refine_session(title or "", project_config, ops_dir, project_dir)

    if not success:
        print(message)
        return 1

    # Create the story
    story = create_story(project_dir, story_data)
    print(f"Created {story.id}: {story.title}")
    print(f"\nTo start implementation: wf open {story.id}")
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
    title = args.title
    description = getattr(args, 'description', '') or ''

    # Validate workstream exists
    workstream_dir = ops_dir / "workstreams" / ws_id
    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 1

    plan_path = workstream_dir / "plan.md"

    if not plan_path.exists():
        print(f"ERROR: No plan.md found for workstream '{ws_id}'")
        return 1

    # Parse existing commits to find next number
    commits = parse_plan(str(plan_path))
    if not commits:
        print(f"ERROR: No existing commits in plan.md - cannot determine prefix")
        print("Use 'wf run' first to generate initial commits.")
        return 1

    # Extract WS prefix from existing commit ID (e.g., BUILD_TASKFILE from COMMIT-BUILD_TASKFILE-001)
    first_id = commits[0].id  # e.g., "COMMIT-BUILD_TASKFILE-001"
    parts = first_id.split('-')
    if len(parts) < 3:
        print(f"ERROR: Cannot parse commit ID format: {first_id}")
        return 1

    # Prefix is everything between COMMIT- and -NNN
    ws_prefix = '-'.join(parts[1:-1])  # e.g., "BUILD_TASKFILE"

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

    # Append the new commit
    new_commit = {
        'id': commit_id,
        'title': title,
        'description': description,
    }
    append_commits_to_plan(plan_path, [new_commit])

    print(f"Added {commit_id}: {title}")
    print(f"\nTo implement: wf run {ws_id}")
    return 0
