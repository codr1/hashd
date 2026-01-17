"""
Stage execution framework for AOS.

Defines the stage pipeline and error handling.
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from orchestrator.lib.constants import STATUS_HUMAN_GATE_DONE
from orchestrator.runner.context import RunContext


class StageResult(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    HUMAN_GATE_DONE = STATUS_HUMAN_GATE_DONE  # Human gate processed, flow should exit


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


@dataclass
class StageHumanGateProcessed(Exception):
    """Human gate was processed - flow should exit for new run to continue.

    This is raised after approve/reject at human gate to signal that:
    1. The human decision has been recorded (state transitioned)
    2. The flow should exit cleanly
    3. A new flow run should be triggered to continue work

    This enables the UX pattern where approve/reject exit the current flow
    and optionally trigger a new one.
    """
    stage: str
    action: str  # "approve" or "reject"
    feedback: str = ""
    reset: bool = False


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
    "commit",
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

    except StageHumanGateProcessed as e:
        duration = time.time() - start
        ctx.record_stage(stage_name, STATUS_HUMAN_GATE_DONE, duration, f"{e.action}")
        ctx.log(f"Stage {stage_name} human gate processed: {e.action}")
        raise  # Re-raise so run_once can handle it

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
