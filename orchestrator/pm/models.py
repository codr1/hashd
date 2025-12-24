"""
Data models for PM module.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Story:
    """A refined story carved from dirty requirements.

    Stories are the unit of work for hashd. They represent a coherent
    chunk of functionality that can be implemented in a workstream.
    """
    id: str                                    # STORY-0001
    title: str
    status: str                                # draft, accepted, implemented
    created: str                               # ISO timestamp
    source_refs: str                           # Free text: "REQS.md Section 4.4, Issue #32"
    problem: str                               # What problem does this solve
    acceptance_criteria: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    workstream: Optional[str] = None           # Linked workstream when accepted
    implemented_at: Optional[str] = None       # ISO timestamp when implemented
