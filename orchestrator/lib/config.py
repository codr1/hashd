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


@dataclass
class ProjectProfile:
    """Build/test configuration from project_profile.env"""
    makefile_path: str
    make_target_test: str
    implement_timeout: int
    review_timeout: int
    test_timeout: int


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
    )


def load_project_profile(project_dir: Path) -> ProjectProfile:
    """Load project_profile.env and return ProjectProfile."""
    env = envparse.load_env(str(project_dir / "project_profile.env"))
    return ProjectProfile(
        makefile_path=env.get("MAKEFILE_PATH", "Makefile"),
        make_target_test=env.get("MAKE_TARGET_TEST", "test"),
        implement_timeout=int(env.get("IMPLEMENT_TIMEOUT", "600")),
        review_timeout=int(env.get("REVIEW_TIMEOUT", "120")),
        test_timeout=int(env.get("TEST_TIMEOUT", "300")),
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
