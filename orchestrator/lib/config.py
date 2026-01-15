"""
Configuration loaders for AOS.

Loads project and workstream configuration from .env files.
"""

from dataclasses import dataclass
from pathlib import Path
from . import envparse
from . import validate


@dataclass
class ProjectConfig:
    """Project-level configuration from project.env"""
    name: str
    repo_path: Path
    default_branch: str
    reqs_path: str  # Relative to repo_path, e.g., "REQS.md"
    description: str  # Brief system description for reviewer context
    tech_preferred: str  # Preferred technologies - use by default
    tech_acceptable: str  # Acceptable - okay when needed, prefer alternatives
    tech_avoid: str  # Avoid - don't introduce unless extraordinary reason


@dataclass
class ProjectProfile:
    """Build/test configuration from project_profile.env"""
    makefile_path: str
    make_target_test: str
    merge_gate_test_target: str
    implement_timeout: int
    review_timeout: int
    test_timeout: int
    breakdown_timeout: int
    supervised_mode: bool


@dataclass
class Workstream:
    """Workstream metadata from meta.env"""
    id: str
    title: str
    branch: str
    worktree: Path
    base_branch: str
    base_sha: str
    status: str
    dir: Path


def load_project_config(project_dir: Path) -> ProjectConfig:
    """Load project.env and return ProjectConfig."""
    env = envparse.load_env(str(project_dir / "project.env"))
    return ProjectConfig(
        name=env["PROJECT_NAME"],
        repo_path=Path(env["REPO_PATH"]),
        default_branch=env.get("DEFAULT_BRANCH", "main"),
        reqs_path=env.get("REQS_PATH", "REQS.md"),
        description=env.get("PROJECT_DESCRIPTION", ""),
        tech_preferred=env.get("TECH_PREFERRED", ""),
        tech_acceptable=env.get("TECH_ACCEPTABLE", ""),
        tech_avoid=env.get("TECH_AVOID", ""),
    )


def load_project_profile(project_dir: Path) -> ProjectProfile:
    """Load project_profile.env and return ProjectProfile."""
    env = envparse.load_env(str(project_dir / "project_profile.env"))
    make_target_test = env.get("MAKE_TARGET_TEST", "test")
    return ProjectProfile(
        makefile_path=env.get("MAKEFILE_PATH", "Makefile"),
        make_target_test=make_target_test,
        merge_gate_test_target=env.get("MAKE_TARGET_MERGE_GATE_TEST", make_target_test),
        implement_timeout=int(env.get("IMPLEMENT_TIMEOUT", "600")),
        review_timeout=int(env.get("REVIEW_TIMEOUT", "300")),  # Contextual reviews need more time
        test_timeout=int(env.get("TEST_TIMEOUT", "300")),
        breakdown_timeout=int(env.get("BREAKDOWN_TIMEOUT", "180")),
        supervised_mode=env.get("SUPERVISED_MODE", "false").lower() == "true",
    )


def load_workstream(workstream_dir: Path) -> Workstream:
    """Load meta.env and return Workstream."""
    env = envparse.load_env(str(workstream_dir / "meta.env"))

    # Validate against schema
    validate.validate(env, "meta")

    return Workstream(
        id=env["ID"],
        title=env["TITLE"],
        branch=env["BRANCH"],
        worktree=Path(env["WORKTREE"]),
        base_branch=env["BASE_BRANCH"],
        base_sha=env["BASE_SHA"],
        status=env.get("STATUS", "active"),
        dir=workstream_dir,
    )


def get_active_workstreams(project_dir: Path) -> list[Workstream]:
    """List all active (non-closed) workstreams."""
    ws_dir = project_dir / "workstreams"
    if not ws_dir.exists():
        return []

    workstreams = []
    for d in ws_dir.iterdir():
        if d.is_dir() and not d.name.startswith("_"):
            try:
                workstreams.append(load_workstream(d))
            except Exception:
                pass  # Skip invalid workstreams

    return workstreams


def get_current_workstream(ops_dir: Path) -> str | None:
    """Get the current workstream ID from context, or None if not set.

    Auto-clears stale context if the workstream no longer exists.
    """
    context_file = ops_dir / "config" / "current_workstream"
    if context_file.exists():
        ws_id = context_file.read_text().strip()
        if ws_id:
            # Validate workstream still exists
            ws_dir = ops_dir / "workstreams" / ws_id
            if ws_dir.exists():
                return ws_id
            # Stale context - clean it up
            context_file.unlink()
    return None


def set_current_workstream(ops_dir: Path, ws_id: str) -> None:
    """Set the current workstream context."""
    config_dir = ops_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    context_file = config_dir / "current_workstream"
    context_file.write_text(ws_id + "\n")


def clear_current_workstream(ops_dir: Path) -> None:
    """Clear the current workstream context."""
    context_file = ops_dir / "config" / "current_workstream"
    if context_file.exists():
        context_file.unlink()
