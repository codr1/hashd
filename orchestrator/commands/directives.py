"""
wf directives - View and manage directives.

Directives are curated rules that guide AI implementation.
They exist at three levels:
- Global: ~/.config/wf/directives.md
- Project: {repo}/WF_DIRECTIVES.md
- Feature: workstreams/{id}/directives.md
"""

import os
import subprocess
from pathlib import Path

from orchestrator.lib.config import ProjectConfig
from orchestrator.lib.directives import (
    GLOBAL_DIRECTIVES_PATH,
    PROJECT_DIRECTIVES_FILENAME,
    FEATURE_DIRECTIVES_FILENAME,
    format_directives_display,
)


def cmd_directives(args, ops_dir: Path, project_config: ProjectConfig):
    """Show directives at all levels."""
    repo_path = Path(project_config.repo_path) if project_config.repo_path else None

    # Determine workstream dir if showing feature directives
    workstream_dir = None
    if hasattr(args, 'workstream') and args.workstream:
        workstream_dir = ops_dir / "workstreams" / args.workstream
        if not workstream_dir.exists():
            print(f"Warning: Workstream '{args.workstream}' not found")
            workstream_dir = None

    # Determine which levels to show
    show_global = not (args.project_only or args.feature_only)
    show_project = not (args.global_only or args.feature_only)
    show_feature = not (args.global_only or args.project_only) and workstream_dir is not None

    output = format_directives_display(
        repo_path=repo_path,
        workstream_dir=workstream_dir,
        show_global=show_global,
        show_project=show_project,
        show_feature=show_feature,
    )

    print(output)
    return 0


def cmd_directives_edit(args, ops_dir: Path, project_config: ProjectConfig):
    """Edit directives file in $EDITOR."""
    if args.level == "global":
        file_path = GLOBAL_DIRECTIVES_PATH
        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)
    elif args.level == "project":
        if not project_config.repo_path:
            print("ERROR: No project configured")
            return 1
        file_path = Path(project_config.repo_path) / PROJECT_DIRECTIVES_FILENAME
    elif args.level == "feature":
        if not args.workstream:
            print("ERROR: --workstream required for feature directives")
            return 1
        workstream_dir = ops_dir / "workstreams" / args.workstream
        if not workstream_dir.exists():
            print(f"ERROR: Workstream '{args.workstream}' not found")
            return 1
        file_path = workstream_dir / FEATURE_DIRECTIVES_FILENAME

    # Create file if it doesn't exist
    if not file_path.exists():
        file_path.write_text("# Directives\n\n- \n")

    editor = os.environ.get("EDITOR", "vim")
    result = subprocess.run([editor, str(file_path)])
    return result.returncode
