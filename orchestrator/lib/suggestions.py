"""
Suggestions CRUD for PM discovery output.

Suggestions are stored in:
  projects/<project>/suggestions.json
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Suggestion:
    """A story suggestion from PM discovery."""
    id: int
    title: str
    summary: str
    rationale: str
    reqs_refs: list[str] = field(default_factory=list)
    status: str = "available"  # available, in_progress, done
    story_id: Optional[str] = None


@dataclass
class SuggestionsFile:
    """Container for suggestions with metadata."""
    generated_at: str
    suggestions: list[Suggestion] = field(default_factory=list)


def get_suggestions_path(project_dir: Path) -> Path:
    """Get path to suggestions.json for a project."""
    return project_dir / "suggestions.json"


def load_suggestions(project_dir: Path) -> Optional[SuggestionsFile]:
    """Load suggestions from file.

    Returns None if file doesn't exist.
    """
    path = get_suggestions_path(project_dir)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())
        suggestions = [
            Suggestion(**s) for s in data.get("suggestions", [])
        ]
        return SuggestionsFile(
            generated_at=data.get("generated_at", ""),
            suggestions=suggestions,
        )
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Failed to load suggestions: {e}")
        return None


def save_suggestions(project_dir: Path, suggestions_file: SuggestionsFile) -> bool:
    """Save suggestions to file.

    Returns True on success.
    """
    path = get_suggestions_path(project_dir)

    try:
        data = {
            "generated_at": suggestions_file.generated_at,
            "suggestions": [asdict(s) for s in suggestions_file.suggestions],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        return True
    except OSError as e:
        logger.error(f"Failed to save suggestions: {e}")
        return False


def rotate_suggestions(project_dir: Path) -> Optional[Path]:
    """Rotate current suggestions file before overwriting.

    Renames the existing file with a timestamp suffix.
    Returns path to rotated file, or None if no file to rotate.
    """
    path = get_suggestions_path(project_dir)
    if not path.exists():
        return None

    # Load to get timestamp
    data = load_suggestions(project_dir)
    if not data:
        return None

    # Create archive filename with timestamp
    timestamp = data.generated_at.replace(":", "-").replace("T", "_")[:19]
    archive_path = path.parent / f"suggestions.{timestamp}.json"

    try:
        path.rename(archive_path)
        return archive_path
    except OSError as e:
        logger.warning(f"Failed to archive suggestions: {e}")
        return None


def create_suggestions_from_discovery(suggestions_data: list[dict]) -> SuggestionsFile:
    """Create a new SuggestionsFile from PM discovery output.

    Args:
        suggestions_data: List of suggestion dicts from Claude

    Returns:
        SuggestionsFile ready to save
    """
    suggestions = []
    for i, s in enumerate(suggestions_data, start=1):
        suggestions.append(Suggestion(
            id=i,
            title=s.get("title", f"Suggestion {i}"),
            summary=s.get("summary", ""),
            rationale=s.get("rationale", ""),
            reqs_refs=s.get("reqs_refs", []),
            status="available",
            story_id=None,
        ))

    return SuggestionsFile(
        generated_at=datetime.now().isoformat(),
        suggestions=suggestions,
    )


def get_suggestion_by_id(suggestions_file: SuggestionsFile, suggestion_id: int) -> Optional[Suggestion]:
    """Get a suggestion by its numeric ID."""
    for s in suggestions_file.suggestions:
        if s.id == suggestion_id:
            return s
    return None


def get_suggestion_by_name(suggestions_file: SuggestionsFile, name: str) -> Optional[Suggestion]:
    """Get a suggestion by matching its title (case-insensitive substring match)."""
    name_lower = name.lower()
    for s in suggestions_file.suggestions:
        if name_lower in s.title.lower():
            return s
    return None


def mark_suggestion_in_progress(
    project_dir: Path,
    suggestion_id: int,
    story_id: str,
) -> bool:
    """Mark a suggestion as in_progress and link to story.

    Returns True on success.
    """
    suggestions_file = load_suggestions(project_dir)
    if not suggestions_file:
        return False

    suggestion = get_suggestion_by_id(suggestions_file, suggestion_id)
    if not suggestion:
        return False

    suggestion.status = "in_progress"
    suggestion.story_id = story_id

    return save_suggestions(project_dir, suggestions_file)


def mark_suggestion_done(project_dir: Path, suggestion_id: int) -> bool:
    """Mark a suggestion as done.

    Returns True on success.
    """
    suggestions_file = load_suggestions(project_dir)
    if not suggestions_file:
        return False

    suggestion = get_suggestion_by_id(suggestions_file, suggestion_id)
    if not suggestion:
        return False

    suggestion.status = "done"

    return save_suggestions(project_dir, suggestions_file)


