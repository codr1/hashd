"""
wf interview - Interactive project configuration wizard.

Detects build systems and prompts user to configure project settings.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DetectionResult:
    """Result of build system detection."""
    test_cmd: str | None = None
    build_cmd: str | None = None
    detected_system: str | None = None  # e.g., "Makefile", "package.json"


@dataclass
class InterviewConfig:
    """Configuration collected during interview."""
    project_name: str
    repo_path: Path
    default_branch: str
    test_cmd: str
    build_cmd: str
    merge_gate_test_cmd: str
    reqs_path: str
    merge_mode: str
    autonomy: str  # "supervised", "gatekeeper", or "autonomous"
    commit_confidence_threshold: float = 0.7
    merge_confidence_threshold: float = 0.8
    description: str = ""
    tech_preferred: str = ""
    tech_acceptable: str = ""
    tech_avoid: str = ""

    @property
    def supervised_mode(self) -> bool:
        """Backward compatibility: True if autonomy is 'supervised'."""
        return self.autonomy == "supervised"


def _safe_read(path: Path) -> str | None:
    """Read file contents, returning None on any error."""
    try:
        return path.read_text()
    except (OSError, IOError):
        return None


def detect_build_system(repo_path: Path) -> DetectionResult:
    """Detect build system and suggest test/build commands.

    Checks for common build system files in order of specificity.
    """
    result = DetectionResult()

    # Check for Makefile with test target
    makefile = repo_path / "Makefile"
    content = _safe_read(makefile)
    if content:
        if re.search(r'^test\s*:', content, re.MULTILINE):
            result.test_cmd = "make test"
            result.detected_system = "Makefile"
        if re.search(r'^build\s*:', content, re.MULTILINE):
            result.build_cmd = "make build"
        if result.test_cmd:
            return result

    # Check for Taskfile.yml
    for taskfile_name in ["Taskfile.yml", "Taskfile.yaml", "taskfile.yml"]:
        taskfile = repo_path / taskfile_name
        content = _safe_read(taskfile)
        if content:
            if "test:" in content:
                result.test_cmd = "task test"
                result.detected_system = taskfile_name
            if "build:" in content:
                result.build_cmd = "task build"
            if result.test_cmd:
                return result

    # Check for package.json (Node.js)
    package_json = repo_path / "package.json"
    content = _safe_read(package_json)
    if content:
        try:
            pkg = json.loads(content)
            scripts = pkg.get("scripts", {})
            if "test" in scripts:
                result.test_cmd = "npm test"
                result.detected_system = "package.json"
            if "build" in scripts:
                result.build_cmd = "npm run build"
            if result.test_cmd:
                return result
        except json.JSONDecodeError:
            pass

    # Check for pyproject.toml (Python)
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        result.test_cmd = "pytest"
        result.detected_system = "pyproject.toml"
        return result

    # Check for setup.py (Python legacy)
    setup_py = repo_path / "setup.py"
    if setup_py.exists():
        result.test_cmd = "pytest"
        result.detected_system = "setup.py"
        return result

    # Check for Cargo.toml (Rust)
    cargo = repo_path / "Cargo.toml"
    if cargo.exists():
        result.test_cmd = "cargo test"
        result.build_cmd = "cargo build"
        result.detected_system = "Cargo.toml"
        return result

    # Check for go.mod (Go)
    go_mod = repo_path / "go.mod"
    if go_mod.exists():
        result.test_cmd = "go test ./..."
        result.build_cmd = "go build ./..."
        result.detected_system = "go.mod"
        return result

    # Check for pom.xml (Maven/Java)
    pom = repo_path / "pom.xml"
    if pom.exists():
        result.test_cmd = "mvn test"
        result.build_cmd = "mvn compile"
        result.detected_system = "pom.xml"
        return result

    # Check for build.gradle (Gradle/Java)
    gradle = repo_path / "build.gradle"
    if not gradle.exists():
        gradle = repo_path / "build.gradle.kts"
    if gradle.exists():
        result.test_cmd = "./gradlew test"
        result.build_cmd = "./gradlew build"
        result.detected_system = gradle.name
        return result

    return result


def detect_project_name(repo_path: Path) -> str:
    """Detect project name from git remote or directory name."""
    # Try git remote
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Extract repo name from URL
            # Handle: git@github.com:user/repo.git or https://github.com/user/repo.git
            match = re.search(r'[/:]([^/]+?)(?:\.git)?$', url)
            if match:
                return match.group(1)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fall back to directory name
    return repo_path.name


def detect_default_branch(repo_path: Path) -> str:
    """Detect default branch from git."""
    # Try to get the default branch from remote HEAD
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # refs/remotes/origin/main -> main
            ref = result.stdout.strip()
            return ref.split("/")[-1]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Try common branch names
    for branch in ["main", "master"]:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "--verify", f"refs/heads/{branch}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return branch
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    return "main"


def prompt(message: str, default: str = "") -> str:
    """Prompt user for input with optional default."""
    if default:
        display = f"{message} [{default}]: "
    else:
        display = f"{message}: "

    try:
        value = input(display).strip()
        return value if value else default
    except EOFError:
        return default


def prompt_choice(message: str, choices: list[tuple[str, str]], default: int = 1) -> str:
    """Prompt user to select from numbered choices.

    Args:
        message: Prompt message
        choices: List of (value, description) tuples
        default: 1-indexed default choice

    Returns:
        Selected value
    """
    print(f"\n{message}")
    for i, (value, desc) in enumerate(choices, 1):
        marker = "*" if i == default else " "
        print(f"  {marker}{i}. {desc}")

    while True:
        try:
            selection = input(f"Select [1-{len(choices)}, default={default}]: ").strip()
            if not selection:
                return choices[default - 1][0]
            idx = int(selection)
            if 1 <= idx <= len(choices):
                return choices[idx - 1][0]
            print(f"Please enter a number between 1 and {len(choices)}")
        except ValueError:
            print("Please enter a valid number")
        except EOFError:
            return choices[default - 1][0]


def prompt_bool(message: str, default: bool = False) -> bool:
    """Prompt user for yes/no answer."""
    default_str = "Y/n" if default else "y/N"
    try:
        value = input(f"{message} [{default_str}]: ").strip().lower()
        if not value:
            return default
        return value in ("y", "yes", "true", "1")
    except EOFError:
        return default


def run_interview(
    repo_path: Path,
    existing_config: InterviewConfig | None = None,
    reserved_names: set[str] | None = None,
) -> InterviewConfig:
    """Run interactive interview to configure project.

    Args:
        repo_path: Path to the project repository
        existing_config: Existing config to use as defaults (for updates)
        reserved_names: Project names that are already taken (for validation)

    Returns:
        InterviewConfig with user's choices
    """
    print("\n=== HASHD Project Setup ===\n")

    # Detect values
    detection = detect_build_system(repo_path)
    detected_name = detect_project_name(repo_path)
    detected_branch = detect_default_branch(repo_path)

    # Use existing config as defaults if provided
    defaults = existing_config or InterviewConfig(
        project_name=detected_name,
        repo_path=repo_path,
        default_branch=detected_branch,
        test_cmd=detection.test_cmd or "",
        build_cmd=detection.build_cmd or "",
        merge_gate_test_cmd=detection.test_cmd or "",
        reqs_path="REQS.md",
        merge_mode="local",
        autonomy="gatekeeper",
    )

    # Basic info - validate project name isn't taken
    reserved = reserved_names or set()
    while True:
        project_name = prompt("Project name", defaults.project_name)
        if project_name not in reserved:
            break
        print(f"  ERROR: Project '{project_name}' already exists. Choose a different name.")
    default_branch = prompt("Default branch", defaults.default_branch)

    # Build system detection feedback
    if detection.detected_system:
        print(f"\nDetected: {detection.detected_system}")
        if detection.test_cmd:
            print(f"  Test command: {detection.test_cmd}")
        if detection.build_cmd:
            print(f"  Build command: {detection.build_cmd}")
    else:
        print("\nNo build system detected. Please specify commands manually.")

    # Commands
    print()
    test_cmd = prompt("Test command", defaults.test_cmd)
    build_cmd = prompt("Build command (optional, press Enter to skip)", defaults.build_cmd)
    merge_gate_test_cmd = prompt("Merge gate test command", test_cmd)

    # Paths
    print()
    reqs_path = prompt("Requirements file path", defaults.reqs_path)

    # Merge mode
    merge_mode = prompt_choice(
        "Merge mode:",
        [
            ("local", "local - merge directly to main branch"),
            ("github_pr", "github_pr - create PR, merge via GitHub"),
        ],
        default=1 if defaults.merge_mode == "local" else 2,
    )

    # Autonomy mode
    autonomy_default = {"supervised": 1, "gatekeeper": 2, "autonomous": 3}.get(
        defaults.autonomy, 2
    )
    autonomy = prompt_choice(
        "Autonomy mode:",
        [
            ("supervised", "supervised - pause for human review after every commit"),
            ("gatekeeper", "gatekeeper - auto-continue if AI confidence >= 70%"),
            ("autonomous", "autonomous - auto-continue commits + auto-merge"),
        ],
        default=autonomy_default,
    )

    return InterviewConfig(
        project_name=project_name,
        repo_path=repo_path,
        default_branch=default_branch,
        test_cmd=test_cmd,
        build_cmd=build_cmd,
        merge_gate_test_cmd=merge_gate_test_cmd,
        reqs_path=reqs_path,
        merge_mode=merge_mode,
        autonomy=autonomy,
        commit_confidence_threshold=defaults.commit_confidence_threshold,
        merge_confidence_threshold=defaults.merge_confidence_threshold,
        description=defaults.description,
        tech_preferred=defaults.tech_preferred,
        tech_acceptable=defaults.tech_acceptable,
        tech_avoid=defaults.tech_avoid,
    )


def write_config_files(project_dir: Path, config: InterviewConfig) -> None:
    """Write project.env, project_profile.env, and escalation.json files.

    Args:
        project_dir: Directory to write config files to (projects/<name>/)
        config: Configuration to write
    """
    project_dir.mkdir(parents=True, exist_ok=True)

    # Write project.env
    project_env = project_dir / "project.env"
    project_env_content = f"""# Project configuration
PROJECT_NAME="{config.project_name}"
REPO_PATH="{config.repo_path}"
DEFAULT_BRANCH="{config.default_branch}"
REQS_PATH="{config.reqs_path}"
PROJECT_DESCRIPTION="{config.description}"
TECH_PREFERRED="{config.tech_preferred}"
TECH_ACCEPTABLE="{config.tech_acceptable}"
TECH_AVOID="{config.tech_avoid}"
"""
    project_env.write_text(project_env_content)
    print(f"Writing {project_env}... done")

    # Write project_profile.env
    profile_env = project_dir / "project_profile.env"
    profile_env_content = f"""# Build and test configuration
TEST_CMD="{config.test_cmd}"
BUILD_CMD="{config.build_cmd}"
MERGE_GATE_TEST_CMD="{config.merge_gate_test_cmd}"

# Merge settings
MERGE_MODE="{config.merge_mode}"

# Timeouts (seconds)
IMPLEMENT_TIMEOUT="1200"
REVIEW_TIMEOUT="900"
TEST_TIMEOUT="300"
BREAKDOWN_TIMEOUT="180"
"""
    profile_env.write_text(profile_env_content)
    print(f"Writing {profile_env}... done")

    # Write escalation.json
    escalation_file = project_dir / "escalation.json"
    escalation_config = {
        "autonomy": config.autonomy,
        "commit_confidence_threshold": config.commit_confidence_threshold,
        "merge_confidence_threshold": config.merge_confidence_threshold,
        "sensitive_paths": {
            "patterns": ["**/auth/**", "**/*.env*", "**/security/**", "**/migrations/**"],
            "threshold_boost": 0.15,
        },
    }
    escalation_file.write_text(json.dumps(escalation_config, indent=2) + "\n")
    print(f"Writing {escalation_file}... done")

    # Ensure Codex trusts the worktrees directory
    # project_dir is ops_dir/projects/<name>, so ops_dir is parent.parent
    ops_dir = project_dir.parent.parent
    _ensure_codex_worktrees_trust(ops_dir)


def _ensure_codex_worktrees_trust(ops_dir: Path) -> None:
    """Add worktrees directory to Codex trust config if not already present.

    Codex requires directories to be explicitly trusted in ~/.codex/config.toml.
    Git worktrees use a .git file instead of .git directory, so Codex doesn't
    recognize them as part of a trusted repo without explicit configuration.
    """
    worktrees_dir = ops_dir / "worktrees"
    worktrees_dir.mkdir(exist_ok=True)

    codex_config_path = Path.home() / ".codex" / "config.toml"
    codex_config_path.parent.mkdir(exist_ok=True)

    # Read existing config
    existing_content = ""
    if codex_config_path.exists():
        existing_content = codex_config_path.read_text()

    # Check if worktrees path is already trusted
    trust_entry = f'[projects."{worktrees_dir}"]'
    if trust_entry in existing_content:
        print(f"Codex trust for {worktrees_dir}... already configured")
        return

    # Append trust entry
    new_entry = f'\n{trust_entry}\ntrust_level = "trusted"\n'
    codex_config_path.write_text(existing_content.rstrip() + new_entry)
    print(f"Codex trust for {worktrees_dir}... added")


def cmd_interview(args, ops_dir: Path) -> int:
    """Run interview to update existing project config."""
    # Load existing project config if available
    try:
        from orchestrator.lib.config import (
            load_project_config,
            load_project_profile,
            load_escalation_config,
        )
        project_config = load_project_config(ops_dir)
        profile = load_project_profile(ops_dir)
        project_dir = ops_dir / "projects" / project_config.name
        escalation = load_escalation_config(project_dir)

        existing = InterviewConfig(
            project_name=project_config.name,
            repo_path=project_config.repo_path,
            default_branch=project_config.default_branch,
            test_cmd=profile.test_cmd,
            build_cmd=profile.build_cmd,
            merge_gate_test_cmd=profile.merge_gate_test_cmd,
            reqs_path=project_config.reqs_path,
            merge_mode=profile.merge_mode,
            autonomy=escalation.autonomy,
            commit_confidence_threshold=escalation.commit_confidence_threshold,
            merge_confidence_threshold=escalation.merge_confidence_threshold,
            description=project_config.description,
            tech_preferred=project_config.tech_preferred,
            tech_acceptable=project_config.tech_acceptable,
            tech_avoid=project_config.tech_avoid,
        )
        repo_path = project_config.repo_path
    except Exception as e:
        print(f"ERROR: No project configured. Use 'wf project add <path>' first.")
        print(f"  ({e})")
        return 2

    # Run interview (don't allow name changes for existing projects)
    config = run_interview(repo_path, existing)

    # Validate name wasn't changed (would orphan old directory)
    if config.project_name != project_config.name:
        print(f"ERROR: Cannot rename project via interview.")
        print(f"  Current name: {project_config.name}")
        print(f"  To rename, manually rename the project directory in projects/")
        return 2

    # Write config files to the project directory
    print()
    project_dir = ops_dir / "projects" / config.project_name
    write_config_files(project_dir, config)

    print(f"\nProject '{config.project_name}' updated successfully.")
    return 0
