"""
Configuration loaders for AOS.

Loads project and workstream configuration from .env files.
"""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from . import envparse
from . import validate
from .github import VALID_MERGE_MODES

logger = logging.getLogger(__name__)


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


VALID_BUILD_RUNNERS = {"make", "task"}


@dataclass
class ProjectProfile:
    """Build/test configuration from project_profile.env"""
    build_runner: str  # "make" (default) or "task"
    makefile_path: str  # For make: path to Makefile
    build_target: str  # Target to compile (e.g., "build") - run before tests
    test_target: str  # Target to run tests (e.g., "test")
    merge_gate_test_target: str  # Target for full test suite at merge gate
    implement_timeout: int
    review_timeout: int
    test_timeout: int
    breakdown_timeout: int
    supervised_mode: bool
    merge_mode: str  # "local" or "github_pr"

    def get_build_file(self, worktree: Path) -> Path:
        """Get the build file path for this runner.

        For task runner, checks common Taskfile variants in order:
        Taskfile.yml, Taskfile.yaml, taskfile.yml, Taskfile.dist.yml

        Returns the first existing file, or the default path if none exist.
        """
        if self.build_runner == "task":
            # Check common Taskfile variants in order of preference
            variants = [
                "Taskfile.yml",
                "Taskfile.yaml",
                "taskfile.yml",
                "Taskfile.dist.yml",
            ]
            for variant in variants:
                path = worktree / variant
                if path.exists():
                    return path
            # Return default if none exist (caller will handle missing file)
            return worktree / "Taskfile.yml"
        else:  # make
            return worktree / self.makefile_path

    def get_build_command(self, worktree: Path, target: str) -> list[str]:
        """Get the command to run a build target."""
        if self.build_runner == "task":
            return ["task", "-d", str(worktree), target]
        else:  # make
            return ["make", "-C", str(worktree), target]

    def validate_runner(self) -> tuple[bool, str]:
        """Check if the build runner binary is available.

        Returns:
            (True, "") if runner is available
            (False, error_message) if runner is not found
        """
        binary = "task" if self.build_runner == "task" else "make"
        if shutil.which(binary) is None:
            return False, f"Build runner '{binary}' not found in PATH"
        return True, ""


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
    pr_url: str | None = None  # GitHub PR URL if in PR workflow
    pr_number: int | None = None  # GitHub PR number if in PR workflow


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
    # Support both TEST_TARGET (preferred) and MAKE_TARGET_TEST (legacy)
    test_target = env.get("TEST_TARGET") or env.get("MAKE_TARGET_TEST", "test")

    # Validate build runner - default to make
    build_runner = env.get("BUILD_RUNNER", "make")
    if build_runner not in VALID_BUILD_RUNNERS:
        logger.warning(
            f"Unknown BUILD_RUNNER '{build_runner}', defaulting to 'make'. "
            f"Valid runners: {', '.join(sorted(VALID_BUILD_RUNNERS))}"
        )
        build_runner = "make"

    # Validate merge mode - default based on environment
    from orchestrator.lib.github import get_default_merge_mode, VALID_MERGE_MODES
    merge_mode = env.get("MERGE_MODE") or get_default_merge_mode()
    if merge_mode not in VALID_MERGE_MODES:
        logger.warning(
            f"Unknown MERGE_MODE '{merge_mode}', defaulting to 'local'. "
            f"Valid modes: {', '.join(sorted(VALID_MERGE_MODES))}"
        )
        merge_mode = "local"

    return ProjectProfile(
        build_runner=build_runner,
        makefile_path=env.get("MAKEFILE_PATH", "Makefile"),
        build_target=env.get("BUILD_TARGET", "build"),
        test_target=test_target,
        merge_gate_test_target=env.get("MERGE_GATE_TEST_TARGET") or env.get("MAKE_TARGET_MERGE_GATE_TEST", test_target),
        implement_timeout=int(env.get("IMPLEMENT_TIMEOUT", "1200")),  # 20 min default
        review_timeout=int(env.get("REVIEW_TIMEOUT", "900")),  # Increased from 600 - large changes need more time
        test_timeout=int(env.get("TEST_TIMEOUT", "300")),
        breakdown_timeout=int(env.get("BREAKDOWN_TIMEOUT", "180")),
        supervised_mode=env.get("SUPERVISED_MODE", "false").lower() == "true",
        merge_mode=merge_mode,
    )


def load_workstream(workstream_dir: Path) -> Workstream:
    """Load meta.env and return Workstream."""
    env = envparse.load_env(str(workstream_dir / "meta.env"))

    # Validate against schema
    validate.validate(env, "meta")

    # Parse PR number if present
    pr_number = None
    if env.get("PR_NUMBER"):
        try:
            pr_number = int(env["PR_NUMBER"])
        except ValueError:
            pass

    return Workstream(
        id=env["ID"],
        title=env["TITLE"],
        branch=env["BRANCH"],
        worktree=Path(env["WORKTREE"]),
        base_branch=env["BASE_BRANCH"],
        base_sha=env["BASE_SHA"],
        status=env.get("STATUS", "active"),
        dir=workstream_dir,
        pr_url=env.get("PR_URL"),
        pr_number=pr_number,
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
