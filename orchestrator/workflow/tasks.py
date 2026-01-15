"""Prefect task wrappers for stage functions.

Wraps stage functions with @task decorators to get:
- Automatic retry with configurable delay
- Structured logging
- Observability (when connected to Prefect server)

The underlying stage functions and business logic remain unchanged.
"""

from typing import TYPE_CHECKING

from prefect import task

from orchestrator.runner.impl.stages import (
    stage_implement,
    stage_test,
    stage_review,
    stage_qa_gate,
    stage_commit,
)

if TYPE_CHECKING:
    from orchestrator.runner.context import RunContext


@task(
    retries=2,
    retry_delay_seconds=10,
    name="implement",
    description="Run Codex to implement the current micro-commit"
)
def task_implement(ctx: "RunContext", human_feedback: str | None = None):
    """Implement stage with Prefect retry handling.

    Retries handle transient failures like Codex timeouts or API errors.
    Session resume logic in the underlying function handles state recovery.
    """
    return stage_implement(ctx, human_feedback)


@task(
    retries=2,
    retry_delay_seconds=5,
    name="test",
    description="Run build and test suite"
)
def task_test(ctx: "RunContext"):
    """Test stage with Prefect retry handling.

    Retries handle subprocess timeouts and infrastructure failures.
    """
    return stage_test(ctx)


@task(
    retries=1,
    retry_delay_seconds=30,
    name="review",
    description="Run Claude to review implementation"
)
def task_review(ctx: "RunContext"):
    """Review stage with Prefect retry handling.

    Single retry with longer delay for Claude rate limits.
    """
    return stage_review(ctx)


@task(
    retries=1,
    retry_delay_seconds=5,
    name="qa_gate",
    description="Run QA validation checks"
)
def task_qa_gate(ctx: "RunContext"):
    """QA gate with Prefect retry handling."""
    return stage_qa_gate(ctx)


@task(
    retries=2,
    retry_delay_seconds=5,
    name="commit",
    description="Commit and push changes"
)
def task_commit(ctx: "RunContext"):
    """Commit changes with Prefect retry handling.

    Retries handle transient git push failures.
    """
    return stage_commit(ctx)
