"""
Stage execution framework for AOS.

Defines the stage pipeline and error handling.
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from orchestrator.runner.context import RunContext


class StageResult(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


@dataclass
class StageError(Exception):
    """A stage failed."""
    stage: str
    message: str
    exit_code: int
    details: Optional[dict] = None  # Structured details (e.g., {"type": "test_failure", "output": "..."})

    def __str__(self):
        return f"[{self.stage}] {self.message}"


@dataclass
class StageBlocked(Exception):
    """A stage is blocked (e.g., needs clarification)."""
    stage: str
    reason: str


# Stage function signature: (ctx: RunContext) -> None
# Raises StageError on failure, StageBlocked if blocked

STAGE_ORDER = [
    "load",
    "select",
    "clarification_check",
    "implement",
    "test",
    "review",
    "qa_gate",
    "update_state",
]


def run_stage(ctx: RunContext, stage_name: str, stage_fn: Callable[[RunContext], None]) -> StageResult:
    """
    Run a single stage with timing and error handling.

    Returns StageResult and updates ctx.stages.
    """
    ctx.log(f"Starting stage: {stage_name}")
    start = time.time()

    try:
        stage_fn(ctx)
        duration = time.time() - start
        ctx.record_stage(stage_name, "passed", duration)
        ctx.log(f"Stage {stage_name} passed ({duration:.2f}s)")
        return StageResult.PASSED

    except StageBlocked as e:
        duration = time.time() - start
        ctx.record_stage(stage_name, "blocked", duration, e.reason)
        ctx.log(f"Stage {stage_name} blocked: {e.reason}")
        return StageResult.BLOCKED

    except StageError as e:
        duration = time.time() - start
        ctx.record_stage(stage_name, "failed", duration, e.message)
        ctx.log(f"Stage {stage_name} failed: {e.message}")
        raise

    except Exception as e:
        duration = time.time() - start
        ctx.record_stage(stage_name, "failed", duration, str(e))
        ctx.log(f"Stage {stage_name} error: {e}")
        raise StageError(stage_name, str(e), 9)
