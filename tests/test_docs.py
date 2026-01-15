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
        # Function builds update prompt regardless of current_spec
        assert "Update SPEC.md" in result
        assert "Test Workstream" in result

    def test_builds_prompt_with_current_spec(self):
        """Should build prompt regardless of current SPEC (Claude reads file directly)."""
        context = {
            "workstream_id": "test-ws",
            "title": "Test Workstream",
            "current_spec": "# Existing SPEC\n\nSome content here.",
            "story": None,
            "microcommits": [],
            "commits": [],
        }
        result = build_spec_prompt(context)
        # current_spec is no longer embedded - Claude reads SPEC.md directly
        assert "Update SPEC.md" in result
        assert "Test Workstream" in result

    def test_builds_prompt_with_story(self):
        """Should include story problem and acceptance criteria."""
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
        # Function includes problem and acceptance criteria, but not id/title/non_goals
        assert "Users need feature X" in result
        assert "AC1" in result
        assert "AC2" in result

    def test_builds_prompt_with_microcommits(self):
        """Microcommits are not included in prompt (Claude reads code directly)."""
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
        # Microcommits are not embedded in prompt
        assert "COMMIT-TEST-001" not in result
        assert "COMMIT-TEST-002" not in result
        assert "First commit" not in result

    def test_builds_prompt_with_commits(self):
        """Commits are not included in prompt (Claude reads code directly)."""
        context = {
            "workstream_id": "test-ws",
            "title": "Test Workstream",
            "current_spec": None,
            "story": None,
            "microcommits": [],
            "commits": ["abc123 First commit", "def456 Second commit"],
        }
        result = build_spec_prompt(context)
        # Commits are not embedded in prompt
        assert "abc123" not in result
        assert "def456" not in result

    def test_includes_diff_stat_and_code_diff(self):
        """Should include diff_stat and code_diff when provided."""
        context = {
            "workstream_id": "test-ws",
            "title": "Test Workstream",
            "current_spec": None,
            "story": None,
            "microcommits": [],
            "commits": [],
            "diff_stat": "file.py | 5 +++++",
            "code_diff": "+def new_func():\n+    pass",
        }
        result = build_spec_prompt(context)
        assert "file.py | 5" in result
        assert "+def new_func" in result

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
