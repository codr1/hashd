"""Tests for orchestrator.runner.impl.state_files module."""

from pathlib import Path

from orchestrator.runner.impl.state_files import update_workstream_status


class TestUpdateWorkstreamStatus:
    """Test update_workstream_status function."""

    def test_updates_existing_status(self, tmp_path):
        """Should replace existing STATUS line."""
        meta_file = tmp_path / "meta.env"
        meta_file.write_text('ID="test"\nSTATUS="pr_open"\nBRANCH="feat/test"\n')

        update_workstream_status(tmp_path, "active")

        content = meta_file.read_text()
        assert 'STATUS="active"' in content
        assert 'STATUS="pr_open"' not in content
        # Other fields preserved
        assert 'ID="test"' in content
        assert 'BRANCH="feat/test"' in content

    def test_handles_different_status_values(self, tmp_path):
        """Should work with various status values."""
        meta_file = tmp_path / "meta.env"
        meta_file.write_text('STATUS="awaiting_human_review"\n')

        update_workstream_status(tmp_path, "merged")

        content = meta_file.read_text()
        assert 'STATUS="merged"' in content

    def test_handles_missing_file_gracefully(self, tmp_path, caplog):
        """Should log warning if file doesn't exist."""
        import logging
        caplog.set_level(logging.WARNING)

        update_workstream_status(tmp_path, "active")

        assert "Failed to update workstream status" in caplog.text

    def test_preserves_file_structure(self, tmp_path):
        """Should maintain line order and other content."""
        meta_file = tmp_path / "meta.env"
        original = 'ID="ws1"\nSTATUS="active"\nBRANCH="feat/ws1"\nPR_NUMBER="42"\n'
        meta_file.write_text(original)

        update_workstream_status(tmp_path, "pr_open")

        lines = meta_file.read_text().splitlines()
        assert lines[0] == 'ID="ws1"'
        assert lines[1] == 'STATUS="pr_open"'
        assert lines[2] == 'BRANCH="feat/ws1"'
        assert lines[3] == 'PR_NUMBER="42"'

    def test_adds_status_if_missing(self, tmp_path):
        """Should append STATUS if not present in file."""
        meta_file = tmp_path / "meta.env"
        meta_file.write_text('ID="test"\nBRANCH="feat/test"\n')

        update_workstream_status(tmp_path, "active")

        content = meta_file.read_text()
        assert 'STATUS="active"' in content
        # Other fields preserved
        assert 'ID="test"' in content
        assert 'BRANCH="feat/test"' in content
