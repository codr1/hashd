#!/usr/bin/env python3
"""AOS CLI entrypoint."""

import sys
import argparse
from pathlib import Path

from orchestrator.lib.config import (
    load_project_config,
    get_current_workstream,
    set_current_workstream,
    clear_current_workstream,
)
from orchestrator.commands import new as cmd_new_module
from orchestrator.commands import list as cmd_list_module
from orchestrator.commands import refresh as cmd_refresh_module
from orchestrator.commands import status as cmd_status_module
from orchestrator.commands import conflicts as cmd_conflicts_module
from orchestrator.commands import close as cmd_close_module
from orchestrator.commands import merge as cmd_merge_module
from orchestrator.commands import archive as cmd_archive_module
from orchestrator.commands import run as cmd_run_module
from orchestrator.commands import approve as cmd_approve_module
from orchestrator.commands import show as cmd_show_module
from orchestrator.commands import review as cmd_review_module
from orchestrator.commands import clarify as cmd_clarify_module
from orchestrator.commands import pm as cmd_pm_module
from orchestrator.commands import open as cmd_open_module


def get_ops_dir() -> Path:
    """Get the ops directory (where this CLI lives)."""
    return Path(__file__).parent.parent


def get_project_config(args):
    """Load project config from --project or default."""
    ops_dir = get_ops_dir()
    projects_dir = ops_dir / "projects"

    if args.project:
        project_dir = projects_dir / args.project
    else:
        # Find single project or error
        projects = [d for d in projects_dir.iterdir() if d.is_dir() and (d / "project.env").exists()]
        if len(projects) == 0:
            print("ERROR: No projects configured. Create projects/<name>/project.env")
            sys.exit(2)
        elif len(projects) > 1:
            print(f"ERROR: Multiple projects found. Use --project to specify one:")
            for p in projects:
                print(f"  {p.name}")
            sys.exit(2)
        project_dir = projects[0]

    return load_project_config(project_dir), ops_dir


def resolve_workstream_id(args, ops_dir: Path) -> str:
    """Resolve workstream ID from args or current context."""
    # Use explicit ID if provided
    ws_id = getattr(args, 'id', None)
    if ws_id:
        return ws_id

    # Fall back to current context
    current = get_current_workstream(ops_dir)
    if current:
        return current

    print("ERROR: No workstream specified. Use 'wf use <id>' to set current workstream.")
    sys.exit(2)


def cmd_new(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_new_module.cmd_new(args, ops_dir, project_config)


def cmd_use(args):
    """Set, show, or clear the current workstream context."""
    ops_dir = get_ops_dir()

    # Clear context
    if args.clear:
        clear_current_workstream(ops_dir)
        print("Cleared current workstream context.")
        return 0

    # Show current context
    if not args.id:
        current = get_current_workstream(ops_dir)
        if current:
            print(f"Current workstream: {current}")
        else:
            print("No current workstream set. Use 'wf use <id>' to set one.")
        return 0

    # Set context - validate workstream exists first
    ws_dir = ops_dir / "workstreams" / args.id
    if not ws_dir.exists():
        print(f"ERROR: Workstream '{args.id}' not found.")
        return 1

    set_current_workstream(ops_dir, args.id)
    print(f"Now using workstream: {args.id}")
    return 0


def cmd_list(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_list_module.cmd_list(args, ops_dir, project_config)


def cmd_refresh(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_refresh_module.cmd_refresh(args, ops_dir, project_config)


def cmd_status(args):
    project_config, ops_dir = get_project_config(args)
    args.id = resolve_workstream_id(args, ops_dir)
    return cmd_status_module.cmd_status(args, ops_dir, project_config)


def cmd_conflicts(args):
    project_config, ops_dir = get_project_config(args)
    args.id = resolve_workstream_id(args, ops_dir)
    return cmd_conflicts_module.cmd_conflicts(args, ops_dir, project_config)


def cmd_run(args):
    project_config, ops_dir = get_project_config(args)
    args.id = resolve_workstream_id(args, ops_dir)
    return cmd_run_module.cmd_run(args, ops_dir, project_config)


def cmd_close(args):
    project_config, ops_dir = get_project_config(args)
    args.id = resolve_workstream_id(args, ops_dir)
    return cmd_close_module.cmd_close(args, ops_dir, project_config)


def cmd_merge(args):
    project_config, ops_dir = get_project_config(args)
    args.id = resolve_workstream_id(args, ops_dir)
    return cmd_merge_module.cmd_merge(args, ops_dir, project_config)


def cmd_archive(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_archive_module.cmd_archive(args, ops_dir, project_config)


def cmd_archive_delete(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_archive_module.cmd_archive_delete(args, ops_dir, project_config)


def cmd_open(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_open_module.cmd_open(args, ops_dir, project_config)


def cmd_approve(args):
    project_config, ops_dir = get_project_config(args)
    args.id = resolve_workstream_id(args, ops_dir)
    return cmd_approve_module.cmd_approve(args, ops_dir, project_config)


def cmd_reject(args):
    project_config, ops_dir = get_project_config(args)
    args.id = resolve_workstream_id(args, ops_dir)
    return cmd_approve_module.cmd_reject(args, ops_dir, project_config)


def cmd_reset(args):
    project_config, ops_dir = get_project_config(args)
    args.id = resolve_workstream_id(args, ops_dir)
    return cmd_approve_module.cmd_reset(args, ops_dir, project_config)


def cmd_show(args):
    project_config, ops_dir = get_project_config(args)
    args.id = resolve_workstream_id(args, ops_dir)
    return cmd_show_module.cmd_show(args, ops_dir, project_config)


def cmd_review(args):
    project_config, ops_dir = get_project_config(args)
    args.id = resolve_workstream_id(args, ops_dir)
    return cmd_review_module.cmd_review(args, ops_dir, project_config)


def cmd_clarify_list(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_clarify_module.cmd_clarify_list(args, ops_dir, project_config)


def cmd_clarify_show(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_clarify_module.cmd_clarify_show(args, ops_dir, project_config)


def cmd_clarify_answer(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_clarify_module.cmd_clarify_answer(args, ops_dir, project_config)


def cmd_clarify_ask(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_clarify_module.cmd_clarify_ask(args, ops_dir, project_config)


def cmd_pm_plan(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_pm_module.cmd_pm_plan(args, ops_dir, project_config)


def cmd_pm_refine(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_pm_module.cmd_pm_refine(args, ops_dir, project_config)


def cmd_pm_spec(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_pm_module.cmd_pm_spec(args, ops_dir, project_config)


def cmd_pm_status(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_pm_module.cmd_pm_status(args, ops_dir, project_config)


def cmd_pm_list(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_pm_module.cmd_pm_list(args, ops_dir, project_config)


def cmd_pm_show(args):
    project_config, ops_dir = get_project_config(args)
    return cmd_pm_module.cmd_pm_show(args, ops_dir, project_config)


def main():
    parser = argparse.ArgumentParser(prog='wf', description='AOS Workflow CLI')
    parser.add_argument('--project', '-p', help='Project name')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # wf new
    p_new = subparsers.add_parser('new', help='Create workstream')
    p_new.add_argument('id', nargs='?', help='Workstream ID (optional if --stories provided)')
    p_new.add_argument('title', nargs='?', help='Workstream title (optional if --stories provided)')
    p_new.add_argument('--stories', '-s', help='Link to story ID (e.g., STORY-0001)')
    p_new.set_defaults(func=cmd_new)

    # wf list
    p_list = subparsers.add_parser('list', help='List workstreams')
    p_list.set_defaults(func=cmd_list)

    # wf use
    p_use = subparsers.add_parser('use', help='Set/show current workstream')
    p_use.add_argument('id', nargs='?', help='Workstream ID to use')
    p_use.add_argument('--clear', action='store_true', help='Clear current workstream')
    p_use.set_defaults(func=cmd_use)

    # wf refresh
    p_refresh = subparsers.add_parser('refresh', help='Refresh touched files')
    p_refresh.add_argument('id', nargs='?', help='Workstream ID (optional, refreshes all if omitted)')
    p_refresh.set_defaults(func=cmd_refresh)

    # wf status
    p_status = subparsers.add_parser('status', help='Show workstream status')
    p_status.add_argument('id', nargs='?', help='Workstream ID (uses current if not specified)')
    p_status.set_defaults(func=cmd_status)

    # wf show
    p_show = subparsers.add_parser('show', help='Show changes and last run details')
    p_show.add_argument('id', nargs='?', help='Workstream ID (uses current if not specified)')
    p_show.add_argument('--brief', '-b', action='store_true', help='Show only diff stats, not full diff')
    p_show.set_defaults(func=cmd_show)

    # wf review
    p_review = subparsers.add_parser('review', help='Final AI review of entire branch before merge')
    p_review.add_argument('id', nargs='?', help='Workstream ID (uses current if not specified)')
    p_review.set_defaults(func=cmd_review)

    # wf conflicts
    p_conflicts = subparsers.add_parser('conflicts', help='Check file conflicts')
    p_conflicts.add_argument('id', nargs='?', help='Workstream ID (uses current if not specified)')
    p_conflicts.set_defaults(func=cmd_conflicts)

    # wf run
    p_run = subparsers.add_parser('run', help='Run cycle')
    p_run.add_argument('id', nargs='?', help='Workstream ID (uses current if not specified)')
    p_run.add_argument('--once', action='store_true', help='Run single cycle')
    p_run.add_argument('--loop', action='store_true', help='Run until blocked')
    p_run.add_argument('--verbose', '-v', action='store_true', help='Show implement/review exchange')
    p_run.set_defaults(func=cmd_run)

    # wf close
    p_close = subparsers.add_parser('close', help='Archive workstream without merging (abandon)')
    p_close.add_argument('id', nargs='?', help='Workstream ID (uses current if not specified)')
    p_close.add_argument('--force', action='store_true', help='Close even with uncommitted changes')
    p_close.set_defaults(func=cmd_close)

    # wf merge
    p_merge = subparsers.add_parser('merge', help='Merge workstream to main and archive')
    p_merge.add_argument('id', nargs='?', help='Workstream ID (uses current if not specified)')
    p_merge.add_argument('--push', action='store_true', help='Push to remote after merge')
    p_merge.set_defaults(func=cmd_merge)

    # wf archive
    p_archive = subparsers.add_parser('archive', help='List archived workstreams')
    p_archive.set_defaults(func=cmd_archive)
    archive_sub = p_archive.add_subparsers(dest='archive_cmd')

    # wf archive delete
    p_archive_delete = archive_sub.add_parser('delete', help='Permanently delete archived workstream')
    p_archive_delete.add_argument('id', help='Workstream ID')
    p_archive_delete.add_argument('--confirm', action='store_true', required=True, help='Confirm deletion')
    p_archive_delete.set_defaults(func=cmd_archive_delete)

    # wf open
    p_open = subparsers.add_parser('open', help='Resurrect archived workstream')
    p_open.add_argument('id', help='Workstream ID')
    p_open.add_argument('--use', action='store_true', help='Set as current workstream after opening')
    p_open.add_argument('--force', action='store_true', help='Skip confirmation for high-severity conflicts')
    p_open.set_defaults(func=cmd_open)

    # wf approve
    p_approve = subparsers.add_parser('approve', help='Approve workstream for commit')
    p_approve.add_argument('id', nargs='?', help='Workstream ID (uses current if not specified)')
    p_approve.set_defaults(func=cmd_approve)

    # wf reject
    p_reject = subparsers.add_parser('reject', help='Reject and iterate on current changes')
    p_reject.add_argument('id', nargs='?', help='Workstream ID (uses current if not specified)')
    p_reject.add_argument('--feedback', '-f', help='Feedback for the implementer')
    p_reject.set_defaults(func=cmd_reject)

    # wf reset
    p_reset = subparsers.add_parser('reset', help='Discard changes and start fresh (rare)')
    p_reset.add_argument('id', nargs='?', help='Workstream ID (uses current if not specified)')
    p_reset.add_argument('--feedback', '-f', help='Feedback for the implementer')
    p_reset.set_defaults(func=cmd_reset)

    # wf clarify
    p_clarify = subparsers.add_parser('clarify', help='Manage clarification requests')
    p_clarify.set_defaults(func=cmd_clarify_list)
    clarify_sub = p_clarify.add_subparsers(dest='clarify_cmd')

    # wf clarify list
    p_clarify_list = clarify_sub.add_parser('list', help='List pending clarifications')
    p_clarify_list.set_defaults(func=cmd_clarify_list)

    # wf clarify show
    p_clarify_show = clarify_sub.add_parser('show', help='Show clarification details')
    p_clarify_show.add_argument('workstream', help='Workstream ID')
    p_clarify_show.add_argument('id', help='Clarification ID (e.g., CLQ-001)')
    p_clarify_show.set_defaults(func=cmd_clarify_show)

    # wf clarify answer
    p_clarify_answer = clarify_sub.add_parser('answer', help='Answer a clarification')
    p_clarify_answer.add_argument('workstream', help='Workstream ID')
    p_clarify_answer.add_argument('id', help='Clarification ID')
    p_clarify_answer.add_argument('--answer', '-a', help='Answer text (prompts if not provided)')
    p_clarify_answer.set_defaults(func=cmd_clarify_answer)

    # wf clarify ask
    p_clarify_ask = clarify_sub.add_parser('ask', help='Create a clarification (for testing)')
    p_clarify_ask.add_argument('workstream', help='Workstream ID')
    p_clarify_ask.add_argument('question', help='The question')
    p_clarify_ask.add_argument('--context', '-c', help='Additional context')
    p_clarify_ask.add_argument('--urgency', '-u', choices=['blocking', 'non-blocking'], default='blocking')
    p_clarify_ask.set_defaults(func=cmd_clarify_ask)

    # wf pm
    p_pm = subparsers.add_parser('pm', help='Project management (story sifting, SPEC generation)')
    p_pm.set_defaults(func=cmd_pm_status)
    pm_sub = p_pm.add_subparsers(dest='pm_cmd')

    # wf pm plan
    p_pm_plan = pm_sub.add_parser('plan', help='Start interactive planning session')
    p_pm_plan.set_defaults(func=cmd_pm_plan)

    # wf pm refine
    p_pm_refine = pm_sub.add_parser('refine', help='Create story from a chunk')
    p_pm_refine.add_argument('name', help='Chunk name/description')
    p_pm_refine.set_defaults(func=cmd_pm_refine)

    # wf pm spec
    p_pm_spec = pm_sub.add_parser('spec', help='Update SPEC.md from workstream')
    p_pm_spec.add_argument('workstream', help='Workstream ID')
    p_pm_spec.set_defaults(func=cmd_pm_spec)

    # wf pm status
    p_pm_status = pm_sub.add_parser('status', help='Show PM status')
    p_pm_status.set_defaults(func=cmd_pm_status)

    # wf pm list
    p_pm_list = pm_sub.add_parser('list', help='List stories')
    p_pm_list.set_defaults(func=cmd_pm_list)

    # wf pm show
    p_pm_show = pm_sub.add_parser('show', help='Show story details')
    p_pm_show.add_argument('story', help='Story ID')
    p_pm_show.set_defaults(func=cmd_pm_show)

    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
