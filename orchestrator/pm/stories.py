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


def write_story_markdown(path: Path, story: Story):
    """Write story as human-readable markdown."""
    lines = [
        f"# {story.id}: {story.title}",
        "",
        f"**Status:** {story.status}",
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
