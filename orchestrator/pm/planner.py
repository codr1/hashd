"""
PM Planning Session for hashd.

The interactive planning session reads requirements, SPEC, and active
workstreams to propose next logical chunks to build.
"""

import json
import re
import secrets
from pathlib import Path
from typing import Optional

from orchestrator.lib.config import ProjectConfig
from orchestrator.lib.constants import MAX_WS_ID_LEN, WS_ID_PATTERN
from orchestrator.lib.prompts import render_prompt, build_section
from orchestrator.pm.claude_utils import run_claude, strip_markdown_fences

# Limit touched files in prompt to avoid token explosion
MAX_TOUCHED_FILES_IN_PROMPT = 20


def is_ws_id_available(ops_dir: Path, ws_id: str) -> bool:
    """Check if workstream ID is available (not in active or archived)."""
    return (
        not (ops_dir / "workstreams" / ws_id).exists() and
        not (ops_dir / "workstreams" / "_closed" / ws_id).exists()
    )


def is_valid_ws_id(ws_id: str) -> bool:
    """Check if workstream ID is valid format."""
    return bool(WS_ID_PATTERN.match(ws_id)) and len(ws_id) <= MAX_WS_ID_LEN


def slugify_for_ws_id(text: str, max_len: int = MAX_WS_ID_LEN) -> str:
    """Convert text to valid workstream ID.

    - Lowercase
    - Replace spaces/special chars with underscore
    - Remove consecutive underscores
    - Truncate to max_len
    - Ensure starts with letter
    """
    # Lowercase and replace non-alphanumeric with underscore
    slug = re.sub(r'[^a-z0-9]+', '_', text.lower())
    # Remove leading/trailing underscores
    slug = slug.strip('_')
    # Remove consecutive underscores
    slug = re.sub(r'_+', '_', slug)
    # Ensure starts with letter
    if slug and not slug[0].isalpha():
        slug = 'ws_' + slug
    # Truncate
    if len(slug) > max_len:
        # Try to truncate at word boundary
        slug = slug[:max_len].rsplit('_', 1)[0]
    return slug or 'workstream'


def gather_context(
    project_config: ProjectConfig,
    ops_dir: Path,
    project_dir: Path,
) -> dict:
    """Gather context for planning session.

    Returns dict with:
      - reqs_content: Contents of REQS.md
      - spec_content: Contents of SPEC.md (or None)
      - workstreams: List of active workstream info with touched files
    """
    context = {}

    # Read requirements
    reqs_path = project_config.repo_path / project_config.reqs_path
    if reqs_path.exists():
        context["reqs_content"] = reqs_path.read_text()
        context["reqs_path"] = str(reqs_path)
    else:
        context["reqs_content"] = None
        context["reqs_path"] = str(reqs_path)

    # Read SPEC if exists
    spec_path = project_dir / "SPEC.md"
    if spec_path.exists():
        context["spec_content"] = spec_path.read_text()
    else:
        context["spec_content"] = None

    # Get active workstreams with touched files
    workstreams_dir = ops_dir / "workstreams"
    context["workstreams"] = []

    if workstreams_dir.exists():
        for ws_dir in sorted(workstreams_dir.iterdir()):
            if not ws_dir.is_dir() or ws_dir.name.startswith("_"):
                continue

            meta_path = ws_dir / "meta.env"
            if not meta_path.exists():
                continue

            ws_info = {"id": ws_dir.name, "touched_files": []}

            # Read touched files
            touched_path = ws_dir / "touched_files.txt"
            if touched_path.exists():
                ws_info["touched_files"] = [
                    f.strip() for f in touched_path.read_text().splitlines()
                    if f.strip()
                ]

            context["workstreams"].append(ws_info)

    return context


def build_plan_prompt(context: dict) -> str:
    """Build the planning prompt for Claude."""
    # Build SPEC section
    spec_section = build_section(
        context.get("spec_content"),
        "## Current SPEC (what's already built)",
        "No SPEC.md exists yet. This is a greenfield project."
    )

    # Build active workstreams section
    workstreams_section = ""
    if context.get("workstreams"):
        ws_parts = ["## Active Workstreams (in progress)\n"]
        for ws in context["workstreams"]:
            ws_parts.append(f"### {ws['id']}")
            if ws["touched_files"]:
                ws_parts.append("Touches:")
                for f in ws["touched_files"][:MAX_TOUCHED_FILES_IN_PROMPT]:
                    ws_parts.append(f"  - {f}")
                if len(ws["touched_files"]) > MAX_TOUCHED_FILES_IN_PROMPT:
                    ws_parts.append(f"  ... and {len(ws['touched_files']) - MAX_TOUCHED_FILES_IN_PROMPT} more")
            else:
                ws_parts.append("No files touched yet.")
            ws_parts.append("")
        workstreams_section = "\n".join(ws_parts)
    else:
        workstreams_section = "## Active Workstreams\n\nNo active workstreams.\n"

    # Build REQS section
    if context.get("reqs_content"):
        reqs_section = build_section(
            context["reqs_content"],
            "## Requirements (REQS.md)"
        )
    else:
        reqs_section = f"## Requirements\n\nERROR: Could not read {context.get('reqs_path', 'REQS.md')}\n"

    return render_prompt(
        "plan_discovery",
        spec_section=spec_section,
        workstreams_section=workstreams_section,
        reqs_section=reqs_section
    )


def run_plan_session(
    project_config: ProjectConfig,
    ops_dir: Path,
    project_dir: Path,
    timeout: int = 300,
) -> tuple[bool, str]:
    """Run interactive planning session with Claude.

    Returns (success, response_text).
    """
    context = gather_context(project_config, ops_dir, project_dir)

    if not context.get("reqs_content"):
        return False, f"Cannot read requirements file: {context.get('reqs_path')}"

    prompt = build_plan_prompt(context)
    return run_claude(prompt, timeout)


def build_refine_prompt(chunk_name: str, context: dict, existing_ws_ids: list[str] = None) -> str:
    """Build the refinement prompt for Claude."""
    # Build existing workstream IDs section
    existing_ws_ids_section = ""
    if existing_ws_ids:
        existing_ws_ids_section = f"## Existing Workstream IDs (DO NOT reuse these)\n\n{', '.join(existing_ws_ids)}\n"

    # Build REQS section
    reqs_section = build_section(
        context.get("reqs_content"),
        "## Requirements (REQS.md)"
    )

    # Build SPEC section
    spec_section = build_section(
        context.get("spec_content"),
        "## Current SPEC (what's already built)"
    )

    return render_prompt(
        "refine_story",
        chunk_name=chunk_name,
        existing_ws_ids_section=existing_ws_ids_section,
        reqs_section=reqs_section,
        spec_section=spec_section
    )


def get_existing_ws_ids(ops_dir: Path) -> list[str]:
    """Get list of all existing workstream IDs (active and archived)."""
    ws_ids = []
    workstreams_dir = ops_dir / "workstreams"

    if workstreams_dir.exists():
        # Active workstreams
        for ws_dir in workstreams_dir.iterdir():
            if ws_dir.is_dir() and not ws_dir.name.startswith("_"):
                ws_ids.append(ws_dir.name)

        # Archived workstreams
        closed_dir = workstreams_dir / "_closed"
        if closed_dir.exists():
            for ws_dir in closed_dir.iterdir():
                if ws_dir.is_dir():
                    ws_ids.append(ws_dir.name)

    return ws_ids


def run_refine_session(
    chunk_name: str,
    project_config: ProjectConfig,
    ops_dir: Path,
    project_dir: Path,
    timeout: int = 300,
) -> tuple[bool, Optional[dict], str]:
    """Refine a chunk into a story.

    Returns (success, story_data, message).
    story_data is a dict ready for create_story() if successful.
    """
    context = gather_context(project_config, ops_dir, project_dir)

    if not context.get("reqs_content"):
        return False, None, f"Cannot read requirements file: {context.get('reqs_path')}"

    # Get existing workstream IDs to avoid duplicates
    existing_ws_ids = get_existing_ws_ids(ops_dir)

    prompt = build_refine_prompt(chunk_name, context, existing_ws_ids)
    success, response = run_claude(prompt, timeout)

    if not success:
        return False, None, response

    # Parse JSON response
    try:
        text = strip_markdown_fences(response)
        data = json.loads(text)

        # Validate required fields
        required = ["title", "source_refs", "problem", "acceptance_criteria"]
        missing = [f for f in required if f not in data]
        if missing:
            return False, None, f"Missing required fields: {missing}"

        # Validate or generate suggested_ws_id
        suggested_id = data.get("suggested_ws_id", "")
        if not suggested_id or not is_valid_ws_id(suggested_id):
            # AI didn't provide valid ID, generate from title
            suggested_id = slugify_for_ws_id(data["title"])

        # Check for duplicates
        if not is_ws_id_available(ops_dir, suggested_id):
            # Add suffix to make unique
            base_id = suggested_id[:MAX_WS_ID_LEN - 2]
            for i in range(1, 100):
                candidate = f"{base_id}_{i}"
                if len(candidate) <= MAX_WS_ID_LEN and is_ws_id_available(ops_dir, candidate):
                    suggested_id = candidate
                    break
            else:
                # All suffixes exhausted, use random suffix
                suggested_id = f"{base_id[:8]}_{secrets.token_hex(3)}"

        data["suggested_ws_id"] = suggested_id

        return True, data, "Story refined successfully"

    except json.JSONDecodeError as e:
        return False, None, f"Invalid JSON response: {e}\n\nResponse:\n{response}"
