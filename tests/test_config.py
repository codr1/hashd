"""Tests for orchestrator.lib.config module."""

import pytest
from pathlib import Path
from unittest.mock import patch

from orchestrator.lib.config import (
    load_project_profile,
    VALID_MERGE_MODES,
)


class TestMergeModeValidation:
    """Test MERGE_MODE validation in load_project_profile."""

    @patch("orchestrator.lib.config.envparse.load_env")
    def test_valid_local_mode(self, mock_load_env):
        mock_load_env.return_value = {"MERGE_MODE": "local"}
        profile = load_project_profile(Path("/fake/project"))
        assert profile.merge_mode == "local"

    @patch("orchestrator.lib.config.envparse.load_env")
    def test_valid_github_pr_mode(self, mock_load_env):
        mock_load_env.return_value = {"MERGE_MODE": "github_pr"}
        profile = load_project_profile(Path("/fake/project"))
        assert profile.merge_mode == "github_pr"

    @patch("orchestrator.lib.config.envparse.load_env")
    def test_defaults_to_local(self, mock_load_env):
        mock_load_env.return_value = {}
        profile = load_project_profile(Path("/fake/project"))
        assert profile.merge_mode == "local"

    @patch("orchestrator.lib.config.envparse.load_env")
    def test_invalid_mode_defaults_to_local_with_warning(self, mock_load_env, caplog):
        mock_load_env.return_value = {"MERGE_MODE": "invalid_mode"}
        profile = load_project_profile(Path("/fake/project"))
        assert profile.merge_mode == "local"
        assert "Unknown MERGE_MODE 'invalid_mode'" in caplog.text

    @patch("orchestrator.lib.config.envparse.load_env")
    def test_typo_in_mode_defaults_to_local(self, mock_load_env, caplog):
        mock_load_env.return_value = {"MERGE_MODE": "github"}  # typo - missing _pr
        profile = load_project_profile(Path("/fake/project"))
        assert profile.merge_mode == "local"
        assert "Unknown MERGE_MODE 'github'" in caplog.text


class TestValidMergeModes:
    """Test VALID_MERGE_MODES constant."""

    def test_contains_expected_modes(self):
        assert "local" in VALID_MERGE_MODES
        assert "github_pr" in VALID_MERGE_MODES
        assert len(VALID_MERGE_MODES) == 2
