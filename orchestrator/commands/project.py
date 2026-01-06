"""
wf project - Project management commands.
"""

from pathlib import Path

from orchestrator.lib.config import ProjectConfig, load_project_profile


def get_projects_dir(ops_dir: Path) -> Path:
    """Get the projects directory."""
    return ops_dir / "projects"


def get_current_project_file(ops_dir: Path) -> Path:
    """Get the path to the current project context file."""
    return ops_dir / "config" / "current_project"


def get_current_project(ops_dir: Path) -> str | None:
    """Get the current project name, or None if not set."""
    context_file = get_current_project_file(ops_dir)
    if context_file.exists():
        name = context_file.read_text().strip()
        if name and (get_projects_dir(ops_dir) / name).exists():
            return name
    return None


def set_current_project(ops_dir: Path, name: str) -> None:
    """Set the current project context."""
    context_file = get_current_project_file(ops_dir)
    context_file.parent.mkdir(parents=True, exist_ok=True)
    context_file.write_text(name + "\n")


def list_projects(ops_dir: Path) -> list[str]:
    """List all registered projects."""
    projects_dir = get_projects_dir(ops_dir)
    if not projects_dir.exists():
        return []
    return sorted([
        d.name for d in projects_dir.iterdir()
        if d.is_dir() and (d / "project.env").exists()
    ])


def cmd_project_add(args, ops_dir: Path) -> int:
    """Register a new project and run interview.

    Usage:
        wf project add /path/to/repo [--no-interview]
    """
    from orchestrator.commands.interview import (
        run_interview, write_config_files, detect_project_name, detect_build_system
    )

    repo_path = Path(args.path).resolve()

    # Validate repo path
    if not repo_path.exists():
        print(f"ERROR: Path does not exist: {repo_path}")
        return 2
    if not repo_path.is_dir():
        print(f"ERROR: Path is not a directory: {repo_path}")
        return 2

    # Detect project name
    project_name = detect_project_name(repo_path)
    projects_dir = get_projects_dir(ops_dir)
    project_dir = projects_dir / project_name

    # Check if already exists
    if project_dir.exists():
        print(f"ERROR: Project '{project_name}' already exists.")
        print(f"  Use 'wf interview' to update its configuration.")
        return 2

    if args.no_interview:
        # Quick registration without interview
        project_dir.mkdir(parents=True, exist_ok=True)

        # Detect build system for defaults
        detection = detect_build_system(repo_path)
        test_cmd = detection.test_cmd or ""
        build_cmd = detection.build_cmd or ""

        # Write minimal project.env
        project_env = project_dir / "project.env"
        project_env.write_text(f"""# Project configuration
PROJECT_NAME="{project_name}"
REPO_PATH="{repo_path}"
DEFAULT_BRANCH="main"
REQS_PATH="REQS.md"
""")
        print(f"Writing {project_env}... done")

        # Write minimal project_profile.env
        profile_env = project_dir / "project_profile.env"
        profile_env.write_text(f"""# Build and test configuration
TEST_CMD="{test_cmd}"
BUILD_CMD="{build_cmd}"
MERGE_GATE_TEST_CMD="{test_cmd}"
MERGE_MODE="local"
SUPERVISED_MODE="false"
""")
        print(f"Writing {profile_env}... done")

        print(f"\nProject '{project_name}' registered (minimal config).")
        if not test_cmd:
            print(f"WARNING: No build system detected. Run 'wf interview' to configure test commands.")
    else:
        # Run full interview with existing project names as reserved
        existing_projects = set(list_projects(ops_dir))
        config = run_interview(repo_path, None, reserved_names=existing_projects)

        # Update project name from interview (user may have changed it)
        project_name = config.project_name
        project_dir = projects_dir / project_name

        print()
        write_config_files(project_dir, config)
        print(f"\nProject '{project_name}' registered successfully.")

    # Set as current project
    set_current_project(ops_dir, project_name)
    print(f"Set as current project. Run: wf project show")

    return 0


def cmd_project_list(args, ops_dir: Path) -> int:
    """List all registered projects."""
    projects = list_projects(ops_dir)
    current = get_current_project(ops_dir)

    if not projects:
        print("No projects registered.")
        print("  Use 'wf project add <path>' to register a project.")
        return 0

    print("Registered projects:")
    for name in projects:
        marker = "*" if name == current else " "
        print(f"  {marker} {name}")

    if current:
        print(f"\n* = current project")

    return 0


def cmd_project_use(args, ops_dir: Path) -> int:
    """Set the active project context."""
    name = args.name
    projects_dir = get_projects_dir(ops_dir)
    project_dir = projects_dir / name

    if not project_dir.exists():
        print(f"ERROR: Project '{name}' not found.")
        available = list_projects(ops_dir)
        if available:
            print(f"  Available projects: {', '.join(available)}")
        else:
            print("  No projects registered. Use 'wf project add <path>' first.")
        return 2

    set_current_project(ops_dir, name)
    print(f"Switched to project: {name}")
    return 0


def cmd_project_show(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Display current project configuration."""
    project_dir = ops_dir / "projects" / project_config.name

    print(f"Project: {project_config.name}")
    print("=" * 60)

    # Basic config
    print()
    print("Configuration (project.env)")
    print("-" * 40)
    print(f"  Repo path:       {project_config.repo_path}")
    print(f"  Default branch:  {project_config.default_branch}")
    print(f"  Requirements:    {project_config.reqs_path}")

    # Profile
    try:
        profile = load_project_profile(project_dir)
        print()
        print("Profile (project_profile.env)")
        print("-" * 40)
        print(f"  Test command:    {profile.test_cmd}")
        if profile.build_cmd:
            print(f"  Build command:   {profile.build_cmd}")
        print(f"  Merge gate test: {profile.merge_gate_test_cmd}")
        print(f"  Merge mode:      {profile.merge_mode}")
        print()
        print("  Timeouts:")
        print(f"    Implement:     {profile.implement_timeout}s")
        print(f"    Review:        {profile.review_timeout}s")
        print(f"    Test:          {profile.test_timeout}s")
        print(f"    Breakdown:     {profile.breakdown_timeout}s")
        print()
        mode = "supervised" if profile.supervised_mode else "gatekeeper"
        print(f"  Autonomy mode:   {mode}")
    except FileNotFoundError:
        print()
        print("Profile (project_profile.env)")
        print("-" * 40)
        print("  (not configured - using defaults)")

    # Count stories (project-specific)
    stories_dir = project_dir / "pm" / "stories"
    if stories_dir.exists():
        story_files = list(stories_dir.glob("STORY-*.json"))
        print()
        print("Stories")
        print("-" * 40)
        print(f"  Total: {len(story_files)}")

    return 0
