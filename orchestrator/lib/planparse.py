"""
Plan.md parser for AOS.

Extracts micro-commits from plan files.
"""

import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

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


def get_next_microcommit(commits: list[MicroCommit]) -> Optional[MicroCommit]:
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
