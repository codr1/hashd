"""Git utility functions for the runner.

This module re-exports from orchestrator.git for backward compatibility.
New code should import directly from orchestrator.git.
"""

from orchestrator.git.status import has_uncommitted_changes

__all__ = ["has_uncommitted_changes"]
