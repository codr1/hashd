"""
wf clarify - Manage clarification requests.
"""

from pathlib import Path

from orchestrator.lib.config import ProjectConfig
from orchestrator.clarifications import (
    get_pending_clarifications,
    get_blocking_clarifications,
    get_clarification,
    answer_clarification,
    create_clarification,
)


def cmd_clarify_list(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """List pending clarifications across all workstreams."""
    workstreams_dir = ops_dir / "workstreams"

    all_clqs = []

    # Check all active workstreams
    for ws_dir in workstreams_dir.iterdir():
        if ws_dir.is_dir() and ws_dir.name != "_closed" and (ws_dir / "meta.env").exists():
            clqs = get_pending_clarifications(ws_dir)
            for clq in clqs:
                all_clqs.append((ws_dir.name, clq))

    if not all_clqs:
        print("No pending clarifications")
        return 0

    print(f"Pending clarifications for: {project_config.name}")
    print()
    print(f"{'ID':<12} {'WORKSTREAM':<20} {'URGENCY':<12} QUESTION")
    print("─" * 80)

    for ws_name, clq in all_clqs:
        question_preview = clq.question[:40] + "..." if len(clq.question) > 40 else clq.question
        print(f"{clq.id:<12} {ws_name:<20} {clq.urgency:<12} {question_preview}")

    print("─" * 80)
    print(f"{len(all_clqs)} pending clarification(s)")
    print()
    print("Use 'wf clarify show <workstream> <id>' to view details")
    print("Use 'wf clarify answer <workstream> <id>' to answer")

    return 0


def cmd_clarify_show(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Show full details of a clarification."""
    ws_id = args.workstream
    clq_id = args.id

    workstream_dir = ops_dir / "workstreams" / ws_id
    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    clq = get_clarification(workstream_dir, clq_id)
    if not clq:
        print(f"ERROR: Clarification '{clq_id}' not found in workstream '{ws_id}'")
        return 2

    print(f"Clarification: {clq.id}")
    print("=" * 60)
    print(f"Status:     {clq.status}")
    print(f"Urgency:    {clq.urgency}")
    print(f"Workstream: {clq.workstream}")
    print(f"Created:    {clq.created}")
    print()

    print("Question")
    print("-" * 40)
    print(clq.question)
    print()

    if clq.context:
        print("Context")
        print("-" * 40)
        print(clq.context)
        print()

    if clq.options:
        print("Options")
        print("-" * 40)
        for i, opt in enumerate(clq.options, 1):
            label = opt.get("label", f"Option {i}")
            desc = opt.get("description", "")
            print(f"  {i}. {label}")
            if desc:
                print(f"     {desc}")
        print()

    if clq.blocks:
        print(f"Blocks: {', '.join(clq.blocks)}")
        print()

    if clq.status == "answered":
        print("Answer")
        print("-" * 40)
        print(f"Answered by: {clq.answered_by}")
        print(f"Answered at: {clq.answered}")
        print()
        print(clq.answer or "(no answer)")
    else:
        print("Actions")
        print("-" * 40)
        print(f"  wf clarify answer {ws_id} {clq_id}")

    return 0


def cmd_clarify_answer(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Answer a clarification."""
    ws_id = args.workstream
    clq_id = args.id
    answer = args.answer

    workstream_dir = ops_dir / "workstreams" / ws_id
    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    clq = get_clarification(workstream_dir, clq_id)
    if not clq:
        print(f"ERROR: Clarification '{clq_id}' not found")
        return 2

    if clq.status != "pending":
        print(f"ERROR: Clarification already {clq.status}")
        return 2

    # If no answer provided on command line, prompt for it
    if not answer:
        print(f"Answering: {clq.id}")
        print()
        print("Question:")
        print(clq.question)
        print()

        if clq.options:
            print("Options:")
            for i, opt in enumerate(clq.options, 1):
                label = opt.get("label", f"Option {i}")
                print(f"  {i}. {label}")
            print()
            answer = input("Your answer (number or text): ").strip()

            # If they entered a number, look up the option
            if answer.isdigit():
                idx = int(answer) - 1
                if 0 <= idx < len(clq.options):
                    answer = clq.options[idx].get("label", answer)
        else:
            answer = input("Your answer: ").strip()

    if not answer:
        print("ERROR: Answer cannot be empty")
        return 2

    try:
        answer_clarification(workstream_dir, clq_id, answer, by="human")
        print(f"Answered {clq_id}: {answer}")

        # Check if there are remaining blocking CLQs
        remaining = get_blocking_clarifications(workstream_dir)
        if remaining:
            print(f"Note: {len(remaining)} blocking CLQ(s) remain: {', '.join(c.id for c in remaining)}")
        else:
            print(f"Workstream '{ws_id}' unblocked")
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 2

    return 0


def cmd_clarify_ask(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Create a new clarification request (for testing/manual use)."""
    ws_id = args.workstream
    question = args.question
    urgency = getattr(args, 'urgency', 'blocking') or 'blocking'

    workstream_dir = ops_dir / "workstreams" / ws_id
    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    data = {
        "question": question,
        "context": getattr(args, 'context', '') or '',
        "options": [],
        "blocks": [],
        "urgency": urgency,
    }

    clq = create_clarification(workstream_dir, data)
    print(f"Created clarification: {clq.id}")
    print(f"  Question: {question}")
    print(f"  Urgency: {urgency}")
    print()
    print(f"Answer with: wf clarify answer {ws_id} {clq.id}")

    return 0
