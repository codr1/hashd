"""
Directives loading for WF.

Loads directives from three levels:
- Global: ~/.config/wf/directives.md
- Project: {repo}/WF_DIRECTIVES.md
- Feature: {workstream_dir}/directives.md (optional)
"""

from pathlib import Path
from typing import Optional


GLOBAL_DIRECTIVES_PATH = Path.home() / ".config" / "wf" / "directives.md"
PROJECT_DIRECTIVES_FILENAME = "WF_DIRECTIVES.md"
FEATURE_DIRECTIVES_FILENAME = "directives.md"


def load_global_directives() -> Optional[str]:
    """Load global directives from ~/.config/wf/directives.md."""
    if GLOBAL_DIRECTIVES_PATH.exists():
        content = GLOBAL_DIRECTIVES_PATH.read_text().strip()
        if content:
            return content
    return None


def load_project_directives(repo_path: Path) -> Optional[str]:
    """Load project directives from {repo}/WF_DIRECTIVES.md."""
    project_file = repo_path / PROJECT_DIRECTIVES_FILENAME
    if project_file.exists():
        content = project_file.read_text().strip()
        if content:
            return content
    return None


def load_feature_directives(workstream_dir: Path) -> Optional[str]:
    """Load feature directives from workstream directory."""
    if workstream_dir is None:
        return None
    feature_file = workstream_dir / FEATURE_DIRECTIVES_FILENAME
    if feature_file.exists():
        content = feature_file.read_text().strip()
        if content:
            return content
    return None


def load_directives(repo_path: Path, workstream_dir: Path = None) -> str:
    """Load all directives and format for prompt inclusion.

    Returns empty string if no directives found.
    """
    sections = []

    global_content = load_global_directives()
    if global_content:
        sections.append(f"### Global Directives\n\n{global_content}")

    project_content = load_project_directives(repo_path)
    if project_content:
        sections.append(f"### Project Directives\n\n{project_content}")

    feature_content = load_feature_directives(workstream_dir)
    if feature_content:
        sections.append(f"### Feature Directives\n\n{feature_content}")

    if not sections:
        return ""

    return "\n\n".join(sections)


def format_directives_display(
    repo_path: Path = None,
    workstream_dir: Path = None,
    show_global: bool = True,
    show_project: bool = True,
    show_feature: bool = True,
) -> str:
    """Format directives for CLI display.

    Returns formatted string showing directives at each level.
    """
    lines = []

    if show_global:
        lines.append(f"Global ({GLOBAL_DIRECTIVES_PATH}):")
        global_content = load_global_directives()
        if global_content:
            for line in global_content.splitlines():
                lines.append(f"  {line}")
        else:
            lines.append("  (none)")
        lines.append("")

    if show_project and repo_path:
        lines.append(f"Project ({PROJECT_DIRECTIVES_FILENAME}):")
        project_content = load_project_directives(repo_path)
        if project_content:
            for line in project_content.splitlines():
                lines.append(f"  {line}")
        else:
            lines.append("  (none)")
        lines.append("")

    if show_feature and workstream_dir:
        lines.append(f"Feature ({workstream_dir.name}/{FEATURE_DIRECTIVES_FILENAME}):")
        feature_content = load_feature_directives(workstream_dir)
        if feature_content:
            for line in feature_content.splitlines():
                lines.append(f"  {line}")
        else:
            lines.append("  (none)")

    return "\n".join(lines)
