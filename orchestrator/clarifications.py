"""
Clarification Queue (CLQ) management for AOS.

CLQs are questions that agents need answered before proceeding.
They can be blocking (stops pipeline) or non-blocking (informational).
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Clarification:
    """A clarification request from an agent."""
    id: str
    status: str  # pending, answered, stale
    question: str
    context: str
    options: list[dict[str, str]]  # List of option dicts with label/description
    workstream: str
    blocks: list  # List of micro-commit IDs this blocks
    urgency: str  # blocking, non-blocking
    created: str
    answered: Optional[str] = None
    answer: Optional[str] = None
    answered_by: Optional[str] = None


def create_clarification(ws_dir: Path, data: dict) -> Clarification:
    """Create a new clarification request.

    Args:
        ws_dir: Workstream directory
        data: Dict with question, context, options, blocks, urgency

    Returns:
        Created Clarification object
    """
    clq_id = generate_clq_id(ws_dir)

    clq = Clarification(
        id=clq_id,
        status="pending",
        question=data["question"],
        context=data.get("context", ""),
        options=data.get("options", []),
        workstream=ws_dir.name,
        blocks=data.get("blocks", []),
        urgency=data.get("urgency", "blocking"),
        created=datetime.now().isoformat()
    )

    # Write JSON
    pending_dir = ws_dir / "clarifications" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    (pending_dir / f"{clq_id}.json").write_text(
        json.dumps(asdict(clq), indent=2)
    )

    # Write markdown for human readability
    write_clq_markdown(pending_dir / f"{clq_id}.md", clq)

    return clq


def get_pending_clarifications(ws_dir: Path) -> list[Clarification]:
    """Get all pending CLQs for a workstream."""
    pending_dir = ws_dir / "clarifications" / "pending"
    if not pending_dir.exists():
        return []

    clqs = []
    for f in sorted(pending_dir.glob("CLQ-*.json")):
        try:
            data = json.loads(f.read_text())
            clqs.append(Clarification(**data))
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to load CLQ file {f}: {e}")

    return clqs


def get_blocking_clarifications(ws_dir: Path) -> list[Clarification]:
    """Get blocking CLQs for workstream."""
    pending = get_pending_clarifications(ws_dir)
    return [c for c in pending if c.urgency == "blocking"]


def get_clarification(ws_dir: Path, clq_id: str) -> Optional[Clarification]:
    """Get a specific CLQ by ID (checks pending and answered)."""
    for subdir in ["pending", "answered"]:
        path = ws_dir / "clarifications" / subdir / f"{clq_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                return Clarification(**data)
            except (json.JSONDecodeError, TypeError):
                return None
    return None


def answer_clarification(ws_dir: Path, clq_id: str, answer: str, by: str = "human"):
    """Record answer and move CLQ to answered/.

    Args:
        ws_dir: Workstream directory
        clq_id: CLQ ID (e.g., "CLQ-001")
        answer: The answer text
        by: Who answered (default "human")
    """
    pending_path = ws_dir / "clarifications" / "pending" / f"{clq_id}.json"

    if not pending_path.exists():
        raise FileNotFoundError(f"CLQ not found: {clq_id}")

    data = json.loads(pending_path.read_text())
    data["status"] = "answered"
    data["answer"] = answer
    data["answered"] = datetime.now().isoformat()
    data["answered_by"] = by

    # Move to answered
    answered_dir = ws_dir / "clarifications" / "answered"
    answered_dir.mkdir(parents=True, exist_ok=True)

    (answered_dir / f"{clq_id}.json").write_text(json.dumps(data, indent=2))
    pending_path.unlink()

    # Also update/move markdown
    md_pending = pending_path.with_suffix(".md")
    if md_pending.exists():
        # Update markdown with answer
        clq = Clarification(**data)
        write_clq_markdown(answered_dir / f"{clq_id}.md", clq)
        md_pending.unlink()


def generate_clq_id(ws_dir: Path) -> str:
    """Generate next CLQ ID for workstream."""
    pending = ws_dir / "clarifications" / "pending"
    answered = ws_dir / "clarifications" / "answered"

    existing = []
    for d in [pending, answered]:
        if d.exists():
            existing.extend(f.stem for f in d.glob("CLQ-*.json"))

    if not existing:
        return "CLQ-001"

    nums = []
    for x in existing:
        if x.startswith("CLQ-"):
            try:
                nums.append(int(x.split("-")[1]))
            except (ValueError, IndexError):
                logger.warning(f"Malformed CLQ ID ignored: {x}")

    if not nums:
        return "CLQ-001"
    return f"CLQ-{max(nums) + 1:03d}"


def write_clq_markdown(path: Path, clq: Clarification):
    """Write CLQ as human-readable markdown."""
    lines = [
        f"# {clq.id}: Clarification Request",
        "",
        f"**Status:** {clq.status}",
        f"**Urgency:** {clq.urgency}",
        f"**Created:** {clq.created}",
        "",
        "## Question",
        "",
        clq.question,
        "",
    ]

    if clq.context:
        lines.extend([
            "## Context",
            "",
            clq.context,
            "",
        ])

    if clq.options:
        lines.extend([
            "## Options",
            "",
        ])
        for i, opt in enumerate(clq.options, 1):
            label = opt.get("label", f"Option {i}")
            desc = opt.get("description", "")
            lines.append(f"{i}. **{label}**")
            if desc:
                lines.append(f"   {desc}")
        lines.append("")

    if clq.blocks:
        lines.extend([
            "## Blocks",
            "",
            ", ".join(clq.blocks),
            "",
        ])

    if clq.status == "answered":
        lines.extend([
            "## Answer",
            "",
            f"**Answered by:** {clq.answered_by}",
            f"**Answered at:** {clq.answered}",
            "",
            clq.answer or "(no answer provided)",
            "",
        ])

    path.write_text("\n".join(lines))
