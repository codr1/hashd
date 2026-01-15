"""
Configuration loaders for AOS.

Loads project and workstream configuration from .env files.
"""

import fnmatch
import json
import logging
import shlex
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
    """Build/test configuration from project_profile.env

    Supports two config styles:
    1. New (command-based): TEST_CMD="npm test", BUILD_CMD="npm run build"
    2. Legacy (target-based): BUILD_RUNNER=make, TEST_TARGET=test
    """
    # New command-based fields (preferred)
    test_cmd: str  # Full command to run tests, e.g., "make test", "npm test", "pytest"
    build_cmd: str  # Full command to build (optional), e.g., "make build", "npm run build"
    merge_gate_test_cmd: str  # Full command for merge gate tests (defaults to test_cmd)

    # Legacy fields (kept for backward compatibility)
    build_runner: str  # "make" (default) or "task" - DEPRECATED
    makefile_path: str  # For make: path to Makefile - DEPRECATED
    build_target: str  # Target to compile (e.g., "build") - DEPRECATED
    test_target: str  # Target to run tests (e.g., "test") - DEPRECATED
    merge_gate_test_target: str  # Target for full test suite - DEPRECATED

    # Timeouts and modes
    implement_timeout: int
    review_timeout: int
    test_timeout: int
    breakdown_timeout: int
    merge_mode: str  # "local" or "github_pr"
    # Note: autonomy mode (supervised/gatekeeper/autonomous) is in escalation.json, not here

    def get_test_command(self) -> list[str]:
        """Get the command to run tests.

        Returns command as list for subprocess.
        Raises ValueError if test_cmd is not configured.
        """
        if not self.test_cmd:
            raise ValueError(
                "No test command configured. Run 'wf interview' to set TEST_CMD."
            )
        return shlex.split(self.test_cmd)

    def get_build_command_str(self) -> list[str] | None:
        """Get the command to run build (if configured).

        Returns None if no build command is configured.
        """
        if not self.build_cmd:
            return None
        return shlex.split(self.build_cmd)

    def get_merge_gate_test_command(self) -> list[str]:
        """Get the command to run merge gate tests.

        Raises ValueError if merge_gate_test_cmd is not configured.
        """
        if not self.merge_gate_test_cmd:
            raise ValueError(
                "No merge gate test command configured. Run 'wf interview' to set MERGE_GATE_TEST_CMD."
            )
        return shlex.split(self.merge_gate_test_cmd)

    def get_build_file(self, worktree: Path) -> Path:
        """Get the build file path for this runner (legacy support).

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
        """Get the command to run a build target (legacy support)."""
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

    @classmethod
    def default(cls) -> "ProjectProfile":
        """Return a default ProjectProfile for when project_profile.env is missing."""
        return cls(
            test_cmd="make test",
            build_cmd="make build",
            merge_gate_test_cmd="make test",
            build_runner="make",
            makefile_path="Makefile",
            build_target="build",
            test_target="test",
            merge_gate_test_target="test",
            implement_timeout=1200,
            review_timeout=900,
            test_timeout=300,
            breakdown_timeout=180,
            merge_mode="local",
        )


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
    codex_session_id: str | None = None  # Codex session UUID for resume


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
    """Load project_profile.env and return ProjectProfile.

    Supports two config styles:
    1. New (command-based): TEST_CMD, BUILD_CMD, MERGE_GATE_TEST_CMD
    2. Legacy (target-based): BUILD_RUNNER, TEST_TARGET, etc.

    Legacy configs are automatically converted to command format.
    """
    env = envparse.load_env(str(project_dir / "project_profile.env"))

    # Validate merge mode - default based on environment
    from orchestrator.lib.github import get_default_merge_mode, VALID_MERGE_MODES
    merge_mode = env.get("MERGE_MODE") or get_default_merge_mode()
    if merge_mode not in VALID_MERGE_MODES:
        logger.warning(
            f"Unknown MERGE_MODE '{merge_mode}', defaulting to 'local'. "
            f"Valid modes: {', '.join(sorted(VALID_MERGE_MODES))}"
        )
        merge_mode = "local"

    # Determine config style and build commands
    if "TEST_CMD" in env:
        # New command-based style
        test_cmd = env["TEST_CMD"]
        build_cmd = env.get("BUILD_CMD", "")
        merge_gate_test_cmd = env.get("MERGE_GATE_TEST_CMD", test_cmd)
        # Legacy fields get defaults (not used but required for dataclass)
        build_runner = "make"
        makefile_path = "Makefile"
        build_target = "build"
        test_target = "test"
        merge_gate_test_target = "test"
    else:
        # Legacy target-based style - convert to commands
        build_runner = env.get("BUILD_RUNNER", "make")
        if build_runner not in VALID_BUILD_RUNNERS:
            logger.warning(
                f"Unknown BUILD_RUNNER '{build_runner}', defaulting to 'make'. "
                f"Valid runners: {', '.join(sorted(VALID_BUILD_RUNNERS))}"
            )
            build_runner = "make"

        makefile_path = env.get("MAKEFILE_PATH", "Makefile")
        build_target = env.get("BUILD_TARGET", "build")
        test_target = env.get("TEST_TARGET") or env.get("MAKE_TARGET_TEST", "test")
        merge_gate_test_target = env.get("MERGE_GATE_TEST_TARGET") or env.get("MAKE_TARGET_MERGE_GATE_TEST", test_target)

        # Convert to command format
        if build_runner == "task":
            test_cmd = f"task {test_target}"
            build_cmd = f"task {build_target}" if build_target else ""
            merge_gate_test_cmd = f"task {merge_gate_test_target}"
        else:  # make
            test_cmd = f"make {test_target}"
            build_cmd = f"make {build_target}" if build_target else ""
            merge_gate_test_cmd = f"make {merge_gate_test_target}"

    return ProjectProfile(
        # New command-based fields
        test_cmd=test_cmd,
        build_cmd=build_cmd,
        merge_gate_test_cmd=merge_gate_test_cmd,
        # Legacy fields (for backward compat)
        build_runner=build_runner,
        makefile_path=makefile_path,
        build_target=build_target,
        test_target=test_target,
        merge_gate_test_target=merge_gate_test_target,
        # Timeouts and modes
        implement_timeout=int(env.get("IMPLEMENT_TIMEOUT", "1200")),  # 20 min default
        review_timeout=int(env.get("REVIEW_TIMEOUT", "900")),  # Increased from 600 - large changes need more time
        test_timeout=int(env.get("TEST_TIMEOUT", "300")),
        breakdown_timeout=int(env.get("BREAKDOWN_TIMEOUT", "180")),
        merge_mode=merge_mode,
        # Note: SUPERVISED_MODE is deprecated - autonomy is now in escalation.json
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
            logger.debug(f"Invalid PR_NUMBER '{env['PR_NUMBER']}' in {workstream_dir}")

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
        codex_session_id=env.get("CODEX_SESSION_ID") or None,
    )


def _escape_env_value(value: str) -> str:
    """Escape a value for safe inclusion in a shell-style env file.

    Escapes backslashes, double quotes, and newlines.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def update_workstream_meta(workstream_dir: Path, updates: dict[str, str | None]) -> None:
    """Update fields in meta.env.

    Args:
        workstream_dir: Path to workstream directory
        updates: Dict of field_name -> value. If value is None, the field is removed.

    Example:
        update_workstream_meta(ws_dir, {"CODEX_SESSION_ID": "abc-123"})
        update_workstream_meta(ws_dir, {"CODEX_SESSION_ID": None})  # removes field
    """
    meta_path = workstream_dir / "meta.env"
    if not meta_path.exists():
        logger.warning(f"meta.env not found at {meta_path}")
        return

    content = meta_path.read_text()
    lines = content.splitlines()

    # Track which updates we've applied (to append new fields)
    applied = set()

    # Update existing fields
    new_lines = []
    for line in lines:
        field_name = None
        for key in updates:
            if line.startswith(f"{key}="):
                field_name = key
                break

        if field_name:
            value = updates[field_name]
            applied.add(field_name)
            if value is not None:
                # Update the field with escaped value
                new_lines.append(f'{field_name}="{_escape_env_value(value)}"')
            # If value is None, don't add the line (removes the field)
        else:
            new_lines.append(line)

    # Append new fields that weren't in the file
    for key, value in updates.items():
        if key not in applied and value is not None:
            new_lines.append(f'{key}="{_escape_env_value(value)}"')

    meta_path.write_text("\n".join(new_lines) + "\n")


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


# Autonomy modes
AUTONOMY_MODES = {"supervised", "gatekeeper", "autonomous"}


@dataclass
class SensitivePathsConfig:
    """Configuration for sensitive path detection."""
    patterns: list[str]  # Glob patterns like "**/auth/**", "**/*.env*"
    threshold_boost: float  # How much to increase threshold for sensitive paths


@dataclass
class EscalationConfig:
    """Escalation configuration from escalation.json.

    Controls autonomy level and confidence thresholds for auto-continue decisions.
    """
    autonomy: str  # "supervised", "gatekeeper", or "autonomous"
    commit_confidence_threshold: float  # Threshold for auto-continuing commits (e.g., 0.7)
    merge_confidence_threshold: float  # Threshold for auto-merging (e.g., 0.8)
    sensitive_paths: SensitivePathsConfig | None  # Optional path-based threshold boost


def load_escalation_config(project_dir: Path) -> EscalationConfig:
    """Load escalation.json and return EscalationConfig.

    If file doesn't exist, returns sensible defaults.
    """
    config_path = project_dir / "escalation.json"

    # Defaults
    defaults = EscalationConfig(
        autonomy="gatekeeper",
        commit_confidence_threshold=0.7,
        merge_confidence_threshold=0.8,
        sensitive_paths=SensitivePathsConfig(
            patterns=["**/auth/**", "**/*.env*", "**/security/**", "**/migrations/**"],
            threshold_boost=0.15,
        ),
    )

    if not config_path.exists():
        return defaults

    try:
        data = json.loads(config_path.read_text())

        autonomy = data.get("autonomy", "gatekeeper")
        if autonomy not in AUTONOMY_MODES:
            logger.warning(f"Unknown autonomy mode '{autonomy}', defaulting to 'gatekeeper'")
            autonomy = "gatekeeper"

        sensitive_paths = None
        if "sensitive_paths" in data:
            sp = data["sensitive_paths"]
            sensitive_paths = SensitivePathsConfig(
                patterns=sp.get("patterns", []),
                threshold_boost=sp.get("threshold_boost", 0.15),
            )

        return EscalationConfig(
            autonomy=autonomy,
            commit_confidence_threshold=data.get("commit_confidence_threshold", 0.7),
            merge_confidence_threshold=data.get("merge_confidence_threshold", 0.8),
            sensitive_paths=sensitive_paths,
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Failed to parse escalation.json: {e}, using defaults")
        return defaults


def get_confidence_threshold(
    changed_files: list[str],
    config: EscalationConfig,
    for_merge: bool = False,
) -> float:
    """Calculate effective confidence threshold based on changed files.

    Args:
        changed_files: List of file paths that were changed
        config: Escalation config with thresholds and sensitive patterns
        for_merge: If True, use merge threshold; otherwise use commit threshold

    Returns:
        Effective threshold (base + boost if sensitive paths touched)
    """
    base_threshold = config.merge_confidence_threshold if for_merge else config.commit_confidence_threshold

    if not config.sensitive_paths or not config.sensitive_paths.patterns:
        return base_threshold

    # Check if any changed file matches sensitive patterns
    for file_path in changed_files:
        for pattern in config.sensitive_paths.patterns:
            if fnmatch.fnmatch(file_path, pattern):
                boosted = base_threshold + config.sensitive_paths.threshold_boost
                # Cap at 1.0
                return min(boosted, 1.0)

    return base_threshold
