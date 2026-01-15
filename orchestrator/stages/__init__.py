"""Transcript/observability infrastructure for HASHD workflow.

Note: Stage execution types (StageError, StageBlocked, StageResult) are in
orchestrator.runner.stages. This module provides observability for recording
agent/human exchanges during runs.
"""

from orchestrator.stages.transcript import (
    Transcript,
    TranscriptEntry,
    Actor,
)

__all__ = [
    "Transcript",
    "TranscriptEntry",
    "Actor",
]
