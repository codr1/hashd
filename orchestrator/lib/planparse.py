"""
Plan.md parser for AOS.

Extracts micro-commits from plan files.
"""

import re
from dataclasses import dataclass
from pathlib import Path

HEADING_RE = re.compile(r'^###\s+(COMMIT-[A-Za-z0-9_-]+-\d{3}):\s*(.+?)\s*$')
DONE_RE = re.compile(r'^Done:\s*\[([ xX])\]\s*$')


@dataclass
class MicroCommit:
    id: str
    title: str
    done: bool
    line_number: int
    block_content: str


def parse_plan(filepath: str) -> list[MicroCommit]:
    """Parse plan.md and return list of micro-commits."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Plan file not found: {filepath}")

    lines = path.read_text().splitlines()
    commits = []
    current = None
    current_lines = []
    in_comment = False

    for lineno, line in enumerate(lines, 1):
        # Track HTML comment blocks
        if '<!--' in line:
            in_comment = True
        if '-->' in line:
            in_comment = False
            continue

        # Skip lines inside HTML comments
        if in_comment:
            continue

        heading_match = HEADING_RE.match(line)

        if heading_match:
            # Save previous block
            if current:
                current.block_content = '\n'.join(current_lines)
                commits.append(current)

            # Start new block
            current = MicroCommit(
                id=heading_match.group(1),
                title=heading_match.group(2),
                done=False,
                line_number=lineno,
                block_content=""
            )
            current_lines = [line]
        elif current:
            current_lines.append(line)
            done_match = DONE_RE.match(line)
            if done_match:
                current.done = done_match.group(1).lower() == 'x'

    # Save last block
    if current:
        current.block_content = '\n'.join(current_lines)
        commits.append(current)

    return commits


def get_next_microcommit(commits: list[MicroCommit]) -> MicroCommit | None:
    """Return first undone micro-commit, or None if all done."""
    for commit in commits:
        if not commit.done:
            return commit
    return None


def mark_done(filepath: str, commit_id: str) -> bool:
    """Mark a micro-commit as done in the plan file. Returns True if updated."""
    path = Path(filepath)
    content = path.read_text()
    lines = content.splitlines()
    in_block = False

    for i, line in enumerate(lines):
        heading_match = HEADING_RE.match(line)
        if heading_match:
            in_block = (heading_match.group(1) == commit_id)
        elif in_block and DONE_RE.match(line):
            lines[i] = 'Done: [x]'
            path.write_text('\n'.join(lines) + '\n')
            return True

    return False


def update_microcommit(filepath: str, commit_id: str, new_title: str, new_content: str) -> bool:
    """Update a microcommit's title and body in plan.md.

    Only works for pending commits (done=False).
    Returns True if updated, False if not found or already done.

    Args:
        filepath: Path to plan.md
        commit_id: The commit ID (e.g., COMMIT-WS-001)
        new_title: New title for the commit (must be single line)
        new_content: New body content (description, without heading or Done marker)
    """
    path = Path(filepath)
    if not path.exists():
        return False

    # Validate title - must be single line
    new_title = new_title.strip()
    if not new_title or '\n' in new_title or '\r' in new_title:
        return False

    content = path.read_text()
    lines = content.splitlines()

    # Find the commit block and Done marker position
    block_start = None
    block_end = None
    done_line_idx = None
    is_done = False

    for i, line in enumerate(lines):
        heading_match = HEADING_RE.match(line)
        if heading_match and heading_match.group(1) == commit_id:
            block_start = i
        elif block_start is not None and heading_match:
            # Next commit starts - end of our block
            block_end = i
            break
        elif block_start is not None:
            done_match = DONE_RE.match(line)
            if done_match:
                is_done = done_match.group(1).lower() == 'x'
                done_line_idx = i

    if block_start is None:
        return False  # Not found

    if is_done:
        return False  # Cannot edit done commits

    # Set block_end to end of file if this is the last commit
    if block_end is None:
        block_end = len(lines)

    # Build the new block
    new_block = [f"### {commit_id}: {new_title}"]

    # Add content lines (ensure blank line after heading if content exists)
    content_lines = new_content.strip().splitlines() if new_content.strip() else []
    if content_lines:
        new_block.append("")
        new_block.extend(content_lines)

    # Add Done marker
    new_block.append("")
    new_block.append("Done: [ ]")

    # Preserve any trailing content after Done marker (notes, metadata, etc.)
    if done_line_idx is not None and done_line_idx + 1 < block_end:
        trailing = lines[done_line_idx + 1:block_end]
        # Only preserve if there's actual content (not just blank lines before next block)
        trailing_content = [l for l in trailing if l.strip()]
        if trailing_content:
            new_block.append("")
            new_block.extend(trailing)
    else:
        new_block.append("")

    # Replace the block in the file
    new_lines = lines[:block_start] + new_block + lines[block_end:]

    path.write_text('\n'.join(new_lines) + '\n')
    return True


def get_next_fix_number(commits: list[MicroCommit], ws_id: str) -> int:
    """Determine the next fix commit number.

    Scans existing commits for FIX-NNN pattern and returns next available.
    """
    max_fix = 0
    pattern = re.compile(rf'COMMIT-{re.escape(ws_id.upper())}-FIX-(\d{{3}})', re.IGNORECASE)

    for commit in commits:
        match = pattern.search(commit.id)
        if match:
            fix_num = int(match.group(1))
            max_fix = max(max_fix, fix_num)

    return max_fix + 1


def format_fix_commit(
    ws_id: str,
    fix_number: int,
    feedback_items: list,
    feedback_source: str | None,
    user_guidance: str | None,
) -> str:
    """Format a fix micro-commit for appending to plan.md.

    Args:
        ws_id: Workstream ID (used for commit ID)
        fix_number: Sequential fix number (1, 2, 3...)
        feedback_items: List of feedback items (with 'type', 'body', optional 'path', 'line')
        feedback_source: Source of feedback ("Final Review", "PR #123")
        user_guidance: Additional guidance from user

    Returns:
        Markdown text for the micro-commit block.
    """
    commit_id = f"COMMIT-{ws_id.upper()}-FIX-{fix_number:03d}"

    lines = [f"### {commit_id}: Address review feedback", ""]

    if feedback_source:
        lines.append(f"**Source:** {feedback_source}")
        lines.append("")

    if feedback_items:
        lines.append("**Feedback to address:**")
        lines.append("")

        for i, item in enumerate(feedback_items, 1):
            # Handle both dataclass objects and dicts
            if isinstance(item, dict):
                item_type = item.get('type', '')
                body = item.get('body', '')
                path = item.get('path')
                line_num = item.get('line')
            else:
                item_type = getattr(item, 'type', '')
                body = getattr(item, 'body', str(item))
                path = getattr(item, 'path', None)
                line_num = getattr(item, 'line', None)

            if item_type == "line_comment" and path:
                loc = f"`{path}"
                if line_num:
                    loc += f":{line_num}"
                loc += "`"
                lines.append(f"{i}. {loc} - {body}")
            else:
                lines.append(f"{i}. {body}")

        lines.append("")

    if user_guidance:
        lines.append("**Additional guidance:**")
        lines.append("")
        lines.append(user_guidance)
        lines.append("")

    lines.append("Done: [ ]")
    lines.append("")

    return "\n".join(lines)


def append_commit_to_plan(filepath: str, commit_content: str) -> bool:
    """Append a micro-commit block to plan.md.

    Adds the commit content at the end of the file.
    Returns True on success.
    """
    path = Path(filepath)
    if not path.exists():
        return False

    content = path.read_text()

    # Ensure there's a newline before appending
    if not content.endswith('\n'):
        content += '\n'

    content += commit_content

    path.write_text(content)
    return True
