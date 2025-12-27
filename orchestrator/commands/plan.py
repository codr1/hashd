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
from orchestrator.pm.stories import (
    list_stories,
    load_story,
    clone_story,
    is_story_locked,
)
from orchestrator.pm.planner import run_planning_session, run_refine_session


def cmd_plan(args, ops_dir: Path, project_config: ProjectConfig):
    """Main entry point for wf plan."""
    # wf plan new
    if getattr(args, 'new', False):
        return cmd_plan_new(args, ops_dir, project_config)

    # wf plan clone STORY-xxx
    if getattr(args, 'clone', False):
        return cmd_plan_clone(args, ops_dir, project_config)

    # wf plan STORY-xxx (edit existing)
    story_id = getattr(args, 'story_id', None)
    if story_id and story_id.startswith('STORY-'):
        return cmd_plan_edit(args, ops_dir, project_config, story_id)

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

    return run_planning_session(project_dir, project_config)


def cmd_plan_new(args, ops_dir: Path, project_config: ProjectConfig):
    """Create an ad-hoc story not from REQS.md."""
    project_dir = ops_dir / "projects" / project_config.name
    title = getattr(args, 'title', None)

    if title:
        print(f"Creating story: {title}")
    else:
        print("Creating new story...")

    return run_refine_session(project_dir, project_config, title or "")


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

    # TODO: Interactive editing session
    # For now, just show the story and hint to edit the markdown
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

    return 0
