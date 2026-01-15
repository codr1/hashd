"""
REQS.md annotation utilities.

Annotates REQS.md with WIP markers when stories are created,
and removes annotations when stories are abandoned or merged.
"""

import logging
import re
from pathlib import Path
from typing import Optional

from orchestrator.lib.config import ProjectConfig
from orchestrator.pm.claude_utils import run_claude
from orchestrator.pm.models import Story

logger = logging.getLogger(__name__)


def annotate_reqs_for_story(
    story: Story,
    project_config: ProjectConfig,
    timeout: int = 180,
) -> tuple[bool, str]:
    """
    Use Claude Code to annotate REQS.md with story WIP markers.

    Claude reads REQS.md and wraps the sentences/paragraphs that this story
    addresses with HTML comment markers:

        <!-- BEGIN WIP: STORY-0004 -->
        Users should be able to export their data...
        <!-- END WIP -->

    Args:
        story: The newly created Story object
        project_config: Project configuration with repo_path and reqs_path
        timeout: Timeout in seconds

    Returns:
        Tuple of (success, message)
    """
    reqs_path = project_config.repo_path / project_config.reqs_path

    if not reqs_path.exists():
        return True, "No REQS.md to annotate"

    # Build acceptance criteria as bullet list
    ac_list = "\n".join(f"- {ac}" for ac in story.acceptance_criteria)

    prompt = f"""You need to annotate REQS.md to mark which requirements are being worked on.

Story just created: {story.id}: {story.title}

Problem this story solves:
{story.problem}

Acceptance criteria:
{ac_list}

Your task:
1. Read {project_config.reqs_path}
2. Find sentences or paragraphs that are DIRECTLY addressed by this story
3. Wrap each relevant section with these markers:

<!-- BEGIN WIP: {story.id} -->
...the requirement text...
<!-- END WIP -->

Rules:
- Only wrap text that is DIRECTLY covered by this story's acceptance criteria
- One story may cover multiple scattered sections - wrap each separately
- Do NOT wrap headings or section titles
- Do NOT wrap text that is only tangentially related
- If a section is already wrapped with another story's WIP marker, leave it alone
- Use the Edit tool to make changes to {project_config.reqs_path}

After editing, respond with a brief summary of what you annotated.
"""

    success, response = run_claude(prompt, cwd=project_config.repo_path, timeout=timeout, accept_edits=True)

    if not success:
        logger.warning(f"REQS annotation failed: {response}")
        return False, f"Annotation failed: {response}"

    return True, response


def remove_reqs_annotations(
    story_id: str,
    project_config: ProjectConfig,
) -> tuple[bool, str]:
    """
    Remove WIP annotations for a story from REQS.md.

    Used when a story is abandoned - unwraps the markers but keeps the text.

    Args:
        story_id: The story ID (e.g., "STORY-0004")
        project_config: Project configuration

    Returns:
        Tuple of (success, message)
    """
    reqs_path = project_config.repo_path / project_config.reqs_path

    if not reqs_path.exists():
        return True, "No REQS.md to clean"

    content = reqs_path.read_text()

    # Remove BEGIN/END markers for this story, keeping the content between
    # Trailing \n? ensures we don't leave extra blank lines
    pattern = rf'<!-- BEGIN WIP: {re.escape(story_id)} -->\n?(.*?)\n?<!-- END WIP -->\n?'

    new_content, count = re.subn(pattern, r'\1', content, flags=re.DOTALL)

    if count == 0:
        return True, f"No annotations found for {story_id}"

    try:
        reqs_path.write_text(new_content)
    except OSError as e:
        return False, f"Failed to write REQS.md: {e}"

    return True, f"Removed {count} annotation(s) for {story_id}"


def delete_reqs_sections(
    story_id: str,
    project_config: ProjectConfig,
    base_path: Optional[Path] = None,
) -> tuple[bool, str, str]:
    """
    Delete annotated sections for a story from REQS.md.

    Used when a story is merged - extracts the content and deletes the blocks.

    Args:
        story_id: The story ID
        project_config: Project configuration
        base_path: Optional base path override (e.g., worktree path)

    Returns:
        Tuple of (success, message, extracted_content)
    """
    base = base_path or project_config.repo_path
    reqs_path = base / project_config.reqs_path

    if not reqs_path.exists():
        return True, "No REQS.md", ""

    content = reqs_path.read_text()

    pattern = rf'<!-- BEGIN WIP: {re.escape(story_id)} -->\n?(.*?)\n?<!-- END WIP -->\n?'

    # Extract all matched content
    matches = re.findall(pattern, content, flags=re.DOTALL)

    if not matches:
        return True, f"No annotations found for {story_id}", ""

    extracted = "\n\n".join(m.strip() for m in matches)

    # Remove the blocks entirely
    new_content = re.sub(pattern, '', content, flags=re.DOTALL)

    try:
        reqs_path.write_text(new_content)
    except OSError as e:
        return False, f"Failed to write REQS.md: {e}", ""

    return True, f"Deleted {len(matches)} section(s) for {story_id}", extracted
