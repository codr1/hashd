"""
Pre-compute codebase context for agents.

Provides directory structure and key file listings to reduce
agent exploration time during implementation.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["get_codebase_context"]


def get_codebase_context(worktree: Path, max_files: int = 50) -> str:
    """Generate a concise codebase summary for agent prompts.

    Returns a markdown section with:
    - Key directory structure
    - Relevant file paths for the project type
    - Detected patterns

    Args:
        worktree: Path to the worktree root
        max_files: Maximum files to list per category

    Returns:
        Formatted markdown string for insertion into prompt
    """
    lines = ["## Codebase Context\n"]

    # Get directory structure (top 2 levels)
    try:
        result = subprocess.run(
            ["find", ".", "-maxdepth", "2", "-type", "d", "-not", "-path", "*/.*"],
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            dirs = sorted(result.stdout.strip().split("\n"))[:20]
            lines.append("### Directory Structure")
            lines.append("```")
            lines.extend(dirs)
            lines.append("```\n")
    except Exception as e:
        logger.debug(f"Failed to get directory structure: {e}")

    # Detect project type and get relevant files
    file_patterns = _detect_file_patterns(worktree)

    for label, pattern in file_patterns.items():
        try:
            result = subprocess.run(
                ["find", ".", "-path", f"./{pattern}", "-type", "f"],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                files = result.stdout.strip().split("\n")[:10]
                lines.append(f"### {label}")
                for f in files:
                    lines.append(f"- {f}")
                lines.append("")
        except Exception as e:
            logger.debug(f"Failed to find {label} files: {e}")

    return "\n".join(lines)


def _detect_file_patterns(worktree: Path) -> dict[str, str]:
    """Detect project type and return relevant file patterns."""
    patterns = {}

    # Check for Go project
    if (worktree / "go.mod").exists():
        patterns.update(
            {
                "Go handlers": "internal/api/**/handlers.go",
                "Go templates": "internal/templates/**/*.templ",
                "SQL queries": "internal/db/queries/*.sql",
                "Routes": "cmd/server/server.go",
            }
        )

    # Check for Python project
    if (worktree / "requirements.txt").exists() or (worktree / "pyproject.toml").exists():
        patterns.update(
            {
                "Python modules": "**/*.py",
                "Tests": "tests/**/*.py",
            }
        )

    # Check for Node/TypeScript project
    if (worktree / "package.json").exists():
        patterns.update(
            {
                "TypeScript files": "src/**/*.ts",
                "React components": "src/**/*.tsx",
                "Tests": "**/*.test.ts",
            }
        )

    # Fallback: general patterns
    if not patterns:
        patterns.update(
            {
                "Source files": "src/**/*",
                "Config files": "*.yaml",
            }
        )

    return patterns
