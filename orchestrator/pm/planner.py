"""
PM Planning Session for hashd.

The interactive planning session reads requirements, SPEC, and active
workstreams to propose next logical chunks to build.
"""

import json
from pathlib import Path
from typing import Optional

from orchestrator.lib.config import ProjectConfig
from orchestrator.pm.claude_utils import run_claude, strip_markdown_fences

# Limit touched files in prompt to avoid token explosion
MAX_TOUCHED_FILES_IN_PROMPT = 20


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
    parts = [
        "You are helping a human PM plan the next chunk of work to implement.",
        "",
        "Your job is to:",
        "1. Review the dirty requirements (REQS.md)",
        "2. See what's already built (SPEC.md)",
        "3. Check for conflicts with active workstreams",
        "4. Propose 2-4 logical next chunks to build",
        "5. Flag any missing requirements or ambiguities",
        "",
    ]

    # Add SPEC
    if context.get("spec_content"):
        parts.extend([
            "## Current SPEC (what's already built)",
            "",
            context["spec_content"],
            "",
        ])
    else:
        parts.extend([
            "## Current SPEC",
            "",
            "No SPEC.md exists yet. This is a greenfield project.",
            "",
        ])

    # Add active workstreams
    if context.get("workstreams"):
        parts.extend([
            "## Active Workstreams (in progress)",
            "",
        ])
        for ws in context["workstreams"]:
            parts.append(f"### {ws['id']}")
            if ws["touched_files"]:
                parts.append("Touches:")
                for f in ws["touched_files"][:MAX_TOUCHED_FILES_IN_PROMPT]:
                    parts.append(f"  - {f}")
                if len(ws["touched_files"]) > MAX_TOUCHED_FILES_IN_PROMPT:
                    parts.append(f"  ... and {len(ws['touched_files']) - MAX_TOUCHED_FILES_IN_PROMPT} more")
            else:
                parts.append("No files touched yet.")
            parts.append("")
    else:
        parts.extend([
            "## Active Workstreams",
            "",
            "No active workstreams.",
            "",
        ])

    # Add REQS
    if context.get("reqs_content"):
        parts.extend([
            "## Requirements (REQS.md)",
            "",
            context["reqs_content"],
            "",
        ])
    else:
        parts.extend([
            "## Requirements",
            "",
            f"ERROR: Could not read {context.get('reqs_path', 'REQS.md')}",
            "",
        ])

    # Add instructions
    parts.extend([
        "---",
        "",
        "## Your Response",
        "",
        "Analyze the requirements and propose the next chunks to build.",
        "For each chunk:",
        "- Give it a short name (e.g., 'cognito-auth', 'theme-management')",
        "- Describe what it covers from the requirements",
        "- Note which requirement sections it addresses",
        "- Flag any missing or unclear requirements",
        "- List likely files/directories it will touch",
        "- Warn about potential conflicts with active workstreams",
        "",
        "Be thorough but succinct. The human will pick one chunk to refine into a story.",
    ])

    return "\n".join(parts)


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


def build_refine_prompt(chunk_name: str, context: dict) -> str:
    """Build the refinement prompt for Claude."""
    parts = [
        f"Refine the chunk '{chunk_name}' into a proper story.",
        "",
        "Create a well-structured story with:",
        "- A clear title",
        "- Source references (which sections of REQS this covers)",
        "- Problem statement (what problem does this solve)",
        "- Acceptance criteria (testable conditions)",
        "- Non-goals (what this explicitly does NOT do)",
        "- Dependencies (what needs to exist first)",
        "- Open questions (anything unclear)",
        "",
        "Respond with ONLY valid JSON (no markdown, no explanation).",
        "",
        "## Required Response Format",
        "{",
        '  "title": "Short descriptive title",',
        '  "source_refs": "REQS.md Section 4.4, Section 7.2",',
        '  "problem": "What problem this solves",',
        '  "acceptance_criteria": ["Criterion 1", "Criterion 2"],',
        '  "non_goals": ["Not doing X", "Not doing Y"],',
        '  "dependencies": ["Need X first"],',
        '  "open_questions": ["Unclear about Y"]',
        "}",
        "",
    ]

    # Add REQS
    if context.get("reqs_content"):
        parts.extend([
            "## Requirements (REQS.md)",
            "",
            context["reqs_content"],
            "",
        ])

    # Add SPEC
    if context.get("spec_content"):
        parts.extend([
            "## Current SPEC (what's already built)",
            "",
            context["spec_content"],
            "",
        ])

    return "\n".join(parts)


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

    prompt = build_refine_prompt(chunk_name, context)
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

        return True, data, "Story refined successfully"

    except json.JSONDecodeError as e:
        return False, None, f"Invalid JSON response: {e}\n\nResponse:\n{response}"
