"""
Story CRUD operations for PM module.

Stories are stored as JSON + markdown pairs in:
  projects/<project>/pm/stories/STORY-xxxx.json
  projects/<project>/pm/stories/STORY-xxxx.md
"""

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.lib.validate import validate_before_write
from orchestrator.pm.models import Story

logger = logging.getLogger(__name__)


def get_pm_dir(project_dir: Path) -> Path:
    """Get PM directory for a project."""
    return project_dir / "pm"


def get_stories_dir(project_dir: Path) -> Path:
    """Get stories directory for a project."""
    return get_pm_dir(project_dir) / "stories"


def generate_story_id(project_dir: Path) -> str:
    """Generate next story ID for project."""
    stories_dir = get_stories_dir(project_dir)

    existing = []
    if stories_dir.exists():
        existing = [f.stem for f in stories_dir.glob("STORY-*.json")]

    if not existing:
        return "STORY-0001"

    nums = []
    for x in existing:
        if x.startswith("STORY-"):
            try:
                nums.append(int(x.split("-")[1]))
            except (ValueError, IndexError):
                logger.warning(f"Malformed story ID ignored: {x}")

    if not nums:
        return "STORY-0001"
    return f"STORY-{max(nums) + 1:04d}"


def create_story(project_dir: Path, data: dict) -> Story:
    """Create a new story.

    Args:
        project_dir: Project directory (projects/<project>/)
        data: Dict with title, source_refs, problem, etc.

    Returns:
        Created Story object
    """
    story_id = generate_story_id(project_dir)

    story = Story(
        id=story_id,
        title=data["title"],
        status="draft",
        created=datetime.now().isoformat(),
        source_refs=data.get("source_refs", ""),
        problem=data.get("problem", ""),
        acceptance_criteria=data.get("acceptance_criteria", []),
        non_goals=data.get("non_goals", []),
        dependencies=data.get("dependencies", []),
        open_questions=data.get("open_questions", []),
        suggested_ws_id=data.get("suggested_ws_id", ""),
    )

    stories_dir = get_stories_dir(project_dir)
    stories_dir.mkdir(parents=True, exist_ok=True)

    # Validate before writing
    story_dict = asdict(story)
    json_path = stories_dir / f"{story_id}.json"
    validate_before_write(story_dict, "story", json_path)

    # Write JSON
    json_path.write_text(json.dumps(story_dict, indent=2))

    # Write markdown for human readability
    write_story_markdown(stories_dir / f"{story_id}.md", story)

    return story


def load_story(project_dir: Path, story_id: str) -> Optional[Story]:
    """Load a story by ID."""
    stories_dir = get_stories_dir(project_dir)
    path = stories_dir / f"{story_id}.json"

    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())
        return Story(**data)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Failed to load story {story_id}: {e}")
        return None


def list_stories(project_dir: Path) -> list[Story]:
    """List all stories for a project."""
    stories_dir = get_stories_dir(project_dir)
    if not stories_dir.exists():
        return []

    stories = []
    for f in sorted(stories_dir.glob("STORY-*.json")):
        try:
            data = json.loads(f.read_text())
            stories.append(Story(**data))
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to load story file {f}: {e}")

    return stories


def get_stories_by_status(project_dir: Path, status: str) -> list[Story]:
    """Get stories filtered by status."""
    return [s for s in list_stories(project_dir) if s.status == status]


def update_story(project_dir: Path, story_id: str, updates: dict) -> Optional[Story]:
    """Update a story with new values.

    Args:
        project_dir: Project directory
        story_id: Story ID
        updates: Dict of fields to update

    Returns:
        Updated Story or None if not found
    """
    story = load_story(project_dir, story_id)
    if not story:
        return None

    # Apply updates
    story_dict = asdict(story)
    story_dict.update(updates)
    updated = Story(**story_dict)

    # Validate and write back
    stories_dir = get_stories_dir(project_dir)
    updated_dict = asdict(updated)
    json_path = stories_dir / f"{story_id}.json"
    validate_before_write(updated_dict, "story", json_path)

    json_path.write_text(json.dumps(updated_dict, indent=2))
    write_story_markdown(stories_dir / f"{story_id}.md", updated)

    return updated


def is_story_locked(story: Story) -> bool:
    """Check if a story is locked (cannot be edited).

    Stories are locked when:
    - status is 'implementing' (workstream in progress)
    - status is 'implemented' (workstream completed)
    """
    return story.status in ("implementing", "implemented")


def lock_story(project_dir: Path, story_id: str, workstream_id: str) -> Optional[Story]:
    """Lock a story when implementation starts.

    Sets status to 'implementing' and links the workstream.

    Args:
        project_dir: Project directory
        story_id: Story ID to lock
        workstream_id: Workstream ID being created

    Returns:
        Updated Story or None if not found or already locked
    """
    story = load_story(project_dir, story_id)
    if not story:
        return None

    if is_story_locked(story):
        logger.warning(f"Cannot lock {story_id}: already locked (status={story.status})")
        return None

    return update_story(project_dir, story_id, {
        "status": "implementing",
        "workstream": workstream_id,
    })


def unlock_story(project_dir: Path, story_id: str) -> Optional[Story]:
    """Unlock a story when implementation is cancelled.

    Sets status back to 'accepted' (or 'draft' if never accepted).

    Args:
        project_dir: Project directory
        story_id: Story ID to unlock

    Returns:
        Updated Story or None if not found
    """
    story = load_story(project_dir, story_id)
    if not story:
        return None

    if story.status != "implementing":
        logger.warning(f"Cannot unlock {story_id}: not in 'implementing' state (status={story.status})")
        return None

    # Go back to accepted (it was accepted before implementation started)
    return update_story(project_dir, story_id, {
        "status": "accepted",
        "workstream": None,
    })


def mark_story_implemented(project_dir: Path, story_id: str) -> Optional[Story]:
    """Mark a story as implemented when workstream merges.

    Args:
        project_dir: Project directory
        story_id: Story ID to mark

    Returns:
        Updated Story or None if not found
    """
    story = load_story(project_dir, story_id)
    if not story:
        return None

    return update_story(project_dir, story_id, {
        "status": "implemented",
        "implemented_at": datetime.now().isoformat(),
    })


def find_story_by_workstream(project_dir: Path, workstream_id: str) -> Optional[Story]:
    """Find a story by its linked workstream ID.

    Args:
        project_dir: Project directory
        workstream_id: Workstream ID to search for

    Returns:
        Story if found, None otherwise
    """
    for story in list_stories(project_dir):
        if story.workstream == workstream_id:
            return story
    return None


def archive_story(project_dir: Path, story_id: str) -> bool:
    """Archive a story to _implemented/ directory.

    Args:
        project_dir: Project directory
        story_id: Story ID to archive

    Returns:
        True if archived, False otherwise
    """
    stories_dir = get_stories_dir(project_dir)
    implemented_dir = stories_dir / "_implemented"

    json_path = stories_dir / f"{story_id}.json"
    md_path = stories_dir / f"{story_id}.md"

    if not json_path.exists():
        return False

    implemented_dir.mkdir(exist_ok=True)

    # Move JSON file
    json_path.rename(implemented_dir / json_path.name)

    # Move markdown file if exists
    if md_path.exists():
        md_path.rename(implemented_dir / md_path.name)

    return True


def clone_story(project_dir: Path, story_id: str) -> Optional[Story]:
    """Clone a story to create an editable copy.

    Useful when the original is locked (implementing/implemented).

    Args:
        project_dir: Project directory
        story_id: Story ID to clone

    Returns:
        New Story (clone) or None if original not found
    """
    original = load_story(project_dir, story_id)
    if not original:
        return None

    # Create clone with fresh ID, reset status
    clone_data = {
        "title": f"{original.title} (clone)",
        "source_refs": f"Cloned from {story_id}. Original refs: {original.source_refs}",
        "problem": original.problem,
        "acceptance_criteria": original.acceptance_criteria.copy(),
        "non_goals": original.non_goals.copy(),
        "dependencies": original.dependencies.copy(),
        "open_questions": original.open_questions.copy(),
        "suggested_ws_id": "",  # Clear suggested ID for clone
    }

    return create_story(project_dir, clone_data)


def accept_story(project_dir: Path, story_id: str) -> Optional[Story]:
    """Accept a story, marking it ready for implementation.

    Args:
        project_dir: Project directory
        story_id: Story ID to accept

    Returns:
        Updated Story or None if not found or not in draft
    """
    story = load_story(project_dir, story_id)
    if not story:
        return None

    if story.status != "draft":
        logger.warning(f"Cannot accept {story_id}: not in 'draft' state (status={story.status})")
        return None

    return update_story(project_dir, story_id, {
        "status": "accepted",
    })


def write_story_markdown(path: Path, story: Story):
    """Write story as human-readable markdown."""
    status_display = story.status
    if is_story_locked(story):
        status_display = f"{story.status} [LOCKED]"

    lines = [
        f"# {story.id}: {story.title}",
        "",
        f"**Status:** {status_display}",
        f"**Created:** {story.created}",
    ]

    if story.suggested_ws_id:
        lines.append(f"**Suggested Workstream ID:** {story.suggested_ws_id}")
    if story.workstream:
        lines.append(f"**Workstream:** {story.workstream}")
    if story.implemented_at:
        lines.append(f"**Implemented:** {story.implemented_at}")

    lines.append("")

    if story.source_refs:
        lines.extend([
            "## Source References",
            "",
            story.source_refs,
            "",
        ])

    if story.problem:
        lines.extend([
            "## Problem",
            "",
            story.problem,
            "",
        ])

    if story.acceptance_criteria:
        lines.extend([
            "## Acceptance Criteria",
            "",
        ])
        for ac in story.acceptance_criteria:
            lines.append(f"- [ ] {ac}")
        lines.append("")

    if story.non_goals:
        lines.extend([
            "## Non-Goals",
            "",
        ])
        for ng in story.non_goals:
            lines.append(f"- {ng}")
        lines.append("")

    if story.dependencies:
        lines.extend([
            "## Dependencies",
            "",
        ])
        for dep in story.dependencies:
            lines.append(f"- {dep}")
        lines.append("")

    if story.open_questions:
        lines.extend([
            "## Open Questions",
            "",
        ])
        for q in story.open_questions:
            lines.append(f"- {q}")
        lines.append("")

    path.write_text("\n".join(lines))
