"""Tests for the docs module."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from orchestrator.commands.docs import (
    gather_docs_context,
    build_spec_prompt,
    _load_workstream_for_docs,
    _get_docs_timeout,
)


class TestBuildSpecPrompt:
    """Tests for build_spec_prompt function."""

    def test_builds_prompt_with_empty_spec(self):
        """Should handle missing current SPEC."""
        context = {
            "workstream_id": "test-ws",
            "title": "Test Workstream",
            "current_spec": None,
            "story": None,
            "microcommits": [],
            "commits": [],
        }
        result = build_spec_prompt(context)
        assert "No SPEC exists yet" in result
        assert "Test Workstream" in result

    def test_builds_prompt_with_current_spec(self):
        """Should include current SPEC content."""
        context = {
            "workstream_id": "test-ws",
            "title": "Test Workstream",
            "current_spec": "# Existing SPEC\n\nSome content here.",
            "story": None,
            "microcommits": [],
            "commits": [],
        }
        result = build_spec_prompt(context)
        assert "# Existing SPEC" in result
        assert "Some content here" in result

    def test_builds_prompt_with_story(self):
        """Should include story details."""
        context = {
            "workstream_id": "test-ws",
            "title": "Test Workstream",
            "current_spec": None,
            "story": {
                "id": "STORY-0001",
                "title": "Add feature X",
                "problem": "Users need feature X",
                "acceptance_criteria": ["AC1", "AC2"],
                "non_goals": ["NG1"],
            },
            "microcommits": [],
            "commits": [],
        }
        result = build_spec_prompt(context)
        assert "STORY-0001" in result
        assert "Add feature X" in result
        assert "Users need feature X" in result
        assert "AC1" in result
        assert "NG1" in result

    def test_builds_prompt_with_microcommits(self):
        """Should include micro-commit details."""
        context = {
            "workstream_id": "test-ws",
            "title": "Test Workstream",
            "current_spec": None,
            "story": None,
            "microcommits": [
                {"id": "COMMIT-TEST-001", "title": "First commit", "done": True, "content": "Details here"},
                {"id": "COMMIT-TEST-002", "title": "Second commit", "done": False, "content": ""},
            ],
            "commits": [],
        }
        result = build_spec_prompt(context)
        assert "COMMIT-TEST-001" in result
        assert "[x]" in result  # done marker
        assert "[ ]" in result  # not done marker

    def test_builds_prompt_with_commits(self):
        """Should include git commit messages."""
        context = {
            "workstream_id": "test-ws",
            "title": "Test Workstream",
            "current_spec": None,
            "story": None,
            "microcommits": [],
            "commits": ["abc123 First commit", "def456 Second commit"],
        }
        result = build_spec_prompt(context)
        assert "abc123 First commit" in result
        assert "def456 Second commit" in result

    def test_truncates_long_commit_list(self):
        """Should truncate commit list beyond 15."""
        context = {
            "workstream_id": "test-ws",
            "title": "Test Workstream",
            "current_spec": None,
            "story": None,
            "microcommits": [],
            "commits": [f"commit{i} Message {i}" for i in range(20)],
        }
        result = build_spec_prompt(context)
        assert "commit14" in result  # 15th commit (0-indexed)
        assert "commit15" not in result  # 16th commit should be truncated
        assert "... and 5 more" in result

    def test_builds_prompt_with_diff(self):
        """Should include code diff."""
        context = {
            "workstream_id": "test-ws",
            "title": "Test Workstream",
            "current_spec": None,
            "story": None,
            "microcommits": [],
            "commits": [],
            "diff_stat": "foo.py | 10 ++++",
            "code_diff": "+def new_function():\n+    pass",
        }
        result = build_spec_prompt(context)
        assert "foo.py | 10" in result
        assert "+def new_function" in result


class TestLoadWorkstreamForDocs:
    """Tests for _load_workstream_for_docs helper."""

    def test_returns_error_for_missing_workstream(self, tmp_path, capsys):
        """Should return exit code 2 for missing workstream."""
        ops_dir = tmp_path
        (ops_dir / "workstreams").mkdir()

        ws, exit_code = _load_workstream_for_docs("nonexistent", ops_dir)

        assert exit_code == 2
        assert ws is None
        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_finds_workstream_in_closed(self, tmp_path):
        """Should find workstream in _closed directory."""
        ops_dir = tmp_path
        workstreams_dir = ops_dir / "workstreams"
        workstreams_dir.mkdir()
        closed_dir = workstreams_dir / "_closed" / "old_ws"
        closed_dir.mkdir(parents=True)

        # Create meta.env with all required fields
        (closed_dir / "meta.env").write_text(
            'ID="old_ws"\n'
            'TITLE="Old Workstream"\n'
            'BRANCH="feature/old"\n'
            'BASE_BRANCH="main"\n'
            'BASE_SHA="abc1234"\n'
            'WORKTREE="/tmp/nonexistent"\n'
            'STATUS="archived"\n'
        )

        ws, exit_code = _load_workstream_for_docs("old_ws", ops_dir)

        assert exit_code == 0
        assert ws.title == "Old Workstream"


class TestGetDocsTimeout:
    """Tests for _get_docs_timeout helper."""

    def test_returns_default_without_profile(self, tmp_path):
        """Should return 300 when no profile exists."""
        ops_dir = tmp_path
        (ops_dir / "projects" / "test").mkdir(parents=True)

        config = MagicMock()
        config.name = "test"

        timeout = _get_docs_timeout(ops_dir, config)
        assert timeout == 300

    def test_returns_profile_timeout(self, tmp_path):
        """Should return timeout from profile when it exists."""
        ops_dir = tmp_path
        project_dir = ops_dir / "projects" / "test"
        project_dir.mkdir(parents=True)

        # Create profile with custom timeout
        (project_dir / "project_profile.env").write_text(
            'REVIEW_TIMEOUT="600"\n'
        )

        config = MagicMock()
        config.name = "test"

        timeout = _get_docs_timeout(ops_dir, config)
        assert timeout == 600
