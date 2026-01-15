"""
wf pm - Project Management commands.

Commands for story sifting from dirty requirements and SPEC generation.
"""

from pathlib import Path

from orchestrator.lib.config import ProjectConfig
from orchestrator.pm.planner import run_plan_session, run_refine_session
from orchestrator.pm.spec import run_spec_update
from orchestrator.pm.stories import (
    create_story,
    list_stories,
    load_story,
)


def cmd_pm_plan(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Start interactive planning session with Claude.

    Reads REQS.md, SPEC.md, and active workstreams to propose
    next logical chunks to build.
    """
    project_dir = ops_dir / "projects" / project_config.name

    print(f"Planning session for: {project_config.name}")
    print("=" * 60)
    print()
    print("Reading requirements, SPEC, and active workstreams...")
    print("Asking Claude to propose next chunks to build...")
    print()

    success, response = run_plan_session(
        project_config=project_config,
        ops_dir=ops_dir,
        project_dir=project_dir,
    )

    if not success:
        print(f"ERROR: {response}")
        return 1

    print(response)
    print()
    print("-" * 60)
    print("To refine a chunk into a story: wf pm refine <chunk-name>")

    return 0


def cmd_pm_refine(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Create a story from a chunk identified in planning.

    Takes a chunk name/description and creates STORY-xxxx.
    """
    chunk_name = args.name
    project_dir = ops_dir / "projects" / project_config.name

    print(f"Refining chunk: {chunk_name}")
    print("=" * 60)
    print()
    print("Asking Claude to refine this into a proper story...")
    print()

    success, story_data, message = run_refine_session(
        chunk_name=chunk_name,
        project_config=project_config,
        ops_dir=ops_dir,
        project_dir=project_dir,
    )

    if not success:
        print(f"ERROR: {message}")
        return 1

    # Create the story
    story = create_story(project_dir, story_data)

    print(f"Created: {story.id}")
    print(f"Title:   {story.title}")
    if story.suggested_ws_id:
        print(f"Suggested workstream ID: {story.suggested_ws_id}")
    print()
    print("Source References:")
    print(f"  {story.source_refs}")
    print()
    print("Problem:")
    print(f"  {story.problem}")
    print()
    print("Acceptance Criteria:")
    for ac in story.acceptance_criteria:
        print(f"  - {ac}")
    print()

    if story.non_goals:
        print("Non-Goals:")
        for ng in story.non_goals:
            print(f"  - {ng}")
        print()

    if story.dependencies:
        print("Dependencies:")
        for dep in story.dependencies:
            print(f"  - {dep}")
        print()

    if story.open_questions:
        print("Open Questions:")
        for q in story.open_questions:
            print(f"  ? {q}")
        print()

    print("-" * 60)
    print(f"Story saved to: projects/{project_config.name}/pm/stories/{story.id}.md")
    print()
    print("Next steps:")
    print(f"  wf pm show {story.id}     # View full details")
    if story.suggested_ws_id:
        print(f"  wf new --stories {story.id}  # Create workstream (uses ID: {story.suggested_ws_id})")
    else:
        print(f"  wf new <id> --stories {story.id}  # Create workstream")

    return 0


def cmd_pm_spec(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Update SPEC.md based on completed workstream.

    Asks Claude to update SPEC to reflect what was implemented.
    """
    workstream_id = args.workstream
    project_dir = ops_dir / "projects" / project_config.name

    print(f"Updating SPEC for: {project_config.name}")
    print("=" * 60)
    print()
    print(f"Workstream: {workstream_id}")
    print()
    print("Reading current SPEC and workstream context...")
    print("Asking Claude to update SPEC (thorough and succinct)...")
    print()

    success, result = run_spec_update(
        workstream_id=workstream_id,
        project_config=project_config,
        ops_dir=ops_dir,
        project_dir=project_dir,
    )

    if not success:
        print(f"ERROR: {result}")
        return 1

    print(f"SPEC updated: {result}")
    print()
    print("View with: cat " + result)

    return 0


def cmd_pm_status(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Show PM status: what's built, in flight, and pending."""
    project_dir = ops_dir / "projects" / project_config.name

    print(f"PM Status for: {project_config.name}")
    print("=" * 60)
    print()

    # Check for SPEC.md
    spec_path = project_dir / "SPEC.md"
    if spec_path.exists():
        # Count lines as rough metric
        line_count = len(spec_path.read_text().splitlines())
        print(f"SPEC.md: {line_count} lines")
    else:
        print("SPEC.md: Not yet created")

    print()

    # List stories by status
    stories = list_stories(project_dir)

    draft_count = len([s for s in stories if s.status == "draft"])
    accepted_count = len([s for s in stories if s.status == "accepted"])
    implemented_count = len([s for s in stories if s.status == "implemented"])

    print(f"Stories:")
    print(f"  Draft:       {draft_count}")
    print(f"  Accepted:    {accepted_count}")
    print(f"  Implemented: {implemented_count}")

    if not stories:
        print()
        print("No stories yet. Run 'wf pm plan' to start planning.")

    return 0


def cmd_pm_list(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """List all stories for the project."""
    project_dir = ops_dir / "projects" / project_config.name

    stories = list_stories(project_dir)

    if not stories:
        print("No stories found")
        print()
        print("Run 'wf pm plan' to start planning.")
        return 0

    print(f"Stories for: {project_config.name}")
    print()
    print(f"{'ID':<14} {'STATUS':<14} TITLE")
    print("-" * 70)

    for story in stories:
        title_preview = story.title[:40] + "..." if len(story.title) > 40 else story.title
        print(f"{story.id:<14} {story.status:<14} {title_preview}")

    print("-" * 70)
    print(f"{len(stories)} {'story' if len(stories) == 1 else 'stories'}")
    print()
    print("Use 'wf pm show <id>' to view details")

    return 0


def cmd_pm_show(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Show full details of a story."""
    story_id = args.story
    project_dir = ops_dir / "projects" / project_config.name

    story = load_story(project_dir, story_id)

    if not story:
        print(f"ERROR: Story '{story_id}' not found")
        return 2

    print(f"Story: {story.id}")
    print("=" * 60)
    print(f"Title:   {story.title}")
    print(f"Status:  {story.status}")
    print(f"Created: {story.created}")

    if story.suggested_ws_id:
        print(f"Suggested workstream ID: {story.suggested_ws_id}")

    if story.workstream:
        print(f"Workstream: {story.workstream}")
    if story.implemented_at:
        print(f"Implemented: {story.implemented_at}")

    print()

    if story.source_refs:
        print("Source References")
        print("-" * 40)
        print(story.source_refs)
        print()

    if story.problem:
        print("Problem")
        print("-" * 40)
        print(story.problem)
        print()

    if story.acceptance_criteria:
        print("Acceptance Criteria")
        print("-" * 40)
        for ac in story.acceptance_criteria:
            print(f"  [ ] {ac}")
        print()

    if story.non_goals:
        print("Non-Goals")
        print("-" * 40)
        for ng in story.non_goals:
            print(f"  - {ng}")
        print()

    if story.dependencies:
        print("Dependencies")
        print("-" * 40)
        for dep in story.dependencies:
            print(f"  - {dep}")
        print()

    if story.open_questions:
        print("Open Questions")
        print("-" * 40)
        for q in story.open_questions:
            print(f"  ? {q}")
        print()

    return 0
