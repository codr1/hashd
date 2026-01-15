"""
wf project - Project management commands.
"""

from pathlib import Path

from orchestrator.lib.config import ProjectConfig, load_project_profile


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
        print(f"  Makefile:        {profile.makefile_path}")
        print(f"  Test target:     {profile.make_target_test}")
        print(f"  Merge gate test: {profile.merge_gate_test_target}")
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
